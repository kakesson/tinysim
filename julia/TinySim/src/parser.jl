# Stage 2 of the pipeline: turning tokens into a syntax tree.
#
# A hand-written recursive-descent parser: one small function per rule of the
# grammar in `docs/language.md`. Reading the functions top to bottom is reading
# the grammar. Expression parsing uses the usual precedence ladder, one
# function per level, loosest operator first -- which is why `a + b * c` groups
# as `a + (b * c)` with no special handling.

const KEYWORDS = Set([
    "model", "connector", "record", "automaton", "partial", "extends", "end",
    "equation", "initial", "parameter", "constant", "discrete", "flow",
    "potential", "connect", "when", "then", "if", "else", "elseif", "and", "or",
    "not", "der", "pre", "reinit", "Real", "time", "sample",
    "contract", "assume", "guarantee",
])

const VARIABILITY_PREFIXES = ["parameter", "constant", "discrete"]
const CONNECTION_PREFIXES = ["flow", "potential"]
const RELATIONAL_OPERATORS = ["<", "<=", ">", ">=", "==", "<>"]

"""
Functions a model may call. A closed list means a typo such as `sinn(x)` is
caught while parsing rather than at simulation time.
"""
const BUILTIN_FUNCTIONS = Set(["sin", "cos", "tan", "asin", "acos", "atan",
                               "atan2", "exp", "log", "log10", "sqrt", "abs",
                               "sign", "tanh", "min", "max", "der", "pre"])

mutable struct Parser
    tokens::Vector{Token}
    filename::String
    position::Int
end

Parser(source::AbstractString; filename::AbstractString = "<string>") =
    Parser(tokenize(source; filename), filename, 1)

# -- token handling ---------------------------------------------------------

current(parser::Parser) = parser.tokens[parser.position]
peek(parser::Parser, offset::Int = 1) =
    parser.tokens[min(parser.position + offset, length(parser.tokens))]

"""True when the current token is exactly `text`."""
at(parser::Parser, text::AbstractString) =
    current(parser).text == text && current(parser).kind in (:operator, :identifier)

"""Consume the current token if it matches; report whether it did."""
function accept!(parser::Parser, text::AbstractString)
    at(parser, text) || return false
    parser.position += 1
    return true
end

"""Consume the current token, or fail with a message that points at it."""
function expect!(parser::Parser, text::AbstractString)
    at(parser, text) ||
        error_at(parser, "expected $(repr(text)) but found $(repr(current(parser).text))")
    token = current(parser)
    parser.position += 1
    return token
end

function expect_kind!(parser::Parser, kind::Symbol)
    current(parser).kind === kind ||
        error_at(parser, "expected $(lowercase(String(kind))) but found " *
                         "$(repr(current(parser).text))")
    token = current(parser)
    parser.position += 1
    return token
end

"""Consume a plain identifier -- not a keyword."""
function identifier!(parser::Parser)
    token = expect_kind!(parser, :identifier)
    token.text in KEYWORDS &&
        error_at(parser, "$(repr(token.text)) is a keyword and cannot be a name")
    return token.text
end

error_at(parser::Parser, message::AbstractString) =
    throw(TinySimSyntaxError("$(parser.filename):$(current(parser).line): $message"))

# ===========================================================================
# program = { definition }
# ===========================================================================

"""
    parse(source; filename) -> Program

Parse `.tiny` source text.
"""
function parse(source::AbstractString; filename::AbstractString = "<string>")
    parser = Parser(source; filename)
    program = Program()
    while current(parser).kind !== :eof
        if at(parser, "contract")
            contract = parse_contract!(parser)
            any(c -> c.name == contract.name, program.contracts) &&
                error_at(parser, "contract $(repr(contract.name)) is defined twice")
            push!(program.contracts, contract)
        elseif at(parser, "automaton")
            automaton = parse_automaton!(parser)
            find_automaton(program, automaton.name) !== nothing &&
                error_at(parser, "automaton $(repr(automaton.name)) is defined twice")
            push!(program.automata, automaton)
        else
            class = parse_class!(parser)
            find_class(program, class.name) !== nothing &&
                error_at(parser, "class $(repr(class.name)) is defined twice")
            push!(program.classes, class)
        end
    end
    return program
end

parse_file(path) = parse(read(path, String); filename = string(path))

# -- model, connector, record ------------------------------------------------

