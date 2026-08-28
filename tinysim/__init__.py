"""
TinySim -- a tiny equation-based, acausal modeling language, for teaching.

The whole pipeline, in the order the modules appear in this package:

    model text
      -> lexer.py       tokens
      -> parser.py      abstract syntax tree
      -> flatten.py     flat equations (components expanded, connect() applied)
      -> alias.py       trivial equations removed
      -> analysis.py    unknowns matched to equations, sorted into blocks
      -> codegen.py     readable Python source for the simulation function
      -> simulator.py   SciPy integration, with events
      -> plotting.py    Matplotlib results

Typical use:

    import tinysim

    model  = tinysim.load("examples/electrical.tiny", "RCCircuit")
    tinysim.explain(model)                       # show every stage
    result = tinysim.simulate(model, stop=1.0)
    tinysim.plot(result, ["c.v", "r.i"])

Every intermediate object is kept on the returned `CompiledModel`, so anything
the report prints can also be inspected directly from Python.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from .alias import AliasResult, eliminate_aliases
from .analysis import (StructuralAnalysis, StructuralError, analyze,
                       check_balance)
from .ast_nodes import Program
from .codegen import GeneratedCode, generate_code
from .contracts import Contract, prefix_contract, references
from .flatten import FlatModel, ModelError, flatten
from .lexer import TinySimSyntaxError
from .parser import parse, parse_file
from .simulator import Event, SimulationResult, Simulator

__version__ = "0.1.0"

__all__ = [
    "load", "load_source", "compile_model", "choose_model", "simulate", "plot",
    "explain", "check_contracts", "cross_check_contracts",
    "CompiledModel", "SimulationResult", "Event",
    "ModelError", "StructuralError", "TinySimSyntaxError",
]


@dataclass
class CompiledModel:
    """
    Everything the pipeline produced for one model.

    Keeping all the intermediate results is the whole point: `flat` is the
    model before simplification, `model` after it, `analysis` holds the
    matching and the BLT blocks, and `code` holds the generated source.
    """
    name: str
    program: Program
    flat: FlatModel                       # straight after flattening
    alias: Optional[AliasResult]          # None if alias elimination was skipped
    model: FlatModel                      # the model the code was generated from
    #: `analysis` and `code` are None only on the partial model attached to a
    #: structural error, so that the stages that did succeed can still be shown.
    analysis: Optional[StructuralAnalysis] = None
    code: Optional[GeneratedCode] = None
    initialization: Optional[GeneratedCode] = None
    initialization_analysis: Optional[StructuralAnalysis] = None
    #: `(contract, instance name)` for the model itself and for every component
    #: instance whose class carries a contract.  The contracts are already
    #: written in each instance's namespace.
    contract_instances: List[tuple] = field(default_factory=list)

    @property
    def source(self) -> str:
        """The generated simulation code, as text."""
        return self.code.source

    def __repr__(self) -> str:
        loops = sum(1 for block in self.analysis.blocks if len(block) > 1)
        return (f"<CompiledModel {self.name!r}: "
                f"{len(self.analysis.states)} states, "
                f"{len(self.analysis.equations)} equations, "
                f"{len(self.analysis.blocks)} blocks, {loops} algebraic loop(s)>")


def compile_model(program: Program, model_name: str,
                  eliminate_alias_equations: bool = True) -> CompiledModel:
    """
    Run the whole pipeline on one model of an already parsed program.

    Set `eliminate_alias_equations=False` to see the unsimplified system, which
    is instructive: the RC circuit then has 20 equations instead of 3.
    """
    flat = flatten(program, model_name)
    check_balance(flat)
    # Contracts are resolved and name-checked here, before the structural
    # analysis: a model that turns out to be unsolvable should still report
    # what it was supposed to promise.
    contracts = attach_contracts(program, flat)

    if eliminate_alias_equations:
        alias_result = eliminate_aliases(flat)
        model = alias_result.model
        eliminated = alias_result.eliminated
    else:
        alias_result, model, eliminated = None, flat, {}

    # If the model turns out not to be solvable, the stages that already
    # succeeded are still worth looking at -- that is usually where the mistake
    # is visible -- so they travel with the exception.
    partial = CompiledModel(name=model_name, program=program, flat=flat,
                            alias=alias_result, model=model,
                            contract_instances=contracts)
    try:
        analysis = analyze(model, kind="simulation")
        code = generate_code(model, analysis, eliminated, function_name="evaluate")
    except ModelError as error:
        error.partial_model = partial
        raise

    initialization = initialization_analysis = None
    if model.initial_equations:
        initialization_analysis = analyze(model, kind="initialization")
        initialization = generate_code(model, initialization_analysis, eliminated,
                                       function_name="initialize")

    return CompiledModel(name=model_name, program=program, flat=flat,
                         alias=alias_result, model=model, analysis=analysis,
                         code=code, initialization=initialization,
                         initialization_analysis=initialization_analysis,
                         contract_instances=contracts)


def attach_contracts(program: Program, flat: FlatModel) -> List[tuple]:
    """
    Work out which contracts apply to a flattened model, and to what.

    A contract belongs to a class, so it applies once to the model being
    simulated and once to every component instance of that class inside it.
    Each copy is rewritten into that instance's namespace, and every name it
    mentions is checked to exist -- a typo in a contract should fail the way a
    typo in an equation does, before anything runs.
    """
    known = set(flat.variables) | {"time"}
    states = {name for name, variable in flat.variables.items()
              if variable.kind == "continuous"}
    instances: List[tuple] = []

    for contract in program.contracts.values():
        if contract.model_name not in program.classes:
            raise ModelError(
                f"contract {contract.name!r} is written for {contract.model_name!r}, "
                f"which is not a class in this file")
        matching = [instance for instance, class_name in flat.components.items()
                    if class_name == contract.model_name]
        for instance in matching:
            prefix = f"{instance}." if instance else ""
            moved = prefix_contract(contract, prefix)
            for _, clause in moved.clauses():
                _check_contract_names(contract, clause, known, states, instance)
            instances.append((moved, instance))
    return instances


def _check_contract_names(contract, clause, known, states, instance):
    from .contracts import references as clause_references
    for name in clause_references(clause.formula):
        if name in known:
            continue
        raise ModelError(
            f"contract {contract.name!r}, line {clause.line}: {name!r} is not a "
            f"variable or parameter of "
            + (f"component {instance!r}" if instance else "this model"))


def load(path, model_name: Optional[str] = None, **options) -> CompiledModel:
    """
    Parse a `.tiny` file and compile one of its models.

    If `model_name` is left out and the file defines exactly one non-partial
    model, that one is used.
    """
    program = parse_file(path)
    return compile_model(program, _choose_model(program, model_name, str(path)),
                         **options)


def load_source(source: str, model_name: Optional[str] = None,
                **options) -> CompiledModel:
    """Same as `load`, but from a string -- handy in tests and notebooks."""
    program = parse(source)
    return compile_model(program, _choose_model(program, model_name, "<string>"),
                         **options)


def choose_model(program: Program, model_name: Optional[str] = None,
                 where: str = "<program>") -> str:
    """
    Decide which model of a file to compile.

    Example files usually hold a small component library followed by the system
    built from it, so the model that nobody else uses as a component or as a
    base class is the one that was meant to be simulated.  When several models
    qualify -- a library may define components that this particular file never
    uses -- the last one declared wins, which is where the system model
    conventionally goes.  Pass `model_name` to be explicit.
    """
    if model_name is not None:
        return model_name

    models = [name for name, definition in program.classes.items()
              if definition.kind == "model" and not definition.partial]
    if not models:
        raise ModelError(f"{where} defines no model that can be simulated")
    if len(models) == 1:
        return models[0]

    used_as_component = {declaration.type_name
                         for definition in program.classes.values()
                         for declaration in definition.decls}
    used_as_base = {base for definition in program.classes.values()
                    for base in definition.extends}
    roots = [name for name in models
             if name not in used_as_component and name not in used_as_base]
    if roots:
        return roots[-1]
    raise ModelError(
        f"every model in {where} is used as a component of another one "
        f"({', '.join(models)}); say which one to simulate, for example "
        f"load(path, {models[-1]!r})")


_choose_model = choose_model


def check_contracts(model, result, backend: str = "builtin"):
    """
    Check every contract that applies to a run.

    `backend="builtin"` uses TinySim's own monitor, written out in
    `monitor.py`.  `backend="julia"` hands the same clauses to
    SignalTemporalLogic.jl instead -- an independent implementation, useful
    both as a cross-check and because trusting a monitor you wrote yourself is
    not the same as trusting one you did not.  See `stl_julia.py`.
    """
    if backend == "builtin":
        from .monitor import check_contracts as _check
        return _check(model, result)
    if backend == "julia":
        from .stl_julia import check_contracts as _check
        return _check(model, result)
    raise ValueError(f"unknown contract backend {backend!r}; "
                     f"choose 'builtin' or 'julia'")


def cross_check_contracts(model, result):
    """
    Check the contracts with both implementations and compare them.

    Returns `(builtin_report, julia_report, differences)`, where `differences`
    maps each clause to the size of the disagreement.  On the examples that
    ships with TinySim the disagreement is exactly zero, which is the point:
    the readable monitor in `monitor.py` is not just readable but right.
    """
    from .stl_julia import robustness_from_julia

    builtin = check_contracts(model, result)
    margins = robustness_from_julia(model, result)
    differences = {}
    for item in builtin.results:
        for clause in item.assumptions + item.guarantees:
            label = (f"{item.instance}|{item.contract.name}|{clause.kind}|"
                     f"{clause.clause.line}")
            if label in margins:
                differences[label] = abs(clause.margin - margins[label])
    return builtin, check_contracts(model, result, backend="julia"), differences


def simulate(model, stop: float = 1.0, **options) -> SimulationResult:
    """
    Simulate a compiled model (or a path, or source text) up to time `stop`.

    Options are passed on to `simulator.Simulator.simulate`: `start`, `points`,
    `method`, `rtol`, `atol`.
    """
    if not isinstance(model, CompiledModel):
        raise TypeError("simulate() expects a CompiledModel from load() or "
                        "load_source()")
    return Simulator(model).simulate(stop=stop, **options)


def plot(*args, **kwargs):
    """Plot simulation results; see `tinysim.plotting.plot`."""
    from .plotting import plot as _plot
    return _plot(*args, **kwargs)


def explain(model: CompiledModel, stages="all", file=None):
    """Print the requested pipeline stages; see `tinysim.report.explain`."""
    from .report import explain as _explain
    return _explain(model, stages=stages, file=file)
