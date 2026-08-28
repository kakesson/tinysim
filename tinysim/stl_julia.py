"""
Checking contracts with SignalTemporalLogic.jl.

TinySim's own monitor (`monitor.py`) is written out longhand so that students
can read the robust semantics.  Code you can read is not the same as code you
should trust, so contracts can also be evaluated by an established, independent
implementation: **SignalTemporalLogic.jl** from the Stanford Intelligent
Systems Laboratory (https://github.com/sisl/SignalTemporalLogic.jl).

Nothing here is required to use TinySim.  There is no Python dependency: the
translation is written to a Julia script and run with the `julia` binary, and
the script is printed on request -- generated Julia next to the generated
Python, for the same reason.

**How the translation works.**  That library evaluates a formula once over a
whole trace, and its time windows are *sample index ranges*, not times.  Both
match what a contract clause means here -- it is evaluated at the start of the
run -- so a window `[a, b]` in seconds becomes the range of sample indices
whose times fall in it.  No resampling is involved, and the two implementations
should agree exactly.

A predicate `lhs op rhs` becomes `xt -> (lhs - rhs) op 0`, because that
library's predicates compare a function of the sample against a constant, and
its robustness for `mu(xt) > c` is `mu(xt) - c`.  With `c = 0` that is
`lhs - rhs`, which is the definition `monitor.py` uses.

**What it cannot do.**  The library has no notion of a rising edge, and a
temporal operator there applies to a *sample*, not to a sub-signal, so a
temporal operator nested inside another one cannot be expressed.  Both together
mean `whenever ... then ... within ...` is out of its reach.  Those clauses are
reported as not covered rather than quietly evaluated by something else.
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .ast_nodes import BinOp, Call, Expr, IfExpr, Num, Ref, UnOp, to_string
from .contracts import (
    Always, And, AtEnd, AtStart, Eventually, Implies, Not, Or, Predicate, Rise,
    Until,
)

#: The Julia environment the package is installed into.  Kept out of the
#: user's default environment so that trying this out cannot disturb anything.
ENVIRONMENT = pathlib.Path.home() / ".julia" / "environments" / "tinysim-stl"

#: Where to look for julia when it is not on PATH (Homebrew's location).
EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin"]


class Unsupported(Exception):
    """Raised for a clause SignalTemporalLogic.jl cannot express."""


def julia_executable() -> Optional[str]:
    """The `julia` binary, or None if this machine has no Julia."""
    found = shutil.which("julia")
    if found:
        return found
    for directory in EXTRA_PATHS:
        candidate = pathlib.Path(directory) / "julia"
        if candidate.exists():
            return str(candidate)
    return None


def available() -> bool:
    """True when contracts can actually be checked with the Julia library."""
    if julia_executable() is None:
        return False
    return (ENVIRONMENT / "Manifest.toml").exists()


def install(quiet: bool = True) -> bool:
    """
    Install SignalTemporalLogic.jl into TinySim's own Julia environment.

    Done once, and only when asked: this is an optional cross-check, not a
    dependency.
    """
    julia = julia_executable()
    if julia is None:
        return False
    ENVIRONMENT.mkdir(parents=True, exist_ok=True)
    script = (f'using Pkg; Pkg.activate("{ENVIRONMENT}"; io=devnull); '
              f'Pkg.add("SignalTemporalLogic"; io=devnull)')
    finished = subprocess.run([julia, "-e", script], capture_output=True,
                              text=True, timeout=900)
    if finished.returncode != 0 and not quiet:            # pragma: no cover
        print(finished.stderr[-2000:])
    return finished.returncode == 0


# =============================================================================
# Translating one formula
# =============================================================================

class Translator:
    """Turns one core TinySim formula into SignalTemporalLogic.jl source."""

    def __init__(self, times: np.ndarray, columns: Dict[str, int], constant):
        self.times = times
        self.columns = columns            # model name -> 1-based column in xt
        self.constant = constant          # a window bound as one number

    # -- expressions ---------------------------------------------------------

    def expression(self, expr: Expr) -> str:
        """A model expression as Julia, in terms of one sample `xt`."""
        if isinstance(expr, Num):
            return repr(float(expr.value))
        if isinstance(expr, Ref):
            return f"xt[{self.columns[expr.name]}]"
        if isinstance(expr, UnOp):
            if expr.op == "-":
                return f"(-{self.expression(expr.operand)})"
            raise Unsupported("'not' inside an expression")
        if isinstance(expr, BinOp):
            left, right = self.expression(expr.left), self.expression(expr.right)
            if expr.op in ("+", "-", "*", "/"):
                return f"({left} {expr.op} {right})"
            if expr.op == "^":
                return f"({left} ^ {right})"
            raise Unsupported(f"the operator {expr.op!r} inside an expression")
        if isinstance(expr, Call):
            if expr.func in ("der", "pre"):
                name = (f"der({expr.args[0].name})" if expr.func == "der"
                        else expr.args[0].name)
                return f"xt[{self.columns[name]}]"
            functions = {"abs": "abs", "sqrt": "sqrt", "exp": "exp", "log": "log",
                         "log10": "log10", "sin": "sin", "cos": "cos", "tan": "tan",
                         "asin": "asin", "acos": "acos", "atan": "atan",
                         "atan2": "atan", "tanh": "tanh", "sign": "sign",
                         "min": "min", "max": "max"}
            if expr.func not in functions:                # pragma: no cover
                raise Unsupported(f"the function {expr.func}()")
            arguments = ", ".join(self.expression(a) for a in expr.args)
            return f"{functions[expr.func]}({arguments})"
        if isinstance(expr, IfExpr):
            raise Unsupported("an if-expression")
        raise Unsupported(f"{to_string(expr)}")

    # -- windows -------------------------------------------------------------

    def interval(self, window) -> str:
        """
        A time window in seconds as the range of sample indices inside it.

        SignalTemporalLogic.jl indexes samples, and a clause is evaluated at
        the start of the run, so this conversion is exact -- no resampling.
        """
        low, high = window
        if low is None and high is None:
            return ""
        start = self.times[0]
        lower = start if low is None else start + self.constant(low)
        upper = self.times[-1] if high is None else start + self.constant(high)
        inside = np.nonzero((self.times >= lower - 1e-12)
                            & (self.times <= upper + 1e-12))[0]
        if inside.size == 0:
            raise Unsupported("a time window with no sample in it")
        return f"{inside[0] + 1}:{inside[-1] + 1}, "

    # -- formulas ------------------------------------------------------------

    def formula(self, node, inside_temporal: bool = False) -> str:
        if isinstance(node, Predicate):
            # `lhs op rhs` becomes `(lhs - rhs) op 0`, which is the form that
            # library's predicates take, with the robustness TinySim uses.
            difference = (f"({self.expression(node.left)} - "
                          f"{self.expression(node.right)})")
            operator = {">": ">", ">=": ">", "<": "<", "<=": "<",
                        "==": "==", "<>": "!="}[node.op]
            return f"xt -> {difference} {operator} 0"
        if isinstance(node, Not):
            return f"¬({self.formula(node.formula, inside_temporal)})"
        if isinstance(node, And):
            return " ∧ ".join(f"({self.formula(part, inside_temporal)})"
                              for part in node.parts)
        if isinstance(node, Or):
            return " ∨ ".join(f"({self.formula(part, inside_temporal)})"
                              for part in node.parts)
        if isinstance(node, Implies):
            return (f"({self.formula(node.left, inside_temporal)}) ⟹ "
                    f"({self.formula(node.right, inside_temporal)})")
        if isinstance(node, (Always, Eventually, Until)):
            if inside_temporal:
                raise Unsupported("a temporal operator inside another one")
            symbol = "□" if isinstance(node, Always) else "◊"
            if isinstance(node, Until):
                return (f"𝒰({self.interval(node.window)}"
                        f"{self.formula(node.left, True)}, "
                        f"{self.formula(node.right, True)})")
            return (f"{symbol}({self.interval(node.window)}"
                    f"{self.formula(node.formula, True)})")
        if isinstance(node, AtStart):
            return f"□(1:1, {self.formula(node.formula, True)})"
        if isinstance(node, AtEnd):
            last = len(self.times)
            return f"□({last}:{last}, {self.formula(node.formula, True)})"
        if isinstance(node, Rise):
            raise Unsupported("a rising-edge trigger (`whenever`)")
        raise Unsupported(f"{type(node).__name__}")       # pragma: no cover

    def top_level(self, node) -> str:
        """
        A whole clause.

        A clause with no temporal operator at all -- `P > 0`, `at start x > 0`
        -- has to be pinned to the first sample, because that is where a
        contract clause is evaluated.
        """
        text = self.formula(node)
        if isinstance(node, (Always, Eventually, Until, AtStart, AtEnd)):
            return text
        return f"□(1:1, {text})"


# =============================================================================
# Running the check
# =============================================================================

@dataclass
class JuliaClause:
    """One clause, ready to hand to Julia -- or the reason it cannot be."""
    label: str
    source: Optional[str] = None
    unsupported: Optional[str] = None


def _needed_names(contract_instances) -> List[str]:
    from .contracts import references
    names = set()
    for contract, _ in contract_instances:
        for _, clause in contract.clauses():
            names |= references(clause.formula)
    return sorted(names)


def build_script(compiled, result) -> Tuple[str, str, List[JuliaClause]]:
    """
    Generate the Julia program that checks every clause.

    Returns the program, the trace as text, and one entry per clause -- so the
    caller can print exactly what was run, the way the generated Python is
    printed.
    """
    from .monitor import Trace

    trace = Trace(result, compiled.model.parameter_values)
    names = [name for name in _needed_names(compiled.contract_instances)
             if name != "time"]
    columns = {name: position + 1 for position, name in enumerate(names)}
    columns["time"] = len(names) + 1
    signals = [trace.signal(name) for name in names] + [trace.time]

    translator = Translator(trace.time, columns, trace.constant)
    clauses: List[JuliaClause] = []
    for contract, instance in compiled.contract_instances:
        for kind, clause in contract.clauses():
            label = f"{instance}|{contract.name}|{kind}|{clause.line}"
            try:
                clauses.append(JuliaClause(label, translator.top_level(clause.core)))
            except Unsupported as reason:
                clauses.append(JuliaClause(label, unsupported=str(reason)))

    header = ", ".join(f"{name} = column {position}"
                       for name, position in columns.items())
    formulas = "\n".join(
        f'    ("{clause.label}", @formula {clause.source}),'
        for clause in clauses if clause.source is not None)

    program = f'''# ---------------------------------------------------------------------------
# Generated by TinySim: contract clauses of model {compiled.name!r} as
# SignalTemporalLogic.jl formulas, checked against one simulation run.
#
# Each sample of the trace is a vector: {header}
# Time windows are sample index ranges, converted from the seconds written in
# the contract using the actual output times of the run.
# ---------------------------------------------------------------------------

using Pkg
Pkg.activate("{ENVIRONMENT}"; io=devnull)
using SignalTemporalLogic

# The trace: one line per output point, one column per signal.
x = [parse.(Float64, split(line)) for line in eachline(ARGS[1])]

formulas = [
{formulas}
]

for (label, phi) in formulas
    println(label, "\\t", ρ(x, phi))
end
'''
    # 17 significant digits round-trips a double exactly; `repr` on a NumPy
    # scalar would write `np.float64(...)`, which Julia cannot parse.
    trace_text = "\n".join(" ".join(f"{float(value):.17g}" for value in row)
                           for row in np.column_stack(signals))
    return program, trace_text, clauses


def robustness_from_julia(compiled, result, timeout: float = 300.0) -> Dict[str, float]:
    """Run the generated program and read back one number per clause."""
    julia = julia_executable()
    if julia is None:
        raise RuntimeError(
            "no julia executable found; install Julia to use this backend")
    if not available():
        raise RuntimeError(
            "SignalTemporalLogic.jl is not installed for TinySim; run "
            "`python -c \"import tinysim.stl_julia as j; j.install()\"` once")

    program, trace_text, _ = build_script(compiled, result)
    with tempfile.TemporaryDirectory() as directory:
        script = pathlib.Path(directory) / "check.jl"
        data = pathlib.Path(directory) / "trace.txt"
        script.write_text(program)
        data.write_text(trace_text)
        finished = subprocess.run([julia, str(script), str(data)],
                                  capture_output=True, text=True, timeout=timeout)
    if finished.returncode != 0:
        raise RuntimeError(f"SignalTemporalLogic.jl failed:\n{finished.stderr[-2000:]}")

    margins = {}
    for line in finished.stdout.splitlines():
        if "\t" not in line:
            continue
        label, value = line.split("\t")
        margins[label] = float(value)
    return margins


def check_contracts(compiled, result, timeout: float = 300.0):
    """
    Check the contracts with SignalTemporalLogic.jl instead of with `monitor.py`.

    Clauses the library cannot express fall back to TinySim's own monitor and
    are marked, so a report never mixes the two silently.
    """
    from .monitor import ContractReport, check_clause, check_contract
    from .monitor import Trace

    margins = robustness_from_julia(compiled, result, timeout=timeout)
    trace = Trace(result, compiled.model.parameter_values)
    report = ContractReport(
        output_interval=float(np.median(np.diff(trace.time)))
        if len(trace.time) > 1 else 0.0)

    for contract, instance in compiled.contract_instances:
        outcome = check_contract(contract, instance, trace)   # times and fallbacks
        for kind, clause in contract.clauses():
            label = f"{instance}|{contract.name}|{kind}|{clause.line}"
            results = (outcome.assumptions if kind == "assume"
                       else outcome.guarantees)
            for item in results:
                if item.clause is clause and label in margins:
                    item.margin = margins[label]
                    item.backend = "julia"
        report.results.append(_revised(outcome))
    return report


def _revised(outcome):
    """Recompute the verdict after the margins were replaced."""
    from .monitor import NOT_TESTED, SATISFIED, VIOLATED

    broken = [item for item in outcome.assumptions if not item.satisfied]
    failed = [item for item in outcome.guarantees if not item.satisfied]
    outcome.verdict = (NOT_TESTED if broken else
                       VIOLATED if failed else SATISFIED)
    fallbacks = [item for item in outcome.assumptions + outcome.guarantees
                 if item.backend != "julia"]
    for item in fallbacks:
        outcome.notes.append(
            f"'{item.clause.written}' was checked by TinySim's own monitor: "
            f"SignalTemporalLogic.jl cannot express it")
    return outcome