function parse_class!(parser::Parser)
    line = current(parser).line
    partial = accept!(parser, "partial")
    kind = at(parser, "model") ? :model :
           at(parser, "connector") ? :connector :
           at(parser, "record") ? :record :
           error_at(parser, "expected 'model', 'connector' or 'record' but found " *
                            "$(repr(current(parser).text))")
    parser.position += 1

    name = identifier!(parser)
    description = current(parser).kind === :string ? current(parser).text : ""
    isempty(description) || (parser.position += 1)

    extends = String[]
    declarations = Declaration[]
    equations = ModelEquation[]
    initial_equations = SimpleEquation[]

    while !at(parser, "end")
        current(parser).kind === :eof &&
            error_at(parser, "unterminated $kind $(repr(name)): missing 'end'")
        if accept!(parser, "initial")
            expect!(parser, "equation")
            for equation in parse_equations!(parser)
                equation isa SimpleEquation ||
                    error_at(parser, "only plain equations may be initial")
                push!(initial_equations, equation)
            end
        elseif accept!(parser, "equation")
            append!(equations, parse_equations!(parser))
        elseif accept!(parser, "extends")
            base = identifier!(parser)
            at(parser, "(") && error_at(parser,
                "TinySim does not support modifiers on 'extends'; declare the " *
                "parameter again in $(repr(name)), or set it where the component " *
                "is instantiated")
            push!(extends, base)
            expect!(parser, ";")
        else
            append!(declarations, parse_declaration!(parser))
        end
    end

    expect!(parser, "end")
    if current(parser).kind === :identifier
        closing = identifier!(parser)
        closing == name ||
            error_at(parser, "'end $closing' does not match '$kind $name'")
    end
    expect!(parser, ";")

    if kind === :record && !(isempty(equations) && isempty(initial_equations))
        throw(TinySimSyntaxError("$(parser.filename):$line: a record groups " *
                                 "variables and has no equations; $(repr(name)) has some"))
    end
    return ClassDefinition(kind, name, partial, extends, declarations, equations,
                           initial_equations, description, line)
end

# -- declarations -------------------------------------------------------------

function parse_declaration!(parser::Parser)
    line = current(parser).line
    prefixes = Symbol[]
    while current(parser).text in vcat(VARIABILITY_PREFIXES, CONNECTION_PREFIXES)
        push!(prefixes, Symbol(current(parser).text))
        parser.position += 1
    end

    current(parser).kind === :identifier ||
        error_at(parser, "expected a declaration but found $(repr(current(parser).text))")
    type_name = current(parser).text
    parser.position += 1
    type_name != "Real" && type_name in KEYWORDS &&
        error_at(parser, "$(repr(type_name)) cannot be used as a type")

    items = [parse_declaration_item!(parser)]
    while accept!(parser, ",")
        push!(items, parse_declaration_item!(parser))
    end

    description = ""
    if current(parser).kind === :string
        description = current(parser).text
        parser.position += 1
    end
    expect!(parser, ";")

    return [Declaration(name, type_name, prefixes, modifiers, value, description, line)
            for (name, modifiers, value) in items]
end

function parse_declaration_item!(parser::Parser)
    name = identifier!(parser)
    modifiers = at(parser, "(") ? parse_modification!(parser) : Dict{String, Any}()
    value = accept!(parser, "=") ? parse_expression!(parser) : nothing
    return (name, modifiers, value)
end

"""`(start = 0)`, `(C = 1e-3, v(start = 0))` -- values or nested modifications."""
function parse_modification!(parser::Parser)
    expect!(parser, "(")
    modifiers = Dict{String, Any}()
    while true
        key = identifier!(parser)
        if at(parser, "(")
            modifiers[key] = parse_modification!(parser)
        else
            expect!(parser, "=")
            modifiers[key] = parse_expression!(parser)
        end
        accept!(parser, ",") || break
    end
    expect!(parser, ")")
    return modifiers
end

# -- equations ----------------------------------------------------------------

function parse_equations!(parser::Parser)
    equations = ModelEquation[]
    while !(at(parser, "end") || at(parser, "equation") || at(parser, "initial") ||
            at(parser, "extends") || current(parser).kind === :eof)
        looks_like_declaration(parser) && break
        push!(equations, parse_equation!(parser))
    end
    return equations
end

function looks_like_declaration(parser::Parser)
    current(parser).text in vcat(VARIABILITY_PREFIXES, CONNECTION_PREFIXES) && return true
    return current(parser).kind === :identifier &&
           !(current(parser).text in KEYWORDS) &&
           peek(parser).kind === :identifier
end

