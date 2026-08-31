# TinySim

A tiny equation-based, **acausal** modeling language and simulator, written to
be read.

TinySim is a small subset of [Modelica](https://modelica.org), with the same
syntax and the same semantics, implemented in under 4000 lines of
heavily commented Python. It is not fast and it is not complete. Its purpose is that **every step
from model text to simulation result can be printed and inspected**: the
flattened equations, what `connect` generated, which equation was chosen to
compute which unknown, the order they are solved in, the Python code that gets
generated, and how events are handled.

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

Nothing in that model says what depends on what. TinySim turns it into 20 flat
equations, reduces them to 4, sorts them, and generates this:

```python
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

The sorted incidence matrix is the picture worth keeping: dark cells are the
unknown each equation was chosen to compute, the red outline is an algebraic
loop, and nothing above the diagonal means the blocks can be solved from the
top down.

![Incidence matrix before and after sorting](figures/incidence_resistornetwork.png)

## Moving to Julia

TinySim is being rebuilt in Julia on
[ModelingToolkit](https://github.com/SciML/ModelingToolkit.jl), so that the
reports show what a production tool really does with a model rather than what a
teaching reimplementation does. The plan, and what was verified before any of
it was written, is in
[`docs/julia-migration-plan.md`](docs/julia-migration-plan.md).

The Python implementation described below is **frozen**: it is the oracle the
port is checked against, and `golden/` holds its recorded answers for every
example -- flat equations, solution order, sampled trajectories, events and
contract margins.

```bash
julia --project=julia/TinySim -e 'using Pkg; Pkg.test()'
```

## Install

```bash
python -m venv .venv
.venv/bin/pip install -e .          # numpy, scipy, sympy, matplotlib
.venv/bin/pip install pytest        # to run the tests
```

## Use it

From Python -- the model file describes the system, the script decides what to
do with it:

```python
import tinysim

model = tinysim.load("examples/electrical.tiny", "RCCircuit")
tinysim.explain(model)                       # every stage of the pipeline
result = tinysim.simulate(model, stop=1.0)
tinysim.plot(result, ["c.v", "r.i"])
```

How it is integrated is a choice, and the choice is visible:

```python
tinysim.simulate(model, stop=1.0)                              # variable step, events located
tinysim.simulate(model, stop=1.0, method="rk4", step=1e-3)     # fixed step
tinysim.simulate(model, stop=1.0, method="euler", step=1e-3,
                 events="step")                                # events noticed a step late
tinysim.simulate(model, stop=1.0, events="off")                # when-clauses never fire
```

`method` is a SciPy method (`Radau`, `BDF`, `RK45`, `LSODA`) or a fixed-step one
written out in `integrators.py` (`euler`, `heun`, `rk4`), and `events` is
`"locate"`, `"step"` or `"off"`. Simulate the bouncing ball with `events="off"`
and it falls through the floor; with `events="step"` it bounces late, from
below the floor, and keeps the wrong fraction of its energy at every bounce.
`experiments/08_zero_crossing.py` draws the crossing function itself and shows
where each answer comes from.

![Zero-crossing detection on the bouncing ball](figures/zero_crossing_detail.png)

From the command line:

```bash
tinysim show  examples/electrical.tiny                 # the whole pipeline
tinysim show  examples/dcmotor.tiny --stages flat,blt,procedure,code
tinysim show  examples/dcmotor.tiny --html report.html # ... as a web page
tinysim check examples/pendulum_cartesian.tiny         # analyse only
tinysim run   examples/bouncing_ball.tiny --stop 3 --plot h,v
tinysim run   examples/bouncing_ball.tiny --stop 3 --method rk4 --step 1e-3
tinysim run   examples/bouncing_ball.tiny --stop 3 --events off
tinysim run   examples/dcmotor.tiny --stop 3 --contracts   # check the contracts
```

## Contracts

A model can say what it needs from its environment and what it promises in
return, in a readable layer over Signal Temporal Logic:

```modelica
contract ChargesInTime for RCCircuit
  "the capacitor reaches 95 % of the source voltage within half a second"
assume
  always src.V >= 5 and src.V <= 15;
guarantee
  eventually within [0, 0.5] c.v >= 0.95 * src.V;
  always c.v <= src.V;
end ChargesInTime;
```

Every clause is checked against a run and reported with a **margin** -- the
robustness of the formula, in the units of the signal -- so "satisfied" comes
with how much room there was, and "violated" with by how much. A run in which
an assumption fails is reported as **not tested**, never as a pass: that is the
assume-guarantee reading, and it is what separates *the system misused this
component* from *this component broke its promise*. See
[`docs/contracts.md`](docs/contracts.md).

The clauses can also be handed to
[SignalTemporalLogic.jl](https://github.com/sisl/SignalTemporalLogic.jl) instead
of to TinySim's own monitor (`--stl-backend julia`), which is how the readable
monitor is kept honest: on every example here the two implementations agree
exactly.

![RC circuit simulation against the analytic solution](figures/rc_circuit.png)

## What it covers

| Idea | Where to see it |
| --- | --- |
| Acausal composition: `connect` becomes Kirchhoff's laws | `examples/electrical.tiny` |
| The same rules in two physical domains at once | `examples/dcmotor.tiny` |
| Flattening, alias elimination, matching, BLT sorting | `tinysim show ... --stages flat,alias,matching,blt` |
| How the sorted blocks are solved, without reading Python | `tinysim show ... --stages procedure` |
| Linear algebraic loops, solved as a matrix equation | `examples/resistor_network.tiny` |
| Nonlinear algebraic loops, solved by iteration | `examples/diode_circuit.tiny` |
| Initialization as a system of its own | `examples/tank.tiny` |
| Events, state jumps, discrete variables | `examples/bouncing_ball.tiny`, `examples/thermostat.tiny` |
| High index detected and explained, not solved | `examples/pendulum_cartesian.tiny` |
| Assume-guarantee contracts over Signal Temporal Logic | every example, [`docs/contracts.md`](docs/contracts.md) |
| A component contract checked once per instance | `examples/dcmotor.tiny`, `examples/electrical.tiny` |
| Fixed step against variable step, and the order of a method | `experiments/07_solvers.py` |
| With and without zero-crossing detection | `experiments/08_zero_crossing.py` |

## Read it in this order

1. [`docs/language.md`](docs/language.md) -- the language, with a grammar.
2. [`docs/pipeline.md`](docs/pipeline.md) -- one circuit followed through every
   stage, with the real output at each one. **Start here if you only read one.**
   Then [`docs/contracts.md`](docs/contracts.md) for what a model can be asked
   to promise.
3. [`experiments/`](experiments) -- six runnable scripts, each teaching one
   idea and producing one figure:

   ```bash
   .venv/bin/python experiments/01_rc_pipeline.py          # the pipeline, end to end
   .venv/bin/python experiments/02_structure_and_sorting.py # incidence matrices, drawn
   .venv/bin/python experiments/03_hybrid_events.py         # events and state jumps
   .venv/bin/python experiments/04_initialization.py        # steady-state start-up
   .venv/bin/python experiments/05_when_it_does_not_work.py # the error messages
   .venv/bin/python experiments/06_dc_motor.py              # two domains, one rule
   .venv/bin/python experiments/07_solvers.py               # fixed against variable step
   .venv/bin/python experiments/08_zero_crossing.py         # with and without event detection
   .venv/bin/python experiments/09_contracts.py             # contracts, checked against runs
   ```

   Every experiment also takes `--html`, which writes a standalone page
   instead of showing the plot: the model, every intermediate form of its
   equations, the sorted blocks, the generated simulation code, the results,
   the figures, and the script that produced them -- with no external files, so
   it can be handed out, opened offline, or printed.

   ```bash
   .venv/bin/python experiments/03_hybrid_events.py --html   # one page
   .venv/bin/python experiments/build_html.py                # all six + an index
   ```

4. The source, in pipeline order: `lexer.py`, `parser.py`, `flatten.py`,
   `alias.py`, `analysis.py`, `codegen.py`, `simulator.py`, `integrators.py`,
   then `contracts.py` and `monitor.py`.

## What it deliberately does not do

One scalar type (`Real`), no arrays, no records, no `algorithm` sections, no
user-defined functions, no packages, no tearing of algebraic loops, and **no
index reduction**: a high-index model is detected and explained, with a
description of what Pantelides' algorithm would do about it, rather than
quietly repaired. Leaving these out is what keeps the implementation readable;
saying so out loud is what keeps it honest.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

The tests check against results worked out by hand -- `V(1 - exp(-t/RC))`,
conservation of energy, `2*pi*sqrt(L/g)`, `sqrt(2h/g)`, `V*k/(k^2 + R*d)` --
not against whatever the code currently prints.

## Built with Claude Code

This repository is also a worked example of building a project of this size
with an AI coding agent: see
[`docs/building-with-claude.md`](docs/building-with-claude.md), the reusable
prompts in [`prompts/`](prompts), and the review subagents in
[`.claude/agents/`](.claude/agents).

## Licence

MIT.
