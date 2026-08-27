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

from dataclasses import dataclass
from typing import Optional

from .alias import AliasResult, eliminate_aliases
from .analysis import StructuralAnalysis, StructuralError, analyze
from .ast_nodes import Program
from .codegen import GeneratedCode, generate_code
from .flatten import FlatModel, ModelError, flatten
from .lexer import TinySimSyntaxError
from .parser import parse, parse_file
from .simulator import Event, SimulationResult, Simulator

__version__ = "0.1.0"

__all__ = [
    "load", "load_source", "compile_model", "choose_model", "simulate", "plot",
    "explain",
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
    analysis: StructuralAnalysis
    code: GeneratedCode
    initialization: Optional[GeneratedCode] = None
    initialization_analysis: Optional[StructuralAnalysis] = None

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

    if eliminate_alias_equations:
        alias_result = eliminate_aliases(flat)
        model = alias_result.model
        eliminated = alias_result.eliminated
    else:
        alias_result, model, eliminated = None, flat, {}

    analysis = analyze(model, kind="simulation")
    code = generate_code(model, analysis, eliminated, function_name="evaluate")

    initialization = initialization_analysis = None
    if model.initial_equations:
        initialization_analysis = analyze(model, kind="initialization")
        initialization = generate_code(model, initialization_analysis, eliminated,
                                       function_name="initialize")

    return CompiledModel(name=model_name, program=program, flat=flat,
                         alias=alias_result, model=model, analysis=analysis,
                         code=code, initialization=initialization,
                         initialization_analysis=initialization_analysis)


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
    base class is the one that was meant to be simulated.  When that is
    ambiguous, say so rather than guess.
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
    if len(roots) == 1:
        return roots[0]
    raise ModelError(
        f"{where} defines several models that could be simulated "
        f"({', '.join(roots or models)}); say which one to use, for example "
        f"load(path, {(roots or models)[0]!r})")


_choose_model = choose_model


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
