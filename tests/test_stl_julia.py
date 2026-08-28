"""
Cross-checking the monitor against SignalTemporalLogic.jl.

TinySim's own monitor is written out longhand so that it can be read. These
tests are the evidence that it is also *right*: the same clauses are handed to
an independent implementation -- SignalTemporalLogic.jl from the Stanford
Intelligent Systems Laboratory -- and the two must agree.

Everything here is skipped when Julia or the package is not installed, so the
suite still passes on a machine that has neither.
"""

import pytest

import tinysim
from tinysim import stl_julia

julia_available = pytest.mark.skipif(
    not stl_julia.available(),
    reason="Julia with SignalTemporalLogic.jl is not installed "
           "(run tinysim.stl_julia.install() once)")

EXAMPLES_WITH_CONTRACTS = [
    ("electrical.tiny", dict(stop=1.0, points=201)),
    ("thermostat.tiny", dict(stop=200.0, points=1001)),
    ("tank.tiny", dict(stop=20.0, points=201)),
    ("dcmotor.tiny", dict(stop=3.0, points=601)),
    ("bouncing_ball.tiny", dict(stop=3.0, points=601)),
]


# ---------------------------------------------------------------------------
# The translation, which can be checked without running Julia
# ---------------------------------------------------------------------------

def test_the_generated_julia_says_what_the_contract_says(examples):
    model = tinysim.load(examples / "electrical.tiny", "RCCircuit")
    result = tinysim.simulate(model, stop=1.0, points=101)
    program, trace, clauses = stl_julia.build_script(model, result)

    assert "using SignalTemporalLogic" in program
    assert "@formula" in program
    # `eventually within [0, 0.5]` becomes the sample indices inside 0.5 s.
    assert "◊(1:51," in program
    # A predicate becomes `(lhs - rhs) op 0`, the form the library takes.
    import re
    assert re.search(r"xt -> \(xt\[\d+\] - xt\[\d+\]\) < 0", program)
    assert len(trace.splitlines()) == len(result.time)
    assert all(clause.source for clause in clauses)


def test_a_trigger_is_reported_as_out_of_reach(examples):
    """The library has no rising edge, so `whenever` is not translated."""
    model = tinysim.load(examples / "thermostat.tiny", "Thermostat")
    result = tinysim.simulate(model, stop=100.0, points=1001)
    _, _, clauses = stl_julia.build_script(model, result)
    unsupported = [clause for clause in clauses if clause.unsupported]
    assert unsupported
    assert all("rising-edge" in clause.unsupported for clause in unsupported)


def test_the_trace_is_written_as_plain_numbers(examples):
    model = tinysim.load(examples / "tank.tiny", "Tank")
    result = tinysim.simulate(model, stop=5.0, points=11)
    _, trace, _ = stl_julia.build_script(model, result)
    for line in trace.splitlines():
        for field in line.split():
            float(field)                      # Julia has to be able to parse it


# ---------------------------------------------------------------------------
# The cross-check itself
# ---------------------------------------------------------------------------

@julia_available
@pytest.mark.parametrize("name, options", EXAMPLES_WITH_CONTRACTS)
def test_both_implementations_agree_on_every_example(name, options, examples):
    model = tinysim.load(examples / name)
    result = tinysim.simulate(model, **options)
    builtin, julia, differences = tinysim.cross_check_contracts(model, result)

    assert differences, f"{name}: nothing was cross-checked"
    worst = max(differences.values())
    assert worst < 1e-9, f"{name}: implementations differ by {worst}"
    assert builtin.summary() == julia.summary()


@julia_available
def test_the_julia_backend_finds_the_same_violation(examples):
    """The bouncing ball without event detection, checked by the other tool."""
    model = tinysim.load(examples / "bouncing_ball.tiny", "BouncingBall")
    result = tinysim.simulate(model, stop=3.0, method="rk4", step=1e-3,
                              events="off")
    report = tinysim.check_contracts(model, result, backend="julia")
    assert report.violated
    failing = report.results[0].failing
    assert failing.margin < -40
    assert failing.backend == "julia"


@julia_available
def test_clauses_the_library_cannot_express_fall_back_and_say_so(examples):
    model = tinysim.load(examples / "thermostat.tiny", "Thermostat")
    result = tinysim.simulate(model, stop=100.0, points=1001)
    report = tinysim.check_contracts(model, result, backend="julia")
    outcome = report.results[0]
    backends = {clause.backend
                for clause in outcome.assumptions + outcome.guarantees}
    assert backends == {"julia", "builtin"}
    assert any("cannot express it" in note for note in outcome.notes)


def test_an_unknown_backend_is_reported(examples):
    model = tinysim.load(examples / "tank.tiny", "Tank")
    result = tinysim.simulate(model, stop=1.0, points=11)
    with pytest.raises(ValueError, match="unknown contract backend"):
        tinysim.check_contracts(model, result, backend="matlab")
