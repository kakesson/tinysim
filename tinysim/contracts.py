"""
Assume-guarantee contracts: the syntax tree, and what the sugar means.

A contract says what a model needs from its environment and what it promises in
return:

    contract ChargesInTime for RCCircuit
      "the capacitor reaches 95 % of the source voltage within half a second"
    assume
      always src.V >= 5 and src.V <= 15;
    guarantee
      eventually within [0, 0.5] c.v >= 0.95 * src.V;
    end ChargesInTime;

Formally a contract is the pair `(A, G)` and it is read as *A implies G*: a run
in which the assumption fails says nothing about the component.  That is what
makes "not tested" a third verdict rather than a pass -- see `monitor.py`.

This module holds two things.

**The surface tree** is what the user wrote, patterns and all: `whenever`,
`stays within`, `after`, `never`.  It exists so the reports can print the
requirement back in the words it was written in.

**The core tree** is Signal Temporal Logic: predicates, the Boolean
connectives, and `always`, `eventually`, `until` over a time window.
`desugar()` maps the first onto the second, and the reports print both, side by
side -- seeing that translation is most of the point of the exercise.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

from .ast_nodes import Expr, to_string

#: A time window `[a, b]`; `None` for the upper bound means "to the end of the
#: run", which is what an unbounded `always` or `eventually` gets.
Window = Tuple[Optional[Expr], Optional[Expr]]


class Formula:
    """Base class for every node, surface and core alike."""


# =============================================================================
# Core nodes: Signal Temporal Logic and nothing else
# =============================================================================

@dataclass
class Predicate(Formula):
    """A comparison between two model expressions: `c.v >= 0.95 * src.V`."""
    op: str                      # < <= > >= == <>
    left: Expr
    right: Expr


@dataclass
class Not(Formula):
    formula: Formula


@dataclass
class And(Formula):
    parts: List[Formula]


@dataclass
class Or(Formula):
    parts: List[Formula]


@dataclass
class Implies(Formula):
    left: Formula
    right: Formula


@dataclass
class Always(Formula):
    """`G[a,b] phi` -- true at t when phi holds everywhere in t+[a,b]."""
    formula: Formula
    window: Window = (None, None)


@dataclass
class Eventually(Formula):
    """`F[a,b] phi` -- true at t when phi holds somewhere in t+[a,b]."""
    formula: Formula
    window: Window = (None, None)


@dataclass
class Until(Formula):
    """`phi U[a,b] psi` -- phi holds until psi does, and psi does in t+[a,b]."""
    left: Formula
    right: Formula
    window: Window = (None, None)


@dataclass
class Rise(Formula):
    """The instant a condition *becomes* true: false before, true now."""
    formula: Formula


@dataclass
class AtStart(Formula):
    formula: Formula


@dataclass
class AtEnd(Formula):
    formula: Formula


# =============================================================================
# Surface nodes: the patterns, which exist to be read
# =============================================================================

@dataclass
class Never(Formula):
    """`never phi` -- reads better than `always not phi`, means the same."""
    formula: Formula


@dataclass
class After(Formula):
    """`after t always phi` -- the requirement starts to apply at t."""
    time: Expr
    formula: Formula


@dataclass
class During(Formula):
    """`during [a, b] phi`."""
    window: Window
    formula: Formula


@dataclass
class Whenever(Formula):
    """
    `whenever c then r within [a, b]`, or `... then r holds for d`.

    The EARS/FRET shape: a trigger, a response, and a deadline.  This is the
    pattern most requirements turn out to be.
    """
    trigger: Formula
    response: Formula
    window: Window
    holds: bool = False          # True for `holds for d`, False for `within`


@dataclass
class StaysWithin(Formula):
    """`x stays within [lo, hi]`."""
    subject: Expr
    low: Expr
    high: Expr


@dataclass
class SettlesTo(Formula):
    """`x settles to value within tolerance after t`."""
    subject: Expr
    value: Expr
    tolerance: Expr
    after: Optional[Expr] = None


# =============================================================================
# A contract
# =============================================================================

@dataclass
class Clause:
    """One line of an `assume` or `guarantee` section."""
    formula: Formula             # as written, patterns included
    line: int = 0

    @property
    def core(self) -> Formula:
        """The same clause as plain Signal Temporal Logic."""
        return desugar(self.formula)

    @property
    def written(self) -> str:
        return to_text(self.formula)

    @property
    def stl(self) -> str:
        return to_stl(self.core)


@dataclass
class Contract:
    """`contract Name for Model ... end Name;`"""
    name: str
    model_name: str
    description: str = ""
    assumptions: List[Clause] = field(default_factory=list)
    guarantees: List[Clause] = field(default_factory=list)
    line: int = 0

    def clauses(self):
        for clause in self.assumptions:
            yield "assume", clause
        for clause in self.guarantees:
            yield "guarantee", clause


# =============================================================================
# Desugaring: every pattern is one of the core operators
# =============================================================================

def _always(formula: Formula, window: Window = (None, None)) -> Always:
    """
    Build `G[window] formula`, collapsing a redundant nesting on the way.

    `after 2 (x settles to 3 within 0.1)` desugars to `G[2,end](G(...))`, and
    `G[a,b] G phi` says exactly what `G[a,end] phi` says -- taking the earliest
    moment of the outer window already reaches the end of the run.  Collapsing
    it keeps the printed logic honest about how simple the requirement really
    is, and keeps it within reach of tools that do not nest operators.
    """
    if isinstance(formula, Always) and formula.window == (None, None):
        return Always(formula.formula, (window[0], None))
    return Always(formula, window)


def desugar(formula: Formula) -> Formula:
    """Rewrite the surface tree into pure Signal Temporal Logic."""
    if isinstance(formula, Predicate):
        return formula
    if isinstance(formula, Not):
        return Not(desugar(formula.formula))
    if isinstance(formula, And):
        return And([desugar(part) for part in formula.parts])
    if isinstance(formula, Or):
        return Or([desugar(part) for part in formula.parts])
    if isinstance(formula, Implies):
        return Implies(desugar(formula.left), desugar(formula.right))
    if isinstance(formula, Always):
        return _always(desugar(formula.formula), formula.window)
    if isinstance(formula, Eventually):
        return Eventually(desugar(formula.formula), formula.window)
    if isinstance(formula, Until):
        return Until(desugar(formula.left), desugar(formula.right), formula.window)
    if isinstance(formula, Rise):
        return Rise(desugar(formula.formula))
    if isinstance(formula, AtStart):
        return AtStart(desugar(formula.formula))
    if isinstance(formula, AtEnd):
        return AtEnd(desugar(formula.formula))

    # -- the patterns ---------------------------------------------------------
    if isinstance(formula, Never):
        return _always(Not(desugar(formula.formula)))
    if isinstance(formula, After):
        return _always(desugar(formula.formula), (formula.time, None))
    if isinstance(formula, During):
        return _always(desugar(formula.formula), formula.window)
    if isinstance(formula, Whenever):
        inner = (Always(desugar(formula.response), formula.window) if formula.holds
                 else Eventually(desugar(formula.response), formula.window))
        return Always(Implies(Rise(desugar(formula.trigger)), inner))   # no collapse
    if isinstance(formula, StaysWithin):
        return _always(And([Predicate(">=", formula.subject, formula.low),
                            Predicate("<=", formula.subject, formula.high)]))
    if isinstance(formula, SettlesTo):
        from .ast_nodes import BinOp, Call
        distance = Call("abs", (BinOp("-", formula.subject, formula.value),))
        window = (formula.after, None) if formula.after is not None else (None, None)
        return _always(Predicate("<=", distance, formula.tolerance), window)
    raise TypeError(f"cannot desugar {formula!r}")


# =============================================================================
# Printing: what was written, and what it means
# =============================================================================

def _window_text(window: Window) -> str:
    low, high = window
    if low is None and high is None:
        return ""
    low_text = "0" if low is None else to_string(low)
    high_text = "end" if high is None else to_string(high)
    return f"[{low_text}, {high_text}]"


def to_text(formula: Formula) -> str:
    """Print a formula the way it was written, patterns included."""
    if isinstance(formula, Predicate):
        return f"{to_string(formula.left)} {formula.op} {to_string(formula.right)}"
    if isinstance(formula, Not):
        return f"not {to_text(formula.formula)}"
    if isinstance(formula, And):
        return " and ".join(_bracket(part) for part in formula.parts)
    if isinstance(formula, Or):
        return " or ".join(_bracket(part) for part in formula.parts)
    if isinstance(formula, Implies):
        return f"{_bracket(formula.left)} implies {_bracket(formula.right)}"
    if isinstance(formula, Always):
        window = _window_text(formula.window)
        prefix = "always" + (f" within {window}" if window else "")
        return f"{prefix} {_bracket(formula.formula)}"
    if isinstance(formula, Eventually):
        window = _window_text(formula.window)
        prefix = "eventually" + (f" within {window}" if window else "")
        return f"{prefix} {_bracket(formula.formula)}"
    if isinstance(formula, Until):
        window = _window_text(formula.window)
        middle = f"until within {window}" if window else "until"
        return f"{_bracket(formula.left)} {middle} {_bracket(formula.right)}"
    if isinstance(formula, Rise):
        return f"rise({to_text(formula.formula)})"
    if isinstance(formula, AtStart):
        return f"at start {_bracket(formula.formula)}"
    if isinstance(formula, AtEnd):
        return f"at end {_bracket(formula.formula)}"
    if isinstance(formula, Never):
        return f"never {_bracket(formula.formula)}"
    if isinstance(formula, After):
        return f"after {to_string(formula.time)} {_bracket(formula.formula)}"
    if isinstance(formula, During):
        return f"during {_window_text(formula.window)} {_bracket(formula.formula)}"
    if isinstance(formula, Whenever):
        tail = ("holds for " + to_string(formula.window[1])
                if formula.holds else "within " + _window_text(formula.window))
        return (f"whenever {_bracket(formula.trigger)} then "
                f"{_bracket(formula.response)} {tail}")
    if isinstance(formula, StaysWithin):
        return (f"{to_string(formula.subject)} stays within "
                f"[{to_string(formula.low)}, {to_string(formula.high)}]")
    if isinstance(formula, SettlesTo):
        text = (f"{to_string(formula.subject)} settles to "
                f"{to_string(formula.value)} within {to_string(formula.tolerance)}")
        return text + (f" after {to_string(formula.after)}"
                       if formula.after is not None else "")
    raise TypeError(f"cannot print {formula!r}")


def _bracket(formula: Formula) -> str:
    """Parenthesise anything that is not already a single comparison."""
    text = to_text(formula)
    if isinstance(formula, (Predicate, Rise)):
        return text
    return f"({text})"


def to_stl(formula: Formula) -> str:
    """
    Print the core formula in the usual operator notation.

    `G` is always, `F` is eventually, `U` is until, and a subscript is the time
    window the operator looks at, measured from wherever it is evaluated.
    """
    if isinstance(formula, Predicate):
        return f"{to_string(formula.left)} {formula.op} {to_string(formula.right)}"
    if isinstance(formula, Not):
        return f"!({to_stl(formula.formula)})"
    if isinstance(formula, And):
        return " & ".join(_stl_bracket(part) for part in formula.parts)
    if isinstance(formula, Or):
        return " | ".join(_stl_bracket(part) for part in formula.parts)
    if isinstance(formula, Implies):
        return f"{_stl_bracket(formula.left)} -> {_stl_bracket(formula.right)}"
    if isinstance(formula, Always):
        return f"G{_window_text(formula.window)}({to_stl(formula.formula)})"
    if isinstance(formula, Eventually):
        return f"F{_window_text(formula.window)}({to_stl(formula.formula)})"
    if isinstance(formula, Until):
        return (f"{_stl_bracket(formula.left)} U{_window_text(formula.window)} "
                f"{_stl_bracket(formula.right)}")
    if isinstance(formula, Rise):
        return f"rise({to_stl(formula.formula)})"
    if isinstance(formula, AtStart):
        return f"at_start({to_stl(formula.formula)})"
    if isinstance(formula, AtEnd):
        return f"at_end({to_stl(formula.formula)})"
    raise TypeError(f"{formula!r} is not a core formula; desugar it first")


def _stl_bracket(formula: Formula) -> str:
    text = to_stl(formula)
    return text if isinstance(formula, (Predicate, Rise)) else f"({text})"


def prefix_formula(formula: Formula, prefix: str) -> Formula:
    """
    Move a formula into the namespace of one component instance.

    A contract written for the class `Inductor` talks about `i` and `v`; for
    the instance `l` inside a `DCMotor` it has to talk about `l.i` and `l.v`.
    That is the same rewriting flattening does to equations, and it is what
    lets one contract be checked separately for every instance.
    """
    from .flatten import prefix_names

    def expression(value):
        return prefix_names(value, prefix) if value is not None else None

    def recurse(node: Formula) -> Formula:
        if isinstance(node, Predicate):
            return Predicate(node.op, expression(node.left), expression(node.right))
        if isinstance(node, Not):
            return Not(recurse(node.formula))
        if isinstance(node, And):
            return And([recurse(part) for part in node.parts])
        if isinstance(node, Or):
            return Or([recurse(part) for part in node.parts])
        if isinstance(node, Implies):
            return Implies(recurse(node.left), recurse(node.right))
        if isinstance(node, (Always, Eventually)):
            window = (expression(node.window[0]), expression(node.window[1]))
            return type(node)(recurse(node.formula), window)
        if isinstance(node, Until):
            window = (expression(node.window[0]), expression(node.window[1]))
            return Until(recurse(node.left), recurse(node.right), window)
        if isinstance(node, (Rise, AtStart, AtEnd, Never)):
            return type(node)(recurse(node.formula))
        if isinstance(node, After):
            return After(expression(node.time), recurse(node.formula))
        if isinstance(node, During):
            window = (expression(node.window[0]), expression(node.window[1]))
            return During(window, recurse(node.formula))
        if isinstance(node, Whenever):
            window = (expression(node.window[0]), expression(node.window[1]))
            return Whenever(recurse(node.trigger), recurse(node.response),
                            window, node.holds)
        if isinstance(node, StaysWithin):
            return StaysWithin(expression(node.subject), expression(node.low),
                               expression(node.high))
        if isinstance(node, SettlesTo):
            return SettlesTo(expression(node.subject), expression(node.value),
                             expression(node.tolerance), expression(node.after))
        raise TypeError(f"cannot move {node!r} into a namespace")

    return recurse(formula)


def prefix_contract(contract: Contract, prefix: str) -> Contract:
    """A copy of `contract` written in the namespace of one instance."""
    if not prefix:
        return contract
    return Contract(
        name=contract.name, model_name=contract.model_name,
        description=contract.description, line=contract.line,
        assumptions=[Clause(prefix_formula(c.formula, prefix), c.line)
                     for c in contract.assumptions],
        guarantees=[Clause(prefix_formula(c.formula, prefix), c.line)
                    for c in contract.guarantees])


def references(formula: Formula, found=None) -> set:
    """Every model name a formula mentions, so it can be checked to exist."""
    from .evaluator import free_names
    found = set() if found is None else found
    for attribute in ("left", "right", "subject", "value", "tolerance",
                      "after", "time", "low", "high"):
        child = getattr(formula, attribute, None)
        if isinstance(child, Expr):
            free_names(child, found)
        elif isinstance(child, Formula):
            references(child, found)
    for attribute in ("formula", "trigger", "response"):
        child = getattr(formula, attribute, None)
        if isinstance(child, Formula):
            references(child, found)
    for part in getattr(formula, "parts", []):
        references(part, found)
    window = getattr(formula, "window", None)
    if window:
        for bound in window:
            if isinstance(bound, Expr):
                free_names(bound, found)
    return found
