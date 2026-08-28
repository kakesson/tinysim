"""
Assume-guarantee contracts: the syntax, the desugaring, and the verdicts.

The monitoring tests use a model whose solution is `x(t) = t`, so every
robustness value in them can be worked out on paper.
"""

import pytest

import tinysim
from tinysim.contracts import to_stl, to_text
from tinysim.flatten import ModelError
from tinysim.lexer import TinySimSyntaxError
from tinysim.monitor import NOT_TESTED, SATISFIED, VIOLATED
from tinysim.parser import parse

RAMP = """
model Ramp
  parameter Real rate = 1;
  Real x(start = 0);
equation
  der(x) = rate;
end Ramp;
"""


def contract_of(clauses: str, section: str = "guarantee", model: str = RAMP):
    source = f"{model}\ncontract C for Ramp\n{section}\n{clauses}\nend C;\n"
    return parse(source).contracts["C"]


def check(clauses: str, stop: float = 3.0, points: int = 301,
          assume: str = "", model: str = RAMP, name: str = "Ramp"):
    """Compile a model with one contract, simulate it, and check it."""
    sections = (f"assume\n{assume}\n" if assume else "") + f"guarantee\n{clauses}\n"
    source = f"{model}\ncontract C for {name}\n{sections}end C;\n"
    compiled = tinysim.load_source(source, name)
    result = tinysim.simulate(compiled, stop=stop, points=points)
    return tinysim.check_contracts(compiled, result).results[0]


# ---------------------------------------------------------------------------
# Syntax: what was written, and what it means
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("written, stl", [
    ("always x > 0", "G(x > 0)"),
    ("never x > 5", "G(!(x > 5))"),
    ("eventually within [0, 2] x > 1", "F[0, 2](x > 1)"),
    ("during [1, 2] x > 0", "G[1, 2](x > 0)"),
    ("after 2 always x > 1", "G[2, end](x > 1)"),
    ("x stays within [0, 3]", "G(x >= 0 & x <= 3)"),
    ("x settles to 3 within 0.1 after 2", "G[2, end](abs(x - 3) <= 0.1)"),
    ("whenever x > 1 then x > 0 within [0, 1]",
     "G(rise(x > 1) -> (F[0, 1](x > 0)))"),
    ("whenever x > 1 then x > 0 holds for 1",
     "G(rise(x > 1) -> (G[0, 1](x > 0)))"),
    ("at start x <= 0", "at_start(x <= 0)"),
    ("at end x >= 2", "at_end(x >= 2)"),
    ("x > 0 until within [0, 3] x > 2", "x > 0 U[0, 3] x > 2"),
    ("always x > 0 implies x > -1", "G(x > 0 -> x > -1)"),
])
def test_every_pattern_desugars_to_the_expected_logic(written, stl):
    clause = contract_of(f"  {written};").guarantees[0]
    assert clause.stl == stl


def test_a_temporal_operator_scopes_over_everything_after_it():
    """`always a and b` means `always (a and b)`, the way it reads aloud."""
    wide = contract_of("  always x > 0 and x < 9;").guarantees[0]
    assert wide.stl == "G(x > 0 & x < 9)"
    narrow = contract_of("  (always x > 0) and x < 9;").guarantees[0]
    assert narrow.stl == "(G(x > 0)) & x < 9"


def test_the_written_form_survives_a_round_trip():
    clause = contract_of("  whenever x > 1 then x > 0 within [0, 1];").guarantees[0]
    assert clause.written == "whenever x > 1 then x > 0 within [0, 1]"


# ---------------------------------------------------------------------------
# Things that should be refused
# ---------------------------------------------------------------------------

def test_a_clause_must_compare_something():
    with pytest.raises(TinySimSyntaxError, match="must compare something"):
        contract_of("  always x;")


def test_a_contract_must_say_something():
    with pytest.raises(TinySimSyntaxError, match="says nothing"):
        parse(RAMP + "contract C for Ramp\nend C;\n")


