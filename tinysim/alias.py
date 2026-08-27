"""
Stage 3b of the pipeline: *alias elimination*.

Flattening produces a lot of equations that say nothing more than "these two
variables are the same quantity":

    r.i = r.p.i                 (a variable and the current through a pin)
    src.p.i + r.p.i = 0         (from connect(): what leaves one pin enters the other)
    gnd.p.v = 0                 (a variable pinned to a constant)

Every real Modelica compiler removes them before doing anything else, and the
effect is dramatic: the RC circuit below goes from 20 equations to 3.  It also
matters for *correctness* of the structural analysis: without it, the DC motor
has both `load.phi` and `emf.flange.phi` as separate states even though they
are one and the same angle, which makes the model look like a high-index DAE.

An alias is any equation of the form

    a = b        a = -b        a + b = 0       a - b = 0

and a "known" variable is one pinned to an expression of parameters only

    v = 0        v = src.V

Both are handled here, with a union-find structure that carries a sign.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .ast_nodes import (
    Assign, BinOp, Call, Equation, Expr, IfExpr, Num, Ref, Reinit, UnOp,
    WhenEquation, equation_to_string, to_string,
)
from .evaluator import free_names
from .flatten import FlatModel, FlatVariable, ModelError, derivative_names


@dataclass
class AliasResult:
    """The reduced model, plus a record of what was removed and why."""
    model: FlatModel
    #: eliminated variable -> ('alias', representative, sign) | ('known', expr)
    eliminated: Dict[str, tuple] = field(default_factory=dict)
    removed_equations: List[Equation] = field(default_factory=list)

    def describe(self, name: str) -> str:
        kind, *rest = self.eliminated[name]
        if kind == "alias":
            representative, sign = rest
            return f"{name} = {'-' if sign < 0 else ''}{representative}"
        return f"{name} = {to_string(rest[0])}"


# =============================================================================
# Recognising the simple equations
# =============================================================================

def _additive_terms(expr: Expr, sign: int = 1) -> Optional[List[Tuple[int, Expr]]]:
    """
    Split `a + b - c` into [(+1, a), (+1, b), (-1, c)].

    Returns None for anything that is not a plain sum of signed terms, which is
    the honest answer for `R * i` or `sin(phi)`.
    """
    if isinstance(expr, BinOp) and expr.op in ("+", "-"):
        left = _additive_terms(expr.left, sign)
        right = _additive_terms(expr.right, sign if expr.op == "+" else -sign)
        if left is None or right is None:
            return None
        return left + right
    if isinstance(expr, UnOp) and expr.op == "-":
        return _additive_terms(expr.operand, -sign)
    if isinstance(expr, (Ref, Num)):
        return [(sign, expr)]
    return None


def _classify(equation: Equation, unknowns: set, constants: set) -> Optional[tuple]:
    """
    Decide whether `equation` is an alias, a known value, or neither.

    Returns ('alias', a, b, sign), ('known', name, expression) or None.
    Everything is first moved to one side, so `a = -b` and `a + b = 0` are
    recognised as the same thing.

    A "known" value must be built from parameters and numbers only.  In
    particular `v = slope * time` does *not* count: a variable that follows the
    clock still has a derivative, and pretending otherwise would silently turn
    a high-index model into a wrong one.
    """
    left = _additive_terms(equation.lhs, +1)
    right = _additive_terms(equation.rhs, -1)
    if left is None or right is None:
        return None
    terms = left + right

    variable_terms = [(s, t.name) for s, t in terms
                      if isinstance(t, Ref) and t.name in unknowns]
    other_terms = [(s, t) for s, t in terms
                   if not (isinstance(t, Ref) and t.name in unknowns)
                   and not (isinstance(t, Num) and t.value == 0.0)]

    if any(name not in constants
           for _, term in other_terms for name in free_names(term)):
        return None

    if len(variable_terms) == 2 and not other_terms:
        # s1*a + s2*b = 0   ->   a = -(s2/s1) * b
        (sign_a, name_a), (sign_b, name_b) = variable_terms
        if name_a == name_b:
            return None
        return "alias", name_a, name_b, -sign_a * sign_b

    if len(variable_terms) == 1:
        # s*v + C = 0   ->   v = -s*C   (with C = 0 when there is no other term)
        sign, name = variable_terms[0]
        constant = None
        for term_sign, term in other_terms:
            piece = term if term_sign * sign < 0 else UnOp("-", term)
            constant = piece if constant is None else BinOp("+", constant, piece)
        return "known", name, constant if constant is not None else Num(0.0)

    return None


# =============================================================================
# Substitution
# =============================================================================

def substitute(expr: Expr, mapping: Dict[str, tuple]) -> Expr:
    """Replace eliminated variables in `expr` by what they are equal to."""
    if isinstance(expr, Num):
        return expr
    if isinstance(expr, Ref):
        return _replacement(expr.name, mapping) or expr
    if isinstance(expr, UnOp):
        return UnOp(expr.op, substitute(expr.operand, mapping))
    if isinstance(expr, BinOp):
        return BinOp(expr.op, substitute(expr.left, mapping),
                     substitute(expr.right, mapping))
    if isinstance(expr, IfExpr):
        return IfExpr(substitute(expr.cond, mapping),
                      substitute(expr.then_expr, mapping),
                      substitute(expr.else_expr, mapping))
    if isinstance(expr, Call):
        if expr.func in ("der", "pre"):
            return _substitute_derivative(expr.func, expr.args[0].name, mapping)
        return Call(expr.func, tuple(substitute(a, mapping) for a in expr.args))
    raise TypeError(f"cannot substitute in {expr!r}")


def _substitute_derivative(func: str, name: str, mapping: Dict[str, tuple]) -> Expr:
    """
    Substitute inside `der(x)` or `pre(x)`.

    Which rule applies depends on what `x` turned out to be:

    * an alias of another variable  ->  `der(y)`, negated if the alias is;
    * a known constant value        ->  `der(x)` is zero.

    The second rule is only sound because a "known" value is built from
    parameters alone (see `_classify`), so it really does not change with time.
    """
    entry = mapping.get(name)
    if entry is None:
        return Call(func, (Ref(name),))
    if entry[0] == "alias":
        _, representative, sign = entry
        call = Call(func, (Ref(representative),))
        return call if sign > 0 else UnOp("-", call)
    return Num(0.0) if func == "der" else entry[1]


def _replacement(name: str, mapping: Dict[str, tuple]) -> Optional[Expr]:
    entry = mapping.get(name)
    if entry is None:
        return None
    if entry[0] == "alias":
        _, representative, sign = entry
        reference = Ref(representative)
        return reference if sign > 0 else UnOp("-", reference)
    return entry[1]


# =============================================================================
# The pass itself
# =============================================================================

def eliminate_aliases(model: FlatModel, maximum_passes: int = 10) -> AliasResult:
    """
    Remove alias and known-value equations, returning a smaller model.

    One pass is not enough.  Substituting `src.n.v = 0` turns

        c1.v = c1.p.v - c1.n.v      (three variables, not an alias)

    into

        c1.v = r.n.v                (an alias, once the zero is substituted)

    so the pass is repeated until nothing more can be removed.  Real Modelica
    compilers do the same, which is why they can report that a model with
    thousands of flat equations has only a handful of real ones left.
    """
    combined: Dict[str, tuple] = {}
    removed: List[Equation] = []
    current = model

    for _ in range(maximum_passes):
        result = _one_pass(current)
        if not result.removed_equations:
            break
        _compose(combined, result.eliminated)
        removed.extend(result.removed_equations)
        current = result.model

    return AliasResult(model=current, eliminated=combined, removed_equations=removed)


def _compose(combined: Dict[str, tuple], latest: Dict[str, tuple]):
    """
    Fold the newest pass into what earlier passes already found.

    A variable that was mapped to a representative which has now itself been
    eliminated must be re-pointed at whatever replaced it.
    """
    for name, entry in list(combined.items()):
        if entry[0] != "alias":
            continue
        _, representative, sign = entry
        replacement = latest.get(representative)
        if replacement is None:
            continue
        if replacement[0] == "alias":
            combined[name] = ("alias", replacement[1], sign * replacement[2])
        else:
            expression = replacement[1]
            combined[name] = ("known",
                              expression if sign > 0 else UnOp("-", expression))
    combined.update(latest)


def _one_pass(model: FlatModel) -> AliasResult:
    """A single sweep: find the trivial equations and substitute them away."""
    unknowns = set(model.continuous_variables()) | set(model.discrete_variables())
    constants = set(model.parameters())

    # Variables that must survive, because events act on them by name.
    protected = set()
    for when_equation in model.when_equations:
        for statement in when_equation.body:
            protected.add(statement.name)

    # States make the best representatives: the generated code then talks about
    # `c.v` rather than about `c.p.v`.
    states = set()
    for equation in model.equations + model.initial_equations:
        states |= derivative_names(equation.lhs) | derivative_names(equation.rhs)

    # -- 1. sort the equations into aliases, known values, and the rest -------
    alias_equations = []        # (equation, name_a, name_b, sign)
    known_equations = []        # (equation, name, expression)
    kept_equations: List[Equation] = []
    removed: List[Equation] = []

    for equation in model.equations:
        classified = _classify(equation, unknowns, constants)
        if classified is None:
            kept_equations.append(equation)
        elif classified[0] == "alias":
            _, name_a, name_b, sign = classified
            # Never merge across variability: a discrete flag is not a
            # continuous variable, even when an equation relates them.
            if (model.variables[name_a].kind != model.variables[name_b].kind
                    or (name_a in protected and name_b in protected)):
                kept_equations.append(equation)
            else:
                alias_equations.append((equation, name_a, name_b, sign))
        else:
            _, name, expression = classified
            if name in protected:
                kept_equations.append(equation)
            else:
                known_equations.append((equation, name, expression))

    # -- 2. group the aliases: union-find carrying a sign, x = sign * root ----
    root: Dict[str, Tuple[str, int]] = {}

    def find(name: str) -> Tuple[str, int]:
        parent, sign = root.setdefault(name, (name, 1))
        if parent == name:
            return name, sign
        grand_parent, grand_sign = find(parent)
        root[name] = (grand_parent, sign * grand_sign)
        return root[name]

    for equation, name_a, name_b, sign in alias_equations:
        root_a, sign_a = find(name_a)
        root_b, sign_b = find(name_b)
        if root_a == root_b:
            if sign_a * sign_b != sign:
                raise ModelError(f"contradictory equations: {equation.source}")
            kept_equations.append(equation)     # says nothing new, keep the count
            continue
        # a = sign_a * root_a, b = sign_b * root_b, and a = sign * b
        root[root_a] = (root_b, sign * sign_b * sign_a)
        removed.append(equation)

    # -- 3. resolve the known values, now that the groups are final ----------
    known: Dict[str, Expr] = {}
    for equation, name, expression in known_equations:
        group_root, sign = find(name)
        # name = sign * group_root and name = expression, so the root is
        # sign * expression.
        value = expression if sign > 0 else UnOp("-", expression)
        if group_root in known:
            kept_equations.append(equation)     # a second equation for the same
            continue                            # value: redundant, keep it
        known[group_root] = value
        removed.append(equation)

    # -- 4. decide what each eliminated variable is equal to ------------------
    groups: Dict[str, List[str]] = {}
    for name in list(root):
        group_root, _ = find(name)
        groups.setdefault(group_root, []).append(name)

    mapping: Dict[str, tuple] = {}
    for group_root, members in groups.items():
        if group_root in known:
            expression = known[group_root]
            for member in members:
                _, sign = find(member)
                mapping[member] = ("known", expression if sign > 0
                                   else UnOp("-", expression))
            continue
        representative = _pick_representative(members, states, protected, model)
        _, representative_sign = find(representative)
        for member in members:
            if member == representative:
                continue
            _, member_sign = find(member)
            mapping[member] = ("alias", representative,
                               member_sign * representative_sign)

    # Variables pinned to a constant without ever being aliased.
    for group_root, expression in known.items():
        mapping.setdefault(group_root, ("known", expression))

    return _rebuild(model, mapping, kept_equations, removed)


def _pick_representative(members, states, protected, model: FlatModel) -> str:
    """
    Pick the name the reduced model will keep.

    Preference order: a variable an event acts on, then a state, then one with
    an explicit `start` value, then the shortest name.  The aim is purely
    readability of the generated code.
    """
    def rank(name: str):
        variable = model.variables[name]
        return (0 if name in protected else 1,
                0 if name in states else 1,
                0 if variable.start is not None else 1,
                name.count("."), len(name), name)
    return sorted(members, key=rank)[0]


def _rebuild(model: FlatModel, mapping, kept_equations, removed) -> AliasResult:
    """Build the reduced model by substituting everywhere."""
    reduced = FlatModel(name=model.name)
    reduced.parameter_values = dict(model.parameter_values)
    reduced.connection_sets = model.connection_sets
    reduced.variables = {name: variable for name, variable in model.variables.items()
                         if name not in mapping}

    # A `start` value given for an eliminated variable still applies to the
    # variable that replaced it.
    for name, entry in mapping.items():
        if entry[0] != "alias":
            continue
        _, representative, sign = entry
        source = model.variables[name]
        target = reduced.variables[representative]
        if source.start is not None and target.start is None:
            target.start = source.start if sign > 0 else UnOp("-", source.start)

    for equation in kept_equations:
        new_equation = Equation(substitute(equation.lhs, mapping),
                                substitute(equation.rhs, mapping),
                                line=equation.line, origin=equation.origin)
        new_equation.source = equation_to_string(new_equation)
        reduced.equations.append(new_equation)

    for equation in model.initial_equations:
        new_equation = Equation(substitute(equation.lhs, mapping),
                                substitute(equation.rhs, mapping),
                                line=equation.line, origin=equation.origin)
        new_equation.source = equation_to_string(new_equation)
        reduced.initial_equations.append(new_equation)

    for when_equation in model.when_equations:
        body = []
        for statement in when_equation.body:
            value = substitute(statement.value, mapping)
            name = statement.name
            if name in mapping:                     # follow the alias
                entry = mapping[name]
                if entry[0] != "alias":
                    raise ModelError(
                        f"an event assigns to {name!r}, which is fixed by an "
                        f"equation elsewhere")
                _, name, sign = entry
                if sign < 0:
                    value = UnOp("-", value)
            body.append(Assign(name, value, statement.line)
                        if isinstance(statement, Assign)
                        else Reinit(name, value, statement.line))
        reduced.when_equations.append(
            WhenEquation(substitute(when_equation.condition, mapping), body,
                         when_equation.line))

    return AliasResult(model=reduced, eliminated=mapping, removed_equations=removed)
