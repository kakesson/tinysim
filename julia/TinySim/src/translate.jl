# Stage 3 of the pipeline: turning the syntax tree into a ModelingToolkit system.
#
# This is where TinySim stops doing the work itself. Everything after this
# point -- expanding `connect`, eliminating aliases, matching, tearing,
# generating code -- is MTK's, and the reports' job is to make those stages
# legible. What happens here is the translation, and it is worth reading
# because it is where a construct of the language becomes a construct of a real
# modeling tool.
#
# The map, in one table:
#
#     .tiny                        ModelingToolkit
#     ---------------------------  -----------------------------------------
#     connector C ... end          a System of variables, marked a connector
#     flow Real i                  a variable carrying `Flow` metadata
#     model M ... end              a System with subsystems and equations
#     record R ... end             a System with variables and no equations
#     Capacitor c(C = 1e-3)        a subsystem built with that default
#     Real v(start = 0)            a variable with that default value
#     discrete Real u              a *held* state: an unknown with D(u) ~ 0
#     connect(a, b)                connect(a, b)
#     when h < 0 then ... end      a continuous event on the crossing h ~ 0
#     when sample(0, Ts) then ...  a periodic discrete event, scheduled
#     pre(x)                       Pre(x)
#
# Three traps are paid for here, each found by experiment before this file was
# written (see `docs/julia-migration-plan.md` §3):
#
#   * every variable and parameter is declared on the `System` explicitly,
#     because MTK infers them from the equations and would miss one that
#     appears only inside an event;
#   * a discrete variable is a held state, not a parameter, because an affect
#     written as an equation may not assign a parameter;
#   * `start` reaches MTK as a default, or initialization is underdetermined.

using ModelingToolkit
using ModelingToolkit: t_nounits as t, D_nounits as D
using Symbolics
using Accessors: @set

const MTK = ModelingToolkit

"""Raised when a model is syntactically fine but cannot be translated."""
struct ModelError <: Exception
    message::String
end

Base.showerror(io::IO, error::ModelError) = print(io, error.message)

# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------

"""A time-dependent unknown, with an optional default value."""
function make_variable(name::Symbol, default = nothing)
    symbol = only(@variables $name(t))
    return default === nothing ? symbol : Symbolics.setdefaultval(symbol, default)
end

"""A parameter, which always has a value."""
function make_parameter(name::Symbol, default)
    symbol = only(@parameters $name)
    return Symbolics.setdefaultval(symbol, default)
end

"""Mark a variable as a flow: what `connect` must sum to zero."""
as_flow(symbol) = Symbolics.setmetadata(symbol, MTK.VariableConnectType, MTK.Flow)

# ---------------------------------------------------------------------------
# One instance being built
# ---------------------------------------------------------------------------

"""
What a class expands into: the symbols it declares, the subsystems it contains,
and the equations that relate them.

`scope` maps a local name to what it stands for -- a symbol, or a subsystem --
which is what expression translation looks names up in.
"""
mutable struct Instance
    scope::Dict{String, Any}
    unknowns::Vector{Any}
    parameters::Vector{Any}
    equations::Vector{Equation}
    subsystems::Vector{Any}
    continuous_events::Vector{Any}
    discrete_events::Vector{Any}
    discrete_names::Set{String}          # held states, for the report
end

Instance() = Instance(Dict{String, Any}(), [], [], Equation[], [], [], [], Set{String}())

# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------

"""
Copy everything from the base classes in first.

`extends` in TinySim is literally textual reuse, which is a fair picture of
what it means in Modelica too.
"""
function resolve_inheritance(program::Program, class::ClassDefinition)
    isempty(class.extends) && return class
    declarations = Declaration[]
    equations = ModelEquation[]
    initial_equations = SimpleEquation[]
    for base_name in class.extends
        base = find_class(program, base_name)
        base === nothing && throw(ModelError("$(class.name) extends $base_name, " *
                                             "which is not defined here"))
        merged = resolve_inheritance(program, base)
        append!(declarations, merged.declarations)
        append!(equations, merged.equations)
        append!(initial_equations, merged.initial_equations)
    end
    append!(declarations, class.declarations)
    append!(equations, class.equations)
    append!(initial_equations, class.initial_equations)

    names = [declaration.name for declaration in declarations]
    duplicates = unique([name for name in names if count(==(name), names) > 1])
    isempty(duplicates) || throw(ModelError(
        "model $(class.name) inherits declarations that clash: " *
        join(sort(duplicates), ", ")))

    return ClassDefinition(class.kind, class.name, class.partial, String[],
                           declarations, equations, initial_equations,
                           class.description, class.line)
