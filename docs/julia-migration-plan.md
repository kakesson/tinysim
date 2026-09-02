# Rebuilding TinySim on ModelingToolkit -- a migration plan

> **Status: plan.** Nothing has been ported yet. The strategic decisions are
> settled (section 1); one consequence of them needs a decision of its own and
> is marked **[OPEN]** in section 6.

TinySim is about 7 000 lines of Python and 2 000 lines of tests. This is a plan
for rebuilding it in Julia on top of ModelingToolkit, and a record of what was
verified on this machine before any of it was written.

---

## 1. The decisions

| | decided |
| --- | --- |
| **Architecture** | build on **ModelingToolkit**: `.tiny` compiles to an MTK `System`, and MTK does connection expansion, alias elimination, matching, tearing and code generation |
| **Model syntax** | keep `.tiny` as a text format, with its own lexer and parser |
| **Reports** | port the HTML generator, so pages stay comparable with today's |
| **Python** | frozen from phase 1 and used as the oracle; archived under a tag when Julia reproduces its numbers |

The cost of the architecture decision, stated plainly: the pipeline stops being
readable *as TinySim's own code*. What replaces it is better in one way and
worse in another -- students see what a production tool really does, and they
see it through MTK's internals rather than through 3 000 lines written to be
read. The reports are what decide whether that trade pays off, which is why
they are ported rather than dropped: TinySim's job becomes **an instrument that
makes MTK's stages legible**.

## 2. What is still ours to write

| | |
| --- | --- |
| lexer, parser, AST | ~900 lines, mechanical translation of the Python |
| `.tiny` → MTK translator | new, and the heart of the port: connectors, components, equations, `when`/`reinit`, `start`, contracts |
| contracts and the monitor | ports directly; `SignalTemporalLogic.jl` becomes a normal dependency |
| reports, terminal and HTML | port, driven by MTK's data instead of ours |
| CLI, experiments, documentation | port |

That is still a substantial and readable body of code -- roughly 2 500 lines --
and it is where the teaching now lives: the translator shows what a `.tiny`
construct *becomes*, and the reports show what MTK makes of it.

## 3. Verified on this machine, before writing anything

Julia 1.12.3, ModelingToolkit **11.40.0**, and the whole SciML stack already in
the depot. Every claim below was run, not assumed.

**The pipeline.** For the RC circuit, built programmatically:

| stage | call | result |
| --- | --- | --- |
| as written | `equations(rc)` | the four `connect(...)` equations |
| flat model | `expand_connections(rc)` | **20 equations, 20 unknowns** -- the same flat model the Python flattener produces |
| simplified | `mtkcompile(rc)` | **1 equation**, `D(c₊v) ~ c₊i/c₊C` |
| what was eliminated | `observed(simplified)` | **19 equations** -- our alias table *and* the solution order, in MTK's own output |
| result | `solve(...; reltol=1e-9)` | `c.v(1) = 9.999550047` against the analytic `9.999546001` |

**The structural data the reports need is reachable.** `TearingState(sys)`
gives `structure.graph` (a `BipartiteGraph` of equations against variables,
with a print matrix), `var_to_diff`, `solvable_graph`, `var_types` and
`fullvars`. `ModelingToolkitTearing`, `StateSelection` and `BipartiteGraphs`
are separate packages in the depot, so the matching and BLT sections can be
built from the real tool's structures.

**Events work, and match our numbers.** `continuous_events = [[h ~ 0] =>
[v ~ -e * Pre(v)]]`, where MTK's `Pre` is exactly our `pre()`. The bouncing
ball: 6 bounces, the first at **0.451524** (analytic 0.451524), restitution
0.8000, `min h = 5.3e-16`, `h(3) = +0.0687` -- the same answer as the Python
version to every digit reported.

**The thermostat matches too**: `T` in **[19.0000, 21.0000]** after 20 s, `on`
taking exactly {0, 1}, and **209 switches** -- the same count as Python.

**One library cannot come along.** `SignalTemporalLogic.jl` 1.0 pins Zygote
0.6, which cannot coexist with the SciML stack MTK 11.40 needs. It is therefore
*not* a dependency of `TinySim.jl`; it stays available as an optional
cross-check run in its own environment, exactly as the Python implementation
already runs it. The contract monitor itself is ours in both languages.

### Traps, found by probing rather than by debugging later

1. **Symbols that appear only in an affect must be declared on the `System`.**
   MTK infers unknowns and parameters from the *equations*; a parameter used
   only inside a `when` body is never registered, and the failure is a runtime
   `UndefVarError` from generated code, thirty frames deep. The translator must
   always pass the declared variables and parameters explicitly.
2. **An affect written as an equation cannot assign a parameter**
   (`check_no_parameter_equations`). So a `discrete Real` does *not* become an
   MTK parameter. It becomes a **held state**: `D(on) ~ 0` plus events that
   jump it. That keeps the system balanced and every affect symbolic, and it is
   what produced the matching thermostat numbers above.
3. **`start` must reach MTK as a default**, or `mtkcompile` warns that the
   initialization system is underdetermined and quietly falls back to least
   squares.

