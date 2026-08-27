# How a model becomes a simulation

This is the document to read after `docs/language.md`. It follows one small
circuit through every stage of TinySim, showing the real output at each step.
Everything printed here comes from

```bash
tinysim show examples/electrical.tiny
```

so you can reproduce it, and change the model to see what changes. The same
material for any model, as a standalone web page:

```bash
tinysim show examples/electrical.tiny --html report.html
python experiments/build_html.py            # a page per experiment, plus an index
```

The circuit is a voltage source, a resistor, a capacitor and a ground:

```modelica
model RCCircuit
  ConstantVoltage src(V = 10);
  Resistor        r(R = 100);
  Capacitor       c(C = 1e-3, v(start = 0));
  Ground          gnd;
equation
  connect(src.p, r.p);
  connect(r.n, c.p);
  connect(c.n, src.n);
  connect(src.n, gnd.p);
end RCCircuit;
```

Notice what the model does *not* say: which variable depends on which, what
should be computed from what, or in which order. That is the whole point of an
acausal language, and it is what the rest of this document has to make up for.

---

## Stage 1 -- parsing

The text becomes a tree. `tinysim/lexer.py` produces tokens; `tinysim/parser.py`
turns them into the classes in `tinysim/ast_nodes.py`, with one method per rule
of the grammar. Nothing interesting happens here, which is why the parser is
hand-written and short: it is meant to be read once and then ignored.

The one thing worth noticing is that `v = R * i;` becomes an `Equation` node
with a left side and a right side -- **not** an assignment. Nothing so far has
decided that this equation computes `v`.

## Stage 2 -- flattening

Every component is expanded into its class's variables and equations, with
dotted names, and every `connect` becomes real equations.

```
2. FLATTENED MODEL  --  20 equations, 20 continuous variables

    1  src.v = src.p.v - src.n.v       # ConstantVoltage
    2  src.i = src.p.i                 # ConstantVoltage
    3  src.p.i + src.n.i = 0           # ConstantVoltage
    4  src.v = src.V                   # ConstantVoltage
    5  r.v = r.p.v - r.n.v             # Resistor
    6  r.i = r.p.i                     # Resistor
    7  r.p.i + r.n.i = 0               # Resistor
    8  r.v = r.R * r.i                 # Resistor
    9  c.v = c.p.v - c.n.v             # Capacitor
   10  c.i = c.p.i                     # Capacitor
   11  c.p.i + c.n.i = 0               # Capacitor
   12  c.C * der(c.v) = c.i            # Capacitor
   13  gnd.p.v = 0                     # Ground
   14  src.p.v = r.p.v                 # connect(src.p, r.p) - potential
   15  src.p.i + r.p.i = 0             # connect(src.p, r.p) - flow
   16  src.n.v = c.n.v                 # connect(src.n, c.n, gnd.p) - potential
   17  src.n.v = gnd.p.v               # connect(src.n, c.n, gnd.p) - potential
   18  src.n.i + c.n.i + gnd.p.i = 0   # connect(src.n, c.n, gnd.p) - flow
   19  r.n.v = c.p.v                   # connect(r.n, c.p) - potential
   20  r.n.i + c.p.i = 0               # connect(r.n, c.p) - flow
```

Equations 14 to 20 are the ones to stare at. They were generated from four
`connect` statements by two rules and nothing else:

* every **potential** variable in a connection set is equal -- equations 14,
  16, 17, 19;
* every **flow** variable in a connection set sums to zero -- equations 15, 18,
  20.

Those two lines are Kirchhoff's voltage and current laws. Give the connector
different variable names and they are Newton's third law, or conservation of
mass, or an energy balance -- see `examples/dcmotor.tiny`, where the same rules
produce both at once. This is the central idea of the whole subject: the
physics lives in the components, the topology lives in `connect`, and the tool
knows neither.

The count at the top matters too: **20 equations, 20 continuous variables**. A
model with a different count is not a model, and TinySim says so before doing
anything else.

### The sign convention

A flow variable is positive *into* the component. When a model connects its own
connector to a sub-component's -- a composite built from smaller parts -- the
same physical current is leaving through one and entering through the other, so
the model's own connector enters the sum with the opposite sign. Connection
sets are therefore built per model, not once for the flattened whole. Getting
this wrong produces a model that looks fine and quietly lets current vanish.

## Stage 3 -- alias elimination

Most of those twenty equations say nothing: `r.i = r.p.i` means the two names
are the same quantity. Every Modelica compiler removes them first.

```
4. ALIAS ELIMINATION
  20 equations -> 4 equations, 16 variables removed

    c.n.i = -c.i      c.p.i = c.i       r.i = c.i        src.i = -c.i
    c.n.v = 0         c.p.v = c.v       r.n.i = -c.i     src.n.i = c.i
    gnd.p.v = 0       r.p.i = c.i       r.n.v = c.v      src.n.v = 0
    r.p.v = src.V     src.p.i = -c.i    src.p.v = src.V  src.v = src.V

  Remaining equations:
    1  r.v = src.V - c.v
    2  r.v = r.R * c.i
    3  c.C * der(c.v) = c.i
    4  c.i - c.i + gnd.p.i = 0
```

