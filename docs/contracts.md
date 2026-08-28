# Assume-guarantee contracts

A model can carry a **contract**: what it needs from its environment, and what
it promises in return.

```modelica
contract ChargesInTime for RCCircuit
  "the capacitor reaches 95 % of the source voltage within half a second"
assume
  always src.V >= 5 and src.V <= 15;      // the source stays in its rated range
guarantee
  eventually within [0, 0.5] c.v >= 0.95 * src.V;
  always c.v <= src.V;                    // and it never overshoots
  never abs(r.i) > 0.2;                   // nor draws more than 200 mA
end ChargesInTime;
```

```bash
tinysim run examples/electrical.tiny --stop 1 --contracts
```

```
  ChargesInTime   [SATISFIED]
    "the capacitor reaches 95 % of the source voltage within half a second"
    assume    always (src.V >= 5 and src.V <= 15)   margin         +5 at t = 0
    guarantee eventually within [0, 0.5] c.v >= 0.95 * src.V
                                                    margin     +0.462 at t = 0.5
    guarantee always c.v <= src.V                   margin +0.0004541 at t = 1
    guarantee never abs(r.i) > 0.2                  margin       +0.1 at t = 0
```

Read the third line again: that requirement is true by **half a millivolt**.
A pass/fail test would have printed a tick.

---

## 1. What a contract means

A contract is the pair `(A, G)`, and it is read as **A implies G**. A run in
which an assumption fails is not evidence about the component, so there are
three verdicts, not two:

| | verdict |
| --- | --- |
| assumptions hold, guarantees hold | **satisfied** |
| assumptions hold, a guarantee fails | **violated** -- the component's fault |
| an assumption fails | **not tested** -- the environment was outside the contract |

The third is the one a pass/fail test cannot express, and it is usually the
most informative: it points at the *system*, not at the component.

## 2. Where the ideas come from

