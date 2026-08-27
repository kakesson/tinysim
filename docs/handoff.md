# Where this project stands, and how to pick it up again

Written at the end of the session that built TinySim, for whoever continues it
-- including a future Claude Code session, which should read this file and
`CLAUDE.md` first.

Repository: <https://github.com/kakesson/tinysim> (public, `main`).

---

## 1. What exists

Everything below is committed and pushed. `python -m pytest tests/ -q` passes
112 tests; `python experiments/build_html.py` regenerates the eight reports.

```
tinysim/                the implementation, one module per pipeline stage
  lexer.py              text -> tokens
  parser.py             tokens -> syntax tree (recursive descent, one method per rule)
  ast_nodes.py          the tree, and printing expressions back as text
  flatten.py            instantiation, extends, modifiers, connect() expansion
  alias.py              alias and known-value elimination, iterated to a fixed point
  analysis.py           states, incidence, matching, Tarjan BLT, balance checks
  codegen.py            SymPy per block -> readable Python source, compiled
  integrators.py        euler, heun, rk4, written out in full
  simulator.py          initialization, integration, events, results
  evaluator.py          direct numeric evaluation (parameters, start values, when bodies)
  report.py             the pipeline, printed
  htmlreport.py         the pipeline, as a standalone HTML page
  plotting.py           results and incidence matrices
  cli.py                tinysim show | check | run

docs/language.md        the language specification -- the contract
docs/pipeline.md        one circuit followed through every stage (start here)
docs/building-with-claude.md   how this was built with an agent
docs/handoff.md         this file
prompts/                the master prompt and the phase prompts
.claude/agents/         four review subagents
examples/*.tiny         nine models, one teaching point each
experiments/0*.py       eight scripts, each with --html
html/                   the generated reports, committed
figures/                the generated figures, committed
tests/                  112 tests, asserting against hand calculations
```

## 2. The decisions that are settled, and why

Reopening any of these means rewriting more than it looks like.

| Decision | Why | Where it lives |
| --- | --- | --- |
| Modelica-faithful syntax: `Real`, semicolons, `end Name;` | students carry the concepts to OpenModelica or Dymola | `docs/language.md` |
| Every block closes with a plain `end` -- `end;`, never `end when;` | one rule instead of several | parser rejects `end when;` with a message |
| No experiment or plotting construct in the language | the `.tiny` file says what is *true*; Python says what to *do* | `docs/language.md` §7 |
| One scalar type, `Real` | no arrays, records, Integer, Boolean; `discrete Real` stands in for a flag | spec §2, §9 |
| Index-1 only | a high-index model is detected and explained, never repaired | `analysis.py`, `examples/pendulum_cartesian.tiny` |
| No tearing of algebraic loops | a loop is solved as it stands, which is slower and far easier to read | noted in `docs/pipeline.md` |
| Generated code is a deliverable | it is printed for students, so it keeps comments and real names | `codegen.py` |
| Errors are teaching material | every message names the equation or variable and says what to do | `analysis.py`, `flatten.py` |

## 3. Things that will bite you

Each of these was a real bug, found by a test rather than by reading. They are
also documented with their symptoms in `docs/building-with-claude.md` §5.

- **Connection sets are formed per model, not once for the flattened whole.**
  Merging them globally lets current vanish in a composite component. See
  `flatten._expand_connections` and the test with `Series`.
- **Alias elimination must iterate.** Substituting `gnd.p.v = 0` creates new
  aliases that the same pass has already walked past.
- **A "known" value must be built from parameters only.** `v = slope * time`
  is not constant; treating it as one turns an index-2 model into a wrong
  index-1 one.
- **A failed `fsolve` must never pass silently.** The generated code checks the
  flag and raises with the block number and the model variable names.
- **Do not default to `LSODA`.** An exception raised inside its Fortran
  callback aborts the interpreter. The default is `Radau`, which is Python.
- **Events need hysteresis.** At the instant an event is handled its condition
  sits exactly on zero and integration restarts there, so the same crossing is
  found again forever. `event_tolerance` (default `1e-8`) is the cure.

## 4. Environment

- Python lives in `.venv/` (3.9). There is **no** system `python3` with SciPy,
  so always run `.venv/bin/python`.
- `pip install -e .` puts a `tinysim` command on the path.
- Pushing to GitHub uses **HTTPS with the `gh` credential helper**; the SSH key
  on this machine is rejected. The remote is already set up correctly.

## 5. Open items, roughly in order of value

1. **GitHub Pages.** `html/` is committed but GitHub serves it as raw text.
   Turning on Pages would make `html/index.html` a link to hand to students.
   One command: `gh api -X POST repos/kakesson/tinysim/pages -f source[branch]=main -f source[path]=/`.
2. **Rebuild the reports automatically.** A Claude Code hook on writes to
   `tinysim/` or `examples/` could run `experiments/build_html.py`, so `html/`
   never goes stale. Today it is a manual step.
3. **Tearing**, as an optional, printable stage. The resistor network's 6x6
   loop would become one iteration variable, and the report would then show
   what Dymola means by "6 equations, 1 iteration variable".
4. **Pantelides index reduction**, behind a flag. Deliberately out of scope
   today; if it is ever added, the failure message in `analysis.py` must stay
   reachable, because the failure is the lesson.
5. **A worked exercise sheet** built on the examples: change a parameter,
   predict the BLT blocks, break a model on purpose.

## 6. Continuing with Claude Code

The conversation that produced this is stored locally by Claude Code:

```bash
cd /Users/knut/Dropbox/work/courses/SSY191/tinysim
claude --continue        # resume the most recent session in this directory
claude --resume          # pick from a list of sessions
```

`/resume` does the same from inside a running session. If you start fresh
instead, the context that matters is on disk: `CLAUDE.md` is loaded
automatically, and this file plus `docs/language.md` are the two to read.

A good opening prompt for a new session:

```text
Read CLAUDE.md, docs/handoff.md and docs/language.md. Then <the task>.
Run the tests before and after, and keep docs/language.md and the
implementation in agreement.
```

For a larger piece of work, `prompts/01-phase-prompts.md` has the pattern that
built this: one stage per session, a green test suite at the end of each, and
an independent review by one of the subagents in `.claude/agents/`.