def test_an_unterminated_contract_is_reported():
    with pytest.raises(TinySimSyntaxError, match="unterminated contract"):
        parse(RAMP + "contract C for Ramp\nguarantee\n  always x > 0;\n")


def test_a_contract_for_an_unknown_model_is_reported():
    source = RAMP + "contract C for Nothing\nguarantee always x > 0;\nend C;\n"
    with pytest.raises(ModelError, match="not a class in this file"):
        tinysim.load_source(source, "Ramp")


def test_a_typo_in_a_contract_fails_before_anything_runs():
    source = RAMP + "contract C for Ramp\nguarantee always velocity > 0;\nend C;\n"
    with pytest.raises(ModelError, match="'velocity' is not a variable"):
        tinysim.load_source(source, "Ramp")


# ---------------------------------------------------------------------------
# Robustness, on a model whose answer is x(t) = t
# ---------------------------------------------------------------------------

def test_always_reports_the_worst_moment():
    result = check("  always x <= 2;")
    assert result.verdict == VIOLATED
    assert result.guarantees[0].margin == pytest.approx(-1.0)     # 2 - 3
    assert result.guarantees[0].at_time == pytest.approx(3.0)


def test_a_satisfied_clause_reports_how_much_room_there_was():
    result = check("  always x <= 5;")
    assert result.verdict == SATISFIED
    assert result.guarantees[0].margin == pytest.approx(2.0)      # 5 - 3
    assert result.guarantees[0].at_time == pytest.approx(3.0)


def test_eventually_reports_the_best_moment_in_the_window():
    result = check("  eventually within [0, 1] x >= 0.5;")
    assert result.guarantees[0].margin == pytest.approx(0.5)      # 1 - 0.5
    assert result.guarantees[0].at_time == pytest.approx(1.0)


def test_at_start_and_at_end_look_only_there():
    assert check("  at start x <= 0.1;").guarantees[0].margin == pytest.approx(0.1)
    assert check("  at end x >= 2.9;").guarantees[0].margin == pytest.approx(0.1)


def test_stays_within_takes_the_tighter_of_the_two_bounds():
    result = check("  x stays within [-1, 5];")
    assert result.guarantees[0].margin == pytest.approx(1.0)      # x - (-1) at t=0
    assert result.guarantees[0].at_time == pytest.approx(0.0)


def test_until_needs_the_second_condition_to_arrive():
    assert check("  x < 9 until within [0, 3] x >= 2.5;").verdict == SATISFIED
    assert check("  x < 9 until within [0, 1] x >= 2.5;").verdict == VIOLATED


def test_equality_is_satisfied_exactly_with_no_room():
    source = """
    model Flag
      Real x(start = 0);
      discrete Real on(start = 1);
    equation
      der(x) = 1;
      when x > 1 then on = 0; end;
    end Flag;
    """
    result = check("  at start on == 1;", model=source, name="Flag")
    assert result.verdict == SATISFIED
    assert result.guarantees[0].margin == 0
    assert result.guarantees[0].margin_text == "+0"


# ---------------------------------------------------------------------------
# The three verdicts
# ---------------------------------------------------------------------------

def test_a_broken_assumption_makes_the_run_prove_nothing():
    result = check("  always x <= 0;", assume="  always rate <= 0.5;")
    assert result.verdict == NOT_TESTED
    assert "nothing was promised" in result.notes[0]
    # The guarantee did fail, but that is not the component's fault.
    assert not result.guarantees[0].satisfied


def test_a_kept_assumption_puts_the_guarantee_on_the_hook():
    assert check("  always x <= 5;", assume="  always rate <= 2;").verdict == SATISFIED
    assert check("  always x <= 1;", assume="  always rate <= 2;").verdict == VIOLATED


def test_a_trigger_that_never_fires_is_reported_as_proving_nothing():
    result = check("  whenever x > 99 then x > 0 within [0, 1];")
    assert result.verdict == SATISFIED               # vacuously
    assert result.guarantees[0].vacuous
    assert any("never triggered" in note for note in result.notes)


