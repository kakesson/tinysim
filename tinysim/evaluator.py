"""
A direct numeric evaluator for expressions.

Later stages hand most expressions to SymPy so they can be *solved*, but a few
places only need a number right now: parameter values, `start` attributes, and
the right-hand sides of assignments inside `when` clauses at event time.  This
module is that small, obvious evaluator.
"""

import math

from .ast_nodes import BinOp, Call, Expr, IfExpr, Num, Ref, UnOp

#: Functions available in model equations, mapped to their Python implementation.
FUNCTIONS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "atan2": math.atan2, "exp": math.exp, "log": math.log,
    "log10": math.log10, "sqrt": math.sqrt, "abs": abs,
    "sign": lambda x: math.copysign(1.0, x) if x != 0 else 0.0,
    "tanh": math.tanh, "min": min, "max": max,
}


class EvaluationError(Exception):
    """Raised when an expression cannot be evaluated with the given values."""


def evaluate(expr: Expr, values: dict) -> float:
    """
    Evaluate `expr` numerically, looking names up in `values`.

    Booleans are represented as 1.0 / 0.0, which is also how `discrete Real`
    variables act as flags in TinySim models.
    """
    if isinstance(expr, Num):
        return expr.value

    if isinstance(expr, Ref):
        if expr.name not in values:
            raise EvaluationError(f"value of {expr.name!r} is not known here")
        return float(values[expr.name])

    if isinstance(expr, UnOp):
        operand = evaluate(expr.operand, values)
        return -operand if expr.op == "-" else float(not operand)

    if isinstance(expr, BinOp):
        left = evaluate(expr.left, values)
        right = evaluate(expr.right, values)
        return _apply_binary(expr.op, left, right)

    if isinstance(expr, Call):
        if expr.func == "pre":
            # `pre(x)` is resolved by the simulator, which knows the pre-event
            # values; it puts them into `values` under the name "pre(x)".
            key = f"pre({expr.args[0].name})"
            if key not in values:
                raise EvaluationError(f"no pre-event value available for {key}")
            return float(values[key])
        if expr.func == "der":
            key = f"der({expr.args[0].name})"
            if key not in values:
                raise EvaluationError(f"value of {key} is not known here")
            return float(values[key])
        arguments = [evaluate(a, values) for a in expr.args]
        return float(FUNCTIONS[expr.func](*arguments))

    if isinstance(expr, IfExpr):
        condition = evaluate(expr.cond, values)
        branch = expr.then_expr if condition else expr.else_expr
        return evaluate(branch, values)

    raise EvaluationError(f"cannot evaluate {expr!r}")


def _apply_binary(op: str, left: float, right: float) -> float:
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        return left / right
    if op == "^":
        return left ** right
    if op == "<":
        return float(left < right)
    if op == "<=":
        return float(left <= right)
    if op == ">":
        return float(left > right)
    if op == ">=":
        return float(left >= right)
    if op == "==":
        return float(left == right)
    if op == "<>":
        return float(left != right)
    if op == "and":
        return float(bool(left) and bool(right))
    if op == "or":
        return float(bool(left) or bool(right))
    raise EvaluationError(f"unknown operator {op!r}")


def free_names(expr: Expr, found=None) -> set:
    """All variable names an expression refers to (`time` included)."""
    found = set() if found is None else found
    if isinstance(expr, Ref):
        found.add(expr.name)
    elif isinstance(expr, UnOp):
        free_names(expr.operand, found)
    elif isinstance(expr, BinOp):
        free_names(expr.left, found)
        free_names(expr.right, found)
    elif isinstance(expr, IfExpr):
        free_names(expr.cond, found)
        free_names(expr.then_expr, found)
        free_names(expr.else_expr, found)
    elif isinstance(expr, Call):
        for argument in expr.args:
            free_names(argument, found)
    return found
