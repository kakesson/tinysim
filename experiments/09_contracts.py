"""
Experiment 9 -- assume-guarantee contracts, checked against a run.

    python experiments/09_contracts.py
    python experiments/09_contracts.py --html

A contract says what a model needs from its environment and what it promises in
return. It is read as *assume implies guarantee*, which is why there are three
verdicts and not two: on a run where an assumption fails, nothing was promised,
and the honest answer is "not tested".

Three things this experiment shows:

1. **A margin, not a tick.** Every clause gets a number -- the robustness of
   the formula -- so "satisfied" comes with how much room there was, and
   "violated" with by how much.
2. **Contracts compose.** A component's contract is checked once per instance,
   under the environment the system actually gave it, which separates *the
   system misused the component* from *the component broke its promise*.
3. **A contract turns a wrong plot into a number.** The bouncing ball is
   simulated with event detection on and off; the same contract passes and then
   fails by 43 metres.
4. **The monitor is checked against someone else's.** The same clauses are
   handed to SignalTemporalLogic.jl, and the two implementations are compared.
   That part is skipped when Julia is not installed.
"""

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
    title="Experiment 9 - assume-guarantee contracts",
    subtitle="What a model promises, whether the run kept the promise, and by how much")


def show(report, heading):
    """Print one report the way the terminal report does, but compactly."""
    print(f"\n{heading}\n{'-' * len(heading)}")
    for item in report.results:
        print(f"  {item.title:32s} {item.verdict.upper()}")
        for clause in item.assumptions + item.guarantees:
            print(f"      {clause.kind:9s} {clause.clause.written:44s} "
                  f"margin {clause.margin_text:>11s}  at t = {clause.at_time:.4g}")
        for note in item.notes:
            print(f"      note: {note}")
    print(f"  => {report.summary()}")


# ---------------------------------------------------------------------------
# 1. A margin, not a tick
# ---------------------------------------------------------------------------
circuit = tinysim.load(EXAMPLES / "electrical.tiny", "RCCircuit")
run = tinysim.simulate(circuit, stop=1.0, points=1001)
report = tinysim.check_contracts(circuit, run)
show(report, "The RC circuit")

page.add_text("""
    The contract below is three sentences about the circuit. Two of them are
    comfortably true; the third, that the capacitor never overshoots the
    source, is true by less than a millivolt - and the report says so, rather
    than printing a tick. That number is the robustness of the formula, and it
    is what a contract gives you that a test does not.
    """)
page.add_source(EXAMPLES / "electrical.tiny", title="The model and its contract")
page.add_contracts(circuit, report, title="Contracts - the RC circuit")
page.add_result(run, ["c.v", "r.i"])

# ---------------------------------------------------------------------------
# 2. Contracts compose: one per component instance
# ---------------------------------------------------------------------------
motor = tinysim.load(EXAMPLES / "dcmotor.tiny", "DCMotor")
motor_run = tinysim.simulate(motor, stop=3.0, points=3001)
motor_report = tinysim.check_contracts(motor, motor_run)
show(motor_report, "The DC motor, and the components inside it")

page.add_text("""
    The DC motor carries a contract of its own, and so do two of the components
    it is built from. A component contract is attached to the class, so it is
    checked once for every instance, against the environment that instance
    actually had. Reading the report from the bottom up is the compositional
    argument in miniature: the system kept the inductor inside its rated
    voltage, so the inductor owed its current bound, and it kept it.
    """)
page.add_contracts(motor, motor_report, title="Contracts - the DC motor")

# Raise the supply until the inductor's own assumption breaks: the finding is
# then against the *system*, not the component.
overdriven_source = (EXAMPLES / "dcmotor.tiny").read_text().replace(
    "ConstantVoltage src(V = 24);", "ConstantVoltage src(V = 48);").replace(
    "always src.V >= 20 and src.V <= 28;", "always src.V >= 20 and src.V <= 60;")
overdriven = tinysim.load_source(overdriven_source, "DCMotor")
overdriven_run = tinysim.simulate(overdriven, stop=3.0, points=3001)
overdriven_report = tinysim.check_contracts(overdriven, overdriven_run)
show(overdriven_report, "The same motor on a 48 V supply")

page.add_text("""
    Now the supply is doubled. The inductor's guarantee is not broken - but its
    assumption is, so the run says nothing about whether the inductor would
    have kept its promise. That verdict, "not tested", is the one a two-valued
    pass/fail cannot express, and it points at the system rather than at the
    component.
    """)
page.add_contracts(overdriven, overdriven_report,
                   title="Contracts - the motor on a 48 V supply")

# ---------------------------------------------------------------------------
# 3. A contract turns a wrong plot into a number
# ---------------------------------------------------------------------------
ball = tinysim.load(EXAMPLES / "bouncing_ball.tiny", "BouncingBall")
runs, reports = {}, {}
for policy in ("locate", "step", "off"):
    runs[policy] = tinysim.simulate(ball, stop=3.0, method="rk4", step=1e-3,
                                    events=policy)
    reports[policy] = tinysim.check_contracts(ball, runs[policy])
    show(reports[policy], f"The bouncing ball with events={policy!r}")

