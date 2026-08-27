"""
Experiment 7 -- step size and event handling are choices, and they cost.

    python experiments/07_solvers.py
    python experiments/07_solvers.py --html

Two questions this answers by measurement rather than by assertion:

1. What does a fixed step buy and cost? The RC circuit has a known solution, so
   the error of `euler`, `heun` and `rk4` can be plotted against the step size.
   The slopes on a log-log plot are 1, 2 and 4 -- the order of each method.

2. What does locating an event exactly buy? The bouncing ball is simulated
   three times: finding the crossing instant, noticing it only at the end of
   the step, and ignoring events altogether. The first is right, the second is
   late and slowly loses energy, the third falls through the floor.
"""

import math
import pathlib

import matplotlib.pyplot as plt
import numpy as np

import setup_path  # noqa: F401  (lets this run without installing TinySim)
import tinysim
from tinysim import htmlreport

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"
FIGURES = pathlib.Path(__file__).resolve().parent.parent / "figures"
FIGURES.mkdir(exist_ok=True)

page = htmlreport.start(
    __file__,
    title="Experiment 7 - fixed step, variable step, and finding events",
    subtitle="What each solver choice costs, measured on two models with known answers")

# ---------------------------------------------------------------------------
# 1. Accuracy against step size, on a model whose answer we know exactly
# ---------------------------------------------------------------------------
rc = tinysim.load(EXAMPLES / "electrical.tiny", "RCCircuit")
STOP = 0.5
EXACT = 10 * (1 - math.exp(-STOP / (100 * 1e-3)))       # V (1 - exp(-t/RC))

steps = [0.02, 0.01, 0.005, 0.0025, 0.00125]
errors = {}
for method in ("euler", "heun", "rk4"):
    errors[method] = []
    for step in steps:
        result = tinysim.simulate(rc, stop=STOP, method=method, step=step)
        errors[method].append(abs(result["c.v"][-1] - EXACT))

print(f"Error in c.v at t = {STOP} s, against the analytic solution\n")
print("  step      " + "".join(f"{m:>12}" for m in errors))
for position, step in enumerate(steps):
    print(f"  {step:<10.5f}"
          + "".join(f"{errors[m][position]:12.2e}" for m in errors))

reference = tinysim.simulate(rc, stop=STOP, points=2, rtol=1e-10, atol=1e-12)
print(f"\n  variable step (Radau, rtol 1e-10): "
      f"{abs(reference['c.v'][-1] - EXACT):.2e} in {len(reference.time)} points")

figure, axis = plt.subplots(figsize=(7, 5))
for method, marker in zip(errors, "os^"):
    axis.loglog(steps, errors[method], marker + "-", label=method)
for order, style in [(1, ":"), (2, "--"), (4, "-.")]:
    scale = errors["euler"][0] / steps[0] ** order
    axis.loglog(steps, [scale * s ** order for s in steps], style,
                color="grey", linewidth=0.8, label=f"slope {order}")
axis.set_xlabel("step size h [s]")
axis.set_ylabel("error in c.v at t = 0.5 s [V]")
axis.set_title("A fixed step: the error follows the order of the method")
axis.grid(True, which="both", alpha=0.3)
axis.legend()
figure.tight_layout()
figure.savefig(FIGURES / "solver_accuracy.png", dpi=150)

page.add_text("""
    The RC circuit has an analytic solution, so the error of a fixed-step run
    can simply be measured. Each method's error falls off as its order: halving
    the step divides Euler's error by two, Heun's by four and RK4's by sixteen.
    A variable-step method reaches a tolerance you ask for instead, choosing
    its own steps -- larger where the solution is smooth, smaller where it is
    not.
    """)
page.add_source(EXAMPLES / "electrical.tiny", title="The model - RCCircuit")
page.add_figure(figure, "Error against step size, log-log. The grey lines are "
                        "slopes 1, 2 and 4 for comparison.")

# ---------------------------------------------------------------------------
# 2. What locating an event is worth
# ---------------------------------------------------------------------------
ball = tinysim.load(EXAMPLES / "bouncing_ball.tiny", "BouncingBall")
FIRST_BOUNCE = math.sqrt(2 * 1.0 / 9.81)

runs = {
    "locate": tinysim.simulate(ball, stop=3.0, points=3001,
                               method="rk4", step=1e-3, events="locate"),
    "step": tinysim.simulate(ball, stop=3.0, points=3001,
                             method="rk4", step=1e-3, events="step"),
    "off": tinysim.simulate(ball, stop=3.0, points=3001,
                            method="rk4", step=1e-3, events="off"),
}

print(f"\n\nThe bouncing ball, rk4 with a 1 ms step. "
      f"The first bounce is at sqrt(2h/g) = {FIRST_BOUNCE:.6f} s.\n")
print(f"  {'events=':<10}{'bounces':>9}{'first at':>12}{'late by':>12}"
      f"{'deepest h':>12}{'h at 3 s':>11}")
for policy, result in runs.items():
    first = result.events[0].time if result.events else float("nan")
    print(f"  {policy:<10}{len(result.events):9d}{first:12.6f}"
          f"{first - FIRST_BOUNCE:12.2e}{result['h'].min():12.2e}"
          f"{result['h'][-1]:11.4f}")

figure, (whole, zoom) = plt.subplots(1, 2, figsize=(12, 4.5))
# `locate` and `step` almost coincide at this scale, so draw them differently.
styles = {"locate": dict(linewidth=2.6, alpha=0.45),
          "step": dict(linewidth=1.2, linestyle="--"),
          "off": dict(linewidth=1.6)}
for policy, result in runs.items():
    whole.plot(result.time, result["h"], label=f"events={policy}", **styles[policy])
whole.axhline(0, color="grey", linewidth=0.8)
whole.set_ylim(-0.6, 1.1)
whole.set_xlabel("time [s]")
whole.set_ylabel("height h [m]")
whole.set_title("Ignoring events: the ball leaves through the floor")
whole.legend()
whole.grid(alpha=0.3)

for policy in ("locate", "step"):
    result = runs[policy]
    window = (result.time > FIRST_BOUNCE - 0.004) & (result.time < FIRST_BOUNCE + 0.004)
    zoom.plot(result.time[window], result["h"][window], "o-", markersize=3,
              label=f"events={policy}")
zoom.axhline(0, color="grey", linewidth=0.8)
zoom.axvline(FIRST_BOUNCE, color="crimson", linestyle=":", linewidth=0.9,
             label="sqrt(2h/g)")
zoom.set_xlabel("time [s]")
zoom.set_title("The first bounce, magnified")
zoom.legend()
zoom.grid(alpha=0.3)
figure.tight_layout()
figure.savefig(FIGURES / "solver_events.png", dpi=150)

page.add_text("""
    The same model, the same integrator and the same step, three times over.
    Locating the crossing stops the integration at the instant the ball reaches
    the floor. Detecting it at the end of the step applies the bounce once the
    ball is already below the floor, so it leaves with a velocity it should
    never have had - and the error repeats at every bounce. Ignoring events
    means the when-clause never fires at all.
    """)
page.add_source(EXAMPLES / "bouncing_ball.tiny", title="The model - BouncingBall")
page.add_model(ball, title="How BouncingBall was compiled")
page.add_figure(figure, "Left: the three policies over three seconds. Right: "
                        "the first bounce magnified, with the analytic bounce "
                        "time marked.")
for policy, result in runs.items():
    page.add_result(result, ["h", "v"], title=f"The simulation - events={policy}")

page.finish()
