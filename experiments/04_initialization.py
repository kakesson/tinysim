"""
Experiment 4 -- initialization is a system of equations of its own.

    python experiments/04_initialization.py

The tank model does not say what its initial level is.  It says something
better: that the level should start where it is not changing.  That single
`initial equation` turns the start-up into a small nonlinear problem, solved
once, before the integration begins -- with its own unknowns, its own matching
and its own generated code.

    python experiments/04_initialization.py --html
"""

import pathlib

import matplotlib.pyplot as plt

import setup_path  # noqa: F401  (lets this run without installing TinySim)
import tinysim
from tinysim import htmlreport

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"
FIGURES = pathlib.Path(__file__).resolve().parent.parent / "figures"
FIGURES.mkdir(exist_ok=True)

page = htmlreport.start(
    __file__,
    title="Experiment 4 - initialization is its own system of equations",
    subtitle="A tank that starts where its level is not changing")

tank = tinysim.load(EXAMPLES / "tank.tiny", "Tank")
page.add_text("""
    During simulation the unknowns are der(h) and q, and the integrator
    supplies h. At initialization h is an unknown as well, and the extra
    equation der(h) = 0 is what pays for it. That is a different system, with
    its own matching, its own solution order and its own generated function -
    both are shown below.
    """)
page.add_source(EXAMPLES / "tank.tiny")
page.add_model(tank)

print("Unknowns during simulation :", tank.analysis.unknowns)
print("Unknowns at initialization :", tank.initialization_analysis.unknowns)
print("  -- note that the state h is an unknown of the initialization problem,")
print("     and that der(h) = 0 is the extra equation that pays for it.\n")

tinysim.explain(tank, "initialization")

steady = tinysim.simulate(tank, stop=20.0, points=201)
print(f"\nthe solver started the tank at h = {steady['h'][0]:.4f} m, "
      f"and (qin/k)^2 = {(0.3 / 0.5) ** 2:.4f} m")

# Compare with a run that starts away from the steady state.
disturbed_source = (EXAMPLES / "tank.tiny").read_text()
disturbed_source = disturbed_source.replace("initial equation\n  der(h) = 0;\n", "")
disturbed_source = disturbed_source.replace('Real h "level [m]";',
                                            'Real h(start = 2) "level [m]";')
disturbed = tinysim.simulate(tinysim.load_source(disturbed_source, "Tank"),
                             stop=20.0, points=201)

figure, axis = plt.subplots(figsize=(8, 4.5))
axis.plot(steady.time, steady["h"], label="initial equation: der(h) = 0")
axis.plot(disturbed.time, disturbed["h"], "--", label="start = 2, no initial equation")
axis.set_xlabel("time [s]")
axis.set_ylabel("level h [m]")
axis.set_title("Steady-state initialization against an arbitrary start value")
axis.legend()
axis.grid(alpha=0.3)
figure.tight_layout()
figure.savefig(FIGURES / "tank_initialization.png", dpi=150)
print(f"wrote {FIGURES / 'tank_initialization.png'}")
page.add_result(steady, ["h", "q"], title="The simulation - steady start")
contracts = tinysim.check_contracts(tank, steady)
print(f"\ncontracts: {contracts.summary()}")
page.add_text("""
    The tank's contract says what starting in steady state is supposed to mean:
    the inflow and the outflow stay balanced, and the level does not drift. It
    is the initial equation, stated as a requirement rather than as an equation
    - and checked against the run rather than solved.
    """)
page.add_contracts(tank, contracts)
page.add_figure(figure, "With the initial equation the level never moves. "
                        "Given an arbitrary start value instead, the same "
                        "model has to settle first.")
page.finish()
