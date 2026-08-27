"""
Fixed-step integrators, written out in full.

For everything else TinySim leans on SciPy, because writing an integrator is
not what a modeling language is about.  These three are here for the opposite
reason: they are *short enough to read*, so that a student can see what a step
actually is, and then compare the answer against a variable-step method that
chooses its own steps and a root finder that locates events exactly.

Each stepper takes the derivative function, the current time and state, and a
step size, and returns the state one step later.  Nothing more.

    x(t + h) = x(t) + h * f(t, x)                          explicit Euler
                                                           local error O(h^2)

The three differ only in how many times they evaluate `f` inside the step, and
that is exactly what buys the accuracy: Euler once, Heun twice, RK4 four times.
Halving the step size divides the error at the end of the simulation by about
2 for Euler, 4 for Heun and 16 for RK4 -- an experiment worth running, and
`experiments/07_solvers.py` runs it.

None of them adapts. A fixed step that is small enough for the fastest part of
a model is wasted everywhere else, and a step that is too large is silently
wrong; that tension is what variable-step methods exist to resolve.
"""

from typing import Callable

import numpy as np


def euler(derivatives: Callable, t: float, x: np.ndarray, h: float) -> np.ndarray:
    """
    Explicit Euler: follow the slope at the start of the step.

    The simplest integrator there is, and the one whose error is easiest to
    see: it always cuts the corner of a curving trajectory.
    """
    return x + h * derivatives(t, x)


def heun(derivatives: Callable, t: float, x: np.ndarray, h: float) -> np.ndarray:
    """
    Heun's method: take an Euler step, then average the two slopes.

    Also called the explicit trapezoidal rule, or RK2.
    """
    slope_start = derivatives(t, x)
    slope_end = derivatives(t + h, x + h * slope_start)
    return x + h * (slope_start + slope_end) / 2.0


def runge_kutta_4(derivatives: Callable, t: float, x: np.ndarray,
                  h: float) -> np.ndarray:
    """
    The classical fourth-order Runge-Kutta method.

    Four slopes: one at the start, two in the middle, one at the end, combined
    with the weights 1/6, 1/3, 1/3, 1/6.  It is the default choice when a fixed
    step is wanted at all, because it is far more accurate than Euler for only
    four times the work per step.
    """
    k1 = derivatives(t, x)
    k2 = derivatives(t + h / 2, x + h / 2 * k1)
    k3 = derivatives(t + h / 2, x + h / 2 * k2)
    k4 = derivatives(t + h, x + h * k3)
    return x + h / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)


#: The fixed-step methods `simulate(method=...)` accepts, and their order of
#: accuracy, which is what the comparison experiment plots.
FIXED_STEP_METHODS = {
    "euler": (euler, 1),
    "heun": (heun, 2),
    "rk4": (runge_kutta_4, 4),
}
