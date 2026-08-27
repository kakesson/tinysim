"""End-to-end simulation, checked against results worked out by hand."""

import math

import numpy as np
import pytest

import tinysim
from tinysim.flatten import ModelError

from conftest import ELECTRICAL, RC_CIRCUIT


def test_rc_circuit_matches_the_analytic_solution():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    result = tinysim.simulate(model, stop=0.5, points=51, rtol=1e-9, atol=1e-11)
    time_constant = 100 * 1e-3
    expected = 10 * (1 - np.exp(-result.time / time_constant))
    assert np.allclose(result["c.v"], expected, atol=1e-6)
    # Kirchhoff must hold at every instant, for the recovered variables too.
    assert np.allclose(result["r.i"], result["c.i"])
    assert np.allclose(result["src.v"], result["r.v"] + result["c.v"])


def test_pendulum_conserves_energy_without_damping(examples):
    source = (examples / "pendulum.tiny").read_text().replace(
        "parameter Real d = 0.1", "parameter Real d = 0.0")
    model = tinysim.load_source(source, "Pendulum")
    result = tinysim.simulate(model, stop=10.0, points=1001,
                              rtol=1e-10, atol=1e-12)
    m, length, g = 1.0, 1.0, 9.81
    energy = (0.5 * m * (length * result["w"]) ** 2
              + m * g * length * (1 - np.cos(result["phi"])))
    assert np.allclose(energy, energy[0], rtol=1e-6)


def test_small_oscillations_have_the_textbook_period(examples):
    source = (examples / "pendulum.tiny").read_text()
    source = source.replace("parameter Real d = 0.1", "parameter Real d = 0.0")
    source = source.replace("phi(start = 1.0)", "phi(start = 0.01)")
    model = tinysim.load_source(source, "Pendulum")
    result = tinysim.simulate(model, stop=6.0, points=6001, rtol=1e-10)
    # Time between the first two upward zero crossings of phi.
    crossings = [result.time[i] for i in range(1, len(result.time))
                 if result["phi"][i - 1] < 0 <= result["phi"][i]]
    period = crossings[1] - crossings[0]
    assert period == pytest.approx(2 * math.pi * math.sqrt(1.0 / 9.81), rel=1e-3)


def test_dc_motor_reaches_the_steady_state_speed(examples):
    model = tinysim.load(examples / "dcmotor.tiny", "DCMotor")
    result = tinysim.simulate(model, stop=5.0, points=501)
    voltage, resistance, k, damping = 24.0, 0.5, 0.1, 0.01
    expected = voltage * k / (k * k + resistance * damping)
    assert result["load.w"][-1] == pytest.approx(expected, rel=1e-3)


def test_tank_starts_in_the_steady_state_the_initial_equation_asks_for(examples):
    model = tinysim.load(examples / "tank.tiny", "Tank")
    result = tinysim.simulate(model, stop=5.0, points=51)
    assert result["h"][0] == pytest.approx((0.3 / 0.5) ** 2)
    assert np.allclose(result["h"], result["h"][0], atol=1e-8)   # it stays there


def test_bouncing_ball_events_happen_when_they_should(examples):
    model = tinysim.load(examples / "bouncing_ball.tiny", "BouncingBall")
    result = tinysim.simulate(model, stop=2.0, points=2001)
    first = math.sqrt(2 * 1.0 / 9.81)
    assert result.events[0].time == pytest.approx(first, rel=1e-4)
    before, after = result.events[0].changes["v"]
    assert after == pytest.approx(-0.8 * before, rel=1e-6)
    # It never sinks through: the small overshoot is the event tolerance,
    # which is how far past the boundary a crossing has to go to be detected.
    assert result["h"].min() > -2e-8
    assert len(result.events) == 3


def test_thermostat_keeps_the_temperature_inside_the_band(examples):
    model = tinysim.load(examples / "thermostat.tiny", "Thermostat")
    result = tinysim.simulate(model, stop=200.0, points=2001)
    settled = result.time > 20
    assert result["T"][settled].max() < 21.5
    assert result["T"][settled].min() > 18.5
    assert set(np.unique(result["on"])) == {0.0, 1.0}


def test_nonlinear_loop_solution_satisfies_the_equations(examples):
    model = tinysim.load(examples / "diode_circuit.tiny", "DiodeCircuit")
    result = tinysim.simulate(model, stop=1.0, points=101)
    # Kirchhoff's voltage law around the diode branch.
    assert np.allclose(result["c.v"], result["d.v"] + result["r2.v"], atol=1e-8)
    # And the diode's own characteristic.
    expected = 1e-6 * (np.exp(result["d.v"] / 0.2) - 1)
    assert np.allclose(result["d.i"], expected, rtol=1e-6)


def test_a_model_without_states_is_still_solved():
    """A purely algebraic model: nothing to integrate, but still a system."""
    source = ELECTRICAL + """
    model Divider
      ConstantVoltage src(V = 12);
      Resistor r1(R = 100);
      Resistor r2(R = 200);
      Ground gnd;
    equation
      connect(src.p, r1.p);
      connect(r1.n, r2.p);
      connect(r2.n, src.n);
      connect(src.n, gnd.p);
    end Divider;
    """
    model = tinysim.load_source(source, "Divider")
    assert model.analysis.states == []
    result = tinysim.simulate(model, stop=1.0, points=5)
    assert np.allclose(result["r2.v"], 12 * 200 / 300)


