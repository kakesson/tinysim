"""
Experiment 2 -- the structure of an equation system, drawn.

    python experiments/02_structure_and_sorting.py

Two pictures of the same equations, side by side: as written, and after the
matching and the BLT sorting.  The second one is block lower triangular, which
is exactly what makes it solvable one block at a time.  The resistor network
has an algebraic loop, so one of its blocks is bigger than 1x1 -- outlined in
red.
"""

import pathlib

import matplotlib.pyplot as plt

import setup_path  # noqa: F401  (lets this run without installing TinySim)
import tinysim
from tinysim.plotting import plot_incidence

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"
FIGURES = pathlib.Path(__file__).resolve().parent.parent / "figures"

for filename, model_name in [("electrical.tiny", "RCCircuit"),
                             ("resistor_network.tiny", "ResistorNetwork")]:
    model = tinysim.load(EXAMPLES / filename, model_name)
    loops = [block for block in model.analysis.blocks if len(block) > 1]
    print(f"{model_name}: {len(model.flat.equations)} equations after flattening, "
          f"{len(model.model.equations)} after alias elimination, "
          f"{len(model.analysis.blocks)} blocks, {len(loops)} algebraic loop(s)")

    figure, (left, right) = plt.subplots(1, 2, figsize=(13, 5))
    plot_incidence(model.analysis, sorted_form=False, ax=left)
    plot_incidence(model.analysis, sorted_form=True, ax=right)
    figure.suptitle(f"{model_name}: incidence matrix before and after sorting")
    figure.tight_layout()

    FIGURES.mkdir(exist_ok=True)
    target = FIGURES / f"incidence_{model_name.lower()}.png"
    figure.savefig(target, dpi=150)
    print(f"  wrote {target}")

    # The loop, and how the generated code deals with it.
    for block in model.code.blocks:
        if block.size > 1:
            print(f"  block {block.index} solves {', '.join(block.unknowns)} "
                  f"as a {block.method}:")
            for line in block.lines:
                print("   ", line.strip())

plt.show()
