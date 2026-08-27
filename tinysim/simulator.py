"""
Stage 6 of the pipeline: *simulation*.

The generated code answers one question -- "given the time, the states and the
discrete variables, what are the derivatives and all the other variables?" --
and everything in this module is built on top of that:

* **Initialization.**  If the model has `initial equation`s, a second generated
  function solves the initialization system for the initial states.  Otherwise
  the `start` attributes are used directly.
* **Integration.**  Either SciPy's `solve_ivp`, which chooses its own step
  sizes, or one of the fixed-step methods in `integrators.py`.
* **Events.**  A `when` condition becomes a *margin* that is positive exactly
  while the condition holds, and an event is the instant that margin crosses
  zero upwards.  How hard the simulator works to find that instant is a choice
  -- see below.

Because integration restarts at every event, a simulation is really a sequence
of continuous segments, which is exactly the picture of a hybrid system that
students should take away.

Two choices, and what they cost
-------------------------------

**Step size.**  `method="Radau"` (the default) and the other SciPy methods vary
their step size to meet a tolerance; `method="euler" | "heun" | "rk4"` take the
fixed step given by `step=`.  A fixed step is predictable and easy to reason
about -- it is what runs inside a real-time controller -- and it is either
wasteful or wrong everywhere the model's own time scale changes.

**Event handling**, chosen with `events=`:

* `"locate"` (default) -- find the crossing instant itself, by root finding in
  the variable-step case and by bisecting the step in the fixed-step case, and
  restart the integration exactly there.
* `"step"` -- notice the crossing only at the end of the step it happened in,
  and act there. Cheap, and always late: the ball is already below the floor
  when the bounce is applied, so it bounces back with the wrong velocity and
  the simulation slowly gains or loses energy.
* `"off"` -- do not look for events at all. The `when` clauses never fire, and
  the ball falls straight through the floor.

`experiments/07_solvers.py` runs all of them side by side. The default is
`method="Radau", events="locate"`, which is what a Modelica tool does.

The default integrator is implicit, and written in Python: a model that raises
an error mid-step (a nonlinear block that will not converge, say) then reports
it properly, where the Fortran-based `LSODA` aborts the interpreter instead.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from scipy.integrate import solve_ivp

from .ast_nodes import Assign, Reinit, to_string
from .evaluator import EvaluationError, evaluate
from .flatten import FlatModel, ModelError
from .integrators import FIXED_STEP_METHODS

#: How hard the simulator works to find the instant an event happens.
EVENT_POLICIES = ("locate", "step", "off")


@dataclass
class Event:
    """One event occurrence, kept for reporting and plotting."""
    time: float
    condition: str
    changes: Dict[str, tuple] = field(default_factory=dict)   # name -> (before, after)

    def __str__(self) -> str:
        changed = ", ".join(f"{name}: {before:g} -> {after:g}"
                            for name, (before, after) in self.changes.items())
        return f"t = {self.time:.6g}  when {self.condition}   {changed}"


@dataclass
class Solver:
    """How a result was produced -- worth carrying around, and reporting."""
    method: str = "Radau"
    step: Optional[float] = None
    events: str = "locate"
    rtol: float = 1e-6
    atol: float = 1e-8

    @property
    def fixed_step(self) -> bool:
        return self.method in FIXED_STEP_METHODS

    def __str__(self) -> str:
        how = (f"fixed step {self.step:g}" if self.fixed_step
               else f"variable step, rtol {self.rtol:g}, atol {self.atol:g}")
        events = {"locate": "events located exactly",
                  "step": "events detected at step ends",
                  "off": "events ignored"}[self.events]
        return f"{self.method}, {how}, {events}"


@dataclass
class SimulationResult:
    """Time series for every variable in the model."""
    time: np.ndarray
    values: Dict[str, np.ndarray]
    events: List[Event] = field(default_factory=list)
    model_name: str = ""
    message: str = ""
    solver: Solver = field(default_factory=Solver)

    def __getitem__(self, name: str) -> np.ndarray:
        if name not in self.values:
            raise KeyError(
                f"{name!r} is not a variable of this model; try one of: "
                f"{', '.join(sorted(self.values)[:12])} ...")
        return self.values[name]

    @property
    def names(self) -> List[str]:
        return sorted(self.values)

    def final(self) -> Dict[str, float]:
        """The value of every variable at the end of the simulation."""
        return {name: series[-1] for name, series in self.values.items()}


class Simulator:
    """Runs a compiled model."""

    def __init__(self, compiled):
        self.compiled = compiled                 # a CompiledModel, see __init__.py
        self.model: FlatModel = compiled.model
        self.parameters = self.model.parameter_values
        self.guess: Dict = {}
        # Filled in for the duration of one run.
        self.discretes: Dict[str, float] = {}
        self.times: List[float] = []
        self.rows: List[Dict[str, float]] = []
        self.events: List[Event] = []

    # =========================================================================
    # Initial values
    # =========================================================================

    def initial_discretes(self) -> Dict[str, float]:
        values = {}
        for name in self.model.discrete_variables():
            variable = self.model.variables[name]
            if variable.start is None:
                raise ModelError(
                    f"discrete variable {name!r} needs a start value, because "
                    f"nothing computes it before its first event")
            values[name] = evaluate(variable.start, self.parameters)
        return values

    def initial_states(self, discretes, start_time: float):
        """
        Work out the initial state vector.

        With `initial equation`s present this solves the initialization system,
        which is a *different* system of equations from the one solved during
        simulation: the states are unknowns there too.
        """
        if self.compiled.initialization is not None:
            result = self.compiled.initialization.function(
                start_time, self.parameters, discretes, self.guess)
            return np.asarray(result["x"], dtype=float)

        vector = []
        for name in self.compiled.analysis.states:
            variable = self.model.variables[name]
            value = 0.0 if variable.start is None else evaluate(
                variable.start, self.parameters)
            vector.append(value)
        return np.array(vector, dtype=float)

    # =========================================================================
    # The model, as the integrator sees it
    # =========================================================================

    def evaluate_at(self, t, state) -> dict:
        """Everything the model knows at one point: derivatives, variables, margins."""
        return self.compiled.code.function(t, state, self.parameters,
                                           self.discretes, self.guess)

    def derivatives(self, t, state):
        return self.evaluate_at(t, state)["der"]

    def margins(self, t, state) -> List[float]:
        """One number per `when` clause, positive while its condition holds."""
        return list(self.evaluate_at(t, state)["events"])

    def record(self, t, state):
        """Keep one output point."""
        row = self.evaluate_at(t, state)["variables"]
        self.times.append(float(t))
        self.rows.append(dict(row, **self.discretes))

    # =========================================================================
    # The run
    # =========================================================================

    def simulate(self, stop: float = 1.0, start: float = 0.0,
                 points: int = 1001, method: str = "Radau",
                 step: Optional[float] = None, events: str = "locate",
                 rtol: float = 1e-6, atol: float = 1e-8,
                 max_events: int = 10000,
                 event_tolerance: float = 1e-8,
                 minimum_event_separation: float = 1e-9) -> SimulationResult:
        """
        Integrate the model from `start` to `stop`.

        `method` is a SciPy method (`Radau`, `BDF`, `RK45`, ...) or one of the
        fixed-step methods `euler`, `heun`, `rk4`, which need `step`.
        `events` is `"locate"`, `"step"` or `"off"`; see the module docstring.
        """
        solver = self._check_options(method, step, events, rtol, atol)

        self.discretes = self.initial_discretes()
        self.times, self.rows, self.events = [], [], []
        state = self.initial_states(self.discretes, start)

        settings = dict(stop=stop, start=start, points=points,
                        max_events=max_events, event_tolerance=event_tolerance,
                        minimum_event_separation=minimum_event_separation)
        if solver.fixed_step:
            message = self._run_fixed_step(state, solver, **settings)
        else:
            message = self._run_variable_step(state, solver, **settings)

        return self._assemble(message, solver)

    @staticmethod
    def _check_options(method, step, events, rtol, atol) -> Solver:
        if events not in EVENT_POLICIES:
            raise ValueError(
                f"events must be one of {', '.join(EVENT_POLICIES)}, not {events!r}")
        fixed = method in FIXED_STEP_METHODS
        if fixed and (step is None or step <= 0):
            raise ValueError(
                f"the fixed-step method {method!r} needs a step size: "
                f"simulate(..., method={method!r}, step=0.001)")
        if not fixed and step is not None:
            raise ValueError(
                f"step= applies only to the fixed-step methods "
                f"({', '.join(FIXED_STEP_METHODS)}); {method!r} chooses its own "
                f"step size, so give it rtol= and atol= instead")
        return Solver(method=method, step=step, events=events, rtol=rtol, atol=atol)

    # -- variable step: SciPy chooses the steps -------------------------------

    def _run_variable_step(self, state, solver: Solver, stop, start, points,
                           max_events, event_tolerance,
                           minimum_event_separation) -> str:
        """
        Integrate with `solve_ivp`, restarting at every event.

        With `events="locate"` each `when` condition is handed to SciPy as a
        terminal event, so the integrator's own root finder stops it at the
        crossing.  Two details make that behave the way `when` is supposed to:

        First, a `when` fires when its condition *becomes* true, so a condition
        that already holds at the start of a segment is watched for becoming
        false instead (`direction = -1`); that re-arms it for next time.

        Second, at the instant an event is handled its condition sits exactly on
        the boundary, and integration restarts from there.  A crossing function
        that is exactly zero at the first step looks like a crossing, so the same
        event would be found again, and again, forever.  The cure is a small
        hysteresis band, `event_tolerance`: the condition must be exceeded by
        that much before it counts as becoming true, and fall that much below
        before it counts as becoming false.  Real simulators do the same thing,
        which is why event times carry a tolerance just as the states do.
        """
        grid = np.linspace(start, stop, points)
        current = start
        holds = [margin > 0 for margin in self.margins(current, state)]

        while current < stop:
            wanted = grid[(grid >= current) & (grid <= stop)]
            if wanted.size == 0 or wanted[0] > current:
                wanted = np.concatenate(([current], wanted))

            functions = (self._scipy_events(holds, event_tolerance)
                         if solver.events == "locate" else None)
            solution = solve_ivp(
                self.derivatives, (current, stop), state, method=solver.method,
                t_eval=wanted, events=functions, rtol=solver.rtol, atol=solver.atol)
            if not solution.success:
                return f"the integrator stopped: {solution.message}"

            if solver.events == "locate":
                for position, t in enumerate(solution.t):
                    self.record(t, solution.y[:, position])
                fired = self._which_scipy_event(solution)
                if fired is None:
                    return ""
                index, event_time, state = fired
            else:
                # No root finding: walk the output points and notice a crossing
                # only once it has already happened.
                fired = self._scan_output_points(solution, holds, solver,
                                                 event_tolerance)
                if fired is None:
                    return ""
                index, event_time, state = fired

            stop_message = self._event_is_too_much(event_time, max_events,
                                                   minimum_event_separation)
            if stop_message:
                return stop_message

            if solver.events == "locate" and holds[index]:
                # The condition became false again: nothing to run, the point of
                # stopping was to re-arm the `when`.
                current = event_time
                holds = [margin > 0 for margin in self.margins(current, state)]
                continue

            self.events.append(self._apply_event(index, event_time, state))
            self.record(event_time, state)          # so the jump shows in the plot
            current = event_time
            holds = [margin > 0 for margin in self.margins(current, state)]
        return ""

    def _scipy_events(self, holds, event_tolerance):
        """Terminal events for `solve_ivp`, one per `when` clause."""
        def make(index, holds_now):
            offset = event_tolerance if holds_now else -event_tolerance

            def event_function(t, state):
                return self.evaluate_at(t, state)["events"][index] + offset
            event_function.terminal = True
            event_function.direction = -1.0 if holds_now else +1.0
            return event_function
        return [make(index, held) for index, held in enumerate(holds)] or None

    @staticmethod
    def _which_scipy_event(solution):
        """Which `when` clause stopped the integration, and where."""
        if solution.status != 1:
            return None
        for index, event_times in enumerate(solution.t_events):
            if event_times.size:
                return (index, float(event_times[-1]),
                        np.asarray(solution.y_events[index][-1], dtype=float))
        return None

    def _scan_output_points(self, solution, holds, solver, event_tolerance):
        """
        Event detection without a root finder.

        The integration ran to the end; this walks the output points and stops
        at the first one where a condition has become true.  The event is then
        applied *there* -- later than it really happened, by up to one output
        interval.  That lateness is the whole point of the comparison.
        """
        armed = list(holds)
        for position, t in enumerate(solution.t):
            state = solution.y[:, position]
            self.record(t, state)
            if solver.events == "off":
                continue
            for index, margin in enumerate(self.margins(t, state)):
                if armed[index]:
                    if margin < -event_tolerance:
                        armed[index] = False        # re-arm for the next crossing
                elif margin > event_tolerance:
                    return index, float(t), np.array(state, dtype=float)
        return None

    # -- fixed step: we choose the steps --------------------------------------

    def _run_fixed_step(self, state, solver: Solver, stop, start, points,
                        max_events, event_tolerance,
                        minimum_event_separation) -> str:
        """
        Integrate with a fixed step, recording every step.

        `points` is ignored here: the step size decides the output, which is
        the honest picture -- the plot then shows exactly the points the method
        actually computed.

        With `events="locate"` a crossing inside a step is found by *bisecting
        the step itself*: retake the step at half the length and look again.
        That is the crudest possible root finder, and it is enough to show what
        a variable-step solver's event location is doing.
        """
        stepper, _order = FIXED_STEP_METHODS[solver.method]
        step = solver.step
        armed = [margin <= 0 for margin in self.margins(start, state)]

        current = start
        self.record(current, state)
        while current < stop - 1e-12:
            length = min(step, stop - current)
            next_state = stepper(self.derivatives, current, state, length)
            next_time = current + length

            fired = None
            if solver.events != "off":
                fired = self._crossing_in_step(stepper, current, state, length,
                                               armed, event_tolerance,
                                               locate=solver.events == "locate")

            if fired is None:
                for index, margin in enumerate(self.margins(next_time, next_state)):
                    if margin < -event_tolerance:
                        armed[index] = True         # re-arm for the next crossing
                state, current = next_state, next_time
                self.record(current, state)
                continue

            index, event_time, event_state = fired
            self.record(event_time, event_state)    # the state as the event begins
            stop_message = self._event_is_too_much(event_time, max_events,
                                                   minimum_event_separation)
            if stop_message:
                return stop_message

            self.events.append(self._apply_event(index, event_time, event_state))
            armed[index] = False
            state, current = event_state, event_time
            self.record(current, state)
        return ""

    def _crossing_in_step(self, stepper, t, state, length, armed,
                          event_tolerance, locate: bool):
        """
        Did any armed condition become true during this step?

        Returns the earliest crossing as (index, time, state), or None.  With
        `locate` the time is found by bisection inside the step; without it, the
        end of the step is used, which is where a controller or a naive
        simulator would notice.
        """
        end_state = stepper(self.derivatives, t, state, length)
        end_margins = self.margins(t + length, end_state)
        crossed = [index for index, margin in enumerate(end_margins)
                   if armed[index] and margin > event_tolerance]
        if not crossed:
            return None

        if not locate:
            index = crossed[0]
            return index, t + length, end_state

        earliest = None
        for index in crossed:
            low, high = 0.0, length          # margin <= 0 at low, > 0 at high
            for _ in range(60):
                middle = (low + high) / 2
                trial = stepper(self.derivatives, t, state, middle)
                if self.margins(t + middle, trial)[index] > 0:
                    high = middle
                else:
                    low = middle
            crossing_state = stepper(self.derivatives, t, state, high)
            if earliest is None or t + high < earliest[1]:
                earliest = (index, t + high, crossing_state)
        return earliest

    # =========================================================================
    # Events, and stopping
    # =========================================================================

    def _event_is_too_much(self, event_time, max_events,
                           minimum_event_separation) -> str:
        """Give up gracefully on chattering and on Zeno behaviour."""
        if len(self.events) >= max_events:
            return (f"stopping at t = {event_time:.6g}: more than {max_events} "
                    f"events, which usually means the model is chattering or "
                    f"shows Zeno behaviour")
        if (self.events
                and event_time - self.events[-1].time < minimum_event_separation):
            return (f"events are arriving infinitely often around "
                    f"t = {event_time:.6g} (Zeno behaviour); stopping here")
        return ""

    def _apply_event(self, index, event_time, state) -> Event:
        """
        Run the body of the `when` clause that fired.

        `state` is modified in place by `reinit`, and `self.discretes` by an
        assignment -- which is exactly the difference between the two.
        """
        when_equation = self.model.when_equations[index]
        variables = self.evaluate_at(event_time, state)["variables"]

        # The environment a `when` body is evaluated in: everything the model
        # knows right now, plus the pre-event values that `pre(x)` refers to.
        environment = dict(self.parameters)
        environment.update(variables)
        environment.update(self.discretes)
        environment["time"] = event_time
        for name, value in self.discretes.items():
            environment[f"pre({name})"] = value
        for name in self.model.continuous_variables():
            if name in variables:
                environment[f"pre({name})"] = variables[name]

        event = Event(time=event_time, condition=to_string(when_equation.condition))
        states = self.compiled.analysis.states
        for statement in when_equation.body:
            try:
                new_value = evaluate(statement.value, environment)
            except EvaluationError as error:
                raise ModelError(
                    f"cannot evaluate the body of the 'when' on line "
                    f"{when_equation.line}: {error}")
            if isinstance(statement, Assign):
                before = self.discretes.get(statement.name, float("nan"))
                self.discretes[statement.name] = new_value
                event.changes[statement.name] = (before, new_value)
            elif isinstance(statement, Reinit):
                if statement.name not in states:
                    raise ModelError(
                        f"reinit() can only be applied to a state; "
                        f"{statement.name!r} is not one")
                position = states.index(statement.name)
                event.changes[statement.name] = (float(state[position]), new_value)
                state[position] = new_value
        return event

    # =========================================================================
    # The result
    # =========================================================================

    def _assemble(self, message, solver) -> SimulationResult:
        if not self.times:                                      # pragma: no cover
            raise ModelError("the simulation produced no output")
        time = np.array(self.times)
        names = sorted(self.rows[0])
        values = {name: np.array([row.get(name, np.nan) for row in self.rows])
                  for name in names}
        return SimulationResult(time=time, values=values, events=list(self.events),
                                model_name=self.model.name, message=message,
                                solver=solver)