TOGGLE = """
model Toggle "pre() lets an event refer to the value before it fired"
  Real x(start = 0);
  discrete Real on(start = 0);
equation
  der(x) = 1;
  when x > 0.1 then
    on = 1 - pre(on);
    reinit(x, 0);
  end;
end Toggle;
"""


def test_pre_refers_to_the_value_before_the_event():
    model = tinysim.load_source(TOGGLE, "Toggle")
    result = tinysim.simulate(model, stop=0.45, points=451)
    assert len(result.events) == 4
    assert [event.changes["on"][1] for event in result.events] == [1, 0, 1, 0]
    assert result["x"].max() == pytest.approx(0.1, abs=1e-3)


def test_time_events_are_found():
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
    result = tinysim.simulate(model, stop=3.0, points=301)
    assert len(result.events) == 1
    assert result.events[0].time == pytest.approx(1.0, abs=1e-6)
    assert result["x"][-1] == pytest.approx(1 - math.exp(-2.0), rel=1e-4)


def test_zeno_behaviour_shows_up_as_the_ball_falling_through_the_floor(examples):
    """
    In exact arithmetic the ball bounces infinitely often in finite time.

    A simulator cannot do that.  Once the bounces are smaller than the event
    tolerance, the contact is no longer detected, the condition `h < 0` stays
    true and never re-arms, and the ball simply keeps falling.  This is the
    classic Zeno artefact, and TinySim shows it rather than hiding it -- the
    honest fix is to model the resting contact, not to tune the tolerance.
    """
    model = tinysim.load(examples / "bouncing_ball.tiny", "BouncingBall")
    result = tinysim.simulate(model, stop=20.0, points=2001)
    assert 5 < len(result.events) < 100          # the bounces stop being found
    assert result["h"][-1] < -1.0                # and then it falls through


def test_too_many_events_stops_with_an_explanation():
    model = tinysim.load_source(TOGGLE, "Toggle")
    result = tinysim.simulate(model, stop=10.0, points=101, max_events=5)
    assert "more than 5 events" in result.message
    assert len(result.events) == 5


def test_reinit_on_something_that_is_not_a_state_is_reported():
    source = """
    model Bad
      Real x(start = 0), y;
    equation
      der(x) = 1;
      y = 2 * x;
      when x > 1 then
        reinit(y, 0);
      end;
    end Bad;
    """
    model = tinysim.load_source(source, "Bad")
    with pytest.raises(ModelError, match="can only be applied to a state"):
        tinysim.simulate(model, stop=2.0, points=21)


def test_discrete_variable_without_a_start_value_is_reported():
    source = """
    model Bad
      Real x(start = 0);
      discrete Real u;
    equation
      der(x) = u;
      when time > 1 then
        u = 1;
      end;
    end Bad;
    """
    model = tinysim.load_source(source, "Bad")
    with pytest.raises(ModelError, match="needs a start value"):
        tinysim.simulate(model, stop=2.0)


def test_asking_for_an_unknown_variable_is_helpful():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    result = tinysim.simulate(model, stop=0.1, points=5)
    with pytest.raises(KeyError, match="not a variable of this model"):
        result["c.voltage"]


def test_if_expressions_and_functions_are_generated_correctly():
    """
    A saturating source: the equation switches on a condition.

    `if` is an *expression* in TinySim, so it appears inside a block like any
    other formula -- SymPy turns it into a piecewise expression and the
    generated code into a conditional.
    """
    source = """
    model Saturated
      parameter Real limit = 2;
      parameter Real rate = 3;
      Real u "the raw signal";
      Real y "the saturated signal";
      Real x(start = 0);
    equation
      u = rate * time;
      y = if u > limit then limit else u;
      der(x) = y;
    end Saturated;
    """
    model = tinysim.load_source(source, "Saturated")
    result = tinysim.simulate(model, stop=2.0, points=201, rtol=1e-9, atol=1e-11)
    assert result["y"].max() == pytest.approx(2.0)
    assert result["y"][10] == pytest.approx(3 * result.time[10])
    # x is the area under the saturated ramp: a triangle then a rectangle.
    corner = 2.0 / 3.0
    expected = 0.5 * corner * 2.0 + 2.0 * (2.0 - corner)
    assert result["x"][-1] == pytest.approx(expected, rel=1e-5)


def test_the_built_in_functions_reach_the_generated_code():
    source = """
    model Functions
      Real a, b, c, d;
      Real x(start = 1);
    equation
      a = sqrt(abs(-4));
      b = max(sin(time), 0.5);
      c = tanh(0) + sign(-3);
      d = atan2(1, 1);
      der(x) = 0;
    end Functions;
    """
    result = tinysim.simulate(tinysim.load_source(source, "Functions"),
                              stop=1.0, points=3)
    assert result["a"][0] == pytest.approx(2.0)
    assert result["b"][0] == pytest.approx(0.5)
    assert result["c"][0] == pytest.approx(-1.0)
    assert result["d"][0] == pytest.approx(math.pi / 4)
