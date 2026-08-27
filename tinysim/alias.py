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


def _classify(equation: Equation, unknowns: set) -> Optional[tuple]:
    """
    Decide whether `equation` is an alias, a known value, or neither.

    Returns ('alias', a, b, sign), ('known', name, expression) or None.
    Everything is first moved to one side, so `a = -b` and `a + b = 0` are
    recognised as the same thing.
    """
    left = _additive_terms(equation.lhs, +1)
    right = _additive_terms(equation.rhs, -1)
    if left is None or right is None:
        return None
    terms = left + right

    variable_terms = [(s, t.name) for s, t in terms
                      if isinstance(t, Ref) and t.name in unknowns]
    other_terms = [(s, t) for s, t in terms
                   if not (isinstance(t, Ref) and t.name in unknowns)]

    if len(variable_terms) == 2 and not other_terms:
        # s1*a + s2*b = 0   ->   a = -(s2/s1) * b
        (sign_a, name_a), (sign_b, name_b) = variable_terms
        if name_a == name_b:
            return None
        return "alias", name_a, name_b, -sign_a * sign_b

    if len(variable_terms) == 1 and other_terms:
        # s*v + C = 0   ->   v = -s*C
        sign, name = variable_terms[0]
        constant = None
        for term_sign, term in other_terms:
            piece = term if term_sign * sign < 0 else UnOp("-", term)
            constant = piece if constant is None else BinOp("+", constant, piece)
        return "known", name, constant

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
            name = expr.args[0].name
            replacement = _replacement(name, mapping)
            if replacement is None:
                return expr
            # der(-x) is not writable, so a negated alias becomes -der(x).
            if isinstance(replacement, Ref):
                return Call(expr.func, (replacement,))
            if (isinstance(replacement, UnOp) and replacement.op == "-"
                    and isinstance(replacement.operand, Ref)):
                return UnOp("-", Call(expr.func, (replacement.operand,)))
            if expr.func == "der":
                return Num(0.0)     # derivative of a constant expression
            return replacement
        return Call(expr.func, tuple(substitute(a, mapping) for a in expr.args))
    raise TypeError(f"cannot substitute in {expr!r}")


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

def eliminate_aliases(model: FlatModel) -> AliasResult:
    """Remove alias and known-value equations, returning a smaller model."""
    unknowns = set(model.continuous_variables()) | set(model.discrete_variables())

    # Variables that must survive, because events act on them by name.
    protected = set()
    for when_equation in model.when_equations:
        for statement in when_equation.body:
            protected.add(statement.name)

    # Variables that are states: they make the best representatives, because
    # the generated code then talks about `c.v` rather than `c.p.v`.
    states = set()
    for equation in model.equations + model.initial_equations:
        states |= derivative_names(equation.lhs) | derivative_names(equation.rhs)

    # -- union-find with signs: x = sign * root ------------------------------
    root: Dict[str, Tuple[str, int]] = {}

    def find(name: str) -> Tuple[str, int]:
        parent, sign = root.setdefault(name, (name, 1))
        if parent == name:
            return name, sign
        grand_parent, grand_sign = find(parent)
        root[name] = (grand_parent, sign * grand_sign)
        return root[name]

    def union(name_a: str, name_b: str, sign: int) -> bool:
        """Record a = sign * b.  Returns False if that contradicts what we know."""
        root_a, sign_a = find(name_a)
        root_b, sign_b = find(name_b)
        if root_a == root_b:
            return sign_a * sign_b == sign
        # a = sign_a * root_a, b = sign_b * root_b, a = sign * b
        root[root_a] = (root_b, sign * sign_b * sign_a)
        return True

    known: Dict[str, Expr] = {}          # group root -> constant expression
    kept_equations: List[Equation] = []
    removed: List[Equation] = []

    for equation in model.equations:
        classified = _classify(equation, unknowns)
        if classified is None:
            kept_equations.append(equation)
            continue
        if classified[0] == "alias":
            _, name_a, name_b, sign = classified
            # Never merge across variability: a discrete flag is not a
            # continuous variable, even when an equation relates them.
            if (model.variables[name_a].kind != model.variables[name_b].kind
                    or name_a in protected and name_b in protected):
                kept_equations.append(equation)
                continue
            if not union(name_a, name_b, sign):
                raise ModelError(
                    f"contradictory equations: {equation.source}")
            removed.append(equation)
        else:
            _, name, expression = classified
            if name in protected:
                kept_equations.append(equation)
                continue
            group_root, sign = find(name)
            value = expression if sign > 0 else UnOp("-", expression)
            if group_root in known:
                kept_equations.append(equation)      # already pinned: keep as a check
                continue
            known[group_root] = value
            removed.append(equation)

    # -- choose a representative for every alias group -----------------------
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
                value = expression if sign > 0 else UnOp("-", expression)
                mapping[member] = ("known", value)
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
