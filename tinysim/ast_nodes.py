"""
The abstract syntax tree (AST): the data structures the parser produces.

Two families of nodes live here:

* *expressions* -- the right- and left-hand sides of equations, and
* *declarations / equations / classes* -- the structure of a model file.

Everything is a small dataclass, so a parsed model can be inspected in a REPL
just by printing it.  Expressions are frozen (immutable) because the later
stages copy them around freely and must never accidentally share mutable state.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union


# =============================================================================
# Expressions
# =============================================================================

class Expr:
    """Base class for all expression nodes."""


@dataclass(frozen=True)
class Num(Expr):
    """A literal number, e.g. `9.81`."""
    value: float


@dataclass(frozen=True)
class Ref(Expr):
    """A reference to a variable, possibly dotted: `R`, `c.v`, `emf.p.i`."""
    name: str


@dataclass(frozen=True)
class BinOp(Expr):
    """A binary operation: `+ - * / ^ < <= > >= == <> and or`."""
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class UnOp(Expr):
    """A unary operation: `-x` or `not x`."""
    op: str
    operand: Expr


@dataclass(frozen=True)
class Call(Expr):
    """
    A function call: `sin(phi)`, `der(w)`, `pre(on)`.

    `der` and `pre` are parsed as ordinary calls and given their special
    meaning later, in the flattening and simulation stages.  That keeps the
    parser simple and puts the semantics where a reader looks for it.
    """
    func: str
    args: Tuple[Expr, ...]


@dataclass(frozen=True)
class IfExpr(Expr):
    """`if cond then a else b` -- an expression, not a statement."""
    cond: Expr
    then_expr: Expr
    else_expr: Expr


# =============================================================================
# Equations
# =============================================================================

@dataclass
class Equation:
    """`lhs = rhs;` -- a *relation*, not an assignment."""
    lhs: Expr
    rhs: Expr
    line: int = 0
    source: str = ""      # the equation as written in the model file
    origin: str = ""      # where it came from: a class name, or a connect() call


@dataclass
class ConnectEquation:
    """`connect(a, b);` -- records a connection, generates equations later."""
    a: str
    b: str
    line: int = 0


@dataclass
class Assign:
    """`x = expr;` inside a `when` body: a discrete-variable update."""
    name: str
    value: Expr
    line: int = 0


@dataclass
class Reinit:
    """`reinit(x, expr);` inside a `when` body: a jump in a continuous state."""
    name: str
    value: Expr
    line: int = 0


@dataclass
class WhenEquation:
    """`when cond then ... end;` -- fires when `cond` becomes true."""
    condition: Expr
    body: List[Union[Assign, Reinit]]
    line: int = 0


AnyEquation = Union[Equation, ConnectEquation, WhenEquation]


# =============================================================================
# Declarations and classes
# =============================================================================

@dataclass
class Decl:
    """
    One declared name, e.g.

        parameter Real R = 100 "resistance [Ohm]";
        Real v(start = 0);
        Capacitor c(C = 1e-3, v(start = 0));

    `type_name` is either "Real" or the name of a connector/model class, which
    is what decides whether flattening treats this as a variable or as a
    sub-component to be instantiated.
    """
    name: str
    type_name: str
    prefixes: Tuple[str, ...] = ()          # 'parameter' | 'constant' | 'discrete' | 'flow' | 'potential'
    modifiers: Dict[str, object] = field(default_factory=dict)  # str -> Expr or nested dict
    value: Optional[Expr] = None            # binding equation, `= expr`
    description: str = ""
    line: int = 0

    # -- convenience predicates, used all over the later stages ---------------
    @property
    def is_parameter(self) -> bool:
        return "parameter" in self.prefixes or "constant" in self.prefixes

    @property
    def is_discrete(self) -> bool:
        return "discrete" in self.prefixes

    @property
    def is_flow(self) -> bool:
        return "flow" in self.prefixes

    @property
    def is_variable(self) -> bool:
        """True for `Real` declarations; False for sub-component declarations."""
        return self.type_name == "Real"


@dataclass
class ClassDef:
    """A `model` or `connector` definition."""
    kind: str                    # 'model' | 'connector'
    name: str
    partial: bool = False
    extends: List[str] = field(default_factory=list)
    decls: List[Decl] = field(default_factory=list)
    equations: List[AnyEquation] = field(default_factory=list)
    initial_equations: List[Equation] = field(default_factory=list)
    description: str = ""
    line: int = 0


@dataclass
class Program:
    """A parsed file: all the classes it defines, in declaration order."""
    classes: Dict[str, ClassDef] = field(default_factory=dict)

    def __getitem__(self, name: str) -> ClassDef:
        if name not in self.classes:
            known = ", ".join(sorted(self.classes)) or "(none)"
            raise KeyError(f"no class named {name!r}; defined here: {known}")
        return self.classes[name]


# =============================================================================
# Printing expressions back as text
# =============================================================================

# Binding powers, used to decide where parentheses are actually needed, so that
# printed equations look like what the student wrote.
_PRECEDENCE = {
    "or": 1, "and": 2,
    "<": 3, "<=": 3, ">": 3, ">=": 3, "==": 3, "<>": 3,
    "+": 4, "-": 4,
    "*": 5, "/": 5,
    "^": 7,
}


def to_string(expr: Expr, parent_precedence: int = 0) -> str:
    """Render an expression as readable TinySim source text."""
    if isinstance(expr, Num):
        value = expr.value
        return str(int(value)) if value == int(value) and abs(value) < 1e15 else repr(value)
    if isinstance(expr, Ref):
        return expr.name
    if isinstance(expr, Call):
        return f"{expr.func}({', '.join(to_string(a) for a in expr.args)})"
    if isinstance(expr, UnOp):
        if expr.op == "not":
            return f"not {to_string(expr.operand, 6)}"
        return f"-{to_string(expr.operand, 6)}"
    if isinstance(expr, IfExpr):
        text = (f"if {to_string(expr.cond)} then {to_string(expr.then_expr)} "
                f"else {to_string(expr.else_expr)}")
        return f"({text})" if parent_precedence > 0 else text
    if isinstance(expr, BinOp):
        # `a + -b` reads better as `a - b`.
        if expr.op == "+" and isinstance(expr.right, UnOp) and expr.right.op == "-":
            return to_string(BinOp("-", expr.left, expr.right.operand),
                             parent_precedence)
        precedence = _PRECEDENCE[expr.op]
        left = to_string(expr.left, precedence)
        right = to_string(expr.right, precedence + 1)   # +1: keep a-(b-c) parenthesised
        text = f"{left} {expr.op} {right}" if expr.op not in ("^",) else f"{left}^{right}"
        return f"({text})" if precedence < parent_precedence else text
    raise TypeError(f"cannot print unknown expression node {expr!r}")


def equation_to_string(equation: Equation) -> str:
    """Render `lhs = rhs` as text."""
    return f"{to_string(equation.lhs)} = {to_string(equation.rhs)}"
