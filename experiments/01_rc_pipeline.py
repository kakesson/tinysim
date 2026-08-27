"""
Experiment 1 -- from model text to simulation result, one stage at a time.

Run it with:

    python experiments/01_rc_pipeline.py

This is the script to read first.  It walks the RC circuit through the whole
compiler pipeline, printing what each stage produced, and finishes by comparing
the simulation against the solution you would get with pen and paper.

Add `--html` to write the whole thing -- model, every intermediate form of the
equations, the generated code, the results -- to a standalone web page instead
of showing the plot:

    python experiments/01_rc_pipeline.py --html
"""

import pathlib

import matplotlib.pyplot as plt
import numpy as np

import setup_path  # noqa: F401  (lets this run without installing TinySim)
import tinysim
from tinysim import htmlreport

HERE = pathlib.Path(__file__).resolve().parent
EXAMPLES = HERE.parent / "examples"
FIGURES = HERE.parent / "figures"

page = htmlreport.start(
    __file__,
    title="Experiment 1 - from model text to simulation result",
    subtitle="An RC circuit, followed through every stage of the compiler")

# ---------------------------------------------------------------------------
# 1. Compile.  Everything the pipeline produced is kept on the returned object.
# ---------------------------------------------------------------------------
model = tinysim.load(EXAMPLES / "electrical.tiny", "RCCircuit")
print(model)

page.add_text("""
    Nothing in the model below says what depends on what, or in which order
    anything should be computed. Everything after it is the tool making up for
    that: expanding the components, turning every connect() into equations,
    removing the equations that say nothing, deciding which equation computes
    which unknown, sorting them, and generating code.
    """)
page.add_source(EXAMPLES / "electrical.tiny")
page.add_model(model)

# The stages, printed in order.  Pass a selection to see only some of them,
# for example tinysim.explain(model, "flat,blt,code").
tinysim.explain(model)

# The same information is available as data, not only as text:
print("\nstates:  ", model.analysis.states)
print("blocks:  ", [[i + 1 for i in block] for block in model.analysis.blocks])
print("code is  ", len(model.source.splitlines()), "lines of Python")

# ---------------------------------------------------------------------------
# 2. Simulate.  Stop time, tolerances and output points are chosen here, in
#    Python -- the model file says nothing about them.
# ---------------------------------------------------------------------------
result = tinysim.simulate(model, stop=1.0, points=501)
page.add_result(result, ["c.v", "c.i", "r.v", "r.i"])

# ---------------------------------------------------------------------------
# 3. Compare against the analytic solution, v(t) = V (1 - exp(-t / RC)).
# ---------------------------------------------------------------------------
V, R, C = 10.0, 100.0, 1e-3
exact = V * (1 - np.exp(-result.time / (R * C)))
error = np.max(np.abs(result["c.v"] - exact))
print(f"\nlargest deviation from the analytic solution: {error:.2e} V")

figure, (top, bottom) = plt.subplots(2, 1, sharex=True, figsize=(8, 6))
top.plot(result.time, result["c.v"], label="c.v  (simulated)")
top.plot(result.time, exact, "--", label="V (1 - exp(-t/RC))")
top.set_ylabel("voltage [V]")
top.legend()
top.grid(alpha=0.3)
top.set_title("RC circuit: simulation against the analytic solution")

bottom.plot(result.time, result["r.i"] * 1000, color="tab:red")
bottom.set_ylabel("current [mA]")
bottom.set_xlabel("time [s]")
bottom.grid(alpha=0.3)
figure.tight_layout()

page.add_figure(figure, f"The capacitor voltage against V(1 - exp(-t/RC)); "
                        f"the largest deviation is {error:.1e} V.")

FIGURES.mkdir(exist_ok=True)
target = FIGURES / "rc_circuit.png"
figure.savefig(target, dpi=150)
print(f"wrote {target}")
page.finish()
