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

from .analysis import StructuralAnalysis, der_name, find_states
from .ast_nodes import equation_to_string, to_string

STAGES = ["model", "flat", "connections", "alias", "variables", "incidence",
          "matching", "blt", "procedure", "code", "initialization", "events"]


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
    # Which variables are states does not depend on the matching, so it can be
    # shown even for a model whose structural analysis failed.
    states = analysis.states if analysis is not None else find_states(model)
    _heading("5. VARIABLES", file)
    print(f"  {'variable':<24} {'kind':<12} {'start':>10}   description",
          file=_out(file))
    print("  " + "-" * 74, file=_out(file))
    for name, variable in model.variables.items():
        kind = variable.kind
        if name in states:
            kind = "state"
        elif variable.kind == "continuous":
            kind = "algebraic"
        start = ("" if variable.start is None else to_string(variable.start))
        if variable.is_parameter:
            start = f"{model.parameter_values.get(name, float('nan')):g}"
        print(f"  {name:<24} {kind:<12} {start:>10}   {variable.description}",
              file=_out(file))
    if analysis is None:
        return
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


def show_procedure(compiled, file=None):
    """
    How the sorted blocks are actually solved -- without showing any Python.

    The blocks are the plan; this is the procedure that follows from it, plus
    what happens around it: the initialization before the first step, and the
    events that interrupt the continuous system.
    """
    from .evaluator import evaluate
    model, analysis = compiled.model, compiled.analysis
    _heading("10. HOW THIS SYSTEM IS ACTUALLY SOLVED", file)

    print("  ONCE, BEFORE THE FIRST STEP", file=_out(file))
    if compiled.initialization is not None:
        initialization = compiled.initialization_analysis
        print(f"    The model has initial equations, so the initial state is "
              f"computed, not given:\n    {len(initialization.equations)} "
              f"equations for {len(initialization.unknowns)} unknowns, the "
              f"states among them.", file=_out(file))
        _print_steps(initialization, compiled.initialization.blocks, file)
    elif analysis.states:
        values = []
        for state in analysis.states:
            variable = model.variables[state]
            number = (0.0 if variable.start is None
                      else evaluate(variable.start, model.parameter_values))
            values.append(f"{state} = {number:g}")
        print("    Every state takes its start value: " + ", ".join(values),
              file=_out(file))
    else:
        print("    Nothing to initialize: the model has no states.", file=_out(file))
    for name in model.discrete_variables():
        print(f"    Discrete {name} starts at "
              f"{to_string(model.variables[name].start)}, and changes only at "
              f"an event.", file=_out(file))

    print("\n  AT EVERY EVALUATION  --  the integrator gives "
          + (", ".join(analysis.states) or "no states")
          + " and time, and asks for the derivatives", file=_out(file))
    _print_steps(analysis, compiled.code.blocks, file)
    derivatives = ", ".join(f"der({state})" for state in analysis.states)
    print(f"    -> {derivatives or 'nothing'} goes back to the integrator; "
          f"everything else is output.", file=_out(file))

    if model.when_equations:
        print("\n  WHENEVER A CONDITION CHANGES  --  the hybrid part",
              file=_out(file))
        for position, when_equation in enumerate(model.when_equations):
            margin = (compiled.code.event_margins[position]
                      if position < len(compiled.code.event_margins) else "?")
            print(f"    when {to_string(when_equation.condition)}   "
                  f"is watched as the crossing function  {margin}",
                  file=_out(file))
            for statement in when_equation.body:
                if type(statement).__name__ == "Reinit":
                    print(f"        -> the state {statement.name} jumps to "
                          f"{to_string(statement.value)}", file=_out(file))
                else:
                    print(f"        -> the discrete {statement.name} becomes "
                          f"{to_string(statement.value)}", file=_out(file))
        print("    The continuous system stops at the crossing, the body runs, "
              "and integration\n    restarts from the updated state. How hard "
              "the crossing is looked for is chosen\n    when you simulate: "
              "events='locate', 'step' or 'off'.", file=_out(file))

    print("\n  IN BETWEEN", file=_out(file))
    print("    The integrator advances the states, choosing its own step size "
          "or taking the\n    fixed one you gave it. That is the only part "
          "TinySim does not do itself.", file=_out(file))


def _print_steps(analysis, blocks, file):
    """One line per block, saying how that block is solved."""
    for block in blocks:
        equations = [analysis.equations[index].source for index in block.equations]
        if block.solution is not None and not block.is_loop:
            how = "rearranged" if block.method == "explicit" else "solved symbolically"
            print(f"    {block.index:3d}. {block.solution:<44} # {how} from "
                  f"{equations[0]}", file=_out(file))
        elif not block.is_loop:
            print(f"    {block.index:3d}. solve {equations[0]} for "
                  f"{block.unknowns[0]}", file=_out(file))
            print(f"         # nothing rearranges this: a root finder starts "
                  f"from the start value", file=_out(file))
        else:
            print(f"    {block.index:3d}. solve simultaneously for "
                  f"{', '.join(block.unknowns)}:", file=_out(file))
            for text in equations:
                print(f"           {text}", file=_out(file))
            if block.method == "linear system":
                print(f"         # a circle of {len(equations)} equations, "
                      f"linear in those unknowns:\n"
                      f"         # one matrix solve A x = b, no iteration",
                      file=_out(file))
            else:
                print(f"         # a circle of {len(equations)} equations, "
                      f"not linear in those unknowns:\n"
                      f"         # solved by iteration from the start values, "
                      f"then from the previous solution",
                      file=_out(file))


def show_code(compiled, file=None):
    """The generated simulation function."""
    _heading("11. GENERATED SIMULATION CODE", file)
    print(compiled.code.source, file=_out(file))


def show_initialization(compiled, file=None):
    """The initialization problem, which is a system of its own."""
    _heading("12. INITIALIZATION PROBLEM", file)
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
    _heading(f"13. EVENTS  --  {len(when_equations)} when-clause(s)", file)
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
    procedure, code, initialization, events.
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
    if "procedure" in wanted and compiled.code is not None:
        show_procedure(compiled, file)
    if "code" in wanted:
        show_code(compiled, file)
    if "initialization" in wanted:
        show_initialization(compiled, file)
    if "events" in wanted:
        show_events(compiled, file)