Twenty equations become four, and those four are what you would have written by
hand. The eliminated variables are not lost: the generated code recovers them
at the end, so `r.p.i` can still be plotted.

Two details are worth more than they look:

* **The pass has to be repeated.** Substituting `gnd.p.v = 0` turns
  `c.v = c.p.v - c.n.v` -- three variables, not an alias -- into `c.v = c.p.v`,
  which is one. TinySim repeats the pass until nothing more can be removed.
* **A "known" value must be built from parameters only.** If `v = slope * time`
  were treated as a known constant, then `der(v)` would be replaced by zero and
  a high-index model would silently turn into a wrong one. See
  `tests/test_analysis.py::test_capacitor_across_a_time_varying_source_is_high_index`.

## Stage 4 -- what is unknown?

```
  variable                 kind              start   description
  src.V                    parameter            10   source voltage [V]
  r.v                      algebraic                 voltage drop p.v - n.v [V]
  r.R                      parameter           100   resistance [Ohm]
  c.v                      state                 0   voltage drop p.v - n.v [V]
  c.i                      algebraic                 current from p to n [A]
  c.C                      parameter         0.001   capacitance [F]
  gnd.p.i                  algebraic                 current INTO the component [A]

  states     (1): c.v
  unknowns   (4): der(c.v), r.v, c.i, gnd.p.i
```

`c.v` appears inside `der(...)`, which makes it a **state**: during simulation
the integrator supplies its value, so it is *known* at each instant, and
`der(c.v)` is the unknown in its place. That substitution is what turns a set
of equations into something an ODE solver can be asked about.

## Stage 5 -- matching: which equation computes which unknown

There are four equations and four unknowns, but no equation says which is
"its" unknown. Finding an assignment is a perfect matching in a bipartite
graph, and `tinysim/analysis.py` finds one with the textbook augmenting-path
algorithm: give each equation an unknown; when only taken ones are left, ask
their owners to move, recursively.

```
7. MATCHING  --  which equation computes which unknown

  eq   1  ->  r.v                   r.v = src.V - c.v
  eq   2  ->  c.i                   r.v = r.R * c.i
  eq   3  ->  der(c.v)              c.C * der(c.v) = c.i
  eq   4  ->  gnd.p.i               c.i - c.i + gnd.p.i = 0
```

Equation 2 was written as `v = R * i` by someone thinking about voltage. Here it
is used to compute the current. Nobody decided that; the matching did.

**If no perfect matching exists, the model is structurally singular** -- and for
a physically sensible model that almost always means the differential index is
higher than one. More on that below.

## Stage 6 -- sorting: in which order?

Equation 2 needs `r.v`, which equation 1 produces, so 1 comes first. Written as
a graph -- an edge from each equation to the equations producing what it needs
-- the question becomes a topological sort, except that the graph may have
cycles. Tarjan's strongly-connected-components algorithm answers both questions
at once: it finds the cycles, and it returns the components already in solvable
order.

```
8. INCIDENCE MATRIX (BLT sorted)

      eq |  1  2  3  4          columns: 1 r.v   2 c.i   3 der(c.v)   4 gnd.p.i
      ---+------------
        1|  X  .  .  .
        2|  x  X  .  .
        3|  .  x  X  .
        4|  .  x  .  X
```

`X` is the unknown an equation was matched with, `x` any other occurrence.
Everything above the diagonal is empty: that is the **block lower triangular**
form, and it is exactly the statement that the equations can be solved from the
top down. `experiments/02_structure_and_sorting.py` draws the same matrix
before and after sorting.

A block containing more than one equation is an **algebraic loop**: those
equations must be solved together.
`examples/resistor_network.tiny` has a 6x6 linear one and
`examples/diode_circuit.tiny` a nonlinear one.

> Real tools go one step further and *tear* a loop: choose a small number of
> iteration variables, express the rest explicitly, and iterate on the small
> residual. That is why Dymola reports "6 equations, 1 iteration variable".
> TinySim does not tear -- it solves the block as it stands, which is simpler
> to read and slower to run.

## Stage 7 -- generating code

Each block becomes a few lines of Python. A block of size 1 that is linear in
its unknown is solved symbolically with SymPy and becomes an assignment; a
nonlinear one is solved symbolically if SymPy manages and otherwise becomes a
call to a root finder; a linear loop becomes a small matrix solve; a nonlinear
loop becomes a residual function and a root find.

