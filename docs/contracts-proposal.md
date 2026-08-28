# Assume-guarantee contracts for TinySim -- a proposal

> **Status: proposal, not implemented.** This document is here to be argued
> with. Nothing in `tinysim/` reads it yet.

The idea: every model can carry a **contract** saying what it needs from its
environment (**assume**) and what it promises in return (**guarantee**), written
so that an engineer who has never seen temporal logic can read it, and given a
precise meaning by **Signal Temporal Logic** underneath.

---

## 1. Where this comes from

| Source | What is taken from it |
| --- | --- |
| **Signal Temporal Logic** (Maler & Nickovic 2004) and its *robust* semantics (Fainekos & Pappas 2009, Donzé & Maler 2010) | the meaning of every operator, and the *margin*: a real number saying how close a run came to violating the property, in the units of the signal |
| **Assume-guarantee contract theory** (Benveniste et al., *Contracts for System Design*) | the shape `C = (A, G)`, what it means to satisfy one, and how a component contract is discharged by its environment |
| **FRET / FRETish** (NASA) and **EARS** | the readable surface: a requirement is *scope, condition, timing, response*, written as a sentence, and translated to logic by the tool rather than by the engineer |
| **Modelica_Requirements / FORM-L** (Otter, Thuy, Bouskela et al.) | three-valued verdicts: *satisfied*, *violated*, and **not tested** -- and the insistence that an untested requirement is a finding, not a pass |

The one thing all four agree on, and which this proposal takes seriously: the
formal notation should not be what the user writes.

## 2. The shape of a contract

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

* `contract <Name> for <Model>` -- a contract belongs to a *class*, so it
  travels with every instance of that class. A `Resistor` contract is checked
  for `r1` and for `r2` separately, under the environment each of them actually
  had.
* The description string is not decoration: the report prints it as the
  headline, and the formal clauses underneath as the detail.
* `assume` and `guarantee` are both lists of clauses; a list means "all of
  them" (conjunction). Either section may be empty; an empty `assume` means
  "in any environment".
* Clauses refer to **variables and parameters of the model by their own
  names** -- `c.v`, `src.V`, `der(h)`, `time`, and anything else the model
  declares. A name that does not exist is a compile-time error, exactly like a
  typo in an equation.

### What a contract *means*

For a run of the model:

| | |
| --- | --- |
| assumptions all hold, guarantees all hold | **satisfied** |
| assumptions all hold, some guarantee fails | **violated** -- and this is the component's fault |
| some assumption fails | **not tested** -- the environment was outside the contract, so nothing was promised. Reported as a warning, never as a pass |

That is the assume-guarantee reading: a contract is a promise of the form
*A ⟹ G*, and a run where *A* is false says nothing about the component.

## 3. The readable layer

Three levels, so a course can climb them in order. Every level is the same
logic underneath; the tool prints the translation.

### Level 1 -- bounds, which need no temporal logic at all

```modelica
  always h >= 0;                        // never below the floor
  never  T > 100;                       // never boils
  T stays within [18, 22];              // shorthand for the two bounds
  at start v == 0;                      // a condition on the initial state
  at end  abs(load.w - 160) <= 1;       // where it must have got to
```

### Level 2 -- patterns, the ones engineers actually write

```modelica
  eventually within [0, 0.5] c.v >= 9.5;
  after 60 always T >= 19;
  whenever h < 0 then v >= 0 within [0, 0.01];
  whenever on == 1 then T rises for 5;
  during [1, 2] abs(l.i) <= 60;
  T settles to 20 within 0.5 after 120;
```

Each is a sentence with the FRETish fields in it: *when* (scope/condition),
*by when* (timing), *what* (response).

### Level 3 -- the operators themselves, for when the patterns run out

```modelica
  always (r.i > 0 implies eventually within [0, 0.2] c.v > 5);
  (T < 21) until within [0, 60] (on == 0);
```

### The translation, which the tool always shows

| written | means, in STL |
| --- | --- |
| `always φ` | `G φ` over the whole run |
| `always within [a, b] φ` | `G[a,b] φ` |
| `eventually within [a, b] φ` | `F[a,b] φ` |
| `never φ` | `G ¬φ` |
| `after t always φ` | `G[t,∞) φ` |
| `during [a, b] φ` | `G[a,b] φ` |
| `whenever c then r within [a, b]` | `G (rise(c) → F[a,b] r)` |
| `whenever c then r holds for d` | `G (rise(c) → G[0,d] r)` |
| `φ until within [a, b] ψ` | `φ U[a,b] ψ` |
| `x stays within [lo, hi]` | `G (lo ≤ x ∧ x ≤ hi)` |
| `x settles to v within tol after t` | `G[t,∞) (\|x − v\| ≤ tol)` |
| `at start φ` / `at end φ` | `φ` at the first / last point |

Time bounds are **relative to where the operator is evaluated**: at the top
level that is the start of the run; inside `whenever`, it is the instant the
trigger became true. Bounds are plain numbers in the model's time unit, as
everywhere else in TinySim.

