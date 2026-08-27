"""
Experiment 7 -- step size and event handling are choices, and they cost.

    python experiments/07_solvers.py
    python experiments/07_solvers.py --html

Two questions this answers by measurement rather than by assertion:

1. What does a fixed step buy and cost? The RC circuit has a known solution, so
   the error of `euler`, `heun` and `rk4` can be plotted against the step size.
   The slopes on a log-log plot are 1, 2 and 4 -- the order of each method.

2. What does locating an event exactly buy? The bouncing ball is simulated
   three times -- finding the crossing instant, noticing it only at the end of
   the step, and ignoring events altogether -- and the three answers are
   summarised in a table here. Experiment 8 takes that comparison apart
   properly, with the crossing function drawn.
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
    title="Experiment 7 - fixed step against variable step",
    subtitle="What a step size buys and costs, measured against a known solution")

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
# 2. A fixed step still has to cope with events -- summarised here, taken apart
#    in experiment 8
# ---------------------------------------------------------------------------
ball = tinysim.load(EXAMPLES / "bouncing_ball.tiny", "BouncingBall")
FIRST_BOUNCE = math.sqrt(2 * 1.0 / 9.81)

runs = {policy: tinysim.simulate(ball, stop=3.0, method="rk4", step=1e-3,
                                 events=policy)
        for policy in ("locate", "step", "off")}

print(f"\n\nThe bouncing ball, rk4 with a 1 ms step. "
      f"The first bounce is at sqrt(2h/g) = {FIRST_BOUNCE:.6f} s.\n")
print(f"  {'events=':<10}{'bounces':>9}{'first at':>12}{'late by':>12}"
      f"{'deepest h':>12}{'h at 3 s':>11}")
summary = []
for policy, result in runs.items():
    first = result.events[0].time if result.events else float("nan")
    print(f"  {policy:<10}{len(result.events):9d}{first:12.6f}"
          f"{first - FIRST_BOUNCE:12.2e}{result['h'].min():12.2e}"
          f"{result['h'][-1]:11.4f}")
    summary.append(f"events={policy:<8} {len(result.events)} bounces, "
                   f"first at {first:.6f} s "
                   f"(late by {first - FIRST_BOUNCE:.1e}), "
                   f"deepest h {result['h'].min():.2e} m, "
                   f"h at 3 s {result['h'][-1]:.4f} m")
print("\nExperiment 8 draws the crossing function and takes this apart.")

page.add_text(f"""
    A step size is only half the choice. The other half is what the simulator
    does when a when-condition changes during a step. Here is the bouncing ball
    with the same integrator and the same 1 ms step under all three policies -
    the exact first bounce is at sqrt(2h/g) = {FIRST_BOUNCE:.6f} s. Experiment 8
    draws the crossing function itself and shows where each answer comes from.
    """)
page.add_code("\n".join(summary), title="The same model under three event policies")

page.finish()