function parse_equation!(parser::Parser)
    line = current(parser).line
    at(parser, "connect") && return parse_connect!(parser)
    at(parser, "when") && return parse_when!(parser)
    left = parse_expression!(parser)
    expect!(parser, "=")
    right = parse_expression!(parser)
    expect!(parser, ";")
    return SimpleEquation(left, right, line)
end

function parse_connect!(parser::Parser)
    line = current(parser).line
    expect!(parser, "connect")
    expect!(parser, "(")
    first = parse_component_reference!(parser)
    expect!(parser, ",")
    second = parse_component_reference!(parser)
    expect!(parser, ")")
    expect!(parser, ";")
    return ConnectEquation(first, second, line)
end

function parse_when!(parser::Parser)
    line = current(parser).line
    expect!(parser, "when")
    condition = parse_when_condition!(parser)
    expect!(parser, "then")
    body = parse_statements!(parser)
    expect!(parser, "end")
    current(parser).kind === :identifier &&
        error_at(parser, "a 'when' block is closed with 'end;', not " *
                         "'end $(current(parser).text);'")
    expect!(parser, ";")
    isempty(body) && throw(TinySimSyntaxError("$(parser.filename):$line: empty 'when' body"))
    return WhenEquation(condition, body, line)
end

"""The condition of a `when`: a comparison, or `sample(t0, Ts)`."""
function parse_when_condition!(parser::Parser)
    if at(parser, "sample")
        parser.position += 1
        expect!(parser, "(")
        start = parse_expression!(parser)
        expect!(parser, ",")
        interval = parse_expression!(parser)
        expect!(parser, ")")
        return SampleCondition(start, interval)
    end
    return ExpressionCondition(parse_expression!(parser))
end

# -- statements ---------------------------------------------------------------

"""The body of a `when`, or of one branch of an `if` inside it."""
function parse_statements!(parser::Parser)
    statements = Statement[]
    while !(at(parser, "end") || at(parser, "else") || at(parser, "elseif"))
        current(parser).kind === :eof &&
            error_at(parser, "unterminated 'when': missing 'end'")
        push!(statements, parse_statement!(parser))
    end
    return statements
end

function parse_statement!(parser::Parser)
    line = current(parser).line
    at(parser, "if") && return parse_if_statement!(parser)

    if at(parser, "reinit")
        expect!(parser, "reinit")
        expect!(parser, "(")
        name = parse_component_reference!(parser)
        expect!(parser, ",")
        value = parse_expression!(parser)
        expect!(parser, ")")
        expect!(parser, ";")
        return Reinit(name, value, line)
    end

    name = parse_component_reference!(parser)
    at(parser, "=") && error_at(parser,
        "a 'when' body assigns with ':=', not '='; its statements run in order, " *
        "and 'pre($name)' is the value before the event")
    expect!(parser, ":=")
    value = parse_expression!(parser)
    expect!(parser, ";")
    return Assignment(name, value, line)
end

function parse_if_statement!(parser::Parser)
    line = current(parser).line
    expect!(parser, "if")
    conditions = Expression[parse_expression!(parser)]
    expect!(parser, "then")
    branches = Vector{Statement}[parse_statements!(parser)]
    otherwise = Statement[]

    while accept!(parser, "elseif")
        push!(conditions, parse_expression!(parser))
        expect!(parser, "then")
        push!(branches, parse_statements!(parser))
    end
    if accept!(parser, "else")
        otherwise = parse_statements!(parser)
    end
    expect!(parser, "end")
    expect!(parser, "if")
    expect!(parser, ";")
    return IfStatement(conditions, branches, otherwise, line)
end

# -- automata -----------------------------------------------------------------

function parse_automaton!(parser::Parser)
    line = current(parser).line
    expect!(parser, "automaton")
    name = identifier!(parser)
    expect!(parser, "sampled")
    expect!(parser, "at")
    rate = parse_expression!(parser)
    description = ""
    if current(parser).kind === :string
        description = current(parser).text
        parser.position += 1
    end

    # An automaton declares what it watches and what it commands, so that its
    # guards and actions have something to read and write. The enclosing model
    # wires those to the plant, exactly as it would for any component.
    declarations = Declaration[]
    while !at(parser, "state")
        current(parser).kind === :eof &&
            error_at(parser, "unterminated automaton $(repr(name)): missing 'state'")
        append!(declarations, parse_declaration!(parser))
    end

    expect!(parser, "state")
    states = [identifier!(parser)]
    while accept!(parser, ",")
        push!(states, identifier!(parser))
    end
    expect!(parser, ";")

    expect!(parser, "initial")
    initial = identifier!(parser)
    initial in states ||
        error_at(parser, "$(repr(initial)) is not one of the states of $name")
    expect!(parser, ";")

    expect!(parser, "transition")
    transitions = Transition[]
    while !at(parser, "end")
        current(parser).kind === :eof &&
            error_at(parser, "unterminated automaton $(repr(name)): missing 'end'")
        push!(transitions, parse_transition!(parser, states, name))
    end

    expect!(parser, "end")
    if current(parser).kind === :identifier
        closing = identifier!(parser)
        closing == name ||
            error_at(parser, "'end $closing' does not match 'automaton $name'")
    end
    expect!(parser, ";")
    return AutomatonDefinition(name, rate, declarations, states, initial, transitions,
                               description, line)