Additional primitives: `rise(c)`, `fall(c)`, `initial(x)` (the value of `x` at
the start time), and every function the equation language already has --
`abs`, `sqrt`, `sin`, `max`, ...

## 4. The margin

Every clause yields a number as well as a verdict: the **robustness**, the
standard quantitative semantics of STL.

```
  ρ(x > c)      = x − c                    in the units of x
  ρ(¬φ)         = −ρ(φ)
  ρ(φ ∧ ψ)      = min(ρ(φ), ρ(ψ))
  ρ(G[a,b] φ)   = inf over the window
  ρ(F[a,b] φ)   = sup over the window
```

Positive means satisfied, and *how much room there was*; negative means
violated, and *by how much*. This is what makes the output useful to a
non-expert:

```
  ChargesInTime                                   satisfied
    the capacitor reaches 95 % of the source voltage within half a second
    assume    always 5 <= src.V <= 15                  margin 5.00 V
    guarantee eventually within [0, 0.5] c.v >= 9.5    margin 0.42 V  at t = 0.30 s
    guarantee always c.v <= src.V                      margin 0.07 V  at t = 1.00 s  <- closest call
```

## 5. Composition: what happens in a system

This is the part that earns the name *contract*. When a model is built from
components that carry contracts:

```modelica
contract WithinCurrentLimit for Inductor
  "an inductor tolerates this much voltage, and then keeps its current bounded"
assume    always abs(v) <= 30;
guarantee always abs(i) <= 60;
end WithinCurrentLimit;
```

then simulating `DCMotor` checks, for the instance `l`:

1. **Is the assumption discharged?** Did the rest of the system keep
   `abs(l.v) <= 30`? If not, the report says so -- the *system* is misusing the
   component, and that is a different finding from a broken promise.
2. **Is the guarantee kept?** Only asked when the assumption held.

The system's own contract is then checked on the same run, and the report puts
the two side by side: *these component assumptions were discharged, these
guarantees held, and the system contract held*. That is a
simulation-level version of the compositional argument, and it makes the point
of contract theory concrete without any algebra.

## 6. What this can and cannot do -- said plainly

Monitoring a simulation run can **falsify** a contract, and it can build
confidence. It can never **prove** one: a contract that holds on ten runs may
fail on the eleventh. The reports will say "held on this run", never "verified".

Two further honest limits, both worth teaching:

* The monitor sees the **output points**, so a violation narrower than the
  output interval can be missed -- the same lesson as event detection, one
  level up. Contract checking will therefore report the output interval it
  used.
* `whenever` clauses whose trigger never fired are reported as **vacuous**, not
  as passes.

## 7. How it would fit into what exists

* **Parsing** -- one more top-level definition next to `model` and `connector`.
* **Checking** -- `tinysim check` resolves every name in a contract against the
  flat model, so typos fail before anything runs.
* **Monitoring** -- a new stage after simulation, working on the
  `SimulationResult`, producing a verdict and a margin per clause.
* **Reports** -- a `contracts` section in the terminal report and in the HTML
  page: the description, the English form, the STL translation, the verdict,
  the margin, and the worst instant; plots shade the region where a guarantee
  failed.
* **Python** -- `tinysim.check_contracts(model, result)`.

Roughly: a `contracts.py` for the syntax tree and the desugaring, a
`monitor.py` for the robustness semantics, and sections in `report.py` and
`htmlreport.py`. It is a comparable amount of code to `analysis.py` plus
`report.py`, and it touches nothing that exists.

## 8. Worked examples

```modelica
contract KeepsTheRoomComfortable for Thermostat
  "the room reaches the band quickly and then stays in it"
assume
  always Tamb < Tset - band;             // the room can actually get cold
  P > 0;                                 // there is a heater
guarantee
  eventually within [0, 60] T >= Tset - band;
  after 60 T stays within [Tset - band - 0.5, Tset + band + 0.5];
  whenever T > Tset + band then on == 0 within [0, 0.1];
end KeepsTheRoomComfortable;


contract NeverSinksThroughTheFloor for BouncingBall
  "the ball stays on top of the floor and never gains energy"
assume
  at start h > 0;
  0 < e and e < 1;
guarantee
  always h >= -0.001;
  always g * h + v ^ 2 / 2 <= g * initial(h) + 1e-9;
end NeverSinksThroughTheFloor;


contract ReachesSpeed for DCMotor
  "the shaft reaches its rated speed without overcurrent"
assume
  always src.V >= 20 and src.V <= 28;
guarantee
  eventually within [0, 2] load.w >= 150;
  always abs(l.i) <= 60;
  after 2 load.w settles to 160 within 2;
end ReachesSpeed;
```

The bouncing-ball contract is the interesting one for the course: run it with
`events="off"` and the first guarantee fails, with a margin of −43 m at
t = 3 s. The contract turns "the plot looks wrong" into a number.
