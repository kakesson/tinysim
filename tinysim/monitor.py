"""
Checking contracts against a simulation run.

The verdict comes from the *robust* semantics of Signal Temporal Logic: every
formula is given a number, not just a truth value.

    rho(x > c)      = x - c                     in the units of x
    rho(not phi)    = -rho(phi)
    rho(a and b)    = min(rho(a), rho(b))
    rho(a or b)     = max(rho(a), rho(b))
    rho(G[a,b] phi) = the smallest rho(phi) in the window
    rho(F[a,b] phi) = the largest  rho(phi) in the window

Positive means satisfied, and says how much room there was; negative means
violated, and says by how much.  That number is what makes a contract useful to
someone who does not read temporal logic: "0.4 degrees to spare at t = 143 s"
needs no explanation.

The three verdicts follow the assume-guarantee reading of a contract, `A => G`:

    assumptions hold, guarantees hold      SATISFIED
    assumptions hold, a guarantee fails    VIOLATED    -- the component's fault
    an assumption fails                    NOT TESTED  -- the environment was
                                                          outside the contract,
                                                          so nothing was promised

A run is evidence, not proof.  Monitoring can *falsify* a contract; it cannot
verify one, and the reports say so.

Two honest limitations, both reported rather than hidden:

* The monitor sees the output points of the run.  A violation narrower than the
  output interval can slip between them -- the same lesson as event detection,
  one level up.
* Triggers are crisp.  `whenever c then r within [0, 2]` reports the margin of
  the *response*; asking how close `c` came to being true is a different
  question, and mixing the two would produce a number that means neither.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .ast_nodes import BinOp, Call, Expr, IfExpr, Num, Ref, UnOp, to_string
from .contracts import (
    Always, And, AtEnd, AtStart, Clause, Contract, Eventually, Implies, Not,
    Or, Predicate, Rise, Until, desugar,
)
from .evaluator import EvaluationError, evaluate

#: The robustness given to a trigger.  Triggers are crisp -- see the module
#: docstring -- and this stands in for "infinitely satisfied" without poisoning
#: the arithmetic the way a real infinity would.
CRISP = 1e12

#: What the numeric functions of the language mean on whole signals.
ARRAY_FUNCTIONS = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "asin": np.arcsin,
    "acos": np.arccos, "atan": np.arctan, "atan2": np.arctan2, "exp": np.exp,
    "log": np.log, "log10": np.log10, "sqrt": np.sqrt, "abs": np.abs,
    "sign": np.sign, "tanh": np.tanh, "min": np.minimum, "max": np.maximum,
}


class ContractError(Exception):
    """Raised when a contract cannot be checked at all."""


# =============================================================================
# The trace, and expressions over it
# =============================================================================

class Trace:
    """A simulation result, seen as one array per name."""

    def __init__(self, result, parameters: Dict[str, float]):
        self.time = np.asarray(result.time, dtype=float)
        self.result = result
        self.parameters = parameters

    def signal(self, name: str) -> np.ndarray:
        if name == "time":
            return self.time
        if name in self.result.values:
            return np.asarray(self.result.values[name], dtype=float)
        if name in self.parameters:
            return np.full_like(self.time, float(self.parameters[name]))
        raise ContractError(
            f"{name!r} is not a variable of this model, so the contract cannot "
            f"be checked against the run")

    def constant(self, expr: Expr) -> float:
        """A time bound or a parameter expression, as one number."""
        try:
            return float(evaluate(expr, self.parameters))
        except EvaluationError as error:
            raise ContractError(
                f"the bound {to_string(expr)!r} must be a number or an "
                f"expression of parameters ({error})")


def evaluate_signal(expr: Expr, trace: Trace) -> np.ndarray:
    """Evaluate a model expression at every output point at once."""
    if isinstance(expr, Num):
        return np.full_like(trace.time, expr.value)
    if isinstance(expr, Ref):
        return trace.signal(expr.name)
    if isinstance(expr, UnOp):
        operand = evaluate_signal(expr.operand, trace)
        return -operand if expr.op == "-" else np.where(operand != 0, 0.0, 1.0)
    if isinstance(expr, BinOp):
        left = evaluate_signal(expr.left, trace)
        right = evaluate_signal(expr.right, trace)
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
        comparisons = {"<": np.less, "<=": np.less_equal, ">": np.greater,
                       ">=": np.greater_equal, "==": np.equal, "<>": np.not_equal}
        if expr.op in comparisons:
            return comparisons[expr.op](left, right).astype(float)
        if expr.op == "and":
            return np.logical_and(left != 0, right != 0).astype(float)
        if expr.op == "or":
            return np.logical_or(left != 0, right != 0).astype(float)
    if isinstance(expr, IfExpr):
        return np.where(evaluate_signal(expr.cond, trace) != 0,
                        evaluate_signal(expr.then_expr, trace),
                        evaluate_signal(expr.else_expr, trace))
    if isinstance(expr, Call):
        if expr.func in ("der", "pre"):
            name = (f"der({expr.args[0].name})" if expr.func == "der"
                    else expr.args[0].name)
            return trace.signal(name)
        arguments = [evaluate_signal(argument, trace) for argument in expr.args]
        return ARRAY_FUNCTIONS[expr.func](*arguments)
    raise ContractError(f"cannot evaluate {to_string(expr)} over the run")


# =============================================================================
# Robustness signals
# =============================================================================

@dataclass
class Signal:
    """
    A robustness value at every output point, and where each one came from.

    Carrying the witness time is what lets a report say *when* the closest call
    happened, which is usually the first thing anyone asks.
    """
    values: np.ndarray
    witness: np.ndarray

    @classmethod
    def of(cls, values: np.ndarray, trace: Trace) -> "Signal":
        return cls(np.asarray(values, dtype=float), trace.time.copy())


@dataclass
class Evaluation:
    """Bookkeeping gathered while a formula is evaluated."""
    triggers: int = 0
    window_past_end: bool = False


def robustness(formula, trace: Trace, notes: Evaluation) -> Signal:
    """The robustness of a *core* formula at every output point."""
    if isinstance(formula, Predicate):
        left = evaluate_signal(formula.left, trace)
        right = evaluate_signal(formula.right, trace)
        if formula.op in (">", ">="):
            values = left - right
        elif formula.op in ("<", "<="):
            values = right - left
        elif formula.op == "==":
            values = -np.abs(left - right)
        elif formula.op == "<>":
            values = np.abs(left - right)
        else:                                                # pragma: no cover
            raise ContractError(f"unknown comparison {formula.op!r}")
        return Signal.of(values, trace)

    if isinstance(formula, Not):
        inner = robustness(formula.formula, trace, notes)
        return Signal(-inner.values, inner.witness)

    if isinstance(formula, And):
        return _combine([robustness(part, trace, notes) for part in formula.parts],
                        smallest=True)
    if isinstance(formula, Or):
        return _combine([robustness(part, trace, notes) for part in formula.parts],
                        smallest=False)
    if isinstance(formula, Implies):
        left = robustness(formula.left, trace, notes)
        right = robustness(formula.right, trace, notes)
        return _combine([Signal(-left.values, left.witness), right], smallest=False)

    if isinstance(formula, Always):
        return _over_window(formula, trace, notes, smallest=True)
    if isinstance(formula, Eventually):
        return _over_window(formula, trace, notes, smallest=False)
    if isinstance(formula, Until):
        return _until(formula, trace, notes)

    if isinstance(formula, Rise):
        inner = robustness(formula.formula, trace, notes)
        holds = inner.values > 0
        rising = np.zeros_like(inner.values, dtype=bool)
        rising[1:] = holds[1:] & ~holds[:-1]
        notes.triggers += int(rising.sum())
        return Signal(np.where(rising, CRISP, -CRISP), trace.time.copy())

    if isinstance(formula, AtStart):
        inner = robustness(formula.formula, trace, notes)
        return Signal(np.full_like(inner.values, inner.values[0]),
                      np.full_like(inner.witness, trace.time[0]))
    if isinstance(formula, AtEnd):
        inner = robustness(formula.formula, trace, notes)
        return Signal(np.full_like(inner.values, inner.values[-1]),
                      np.full_like(inner.witness, trace.time[-1]))

    raise ContractError(f"cannot check {formula!r}")


def _combine(signals: List[Signal], smallest: bool) -> Signal:
    """Elementwise min or max, keeping the witness time of the winner."""
    values = np.stack([signal.values for signal in signals])
    witnesses = np.stack([signal.witness for signal in signals])
    chosen = np.argmin(values, axis=0) if smallest else np.argmax(values, axis=0)
    columns = np.arange(values.shape[1])
    return Signal(values[chosen, columns], witnesses[chosen, columns])


def _window_bounds(formula, trace: Trace, notes: Evaluation):
    low, high = formula.window
    lower = 0.0 if low is None else trace.constant(low)
    upper = math.inf if high is None else trace.constant(high)
    if math.isfinite(upper) and trace.time[0] + upper > trace.time[-1] + 1e-12:
        notes.window_past_end = True
    return lower, upper


def _over_window(formula, trace: Trace, notes: Evaluation, smallest: bool) -> Signal:
    """
    `always` and `eventually`, the direct way: for every point, look at every
    point in its window.

    This is O(n^2) in the worst case.  Real monitors use a sliding-window
    minimum (Lemire's algorithm) to make it linear; the plain version is left
    here because it is the definition, written out.
    """
    inner = robustness(formula.formula, trace, notes)
    lower, upper = _window_bounds(formula, trace, notes)
    time = trace.time

    values = np.empty_like(inner.values)
    witness = np.empty_like(inner.witness)
    for index, moment in enumerate(time):
        window = (time >= moment + lower - 1e-12) & (time <= moment + upper + 1e-12)
        if not window.any():
            # The run ended before this window opened: nothing to check, so
            # `always` is vacuously true and `eventually` never happened.
            values[index] = math.inf if smallest else -math.inf
            witness[index] = time[-1]
            continue
        candidates = inner.values[window]
        position = np.argmin(candidates) if smallest else np.argmax(candidates)
        values[index] = candidates[position]
        witness[index] = inner.witness[window][position]
    return Signal(values, witness)


def _until(formula, trace: Trace, notes: Evaluation) -> Signal:
    """
    `phi until[a,b] psi`: psi has to happen inside the window, and phi has to
    hold every moment until it does.
    """
    left = robustness(formula.left, trace, notes)
    right = robustness(formula.right, trace, notes)
    lower, upper = _window_bounds(formula, trace, notes)
    time = trace.time

    values = np.full_like(left.values, -math.inf)
    witness = time.copy()
    for index, moment in enumerate(time):
        best, best_time = -math.inf, time[index]
        running = math.inf                       # inf over phi from index to here
        for later in range(index, len(time)):
            running = min(running, left.values[later])
            if not (moment + lower - 1e-12 <= time[later] <= moment + upper + 1e-12):
                if time[later] > moment + upper:
                    break
                continue
            candidate = min(right.values[later], running)
            if candidate > best:
                best, best_time = candidate, right.witness[later]
        values[index] = best
        witness[index] = best_time
    return Signal(values, witness)


# =============================================================================
# Verdicts
# =============================================================================

SATISFIED = "satisfied"
VIOLATED = "violated"
NOT_TESTED = "not tested"


@dataclass
class ClauseResult:
    """What one `assume` or `guarantee` line came to on this run."""
    kind: str                      # 'assume' | 'guarantee'
    clause: Clause
    margin: float
    at_time: float
    triggers: int = 0
    vacuous: bool = False
    window_past_end: bool = False
    #: Which implementation produced the margin: TinySim's own monitor, or
    #: SignalTemporalLogic.jl.  See `stl_julia.py`.
    backend: str = "builtin"

    @property
    def satisfied(self) -> bool:
        return self.margin >= 0

    @property
    def margin_text(self) -> str:
        if self.margin >= CRISP / 2:
            return "not triggered"
        # `on == 0` is satisfied exactly, with no room; -0.0 would read as a
        # violation to anyone skimming.
        value = 0.0 if self.margin == 0 else self.margin
        return f"{value:+.4g}"


@dataclass
class ContractResult:
    """One contract, checked against one instance of its model."""
    contract: Contract
    instance: str                  # '' for the simulated model itself
    verdict: str
    assumptions: List[ClauseResult] = field(default_factory=list)
    guarantees: List[ClauseResult] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        where = f"{self.instance} : " if self.instance else ""
        return f"{where}{self.contract.name}"

    @property
    def failing(self) -> Optional[ClauseResult]:
        """The clause that decided a violation, or the closest call."""
        results = self.guarantees or self.assumptions
        return min(results, key=lambda result: result.margin) if results else None


@dataclass
class ContractReport:
    """Every contract that applied to a run."""
    results: List[ContractResult] = field(default_factory=list)
    output_interval: float = 0.0

    @property
    def violated(self) -> List[ContractResult]:
        return [result for result in self.results if result.verdict == VIOLATED]

    @property
    def not_tested(self) -> List[ContractResult]:
        return [result for result in self.results if result.verdict == NOT_TESTED]

    @property
    def all_satisfied(self) -> bool:
        return not self.violated

    def summary(self) -> str:
        satisfied = sum(1 for r in self.results if r.verdict == SATISFIED)
        return (f"{satisfied} satisfied, {len(self.violated)} violated, "
                f"{len(self.not_tested)} not tested")


def check_clause(kind: str, clause: Clause, trace: Trace) -> ClauseResult:
    """Evaluate one clause at the start of the run."""
    notes = Evaluation()
    signal = robustness(clause.core, trace, notes)
    margin = float(signal.values[0])
    at_time = float(signal.witness[0])
    vacuous = notes.triggers == 0 and _has_trigger(clause.core)
    return ClauseResult(kind=kind, clause=clause, margin=margin, at_time=at_time,
                        triggers=notes.triggers, vacuous=vacuous,
                        window_past_end=notes.window_past_end)


def _has_trigger(formula) -> bool:
    if isinstance(formula, Rise):
        return True
    for attribute in ("formula", "left", "right"):
        child = getattr(formula, attribute, None)
        if child is not None and not isinstance(child, Expr) and _has_trigger(child):
            return True
    return any(_has_trigger(part) for part in getattr(formula, "parts", []))


def check_contract(contract: Contract, instance: str, trace: Trace) -> ContractResult:
    """Check one contract instance: assumptions first, then the guarantees."""
    assumptions = [check_clause("assume", clause, trace)
                   for clause in contract.assumptions]
    guarantees = [check_clause("guarantee", clause, trace)
                  for clause in contract.guarantees]

    broken = [result for result in assumptions if not result.satisfied]
    if broken:
        verdict = NOT_TESTED
        notes = [f"the assumption '{result.clause.written}' failed at "
                 f"t = {result.at_time:.6g}, so nothing was promised on this run"
                 for result in broken]
    else:
        failed = [result for result in guarantees if not result.satisfied]
        verdict = VIOLATED if failed else SATISFIED
        notes = [f"'{result.clause.written}' fails by {abs(result.margin):.4g} "
                 f"at t = {result.at_time:.6g}" for result in failed]

    for result in assumptions + guarantees:
        if result.vacuous:
            notes.append(f"'{result.clause.written}' was never triggered on this "
                         f"run, so it proves nothing")
        if result.window_past_end:
            notes.append(f"the run ends before the window of "
                         f"'{result.clause.written}' closes")
    return ContractResult(contract=contract, instance=instance, verdict=verdict,
                          assumptions=assumptions, guarantees=guarantees,
                          notes=notes)


def check_contracts(compiled, result) -> ContractReport:
    """
    Check every contract that applies to a simulated model.

    That means the model's own contract, and the contract of every component
    instance inside it -- each under the environment the system actually gave
    it, which is what makes the check compositional rather than decorative.
    """
    trace = Trace(result, compiled.model.parameter_values)
    report = ContractReport(
        output_interval=float(np.median(np.diff(trace.time)))
        if len(trace.time) > 1 else 0.0)
    for contract, instance in compiled.contract_instances:
        report.results.append(check_contract(contract, instance, trace))
    return report
