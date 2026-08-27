"""
Experiment 8 -- zero-crossing detection, illustrated on the bouncing ball.

    python experiments/08_zero_crossing.py
    python experiments/08_zero_crossing.py --html

A `when` condition is not tested at the output points and it is not tested
"often enough". It is turned into a *crossing function*

    g(t) = 0 - h(t)          positive exactly while  h < 0  holds

and the instant g crosses zero upwards is the event. What a simulator does
about that instant is a choice with visible consequences, and the bouncing ball
shows all three at once:

    events="locate"   find the crossing instant itself, and restart there
    events="step"     notice it only at the end of the step it happened in
    events="off"      never look

Everything below is measured against the exact answer: a ball dropped from
h0 with restitution e bounces at

    t_1 = sqrt(2 h0 / g),   t_(n+1) = t_n + 2 e^n sqrt(2 g h0) / g
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

GRAVITY, RESTITUTION, HEIGHT = 9.81, 0.8, 1.0
STEP = 0.005                       # a deliberately visible step size
IMPACT_SPEED = math.sqrt(2 * GRAVITY * HEIGHT)


def analytic_bounce_times(count):
    """When the ball really hits the floor, from elementary mechanics."""
    times = [math.sqrt(2 * HEIGHT / GRAVITY)]
    for n in range(1, count):
        times.append(times[-1] + 2 * RESTITUTION ** n * IMPACT_SPEED / GRAVITY)
    return times


page = htmlreport.start(
    __file__,
    title="Experiment 8 - zero-crossing detection",
    subtitle="The same bouncing ball, with the event located, detected late, and ignored")

ball = tinysim.load(EXAMPLES / "bouncing_ball.tiny", "BouncingBall")
runs = {policy: tinysim.simulate(ball, stop=3.0, method="rk4", step=STEP,
                                 events=policy)
        for policy in ("locate", "step", "off")}
exact = analytic_bounce_times(6)

# ---------------------------------------------------------------------------
# 1. What the integrator actually watches
# ---------------------------------------------------------------------------
print("The crossing function the compiler generated for `when h < 0`, computed\n"
      "alongside the derivatives every time the model is evaluated:\n")
for line in ball.code.source.splitlines():
    if "events =" in line or "event margins" in line or "#        when" in line:
        print("   ", line.rstrip())

free_fall = runs["off"]                       # nobody interferes: pure free fall
# The last few steps before the floor, which is where all the difference is.
window = ((free_fall.time > exact[0] - 5 * STEP)
          & (free_fall.time < exact[0] + 3 * STEP))
sample_time = free_fall.time[window]
crossing_function = -free_fall["h"][window]   # g(t) = 0 - h(t)

# The step in which the sign changes, and the two ways of reacting to it.
first_positive = int(np.argmax(crossing_function > 0))
detected_at = sample_time[first_positive]     # events="step" acts here
located_at = runs["locate"].events[0].time    # events="locate" acts here

print(f"\nWith a {STEP * 1000:g} ms step the sign of g changes between "
      f"t = {sample_time[first_positive - 1]:.4f} and t = {detected_at:.4f}.")
print(f"  events='step'   acts at t = {detected_at:.6f}  "
      f"(h = {free_fall['h'][window][first_positive]:+.4f} m: already below the floor)")
print(f"  events='locate' bisects that step and acts at t = {located_at:.6f}")
print(f"  the exact answer is                       t = {exact[0]:.6f}")

figure, (left, right) = plt.subplots(1, 2, figsize=(12, 4.6))

left.plot(sample_time, crossing_function, "-", color="grey", linewidth=1,
          label="g(t) = -h(t), computed every step")
left.plot(sample_time, crossing_function, "o", color="grey", markersize=6)
for t, g in zip(sample_time, crossing_function):
    left.annotate(f"{g:+.3f}", (t, g), textcoords="offset points",
                  xytext=(0, 8), ha="center", fontsize=6.5, color="dimgrey")
left.axhline(0, color="black", linewidth=0.9)
left.axvspan(sample_time[first_positive - 1], detected_at, color="orange",
             alpha=0.15, label="the step where the sign changes")
left.axvline(located_at, color="crimson", linestyle="-", linewidth=1.2,
             label=f"located crossing (t = {located_at:.4f})")
left.axvline(detected_at, color="darkorange", linestyle="--", linewidth=1.2,
             label=f"noticed at step end (t = {detected_at:.4f})")
left.set_xlabel("time [s]")
left.set_ylabel("crossing function g")
left.set_title("What the integrator watches")
left.legend(fontsize=8, loc="upper left")
left.grid(alpha=0.3)

for policy, colour, style in [("locate", "crimson", "-"), ("step", "darkorange", "--"),
                              ("off", "seagreen", ":")]:
    result = runs[policy]
    inside = (result.time > exact[0] - 3 * STEP) & (result.time < exact[0] + 6 * STEP)
    right.plot(result.time[inside], result["h"][inside], style, color=colour,
               marker="o", markersize=3, label=f'events="{policy}"')
right.axhline(0, color="black", linewidth=0.9)
right.axvline(exact[0], color="black", linestyle=":", linewidth=0.9,
              label="sqrt(2h/g)")
right.set_xlabel("time [s]")
right.set_ylabel("height h [m]")
right.set_title("and what each policy does about it")
right.legend(fontsize=8)
right.grid(alpha=0.3)
figure.tight_layout()
figure.savefig(FIGURES / "zero_crossing_detail.png", dpi=150)

page.add_text(f"""
    The when-condition h < 0 became a crossing function g(t) = -h(t), positive
    exactly while the condition holds. The integrator evaluates it every step
    (grey dots, {STEP * 1000:g} ms apart). Between the last two dots the sign
    changes: that is all any of these policies has to go on. Locating the event
    means bisecting that step until the crossing instant is pinned down;
    detecting it at the end of the step means acting at the dot on the right,
    by which time the ball is already {abs(free_fall['h'][window][first_positive]) * 1000:.1f} mm
    below the floor, falling faster than it ever should have been.
    """)
page.add_source(EXAMPLES / "bouncing_ball.tiny")
page.add_figure(figure, "Left: the crossing function and the step in which its "
                        "sign changes. Right: the trajectory each policy "
                        "produces from that same information.")

# ---------------------------------------------------------------------------
# 2. The error does not stay local: it compounds over the bounces
# ---------------------------------------------------------------------------
print(f"\n\nBounce times, rk4 with a {STEP * 1000:g} ms step:\n")
header = f"  {'n':>2}{'exact':>11}{'locate':>11}{'error':>11}{'step end':>11}{'error':>11}"
print(header)
rows = []
for number in range(len(runs["locate"].events)):
    exact_time = exact[number]
    located = runs["locate"].events[number].time
    detected = runs["step"].events[number].time
    print(f"  {number + 1:2d}{exact_time:11.6f}{located:11.6f}"
          f"{located - exact_time:11.2e}{detected:11.6f}{detected - exact_time:11.2e}")
    rows.append((number + 1, exact_time, located, detected))

energy = {policy: GRAVITY * runs[policy]["h"] + 0.5 * runs[policy]["v"] ** 2
          for policy in ("locate", "step")}


def energy_retained(policy):
    """The fraction of the energy the ball keeps at each bounce."""
    series, times = energy[policy], runs[policy].time
    after = [series[np.argmin(np.abs(times - (event.time + 1e-9)))]
             for event in runs[policy].events]
    return [after[index + 1] / after[index] for index in range(len(after) - 1)]


retained = {policy: energy_retained(policy) for policy in ("locate", "step")}
print(f"\nEnergy kept at each bounce. The model says every bounce keeps "
      f"e^2 = {RESTITUTION ** 2:.2f}:")
for policy in ("locate", "step"):
    print(f"  {policy:8s} " + " ".join(f"{value:.4f}" for value in retained[policy]))

figure, (heights, energies, drift) = plt.subplots(1, 3, figsize=(15, 4.3))

for policy, colour in [("locate", "crimson"), ("step", "darkorange"),
                       ("off", "seagreen")]:
    heights.plot(runs[policy].time, runs[policy]["h"], color=colour,
                 linewidth=1.2, label=f'events="{policy}"')
heights.axhline(0, color="black", linewidth=0.8)
heights.set_ylim(-0.5, 1.15)
heights.set_xlabel("time [s]")
heights.set_ylabel("height h [m]")
heights.set_title("Without detection the floor is not there")
heights.legend(fontsize=8)
heights.grid(alpha=0.3)

bounce_numbers = np.arange(1, len(retained["locate"]) + 1)
width = 0.38
energies.bar(bounce_numbers - width / 2, retained["locate"], width,
             color="crimson", label='events="locate"')
energies.bar(bounce_numbers + width / 2, retained["step"], width,
             color="darkorange", label='events="step"')
energies.axhline(RESTITUTION ** 2, color="black", linewidth=1.1,
                 label=f"what the model says: e^2 = {RESTITUTION ** 2:.2f}")
energies.set_ylim(0.60, 0.66)
energies.set_xlabel("bounce number")
energies.set_ylabel("energy kept at the bounce")
energies.set_title("A late bounce does not obey the model")
energies.legend(fontsize=8)
energies.grid(alpha=0.3, axis="y")

numbers = [row[0] for row in rows]
drift.semilogy(numbers, [abs(row[2] - exact[row[0] - 1]) + 1e-16 for row in rows],
               "o-", color="crimson", label='events="locate"')
drift.semilogy(numbers, [abs(row[3] - exact[row[0] - 1]) for row in rows],
               "s--", color="darkorange", label='events="step"')
drift.axhline(STEP, color="grey", linestyle=":", linewidth=0.9,
              label=f"one step ({STEP:g} s)")
drift.set_xlabel("bounce number")
drift.set_ylabel("error in the bounce time [s]")
drift.set_title("Locating is exact; detecting is late, every time")
drift.legend(fontsize=8)
drift.grid(alpha=0.3, which="both")
figure.tight_layout()
figure.savefig(FIGURES / "zero_crossing_cost.png", dpi=150)

page.add_text("""
    The consequences are not confined to the instant of the bounce. Because the
    ball is below the floor when the late bounce is applied, it leaves with a
    velocity it should never have had, and each bounce starts from a state that
    is already slightly wrong. The located run loses exactly the factor e^2 of
    energy at every bounce, which is what the model says; the late one loses a
    different amount each time, depending on where the step boundary happened
    to fall. With detection switched off there is no floor at all.
    """)
page.add_figure(figure, "Left: three seconds of the same model. Middle: "
                        "specific energy, which should be constant between "
                        "bounces and drop by e^2 at each. Right: the error in "
                        "each bounce time, bounce by bounce.")

# ---------------------------------------------------------------------------
# 3. A smaller step helps, and does not fix it
# ---------------------------------------------------------------------------
print(f"\n\nMaking the step smaller shrinks the error but never removes it.\n"
      f"The step sizes below are deliberately not multiples of each other: how\n"
      f"late the bounce actually is depends on where the step boundary happens\n"
      f"to fall, and only the *bound* is proportional to the step.\n")
print(f"  {'step [s]':>10}{'deepest h [m]':>16}{'first bounce late by [s]':>26}")
sizes = [0.02, 0.0126, 0.008, 0.005, 0.0031, 0.002, 0.00126, 0.0008]
penetration, lateness = [], []
for size in sizes:
    result = tinysim.simulate(ball, stop=1.0, method="rk4", step=size, events="step")
    penetration.append(abs(result["h"].min()))
    lateness.append(result.events[0].time - exact[0])
    print(f"  {size:10.4f}{result['h'].min():16.3e}{lateness[-1]:26.3e}")

located_once = tinysim.simulate(ball, stop=1.0, method="rk4", step=0.02,
                                events="locate")
print(f"\n  events='locate' with the coarsest step of all ({0.02:g} s): "
      f"bounce at {located_once.events[0].time:.6f}, "
      f"error {located_once.events[0].time - exact[0]:.1e}")

figure, axis = plt.subplots(figsize=(7.5, 4.8))
axis.loglog(sizes, penetration, "o", color="darkorange", markersize=6,
            label='events="step": how far the ball sinks')
axis.loglog(sizes, [max(l, 1e-16) for l in lateness], "s", color="chocolate",
            markersize=6, label='events="step": how late the bounce is')
axis.loglog(sizes, sizes, ":", color="chocolate", linewidth=1,
            label="one step: the bound on the lateness")
axis.loglog(sizes, [IMPACT_SPEED * size for size in sizes], ":",
            color="darkorange", linewidth=1,
            label="v * step: the bound on the penetration")
axis.axhline(max(abs(located_once.events[0].time - exact[0]), 1e-16),
             color="crimson", linewidth=1.4,
             label='events="locate", 20 ms step: error at machine precision')
axis.set_xlabel("step size [s]")
axis.set_ylabel("error")
axis.set_title("A smaller step is not a substitute for looking properly")
axis.legend(fontsize=8)
axis.grid(alpha=0.3, which="both")
figure.tight_layout()
figure.savefig(FIGURES / "zero_crossing_step_size.png", dpi=150)

page.add_text("""
    The obvious answer - take smaller steps - does shrink the error, and it
    costs proportionally more work everywhere in the simulation, including
    where nothing is happening. Notice also that only the bound shrinks
    smoothly: how late the bounce actually is depends on where the step
    boundary happens to fall relative to the crossing, so two nearby step sizes
    can give quite different answers. Locating the crossing instead gives the
    exact instant from the coarsest step in the comparison. That is the
    argument for zero-crossing detection in one picture: it buys an accuracy
    that no affordable step size buys, and it buys it predictably.
    """)
page.add_figure(figure, "How far the ball sinks and how late it bounces, "
                        "against step size, with the located result for "
                        "comparison.")
for policy in ("locate", "step", "off"):
    page.add_result(runs[policy], ["h", "v"],
                    title=f'The simulation - events="{policy}"')
page.add_model(ball)

page.finish()