A fourth, smaller one: `@connector` and `@mtkmodel` moved to `SciCompDSL.jl` in
MTK 11, so systems must be built programmatically -- which is what a compiler
does anyway.

Two more turned up while phase 2 was written, and are recorded here for the
same reason:

5. **`==` is a condition, not an equation.** Building it with `~` produces an
   `Equation`, which cannot go inside `ifelse`; it has to be built as a term,
   `Symbolics.term(==, a, b; type = Bool)`, or it answers at translation time
   instead of at simulation time.
6. **MTK's periodic events begin at the end of the first period.**
   `sample(0, Ts)` fires at 0 as well, so the first tick is asked for by name --
   `[t0] => affect` -- and the rest by period.

## 4. What each report section becomes

| today | on MTK |
| --- | --- |
| flat model | `equations(expand_connections(sys))` |
| connection sets | the connect equations, tagged during translation |
| alias elimination | `observed(mtkcompile(sys))` |
| incidence matrix, matching | `TearingState(...).structure.graph` |
| BLT blocks, solution order | MTK's torn blocks -- *including* the tearing our version does not do |
| generated code | `Symbolics.build_function(...; expression = Val{true})`, printable, and `Latexify` for slides |
| the solution procedure, in words | the same narration, over MTK's stages |
| contracts | unchanged; the monitor runs over the `ODESolution` |

## 5. Verification: Python is the oracle

Before any Julia is written, export golden files from the Python version for
all nine examples: flat equations, the alias map, the blocks, simulation
results at fixed time points, and every contract margin. The Julia test suite
must reproduce them -- equations and structure exactly, numbers to a stated
tolerance.

That turns a rewrite into a differential test, the same technique already used
against SignalTemporalLogic.jl and SciPy. It also gives "done" a definition.
The six traps in `docs/handoff.md` §3 must each reappear as a Julia test.

## 6. The high-index example -- **decided: show the reduction**

MTK performs index reduction, so `examples/pendulum_cartesian.tiny` -- today
*rejected* with an explanation of what Pantelides would do -- will simply
**simulate**. The example stays, and the report says what MTK's Pantelides and
dummy-derivative selection did to it. The lesson improves: from *why it cannot
be solved* to *here is the machinery that solves it*, with the refusal that
the Python version produced kept in the golden file as the "before".

`docs/language.md` §10 ("deliberately not in the language") changes with it:
index reduction and tearing are no longer out of scope, they are inherited.

A related question that the experiments force: with MTK constructing the
callbacks, the `events = "locate" | "step" | "off"` comparison in experiments
7-9 needs a different mechanism -- most likely building the `ODEProblem` and
then replacing the callback set, so the same compiled system can be simulated
three ways.

## 7. Phases

| # | phase | contents |
| --- | --- | --- |
| 0 | scaffold **(done)** | `julia/TinySim` pinning MTK 11.40, a test suite over the golden files, CI for both languages, `tools/export_golden.py` |
| 1 | lexer, parser, AST | mechanical; the grammar does not change |
| 2 | translator to MTK **(done)** | connectors, components, `extends`, modifiers, equations, `start`, `when`/`reinit` with `Pre`, discrete variables as held states |
| 3 | compile and inspect | `expand_connections`, `mtkcompile`, `observed`, `TearingState`; the data the reports need, extracted into our own structures |
| 4 | simulation | `ODEProblem`, solvers, the three event policies, fixed and variable step |
| 5 | contracts | monitor port, `SignalTemporalLogic.jl` as a dependency |
| 6 | reports and CLI | terminal report, HTML generator, `show`/`check`/`run` |
| 7 | experiments | the nine scripts, figures, and HTML pages regenerated and compared against the Python ones |
| 8 | documentation | `docs/` ported, `CLAUDE.md` and the prompt kit rewritten |
| 9 | retire Python | tagged, then removed from the main tree |

Phases 2 and 3 are the real work; the traps in section 3 are already paid for.

## 8. Risks

| risk | mitigation |
| --- | --- |
| **MTK's structural internals are not public API.** `TearingState` and the structure fields can change between versions | pin the version in `Project.toml`; keep every MTK access inside one module, `MTKBridge.jl`; the golden files catch a behaviour change immediately |
| **Two MTK versions in the depot** (10.31.2 and 11.6.1 resolved to 11.40.0) | pin, and state the version in the README |
| **Start-up latency**: MTK plus OrdinaryDiffEq plus Plots is 20-30 s to the first plot | `PrecompileTools`; a course system image; keep plotting out of the test suite |
| **MTK's answers differ from ours in a way that is not a bug but a better algorithm** (tearing, index reduction) | the golden files compare *results*, not intermediate block structure, wherever MTK legitimately does more |
| **The teaching value erodes** because the pipeline is now someone else's code | this is the accepted cost of the architecture decision; the reports are the mitigation, and they are ported first-class rather than trimmed |

## 9. What does not change

The language specification (apart from §10), the nine examples, the contracts,
the Lean plan, and the teaching sequence in `docs/pipeline.md`. So does the
principle that every stage stays inspectable -- which after this change means
*making MTK inspectable*, and is the reason the report layer is now the most
important part of the project rather than a convenience.
