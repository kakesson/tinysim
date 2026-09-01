"""
    TinySim

A tiny equation-based, acausal modeling language, for teaching -- rebuilt on
ModelingToolkit.

The pipeline is:

    .tiny source
      -> lexer, parser          (ours)
      -> abstract syntax tree   (ours)
      -> a ModelingToolkit `System`
      -> expand_connections     (MTK: the flat model)
      -> mtkcompile             (MTK: alias elimination, matching, tearing)
      -> ODEProblem, solve      (OrdinaryDiffEq)
      -> contracts, reports     (ours)

What is ours to write is the front end, the translation into MTK, the contract
layer, and -- most importantly -- the reports, whose job is to make MTK's
stages legible. See `docs/julia-migration-plan.md`.

This is phase 0 of that migration: the scaffold, and the golden files that
define what the port has to reproduce.
"""
module TinySim

using JSON3

include("lexer.jl")
include("ast.jl")
include("contracts.jl")
include("parser.jl")

export golden, golden_names, GOLDEN_DIRECTORY
export parse, parse_file, tokenize, to_source
export Program, ClassDefinition, Declaration, AutomatonDefinition, Contract
export TinySimSyntaxError

"""
The directory holding the golden files: what the Python implementation says
about each example. They are the definition of "the same" for this port.
"""
const GOLDEN_DIRECTORY = normpath(joinpath(@__DIR__, "..", "..", "..", "golden"))

"""
    golden_names() -> Vector{String}

Every example that has a golden file, by name.
"""
golden_names() = sort([replace(name, ".json" => "")
                       for name in readdir(GOLDEN_DIRECTORY) if endswith(name, ".json")])

"""
    golden(name) -> JSON3.Object

Read one golden file: the flat equations, what alias elimination removed, the
solution order, a sampled simulation, the events, and every contract margin, as
the Python implementation reported them.

    julia> record = golden("bouncing_ball");
    julia> length(record.simulation.events)
    6
"""
function golden(name::AbstractString)
    path = joinpath(GOLDEN_DIRECTORY, endswith(name, ".json") ? name : name * ".json")
    isfile(path) || error("no golden file for $name; expected $path")
    JSON3.read(read(path, String))
end

"""
    agrees(value, reference, tolerance) -> Bool

Whether a number reproduces the reference within the tolerance recorded in the
golden file. The two implementations use different integrators and are not
expected to agree bit for bit; they are expected to agree about the model.
"""
function agrees(value::Real, reference::Real, tolerance)
    isfinite(value) || return false
    difference = abs(value - reference)
    difference <= tolerance.absolute ||
        difference <= tolerance.relative * max(abs(reference), 1e-12)
end

# ---------------------------------------------------------------------------
# Still to come, in the order of `docs/julia-migration-plan.md`:
#
#   phase 1  lexer.jl, ast.jl, parser.jl        the .tiny front end   (done)
#   phase 2  translate.jl                       AST -> ModelingToolkit
#   phase 3  inspect.jl                         MTK's stages, as data
#   phase 4  simulate.jl                        problems, solvers, events
#   phase 5  contracts.jl, monitor.jl           assume-guarantee contracts
#   phase 6  report.jl, htmlreport.jl, cli.jl   the reports
#
# Nothing is stubbed out here: a function that does not exist is more honest
# than one that pretends.
# ---------------------------------------------------------------------------

end # module TinySim