end

# ---------------------------------------------------------------------------
# Building an instance
# ---------------------------------------------------------------------------

"""
    build(program, model_name) -> System

Translate one model of a parsed program into a ModelingToolkit system, ready
for `expand_connections` and `mtkcompile`.
"""
function build(program::Program, model_name::AbstractString)
    class = find_class(program, model_name)
    class === nothing && throw(ModelError("no class named $(repr(model_name))"))
    class.kind === :model || throw(ModelError(
        "$(repr(model_name)) is a $(class.kind), not a model"))
    class.partial && throw(ModelError(
        "$(repr(model_name)) is a partial model and cannot be simulated on its " *
        "own; it exists to be inherited from with `extends`"))
    return build_instance(program, class, Symbol(model_name), Dict{String, Any}())
end

function build_instance(program::Program, class::ClassDefinition, name::Symbol,
                        modifiers::Dict{String, Any})
    expanded = resolve_inheritance(program, class)
    instance = Instance()

    for declaration in expanded.declarations
        modifier = get(modifiers, declaration.name, nothing)
        if is_variable(declaration)
            add_symbol!(instance, declaration, modifier)
        else
            add_subsystem!(program, instance, declaration, modifier)
        end
    end

    for equation in expanded.equations
        add_equation!(instance, equation)
    end

    # `initial equation` is a second system, solved once before the run: the
    # states are unknowns there too, and these equations are what pays for them.
    initialization = [to_symbolic(equation.left, instance.scope) ~
                      to_symbolic(equation.right, instance.scope)
                      for equation in expanded.initial_equations]

    system = System(instance.equations, t, instance.unknowns, instance.parameters;
                    name,
                    systems = instance.subsystems,
                    continuous_events = instance.continuous_events,
                    discrete_events = instance.discrete_events,
                    initialization_eqs = initialization)
    if class.kind === :connector
        system = @set system.connector_type = MTK.connector_type(system)
    end
    return system
end

"""One `Real` declaration: a parameter, a held discrete state, or an unknown."""
function add_symbol!(instance::Instance, declaration::Declaration, modifier)
    name = Symbol(declaration.name)

    if is_parameter(declaration)
        value = modifier isa Expression ? constant_value(modifier) :
                declaration.value !== nothing ? constant_value(declaration.value) :
                throw(ModelError("parameter $(declaration.name) has no value; give " *
                                 "it one either in its declaration or when the " *
                                 "component is instantiated"))
        symbol = make_parameter(name, value)
        push!(instance.parameters, symbol)
        instance.scope[declaration.name] = symbol
        return
    end

    start = start_value(declaration, modifier)
    symbol = make_variable(name, start)
    is_flow(declaration) && (symbol = as_flow(symbol))
    push!(instance.unknowns, symbol)
    instance.scope[declaration.name] = symbol

    if is_discrete(declaration)
        # A discrete variable is piecewise constant: a state that only events
        # move. Saying so with an equation keeps the system balanced and lets
        # every affect stay symbolic.
        push!(instance.equations, D(symbol) ~ 0)
        push!(instance.discrete_names, declaration.name)
    end
end

"""The `start` attribute, from the declaration or from a modifier on it."""
function start_value(declaration::Declaration, modifier)
    attributes = declaration.modifiers
    if modifier isa Dict
        attributes = merge(attributes, modifier)
    end
    haskey(attributes, "start") || return nothing
    return constant_value(attributes["start"])
end

"""A component or record: build it, with the modifiers handed down."""
function add_subsystem!(program::Program, instance::Instance,
                        declaration::Declaration, modifier)
    class = find_class(program, declaration.type_name)
    class === nothing && throw(ModelError(
        "$(declaration.name): no class named $(repr(declaration.type_name))"))
    class.partial && throw(ModelError(
        "$(declaration.name): $(repr(declaration.type_name)) is partial and " *
        "cannot be instantiated"))

    merged = copy(declaration.modifiers)
    if modifier isa Dict
        merged = merge(merged, modifier)
    elseif modifier !== nothing
        throw(ModelError("$(declaration.name): a component cannot be given a " *
                         "value with '='"))
    end

    subsystem = build_instance(program, class, Symbol(declaration.name), merged)
    push!(instance.subsystems, subsystem)
    instance.scope[declaration.name] = subsystem
end

# ---------------------------------------------------------------------------
# Equations
# ---------------------------------------------------------------------------

