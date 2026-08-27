"""
Stage 5 of the pipeline: *code generation*.

At this point the model is a list of equations sorted into blocks that can be
solved one after the other.  This module turns that into a Python function --
literally, into Python source text that is printed for the student to read and
then compiled with `exec`.  Nothing is hidden: what runs is what is shown.

Each block becomes one or a few lines of code:

* a block of size 1 that is **linear** in its unknown is *solved symbolically*
  with SymPy and becomes an assignment, `r__i = r__v / r__R`;
* a block of size 1 that is nonlinear is solved symbolically if SymPy manages,
  and otherwise becomes a call to a numerical root finder;
* a block of size > 1 is an **algebraic loop**: linear loops become a small
  matrix solve, nonlinear loops a call to `fsolve`.

Because the model's dotted names (`c.v`) are not valid Python identifiers, they
are mangled to `c__v` in the generated code.  The mapping is printed in the
header so the code stays readable.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import sympy

from .analysis import StructuralAnalysis, der_name
from .ast_nodes import (
    BinOp, Call, Equation, Expr, IfExpr, Num, Ref, UnOp, equation_to_string,
)
from .flatten import FlatModel, ModelError

#: Model functions and their SymPy counterparts.
_SYMPY_FUNCTIONS = {
    "sin": sympy.sin, "cos": sympy.cos, "tan": sympy.tan,
    "asin": sympy.asin, "acos": sympy.acos, "atan": sympy.atan,
    "atan2": sympy.atan2, "exp": sympy.exp, "log": sympy.log,
    "log10": lambda x: sympy.log(x, 10), "sqrt": sympy.sqrt,
    "abs": sympy.Abs, "sign": sympy.sign, "tanh": sympy.tanh,
    "min": sympy.Min, "max": sympy.Max,
}

_RELATIONS = {
    "<": sympy.Lt, "<=": sympy.Le, ">": sympy.Gt, ">=": sympy.Ge,
    "==": sympy.Eq, "<>": sympy.Ne,
}


def mangle(name: str) -> str:
    """`c.v` -> `c__v`, and `der(c.v)` -> `der_c__v`."""
    if name.startswith("der(") and name.endswith(")"):
        return "der_" + mangle(name[4:-1])
    return name.replace(".", "__")


# =============================================================================
# Model expressions -> SymPy expressions
# =============================================================================

def to_sympy(expr: Expr):
    """Translate a TinySim expression into a SymPy expression."""
    if isinstance(expr, Num):
        return sympy.Float(expr.value)
    if isinstance(expr, Ref):
        return sympy.Symbol("t" if expr.name == "time" else mangle(expr.name))
    if isinstance(expr, UnOp):
        operand = to_sympy(expr.operand)
        return -operand if expr.op == "-" else sympy.Not(operand)
    if isinstance(expr, BinOp):
        left, right = to_sympy(expr.left), to_sympy(expr.right)
        if expr.op == "+":
            return left + right
        if expr.op == "-":
            return left - right
        if expr.op == "*":
            return left * right
        if expr.op == "/":
            return left / right
        if expr.op == "^":
            return left ** right
        if expr.op in _RELATIONS:
            return _RELATIONS[expr.op](left, right)
        if expr.op == "and":
            return sympy.And(left, right)
        if expr.op == "or":
            return sympy.Or(left, right)
    if isinstance(expr, Call):
        if expr.func == "der":
            return sympy.Symbol(mangle(der_name(expr.args[0].name)))
        if expr.func == "pre":
            # Between events `pre(x)` and `x` are the same value; the simulator
            # gives `pre(x)` its proper meaning when an event fires.
            return sympy.Symbol(mangle(expr.args[0].name))
        arguments = [to_sympy(a) for a in expr.args]
        return _SYMPY_FUNCTIONS[expr.func](*arguments)
    if isinstance(expr, IfExpr):
        return sympy.Piecewise((to_sympy(expr.then_expr), to_sympy(expr.cond)),
                               (to_sympy(expr.else_expr), True))
    raise TypeError(f"cannot translate {expr!r} to SymPy")


def to_python(sympy_expr) -> str:
    """Print a SymPy expression as a line of Python source."""
    text = sympy.pycode(sympy_expr, fully_qualified_modules=False)
    if "ImmutableDenseMatrix" in text or "Symbol" in text:      # pragma: no cover
        raise ModelError(f"cannot generate code for {sympy_expr}")
    return text


# =============================================================================
# The generated program
# =============================================================================

@dataclass
class BlockCode:
    """How one BLT block was solved, for the report and the generated header."""
    index: int
    equations: List[int]
    unknowns: List[str]
    method: str            # 'explicit' | 'symbolic' | 'linear system' | 'newton'
    lines: List[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.equations)


@dataclass
class GeneratedCode:
    """The generated Python source, and the compiled function."""
    source: str
    function: object
    blocks: List[BlockCode]
    state_names: List[str]
    variable_names: List[str]
    event_conditions: List[str]

    def __str__(self) -> str:              # so `print(code)` shows the source
        return self.source


class CodeGenerator:
    """Turns a sorted equation system into readable Python source."""

    def __init__(self, model: FlatModel, analysis: StructuralAnalysis,
                 eliminated: Optional[Dict[str, tuple]] = None,
                 function_name: str = "evaluate"):
        self.model = model
        self.analysis = analysis
        self.eliminated = eliminated or {}
        self.function_name = function_name
        self.blocks: List[BlockCode] = []
        self._check_name_clashes()

    def _check_name_clashes(self):
        """`a.b` and `a__b` would both become `a__b` in the generated code."""
        seen = {}
        # Eliminated variables are recovered in the generated code too, so they
        # take part in the clash.
        for name in list(self.model.variables) + list(self.eliminated):
            mangled = mangle(name)
            if mangled in seen:
                raise ModelError(
                    f"variables {seen[mangled]!r} and {name!r} would both be "
                    f"called {mangled!r} in the generated code; rename one")
            seen[mangled] = name

    # -- the whole module ----------------------------------------------------

    def generate(self) -> GeneratedCode:
        analysis = self.analysis
        states = analysis.states
        solving_for_states = analysis.kind == "initialization"

        lines: List[str] = []
        lines += self._header()
        signature = ("def {}(t, p, d, guess=None):" if solving_for_states
                     else "def {}(t, x, p, d, guess=None):").format(self.function_name)
        lines.append(signature)
        lines.append('    """')
        lines.append(f"    Solve the {analysis.kind} equations of model "
                     f"{self.model.name!r}.")
        lines.append("")
        lines.append("    t     : time")
        if not solving_for_states:
            lines.append("    x     : state vector, in the order listed below")
        lines.append("    p     : dict of parameter values, keyed by model name")
        lines.append("    d     : dict of discrete-variable values")
        lines.append("    guess : dict used to remember iteration start values")
        lines.append('    """')
        lines.append("    guess = {} if guess is None else guess")
        lines.append("")

        if not solving_for_states and states:
            lines.append("    # ---- states: supplied by the integrator ----")
            for position, state in enumerate(states):
                lines.append(f"    {mangle(state)} = x[{position}]"
                             f"{self._comment(state)}")
            lines.append("")

        if self.model.parameter_values:
            lines.append("    # ---- parameters: constant during simulation ----")
            for name in sorted(self.model.parameter_values):
                lines.append(f"    {mangle(name)} = p[{name!r}]"
                             f"{self._comment(name)}")
            lines.append("")

        discretes = self.model.discrete_variables()
        if discretes:
            lines.append("    # ---- discrete variables: constant between events ----")
            for name in discretes:
                lines.append(f"    {mangle(name)} = d[{name!r}]"
                             f"{self._comment(name)}")
            lines.append("")

        if solving_for_states and states:
            lines.append("    # ---- states are unknown here and solved for below ----")
            lines.append("")

        for position, block in enumerate(analysis.blocks, start=1):
            lines += self._emit_block(position, block)

        lines += self._emit_results(solving_for_states)

        source = "\n".join(lines) + "\n"
        namespace = {}
        exec(compile(source, f"<generated {self.model.name}>", "exec"), namespace)
        return GeneratedCode(
            source=source, function=namespace[self.function_name],
            blocks=self.blocks, state_names=list(states),
            variable_names=self._output_names(),
            event_conditions=[c for c, _ in self._event_expressions()],
        )

    def _header(self) -> List[str]:
        analysis = self.analysis
        loops = [b for b in analysis.blocks if len(b) > 1]
        return [
            f"# " + "=" * 74,
            f"# Simulation code generated by TinySim for model "
            f"{self.model.name!r}.",
            f"#",
            f"# {len(analysis.equations)} equations, {len(analysis.unknowns)} unknowns, "
            f"sorted into {len(analysis.blocks)} blocks"
            + (f" ({len(loops)} algebraic loop(s))" if loops else " (no algebraic loops)"),
            f"# States: " + (", ".join(analysis.states) or "none"),
            f"#",
            f"# Dots in model names become double underscores here: c.v -> c__v.",
            f"# " + "=" * 74,
            "",
            "from math import (sin, cos, tan, asin, acos, atan, atan2, exp, log,",
            "                  log10, sqrt, tanh, fabs as abs)",
            "import numpy as np",
            "from scipy.optimize import fsolve",
            "",
            "",
            "def _solve_block(residual, guess, block, unknowns, t):",
            '    """',
            "    Solve one algebraic block numerically.",
            "",
            "    An iterative solver needs a starting point, and a bad one can",
            "    make it fail on a system that has a perfectly good solution --",
            "    which is exactly why `start` values matter for the variables of",
            "    an algebraic loop.  This tries the remembered guess first, then",
            "    a fresh start from zero, and refuses to continue quietly if",
            "    neither works.",
            '    """',
            "    for attempt in (guess, [0.0] * len(guess)):",
            "        values, _, flag, message = fsolve(residual, attempt,",
            "                                          full_output=True)",
            "        if flag == 1:",
            "            return values",
            "    raise RuntimeError(",
            "        'block %d (%s) did not converge at t = %g: %s'",
            "        % (block, unknowns, t, message.strip()))",
            "",
            "",
        ]

    def _comment(self, name: str) -> str:
        variable = self.model.variables.get(name)
        description = variable.description if variable else ""
        label = f"{name}" + (f" -- {description}" if description else "")
        return f"    # {label}"

    # -- one block -----------------------------------------------------------

    def _emit_block(self, position: int, block: List[int]) -> List[str]:
        equations = [self.analysis.equations[i] for i in block]
        unknowns = [self.analysis.matching[i] for i in block]
        symbols = [sympy.Symbol(mangle(u)) for u in unknowns]
        residuals = [to_sympy(e.lhs) - to_sympy(e.rhs) for e in equations]

        if len(block) == 1:
            lines, method = self._emit_scalar_block(residuals[0], symbols[0],
                                                    unknowns[0], equations[0], position)
        else:
            lines, method = self._emit_loop_block(residuals, symbols, unknowns,
                                                  equations, position)

        title = (f"    # ---- block {position}: "
                 + (f"solve for {unknowns[0]}" if len(block) == 1
                    else f"algebraic loop of size {len(block)}: "
                         f"solve for {', '.join(unknowns)}")
                 + f"  [{method}]")
        body = [title]
        for equation in equations:
            body.append(f"    #        {equation.source or equation_to_string(equation)}")
        body += lines
        body.append("")

        self.blocks.append(BlockCode(index=position, equations=list(block),
                                     unknowns=unknowns, method=method, lines=lines))
        return body

    def _emit_scalar_block(self, residual, symbol, unknown, equation, position):
        """One equation, one unknown."""
        # Is the equation linear in this unknown?  If the derivative of the
        # residual does not itself contain the unknown, it is.
        try:
            coefficient = sympy.simplify(sympy.diff(residual, symbol))
        except Exception:                                   # pragma: no cover
            coefficient = None

        if coefficient is not None and symbol not in coefficient.free_symbols \
                and coefficient != 0:
            # residual = coefficient * unknown + rest  =>  unknown = -rest / coefficient
            rest = sympy.simplify(residual - coefficient * symbol)
            solution = sympy.simplify(-rest / coefficient)
            return [f"    {mangle(unknown)} = {to_python(solution)}"], "explicit"

        # Nonlinear: ask SymPy for a closed-form solution before giving up.
        try:
            solutions = sympy.solve(sympy.Eq(residual, 0), symbol, dict=False)
        except Exception:                                   # pragma: no cover
            solutions = []
        if len(solutions) == 1:
            return ([f"    {mangle(unknown)} = {to_python(sympy.simplify(solutions[0]))}"],
                    "symbolic")

        # Fall back to a numerical solution of the single equation.
        guess = self._start_value(unknown)
        return ([
            f"    def _residual{position}(_u):",
            f"        {mangle(unknown)}, = _u",
            f"        return [{to_python(residual)}]",
        ] + self._emit_root_find(position, [unknown], [guess]), "newton")

    def _emit_loop_block(self, residuals, symbols, unknowns, equations, position):
        """Several equations that must be solved together: an algebraic loop."""
        try:
            matrix, right_hand_side = sympy.linear_eq_to_matrix(residuals, symbols)
            is_linear = not any(s in entry.free_symbols
                                for entry in list(matrix) + list(right_hand_side)
                                for s in symbols)
        except Exception:
            is_linear = False

        if is_linear:
            rows = ", ".join(
                "[" + ", ".join(to_python(matrix[r, c]) for c in range(len(symbols))) + "]"
                for r in range(len(symbols)))
            vector = ", ".join(to_python(right_hand_side[r]) for r in range(len(symbols)))
            names = ", ".join(mangle(u) for u in unknowns)
            return ([
                f"    _A{position} = np.array([{rows}])",
                f"    _b{position} = np.array([{vector}])",
                f"    {names} = np.linalg.solve(_A{position}, _b{position})",
            ], "linear system")

        names = ", ".join(mangle(u) for u in unknowns)
        return ([
            f"    def _residual{position}(_u):",
            f"        {names} = _u",
            f"        return [" + ", ".join(to_python(r) for r in residuals) + "]",
        ] + self._emit_root_find(position, unknowns,
                                 [self._start_value(u) for u in unknowns]),
                "newton")

    def _emit_root_find(self, position: int, unknowns: List[str],
                        guesses: List[str]) -> List[str]:
        """
        Call the root finder, and *check that it worked*.

        A silent non-convergence is the worst possible outcome: the simulation
        would carry on with a meaningless number.  The generated code therefore
        always inspects the solver's flag and stops with a message naming the
        block and the model variables involved.
        """
        names = ", ".join(mangle(u) for u in unknowns)
        comma = "," if len(unknowns) == 1 else ""
        return [
            f"    _guess{position} = guess.get({position!r}, [{', '.join(guesses)}])",
            f"    {names}{comma} = _solve_block(_residual{position}, _guess{position},",
            f"                             {position}, {', '.join(unknowns)!r}, t)",
            f"    guess[{position!r}] = [{names}]",
        ]

    def _start_value(self, unknown: str) -> str:
        """A starting guess for an iterative solve, taken from `start`."""
        from .evaluator import EvaluationError, evaluate
        name = unknown[4:-1] if unknown.startswith("der(") else unknown
        variable = self.model.variables.get(name)
        if variable is not None and variable.start is not None:
            try:
                return repr(evaluate(variable.start, self.model.parameter_values))
            except EvaluationError:                          # pragma: no cover
                pass
        return "0.0"        # the same default a Modelica tool would use

    # -- results -------------------------------------------------------------

    def _output_names(self) -> List[str]:
        names = list(self.model.continuous_variables())
        names += self.model.discrete_variables()
        names += [n for n in self.eliminated]
        names += [der_name(s) for s in self.analysis.states]
        return names

    def _event_expressions(self):
        """
        Each `when` condition as a *margin*: positive when the condition holds.

        `h < 0` becomes `0 - h`, so the integrator can look for the instant the
        margin crosses zero upwards.  That is how state events are located.
        """
        expressions = []
        for when_equation in self.model.when_equations:
            condition = when_equation.condition
            if not (isinstance(condition, BinOp)
                    and condition.op in ("<", "<=", ">", ">=")):
                raise ModelError(
                    f"line {when_equation.line}: a 'when' condition must be a "
                    f"simple comparison such as 'h < 0' or 'time > 2'")
            if condition.op in ("<", "<="):
                margin = BinOp("-", condition.right, condition.left)
            else:
                margin = BinOp("-", condition.left, condition.right)
            from .ast_nodes import to_string
            expressions.append((to_string(condition), to_sympy(margin)))
        return expressions

    def _emit_results(self, solving_for_states: bool) -> List[str]:
        lines = []
        if self.eliminated:
            lines.append("    # ---- variables removed by alias elimination, "
                         "recovered for output ----")
            for name, entry in self.eliminated.items():
                if entry[0] == "alias":
                    _, representative, sign = entry
                    value = ("" if sign > 0 else "-") + mangle(representative)
                else:
                    from .alias import substitute
                    value = to_python(to_sympy(entry[1]))
                lines.append(f"    {mangle(name)} = {value}{self._comment(name)}")
            lines.append("")

        states = self.analysis.states
        lines.append("    # ---- results ----")
        derivative_list = ", ".join(mangle(der_name(s)) for s in states)
        lines.append(f"    der = np.array([{derivative_list}])")

        lines.append("    variables = {")
        for name in self._output_names():
            lines.append(f"        {name!r}: {mangle(name)},")
        lines.append("    }")

        events = self._event_expressions()
        if events:
            lines.append("    # ---- event margins: positive while the condition holds ----")
            for condition, _ in events:
                lines.append(f"    #        when {condition}")
            margins = ", ".join(to_python(margin) for _, margin in events)
            lines.append(f"    events = [{margins}]")
        else:
            lines.append("    events = []")

        if solving_for_states:
            state_list = ", ".join(mangle(s) for s in states)
            lines.append(f"    x = np.array([{state_list}])")
            lines.append("    return {'x': x, 'der': der, 'variables': variables, "
                         "'events': events}")
        else:
            lines.append("    return {'der': der, 'variables': variables, "
                         "'events': events}")
        return lines


def generate_code(model: FlatModel, analysis: StructuralAnalysis,
                  eliminated=None, function_name: str = "evaluate") -> GeneratedCode:
    """Generate and compile simulation code for an analysed model."""
    return CodeGenerator(model, analysis, eliminated, function_name).generate()
