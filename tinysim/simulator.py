"""
Stage 6 of the pipeline: *simulation*.

The generated code answers one question -- "given the time, the states and the
discrete variables, what are the derivatives and all the other variables?" --
and everything in this module is built on top of that:

* **Initialization.**  If the model has `initial equation`s, a second generated
  function solves the initialization system for the initial states.  Otherwise
  the `start` attributes are used directly.
* **Integration.**  SciPy's `solve_ivp` does the actual work.  TinySim never
  writes an integrator: the interesting part of a modeling language is the
  translation, not the Runge-Kutta coefficients.
* **Events.**  Each `when` condition is handed to `solve_ivp` as a terminal
  event with `direction=+1`, so integration stops exactly when the condition
  becomes true.  The `when` body then runs -- updating discrete variables and
  applying `reinit` -- and integration restarts from the new state.

Because the integration restarts at every event, a simulation is really a
sequence of continuous segments, which is exactly the picture of a hybrid
system that students should take away.

The default integrator is `Radau`, an implicit method that copes with the stiff
systems physical models tend to produce.  It is also written in Python, so a
model that raises an error mid-step (a nonlinear block that will not converge,
say) reports it properly; the Fortran-based `LSODA` aborts the interpreter
instead.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from scipy.integrate import solve_ivp

from .analysis import der_name
from .ast_nodes import Assign, Reinit, to_string
from .evaluator import EvaluationError, evaluate
from .flatten import FlatModel, ModelError


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
class SimulationResult:
    """Time series for every variable in the model."""
    time: np.ndarray
    values: Dict[str, np.ndarray]
    events: List[Event] = field(default_factory=list)
    model_name: str = ""
    message: str = ""

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

    # -- initial values ------------------------------------------------------

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
        states = self.compiled.analysis.states
        if self.compiled.initialization is not None:
            result = self.compiled.initialization.function(
                start_time, self.parameters, discretes, self.guess)
            return np.asarray(result["x"], dtype=float)

        vector = []
        for name in states:
            variable = self.model.variables[name]
            value = 0.0 if variable.start is None else evaluate(
                variable.start, self.parameters)
            vector.append(value)
        return np.array(vector, dtype=float)

    # -- the simulation loop -------------------------------------------------

    def simulate(self, stop: float = 1.0, start: float = 0.0,
                 points: int = 1001, method: str = "Radau",
                 rtol: float = 1e-6, atol: float = 1e-8,
                 max_events: int = 10000,
                 minimum_event_separation: float = 1e-12) -> SimulationResult:
        evaluate_model = self.compiled.code.function
        when_equations = self.model.when_equations
        discretes = self.initial_discretes()
        x = self.initial_states(discretes, start)

        times = np.linspace(start, stop, points)
        collected_time: List[float] = []
        collected_rows: List[Dict[str, float]] = []
        events: List[Event] = []
        message = ""

        def derivatives(t, x):
            return evaluate_model(t, x, self.parameters, discretes, self.guess)["der"]

        def make_event_function(index):
            def event_function(t, x):
                return evaluate_model(t, x, self.parameters, discretes,
                                      self.guess)["events"][index]
            event_function.terminal = True
            event_function.direction = +1.0     # only when the condition becomes true
            return event_function

        event_functions = [make_event_function(i) for i in range(len(when_equations))]

        current_time = start
        while current_time < stop:
            wanted = times[(times >= current_time) & (times <= stop)]
            if wanted.size == 0 or wanted[0] > current_time:
                wanted = np.concatenate(([current_time], wanted))

            solution = solve_ivp(
                derivatives, (current_time, stop), x, method=method,
                t_eval=wanted, events=event_functions or None,
                rtol=rtol, atol=atol, dense_output=False)
            if not solution.success:
                message = f"the integrator stopped: {solution.message}"
                break

            self._collect(solution, discretes, collected_time, collected_rows)

            fired = self._which_event(solution)
            if fired is None:
                current_time = stop
                break

            index, event_time, event_state = fired
            if (events and event_time - events[-1].time < minimum_event_separation
                    and abs(event_time - events[-1].time) < minimum_event_separation):
                message = (f"events are arriving infinitely often around "
                           f"t = {event_time:.6g} (Zeno behaviour); stopping here")
                break
            if len(events) >= max_events:
                message = f"more than {max_events} events; stopping at t = {event_time:.6g}"
                break

            event = self._apply_event(index, event_time, event_state, discretes)
            events.append(event)
            # A `reinit` changes the state vector; discrete updates are already
            # in `discretes`.
            x = event_state
            current_time = event_time
            # Record the post-event point, so plots show the jump.
            row = evaluate_model(current_time, x, self.parameters, discretes,
                                 self.guess)["variables"]
            collected_time.append(current_time)
            collected_rows.append(dict(row, **discretes))

        return self._assemble(collected_time, collected_rows, events, message)

    # -- helpers -------------------------------------------------------------

    def _collect(self, solution, discretes, collected_time, collected_rows):
        evaluate_model = self.compiled.code.function
        for position, t in enumerate(solution.t):
            state = solution.y[:, position]
            row = evaluate_model(t, state, self.parameters, discretes,
                                 self.guess)["variables"]
            collected_time.append(float(t))
            collected_rows.append(dict(row, **discretes))

    @staticmethod
    def _which_event(solution):
        """Which `when` clause stopped the integration, and where."""
        if solution.status != 1:
            return None
        for index, event_times in enumerate(solution.t_events):
            if event_times.size:
                return (index, float(event_times[-1]),
                        np.asarray(solution.y_events[index][-1], dtype=float))
        return None

    def _apply_event(self, index, event_time, state, discretes) -> Event:
        """Run the body of the `when` clause that fired."""
        when_equation = self.model.when_equations[index]
        evaluate_model = self.compiled.code.function
        variables = evaluate_model(event_time, state, self.parameters, discretes,
                                   self.guess)["variables"]

        # The environment a `when` body is evaluated in: everything the model
        # knows right now, plus the pre-event values that `pre(x)` refers to.
        environment = dict(self.parameters)
        environment.update(variables)
        environment.update(discretes)
        environment["time"] = event_time
        for name, value in discretes.items():
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
                before = discretes.get(statement.name, float("nan"))
                discretes[statement.name] = new_value
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

    def _assemble(self, collected_time, collected_rows, events, message):
        if not collected_time:                                  # pragma: no cover
            raise ModelError("the simulation produced no output")
        time = np.array(collected_time)
        names = sorted(collected_rows[0])
        values = {name: np.array([row.get(name, np.nan) for row in collected_rows])
                  for name in names}
        return SimulationResult(time=time, values=values, events=events,
                                model_name=self.model.name, message=message)