function add_equation!(instance::Instance, equation::SimpleEquation)
    push!(instance.equations,
          to_symbolic(equation.left, instance.scope) ~
          to_symbolic(equation.right, instance.scope))
end

function add_equation!(instance::Instance, equation::ConnectEquation)
    push!(instance.equations,
          connect(resolve(equation.first, instance.scope),
                  resolve(equation.second, instance.scope)))
end

function add_equation!(instance::Instance, equation::WhenEquation)
    affect = affect_equations(equation.body, instance.scope)
    if equation.condition isa SampleCondition
        start = constant_value(equation.condition.start)
        start == 0 || throw(ModelError(
            "sample(t0, Ts) with t0 = $start: only a first tick at 0 is " *
            "supported, because the period is what schedules the event"))
        period = constant_value(equation.condition.interval)
        push!(instance.discrete_events, period => affect)
    else
        push!(instance.continuous_events,
              crossing_equation(equation.condition.expression, instance.scope) => affect)
    end
end

"""
The crossing an event watches.

A `when` fires when its condition *becomes* true, so `when h < 0` is watched as
the instant `h` reaches zero. MTK takes that as an equation.
"""
function crossing_equation(condition::Expression, scope)
    condition isa BinaryOp && condition.operator in ("<", "<=", ">", ">=") ||
        throw(ModelError("a 'when' condition must be a simple comparison such as " *
                         "'h < 0' or 'time > 2', or sample(t0, Ts)"))
    return [to_symbolic(condition.left, scope) ~ to_symbolic(condition.right, scope)]
end

# ---------------------------------------------------------------------------
# Event bodies: sequential statements become one equation per variable
# ---------------------------------------------------------------------------

"""
Turn the statements of a `when` body into affect equations.

The body is *software*: its statements run in order, and each sees what the
ones before it assigned. MTK's affects are equations, which are simultaneous,
so the order is resolved here by substitution -- an assignment's right-hand
side is built from what has been assigned so far, and anything not yet
assigned is read as its pre-event value, `Pre(x)`.

    e := r - y;                     ->  e        ~ Pre(r) - Pre(y)
    integral := pre(integral) + e;  ->  integral ~ Pre(integral) + (Pre(r) - Pre(y))
    u := Kp * e + integral;         ->  u        ~ Kp*(...) + (Pre(integral) + ...)
"""
function affect_equations(statements::Vector{Statement}, scope)
    assigned = Pair{Any, Any}[]                   # symbol => expression, in order
    for statement in statements
        apply_statement!(assigned, statement, scope)
    end
    return [symbol ~ value for (symbol, value) in assigned]
end

function apply_statement!(assigned, statement::Assignment, scope)
    symbol = resolve(statement.name, scope)
    assign!(assigned, symbol, event_expression(statement.value, scope, assigned))
end

function apply_statement!(assigned, statement::Reinit, scope)
    symbol = resolve(statement.name, scope)
    assign!(assigned, symbol, event_expression(statement.value, scope, assigned))
end

function apply_statement!(assigned, statement::IfStatement, scope)
    # Each branch is evaluated against a copy of what has been assigned so far;
    # the branches are then merged into one conditional expression per variable.
    branches = Vector{Pair{Any, Any}}[]
    for body in statement.branches
        copied = copy(assigned)
        for inner in body
            apply_statement!(copied, inner, scope)
        end
        push!(branches, copied)
    end
    otherwise = copy(assigned)
    for inner in statement.otherwise
        apply_statement!(otherwise, inner, scope)
    end

    touched = Any[]
    for branch in vcat(branches, [otherwise]), (symbol, _) in branch
        any(isequal(symbol), touched) || push!(touched, symbol)
    end

    for symbol in touched
        value = lookup(otherwise, symbol, Pre(symbol))
        for index in length(branches):-1:1
            condition = event_expression(statement.conditions[index], scope, assigned)
            value = ifelse(condition, lookup(branches[index], symbol, Pre(symbol)), value)
        end
        assign!(assigned, symbol, value)
    end
end

lookup(assigned, symbol, fallback) =
    (index = findfirst(pair -> isequal(pair.first, symbol), assigned);
     index === nothing ? fallback : assigned[index].second)

function assign!(assigned, symbol, value)
    index = findfirst(pair -> isequal(pair.first, symbol), assigned)
    index === nothing ? push!(assigned, symbol => value) :
                        (assigned[index] = symbol => value)
    return assigned
end

