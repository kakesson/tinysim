# The TinySim modeling language — specification v0.1

TinySim is a *tiny* equation-based, acausal modeling language for teaching.
It is a deliberately small subset of Modelica, with the same syntax and the same
semantics, so that everything students learn here transfers directly to
Modelica / Modia / Simscape.

The point of the tool is not to be a fast simulator. The point is that **every
intermediate result of the compilation pipeline can be printed and inspected**:
the flat equation set, the incidence matrix, the matching, the BLT sorting, and
the generated Python simulation code.

Model files use the extension `.tiny` (not `.mo`, to avoid confusion with real
Modelica files, since TinySim is only a subset).

---

## 1. Lexical rules

```modelica
// line comment
/* block comment */
```

* Statements and declarations end with `;`.
* Identifiers: `[A-Za-z_][A-Za-z_0-9]*`. Case sensitive.
* Numbers: `1`, `1.0`, `1e-3`, `9.81`.
* Description strings follow a declaration: `parameter Real R = 100 "resistance [Ohm]";`
* Whitespace and newlines are insignificant (unlike Python).
* Every block closes with `end`, never with a repeated keyword: a `when` block
  ends with `end;`, not `end when;`. A `model` or `connector` may repeat its own
  name for readability — `end Resistor;` — and the examples do, but a bare
  `end;` is equally valid.

## 2. Types and variability

There is exactly one data type: `Real`. (No Integer, Boolean, String, arrays or
records — use `0.0`/`1.0` where you would use a Boolean.)

A declaration has an optional *variability prefix*:

| prefix       | meaning                                                       |
|--------------|---------------------------------------------------------------|
| *(none)*     | continuous-time variable, an unknown of the equation system     |
| `parameter`  | constant during simulation, set at instantiation time           |
| `constant`   | constant, cannot be modified at instantiation                   |
| `discrete`   | piecewise constant; only changes inside a `when` clause         |

and, inside a connector, an optional *connection prefix*:

| prefix       | meaning                                                       |
|--------------|---------------------------------------------------------------|
| *(none)*     | potential ("across") variable — equal within a connection set  |
| `potential`  | explicit synonym for the above (a TinySim teaching extension)   |
| `flow`       | flow ("through") variable — sums to zero over a connection set |

Declarations may carry attribute modifiers:

```modelica
Real v(start = 0) "capacitor voltage";
```

Supported attributes: `start` (the initial value of a state, or the initial
guess for an algebraic variable), `min`, `max` and `nominal` (reporting only).

## 3. Connectors

```modelica
connector Pin
  Real v          "electrical potential [V]";
  flow Real i     "current into the component [A]";
end Pin;

connector Flange
  potential Real phi "angle [rad]";
  flow      Real tau "torque into the component [N.m]";
end Flange;

connector Signal   // only potential variables -> connect() is plain equality
  Real u;
end Signal;
```

**Sign convention:** a flow variable is *positive when flowing into the
component* through that connector. This is the convention that makes
`p.i + n.i = 0` mean "nothing accumulates inside the resistor".

### `connect` semantics

`connect(a, b)` does not compute anything. It records that two connectors
belong to the same *connection set*. After all `connect` statements are
collected, each connection set `{c1, c2, ..., cn}` generates:

* for every potential variable `v`:  `c1.v = c2.v`, `c1.v = c3.v`, ... (n-1 equations)
* for every flow variable `i`:       `c1.i + c2.i + ... + cn.i = 0`     (1 equation)

That is Kirchhoff's laws — and, with other variable names, Newton's third law,
mass balance, or energy balance. Showing that these three lines generate all of
them is one of the main teaching goals of TinySim.

An *unconnected* flow variable of an unconnected connector is set to zero.

## 4. Models and components

```modelica
model Resistor
  parameter Real R = 100 "resistance [Ohm]";
  Pin p, n;
  Real v "voltage drop p.v - n.v";
  Real i "current from p to n";
equation
  v = p.v - n.v;
  i = p.i;
  p.i + n.i = 0;
  v = R * i;
end Resistor;
```

Components are instantiated by declaring them, with optional modifiers,
including *nested* modifiers that reach into a sub-component's attributes:

```modelica
model Circuit
  Source    src(V = 10);
  Resistor  r(R = 100);
  Capacitor c(C = 1e-3, v(start = 0));   // nested modifier
  Ground    gnd;
equation
  connect(src.p, r.p);
  connect(r.n,  c.p);
  connect(c.n,  src.n);
  connect(src.n, gnd.p);
end Circuit;
```

Hierarchical composition is arbitrary in depth; flattening produces dotted
names such as `c.p.v`.

### Inheritance

A `partial model` may not be simulated on its own; `extends` copies the
declarations and equations of the base model into the derived model:

```modelica
partial model OnePort "shared by all two-pin electrical components"
  Pin p, n;
  Real v "voltage drop";
  Real i "current p -> n";
equation
  v = p.v - n.v;
  i = p.i;
  p.i + n.i = 0;
end OnePort;

model Resistor
  extends OnePort;
  parameter Real R = 100;
equation
  v = R * i;
end Resistor;
```

## 5. Equations

An equation is `expr = expr;` — **not** an assignment. `R * i = v;` and
`v = R * i;` are the same equation, and the compiler decides which variable to
solve each equation for. The rule for a well-posed model is

> number of equations == number of unknown continuous variables

(parameters, constants and `time` are not unknowns).

### Expressions

