# Assume-guarantee contracts: the syntax tree.
#
# A contract says what a model needs from its environment and what it promises
# in return, and it is read as *assume implies guarantee* -- a run in which an
# assumption fails says nothing about the component.
#
# Two trees live here. The *surface* tree is what the user wrote, patterns and
# all, so that reports can print the requirement in its own words. The *core*
# tree is Signal Temporal Logic. Phase 1 builds the surface tree; the
# desugaring onto the core tree and the robust semantics arrive in phase 5.

"""Base type of every formula node, surface and core alike."""
abstract type Formula end

"""A time window `[a, b]`; `nothing` means "to the end of the run"."""
const Window = Tuple{Union{Expression, Nothing}, Union{Expression, Nothing}}

const UNBOUNDED = (nothing, nothing)

# -- core -------------------------------------------------------------------

"""A comparison between two model expressions: `c.v >= 0.95 * src.V`."""
struct Predicate <: Formula
    operator::String
    left::Expression
    right::Expression
end

struct NotFormula <: Formula
    formula::Formula
end

struct AndFormula <: Formula
    parts::Vector{Formula}
end

struct OrFormula <: Formula
    parts::Vector{Formula}
end

struct ImpliesFormula <: Formula
    left::Formula
    right::Formula
end

"""`G[a,b] phi` -- true at t when phi holds everywhere in t+[a,b]."""
struct Always <: Formula
    formula::Formula
    window::Window
end

"""`F[a,b] phi` -- true at t when phi holds somewhere in t+[a,b]."""
struct Eventually <: Formula
    formula::Formula
    window::Window
end

"""`phi U[a,b] psi`."""
struct Until <: Formula
    left::Formula
    right::Formula
    window::Window
end

"""The instant a condition becomes true: false before, true now."""
struct Rise <: Formula
    formula::Formula
end

struct AtStart <: Formula
    formula::Formula
end

struct AtEnd <: Formula
    formula::Formula
end

# -- surface patterns -------------------------------------------------------

struct Never <: Formula
    formula::Formula
end

struct After <: Formula
    time::Expression
    formula::Formula
end

struct During <: Formula
    window::Window
    formula::Formula
end

"""`whenever c then r within [a, b]`, or `... then r holds for d`."""
struct Whenever <: Formula
    trigger::Formula
    response::Formula
    window::Window
    holds::Bool
end

"""`x stays within [lo, hi]`."""
struct StaysWithin <: Formula
    subject::Expression
    low::Expression
    high::Expression
end

"""`x settles to value within tolerance after t`."""
struct SettlesTo <: Formula
    subject::Expression
    value::Expression
    tolerance::Expression
    after::Union{Expression, Nothing}
end

# -- a contract -------------------------------------------------------------

"""One line of an `assume` or `guarantee` section."""
struct Clause
    formula::Formula
    line::Int
end

"""`contract Name for Model ... end Name;`"""
struct Contract
    name::String
    model_name::String
    description::String
    assumptions::Vector{Clause}
    guarantees::Vector{Clause}
    line::Int
end

# -- printing ---------------------------------------------------------------

function window_source(window::Window)
    low, high = window
    low === nothing && high === nothing && return ""
    low_text = low === nothing ? "0" : to_source(low)
    high_text = high === nothing ? "end" : to_source(high)
    return "[$low_text, $high_text]"
end

"""
    to_source(formula) -> String

Print a formula the way it was written, patterns included.
"""
function to_source(formula::Formula)
    if formula isa Predicate
        return string(to_source(formula.left), " ", formula.operator, " ",
                      to_source(formula.right))
    elseif formula isa NotFormula
        return "not " * bracket(formula.formula)
    elseif formula isa AndFormula
        return join(bracket.(formula.parts), " and ")
    elseif formula isa OrFormula
        return join(bracket.(formula.parts), " or ")
    elseif formula isa ImpliesFormula
        return string(bracket(formula.left), " implies ", bracket(formula.right))
    elseif formula isa Always
        window = window_source(formula.window)
        prefix = isempty(window) ? "always" : "always within $window"
        return string(prefix, " ", bracket(formula.formula))
    elseif formula isa Eventually
        window = window_source(formula.window)
        prefix = isempty(window) ? "eventually" : "eventually within $window"
        return string(prefix, " ", bracket(formula.formula))
    elseif formula isa Until
        window = window_source(formula.window)
        middle = isempty(window) ? "until" : "until within $window"
        return string(bracket(formula.left), " ", middle, " ", bracket(formula.right))
    elseif formula isa Rise
        return "rise(" * to_source(formula.formula) * ")"
    elseif formula isa AtStart
        return "at start " * bracket(formula.formula)
    elseif formula isa AtEnd
        return "at end " * bracket(formula.formula)
    elseif formula isa Never
        return "never " * bracket(formula.formula)
    elseif formula isa After
        return string("after ", to_source(formula.time), " ", bracket(formula.formula))
    elseif formula isa During
        return string("during ", window_source(formula.window), " ",
                      bracket(formula.formula))
    elseif formula isa Whenever
        tail = formula.holds ? "holds for " * to_source(formula.window[2]) :
               "within " * window_source(formula.window)
        return string("whenever ", bracket(formula.trigger), " then ",
                      bracket(formula.response), " ", tail)
    elseif formula isa StaysWithin
        return string(to_source(formula.subject), " stays within [",
                      to_source(formula.low), ", ", to_source(formula.high), "]")
    elseif formula isa SettlesTo
        text = string(to_source(formula.subject), " settles to ",
                      to_source(formula.value), " within ", to_source(formula.tolerance))
        return formula.after === nothing ? text :
               text * " after " * to_source(formula.after)
    end
    error("cannot print $(typeof(formula))")
end

"""Parenthesise anything that is not already a single comparison."""
bracket(formula::Formula) =
    formula isa Predicate || formula isa Rise ? to_source(formula) :
    "(" * to_source(formula) * ")"

clauses(contract::Contract) =
    vcat([("assume", clause) for clause in contract.assumptions],
         [("guarantee", clause) for clause in contract.guarantees])