"""
An expression inside an event body.

A name that this body has already assigned stands for the expression assigned
to it; anything else is read as it was *before* the event, which is what makes
the body sequential rather than simultaneous.
"""
function event_expression(expression::Expression, scope, assigned)
    if expression isa VariableRef && expression.name != "time"
        symbol = resolve(expression.name, scope)
        return lookup(assigned, symbol, Pre(symbol))
    elseif expression isa FunctionCall && expression.name == "pre"
        # `pre(x)` is the value before the event, whatever has been assigned.
        return Pre(resolve(expression.arguments[1].name, scope))
    elseif expression isa NumberLiteral
        return expression.value
    elseif expression isa VariableRef                      # time
        return t
    elseif expression isa UnaryOp
        inner = event_expression(expression.operand, scope, assigned)
        return expression.operator == "-" ? -inner : 1 - inner
    elseif expression isa BinaryOp
        return apply_operator(expression.operator,
                              event_expression(expression.left, scope, assigned),
                              event_expression(expression.right, scope, assigned))
    elseif expression isa FunctionCall
        return apply_function(expression.name,
                              [event_expression(argument, scope, assigned)
                               for argument in expression.arguments])
    elseif expression isa IfExpression
        return ifelse(event_expression(expression.condition, scope, assigned),
                      event_expression(expression.then_value, scope, assigned),
                      event_expression(expression.else_value, scope, assigned))
    end
    throw(ModelError("cannot translate $(to_source(expression)) inside an event"))
end

# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

"""Translate a model expression into a symbolic one, in the given scope."""
function to_symbolic(expression::Expression, scope)
    if expression isa NumberLiteral
        return expression.value
    elseif expression isa VariableRef
        return expression.name == "time" ? t : resolve(expression.name, scope)
    elseif expression isa UnaryOp
        inner = to_symbolic(expression.operand, scope)
        return expression.operator == "-" ? -inner : 1 - inner
    elseif expression isa BinaryOp
        return apply_operator(expression.operator,
                              to_symbolic(expression.left, scope),
                              to_symbolic(expression.right, scope))
    elseif expression isa FunctionCall
        expression.name == "der" &&
            return D(to_symbolic(expression.arguments[1], scope))
        expression.name == "pre" &&
            return Pre(to_symbolic(expression.arguments[1], scope))
        return apply_function(expression.name,
                              [to_symbolic(argument, scope)
                               for argument in expression.arguments])
    elseif expression isa IfExpression
        return ifelse(to_symbolic(expression.condition, scope),
                      to_symbolic(expression.then_value, scope),
                      to_symbolic(expression.else_value, scope))
    end
    throw(ModelError("cannot translate $(to_source(expression))"))
end

function apply_operator(operator, left, right)
    operator == "+" && return left + right
    operator == "-" && return left - right
    operator == "*" && return left * right
    operator == "/" && return left / right
    operator == "^" && return left^right
    operator == "<" && return left < right
    operator == "<=" && return left <= right
    operator == ">" && return left > right
    operator == ">=" && return left >= right
    operator == "==" && return left ~ right
    operator == "and" && return left & right
    operator == "or" && return left | right
    throw(ModelError("unknown operator $(repr(operator))"))
end

function apply_function(name, arguments)
    table = Dict("sin" => sin, "cos" => cos, "tan" => tan, "asin" => asin,
                 "acos" => acos, "atan" => atan, "atan2" => atan, "exp" => exp,
                 "log" => log, "log10" => log10, "sqrt" => sqrt, "abs" => abs,
                 "sign" => sign, "tanh" => tanh, "min" => min, "max" => max)
    haskey(table, name) || throw(ModelError("unknown function $name()"))
    return table[name](arguments...)
end

"""
Look a possibly dotted name up: `v` in this scope, `c.v` inside a subsystem.
"""
function resolve(name::AbstractString, scope)
    parts = split(name, '.')
    haskey(scope, parts[1]) ||
        throw(ModelError("unknown variable $(repr(name))"))
    value = scope[parts[1]]
    for part in parts[2:end]
        value = getproperty(value, Symbol(part))
    end
    return value
end

"""A parameter value or a `start` attribute: a number, known before the run."""
function constant_value(expression::Expression)
    expression isa NumberLiteral && return expression.value
    expression isa UnaryOp && expression.operator == "-" &&
        return -constant_value(expression.operand)
    if expression isa BinaryOp
        left = constant_value(expression.left)
        right = constant_value(expression.right)
        return apply_operator(expression.operator, left, right)
    end
    throw(ModelError("$(to_source(expression)) must be a number here"))
end
