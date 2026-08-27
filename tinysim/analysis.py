"""
Stage 4 of the pipeline: *structural analysis*.

The flat model is a set of equations, not a program.  Before anything can be
simulated, three questions have to be answered, and none of them needs a single
number -- only the *structure* of the equations, i.e. which unknown appears in
which equation:

1. **Which variables are unknown?**  With `der(v)` present, `v` is a state:
   the integrator provides its value, and `der(v)` becomes the unknown.
2. **Which equation should be used to compute which unknown?**  That is a
   perfect matching in a bipartite graph (equations on one side, unknowns on
   the other), found here with the classic augmenting-path algorithm.
3. **In which order can they be computed?**  Sorting the matched equations by
   their dependencies gives a *block lower triangular* (BLT) form.  Equations
   that mutually depend on each other end up in the same block: an algebraic
   loop.  Tarjan's strongly-connected-components algorithm does both jobs at
   once -- it finds the blocks and puts them in a solvable order.

Failure here is informative too: if no perfect matching exists, the model is
structurally singular, which for a physically sensible model usually means its
differential index is higher than 1.  See `report.py` for the message.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .ast_nodes import BinOp, Call, Equation, Expr, IfExpr, Num, Ref, UnOp
from .flatten import FlatModel, ModelError, derivative_names


class StructuralError(ModelError):
    """Raised when the equations cannot be matched or sorted."""

    def __init__(self, message, unmatched_equations=None, unmatched_unknowns=None):
        super().__init__(message)
        self.unmatched_equations = unmatched_equations or []
        self.unmatched_unknowns = unmatched_unknowns or []


def der_name(variable: str) -> str:
    """The name TinySim uses for a derivative unknown: `der(x)`."""
    return f"der({variable})"


# =============================================================================
# Which unknowns does an expression contain?
# =============================================================================

def unknowns_in(expr: Expr, candidates: Set[str], found: Optional[Set] = None) -> Set[str]:
    """
    The unknowns from `candidates` that `expr` actually depends on.

    The one subtlety: inside `der(v)`, the variable `v` itself is *not* a
    dependency -- the integrator supplies it.  The unknown is `der(v)`.
    """
    found = set() if found is None else found
    if isinstance(expr, Num):
        return found
    if isinstance(expr, Ref):
        if expr.name in candidates:
            found.add(expr.name)
        return found
    if isinstance(expr, Call):
        if expr.func == "der":
            name = der_name(expr.args[0].name)
            if name in candidates:
                found.add(name)
            return found
        if expr.func == "pre":
            return found                    # `pre(x)` is a known, pre-event value
        for argument in expr.args:
            unknowns_in(argument, candidates, found)
        return found
    if isinstance(expr, UnOp):
        return unknowns_in(expr.operand, candidates, found)
    if isinstance(expr, BinOp):
        unknowns_in(expr.left, candidates, found)
        return unknowns_in(expr.right, candidates, found)
    if isinstance(expr, IfExpr):
        unknowns_in(expr.cond, candidates, found)
        unknowns_in(expr.then_expr, candidates, found)
        return unknowns_in(expr.else_expr, candidates, found)
    raise TypeError(f"cannot analyse {expr!r}")


# =============================================================================
# The result of the analysis
# =============================================================================

@dataclass
class StructuralAnalysis:
    """Everything the later stages need to generate simulation code."""
    kind: str                                   # 'simulation' | 'initialization'
    equations: List[Equation]
    unknowns: List[str]                         # names, `der(x)` included
    states: List[str]                           # variables that appear under der()
    incidence: List[Set[str]] = field(default_factory=list)   # per equation
    matching: Dict[int, str] = field(default_factory=dict)    # equation -> unknown
    blocks: List[List[int]] = field(default_factory=list)     # BLT order

    @property
    def assigned(self) -> Dict[str, int]:
        """unknown -> the equation chosen to compute it."""
        return {unknown: index for index, unknown in self.matching.items()}

    def block_unknowns(self, block: List[int]) -> List[str]:
        return [self.matching[index] for index in block]


# =============================================================================
# Building the analysis
# =============================================================================

def find_states(model: FlatModel) -> List[str]:
    """Continuous variables that appear inside `der(...)` somewhere."""
    states: Set[str] = set()
    for equation in model.equations + model.initial_equations:
        states |= derivative_names(equation.lhs)
        states |= derivative_names(equation.rhs)
    for when_equation in model.when_equations:
        for statement in when_equation.body:
            states |= derivative_names(statement.value)

    for name in sorted(states):
        variable = model.variables.get(name)
        if variable is None or variable.kind != "continuous":
            raise ModelError(
                f"der({name}) is used, but {name!r} is not a continuous variable")
    # Keep declaration order: it makes the state vector readable.
    return [n for n in model.continuous_variables() if n in states]


def analyze(model: FlatModel, kind: str = "simulation") -> StructuralAnalysis:
    """
    Analyse the flat model.

    `kind='simulation'` builds the system solved at every time step: the states
    are known (the integrator has them) and their derivatives are unknown.

    `kind='initialization'` builds the system solved once at t = 0, where the
    states are unknown *as well*, and the extra `initial equation`s make up the
    difference.
    """
    states = find_states(model)
    continuous = model.continuous_variables()

    if kind == "simulation":
        equations = list(model.equations)
        unknowns = ([der_name(s) for s in states]
                    + [v for v in continuous if v not in states])
    elif kind == "initialization":
        equations = list(model.equations) + list(model.initial_equations)
        unknowns = [der_name(s) for s in states] + list(continuous)
    else:
        raise ValueError(f"unknown analysis kind {kind!r}")

    analysis = StructuralAnalysis(kind=kind, equations=equations,
                                  unknowns=unknowns, states=states)
    candidates = set(unknowns)
    analysis.incidence = [
        unknowns_in(equation.lhs, candidates) | unknowns_in(equation.rhs, candidates)
        for equation in equations
    ]

    _check_counts(analysis, model)
    analysis.matching = match_equations(analysis)
    analysis.blocks = sort_blocks(analysis)
    return analysis


def _check_counts(analysis: StructuralAnalysis, model: FlatModel):
    """The first sanity check every modeling tool performs."""
    number_of_equations = len(analysis.equations)
    number_of_unknowns = len(analysis.unknowns)
    if number_of_equations == number_of_unknowns:
        return
    what = "initialization problem" if analysis.kind == "initialization" else "model"
    difference = number_of_equations - number_of_unknowns
    hint = ("too many equations: remove one, or make a variable that you are "
            "prescribing into a parameter"
            if difference > 0 else
            "too few equations: every unknown needs an equation of its own")
    raise StructuralError(
        f"the {what} has {number_of_equations} equations but "
        f"{number_of_unknowns} unknowns ({hint})")


# =============================================================================
# Matching: which equation computes which unknown?
# =============================================================================

def match_equations(analysis: StructuralAnalysis) -> Dict[int, str]:
    """
    Find a perfect matching between equations and unknowns.

    The algorithm is the textbook augmenting-path search (Kuhn's algorithm):
    try to give every equation an unknown of its own; when an equation finds
    only unknowns that are taken, ask their current owners to step aside and
    take a different one, recursively.
    """
    assignment: Dict[str, int] = {}          # unknown -> equation index

    def try_assign(equation_index: int, visited: Set[str]) -> bool:
        for unknown in sorted(analysis.incidence[equation_index]):
            if unknown in visited:
                continue
            visited.add(unknown)
            owner = assignment.get(unknown)
            if owner is None or try_assign(owner, visited):
                assignment[unknown] = equation_index
                return True
        return False

    for index in range(len(analysis.equations)):
        if not try_assign(index, set()):
            _report_singularity(analysis, assignment)

    return {equation_index: unknown for unknown, equation_index in assignment.items()}


def _report_singularity(analysis: StructuralAnalysis, assignment: Dict[str, int]):
    """
    Explain a failed matching.

    The equation count came out right -- otherwise `_check_counts` would have
    complained already -- so some equations must be competing for the same
    unknowns while others have none left.  For a physically sensible model that
    almost always means the differential index is higher than one: there is a
    constraint between the states, so the states are not independent, and no
    state-space form exists without differentiating equations first.
    """
    matched_equations = set(assignment.values())
    unmatched_equations = [i for i in range(len(analysis.equations))
                           if i not in matched_equations]
    unmatched_unknowns = [u for u in analysis.unknowns if u not in assignment]

    lines = ["the model is structurally singular: there is no way to give "
             "every equation an unknown of its own.", ""]
    if unmatched_equations:
        lines.append("Equations left without an unknown to compute:")
        for index in unmatched_equations:
            equation = analysis.equations[index]
            lines.append(f"    eq {index + 1}: {equation.source}"
                         + (f"      [{equation.origin}]" if equation.origin else ""))
    if unmatched_unknowns:
        lines.append("Unknowns left without an equation to compute them:")
        for unknown in unmatched_unknowns:
            lines.append(f"    {unknown}")
    lines += [
        "",
        "This usually means the differential index of the model is higher "
        "than 1: the",
        "state variables are not independent, but tied together by a "
        "constraint. A model",
        "like a pendulum in Cartesian coordinates is the standard example -- "
        "x and y are",
        "states, yet x^2 + y^2 = L^2 must hold at all times.",
        "",
        "Real tools handle this by *index reduction*: Pantelides' algorithm "
        "finds the",
        "constraints that must be differentiated, differentiates them, and "
        "dummy-derivative",
        "selection then picks which variables stay states. TinySim deliberately "
        "stops here",
        "instead, so that the failure -- and the reason index reduction exists "
        "-- is visible.",
        "",
        "If the model is not meant to be high index, look for a variable that "
        "two equations",
        "are both trying to compute, or one that no equation computes at all.",
    ]
    raise StructuralError("\n".join(lines),
                          unmatched_equations=unmatched_equations,
                          unmatched_unknowns=unmatched_unknowns)


# =============================================================================
# Sorting: Tarjan's strongly connected components
# =============================================================================

def dependency_graph(analysis: StructuralAnalysis) -> Dict[int, Set[int]]:
    """
    Which equations must be solved before which.

    Equation *i* depends on equation *j* when *i* uses the unknown that *j* was
    matched with -- so *j* has to be computed first.
    """
    owner = analysis.assigned
    graph: Dict[int, Set[int]] = {}
    for index in range(len(analysis.equations)):
        needed = set()
        for unknown in analysis.incidence[index]:
            producer = owner[unknown]
            if producer != index:
                needed.add(producer)
        graph[index] = needed
    return graph


def sort_blocks(analysis: StructuralAnalysis) -> List[List[int]]:
    """
    Sort the matched equations into block lower triangular form.

    Tarjan's algorithm returns each strongly connected component only after
    everything it depends on has already been returned, so the components come
    out in exactly the order they must be solved.  A component with more than
    one equation is an algebraic loop.
    """
    graph = dependency_graph(analysis)
    index_counter = [0]
    indices: Dict[int, int] = {}
    lowlink: Dict[int, int] = {}
    stack: List[int] = []
    on_stack: Set[int] = set()
    components: List[List[int]] = []

    def strongconnect(node: int):
        indices[node] = lowlink[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack.add(node)

        for neighbour in sorted(graph[node]):
            if neighbour not in indices:
                strongconnect(neighbour)
                lowlink[node] = min(lowlink[node], lowlink[neighbour])
            elif neighbour in on_stack:
                lowlink[node] = min(lowlink[node], indices[neighbour])

        if lowlink[node] == indices[node]:          # `node` is a component root
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            strongconnect(node)
    return components