| | |
| --- | --- |
| [Signal Temporal Logic](https://doi.org/10.1007/978-3-540-30206-3_12) and its robust semantics (Fainekos & Pappas; Donzé & Maler) | the meaning of every operator, and the margin |
| [Assume-guarantee contract theory](https://www.semanticscholar.org/paper/Contracts-for-Systems-Design:-Theory-Benveniste-Caillaud/0ba4f16bed2262591d4233685c51229501c74715) (Benveniste et al.) | the shape `(A, G)`, and what discharging an assumption means |
| [FRET / FRETish](https://ntrs.nasa.gov/api/citations/20200001989/downloads/20200001989.pdf) (NASA) and EARS | the readable surface: *condition, timing, response*, written as a sentence |
| [Modelica_Requirements / FORM-L](https://github.com/modelica-3rdparty/Modelica_Requirements) (Otter, Thuy, Bouskela et al.) | three-valued verdicts, and treating "not tested" as a finding |

## 3. Writing one

```
contract <Name> for <Model>
  "<what it is for, in words>"
assume
  <clause>;  ...
guarantee
  <clause>;  ...
end <Name>;
```

A contract belongs to a **class**, so it travels with every instance of that
class -- see section 6. Both sections are optional and both are lists; a list
means *all of them*. An empty `assume` means "in any environment".

Clauses name the model's own variables and parameters -- `c.v`, `src.V`,
`der(h)`, `time` -- and a name that does not exist is a compile-time error,
exactly like a typo in an equation:

```
error: contract 'ChargesInTime', line 5: 'c.voltage' is not a variable or
       parameter of this model
```

### Level 1: bounds, which need no temporal logic at all

```modelica
  always h >= 0;                          // never below the floor
  never T > 100;                          // never boils
  T stays within [18, 22];
  at start v == 0;                        // a condition on the initial state
  at end abs(load.w - 160) <= 1;          // where it must have got to
```

### Level 2: patterns, which is what requirements usually are

```modelica
  eventually within [0, 0.5] c.v >= 9.5;
  after 60 always T >= 19;
  during [1, 2] abs(l.i) <= 60;
  whenever T > Tset + band then on == 0 within [0, 0.1];
  whenever on == 1 then T >= 15 holds for 5;
  load.w settles to 160 within 8 after 2;
```

### Level 3: the operators, for when the patterns run out

```modelica
  always (r.i > 0 implies eventually within [0, 0.2] c.v > 5);
  c.v < 9 until within [0, 1] c.v >= 9.5;
```

### Scope

**A temporal operator applies to everything that follows it**, `and` and `or`
included, because that is how it reads aloud:

```modelica
  always x > 0 and y > 0        // means  always (x > 0 and y > 0)
  (always x > 0) and y > 0      // parentheses stop it
```

`not` is the exception and binds tightly.

### The translation, which the tool always shows

| written | means |
| --- | --- |
| `always φ` | `G(φ)` over the rest of the run |
| `always within [a, b] φ` | `G[a,b](φ)` |
| `eventually within [a, b] φ` | `F[a,b](φ)` |
| `never φ` | `G(!(φ))` |
| `after t always φ` | `G[t,end](φ)` |
| `during [a, b] φ` | `G[a,b](φ)` |
| `whenever c then r within [a, b]` | `G(rise(c) -> F[a,b](r))` |
| `whenever c then r holds for d` | `G(rise(c) -> G[0,d](r))` |
| `φ until within [a, b] ψ` | `φ U[a,b] ψ` |
| `x stays within [lo, hi]` | `G(x >= lo & x <= hi)` |
| `x settles to v within tol after t` | `G[t,end](abs(x - v) <= tol)` |
| `at start φ` / `at end φ` | `φ` at the first / last output point |

Time bounds are **relative to where the operator is evaluated**: at the top
level that is the start of the run; inside `whenever`, the instant the trigger
became true. Bounds may be numbers or expressions of parameters.

`tinysim show FILE --stages contracts` prints both forms, one under the other.

## 4. The margin

Every clause yields a number as well as a verdict: the robustness of the
formula, in the units of the signal.

```
  rho(x > c)      = x - c
  rho(not φ)      = -rho(φ)
  rho(a and b)    = min(rho(a), rho(b))
  rho(a or b)     = max(rho(a), rho(b))
  rho(G[a,b] φ)   = the smallest rho(φ) in the window
  rho(F[a,b] φ)   = the largest  rho(φ) in the window
```

Positive means satisfied, and says how much room there was; negative means
violated, and says by how much. The report also gives the instant responsible
-- the moment of the closest call, or of the failure.

Two conventions worth knowing:

* `x == c` has robustness `-|x - c|`, so an equality that holds is satisfied
  *exactly*, with a margin of zero. That is honest: there is no room.
* Triggers are **crisp**. `whenever c then r within [0, 2]` reports the margin
  of the *response*; how close `c` came to being true is a different question,
  and mixing the two would give a number that means neither.

## 5. What monitoring can and cannot do

Checking a contract against a run can **falsify** it, and can build confidence.
It cannot **verify** it: a contract that holds on ten runs may fail on the
eleventh. The reports say "checked on one run", never "verified".

Two limits are reported rather than hidden:

* The monitor sees the **output points**. A violation narrower than the output
  interval can pass unnoticed -- the same lesson as event detection, one level
  up. The report prints the interval it used.
* A `whenever` whose trigger never fired is reported as **vacuous**: it proves
  nothing, and it is not a pass. So is a window that outlives the run.

## 6. Composition: contracts of the parts

A contract on a component class is checked once **per instance**, against the
environment that instance actually had:

```modelica
contract WithinCurrentLimit for Inductor
  "the winding tolerates this voltage, and keeps its current bounded"
assume    always abs(v) <= 30;
guarantee always abs(i) <= 60;
end WithinCurrentLimit;
```

Simulating the DC motor then reports:

```
  l : WithinCurrentLimit   [SATISFIED]
    assume    always abs(l.v) <= 30      margin     +6 at t = 0
    guarantee always abs(l.i) <= 60      margin +19.74 at t = 0.272
```

The system kept the inductor inside its rated voltage, so the inductor owed its
current bound -- and kept it. Double the supply and the same report reads:

```
  l : WithinCurrentLimit   [NOT TESTED]
    assume    always abs(l.v) <= 30      margin    -18 at t = 0
    note: the assumption 'always abs(l.v) <= 30' failed at t = 0,
          so nothing was promised on this run
  ReachesSpeed             [VIOLATED]
    note: 'always abs(l.i) <= 60' fails by 20.51 at t = 0.272
```

Which is the whole point: the system is at fault, not the component. That
distinction is what contract theory is for, and here it costs one extra
verdict.

## 7. Where to see one

Every example that can be simulated carries a contract, and every generated
report shows it:

| example | what it promises |
| --- | --- |
| `electrical.tiny` | the capacitor charges in time and never overshoots -- plus a per-instance contract on the resistor class |
| `thermostat.tiny` | the room reaches the band, stays in it, and the heater switches when it should |
| `bouncing_ball.tiny` | the ball stays on top of the floor and never gains energy |
| `tank.tiny` | started in steady state, the level does not drift |
| `dcmotor.tiny` | the shaft reaches its rated speed -- and two component contracts underneath it |
| `pendulum.tiny` | a damped pendulum never gains energy and its swing dies away |
| `resistor_network.tiny` | the capacitor charges to the divider voltage, drawing at most 50 mA |
| `diode_circuit.tiny` | the diode conducts one way only, and Kirchhoff holds around the branch |
| `pendulum_cartesian.tiny` | the mass stays at the end of the rod -- the requirement is fine; it is the same fact that makes the model impossible to solve |

## 8. Using it

```bash
tinysim show FILE --stages contracts        # what the contracts say, and mean
tinysim run  FILE --contracts               # check them; exit code 1 if violated
```

```python
model  = tinysim.load("examples/dcmotor.tiny", "DCMotor")
result = tinysim.simulate(model, stop=3.0)
report = tinysim.check_contracts(model, result)

print(report.summary())                     # '2 satisfied, 1 violated, 0 not tested'
for item in report.results:
    print(item.title, item.verdict)
    for clause in item.guarantees:
        print("   ", clause.clause.written, clause.margin_text, clause.at_time)
```

The HTML reports carry a **Contracts** section with the same material:
requirement, translation, verdict, margin and instant.
`experiments/09_contracts.py` walks through all of it, including the bouncing
ball whose contract passes with event detection on, fails by a millimetre when
events are noticed a step late, and fails by 43 metres when they are ignored.

## 9. Checked by somebody else's implementation

A monitor you wrote yourself is readable, which is not the same as right. The
same clauses can be handed to
[SignalTemporalLogic.jl](https://github.com/sisl/SignalTemporalLogic.jl), an
independent implementation from the Stanford Intelligent Systems Laboratory:

```bash
python -c "import tinysim.stl_julia as j; j.install()"     # once, needs Julia
tinysim run examples/electrical.tiny --stop 1 --contracts --stl-backend julia
```

```python
builtin, julia, differences = tinysim.cross_check_contracts(model, result)
max(differences.values())        # 0.0 on every example that ships with TinySim
```

There is no Python dependency and nothing to configure: TinySim generates a
Julia program, runs it with the `julia` binary, and reads the numbers back.
The program is printed on request -- generated Julia next to generated Python,
for the same reason:

```julia
x = [parse.(Float64, split(line)) for line in eachline(ARGS[1])]

formulas = [
    ("|ChargesInTime|assume|76",    @formula □((xt -> (xt[3] - 5.0) > 0) ∧ (xt -> (xt[3] - 15.0) < 0))),
    ("|ChargesInTime|guarantee|78", @formula ◊(1:51, xt -> (xt[1] - (0.95 * xt[3])) > 0)),
    ("|ChargesInTime|guarantee|79", @formula □(xt -> (xt[1] - xt[3]) < 0)),
    ("|ChargesInTime|guarantee|80", @formula □(¬(xt -> (abs(xt[2]) - 0.2) > 0))),
]

for (label, phi) in formulas
    println(label, "\t", ρ(x, phi))
end
```

Two details make the translation exact rather than approximate. That library
indexes **samples** rather than time, and evaluates a formula once over the
whole trace -- which is what a contract clause means here -- so a window in
seconds becomes the range of sample indices whose times fall inside it, with no
resampling. And its predicates compare a function of the sample against a
constant, with robustness `mu(xt) - c`, so `lhs op rhs` is written as
`(lhs - rhs) op 0` and the robustness is the one TinySim uses. On every example
in this repository the two implementations agree to the last bit.

What the library cannot express is reported rather than papered over: it has no
rising edge, and a temporal operator there applies to a sample rather than to a
sub-signal, so `whenever ... then ... within ...` is out of its reach. Those
clauses fall back to TinySim's own monitor and say so in the report.

## 10. Where it lives

| | |
| --- | --- |
| `tinysim/contracts.py` | the syntax tree, the desugaring, and both printed forms |
| `tinysim/parser.py` | one more top-level definition, and the formula ladder |
| `tinysim/monitor.py` | robustness over a run, and the verdicts |
| `tinysim/__init__.py` | `attach_contracts`: which contract applies to what, and the name check |
| `tinysim/stl_julia.py` | the translation to SignalTemporalLogic.jl, and the cross-check |
| `tinysim/report.py`, `htmlreport.py` | the contracts section |
