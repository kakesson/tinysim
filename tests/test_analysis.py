"""Alias elimination, matching, and BLT sorting."""

import pytest

import tinysim
from tinysim.alias import eliminate_aliases
from tinysim.analysis import StructuralError, analyze, der_name
from tinysim.flatten import flatten
from tinysim.parser import parse

from conftest import ELECTRICAL, RC_CIRCUIT


def test_alias_elimination_shrinks_the_rc_circuit():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    assert len(model.flat.equations) == 20
    assert len(model.model.equations) < len(model.flat.equations)
    # Both forms describe the same system and both must be solvable.
    unsimplified = tinysim.load_source(RC_CIRCUIT, "RC",
                                       eliminate_alias_equations=False)
    assert len(unsimplified.analysis.blocks) == 20


def test_negated_aliases_and_constants_are_recognised():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    eliminated = model.alias.eliminated
    assert eliminated["gnd.p.v"] == ("known", tinysim.ast_nodes.Num(0.0))
    assert eliminated["r.p.i"][0] == "alias"              # r.p.i = r.i = c.i
    assert eliminated["src.p.i"][2] == -1                 # opposite direction
    # Whatever survives, the recovered values must still be consistent.
    result = tinysim.simulate(model, stop=0.05, points=3)
    assert result["r.p.i"] == pytest.approx(result["r.i"])
    assert result["src.p.i"] == pytest.approx(-result["r.i"])


PARALLEL_CAPACITORS = ELECTRICAL + """
model Parallel "two capacitors across the same node: c1.v and c2.v are aliases"
  ConstantVoltage src(V = 10);
  Resistor r(R = 100);
  Capacitor c1(C = 1e-3);
  Capacitor c2(C = 3e-3, v(start = 2));
  Ground gnd;
equation
  connect(src.p, r.p);
  connect(r.n, c1.p);
  connect(r.n, c2.p);
  connect(c1.n, src.n);
  connect(c2.n, src.n);
  connect(src.n, gnd.p);
end Parallel;
"""


def test_aliased_states_collapse_into_one_state_that_keeps_the_start_value():
    model = tinysim.load_source(PARALLEL_CAPACITORS, "Parallel")
    assert len(model.analysis.states) == 1
    result = tinysim.simulate(model, stop=0.01, points=3)
    assert result["c1.v"][0] == pytest.approx(2.0)
    assert result["c2.v"][0] == pytest.approx(2.0)


def test_capacitor_across_a_constant_source_loses_its_state():
    """
    The source fixes the capacitor voltage, so it cannot be a state.

    Because the value is constant, alias elimination resolves it: v is known,
    der(v) is zero, and the current is zero.  This is the cheap case of index
    reduction that every tool does for free.
    """
    source = ELECTRICAL + """
    model M
      ConstantVoltage src(V = 5);
      Capacitor c(C = 1);
      Ground gnd;
    equation
      connect(src.p, c.p);
      connect(c.n, src.n);
      connect(src.n, gnd.p);
    end M;
    """
    model = tinysim.load_source(source, "M")
    assert model.analysis.states == []
    result = tinysim.simulate(model, stop=1.0, points=3)
    assert result["c.i"][-1] == pytest.approx(0.0)
    assert result["c.v"][-1] == pytest.approx(5.0)


def test_capacitor_across_a_time_varying_source_is_high_index():
    """
    With a source that moves, the same circuit really is index 2.

    The capacitor voltage follows the clock, so it cannot be a state and it is
    not constant either -- there is nothing left for alias elimination to do,
    and the structural analysis reports the singularity.
    """
    source = ELECTRICAL + """
    model Ramp
      extends OnePort;
      parameter Real slope = 2;
    equation
      v = slope * time;
    end Ramp;

    model M
      Ramp src(slope = 2);
      Capacitor c(C = 1);
      Ground gnd;
    equation
      connect(src.p, c.p);
      connect(c.n, src.n);
      connect(src.n, gnd.p);
    end M;
    """
    with pytest.raises(StructuralError, match="structurally singular"):
        tinysim.load_source(source, "M")


def test_states_are_the_variables_under_der():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    assert model.analysis.states == ["c.v"]
    assert der_name("c.v") in model.analysis.unknowns


def test_matching_is_perfect_and_blocks_cover_every_equation():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    analysis = model.analysis
    assert len(analysis.matching) == len(analysis.equations)
    assert sorted(i for block in analysis.blocks for i in block) == \
        list(range(len(analysis.equations)))
    assert len(set(analysis.matching.values())) == len(analysis.unknowns)


def test_blocks_come_out_in_a_solvable_order():
    """Every unknown a block uses must have been computed by an earlier one."""
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    analysis = model.analysis
    available = set()
    for block in analysis.blocks:
        produced = {analysis.matching[index] for index in block}
        for index in block:
            needed = analysis.incidence[index] - produced
            assert needed <= available, f"block {block} uses {needed - available}"
        available |= produced


def test_algebraic_loop_ends_up_in_one_block(examples):
    model = tinysim.load(examples / "resistor_network.tiny", "ResistorNetwork")
    loops = [block for block in model.analysis.blocks if len(block) > 1]
    assert len(loops) == 1
    assert len(loops[0]) > 1


def test_too_few_equations_is_reported():
    with pytest.raises(StructuralError,
                       match="2 equations but 3 continuous variables"):
        tinysim.load_source("model M Real x, y, z; equation x = 1; y = x; end M;", "M")


def test_too_many_equations_is_reported():
    with pytest.raises(StructuralError,
                       match="3 equations but 2 continuous variables"):
        tinysim.load_source("model M Real x, y; equation x = 1; y = x; y = 2; end M;",
                            "M")


def test_high_index_model_is_detected_and_explained(examples):
    with pytest.raises(StructuralError) as error:
        tinysim.load(examples / "pendulum_cartesian.tiny", "CartesianPendulum")
    message = str(error.value)
    assert "structurally singular" in message
    assert "index" in message and "Pantelides" in message
    assert error.value.unmatched_equations       # names the offending equation


def test_contradictory_equations_are_reported_as_such():
    source = """
    model M
      Real x, y;
    equation
      x = 1;
      x = 2;
    end M;
    """
    with pytest.raises(StructuralError, match="contradicts itself"):
        tinysim.load_source(source, "M")


def test_repeating_the_same_fact_is_reported():
    """Two grounds on one node: consistent, but one equation too many."""
    source = ELECTRICAL + """
    model M
      ConstantVoltage src(V = 1);
      Resistor r(R = 1);
      Ground g1, g2;
    equation
      connect(src.p, r.p);
      connect(r.n, src.n);
      connect(src.n, g1.p);
      connect(src.n, g2.p);
    end M;
    """
    with pytest.raises(StructuralError, match="same fact twice"):
        tinysim.load_source(source, "M")


def test_initialization_is_a_separate_system(examples):
    model = tinysim.load(examples / "tank.tiny", "Tank")
    assert model.initialization is not None
    # In the initialization problem the states are unknowns too.
    assert "h" in model.initialization_analysis.unknowns
    assert "h" not in model.analysis.unknowns
