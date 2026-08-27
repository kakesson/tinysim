"""
Experiment 1 -- from model text to simulation result, one stage at a time.

Run it with:

    python experiments/01_rc_pipeline.py

This is the script to read first.  It walks the RC circuit through the whole
compiler pipeline, printing what each stage produced, and finishes by comparing
the simulation against the solution you would get with pen and paper.
"""

import pathlib

import matplotlib.pyplot as plt
import numpy as np

import setup_path  # noqa: F401  (lets this run without installing TinySim)
import tinysim

HERE = pathlib.Path(__file__).resolve().parent
EXAMPLES = HERE.parent / "examples"
FIGURES = HERE.parent / "figures"

# ---------------------------------------------------------------------------
# 1. Compile.  Everything the pipeline produced is kept on the returned object.
# ---------------------------------------------------------------------------
model = tinysim.load(EXAMPLES / "electrical.tiny", "RCCircuit")
print(model)

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

FIGURES.mkdir(exist_ok=True)
target = FIGURES / "rc_circuit.png"
figure.savefig(target, dpi=150)
print(f"wrote {target}")
plt.show()
