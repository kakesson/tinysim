"""Flattening: instantiation, inheritance, modifiers and connect() expansion."""

import pytest

from tinysim.ast_nodes import equation_to_string
from tinysim.flatten import ModelError, flatten
from tinysim.parser import parse

from conftest import ELECTRICAL, RC_CIRCUIT


def flat(source, name):
    return flatten(parse(source), name)


def test_components_are_expanded_with_dotted_names():
    model = flat(RC_CIRCUIT, "RC")
    assert "c.v" in model.variables
    assert "c.p.i" in model.variables
    assert model.variables["c.v"].declared_in == "Capacitor"


def test_inheritance_copies_declarations_and_equations():
    model = flat(RC_CIRCUIT, "RC")
    equations = [equation_to_string(e) for e in model.equations]
    assert "r.v = r.p.v - r.n.v" in equations       # from OnePort
    assert "r.v = r.R * r.i" in equations           # from Resistor


def test_modifiers_override_declared_values():
    model = flat(RC_CIRCUIT, "RC")
    assert model.parameter_values["r.R"] == 100
    assert model.parameter_values["c.C"] == 1e-3
    assert model.variables["c.v"].start is not None


def test_connect_generates_potential_and_flow_equations():
    model = flat(RC_CIRCUIT, "RC")
    equations = [equation_to_string(e) for e in model.equations]
    assert "r.n.v = c.p.v" in equations                       # potentials equal
    assert "r.n.i + c.p.i = 0" in equations                   # flows sum to zero
    # The three-way node gets two potential equations and one flow equation.
    assert "src.n.i + c.n.i + gnd.p.i = 0" in equations


def test_equation_and_unknown_counts_match():
    model = flat(RC_CIRCUIT, "RC")
    assert len(model.equations) == len(model.continuous_variables())


def test_unconnected_connector_carries_no_flow():
    source = ELECTRICAL + """
    model Open
      ConstantVoltage src(V = 1);
      Resistor r(R = 1);
      Ground gnd;
    equation
      connect(src.p, r.p);
      connect(src.n, gnd.p);
    end Open;
    """
    model = flat(source, "Open")
    assert "r.n.i = 0" in [equation_to_string(e) for e in model.equations]


def test_hierarchical_connect_flips_the_sign_of_outside_connectors():
    """A model's own connector contributes to a connection set with -1."""
    source = ELECTRICAL + """
    model Series "two resistors behind one pair of pins"
      Pin p, n;
      Resistor r1(R = 100);
      Resistor r2(R = 200);
    equation
      connect(p, r1.p);
      connect(r1.n, r2.p);
      connect(r2.n, n);
    end Series;
    """
    model = flat(source + """
    model Circuit
      ConstantVoltage src(V = 30);
      Series s;
      Ground gnd;
    equation
      connect(src.p, s.p);
      connect(s.n, src.n);
      connect(src.n, gnd.p);
    end Circuit;
    """, "Circuit")
    equations = [equation_to_string(e) for e in model.equations]
    assert "-s.p.i + s.r1.p.i = 0" in equations


def test_parameters_may_be_defined_in_terms_of_other_parameters():
    model = flat("model M parameter Real a = 2; parameter Real b = 3 * a; "
                 "Real x; equation x = b; end M;", "M")
    assert model.parameter_values["b"] == 6


def test_parameter_without_a_value_is_reported():
    with pytest.raises(ModelError, match="has no value"):
        flat("model M parameter Real a; Real x; equation x = a; end M;", "M")


def test_circular_parameters_are_reported():
    with pytest.raises(ModelError, match="circle"):
        flat("model M parameter Real a = b; parameter Real b = a; "
             "Real x; equation x = a; end M;", "M")


def test_unknown_variable_is_reported():
    with pytest.raises(ModelError, match="unknown variable 'q'"):
        flat("model M Real x; equation x = q; end M;", "M")


def test_connecting_different_connector_types_is_rejected():
    source = ELECTRICAL + """
    connector Flange Real phi; flow Real tau; end Flange;
    model Load Flange f; equation f.phi = 0; end Load;
    model Bad
      Resistor r(R = 1);
      Load load;
    equation
      connect(r.p, load.f);
    end Bad;
    """
    with pytest.raises(ModelError, match="cannot connect"):
        flat(source, "Bad")


def test_partial_models_cannot_be_simulated():
    with pytest.raises(ModelError, match="partial"):
        flat(ELECTRICAL, "OnePort")


def test_deep_connect_reference_is_rejected():
    source = ELECTRICAL + """
    model Bad
      Resistor r(R = 1);
      Ground gnd;
    equation
      connect(r.p, r.n);
      connect(gnd.p, r.p);
    end Bad;
    """
    flat(source, "Bad")            # one dot is fine
    deeper = source.replace("connect(gnd.p, r.p);", "connect(gnd.p, r.p.v);")
    with pytest.raises(ModelError):
        flat(deeper, "Bad")
