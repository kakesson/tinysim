# Time-discrete controllers in TinySim -- a plan

> **Status: plan.** Nothing is implemented yet. Three decisions marked
> **[OPEN]** in section 8 need answering; everything else follows from them.

The goal is the one the investigation sets out: a controller that is *actually
a digital algorithm* -- sampled, held, running on a state it carries from one
tick to the next -- rather than a continuous approximation of one. And the
constraint is equally clear: nothing that complicates the simulation without
paying for itself.

---

## 0. What was measured before planning anything

**A sampled PI controller already runs in TinySim as it stands.** The tick is
kept in a discrete variable, and the `when` re-arms itself:

```modelica
  when time > nextTick then
    integral = pre(integral) + Ki * Ts * (reference - y);
    u = Kp * (reference - y) + integral;
    nextTick = pre(nextTick) + Ts;
  end;
```

Simulated for 2 s with `Ts = 0.05`: **40 ticks, exactly**; `u` takes 41
distinct values, one per sample, so it is genuinely held between them; and the
loop settles on the reference (`y = 1.0029` at t = 2). Zero-order hold costs
nothing, because a discrete variable *is* held by construction.

**But the assignments are simultaneous, and that is a trap.** At the first
tick the error is 1, so the new integral is `Ki·Ts·e = 0.25`, and the code as
written says `u = Kp·e + integral`. It produced

```
     integral     0.000000 ->   0.250000
     u            0.000000 ->   2.000000      <- 2 + 0, not 2 + 0.25
```

Every right-hand side sees the *pre-event* values, so `u` used the old
integral. The controller still works -- it has one extra sample of delay in the
I-term -- but it does not compute what its author wrote, and anyone
transcribing embedded code would get a silently different controller. This is
the investigation's §7 in the flesh.

**Records already work, under another name.** A component that declares
variables and no equations flattens into dotted fields, is assignable inside a
`when` body, and simulates -- which is exactly what a record is:

```modelica
model ControllerState                    // a record in all but name
  discrete Real integral(start = 0);
  discrete Real previousError(start = 0);
end ControllerState;
```

gave `s.integral`, `s.previousError`, 20 ticks and a settled loop. An earlier
draft of this plan excluded records as expensive. That was wrong: instantiation
already does the work, and the correction is recorded here rather than quietly
fixed.

**Guarded transitions cannot be written at all.** A state machine needs *in
this state, and this guard*, and a `when` refuses it:

```
ModelError: line 8: a 'when' condition must be a simple comparison
```

That single restriction is what shapes the design in section 2F.

**The target technology already has what is needed.** In ModelingToolkit,
`discrete_events = [0.05 => [...]]` is a *periodic* event: the tick is
scheduled, never hunted for by a root finder. The same PI controller in MTK
gives 40 held values of `u` and the same settling behaviour.

So the gap is smaller, and different, from what it looks like on paper.

## 1. The three layers, mapped

The investigation's architecture carries over unchanged; only the mechanism
differs.

| layer | in the investigation | in TinySim |
| --- | --- | --- |
| physical dynamics | declarative Modelica DAEs | unchanged: equations and `connect` |
| sampling and execution | clocked partitions, `Clock`/`sample`/`hold` | a `when` triggered by `sample(t0, Ts)`; the hold is implicit |
| controller software | an imperative function `(s⁺, u) = F(s, y, r, Δt)` | the body of that `when`, written sequentially |

## 2. What is actually missing

Four additions, in order of necessity.

### A. `sample(t0, Ts)` as a `when` condition -- **essential**

```modelica
  when sample(0, Ts) then
    ...
  end;
```

This is the only thing the language genuinely cannot say today. It replaces the
`nextTick` bookkeeping, and -- more importantly -- it tells the simulator that
the instant is *known in advance*. A sampled event is scheduled, not located:
no crossing function, no root finding, no hysteresis, and no accumulated error
in the tick times. It is both simpler and more accurate than the workaround.

### B. Sequential assignment in `when` bodies -- **essential**

Write `:=` and mean it: each statement sees the values assigned by the ones
before it, and `pre(x)` is the value before the event began.