end

function parse_transition!(parser::Parser, states::Vector{String}, automaton::String)
    line = current(parser).line
    from = identifier!(parser)
    from in states || error_at(parser, "$(repr(from)) is not a state of $automaton")
    expect!(parser, "->")
    to = identifier!(parser)
    to in states || error_at(parser, "$(repr(to)) is not a state of $automaton")
    expect!(parser, "when")
    guard = parse_expression!(parser)
    # A transition either ends at its semicolon, or carries actions and closes
    # like every other block in the language: `then ... end;`.
    actions = Statement[]
    if accept!(parser, "then")
        actions = parse_statements!(parser)
        expect!(parser, "end")
        isempty(actions) &&
            error_at(parser, "empty 'then' on a transition of $automaton")
    end
    expect!(parser, ";")
    return Transition(from, to, guard, actions, line)
end

# ===========================================================================
# Expressions, loosest operator first
# ===========================================================================

function parse_expression!(parser::Parser)
    if at(parser, "if")
        expect!(parser, "if")
        condition = parse_expression!(parser)
        expect!(parser, "then")
        then_value = parse_expression!(parser)
        expect!(parser, "else")
        else_value = parse_expression!(parser)
        return IfExpression(condition, then_value, else_value)
    end
    return parse_or!(parser)
end

function parse_or!(parser::Parser)
    expression = parse_and!(parser)
    while accept!(parser, "or")
        expression = BinaryOp("or", expression, parse_and!(parser))
    end
    return expression
end

function parse_and!(parser::Parser)
    expression = parse_not!(parser)
    while accept!(parser, "and")
        expression = BinaryOp("and", expression, parse_not!(parser))
    end
    return expression
end

parse_not!(parser::Parser) =
    accept!(parser, "not") ? UnaryOp("not", parse_not!(parser)) : parse_relation!(parser)

function parse_relation!(parser::Parser)
    expression = parse_sum!(parser)
    if current(parser).kind === :operator && current(parser).text in RELATIONAL_OPERATORS
        operator = current(parser).text
        parser.position += 1
        return BinaryOp(operator, expression, parse_sum!(parser))
    end
    return expression
end

function parse_sum!(parser::Parser)
    expression = accept!(parser, "-") ? UnaryOp("-", parse_product!(parser)) :
                 (accept!(parser, "+"); parse_product!(parser))
    while current(parser).kind === :operator && current(parser).text in ("+", "-")
        operator = current(parser).text
        parser.position += 1
        expression = BinaryOp(operator, expression, parse_product!(parser))
    end
    return expression
end

function parse_product!(parser::Parser)
    expression = parse_power!(parser)
    while current(parser).kind === :operator && current(parser).text in ("*", "/")
        operator = current(parser).text
        parser.position += 1
        expression = BinaryOp(operator, expression, parse_power!(parser))
    end
    return expression
end

function parse_power!(parser::Parser)
    base = parse_atom!(parser)
    if current(parser).kind === :operator && current(parser).text == "^"
        parser.position += 1
        # right-associative: a^b^c is a^(b^c)
        exponent = at(parser, "-") ? (expect!(parser, "-");
                                      UnaryOp("-", parse_power!(parser))) :
                   parse_power!(parser)
        return BinaryOp("^", base, exponent)
    end
    return base
end

