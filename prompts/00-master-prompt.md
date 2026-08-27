# The master prompt

This is the single prompt that produces TinySim from an empty directory. It is
written to be pasted into a fresh Claude Code session, in plan mode, in an
empty git repository.

It follows the shape Anthropic recommends for a large task: a precise
statement of *what*, an explicit statement of *what is out of scope*, the
patterns to follow, and -- most importantly -- **a way for Claude to check its
own work** without you.

Do not paste `docs/language.md` and this prompt into the same session and
expect both to be honoured. Agree the language first (see
`01-phase-prompts.md`, phase 0); the master prompt assumes the specification
already exists on disk.

---

```text
Build TinySim: a tiny equation-based, acausal modeling language and simulator,
in Python, for teaching. The audience is engineering students meeting Modelica
ideas for the first time; the implementation is the teaching material, so it
must be readable, commented, and small.

The language is already specified in docs/language.md. That file is the
contract: implement exactly it, and if you believe something in it is wrong,
say so and stop rather than deviating quietly.

WHAT IT MUST DO

Compile a .tiny model through a pipeline whose every stage can be printed:

  model text
    -> tokens                (lexer)
    -> abstract syntax tree  (parser)
    -> flat equations        (flattening: components expanded, connect() applied)
    -> reduced equations     (alias elimination)
    -> matched, sorted       (incidence matrix, matching, Tarjan BLT sorting)
    -> generated Python      (symbolic per-block solving with SymPy)
    -> results               (scipy.integrate.solve_ivp, with events)
    -> plots                 (matplotlib)

Requirements:
- ODEs, algebraic equations, and mixed systems of the two.
- Initial conditions both as `start` attributes and as `initial equation`
  sections, and the initialization problem must be solved as its own system.
- Hybrid behaviour: `when` clauses with `reinit` and discrete variables, fired
  on a rising edge and located by the integrator's root finder.
- Algebraic loops: detected as blocks larger than 1x1, solved as a linear
  system when they are linear and by a root finder when they are not.
- A high-index model (a pendulum in Cartesian coordinates) must be DETECTED
  and EXPLAINED, not solved. Index reduction is deliberately out of scope;
  the explanation should say what Pantelides' algorithm would do and why real
  tools need it.
- A Python API (`load`, `simulate`, `explain`, `plot`) and a command line
  (`tinysim show|check|run`). Experiments -- stop time, solver settings,
  plotting -- are ordinary Python, never part of the language.

WHAT MATTERS MOST

The generated simulation code is a deliverable, not an implementation detail.
It is printed for students to read. It must look like something a careful
person would write by hand: real variable names, a comment per block naming
the equation it solves, and no cleverness.

Errors are teaching material too. Every error names the offending equation or
variable and says what to do about it. A numerical solver that fails must
never fail silently.

HOW TO VERIFY (do this yourself, do not ask me)

Write pytest tests that check against results worked out by hand, not against
whatever the code currently prints:
- the RC circuit against V(1 - exp(-t/RC)), to 1e-6;
- an undamped pendulum against conservation of energy, and its small-angle
  period against 2*pi*sqrt(L/g);
- the bouncing ball's first bounce against sqrt(2h/g), and the velocity after
  it against -e times the velocity before;
- the DC motor's final speed against V*k/(k^2 + R*d);
- a steady-state initialization against (qin/k)^2;
- Kirchhoff's laws holding at every output point of every circuit;
- every error path: unbalanced, contradictory, high index, non-converging.

Run the suite after every stage and keep it green. When you think a stage is
finished, use a subagent to review it in a fresh context before moving on.

BUILD IT IN THIS ORDER, committing after each

1. lexer + parser + AST, with tests
2. flattening and connect() expansion, with tests
3. alias elimination, with tests
4. structural analysis: incidence, matching, BLT, with tests
5. code generation, with tests on the generated source and its results
6. simulation with events, with the analytic tests above
7. reporting, plotting, CLI
8. examples, experiment scripts, documentation

CONSTRAINTS

- Python 3.9+, only numpy, scipy, sympy, matplotlib.
- No parser generator: a hand-written recursive-descent parser, one method per
  grammar rule, is part of what students are meant to read.
- Full words for identifiers. Module docstrings say which pipeline stage they
  are.
- Do not add features the specification does not list. If you want one, ask.

Start by reading docs/language.md and the examples in examples/, then write a
plan and show it to me before you write code.
```

---

## Why the prompt is shaped this way

| Part | What it is doing |
| --- | --- |
| "The language is already specified... that file is the contract" | Gives one source of truth, so later sessions cannot drift. |
| The pipeline diagram | States the architecture, so Claude does not invent a different decomposition halfway through. |
| "WHAT MATTERS MOST" | Names the quality bar in terms of the *output a student sees*, which is not something Claude can infer from a test suite. |
| "HOW TO VERIFY (do this yourself)" | The single most valuable section. Analytic checks are a pass/fail signal Claude can run without you, which is what makes a long unattended run converge instead of drifting. |
| "BUILD IT IN THIS ORDER, committing after each" | Bounds the context per step and gives natural `/clear` points. |
| "Do not add features the specification does not list" | Scope control. Without it, index reduction and tearing appear uninvited. |
