"""Code generation: the source text, and that it computes the right thing."""

import pytest

import tinysim
from tinysim.codegen import mangle
from tinysim.flatten import ModelError

from conftest import RC_CIRCUIT


def test_generated_source_is_readable_python():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    source = model.code.source
    assert "def evaluate(t, x, p, d, guess=None):" in source
    assert "# ---- states: supplied by the integrator ----" in source
    assert "c__v = x[0]" in source
    assert "der_c__v" in source
    compile(source, "<test>", "exec")           # it really is valid Python


def test_every_block_is_documented_in_the_generated_code():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    for block in model.code.blocks:
        assert f"block {block.index}" in model.code.source
        assert block.method in ("explicit", "symbolic", "linear system", "newton")


def test_linear_equations_are_solved_symbolically():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    methods = {block.method for block in model.code.blocks}
    assert methods == {"explicit"}               # nothing needs iteration here


def test_generated_function_computes_the_expected_derivative():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    result = model.code.function(0.0, [0.0], model.model.parameter_values, {})
    # i = V/R = 0.1 A, der(v) = i/C = 100 V/s
    assert result["der"][0] == pytest.approx(100.0)
    assert result["variables"]["r.i"] == pytest.approx(0.1)


def test_eliminated_variables_are_recovered_for_output():
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    result = model.code.function(0.0, [0.0], model.model.parameter_values, {})
    assert "src.p.i" in result["variables"]      # removed by alias elimination
    assert result["variables"]["gnd.p.v"] == 0.0


def test_linear_algebraic_loop_becomes_a_matrix_solve(examples):
    model = tinysim.load(examples / "resistor_network.tiny", "ResistorNetwork")
    loop = [block for block in model.code.blocks if block.size > 1][0]
    assert loop.method == "linear system"
    assert "np.linalg.solve" in "\n".join(loop.lines)


def test_nonlinear_algebraic_loop_becomes_a_root_find(examples):
    model = tinysim.load(examples / "diode_circuit.tiny", "DiodeCircuit")
    loop = [block for block in model.code.blocks if block.method == "newton"][0]
    text = "\n".join(loop.lines)
    assert "_solve_block" in text
    assert "did not converge" in model.code.source     # failure is not silent


def test_name_mangling():
    assert mangle("c.v") == "c__v"
    assert mangle("der(emf.flange.phi)") == "der_emf__flange__phi"


def test_clashing_mangled_names_are_rejected():
    source = """
    model Inner Real v; equation v = 1; end Inner;
    model M Inner c; Real c__v; equation c__v = 2; end M;
    """
    with pytest.raises(ModelError, match="would both be called"):
        tinysim.load_source(source, "M")


def test_when_conditions_become_zero_crossing_functions(examples):
    model = tinysim.load(examples / "bouncing_ball.tiny", "BouncingBall")
    assert model.code.event_conditions == ["h < 0"]
    result = model.code.function(0.0, [1.0, 0.0], model.model.parameter_values, {})
    assert result["events"][0] == pytest.approx(-1.0)    # margin: 0 - h
