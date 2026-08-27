# Building TinySim with Claude Code

This repository is also a worked example of a second thing: how to build a
project of this size with an AI coding agent, using the practices Anthropic
currently recommends for Claude Code. Everything described here is checked in,
so it can be re-run, criticised, and shown to students.

The prompts themselves are in [`prompts/`](../prompts). The subagents are in
[`.claude/agents/`](../.claude/agents). The project conventions Claude reads at
the start of every session are in [`CLAUDE.md`](../CLAUDE.md).

---

## 1. The one idea that matters: give the agent a way to check its work

Everything else is detail. An agent stops when the work *looks* done; without a
check it can run, "looks done" is the only signal it has, and you become the
verification loop.

For a simulation tool the check is unusually good, because the answers are
known in advance:

| Model | Check |
| --- | --- |
| RC circuit | `v(t) = V (1 - exp(-t/RC))`, to 1e-6 |
| Pendulum, undamped | energy is conserved; small-angle period is `2*pi*sqrt(L/g)` |
| Bouncing ball | first bounce at `sqrt(2h/g)`; velocity after is `-e` times before |
| DC motor | final speed is `V*k / (k^2 + R*d)` |
| Tank | steady-state initialization gives `(qin/k)^2` |
| Every circuit | Kirchhoff's laws hold at every output point |

None of these can be satisfied by code that merely runs. They caught real bugs
during this build -- see section 5.

Put the checks in the prompt, not only in your head:

> *write a test for the RC circuit that compares against V(1 - exp(-t/RC)) to
> 1e-6, run it, and show me the output*

## 2. Agree the specification before writing code

The language was settled first, in a session that produced no code at all --
`docs/language.md` and the example models. Claude Code has a specific tool for
this: ask it to interview you.

> *I want to build [...]. Interview me in detail using the AskUserQuestion
> tool. Ask about technical implementation, edge cases, concerns and
> tradeoffs. Don't ask obvious questions, dig into the hard parts I might not
> have considered. Keep interviewing until we've covered everything, then
> write a complete spec.*

Four questions decided the whole project: Modelica-faithful syntax; index-1
analysis with high index explained rather than solved; `end` closing every
block; and no experiment construct in the language. Reversing any of those
later would have meant rewriting the parser, the analysis, or every example.

Then start a **fresh session** to implement the spec. The implementation
session gets a clean context and a written contract.

## 3. Explore, plan, code, commit

The recommended loop, and the one used here:

1. **Explore** in plan mode (`Shift+Tab` until the status bar shows
   `plan mode on`, or `claude --permission-mode plan`). Claude reads and
   answers; it cannot edit.
2. **Plan.** Ask for the module decomposition and the tests each module will
   have, and read it before approving.
3. **Code**, one pipeline stage per session, tests included.
4. **Commit** after each stage, then `/clear`.

Planning has a cost, and it is not always worth paying: for a change you could
describe in one sentence, skip it. For "implement the structural analysis", it
is the difference between a decomposition you can teach from and one you
cannot.

## 4. Use subagents for the things that would fill your context

A subagent runs in its own context window and returns only its findings. Two
uses paid off here:

**Investigation.** Reading a whole module to answer one question costs the main
session thousands of tokens it then carries for the rest of the run.

**Adversarial review.** A reviewer in a fresh context sees the diff and the
criteria, not the reasoning that produced the code -- so it is not attached to
it. The four subagents in `.claude/agents/` are the review roles this project
actually needs: `spec-guardian`, `numerics-reviewer`, `test-author`,
`teaching-editor`.

A caution worth passing on: a reviewer asked to find gaps will find some, even
when the work is sound. Tell it to report only what affects correctness or a
stated requirement, and treat the rest as optional. Chasing every finding
produces defensive code and tests for cases that cannot happen.

## 5. What the verification loop actually caught

These are real bugs from this build, each found by a check rather than by
reading:

- **Connection sets merged across the hierarchy.** A composite component with
  its own connectors produced a plausible flat model in which the current
  simply did not have to flow into the sub-component. The test that caught it
  compared a two-resistor composite against `V/(R1+R2)`.
- **A single alias-elimination pass was not enough.** Substituting `gnd.p.v = 0`
  turns three-variable equations into two-variable aliases, which the pass had
  already walked past. The RC circuit was left with seven equations instead of
  four -- correct, but not the point of the exercise. Iterating to a fixed
  point fixed it.
- **`v = slope * time` treated as a constant.** The alias pass classified
  anything that was not an unknown as "known", which quietly included `time`,
  which made `der(v)` zero, which turned an index-2 model into a wrong index-1
  one. Caught by asserting that a capacitor across a *ramp* source is reported
  as high index.
- **A failed root find returned garbage silently.** SciPy's `fsolve` reports
  non-convergence through a return flag that is easy to ignore. The diode
  circuit produced `2e-322` volts and simulated happily.
- **Raising an exception inside LSODA's Fortran callback aborted the
  interpreter.** Once the root find *did* report failure, the failure killed
  Python outright. The default integrator is now `Radau`, which is pure Python.
- **Events fired forever at the same instant.** A `when` condition sits exactly
  on zero at the moment its event is handled, and integration restarts there --
  so the crossing is found again, and again. The cure is the small hysteresis
  band real simulators use.

Every one of those is a bug a careful human reviewer could miss and a running
check could not.

## 6. What to keep in CLAUDE.md, and what not to

`CLAUDE.md` is read at the start of every session, so it earns its length or it
costs you. The test for each line is: *would removing this cause a mistake?*

Here it holds: the virtual-environment path (there is no working `python3` on
this machine, which nothing in the code reveals), the test command, the naming
and comment style, and the non-negotiables -- index reduction stays out of
scope, errors are teaching material, every stage stays inspectable.

What it deliberately does not hold: descriptions of what each module does. That
is in the module docstrings, where it cannot go stale.

## 7. Honest limits

- Agents are good at the mechanical middle of a task and weakest exactly where
  this project is most interesting: deciding that alias elimination deserves to
  be a visible stage, or that a high-index model should fail loudly rather than
  be quietly repaired. Those were conversations, not delegations.
- The analytic tests were specified by a human who knew the answers. An agent
  asked to "write tests" will happily write tests that assert current
  behaviour, which locks in whatever bug is present.
- A long session degrades. Almost every mistake in this build happened late in
  a context window; `/clear` between stages is not a formality.

## Further reading

- [Best practices for Claude Code](https://code.claude.com/docs/en/best-practices)
- [Common workflows](https://code.claude.com/docs/en/common-workflows)
- [Subagents](https://code.claude.com/docs/en/sub-agents)
- [CLAUDE.md and memory](https://code.claude.com/docs/en/memory)
