"""
Experiment 5 -- the errors are part of the teaching material.

    python experiments/05_when_it_does_not_work.py

Four models that a tool must reject, and what it should say about each.  The
last one is the interesting case: a pendulum in Cartesian coordinates is a
perfectly good physical model that simply cannot be turned into an ODE without
index reduction.
"""

import pathlib

import setup_path  # noqa: F401  (lets this run without installing TinySim)
import tinysim

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"

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
    try:
        tinysim.load_source(source)
    except (tinysim.ModelError, tinysim.TinySimSyntaxError) as error:
        print(error, "\n")

print("=" * 78)
print("a high-index model: the pendulum in Cartesian coordinates")
print("=" * 78)
print((EXAMPLES / "pendulum_cartesian.tiny").read_text())
try:
    tinysim.load(EXAMPLES / "pendulum_cartesian.tiny", "CartesianPendulum")
except tinysim.StructuralError as error:
    print(error)

print("\nThe same pendulum in angular coordinates has no constraint at all,")
print("and simulates without trouble:")
angular = tinysim.load(EXAMPLES / "pendulum.tiny", "Pendulum")
result = tinysim.simulate(angular, stop=5.0, points=501)
print(f"  states {angular.analysis.states}, "
      f"phi(5 s) = {result['phi'][-1]:.4f} rad")
