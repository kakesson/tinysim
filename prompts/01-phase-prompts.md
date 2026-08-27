# Phase prompts

The master prompt in one shot works, but it is not how this repository was
actually built, and not what to demonstrate to students. Each phase below is a
separate session with a clean context. Between phases: commit, then `/clear`.

---

## Phase 0 -- agree the language before writing any code

The mistake to avoid is letting the implementation define the language by
accident. Settle the syntax first, on paper.

```text
I want to build a tiny equation-based acausal modeling language for teaching,
in the spirit of Modelica, Simscape and Modia. Before any code exists I want
to agree its text format.

Interview me in detail using the AskUserQuestion tool. Ask about syntax
flavour, how far the structural analysis should go, what should be left out,
and what the examples should teach. Do not ask obvious questions; dig into
the decisions I will regret later.

Then write the specification to docs/language.md: lexical rules, connectors
and connect semantics, models and inheritance, equations, initialization,
hybrid constructs, a complete EBNF grammar, and an explicit list of what the
language deliberately does NOT support. Write one example model per teaching
point in examples/.
```

The decisions this actually settled, in about five minutes: Modelica-faithful
syntax with `Real` and semicolons; index-1 analysis only, with high index
detected and explained; blocks closed by a plain `end`; and no experiment or
plotting construct in the language at all -- experiments are Python.

## Phase 1 -- the plan

Plan mode (`Shift+Tab` until the status bar shows `plan mode on`).

```text
Read docs/language.md and examples/. Plan the implementation: the modules, what
each one receives and produces, and the order to build them in. For each module
say what its tests will assert. Do not write code yet.
```

## Phase 2..7 -- one stage per session

Each stage is small enough to hold in one context, and each ends with a green
test suite. The pattern per stage:

```text
Implement <stage> in tinysim/<module>.py, following docs/language.md.

Then use the test-author subagent to write tests for it, and run them.

The module docstring must say which pipeline stage this is, what it receives
and what it produces. Comments name the technique being used.
```

At the end of each stage, an independent check in a fresh context:

```text
Use the numerics-reviewer subagent to review tinysim/<module>.py. It should
construct concrete models that break the code and confirm them by running
them, not speculate.
```

## Phase 8 -- documentation and examples, after the code works

```text
Write docs/pipeline.md: the RC circuit walked through every stage, with the
real output at each one, aimed at a student who has never seen a modeling
compiler. Then write the experiment scripts in experiments/, each teaching one
idea and producing one figure.

Use the teaching-editor subagent on the result and apply what it finds worth
applying.
```

## Phase 9 -- the adversarial pass

```text
Use the spec-guardian subagent to find every place where the code, the examples
and docs/language.md disagree. Then fix the disagreements, changing whichever
side is wrong, and say which you chose and why.
```

---

## What each subagent is for

Defined in `.claude/agents/`, checked into the repository so a fresh clone has
them:

| Subagent | Runs when | Reads | Why it is separate |
| --- | --- | --- | --- |
| `spec-guardian` | after parser/flattener changes, before release | spec, code, examples | Comparing two large documents fills a context window; the finding list is small |
| `numerics-reviewer` | after analysis/codegen/simulator changes | the algorithmic modules | A fresh context is not attached to the code it just wrote |
| `test-author` | whenever a feature lands | the module and existing tests | Keeps the "assert against mathematics" rule in one place |
| `teaching-editor` | after a module or doc is written | the prose and the comments | Judges readability without the author's knowledge of intent |
