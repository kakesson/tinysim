# TinySim

A teaching implementation of an equation-based, acausal modeling language (a
small Modelica subset).  Readability is the product: students read this.

**The project is migrating to Julia on ModelingToolkit** -- see
`docs/julia-migration-plan.md`.  The Python implementation is **frozen**: it is
the oracle the port is checked against, and the golden files in `golden/` are
its recorded answers.  Do not add features to it; CI fails if the golden files
stop matching.  New work goes in `julia/TinySim`.

## Commands

```bash
julia --project=julia/TinySim -e 'using Pkg; Pkg.test()'   # the Julia port
python tools/export_golden.py                 # refresh the oracle (rarely)

.venv/bin/python -m pytest tests/ -q          # the test suite (must stay green)
.venv/bin/python -m tinysim show FILE.tiny    # print every pipeline stage
.venv/bin/python -m tinysim check FILE.tiny   # parse + analyse only
MPLBACKEND=Agg .venv/bin/python experiments/01_rc_pipeline.py
.venv/bin/python experiments/build_html.py    # regenerate html/ after a change
```

The virtual environment is `.venv/` (Python 3.9). There is no `python3` with
scipy on the PATH, so always use `.venv/bin/python`.

## The language is a contract

`docs/language.md` defines the language. Code and examples follow the spec; if
a change needs the spec to change, change `docs/language.md` in the same
commit and say so.

## Style

- Full words, no abbreviations: `equation_index`, not `eq_i`. This is teaching
  code and reads as prose.
- Comments explain *why* and name the concept ("this is the matching step"),
  never restate the line.
- Every module starts with a docstring saying which pipeline stage it is and
  what it hands to the next one.
- The generated simulation code in `codegen.py` is *output students read*:
  keep its comments, blank lines and variable names as tidy as the rest.
- No new runtime dependencies beyond numpy, scipy, sympy, matplotlib.

## Non-negotiables

- Every pipeline stage stays inspectable: if you add a stage, add a section to
  `report.py`, to `htmlreport.py`, and to `docs/pipeline.md`.
- Contracts are checked, never proved. Any wording that suggests a run verifies
  a contract is a bug; "not tested" and "vacuous" are findings, not passes.
- The contract monitor is cross-checked against SignalTemporalLogic.jl
  (`tests/test_stl_julia.py`, skipped without Julia). If you change the
  robustness semantics, that comparison must still come out at zero.
- Generated HTML must stay self-contained: styles inline, images as data URIs,
  no scripts, no network.
- Errors are teaching material. An error message names the offending equation
  or variable and says what to do about it. Never let a numerical solver fail
  silently.
- Index reduction is deliberately **out of scope**: a high-index model must be
  detected and explained, not solved. See `examples/pendulum_cartesian.tiny`.
- Solver choices stay visible and comparable: fixed step (`integrators.py`) as
  well as SciPy, and `events="locate" | "step" | "off"`. The wrong answers the
  cheap options give are teaching material, so never quietly correct them.
- Tests assert against results worked out by hand (analytic solutions,
  textbook formulas), not against whatever the code currently prints.
