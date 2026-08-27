"""
Printing the pipeline.

Nothing here affects the simulation: this module exists purely so that every
intermediate result can be *looked at*.  `explain(model)` walks the stages in
order and prints them; each stage is also available on its own.

    tinysim.explain(model)                       # everything
    tinysim.explain(model, "flat,blt,code")      # a selection
"""

import sys
from typing import Optional

from .analysis import StructuralAnalysis, der_name
from .ast_nodes import equation_to_string, to_string

STAGES = ["model", "flat", "connections", "alias", "variables", "incidence",
          "matching", "blt", "code", "initialization", "events"]


def _out(file):
    return sys.stdout if file is None else file


def _heading(title: str, file=None):
    print("", file=_out(file))
    print("=" * 78, file=_out(file))
    print(title, file=_out(file))
    print("=" * 78, file=_out(file))


# =============================================================================
# The stages
# =============================================================================

def show_model(compiled, file=None):
    """What the file said: classes, and the model that was selected."""
    _heading(f"1. MODEL  --  {compiled.name}", file)
    for name, definition in compiled.program.classes.items():
        marker = "  <- simulated" if name == compiled.name else ""
        kind = ("partial model" if definition.partial else definition.kind)
        print(f"  {kind:15s} {name:20s} "
              f"{len(definition.decls):2d} declarations, "
              f"{len(definition.equations):2d} equations{marker}", file=_out(file))


def show_flat(compiled, file=None):
    """The flat equation set: what acausal composition actually produced."""
    flat = compiled.flat
    _heading(f"2. FLATTENED MODEL  --  {len(flat.equations)} equations, "
             f"{len(flat.continuous_variables())} continuous variables", file)
    print("Every component has been expanded and every connect() has become "
          "equations.\n", file=_out(file))
    width = max((len(e.source or "") for e in flat.equations), default=10)
    for number, equation in enumerate(flat.equations, start=1):
        source = equation.source or equation_to_string(equation)
        print(f"  {number:3d}  {source:<{width}}   # {equation.origin}",
              file=_out(file))
    if flat.initial_equations:
        print("\n  initial equation", file=_out(file))
        for number, equation in enumerate(flat.initial_equations, start=1):
            print(f"  {number:3d}  {equation.source}", file=_out(file))


def show_connections(compiled, file=None):
    """The connection sets, and the two rules applied to them."""
    sets = compiled.flat.connection_sets
    _heading(f"3. CONNECTION SETS  --  {len(sets)}", file)
    if not sets:
        print("  (this model has no connectors)", file=_out(file))
        return
    print("Within a set: potential variables are equal, flow variables sum to "
          "zero.\n", file=_out(file))
    for number, connection_set in enumerate(sets, start=1):
        members = ", ".join(connection_set.connectors)
        print(f"  set {number} ({connection_set.connector_class}): {members}",
              file=_out(file))


def show_alias(compiled, file=None):
    """What alias elimination removed."""
    alias = compiled.alias
    _heading("4. ALIAS ELIMINATION", file)
    if alias is None:
        print("  (skipped: the model was compiled with "
              "eliminate_alias_equations=False)", file=_out(file))
        return
    before, after = len(compiled.flat.equations), len(compiled.model.equations)
    print(f"  {before} equations -> {after} equations, "
          f"{len(alias.eliminated)} variables removed\n", file=_out(file))
    for name in sorted(alias.eliminated):
        print(f"    {alias.describe(name)}", file=_out(file))
    print("\n  Remaining equations:", file=_out(file))
    for number, equation in enumerate(compiled.model.equations, start=1):
        print(f"  {number:3d}  {equation.source}", file=_out(file))


def show_variables(compiled, file=None):
    """How each variable was classified, which is what decides the unknowns."""
    model, analysis = compiled.model, compiled.analysis
    _heading("5. VARIABLES", file)
    print(f"  {'variable':<24} {'kind':<12} {'start':>10}   description",
          file=_out(file))
    print("  " + "-" * 74, file=_out(file))
    for name, variable in model.variables.items():
        kind = variable.kind
        if name in analysis.states:
            kind = "state"
        elif variable.kind == "continuous":
            kind = "algebraic"
        start = ("" if variable.start is None else to_string(variable.start))
        if variable.is_parameter:
            start = f"{model.parameter_values.get(name, float('nan')):g}"
        print(f"  {name:<24} {kind:<12} {start:>10}   {variable.description}",
              file=_out(file))
    print(f"\n  states     ({len(analysis.states)}): "
          f"{', '.join(analysis.states) or 'none'}", file=_out(file))
    print(f"  unknowns   ({len(analysis.unknowns)}): "
          f"{', '.join(analysis.unknowns)}", file=_out(file))


