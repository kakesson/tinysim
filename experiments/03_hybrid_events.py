"""
Experiment 3 -- hybrid models: events, state jumps and discrete variables.

    python experiments/03_hybrid_events.py

The bouncing ball shows `reinit`, a jump in a continuous state.  The thermostat
shows a discrete variable switched by two `when` clauses.  Both are simulated
as a *sequence* of continuous segments, one per event.
"""

import pathlib

import matplotlib.pyplot as plt

import setup_path  # noqa: F401  (lets this run without installing TinySim)
import tinysim

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"
FIGURES = pathlib.Path(__file__).resolve().parent.parent / "figures"
FIGURES.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# A bouncing ball: one state event, applied over and over.
# ---------------------------------------------------------------------------
ball = tinysim.load(EXAMPLES / "bouncing_ball.tiny", "BouncingBall")
tinysim.explain(ball, "events,code")

# Stop at 3 s.  Past that the bounces become smaller than the event tolerance,
# they stop being detected, and the ball sinks through the floor -- the classic
# Zeno artefact, which the model itself cannot avoid.
bounces = tinysim.simulate(ball, stop=3.0, points=3001)
print(f"\n{len(bounces.events)} bounces:")
for event in bounces.events:
    print("   ", event)

figure = tinysim.plot(bounces, ["h", "v"], separate=True,
                      title="Bouncing ball: dotted lines mark the events")
figure.savefig(FIGURES / "bouncing_ball.png", dpi=150)

# ---------------------------------------------------------------------------
# A thermostat: a discrete variable, switched by two conditions.
# ---------------------------------------------------------------------------
thermostat = tinysim.load(EXAMPLES / "thermostat.tiny", "Thermostat")
control = tinysim.simulate(thermostat, stop=200.0, points=4001)
print(f"\nthermostat switched {len(control.events)} times")

figure, (temperature, heater) = plt.subplots(2, 1, sharex=True, figsize=(8, 5))
temperature.plot(control.time, control["T"])
temperature.axhline(21, color="grey", linestyle=":")
temperature.axhline(19, color="grey", linestyle=":")
temperature.set_ylabel("T [degC]")
temperature.set_title("Thermostat: the temperature stays inside the band")
temperature.grid(alpha=0.3)

heater.step(control.time, control["on"], where="post", color="tab:red")
heater.set_ylabel("heater on")
heater.set_xlabel("time [s]")
heater.set_ylim(-0.1, 1.1)
heater.grid(alpha=0.3)
figure.tight_layout()
figure.savefig(FIGURES / "thermostat.png", dpi=150)
print(f"wrote figures to {FIGURES}")
plt.show()