def test_a_window_that_outlives_the_run_is_reported():
    result = check("  eventually within [0, 10] x >= 1;", stop=3.0)
    assert any("run ends before the window" in note for note in result.notes)


# ---------------------------------------------------------------------------
# Contracts belong to a class, so they apply to every instance of it
# ---------------------------------------------------------------------------

TWO_RESISTORS = """
connector Pin
  Real v;
  flow Real i;
end Pin;

model Resistor
  Pin p, n;
  parameter Real R = 100;
  Real v, i;
equation
  v = p.v - n.v;
  i = p.i;
  p.i + n.i = 0;
  v = R * i;
end Resistor;

model Source
  Pin p, n;
  parameter Real V = 10;
  Real v, i;
equation
  v = p.v - n.v;
  i = p.i;
  p.i + n.i = 0;
  v = V;
end Source;

model Ground
  Pin p;
equation
  p.v = 0;
end Ground;

model Divider
  Source src(V = 10);
  Resistor r1(R = 100);
  Resistor r2(R = 900);
  Ground gnd;
equation
  connect(src.p, r1.p);
  connect(r1.n, r2.p);
  connect(r2.n, src.n);
  connect(src.n, gnd.p);
end Divider;

contract SmallDrop for Resistor
  "a resistor in this design drops at most 5 V"
assume
  always abs(i) <= 1;
guarantee
  always abs(v) <= 5;
end SmallDrop;
"""


def test_a_component_contract_is_checked_once_per_instance():
    model = tinysim.load_source(TWO_RESISTORS, "Divider")
    assert [instance for _, instance in model.contract_instances] == ["r1", "r2"]

    report = tinysim.check_contracts(model, tinysim.simulate(model, stop=1.0,
                                                             points=5))
    by_name = {result.instance: result for result in report.results}
    # r1 drops 1 V and keeps its promise; r2 drops 9 V and does not.
    assert by_name["r1"].verdict == SATISFIED
    assert by_name["r1"].guarantees[0].margin == pytest.approx(4.0)
    assert by_name["r2"].verdict == VIOLATED
    assert by_name["r2"].guarantees[0].margin == pytest.approx(-4.0)
    assert report.summary() == "1 satisfied, 1 violated, 0 not tested"


def test_the_names_in_a_component_contract_are_moved_into_the_instance():
    model = tinysim.load_source(TWO_RESISTORS, "Divider")
    contract, instance = model.contract_instances[0]
    assert instance == "r1"
    assert contract.guarantees[0].written == "always abs(r1.v) <= 5"


# ---------------------------------------------------------------------------
# The examples, and the link back to event detection
# ---------------------------------------------------------------------------

def test_the_shipped_examples_keep_their_contracts(examples):
    for name, options in [("electrical.tiny", dict(stop=1.0)),
                          ("thermostat.tiny", dict(stop=200.0, points=4001)),
                          ("tank.tiny", dict(stop=20.0)),
                          ("dcmotor.tiny", dict(stop=3.0, points=3001)),
                          ("bouncing_ball.tiny", dict(stop=3.0))]:
        model = tinysim.load(examples / name)
        report = tinysim.check_contracts(model, tinysim.simulate(model, **options))
        assert report.all_satisfied, f"{name}: {report.summary()}"
        assert not report.not_tested, f"{name}: something went untested"


def test_switching_event_detection_off_breaks_the_ball_contract(examples):
    """The contract turns "the plot looks wrong" into a number."""
    model = tinysim.load(examples / "bouncing_ball.tiny", "BouncingBall")
    result = tinysim.simulate(model, stop=3.0, method="rk4", step=1e-3,
                              events="off")
    report = tinysim.check_contracts(model, result)
    assert report.violated
    failing = report.results[0].failing
    assert "h >= -0.001" in failing.clause.written
    assert failing.margin < -40                      # the ball is 43 m down