function parse_atom!(parser::Parser)
    token = current(parser)
    if token.kind === :number
        parser.position += 1
        return NumberLiteral(Base.parse(Float64, token.text))
    end
    if accept!(parser, "(")
        expression = parse_expression!(parser)
        expect!(parser, ")")
        return expression
    end
    if accept!(parser, "-")
        return UnaryOp("-", parse_atom!(parser))
    end
    if token.kind === :identifier
        if peek(parser).text == "(" && peek(parser).kind === :operator
            name = token.text
            name in BUILTIN_FUNCTIONS || error_at(parser,
                "unknown function $(repr(name)); known functions are " *
                join(sort(collect(BUILTIN_FUNCTIONS)), ", "))
            parser.position += 2
            arguments = Expression[parse_expression!(parser)]
            while accept!(parser, ",")
                push!(arguments, parse_expression!(parser))
            end
            expect!(parser, ")")
            check_arity(parser, name, arguments, token.line)
            return FunctionCall(name, arguments)
        end
        if token.text == "time"
            parser.position += 1
            return VariableRef("time")
        end
        return VariableRef(parse_component_reference!(parser))
    end
    error_at(parser, "unexpected $(repr(token.text)) in an expression")
end

function check_arity(parser::Parser, name, arguments, line)
    expected = name in ("atan2", "min", "max") ? 2 : 1
    length(arguments) == expected || throw(TinySimSyntaxError(
        "$(parser.filename):$line: $name() takes $expected argument(s), " *
        "got $(length(arguments))"))
    name in ("der", "pre") && !(arguments[1] isa VariableRef) && throw(
        TinySimSyntaxError("$(parser.filename):$line: $name() may only be applied " *
                           "to a variable, not to an expression"))
end

"""A possibly dotted name: `v`, `c.v`, `emf.flange.tau`."""
function parse_component_reference!(parser::Parser)
    parts = [identifier!(parser)]
    while accept!(parser, ".")
        push!(parts, identifier!(parser))
    end
    return join(parts, ".")
end

# ===========================================================================
# Contracts
# ===========================================================================
#
# One extra ladder of precedence, for the temporal operators:
#
#     clause -> implies -> or -> and -> temporal -> until -> comparison
#
# The parser produces the *surface* tree -- `whenever`, `stays within`, `never`
# and the rest. Turning that into plain Signal Temporal Logic is phase 5's job,
# and keeping both is what lets a report show a requirement in the words it was
# written in and in the logic it means.

function parse_contract!(parser::Parser)
    line = current(parser).line
    expect!(parser, "contract")
    name = identifier!(parser)
    expect!(parser, "for")
    model_name = identifier!(parser)
    description = ""
    if current(parser).kind === :string
        description = current(parser).text
        parser.position += 1
    end

    assumptions = Clause[]
    guarantees = Clause[]
    while !at(parser, "end")
        current(parser).kind === :eof &&
            error_at(parser, "unterminated contract $(repr(name)): missing 'end'")
        if accept!(parser, "assume")
            append!(assumptions, parse_clauses!(parser))
        elseif accept!(parser, "guarantee")
            append!(guarantees, parse_clauses!(parser))
        else
            error_at(parser, "expected 'assume' or 'guarantee' in contract " *
                             "$(repr(name)), found $(repr(current(parser).text))")
        end
    end
    expect!(parser, "end")
    if current(parser).kind === :identifier
        closing = identifier!(parser)
        closing == name ||
            error_at(parser, "'end $closing' does not match 'contract $name'")
    end
    expect!(parser, ";")
    isempty(assumptions) && isempty(guarantees) &&
        throw(TinySimSyntaxError("$(parser.filename):$line: contract $(repr(name)) " *
                                 "says nothing"))
    return Contract(name, model_name, description, assumptions, guarantees, line)
end

function parse_clauses!(parser::Parser)
    clauses = Clause[]
    while !(at(parser, "end") || at(parser, "assume") || at(parser, "guarantee") ||
            current(parser).kind === :eof)
        line = current(parser).line
        formula = parse_formula!(parser)
        expect!(parser, ";")
        push!(clauses, Clause(formula, line))
    end
    return clauses
end

parse_formula!(parser::Parser) = parse_formula_implies!(parser)

function parse_formula_implies!(parser::Parser)
    left = parse_formula_or!(parser)
    accept!(parser, "implies") || return left
    return ImpliesFormula(left, parse_formula_implies!(parser))
end

function parse_formula_or!(parser::Parser)
    parts = Formula[parse_formula_and!(parser)]
    while accept!(parser, "or")
        push!(parts, parse_formula_and!(parser))
    end
    return length(parts) == 1 ? parts[1] : OrFormula(parts)
end

function parse_formula_and!(parser::Parser)
    parts = Formula[parse_temporal!(parser)]
    while accept!(parser, "and")
        push!(parts, parse_temporal!(parser))
    end
    return length(parts) == 1 ? parts[1] : AndFormula(parts)
