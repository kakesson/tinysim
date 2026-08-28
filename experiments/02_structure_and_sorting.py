"""
Experiment 2 -- the structure of an equation system, drawn.

    python experiments/02_structure_and_sorting.py

Two pictures of the same equations, side by side: as written, and after the
matching and the BLT sorting.  The second one is block lower triangular, which
is exactly what makes it solvable one block at a time.  The resistor network
has an algebraic loop, so one of its blocks is bigger than 1x1 -- outlined in
red.

    python experiments/02_structure_and_sorting.py --html
"""

import pathlib

import matplotlib.pyplot as plt

import setup_path  # noqa: F401  (lets this run without installing TinySim)
import tinysim
from tinysim import htmlreport
from tinysim.plotting import plot_incidence

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"
FIGURES = pathlib.Path(__file__).resolve().parent.parent / "figures"

page = htmlreport.start(
    __file__,
    title="Experiment 2 - the structure of an equation system",
    subtitle="Matching and BLT sorting, drawn, with and without an algebraic loop")
page.add_text("""
    Two models, and for each one the same equations pictured twice: as written,
    and after the matching and the sorting. The second picture is block lower
    triangular, which is precisely the statement that the blocks can be solved
    one at a time from the top down.
    """)

for filename, model_name in [("electrical.tiny", "RCCircuit"),
                             ("resistor_network.tiny", "ResistorNetwork")]:
    model = tinysim.load(EXAMPLES / filename, model_name)
    loops = [block for block in model.analysis.blocks if len(block) > 1]
    print(f"{model_name}: {len(model.flat.equations)} equations after flattening, "
          f"{len(model.model.equations)} after alias elimination, "
          f"{len(model.analysis.blocks)} blocks, {len(loops)} algebraic loop(s)")

    page.add_source(EXAMPLES / filename, title=f"The model - {model_name}")
    page.add_model(model, title=f"How {model_name} was compiled")

    # Both models carry contracts; a short run is enough to check them.
    stop = 1.0 if model_name == "RCCircuit" else 0.01
    contracts = tinysim.check_contracts(
        model, tinysim.simulate(model, stop=stop, points=2001))
    print(f"  contracts: {contracts.summary()}")
    page.add_contracts(model, contracts, title=f"Contracts - {model_name}")

    figure, (left, right) = plt.subplots(1, 2, figsize=(13, 5))
    plot_incidence(model.analysis, sorted_form=False, ax=left)
    plot_incidence(model.analysis, sorted_form=True, ax=right)
    figure.suptitle(f"{model_name}: incidence matrix before and after sorting")
    figure.tight_layout()

    FIGURES.mkdir(exist_ok=True)
    target = FIGURES / f"incidence_{model_name.lower()}.png"
    figure.savefig(target, dpi=150)
    print(f"  wrote {target}")
    page.add_figure(figure, f"{model_name}: the incidence matrix before and "
                            f"after sorting. Dark cells are the unknown each "
                            f"equation was matched with.")

    # The loop, and how the generated code deals with it.
    for block in model.code.blocks:
        if block.size > 1:
            print(f"  block {block.index} solves {', '.join(block.unknowns)} "
                  f"as a {block.method}:")
            for line in block.lines:
                print("   ", line.strip())
            page.add_code("\n".join(line.strip() for line in block.lines),
                          title=f"{model_name}: the loop, as generated code")

page.finish()
