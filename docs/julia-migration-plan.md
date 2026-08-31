# Moving TinySim to Julia -- a migration plan

> **Status: plan.** Nothing has been ported yet. Two decisions marked
> **[OPEN]** below change the shape of the work; everything else follows from
> them. Reports are settled: the HTML generator is ported (section 4).

TinySim is about 7 000 lines of Python and 2 000 lines of tests, covering a
pipeline from `.tiny` source to simulation results, contracts, and reports.
This is a plan for rebuilding it in Julia while leaning on existing Julia
libraries wherever that does not cost the thing the project exists for: that
every stage is *readable*.

---

## 1. Why Julia is a good fit here, and where the danger is

Julia's modeling ecosystem is not a set of loose libraries -- it is the same
pipeline TinySim teaches, implemented for real and split into reusable pieces.
The depot on this machine already contains all of it:

| TinySim stage | Julia library that does the same job |
| --- | --- |
| flattening, connect | `ModelingToolkit` (`@connector`, `@mtkmodel`, `connect`) |
| alias elimination, matching, BLT | `BipartiteGraphs`, `ModelingToolkitTearing`, `StateSelection`, `Graphs` |
| symbolic per-block solving, code generation | `Symbolics.build_function`, `SymbolicUtils` |
| integration, events | `OrdinaryDiffEq`, `Sundials`, `DiffEqCallbacks` |
| algebraic loops | `NonlinearSolve`, `SCCNonlinearSolve`, `LinearSolve` |
| contract monitoring | `SignalTemporalLogic` |
| plots, reports | `Plots`, `Latexify`, `PlutoUI` |
| index reduction (out of scope today) | `StateSelection` (Pantelides, dummy derivatives) |

**The danger is the same thing.** If the pipeline is delegated to
ModelingToolkit, the readable implementation students are meant to study
becomes MTK's -- a mature, heavily optimised, hard-to-read code base. The
teaching artefact would be gone, and what remained would be a worse
ModelingToolkit.

So the strategy question is not "which libraries" but **how much of the
pipeline stays hand-written**.

## 2. **[OPEN 1]** Three strategies

| | what it is | keeps | costs |
| --- | --- | --- | --- |
| **A. Port** | the same hand-written pipeline, in Julia; libraries only for solving, symbolics, plotting, STL | everything the project is for | a full rewrite, and the libraries do less than they could |
| **B. Build on MTK** | `.tiny` front end compiles to ModelingToolkit; MTK does flattening, tearing, code generation, and its results are displayed | far less code; index reduction and tearing for free; real-tool behaviour | the pipeline is no longer readable, which was the point |
| **C. Both** (recommended) | A as the teaching path, plus an MTK back end used as a reference and cross-check | the teaching value *and* a comparison against a real tool | more surface than A alone |

**C** is the same pattern this project already used twice: the hand-written
monitor cross-checked against SignalTemporalLogic.jl, and the built-in
integrators compared against SciPy's. A student sees the hand-written BLT
sorting *and* what `ModelingToolkitTearing` makes of the same model. Do **A**
first; **C** is A plus one extra back end, so nothing is wasted.

## 3. **[OPEN 2]** The model syntax

| | |
| --- | --- |
| **Keep `.tiny` as text** (recommended) | the language is the agreed teaching artefact and is deliberately not tied to a host language. The lexer and parser are the smallest and most mechanical part of the port (~900 lines), and the specification, examples and documentation survive untouched. |
| **Move to a Julia macro DSL** | `@model`/`@connector` like MTK and Modia. More idiomatic, free editor support, no parser to maintain -- but the language stops being a thing students *read a grammar for*, and every example, document and contract has to be rewritten. |

Keeping the text format also keeps the option of a second back end (MTK) honest:
one source, two compilations.

## 4. Reports -- **decided: port the HTML generator**

The ~700 lines of string building are ported as they are, so the generated
pages stay comparable with today's -- which is what makes this part of the port
verifiable at all. `Documenter.jl` remains the right tool for the manual in
`docs/`, and Pluto notebooks are worth adding *later* as a second, interactive
surface for lectures, not as a replacement for reproducible pages.

## 5. How the mapping works out, stage by stage

Most of the port is translation, not design. Two stages get materially better,
and one gets simpler.

**Code generation gets better.** `Symbolics.build_function(expr, args...;
expression = Val{true})` returns the generated code *as an expression*, which
is exactly what TinySim prints today -- but produced by a real symbolic
library, and `Latexify` can render the same equations for slides.