```modelica
  when sample(0, Ts) then
    e := reference - y;                       // reads the plant, sampled here
    integral := pre(integral) + Ki * Ts * e;
    u := Kp * e + integral;                   // the *new* integral
  end;
```

That is what makes a `when` body a piece of controller software rather than a
set of simultaneous equations, and it is what the measurement above shows is
missing. It is a breaking change to two existing examples, both one-liners.

### C. `if` as a *statement* inside a `when` body -- **needed in practice**

Saturation, anti-windup and enable logic are the reason a controller is a
program and not a formula:

```modelica
    if u > uMax then
      u := uMax;
      integral := integral - Kaw * (uUnsat - uMax);   // anti-windup
    elseif u < uMin then
      u := uMin;
    end if;
```

This costs nothing at simulation time: it runs *inside* an event that has
already been located, so it generates no crossing functions of its own -- the
investigation's §14, and the reason clocked partitions are cheap.

### D. Intermediate values -- **no new construct**

`e` above is simply a `discrete Real`. Declaring intermediates as discrete
variables costs one line, and it has a teaching advantage: they appear in the
results, so the integrator state and the unsaturated control signal can be
plotted and put under contract. A `local` or `protected` scope would be more
Modelica-like and buys nothing here.

### E. Records -- **nearly free**

```modelica
record ControllerState "what a PI controller carries between ticks"
  discrete Real integral(start = 0);
  discrete Real previousError(start = 0);
end ControllerState;

record Gains
  parameter Real Kp = 2, Ki = 5, Kd = 0;
end Gains;
```

A `record` is a class that declares variables and has no equations -- which is
what the measurement above shows already flattens correctly. So the additions
are small and mostly about intent:

* the keyword, and an error if a record contains equations;
* a record-typed declaration instantiates its fields, `s.integral` and so on --
  the existing machinery, unchanged, including modifiers such as
  `ControllerState s(integral(start = 2))`;
* two pieces of sugar: whole-record assignment in a `when` body, `s := t;`,
  and whole-record equality in an equation, `s = t;`, both expanding field by
  field in declaration order.

Records nest, because instantiation already recurses. Fields may be
`parameter`, which makes a record a parameter set. Records are gone before any
code is generated, so they cost the simulator nothing at all.

Left out: arrays of records, records inside connectors -- a connector is
already a record with flow semantics -- and record-valued expressions beyond a
plain reference.

### F. State machines -- **an `automaton`, desugared**

```modelica
automaton Supervisor sampled at 0.01
  state Off, Starting, Running, Fault;
  initial Off;
transition
  Off      -> Starting  when startCommand > 0.5  then u := uStart; end;
  Starting -> Running   when w > wTarget;
  Starting -> Fault     when timeInState > startTimeout;
  Running  -> Fault     when abs(i) > iMax;
  Fault    -> Off       when resetCommand > 0.5  then u := 0; end;
end Supervisor;
```

**Semantics, spelled out**, because this is where state machines go wrong:

* the automaton owns one discrete variable, the active state;
  `Supervisor.state == Supervisor.Running` may be used in any expression, and
  the state names are constants;
* it is **level-triggered, on a clock**. At each tick the transitions *leaving
  the active state* are tested in the order written, and the first whose guard
  holds is taken. At most one transition per tick;
* taking a transition records the entry time and runs the `then` actions --
  assignments to discrete variables, sequential, exactly the rules of a `when`
  body;
* `timeInState` is the time since the last transition, usable in guards and in
  equations, which is what makes timeouts and debouncing writable;
* per-mode dynamics are written with the `if`-expressions the language already
  has: `der(x) = if Supervisor.state == Supervisor.Running then -x + u else 0`.

**Why the clock is required.** With edge-triggered `when`s, a guard that became
true while the machine was in some *other* state would never fire when the
machine arrives -- the edge has already passed. Level-triggering at ticks
removes that entire class of bug, needs no conjunction of crossing functions
(the gap measured in section 0), and matches how supervisory logic actually
runs: in software, on a period. Modelica's state machines are clocked for the
same reason.

**What it costs the compiler:** a desugaring, and nothing else. An automaton
becomes one discrete variable for the state, one for the entry time, and a
single `when sample(t0, Ts)` whose body is a chain of `if`s -- so it needs
exactly constructs A, B and C above and no new simulation machinery.

