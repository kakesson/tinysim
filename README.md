# TinySim

A tiny equation-based, acausal modeling language and simulator, for teaching.

TinySim is a small subset of [Modelica](https://modelica.org) with the same
syntax and semantics, implemented in readable, commented Python. Its purpose is
not speed: it is that **every step from model text to simulation result can be
printed and inspected** — flattening, the incidence matrix, the matching, the
BLT sorting, the generated simulation code, and the event handling.

See [`docs/language.md`](docs/language.md) for the language specification and
[`examples/`](examples/) for models.

Status: language specification agreed, implementation in progress.