**Events get simpler, and more honest.** The three event policies map onto
three standard SciML constructs, which is a strong sign the semantics were
right:

| TinySim today | Julia |
| --- | --- |
| `events="locate"` | `VectorContinuousCallback(..., rootfind = SciMLBase.LeftRootFind)` |
| `events="step"` | `DiscreteCallback` -- the condition is checked only at the end of a step, by construction |
| `events="off"` | no callback |
| `reinit(v, ...)` | `integrator.u[i] = ...` inside `affect!` |
| the hysteresis band | the callback's own `abstol` and `repeat_nudge` |

Fixed and variable step likewise: `solve(prob, Euler(), dt = h, adaptive = false)`
against `solve(prob, Rodas5P())`. The hand-written `integrators.py` stays as
teaching code and is cross-checked against `Euler()`, `Heun()`, `RK4()`.

**Contracts.** The monitor ports directly; SignalTemporalLogic.jl becomes a
native dependency rather than a subprocess, so the cross-check gets cheaper.
The `whenever` fragment that library cannot express stays ours.

## 6. Verification: the Python version is the oracle

The port must reproduce known-good numbers, and this repository already has
them. Before writing any Julia:

1. Export golden files from Python for every example -- flat equations, the
   alias map, the BLT blocks and their methods, the generated code's structure,
   simulation results at fixed time points, and every contract margin.
2. The Julia test suite reads those files and must reproduce them:
   equations and blocks exactly, numbers to a stated tolerance.

That turns a rewrite into a differential test, which is the same technique used
for the STL monitor and for the solvers. It also gives an unambiguous
definition of "done".

## 7. Phases

Each phase ends with a green test suite and a commit; the golden files make
every phase verifiable on its own.

| # | phase | what it contains |
| --- | --- | --- |
| 0 | scaffold | `TinySim.jl` package, `Project.toml`, `test/runtests.jl`, GitHub Actions, golden-file export from Python |
| 1 | lexer, parser, AST | mechanical translation; the grammar does not change |
| 2 | flattening, alias elimination | the pass structure carries over unchanged |
| 3 | structural analysis | hand-written matching and Tarjan; cross-checked against `Graphs.strongly_connected_components` and `BipartiteGraphs` |
| 4 | code generation | `Symbolics` for per-block solving and for printable generated code |
| 5 | simulation | `OrdinaryDiffEq` + callbacks; the three event policies; the hand-written fixed-step methods kept and cross-checked |
| 6 | contracts | monitor port, `SignalTemporalLogic` as a direct dependency |
| 7 | reports and CLI | terminal report, HTML generator, `tinysim show/check/run` |
| 8 | experiments | the nine scripts, the figures, the HTML pages regenerated and compared |
| 9 | documentation | `docs/` ported, `CLAUDE.md` and the prompt kit rewritten for Julia |
| 10 | retire Python | archived under a tag, or kept in `python/` as the reference implementation |

Phases 1-3 are pure translation and should go quickly. Phase 4 and 5 are where
the libraries change the shape of the code and where the real work is.

## 8. Risks, and what to do about them

| risk | mitigation |
| --- | --- |
| **Start-up latency.** A Julia script that loads OrdinaryDiffEq and Plots can take 20-30 s before the first plot -- painful in a lecture and in a test loop | `PrecompileTools` in the package; a project-local system image for the course; keep the test suite free of plotting where possible |
| **The libraries pull in a large dependency graph**, which is at odds with a teaching tool that should be easy to install | split: `TinySim.jl` core depends only on `Symbolics` and `OrdinaryDiffEq`; plotting, reports and the MTK back end go in package extensions |
| **The rewrite loses a fix** that took a while to find the first time | the golden files, plus porting the tests first in each phase. The traps are listed in `docs/handoff.md` §3 and must all reappear as Julia tests |
| **Two implementations drift** while both exist | keep Python frozen from phase 1: no new features there, only the oracle |
| **Students must install Julia** | true, and it is a real cost; but this course's machine already carries the whole modeling stack, so it is likely the intended audience does too |

## 9. What does *not* change

The language specification, the examples, the contracts, the Lean plan, and the
teaching sequence in `docs/pipeline.md` are all language-independent. So is the
principle that every stage stays inspectable -- which is the one thing the port
must not trade away for shorter code.