figure, (heights, margins) = plt.subplots(1, 2, figsize=(12, 4.4))
colours = {"locate": "crimson", "step": "darkorange", "off": "seagreen"}
for policy, run_result in runs.items():
    heights.plot(run_result.time, run_result["h"], color=colours[policy],
                 linewidth=1.2, label=f'events="{policy}"')
heights.axhline(-0.001, color="black", linestyle="--", linewidth=0.9,
                label="the contract: h >= -0.001")
heights.set_ylim(-0.5, 1.15)
heights.set_xlabel("time [s]")
heights.set_ylabel("height h [m]")
heights.set_title("The guarantee, and three runs")
heights.legend(fontsize=8)
heights.grid(alpha=0.3)

names = list(runs)
values = [reports[policy].results[0].failing.margin for policy in names]
bars = margins.bar(names, [max(value, -0.5) for value in values],
                   color=[colours[policy] for policy in names])
for bar, value in zip(bars, values):
    margins.annotate(f"{value:+.3g}", (bar.get_x() + bar.get_width() / 2,
                                       max(value, -0.5)),
                     ha="center", va="bottom" if value >= 0 else "top", fontsize=9)
margins.axhline(0, color="black", linewidth=0.9)
margins.set_ylabel("margin of the tightest guarantee [m]")
margins.set_title("Satisfied, and by how much (clipped at -0.5)")
margins.grid(alpha=0.3, axis="y")
figure.tight_layout()
figure.savefig(FIGURES / "contracts_bouncing_ball.png", dpi=150)

page.add_text("""
    The same contract, the same model, three event policies. Locating the
    crossing keeps the promise with a millimetre to spare; noticing it a step
    late breaks it by about a millimetre; ignoring events breaks it by
    43 metres. Nothing about the model changed - only how carefully it was
    simulated - and the contract is what makes that difference a number instead
    of an impression.
    """)
page.add_source(EXAMPLES / "bouncing_ball.tiny", title="The ball and its contract")
for policy in ("locate", "step", "off"):
    page.add_contracts(ball, reports[policy],
                       title=f'Contracts - the ball with events="{policy}"')
page.add_figure(figure, "Left: the guarantee drawn on the trajectories. "
                        "Right: the margin of the tightest guarantee under "
                        "each policy.")

# ---------------------------------------------------------------------------
# 4. The same clauses, checked by somebody else's implementation
# ---------------------------------------------------------------------------
from tinysim import stl_julia  # noqa: E402  (optional, and only needed here)

if stl_julia.available():
    program, _, translated = stl_julia.build_script(circuit, run)
    builtin, julia, differences = tinysim.cross_check_contracts(circuit, run)
    worst = max(differences.values())

    print("\n\nThe same contract, checked by SignalTemporalLogic.jl")
    print("---------------------------------------------------")
    print(f"  {'clause':46s}{'TinySim':>16s}{'Julia':>16s}{'difference':>13s}")
    for item in builtin.results:
        for clause in item.assumptions + item.guarantees:
            label = (f"{item.instance}|{item.contract.name}|{clause.kind}|"
                     f"{clause.clause.line}")
            if label not in differences:
                continue
            other = [c for r in julia.results
                     for c in r.assumptions + r.guarantees
                     if c.clause is clause.clause][0]
            print(f"  {clause.clause.written[:44]:46s}{clause.margin:16.10g}"
                  f"{other.margin:16.10g}{differences[label]:13.1e}")
    print(f"  => the two implementations differ by at most {worst:.1e}")

    page.add_text("""
        TinySim's monitor is written out longhand so that it can be read, which
        is not the same as being right. The clauses below were handed to
        SignalTemporalLogic.jl - an independent implementation from the
        Stanford Intelligent Systems Laboratory - and the two agree exactly.
        The Julia program is generated the same way the simulation code is, and
        printed for the same reason.
        """)
    page.add_code(program, title="The contract, as SignalTemporalLogic.jl")
    unsupported = [clause for clause in translated if clause.unsupported]
    if unsupported:
        page.add_text("Clauses that library cannot express - it has no rising "
                      "edge, and no temporal operator inside another - fall "
                      "back to TinySim's own monitor and are marked as such, "
                      "rather than being checked by something the report does "
                      "not name.")
else:
    print("\n\n(SignalTemporalLogic.jl is not installed; the cross-check was "
          "skipped.\n Run  python -c \"import tinysim.stl_julia as j; "
          "j.install()\"  once to enable it.)")
    page.add_text("""
        TinySim can also hand these clauses to SignalTemporalLogic.jl and
        compare the two implementations. Julia was not available when this page
        was generated, so that section is missing here.
        """)

print("\nA run can falsify a contract. It cannot verify one.")
page.finish()
