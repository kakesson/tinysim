"""
Experiment 5 -- the errors are part of the teaching material.

    python experiments/05_when_it_does_not_work.py

Four models that a tool must reject, and what it should say about each.  The
last one is the interesting case: a pendulum in Cartesian coordinates is a
perfectly good physical model that simply cannot be turned into an ODE without
index reduction.

    python experiments/05_when_it_does_not_work.py --html
"""

import pathlib

import setup_path  # noqa: F401  (lets this run without installing TinySim)
import tinysim
from tinysim import htmlreport

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"

page = htmlreport.start(
    __file__,
    title="Experiment 5 - the errors are part of the teaching material",
    subtitle="Four models a tool must reject, and what it should say about each")
page.add_text("""
    A modeling tool spends much of its time telling people that what they wrote
    cannot be simulated. The useful message names the offending equation and
    says what to do; the last case below is the interesting one, because the
    model is not wrong at all - it is simply of an index that no rearrangement
    can lower.
    """)

CASES = {
    "an unbalanced model (three unknowns, two equations)": """
        model Unbalanced
          Real x, y, z;
        equation
          x = 1;
          y = 2 * x;
        end Unbalanced;
    """,
    "a model that contradicts itself": """
        model Contradiction
          Real x, y;
        equation
          x = 1;
          x = 2;
        end Contradiction;
    """,
    "a typo in a variable name": """
        model Typo
          Real x;
        equation
          der(x) = -y;
        end Typo;
    """,
}

for description, source in CASES.items():
    print("=" * 78)
    print(description)
    print("=" * 78)
    page.add_code(source.strip(), title=description[0].upper() + description[1:],
                  language="modelica")
    try:
        tinysim.load_source(source)
    except (tinysim.ModelError, tinysim.TinySimSyntaxError) as error:
        print(error, "\n")
        page.add_error(error, getattr(error, "partial_model", None),
                       title="What TinySim says about it")

print("=" * 78)
print("a high-index model: the pendulum in Cartesian coordinates")
print("=" * 78)
print((EXAMPLES / "pendulum_cartesian.tiny").read_text())
page.add_source(EXAMPLES / "pendulum_cartesian.tiny",
                title="A high-index model - the pendulum in Cartesian coordinates")
try:
    tinysim.load(EXAMPLES / "pendulum_cartesian.tiny", "CartesianPendulum")
except tinysim.StructuralError as error:
    print(error)
    page.add_error(error, getattr(error, "partial_model", None),
                   title="Why the Cartesian pendulum cannot be simulated")
    partial = getattr(error, "partial_model", None)
    if partial is not None:
        page.add_text("""
            The contract on that model is worth reading next to the error. It
            says the mass stays at the end of the rod - which is exactly what
            the constraint equation says, and exactly what makes the model
            impossible to put into state-space form. The requirement is
            perfectly reasonable; it is the coordinates that are the problem.
            """)
        page.add_contracts(partial, title="What it was supposed to promise")

print("\nThe same pendulum in angular coordinates has no constraint at all,")
print("and simulates without trouble:")
angular = tinysim.load(EXAMPLES / "pendulum.tiny", "Pendulum")
result = tinysim.simulate(angular, stop=5.0, points=501)
print(f"  states {angular.analysis.states}, "
      f"phi(5 s) = {result['phi'][-1]:.4f} rad")

page.add_text("""
    The same pendulum in angular coordinates has no constraint at all: the rod
    length is built into the coordinates rather than imposed on them. It needs
    none of the machinery real tools spend on index reduction, which is the
    practical lesson - choosing coordinates is part of modeling.
    """)
page.add_source(EXAMPLES / "pendulum.tiny", title="The same pendulum, in an angle")
page.add_model(angular, title="How the angular pendulum was compiled")
page.add_result(result, ["phi", "w"])

contracts = tinysim.check_contracts(angular, result)
print(f"  contracts: {contracts.summary()}")
page.add_contracts(angular, contracts,
                   title="Contracts - the angular pendulum")
page.finish()