```python
    # ---- states: supplied by the integrator ----
    c__v = x[0]    # c.v -- voltage drop p.v - n.v [V]

    # ---- block 1: solve for r.v  [explicit]
    #        r.v = src.V - c.v
    r__v = -c__v + src__V

    # ---- block 2: solve for c.i  [explicit]
    #        r.v = r.R * c.i
    c__i = r__v/r__R

    # ---- block 3: solve for der(c.v)  [explicit]
    #        c.C * der(c.v) = c.i
    der_c__v = c__i/c__C
```

This is the *simulation model*: the thing that is actually run. It is printed,
not hidden, because seeing it is the point -- a student can check it against
the derivation they would have done by hand, and find that a tool arrived at
the same three lines from a description that never mentioned an order.

(The dots in model names become double underscores, because `c.v` is not a
valid Python identifier.)

An iterative block always checks whether it converged. A root finder that gives
up returns a number like any other, and a simulation that continues with it
produces a plot that looks plausible and is wrong.

## Stage 8 -- initialization is a second, different system

`start` values are the simple case. When the initial state has to be *computed*,
an `initial equation` says so:

```modelica
initial equation
  der(h) = 0;               // start in steady state
```

During simulation the unknowns are `der(h)` and `q`; at initialization they are
`der(h)`, `h` *and* `q`, and the extra equation pays for the extra unknown. It
gets its own matching, its own sorting and its own generated function --
`tinysim.explain(model, "initialization")` prints them.
`experiments/04_initialization.py` shows the difference against an arbitrary
start value.

## Stage 9 -- simulation, and events

Between events the model is an ODE and SciPy integrates it. TinySim writes no
integrator: the interesting part of a modeling language is the translation.

A `when` clause becomes a **zero-crossing function**. `when h < 0` becomes the
margin `0 - h`, positive exactly while the condition holds, and the integrator
locates the instant it crosses zero upwards. Then the body runs -- `reinit`
jumps a state, an assignment updates a discrete variable -- and integration
restarts from the new state. A hybrid simulation is a *sequence of continuous
segments*, one per event.

Two details decide whether that works:

* A `when` fires when its condition *becomes* true. A condition that already
  holds at the start of a segment is watched for becoming false instead, which
  re-arms it.
* At the instant an event is handled, its condition sits exactly on zero, and
  integration restarts from there -- so the same crossing is found again,
  forever. Real simulators use a small hysteresis band, and so does TinySim
  (`event_tolerance`, default `1e-8`). This is why event times carry a
  tolerance just as the states do.

### Zeno behaviour

The bouncing ball bounces infinitely often in finite time. No simulator can
follow that. Once the bounces are smaller than the event tolerance the contact
stops being detected, the condition `h < 0` stays true, and the ball falls
through the floor. Simulate to 3 s and the plot is right; simulate to 20 s and
it is not. The honest fix is to model the resting contact, not to tune the
tolerance.

## When it does not work: high index

Write the pendulum in Cartesian coordinates and everything above breaks:

```modelica
  der(x) = vx;
  der(y) = vy;
  m * der(vx) = -F * x / L;
  m * der(vy) = -F * y / L - m * g;
  x ^ 2 + y ^ 2 = L ^ 2;      // the constraint
```

Five equations, five unknowns -- the count is right. But the last equation
contains no unknown at all: `x` and `y` are states, so the integrator supplies
them, and there is nothing in it for that equation to compute. The matching
fails, and TinySim says why:

```
the model is structurally singular: there is no way to give every equation an
unknown of its own.

Equations left without an unknown to compute:
    eq 5: x^2 + y^2 = L^2      [CartesianPendulum]
Unknowns left without an equation to compute them:
    der(vy)

This usually means the differential index of the model is higher than 1 [...]
```

The states are not independent: the rod ties them together. A real tool applies
**index reduction** -- Pantelides' algorithm finds the constraints that must be
differentiated, differentiates them, and dummy-derivative selection decides
which variables stay states. TinySim deliberately stops here, so that the
failure, and the reason index reduction exists, are visible rather than
invisible. Run `experiments/05_when_it_does_not_work.py` to see this and three
other failures.

The same pendulum in angular coordinates has no constraint at all, and
simulates without any of that machinery -- which is the practical lesson:
choosing coordinates is part of modeling.

---

## Where each stage lives

| Stage | Module | What it produces |
| --- | --- | --- |
| 1. Tokens | `tinysim/lexer.py` | a list of tokens |
| 1. Syntax tree | `tinysim/parser.py`, `ast_nodes.py` | classes, equations, connects |
| 2. Flattening | `tinysim/flatten.py` | one flat equation set, dotted names |
| 3. Alias elimination | `tinysim/alias.py` | a smaller equation set, plus what was removed |
| 4-6. Structure | `tinysim/analysis.py` | states, unknowns, incidence, matching, BLT blocks |
| 7. Code generation | `tinysim/codegen.py` | readable Python source, compiled |
| 8-9. Simulation | `tinysim/simulator.py` | time series, events |
| Reporting | `tinysim/report.py`, `plotting.py` | everything above, printed or drawn |
