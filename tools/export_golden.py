"""
Export golden files: what the Python implementation says, for Julia to match.

The Julia port is a rewrite, and a rewrite is only safe if "the same" has a
definition. This writes one JSON file per example holding everything the port
has to reproduce -- the flat equations, what alias elimination removed, the
solution order, the simulation at fixed time points, the events, and every
contract margin.

    python tools/export_golden.py            # writes golden/*.json

The Julia test suite reads these files and compares. Structure is compared
exactly; numbers to a tolerance stated in the file itself, because the two
implementations use different integrators and are not expected to agree bit for
bit.

Run this once, before the port begins, and again only if the Python
implementation is deliberately changed -- which, from phase 1 of the migration,
it should not be.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tinysim                                                    # noqa: E402
from tinysim.ast_nodes import equation_to_string                  # noqa: E402

#: How each example is simulated for the golden file. Tolerances are tighter
#: than the defaults so that the recorded numbers are the model's answer rather
#: than the integrator's.
EXAMPLES = [
    ("electrical.tiny", "RCCircuit", dict(stop=1.0, points=1001)),
    ("thermostat.tiny", "Thermostat", dict(stop=200.0, points=4001)),
    ("tank.tiny", "Tank", dict(stop=20.0, points=2001)),
    ("dcmotor.tiny", "DCMotor", dict(stop=3.0, points=3001)),
    ("bouncing_ball.tiny", "BouncingBall", dict(stop=3.0, points=3001)),
    ("pendulum.tiny", "Pendulum", dict(stop=10.0, points=2001)),
    ("resistor_network.tiny", "ResistorNetwork", dict(stop=0.01, points=2001)),
    ("diode_circuit.tiny", "DiodeCircuit", dict(stop=1.0, points=2001)),
    ("pendulum_cartesian.tiny", "CartesianPendulum", None),   # does not compile
]

#: How many points of the run to record. The whole trajectory would make the
#: files large and the comparison no stronger.
SAMPLES = 21

#: What the Julia side may differ by, and why: different integrators, a
#: different event tolerance, a different order of floating-point operations.
TOLERANCE = {"relative": 1e-6, "absolute": 1e-9, "event_time": 1e-6}


def flat_model(compiled) -> dict:
    return {
        "equations": [equation.source for equation in compiled.flat.equations],
        "initial_equations": [equation.source
                              for equation in compiled.flat.initial_equations],
        "origins": [equation.origin for equation in compiled.flat.equations],
        "equation_count": len(compiled.flat.equations),
        "continuous_count": len(compiled.flat.continuous_variables()),
        "connection_sets": [{"connector": connection.connector_class,
                             "members": connection.connectors}
                            for connection in compiled.flat.connection_sets],
        "components": compiled.flat.components,
    }


def simplification(compiled) -> dict:
    alias = compiled.alias
    return {
        "eliminated": {name: alias.describe(name) for name in sorted(alias.eliminated)},
        "remaining": [equation.source for equation in compiled.model.equations],
        "states": list(compiled.analysis.states),
        "unknowns": list(compiled.analysis.unknowns),
        "parameters": compiled.model.parameter_values,
    }


def solution_order(compiled) -> list:
    return [{"index": block.index,
             "unknowns": block.unknowns,
             "method": block.method,
             "solution": block.solution,
             "equations": [compiled.analysis.equations[index].source
                           for index in block.equations]}
            for block in compiled.code.blocks]


def simulation(compiled, options) -> dict:
    result = tinysim.simulate(compiled, rtol=1e-10, atol=1e-12, **options)
    names = sorted(name for name in result.values if not name.startswith("der("))
    stride = max(1, (len(result.time) - 1) // (SAMPLES - 1))
    indices = list(range(0, len(result.time), stride))
    return {
        "options": options,
        "variables": names,
        "samples": [{"time": float(result.time[index]),
                     "values": {name: float(result[name][index]) for name in names}}
                    for index in indices],
        "final": {name: float(result[name][-1]) for name in names},
        "events": [{"time": event.time, "condition": event.condition,
                    "changes": {name: list(change)
                                for name, change in event.changes.items()}}
                   for event in result.events],
    }, result


def contracts(compiled, result) -> list:
    report = tinysim.check_contracts(compiled, result)
    return [{"instance": item.instance,
             "contract": item.contract.name,
             "verdict": item.verdict,
             "clauses": [{"kind": clause.kind,
                          "written": clause.clause.written,
                          "stl": clause.clause.stl,
                          "margin": clause.margin,
                          "at_time": clause.at_time}
                         for clause in item.assumptions + item.guarantees]}
            for item in report.results]


def export(name: str, model_name: str, options) -> dict:
    path = ROOT / "examples" / name
    record = {"file": f"examples/{name}", "model": model_name,
              "tolerance": TOLERANCE}

    if options is None:
        # The model is expected to fail; what it says is worth recording too.
        try:
            tinysim.load(path, model_name)
        except tinysim.StructuralError as error:
            partial = error.partial_model
            record["compiles"] = False
            record["error"] = str(error)
            record["flat"] = flat_model(partial)
            record["contracts_attached"] = [contract.name for contract, _
                                            in partial.contract_instances]
        return record

    compiled = tinysim.load(path, model_name)
    record["compiles"] = True
    record["flat"] = flat_model(compiled)
    record["simplified"] = simplification(compiled)
    record["blocks"] = solution_order(compiled)
    run, result = simulation(compiled, options)
    record["simulation"] = run
    record["contracts"] = contracts(compiled, result)
    return record


def main() -> int:
    target = ROOT / "golden"
    target.mkdir(exist_ok=True)
    for name, model_name, options in EXAMPLES:
        record = export(name, model_name, options)
        path = target / f"{pathlib.Path(name).stem}.json"
        path.write_text(json.dumps(record, indent=2, sort_keys=False) + "\n")
        summary = ("does not compile" if not record["compiles"] else
                   f"{record['flat']['equation_count']} flat equations, "
                   f"{len(record['blocks'])} blocks, "
                   f"{len(record['simulation']['events'])} events, "
                   f"{len(record['contracts'])} contracts")
        print(f"  {path.relative_to(ROOT)}: {summary}")
    print(f"\nwrote {len(EXAMPLES)} golden files to {target.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
