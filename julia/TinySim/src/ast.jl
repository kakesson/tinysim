# The abstract syntax tree: the data the parser produces.
#
# Two families live here -- expressions, which are the sides of an equation,
# and the structural nodes that make up a model file. Everything is a small
# immutable struct, so a parsed model can be inspected just by printing it.

# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

"""Base type of every expression node."""
abstract type Expression end

"""A literal number, `9.81`."""
struct NumberLiteral <: Expression
    value::Float64
end

"""A reference to a variable, possibly dotted: `R`, `c.v`, `emf.p.i`."""
struct VariableRef <: Expression
    name::String
end

"""A binary operation: `+ - * / ^ < <= > >= == <> and or`."""
struct BinaryOp <: Expression
    operator::String
    left::Expression
    right::Expression
end

"""A unary operation: `-x` or `not x`."""
struct UnaryOp <: Expression
    operator::String
    operand::Expression
end

"""
A function call: `sin(phi)`, `der(w)`, `pre(on)`.

`der` and `pre` are parsed as ordinary calls and given their meaning later, in
the translation to ModelingToolkit -- which keeps the parser simple and puts
the semantics where a reader goes looking for it.
"""
struct FunctionCall <: Expression
    name::String
    arguments::Vector{Expression}
end

"""`if cond then a else b` -- an expression, not a statement."""
struct IfExpression <: Expression
    condition::Expression
    then_value::Expression
    else_value::Expression
end

# ---------------------------------------------------------------------------
# Statements: what runs inside a `when` body
# ---------------------------------------------------------------------------

"""
Base type of the statements in a `when` body.

A `when` body is a piece of software: its statements run in order, and each
sees what the ones before it assigned.
"""
abstract type Statement end

"""`x := expr;` -- sequential assignment to a discrete variable."""
struct Assignment <: Statement
    name::String
    value::Expression
    line::Int
end

# A whole-record assignment, `s := t;`, is parsed as an ordinary `Assignment`
# whose value happens to be a plain reference. Whether it means one variable or
# a record's worth of fields cannot be known until the model is flattened, so
# that is where it is expanded.

"""`reinit(x, expr);` -- a jump in a continuous state."""
struct Reinit <: Statement
    name::String
    value::Expression
    line::Int
end

"""
`if ... then ... elseif ... else ... end if;` inside a `when` body.

`branches` pairs each condition with its statements; a final branch with no
condition is the `else`.
"""
struct IfStatement <: Statement
    conditions::Vector{Expression}
    branches::Vector{Vector{Statement}}
    otherwise::Vector{Statement}
    line::Int
end

# ---------------------------------------------------------------------------
# Equations
# ---------------------------------------------------------------------------

abstract type ModelEquation end

"""`lhs = rhs;` -- a relation, not an assignment."""
struct SimpleEquation <: ModelEquation
    left::Expression
    right::Expression
    line::Int
end

"""`connect(a, b);` -- records a connection; becomes equations when flattened."""
struct ConnectEquation <: ModelEquation
    first::String
    second::String
    line::Int
end

"""
The condition of a `when`: either an expression, or `sample(t0, Ts)`.

A sampled `when` fires at instants that are known in advance, so they are
scheduled rather than located.
"""
abstract type WhenCondition end

struct ExpressionCondition <: WhenCondition
    expression::Expression
end

struct SampleCondition <: WhenCondition
    start::Expression
    interval::Expression
end

"""`when cond then ... end;`"""
struct WhenEquation <: ModelEquation
    condition::WhenCondition
    body::Vector{Statement}
    line::Int
end

# ---------------------------------------------------------------------------
# Declarations and classes
# ---------------------------------------------------------------------------

"""
One declared name:

    parameter Real R = 100 "resistance [Ohm]";
    Real v(start = 0);
    Capacitor c(C = 1e-3, v(start = 0));

`type_name` is `"Real"` or the name of a connector, model or record, which is
what decides whether flattening makes a variable or instantiates a component.
"""
struct Declaration
    name::String
    type_name::String
    prefixes::Vector{Symbol}
    modifiers::Dict{String, Any}
    value::Union{Expression, Nothing}
    description::String
    line::Int
end

is_parameter(declaration::Declaration) =
    :parameter in declaration.prefixes || :constant in declaration.prefixes
