"""Shared test fixtures: the small model library the tests build circuits from."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"

#: A minimal electrical library, written inline so tests stay self-contained.
ELECTRICAL = """
connector Pin
  Real v;
  flow Real i;
end Pin;

partial model OnePort
  Pin p, n;
  Real v, i;
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

model Capacitor
  extends OnePort;
  parameter Real C = 1e-3;
equation
  C * der(v) = i;
end Capacitor;

model ConstantVoltage
  extends OnePort;
  parameter Real V = 10;
equation
  v = V;
end ConstantVoltage;

model Ground
  Pin p;
equation
  p.v = 0;
end Ground;
"""

RC_CIRCUIT = ELECTRICAL + """
model RC
  ConstantVoltage src(V = 10);
  Resistor r(R = 100);
  Capacitor c(C = 1e-3, v(start = 0));
  Ground gnd;
equation
  connect(src.p, r.p);
  connect(r.n, c.p);
  connect(c.n, src.n);
  connect(src.n, gnd.p);
end RC;
"""


@pytest.fixture
def rc_source():
    return RC_CIRCUIT


@pytest.fixture
def electrical_source():
    return ELECTRICAL


@pytest.fixture
def examples():
    return EXAMPLES
