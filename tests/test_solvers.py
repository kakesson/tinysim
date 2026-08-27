"""
Step control and event handling: the choices `simulate()` offers.

These tests are also the evidence for the claims made in
`experiments/07_solvers.py` and in the module docstring of `simulator.py`.
"""

import math

import numpy as np
import pytest

import tinysim

from conftest import RC_CIRCUIT

TIME_CONSTANT = 100 * 1e-3          # the RC circuit of conftest.py
FIRST_BOUNCE = math.sqrt(2 * 1.0 / 9.81)


def rc_error(method, step):
    """How far a fixed-step run is from the analytic solution at t = 0.5 s."""
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    result = tinysim.simulate(model, stop=0.5, method=method, step=step)
    exact = 10 * (1 - math.exp(-0.5 / TIME_CONSTANT))
    return abs(result["c.v"][-1] - exact)


@pytest.mark.parametrize("method, order", [("euler", 1), ("heun", 2), ("rk4", 4)])
def test_fixed_step_methods_converge_at_their_stated_order(method, order):
    """Halving the step divides the error by about 2**order."""
    coarse = rc_error(method, 0.01)
    fine = rc_error(method, 0.005)
    assert fine < coarse
    assert coarse / fine == pytest.approx(2 ** order, rel=0.25)


def test_a_fixed_step_run_outputs_exactly_the_steps_it_took():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    result = tinysim.simulate(model, stop=1.0, method="rk4", step=0.01)
    assert len(result.time) == 101                  # 100 steps, plus the start
    assert np.allclose(np.diff(result.time), 0.01)
    # `points` has nothing to say here: the step size decides the output.
    same = tinysim.simulate(model, stop=1.0, method="rk4", step=0.01, points=7)
    assert len(same.time) == len(result.time)


def test_fixed_and_variable_step_agree_on_a_smooth_model():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    variable = tinysim.simulate(model, stop=0.5, points=51, rtol=1e-10, atol=1e-12)
    fixed = tinysim.simulate(model, stop=0.5, method="rk4", step=0.001)
    assert fixed["c.v"][-1] == pytest.approx(variable["c.v"][-1], rel=1e-8)


def test_the_result_says_how_it_was_produced():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    fixed = tinysim.simulate(model, stop=0.1, method="euler", step=0.001)
    assert fixed.solver.fixed_step
    assert "fixed step 0.001" in str(fixed.solver)
    assert "events located exactly" in str(fixed.solver)

    variable = tinysim.simulate(model, stop=0.1, events="off")
    assert not variable.solver.fixed_step
    assert "variable step" in str(variable.solver)
    assert "events ignored" in str(variable.solver)


# ---------------------------------------------------------------------------
# What the three event policies cost, measured on the bouncing ball
# ---------------------------------------------------------------------------

def bounce(examples, **options):
    model = tinysim.load(examples / "bouncing_ball.tiny", "BouncingBall")
    return tinysim.simulate(model, stop=2.0, points=2001, **options)


def test_locating_events_finds_the_analytic_bounce_time(examples):
    for options in [dict(method="Radau", events="locate"),
                    dict(method="rk4", step=1e-3, events="locate")]:
        result = bounce(examples, **options)
        assert len(result.events) == 3
        assert result.events[0].time == pytest.approx(FIRST_BOUNCE, abs=1e-6)
        assert result["h"].min() > -1e-7            # it barely enters the floor


def test_detecting_events_at_step_ends_is_late_and_lets_the_ball_sink(examples):
    """
    The cheap policy: notice the crossing only once the step is over.

    The bounce then happens up to one step late, and the ball is already below
    the floor when it is applied -- so it leaves with the wrong velocity.
    """
    located = bounce(examples, method="rk4", step=1e-3, events="locate")
    at_step_end = bounce(examples, method="rk4", step=1e-3, events="step")

    assert at_step_end.events[0].time > located.events[0].time
    assert at_step_end.events[0].time - located.events[0].time < 1e-3
    assert at_step_end["h"].min() < -1e-4           # a visible penetration
    assert at_step_end["h"].min() > -1e-2

    # The error compounds: with a coarser step the ball sinks further.
    coarse = bounce(examples, method="rk4", step=1e-2, events="step")
    assert coarse["h"].min() < at_step_end["h"].min()


def test_switching_events_off_lets_the_ball_fall_through_the_floor(examples):
    for options in [dict(method="Radau"), dict(method="euler", step=1e-3)]:
        result = bounce(examples, events="off", **options)
        assert result.events == []
        assert result["h"][-1] < -15                # free fall, unimpeded


def test_locating_an_event_costs_more_evaluations_than_ignoring_it(examples):
    """Event location is not free: that is why it is a choice."""
    located = bounce(examples, method="rk4", step=1e-3, events="locate")
    ignored = bounce(examples, method="rk4", step=1e-3, events="off")
    assert len(located.events) == 3 and ignored.events == []
    # Locating adds output points at the events themselves.
    assert len(located.time) > len(ignored.time)


# ---------------------------------------------------------------------------
# Events other than state jumps still work under every policy
# ---------------------------------------------------------------------------

def test_discrete_variables_switch_under_a_fixed_step(examples):
    model = tinysim.load(examples / "thermostat.tiny", "Thermostat")
    result = tinysim.simulate(model, stop=100.0, method="rk4", step=0.01)
    assert len(result.events) > 5
    assert set(np.unique(result["on"])) == {0.0, 1.0}
    settled = result.time > 20
    assert result["T"][settled].max() < 21.5
    assert result["T"][settled].min() > 18.5


def test_a_time_event_is_found_by_every_policy_that_looks_for_one():
    source = """
    model Step
      Real x(start = 0);
      discrete Real u(start = 0);
    equation
      der(x) = u - x;
      when time > 1 then
        u = 1;
      end;
    end Step;
    """
    model = tinysim.load_source(source, "Step")
    # A located event is exact. A detected one is late by at most the interval
    # the simulator was looking at: one output point, or one fixed step.
    for options, lateness in [(dict(events="locate"), 1e-6),
                              (dict(events="step"), 3.0 / 300),
                              (dict(method="rk4", step=1e-3), 1e-6),
                              (dict(method="rk4", step=1e-3, events="step"), 1e-3)]:
        result = tinysim.simulate(model, stop=3.0, points=301, **options)
        assert len(result.events) == 1
        assert 1.0 <= result.events[0].time <= 1.0 + lateness + 1e-9
        assert result["x"][-1] == pytest.approx(1 - math.exp(-2.0), rel=1e-2)

    ignored = tinysim.simulate(model, stop=3.0, points=301, events="off")
    assert ignored.events == []
    assert ignored["x"][-1] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# The options have to be a sensible combination
# ---------------------------------------------------------------------------

def test_a_fixed_step_method_without_a_step_is_reported():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    with pytest.raises(ValueError, match="needs a step size"):
        tinysim.simulate(model, stop=1.0, method="rk4")


def test_a_step_size_given_to_a_variable_step_method_is_reported():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    with pytest.raises(ValueError, match="chooses its own step size"):
        tinysim.simulate(model, stop=1.0, method="Radau", step=0.01)


def test_an_unknown_event_policy_is_reported():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    with pytest.raises(ValueError, match="events must be one of"):
        tinysim.simulate(model, stop=1.0, events="maybe")