Left out, deliberately: hierarchy and composite states, `synchronize`, reset
semantics, the immediate/delayed distinction, and history. Each is a section of
the Modelica specification and a week of work; a flat clocked machine is what a
course needs, and what fits in a language with one scalar type.

Optional later sugar, which changes no semantics: `in Running then der(x) = ...;`
blocks that expand into the `if`-expressions above, with a check that every
`in` block defines equations for the same unknowns.

## 3. What is deliberately left out, and what replaces it

The investigation recommends clocked/synchronous Modelica. TinySim takes its
*architecture* and rejects its *machinery*, because in a language this size the
machinery costs more than it returns.

| clocked Modelica | what TinySim does instead |
| --- | --- |
| `Clock(Ts)` | `sample(t0, Ts)` on the `when` |
| `sample(y, clock)` | reading a continuous variable inside a sampled `when`; the value is the left limit by construction |
| `previous(x)` | `pre(x)` |
| `hold(u)` | nothing -- a discrete variable is already held between events |
| a clocked partition | the body of the `when` |
| `subSample(clock, n)` | a second `when sample(t0, n*Ts)` |
| `interval()` | the parameter `Ts` (constant rate only) |
| clock inference | not needed: the rate is written on the `when` that uses it |

Also left out, each with its reason:

* **Functions.** The investigation puts the algorithm in a `controllerStep`
  function taking a state record. TinySim takes the record (section 2E) and
  leaves the function: the *component* is the unit of reuse -- instantiate the
  controller twice -- and the *contract* is the unit of test. Functions would
  bring parameter modes, multiple returns and a call stack into a language that
  has none of those. Records and automata are in; see 2E and 2F.
* **Clocked continuous partitions** (`der(x)` discretised automatically). The
  point of this exercise is to model the algorithm that runs, not to have the
  tool invent one.
* **Exact rational clock algebra.** Multirate falls out of writing two
  `sample(...)` rates; what is lost is the compiler's guarantee that they stay
  in lockstep, which matters for code generation and not for teaching.

## 4. What this does, and does not, cost the simulator

**Changes:** one new kind of event. Sampled events are *scheduled*: their times
are known from `t0` and `Ts`, so the simulator adds them to the stop times and
steps exactly onto them. In the Julia port this is MTK's
`discrete_events = [Ts => [...]]`, already verified to work; in the Python
oracle it is a merged schedule alongside the existing crossing functions.

**Does not change:** no new solver, no new algebraic machinery, no index
consequences, no interaction with tearing. Sampled events cannot be missed, do
not need the hysteresis band, and are unaffected by the accuracy of the state
event location.

**A teaching bonus falls out.** TinySim already contrasts three ways of
handling a *state* event (`locate`, `step`, `off`). A sampled event is a fourth
kind that needs no locating at all, and putting the two side by side is the
clearest way to explain why simulators distinguish time events from state
events.

## 5. Grammar

```ebnf
definition = connector_def | model_def | record_def | automaton_def | contract_def ;

when_eq    = "when" ( expr | sample_call ) "then" { when_stmt } "end" ";" ;
sample_call= "sample" "(" expr "," expr ")" ;

when_stmt  = comp_ref ":=" expr ";"
           | comp_ref ":=" comp_ref ";"                  (* whole record *)
           | "reinit" "(" comp_ref "," expr ")" ";"
           | if_stmt ;
if_stmt    = "if" expr "then" { when_stmt }
             { "elseif" expr "then" { when_stmt } }
             [ "else" { when_stmt } ] "end" "if" ";" ;

record_def = "record" IDENT [ STRING ] { var_decl } "end" [ IDENT ] ";" ;

automaton_def = "automaton" IDENT "sampled" "at" expr [ STRING ]
                "state" IDENT { "," IDENT } ";"
                "initial" IDENT ";"
                "transition" { transition }
                "end" [ IDENT ] ";" ;
transition = IDENT "->" IDENT "when" expr [ "then" { when_stmt } "end" ] ";" ;
```