def show_incidence(analysis: StructuralAnalysis, file=None, sorted_form: bool = False,
                   title: Optional[str] = None):
    """
    The incidence matrix as text: which unknown occurs in which equation.

    `x` marks an occurrence, `X` the unknown the equation was matched with.
    """
    _heading(title or ("6. INCIDENCE MATRIX (as written)" if not sorted_form
                       else "8. INCIDENCE MATRIX (BLT sorted)"), file)
    if sorted_form:
        rows = [index for block in analysis.blocks for index in block]
        columns = [analysis.matching[index] for index in rows]
    else:
        rows = list(range(len(analysis.equations)))
        columns = list(analysis.unknowns)

    print("  columns:", file=_out(file))
    for number, unknown in enumerate(columns, start=1):
        print(f"    {number:3d}  {unknown}", file=_out(file))
    print(file=_out(file))

    header = "".join(f"{(number % 100):>3d}" for number in range(1, len(columns) + 1))
    print(f"      eq |{header}", file=_out(file))
    print("      ---+" + "-" * (3 * len(columns)), file=_out(file))
    for row in rows:
        cells = ""
        for unknown in columns:
            if unknown not in analysis.incidence[row]:
                cells += "  ."
            elif analysis.matching.get(row) == unknown:
                cells += "  X"
            else:
                cells += "  x"
        print(f"      {row + 1:3d}|{cells}", file=_out(file))


def show_matching(compiled, file=None):
    """The assignment of equations to unknowns."""
    analysis = compiled.analysis
    _heading("7. MATCHING  --  which equation computes which unknown", file)
    print("  Found by augmenting paths; a perfect matching means the model is "
          "structurally\n  well posed.\n", file=_out(file))
    for index in range(len(analysis.equations)):
        unknown = analysis.matching[index]
        print(f"  eq {index + 1:3d}  ->  {unknown:<20}  "
              f"{analysis.equations[index].source}", file=_out(file))


def show_blt(compiled, file=None, analysis: Optional[StructuralAnalysis] = None):
    """The solution order, block by block."""
    analysis = analysis or compiled.analysis
    loops = [block for block in analysis.blocks if len(block) > 1]
    _heading(f"9. BLT SORTING  --  {len(analysis.blocks)} blocks, "
             f"{len(loops)} algebraic loop(s)", file)
    print("  Blocks are solved from top to bottom; a block with more than one "
          "equation is\n  an algebraic loop that must be solved "
          "simultaneously.\n", file=_out(file))
    for position, block in enumerate(analysis.blocks, start=1):
        unknowns = ", ".join(analysis.matching[index] for index in block)
        marker = "  <- algebraic loop" if len(block) > 1 else ""
        print(f"  block {position:3d}  solve for {unknowns}{marker}",
              file=_out(file))
        for index in block:
            print(f"             {analysis.equations[index].source}",
                  file=_out(file))


def show_code(compiled, file=None):
    """The generated simulation function."""
    _heading("10. GENERATED SIMULATION CODE", file)
    print(compiled.code.source, file=_out(file))


def show_initialization(compiled, file=None):
    """The initialization problem, which is a system of its own."""
    _heading("11. INITIALIZATION PROBLEM", file)
    if compiled.initialization is None:
        print("  The model has no `initial equation`s, so the initial state is "
              "taken\n  directly from the `start` attributes.", file=_out(file))
        return
    analysis = compiled.initialization_analysis
    print(f"  {len(analysis.equations)} equations, {len(analysis.unknowns)} "
          f"unknowns -- note that the states are unknowns here too.\n",
          file=_out(file))
    show_blt(compiled, file=file, analysis=analysis)
    print(compiled.initialization.source, file=_out(file))


def show_events(compiled, file=None):
    """The `when` clauses, and the zero-crossing functions they become."""
    when_equations = compiled.model.when_equations
    _heading(f"12. EVENTS  --  {len(when_equations)} when-clause(s)", file)
    if not when_equations:
        print("  (this model is purely continuous)", file=_out(file))
        return
    print("  Each condition becomes a zero-crossing function handed to the "
          "integrator;\n  when it crosses zero upwards, integration stops and "
          "the body runs.\n", file=_out(file))
    for number, when_equation in enumerate(when_equations, start=1):
        print(f"  when {to_string(when_equation.condition)} then",
              file=_out(file))
        for statement in when_equation.body:
            kind = type(statement).__name__.lower()
            if kind == "reinit":
                print(f"      reinit({statement.name}, "
                      f"{to_string(statement.value)});", file=_out(file))
            else:
                print(f"      {statement.name} = {to_string(statement.value)};",
                      file=_out(file))
        print("  end;", file=_out(file))


# =============================================================================
# Everything at once
# =============================================================================

def explain(compiled, stages="all", file=None):
    """
    Print the pipeline for a compiled model.

    `stages` is "all", or a comma-separated selection from:
    model, flat, connections, alias, variables, incidence, matching, blt,
    code, initialization, events.
    """
    wanted = STAGES if stages in ("all", None) else [
        stage.strip() for stage in stages.split(",")]
    unknown = [stage for stage in wanted if stage not in STAGES]
    if unknown:
        raise ValueError(f"unknown stage(s) {', '.join(unknown)}; "
                         f"choose from {', '.join(STAGES)}")

    if "model" in wanted:
        show_model(compiled, file)
    if "flat" in wanted:
        show_flat(compiled, file)
    if "connections" in wanted:
        show_connections(compiled, file)
    if "alias" in wanted:
        show_alias(compiled, file)
    if "variables" in wanted:
        show_variables(compiled, file)
    if "incidence" in wanted:
        show_incidence(compiled.analysis, file)
    if "matching" in wanted:
        show_matching(compiled, file)
    if "blt" in wanted:
        show_incidence(compiled.analysis, file, sorted_form=True)
        show_blt(compiled, file)
    if "code" in wanted:
        show_code(compiled, file)
    if "initialization" in wanted:
        show_initialization(compiled, file)
    if "events" in wanted:
        show_events(compiled, file)