* arithmetic: `+  -  *  /  ^`, unary `-`
* relations: `<  <=  >  >=  ==  <>`
* logic: `and  or  not`
* conditional: `if cond then expr1 else expr2`
* functions: `sin cos tan asin acos atan atan2 exp log log10 sqrt abs sign tanh min max`
* `der(x)` — time derivative of `x`. Only first order, and only on a variable.
* `time` — the built-in independent variable.
* `pre(x)` — value of discrete variable `x` immediately before the current event.

### Initial equations

`start` attributes are the usual way to initialize. When the initial state must
itself be *computed*, an `initial equation` section adds equations that hold
only at `t = startTime`:

```modelica
model Tank
  parameter Real A = 1, k = 0.5, qin = 0.3;
  Real h, q;
initial equation
  der(h) = 0;              // start in steady state
equation
  A * der(h) = qin - q;
  q = k * sqrt(h);
end Tank;
```

The initialization problem is solved as its own nonlinear system, and TinySim
prints it separately so students see that initialization *is* a second,
different equation system.

## 6. Hybrid models

A `when` clause fires at the instant its condition becomes true
(a *rising edge*, false -> true). Its body may only

* assign a **discrete** variable: `on = 0;`
* `reinit(state, expr);` — jump the value of a continuous state.

```modelica
model BouncingBall
  parameter Real g = 9.81 "gravity";
  parameter Real e = 0.8  "restitution";
  Real h(start = 1) "height";
  Real v(start = 0) "velocity";
equation
  der(h) = v;
  der(v) = -g;
  when h < 0 then           // state event, located by the solver's root finder
    reinit(v, -e * v);
  end;
end BouncingBall;
```

```modelica
model Thermostat
  parameter Real Tset = 20, band = 1, k = 0.1, P = 5, Tamb = 5;
  Real T(start = 15);
  discrete Real on(start = 1);
equation
  der(T) = -k * (T - Tamb) + P * on;
  when T > Tset + band then on = 0; end;
  when T < Tset - band then on = 1; end;
end Thermostat;
```

Semantics: between events the model is a continuous ODE with all discrete
variables held constant. Conditions of all `when` clauses are handed to the
integrator as *zero-crossing functions*; at a crossing the integrator stops,
the `when` bodies fire (`pre(x)` refers to the pre-event values), and the
integration restarts from the updated state.

`when time > 2.0 then ...` is recognized as a *time event* and scheduled
exactly instead of being searched for.

## 7. Running an experiment

The language describes *models only*. It has no construct for stop time, step
size, solver settings or plotting: an experiment is ordinary Python code, where
the results already live.

```python
from tinysim import load, simulate, plot

model = load("examples/electrical.tiny", "RCCircuit")
res   = simulate(model, stop=1.0)
plot(res, ["c.v", "r.i"])
```

This is a deliberate separation: the `.tiny` file says what is *true* about the
system, the Python script says what you want to *do* with it.

## 8. Grammar (EBNF)

```ebnf
program        = { definition } ;
definition     = connector_def | model_def ;

connector_def  = "connector" IDENT { var_decl } "end" [ IDENT ] ";" ;
model_def      = [ "partial" ] "model" IDENT
                 { extends_clause | var_decl | comp_decl }
                 [ "initial" "equation" { equation } ]
                 [ "equation" { equation } ]
                 "end" [ IDENT ] ";" ;

extends_clause = "extends" IDENT [ modification ] ";" ;
var_decl       = { prefix } "Real" decl_item { "," decl_item } [ STRING ] ";" ;
comp_decl      = IDENT decl_item { "," decl_item } [ STRING ] ";" ;
prefix         = "parameter" | "constant" | "discrete" | "flow" | "potential" ;
decl_item      = IDENT [ modification ] [ "=" expr ] ;
modification   = "(" mod_item { "," mod_item } ")" ;
mod_item       = IDENT ( "=" expr | modification ) ;

equation       = connect_eq | when_eq | simple_eq ;
simple_eq      = expr "=" expr ";" ;
connect_eq     = "connect" "(" comp_ref "," comp_ref ")" ";" ;
when_eq        = "when" expr "then" { when_stmt } "end" ";" ;
when_stmt      = comp_ref "=" expr ";" | "reinit" "(" comp_ref "," expr ")" ";" ;

expr           = if_expr | logic_expr ;
if_expr        = "if" expr "then" expr "else" expr ;
logic_expr     = rel_expr { ( "and" | "or" ) rel_expr } ;
rel_expr       = add_expr [ ( "<" | "<=" | ">" | ">=" | "==" | "<>" ) add_expr ] ;
add_expr       = [ "-" ] term { ( "+" | "-" ) term } ;
term           = factor { ( "*" | "/" ) factor } ;
factor         = primary [ "^" factor ] ;
primary        = NUMBER | comp_ref | func_call | "(" expr ")" | "not" primary ;
func_call      = IDENT "(" [ expr { "," expr } ] ")" ;
comp_ref       = IDENT { "." IDENT } ;
```

## 9. Deliberately *not* in the language

Left out to keep the implementation readable by students:
arrays and matrices, records, Integer/Boolean/String types, `algorithm`
sections, user-defined functions, packages and imports, replaceable
components, `if`-equations (use `if`-expressions), stream connectors,
`inner`/`outer`, higher-order `der(der(x))`, and units checking.

TinySim also refuses (with an explanation) models of differential index > 1,
such as a pendulum written in Cartesian coordinates: it detects the structural
singularity and explains why real tools need Pantelides index reduction and
dummy derivatives.