end

"""
The temporal operators, and the patterns built on them.

**Scope:** a temporal operator applies to everything that follows it, `and` and
`or` included, so `always a > 0 and b > 0` means `always (a > 0 and b > 0)` --
the way it reads aloud. Parentheses stop it. `not` is the exception and binds
tightly, as everywhere else.
"""
function parse_temporal!(parser::Parser)
    if accept!(parser, "always")
        window = at(parser, "within") ? parse_window!(parser) : UNBOUNDED
        return Always(parse_formula!(parser), window)
    elseif accept!(parser, "eventually")
        window = at(parser, "within") ? parse_window!(parser) : UNBOUNDED
        return Eventually(parse_formula!(parser), window)
    elseif accept!(parser, "never")
        return Never(parse_formula!(parser))
    elseif accept!(parser, "after")
        moment = parse_sum!(parser)
        accept!(parser, "always")            # `after 60 always ...` reads better
        return After(moment, parse_formula!(parser))
    elseif accept!(parser, "during")
        return During(parse_bounds!(parser), parse_formula!(parser))
    elseif accept!(parser, "whenever")
        return parse_whenever!(parser)
    elseif accept!(parser, "at")
        accept!(parser, "start") && return AtStart(parse_formula!(parser))
        expect!(parser, "end")
        return AtEnd(parse_formula!(parser))
    elseif accept!(parser, "not")
        return NotFormula(parse_temporal!(parser))
    end
    return parse_formula_until!(parser)
end

function parse_whenever!(parser::Parser)
    trigger = parse_formula_or!(parser)
    expect!(parser, "then")
    response = parse_formula_or!(parser)
    if accept!(parser, "holds")
        expect!(parser, "for")
        duration = parse_sum!(parser)
        return Whenever(trigger, response, (NumberLiteral(0.0), duration), true)
    end
    return Whenever(trigger, response, parse_window!(parser), false)
end

function parse_formula_until!(parser::Parser)
    left = parse_atom_formula!(parser)
    accept!(parser, "until") || return left
    window = at(parser, "within") ? parse_window!(parser) : UNBOUNDED
    return Until(left, parse_formula!(parser), window)
end

parse_window!(parser::Parser) = (expect!(parser, "within"); parse_bounds!(parser))

function parse_bounds!(parser::Parser)
    expect!(parser, "[")
    low = parse_sum!(parser)
    expect!(parser, ",")
    high = parse_sum!(parser)
    expect!(parser, "]")
    return (low, high)
end

function parse_atom_formula!(parser::Parser)
    if at(parser, "(")
        # `(` starts either a grouped formula, `(a > 1 and b > 2)`, or a grouped
        # arithmetic expression, `(a + b) > 2`. Try the first and fall back to
        # the second: one token of lookahead cannot tell them apart, and
        # backtracking here is easier to read than a scan for the bracket.
        checkpoint = parser.position
        try
            expect!(parser, "(")
            formula = parse_formula!(parser)
            expect!(parser, ")")
            if !(current(parser).kind === :operator &&
                 current(parser).text in RELATIONAL_OPERATORS)
                return formula
            end
        catch error
            error isa TinySimSyntaxError || rethrow()
        end
        parser.position = checkpoint
    end

    if at(parser, "rise") || at(parser, "fall")
        kind = current(parser).text
        parser.position += 1
        expect!(parser, "(")
        inner = parse_formula!(parser)
        expect!(parser, ")")
        return kind == "rise" ? Rise(inner) : Rise(NotFormula(inner))
    end

    left = parse_sum!(parser)

    if accept!(parser, "stays")
        expect!(parser, "within")
        low, high = parse_bounds!(parser)
        return StaysWithin(left, low, high)
    end
    if accept!(parser, "settles")
        expect!(parser, "to")
        value = parse_sum!(parser)
        expect!(parser, "within")
        tolerance = parse_sum!(parser)
        after = accept!(parser, "after") ? parse_sum!(parser) : nothing
        return SettlesTo(left, value, tolerance, after)
    end

    (current(parser).kind === :operator &&
     current(parser).text in RELATIONAL_OPERATORS) ||
        error_at(parser, "a contract clause must compare something, for example " *
                         "'c.v >= 9.5'; write 'x == 1' rather than just 'x'")
    operator = current(parser).text
    parser.position += 1
    return Predicate(operator, left, parse_sum!(parser))
end
