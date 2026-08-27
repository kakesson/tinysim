"""
Stage 3 of the pipeline: *flattening*.

Flattening is where the object-oriented model becomes a plain, flat set of
equations -- the step that makes acausal modeling work.  Three things happen:

1. **Instantiation.**  Every component declaration is expanded, recursively,
   into the variables and equations of its class, with dotted names:
   the `v` inside component `c` becomes `c.v`.
2. **Inheritance and modifiers.**  `extends OnePort;` copies the base class in,
   and `Capacitor c(C = 1e-3, v(start = 0))` overrides declarations inside the
   instantiated component.
3. **Connection expansion.**  Every `connect(a, b)` is forgotten as such and
   replaced by real equations: potential variables are set equal, and flow
   variables are summed to zero.

The result is a `FlatModel`: variables, parameters, equations, and `when`
clauses, and nothing else.  Everything after this point works only on that.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .ast_nodes import (
    Assign, BinOp, Call, ClassDef, ConnectEquation, Decl, Equation, Expr,
    IfExpr, Num, Program, Ref, Reinit, UnOp, WhenEquation, equation_to_string,
    to_string,
)
from .evaluator import EvaluationError, evaluate, free_names


class ModelError(Exception):
    """Raised when a model is syntactically fine but does not make sense."""


# =============================================================================
# The flat model
# =============================================================================

@dataclass
class FlatVariable:
    """One variable of the flattened model, with its dotted name."""
    name: str
    kind: str = "continuous"          # 'continuous' | 'discrete' | 'parameter' | 'constant'
    start: Optional[Expr] = None      # `start` attribute, as an expression
    binding: Optional[Expr] = None    # `= expr` on the declaration (parameters)
    is_flow: bool = False
    description: str = ""
    declared_in: str = ""             # class the declaration came from, for reporting
    attributes: Dict[str, Expr] = field(default_factory=dict)   # min, max, nominal

    @property
    def is_parameter(self) -> bool:
        return self.kind in ("parameter", "constant")


@dataclass
class ConnectionSet:
    """A set of connectors that `connect` statements have tied together."""
    connectors: List[str]                   # flat connector names, e.g. 'r.n'
    signs: Dict[str, int]                   # +1 inside, -1 outside (see below)
    connector_class: str = ""


@dataclass
class FlatModel:
    """The flattened model: the input to all later stages."""
    name: str
    variables: Dict[str, FlatVariable] = field(default_factory=dict)
    equations: List[Equation] = field(default_factory=list)
    initial_equations: List[Equation] = field(default_factory=list)
    when_equations: List[WhenEquation] = field(default_factory=list)
    connection_sets: List[ConnectionSet] = field(default_factory=list)
    parameter_values: Dict[str, float] = field(default_factory=dict)

    # -- handy views used by the later stages --------------------------------
    def continuous_variables(self) -> List[str]:
        return [n for n, v in self.variables.items() if v.kind == "continuous"]

    def discrete_variables(self) -> List[str]:
        return [n for n, v in self.variables.items() if v.kind == "discrete"]

    def parameters(self) -> List[str]:
        return [n for n, v in self.variables.items() if v.is_parameter]


# =============================================================================
# Helpers on expressions
# =============================================================================

def prefix_names(expr: Expr, prefix: str) -> Expr:
    """
    Rewrite every variable reference in `expr` by prepending `prefix`.

    This is what turns the equation `C * der(v) = i` written inside class
    `Capacitor` into `c.C * der(c.v) = c.i` when it is instantiated as `c`.
    The built-in `time` is left alone: it is global.
    """
    if isinstance(expr, Num):
        return expr
    if isinstance(expr, Ref):
        return expr if expr.name == "time" else Ref(prefix + expr.name)
    if isinstance(expr, UnOp):
        return UnOp(expr.op, prefix_names(expr.operand, prefix))
    if isinstance(expr, BinOp):
        return BinOp(expr.op, prefix_names(expr.left, prefix),
                     prefix_names(expr.right, prefix))
    if isinstance(expr, Call):
        return Call(expr.func, tuple(prefix_names(a, prefix) for a in expr.args))
    if isinstance(expr, IfExpr):
        return IfExpr(prefix_names(expr.cond, prefix),
                      prefix_names(expr.then_expr, prefix),
                      prefix_names(expr.else_expr, prefix))
    raise TypeError(f"cannot rewrite {expr!r}")


def derivative_names(expr: Expr, found=None) -> set:
    """Names appearing inside `der(...)`, i.e. the state variables used here."""
    found = set() if found is None else found
    if isinstance(expr, Call):
        if expr.func == "der":
            found.add(expr.args[0].name)
        for argument in expr.args:
            derivative_names(argument, found)
    elif isinstance(expr, UnOp):
        derivative_names(expr.operand, found)
    elif isinstance(expr, BinOp):
        derivative_names(expr.left, found)
        derivative_names(expr.right, found)
    elif isinstance(expr, IfExpr):
        derivative_names(expr.cond, found)
        derivative_names(expr.then_expr, found)
        derivative_names(expr.else_expr, found)
    return found


# =============================================================================
# Flattening
# =============================================================================

class Flattener:
    """Instantiates one model class into a `FlatModel`."""

    def __init__(self, program: Program):
        self.program = program
        self.model = None
        # flat connector name -> its connector class, filled during instantiation
        self.connector_instances: Dict[str, str] = {}
        self.connections: List[tuple] = []      # (a, b, sign_a, sign_b, line)

    # -- entry point ---------------------------------------------------------

    def flatten(self, model_name: str) -> FlatModel:
        definition = self.program[model_name]
        if definition.kind != "model":
            raise ModelError(f"{model_name!r} is a {definition.kind}, not a model")
        if definition.partial:
            raise ModelError(
                f"{model_name!r} is a partial model and cannot be simulated on "
                f"its own; it exists to be inherited from with `extends`")

        self.model = FlatModel(name=model_name)
        self._instantiate(definition, prefix="", modifiers={})
        self._expand_connections()
        self._evaluate_parameters()
        self._check_references()
        return self.model

    # -- 1. instantiation ----------------------------------------------------

    def _instantiate(self, definition: ClassDef, prefix: str, modifiers: dict):
        """
        Recursively expand `definition` under `prefix`, applying `modifiers`
        handed down from the enclosing declaration.
        """
        expanded = self._resolve_inheritance(definition)

        for decl in expanded.decls:
            modifier = modifiers.get(decl.name)
            if decl.is_variable:
                self._add_variable(decl, prefix, modifier, expanded.name)
            else:
                self._instantiate_component(decl, prefix, modifier)

        # Equations of this class, with all names prefixed.
        for equation in expanded.equations:
            self._add_equation(equation, prefix, expanded.name)
        for equation in expanded.initial_equations:
            flat = Equation(prefix_names(equation.lhs, prefix),
                            prefix_names(equation.rhs, prefix),
                            line=equation.line,
                            origin=f"initial equation in {expanded.name}")
            flat.source = equation_to_string(flat)
            self.model.initial_equations.append(flat)

    def _resolve_inheritance(self, definition: ClassDef) -> ClassDef:
        """
        Return a copy of `definition` with everything from its base classes
        copied in first.  `extends` in TinySim is literally textual reuse --
        which is a fair picture of what it means in Modelica too.
        """
        if not definition.extends:
            return definition

        merged = ClassDef(kind=definition.kind, name=definition.name,
                          description=definition.description, line=definition.line)
        for base_name in definition.extends:
            base = self._resolve_inheritance(self.program[base_name])
            merged.decls.extend(base.decls)
            merged.equations.extend(base.equations)
            merged.initial_equations.extend(base.initial_equations)
        merged.decls.extend(definition.decls)
        merged.equations.extend(definition.equations)
        merged.initial_equations.extend(definition.initial_equations)

        names = [d.name for d in merged.decls]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ModelError(
                f"model {definition.name!r} inherits declarations that clash: "
                f"{', '.join(sorted(duplicates))}")
        return merged

    def _add_variable(self, decl: Decl, prefix: str, modifier, declared_in: str):
        """Create one flat variable from a `Real` declaration."""
        name = prefix + decl.name
        if name in self.model.variables:
            raise ModelError(f"variable {name!r} is declared twice")

        kind = ("parameter" if "parameter" in decl.prefixes else
                "constant" if "constant" in decl.prefixes else
                "discrete" if "discrete" in decl.prefixes else "continuous")

        variable = FlatVariable(
            name=name, kind=kind, is_flow=decl.is_flow,
            description=decl.description, declared_in=declared_in,
            binding=decl.value,
        )
        # Attributes written on the declaration itself, e.g. Real v(start = 0).
        self._apply_attributes(variable, decl.modifiers, prefix)
        # Attributes and values handed down by the enclosing declaration,
        # e.g. Capacitor c(C = 1e-3, v(start = 0)).  These win.
        if isinstance(modifier, dict):
            self._apply_attributes(variable, modifier, prefix)
        elif modifier is not None:
            if kind == "constant":
                raise ModelError(f"constant {name!r} cannot be modified")
            variable.binding = modifier

        self.model.variables[name] = variable

    def _apply_attributes(self, variable: FlatVariable, modifiers: dict, prefix: str):
        for key, value in modifiers.items():
            if isinstance(value, dict):
                raise ModelError(
                    f"{variable.name}: attribute {key!r} cannot take modifiers")
            # References inside a modifier are evaluated in the scope where the
            # modifier was written, so they get that scope's prefix.
            expression = prefix_names(value, prefix)
            if key == "start":
                variable.start = expression
            elif key in ("min", "max", "nominal"):
                variable.attributes[key] = expression
            else:
                raise ModelError(
                    f"{variable.name}: unknown attribute {key!r} "
                    f"(known: start, min, max, nominal)")

    def _instantiate_component(self, decl: Decl, prefix: str, modifier):
        """Expand a sub-component or connector declaration."""
        definition = self.program[decl.type_name]
        if definition.partial:
            raise ModelError(
                f"{prefix + decl.name}: {decl.type_name!r} is partial and "
                f"cannot be instantiated")

        # Merge modifiers written on the declaration with those handed down.
        merged = dict(decl.modifiers)
        if isinstance(modifier, dict):
            merged.update(modifier)
        elif modifier is not None:
            raise ModelError(f"{prefix + decl.name}: a component cannot be "
                             f"given a value with '='")

        inner_prefix = f"{prefix}{decl.name}."
        if definition.kind == "connector":
            self.connector_instances[prefix + decl.name] = definition.name
        self._instantiate(definition, inner_prefix, merged)

    def _add_equation(self, equation, prefix: str, class_name: str):
        """Prefix and store one equation, `connect` and `when` included."""
        if isinstance(equation, ConnectEquation):
            self._record_connection(equation, prefix, class_name)
        elif isinstance(equation, WhenEquation):
            body = []
            for statement in equation.body:
                value = prefix_names(statement.value, prefix)
                name = prefix + statement.name
                body.append(Assign(name, value, statement.line)
                            if isinstance(statement, Assign)
                            else Reinit(name, value, statement.line))
            self.model.when_equations.append(
                WhenEquation(prefix_names(equation.condition, prefix), body,
                             equation.line))
        else:
            flat = Equation(prefix_names(equation.lhs, prefix),
                            prefix_names(equation.rhs, prefix),
                            line=equation.line, origin=class_name)
            flat.source = equation_to_string(flat)
            self.model.equations.append(flat)

    # -- 2. connections ------------------------------------------------------

    def _record_connection(self, connect: ConnectEquation, prefix: str,
                           class_name: str):
        """
        Note a `connect(a, b)` for later expansion, together with the sign each
        endpoint's flow variables must carry.

        Sign convention: a flow variable is positive *into* the component.  For
        a connector belonging to a sub-component ("inside"), that is the
        direction the connection set sees, so the sign is +1.  For a connector
        belonging to the model that writes the `connect` ("outside"), the same
        flow leaves the model through that connector, so it enters the set with
        the opposite sign, -1.  Without this rule, hierarchical models would
        violate their own balance equations.
        """
        endpoints = []
        for reference in (connect.a, connect.b):
            depth = reference.count(".")
            if depth > 1:
                raise ModelError(
                    f"line {connect.line}: connect() may only name a connector "
                    f"of this model or of one of its components, not "
                    f"{reference!r}")
            sign = -1 if depth == 0 else +1
            endpoints.append((prefix + reference, sign))
        self.connections.append((prefix, endpoints[0], endpoints[1], connect.line))

    def _expand_connections(self):
        """
        Turn the recorded connections into equations.

        Connection sets are formed *per model*, not once for the whole
        flattened model.  That distinction matters as soon as a model has
        connectors of its own: in

            connect(p, r1.p);        // inside Series
            connect(src.p, s.p);     // inside Circuit

        `s.p` is an outside connector in the first set and an inside connector
        in the second.  Handling each model separately gives the two equations

            -s.p.i + s.r1.p.i = 0    and    src.p.i + s.p.i = 0

        which together say what they should -- the current entering the
        composite component is the current that reaches `r1`.  Merging both
        connects into one set would instead leave `s.p.i` free and quietly
        break the circuit.
        """
        by_model: Dict[str, List[tuple]] = {}
        for prefix, endpoint_a, endpoint_b, line in self.connections:
            by_model.setdefault(prefix, []).append((endpoint_a, endpoint_b, line))

        connected: set = set()
        for prefix in by_model:
            connected |= self._expand_one_model(by_model[prefix])

        # A connector nobody connected to carries no flow.
        for name, connector_class in self.connector_instances.items():
            if name in connected:
                continue
            for flow_variable in self._flow_variables(connector_class):
                self.model.equations.append(Equation(
                    Ref(f"{name}.{flow_variable}"), Num(0.0),
                    source=f"{name}.{flow_variable} = 0",
                    origin=f"unconnected connector {name}"))

    def _expand_one_model(self, connections) -> set:
        """Build the connection sets written inside one model instance."""
        # Union-find: group connectors that are transitively connected.
        parent: Dict[str, str] = {}

        def find(name):
            parent.setdefault(name, name)
            while parent[name] != name:
                parent[name] = parent[parent[name]]
                name = parent[name]
            return name

        def union(a, b):
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[root_b] = root_a

        signs: Dict[str, int] = {}
        for (name_a, sign_a), (name_b, sign_b), line in connections:
            for name, sign in ((name_a, sign_a), (name_b, sign_b)):
                if name not in self.connector_instances:
                    raise ModelError(
                        f"line {line}: {name!r} is not a connector")
                signs[name] = sign
            if (self.connector_instances[name_a]
                    != self.connector_instances[name_b]):
                raise ModelError(
                    f"line {line}: cannot connect {name_a!r} of type "
                    f"{self.connector_instances[name_a]} to {name_b!r} of type "
                    f"{self.connector_instances[name_b]}")
            union(name_a, name_b)

        # Collect the sets, keeping the order the connectors were declared in
        # so that generated equations are reproducible and easy to read.
        groups: Dict[str, List[str]] = {}
        for name in self.connector_instances:
            if name in signs:
                groups.setdefault(find(name), []).append(name)

        for members in groups.values():
            connector_class = self.connector_instances[members[0]]
            self.model.connection_sets.append(
                ConnectionSet(connectors=members,
                              signs={m: signs[m] for m in members},
                              connector_class=connector_class))
            self._equations_for_set(members, signs, connector_class)
        return set(signs)

    def _equations_for_set(self, members, signs, connector_class):
        """The two rules that make acausal modeling work."""
        description = ", ".join(members)

        # Rule 1: all potential variables in a connection set are equal.
        for potential in self._potential_variables(connector_class):
            first = members[0]
            for other in members[1:]:
                self.model.equations.append(Equation(
                    Ref(f"{first}.{potential}"), Ref(f"{other}.{potential}"),
                    source=f"{first}.{potential} = {other}.{potential}",
                    origin=f"connect({description}) - potential"))

        # Rule 2: the flow variables in a connection set sum to zero.
        for flow_variable in self._flow_variables(connector_class):
            total = None
            for member in members:
                term = Ref(f"{member}.{flow_variable}")
                if total is None:
                    total = term if signs[member] > 0 else UnOp("-", term)
                else:
                    total = BinOp("+" if signs[member] > 0 else "-", total, term)
            equation = Equation(total, Num(0.0),
                                origin=f"connect({description}) - flow")
            equation.source = f"{to_string(total)} = 0"
            self.model.equations.append(equation)

    def _connector_class(self, name: str) -> ClassDef:
        return self.program[name]

    def _flow_variables(self, connector_class: str) -> List[str]:
        return [d.name for d in self._connector_class(connector_class).decls
                if d.is_flow]

    def _potential_variables(self, connector_class: str) -> List[str]:
        return [d.name for d in self._connector_class(connector_class).decls
                if not d.is_flow]

    # -- 3. parameters and checking -----------------------------------------

    def _evaluate_parameters(self):
        """
        Work out a number for every parameter.

        Parameters may be defined in terms of other parameters, so this simply
        keeps sweeping the list until nothing new can be computed -- and
        reports the ones that are left over.
        """
        pending = {name: self.model.variables[name]
                   for name in self.model.parameters()}
        values: Dict[str, float] = {}
        while pending:
            progressed = False
            for name, variable in list(pending.items()):
                if variable.binding is None:
                    raise ModelError(
                        f"parameter {name!r} has no value; give it one either "
                        f"in its declaration or when the component is "
                        f"instantiated")
                try:
                    values[name] = evaluate(variable.binding, values)
                except EvaluationError:
                    continue
                del pending[name]
                progressed = True
            if not progressed:
                unresolved = ", ".join(sorted(pending))
                raise ModelError(
                    f"these parameters depend on each other in a circle, or on "
                    f"non-parameters: {unresolved}")
        self.model.parameter_values = values

    def _check_references(self):
        """Every name used in an equation must actually be declared."""
        known = set(self.model.variables) | {"time"}
        problems = []

        def check(expr, where):
            for name in free_names(expr):
                if name not in known:
                    problems.append(f"{where}: unknown variable {name!r}")

        for equation in self.model.equations + self.model.initial_equations:
            check(equation.lhs, equation.source or equation.origin)
            check(equation.rhs, equation.source or equation.origin)
        for when_equation in self.model.when_equations:
            check(when_equation.condition, f"when on line {when_equation.line}")
            for statement in when_equation.body:
                check(statement.value, f"when on line {when_equation.line}")
                if statement.name not in known:
                    problems.append(
                        f"when on line {when_equation.line}: unknown variable "
                        f"{statement.name!r}")
        if problems:
            raise ModelError("\n".join(problems))


def flatten(program: Program, model_name: str) -> FlatModel:
    """Flatten `model_name` from `program` into a `FlatModel`."""
    return Flattener(program).flatten(model_name)