is_discrete(declaration::Declaration) = :discrete in declaration.prefixes
is_flow(declaration::Declaration) = :flow in declaration.prefixes
is_variable(declaration::Declaration) = declaration.type_name == "Real"

"""A `model`, `connector` or `record` definition."""
struct ClassDefinition
    kind::Symbol                       # :model, :connector or :record
    name::String
    partial::Bool
    extends::Vector{String}
    declarations::Vector{Declaration}
    equations::Vector{ModelEquation}
    initial_equations::Vector{SimpleEquation}
    description::String
    line::Int
end

# ---------------------------------------------------------------------------
# State machines
# ---------------------------------------------------------------------------

"""`from -> to when guard then actions;`"""
struct Transition
    from::String
    to::String
    guard::Expression
    actions::Vector{Statement}
    line::Int
end

"""
`automaton S sampled at Ts ... end S;`

Level-triggered on its own rate: at each tick the transitions leaving the
active state are tested in the order written, and the first whose guard holds
is taken.
"""
struct AutomatonDefinition
    name::String
    rate::Expression
    declarations::Vector{Declaration}
    states::Vector{String}
    initial::String
    transitions::Vector{Transition}
    description::String
    line::Int
end

# ---------------------------------------------------------------------------
# A parsed file
# ---------------------------------------------------------------------------

"""
Everything one `.tiny` file defines, in declaration order.

Order matters: the model a file is *about* is conventionally the last one, and
reports list things the way they were written.
"""
struct Program
    classes::Vector{ClassDefinition}
    automata::Vector{AutomatonDefinition}
    contracts::Vector{Any}             # filled in by contracts.jl, phase 5
end

Program() = Program(ClassDefinition[], AutomatonDefinition[], Any[])

"""Look a class up by name, or `nothing`."""
function find_class(program::Program, name::AbstractString)
    index = findfirst(class -> class.name == name, program.classes)
    index === nothing ? nothing : program.classes[index]
end

find_automaton(program::Program, name::AbstractString) =
    (index = findfirst(a -> a.name == name, program.automata);
     index === nothing ? nothing : program.automata[index])

# ---------------------------------------------------------------------------
# Printing expressions back as text
# ---------------------------------------------------------------------------

const PRECEDENCE = Dict("or" => 1, "and" => 2,
                        "<" => 3, "<=" => 3, ">" => 3, ">=" => 3, "==" => 3, "<>" => 3,
                        "+" => 4, "-" => 4, "*" => 5, "/" => 5, "^" => 7)

"""
    to_source(expression) -> String

Print an expression as readable `.tiny` source, with the parentheses that are
needed and no others.
"""
function to_source(expression::Expression, parent_precedence::Int = 0)
    if expression isa NumberLiteral
        value = expression.value
        return value == round(value) && abs(value) < 1e15 ?
               string(Int(round(value))) : string(value)
    elseif expression isa VariableRef
        return expression.name
    elseif expression isa FunctionCall
        return string(expression.name, "(",
                      join(to_source.(expression.arguments), ", "), ")")
    elseif expression isa UnaryOp
        inner = to_source(expression.operand, 6)
        return expression.operator == "not" ? "not $inner" : "-$inner"
    elseif expression isa IfExpression
        text = string("if ", to_source(expression.condition),
                      " then ", to_source(expression.then_value),
                      " else ", to_source(expression.else_value))
        return parent_precedence > 0 ? "($text)" : text
    elseif expression isa BinaryOp
        # `a + -b` reads better as `a - b`.
        if expression.operator == "+" && expression.right isa UnaryOp &&
           expression.right.operator == "-"
            return to_source(BinaryOp("-", expression.left, expression.right.operand),
                             parent_precedence)
        end
        precedence = PRECEDENCE[expression.operator]
        left = to_source(expression.left, precedence)
        right = to_source(expression.right, precedence + 1)
        text = expression.operator == "^" ? "$left^$right" :
               "$left $(expression.operator) $right"
        return precedence < parent_precedence ? "($text)" : text
    end
    error("cannot print $(typeof(expression))")
end

to_source(equation::SimpleEquation) =
    string(to_source(equation.left), " = ", to_source(equation.right))
