"""
Experiment 6 -- one connect rule, two physical domains.

    python experiments/06_dc_motor.py

The DC motor joins an electrical circuit to a rotating shaft.  Nothing in the
tool knows about electricity or mechanics: the same two rules -- potentials
equal, flows sum to zero -- give Kirchhoff's current law on one side and the
balance of torques on the other.  The proof is in the flat equations.

    python experiments/06_dc_motor.py --html
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
    title="Experiment 6 - one connect rule, two physical domains",
    subtitle="A DC motor: Kirchhoff's laws and the balance of torques from the same two lines")

motor = tinysim.load(EXAMPLES / "dcmotor.tiny", "DCMotor")
tinysim.explain(motor, "connections")

page.add_text("""
    Nothing in the tool knows about electricity or mechanics. Both connectors
    declare one potential variable and one flow variable, and the same two
    rules apply to both: potentials in a connection set are equal, flows sum to
    zero. On the electrical side that is Kirchhoff; on the mechanical side it
    is the balance of torques. The flat equations below are the proof.
    """)
page.add_source(EXAMPLES / "dcmotor.tiny")
page.add_model(motor)

print("\nequations that came from connect():")
for equation in motor.flat.equations:
    if equation.origin.startswith("connect"):
        print(f"    {equation.source:<40} # {equation.origin}")

result = tinysim.simulate(motor, stop=2.0, points=2001)

voltage, resistance, k, damping = 24.0, 0.5, 0.1, 0.01
steady_state = voltage * k / (k * k + resistance * damping)
print(f"\nfinal speed {result['load.w'][-1]:.2f} rad/s, "
      f"steady state from hand calculation {steady_state:.2f} rad/s")

figure, (speed, current) = plt.subplots(2, 1, sharex=True, figsize=(8, 5))
speed.plot(result.time, result["load.w"])
speed.axhline(steady_state, color="grey", linestyle=":", label="steady state")
speed.set_ylabel("shaft speed [rad/s]")
speed.set_title("DC motor starting up")
speed.legend()
speed.grid(alpha=0.3)

current.plot(result.time, result["l.i"], color="tab:red")
current.set_ylabel("armature current [A]")
current.set_xlabel("time [s]")
current.grid(alpha=0.3)
figure.tight_layout()
figure.savefig(FIGURES / "dc_motor.png", dpi=150)
print(f"wrote {FIGURES / 'dc_motor.png'}")
page.add_result(result, ["load.w", "load.phi", "l.i", "emf.v"],
                title="The simulation - starting up")
page.add_figure(figure, f"The shaft settles at the speed a hand calculation "
                        f"gives, {steady_state:.1f} rad/s.")
page.finish()