`sample(t0, Ts)` is allowed only as the condition of a `when`, which keeps it
out of expressions where it would have no meaning. Inside an automaton,
`timeInState` refers to that automaton.

## 6. What it buys the course

A new example, `examples/sampled_control.tiny`: the DC motor with a digital PI
speed controller, and a new experiment that puts three runs beside each other:

* continuous control -- the reference behaviour;
* sampled control at `Ts = 5 ms` -- almost the same, and the plot shows the
  staircase in `u`;
* sampled control at `Ts = 100 ms` -- **unstable**, from the same controller
  gains and the same plant.

That last one is the lesson: nothing about the model changed except how often
the algorithm runs. And it is exactly the kind of claim a contract states:

```modelica
contract TracksTheReference for SampledMotor
assume    always abs(reference) <= 200;
guarantee always abs(u) <= uMax;                  // the software respects its limits
          after 1 load.w settles to reference within 5;
end TracksTheReference;
```

which is satisfied at 5 ms and violated at 100 ms, with a margin that says by
how much. The anti-windup branch gives a second contract worth writing: the
integrator never grows while the output is saturated.

The automaton pays off twice. As an example, a supervisor over the same motor
-- off, starting, running, fault -- shows mode logic separated from the
numerical algorithm, which is the investigation's §16. And as a *subject* for
contracts, because modes are exactly what temporal logic is good at:

```modelica
guarantee
  never Supervisor.state == Supervisor.Fault;
  whenever Supervisor.state == Supervisor.Starting
    then Supervisor.state == Supervisor.Running within [0, 5];
  always Supervisor.timeInState <= 60;
```

The record pays off in the reports rather than in the simulation: a controller
whose state is a record produces `s.integral` and `s.previousError` as
plottable, contractible variables, so the internal state of the software is as
visible as the physics.

## 7. Where this fits in the migration

The language change belongs in the specification now; the implementation
belongs in the Julia port, where it is cheapest:

| phase | work |
| --- | --- |
| now | `docs/language.md`: `sample`, `:=`, `if` statements, and the section on what a `when` body means |
| Julia phase 1 | parser: the three constructs |
| Julia phase 2 | translation: `when sample(t0, Ts)` becomes `discrete_events = [Ts => ...]`, statements become an ordered affect, `pre` becomes `Pre` |
| Julia phase 4 | the schedule, and the fourth event kind in the reports |
| Julia phase 7 | the new example, the experiment, the contracts |

The Python oracle gets the same three constructs only if the golden files need
to cover the new example -- which they should, so it is a small, deliberate
exception to the freeze, taken once.

**A door left open.** MTK implements full clocked partitions natively
(`Clock`, `Sample`, `Hold`, `ShiftIndex`). If the course later wants true
synchronous semantics -- multirate with checked rate relations, or generated
embedded code -- the solver work is already done, and only clock inference in
our front end would be missing. Choosing the classic mechanism now does not
close that door.

## 8. **[OPEN]** Decisions

0. **Records and automata are in**, on the strength of the measurements in
   section 0: records cost a keyword and two sugars, automata cost a
   desugaring. An earlier draft excluded both; only the exclusion of functions
   survives.
1. **`:=` and sequential bodies.** Recommended: yes -- it is what makes a
   controller mean what it says, and the measurement in section 0 shows the
   alternative is a silent trap. Cost: `on = 0` becomes `on := 0` in two
   examples, and the specification gains a paragraph.
2. **Does `events = "off"` also switch off sampled events?** Recommended: yes,
   and say so in the report -- "the controller never ran" is a legible outcome,
   and the alternative is a policy that means different things for different
   event kinds.
3. **`if` statements now, or after the port?** Recommended: now, in the
   specification, and implemented in Julia phase 1 with the rest -- saturation
   is not an advanced feature, it is the first thing anyone writes.
4. **Must an automaton always carry a rate?** Recommended: yes. A
   continuous-time automaton would need edge semantics, and the missed
   transition described in 2F is a bug that is very hard to see in a plot. If a
   supervisor really must react instantly, the rate can be made small.
5. **`in <State> then <equations>` blocks?** Recommended: later. The
   `if`-expression form works today and needs no balance analysis; the sugar
   can be added without changing any semantics once the rest is in use.
