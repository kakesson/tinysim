# Stage 1 of the pipeline: turning model text into a stream of tokens.
#
# The lexer is the least interesting part of a compiler, so this one is written
# to be read once and then ignored. It recognises five kinds of token, throws
# away comments and whitespace, and remembers the line of each -- which is what
# makes every later error message able to point somewhere.

"""
    TinySimSyntaxError

Raised for anything the lexer or the parser cannot make sense of. The message
always begins `file:line:`.
"""
struct TinySimSyntaxError <: Exception
    message::String
end

Base.showerror(io::IO, error::TinySimSyntaxError) = print(io, error.message)

"""
    Token

One token: its kind (`:number`, `:identifier`, `:string`, `:operator`, `:eof`),
its text as written, and the line it came from.
"""
struct Token
    kind::Symbol
    text::String
    line::Int
end

Base.show(io::IO, token::Token) = print(io, token.kind, "(", repr(token.text), ")@", token.line)

#: Operators of two characters, which must be tried before the one-character
#: ones so that `<=` is not read as `<` followed by `=`.
const TWO_CHARACTER_OPERATORS = ["<=", ">=", "==", "<>", ":=", "->"]
const ONE_CHARACTER_OPERATORS = "+-*/^(),;.=<>[]"

is_identifier_start(character::Char) = isletter(character) || character == '_'
is_identifier_part(character::Char) = is_identifier_start(character) || isdigit(character)

"""
    tokenize(source; filename) -> Vector{Token}

Convert source text into tokens, ending with an `:eof` token.
"""
function tokenize(source::AbstractString; filename::AbstractString = "<string>")
    tokens = Token[]
    characters = collect(source)
    position = 1
    line = 1
    total = length(characters)

    while position <= total
        character = characters[position]

        if character == '\n'
            line += 1
            position += 1
            continue
        elseif isspace(character)
            position += 1
            continue
        end

        # comments: `// to the end of the line` and `/* ... */`
        if character == '/' && position < total && characters[position + 1] == '/'
            while position <= total && characters[position] != '\n'
                position += 1
            end
            continue
        end
        if character == '/' && position < total && characters[position + 1] == '*'
            closing = position + 2
            while closing < total &&
                  !(characters[closing] == '*' && characters[closing + 1] == '/')
                characters[closing] == '\n' && (line += 1)
                closing += 1
            end
            closing >= total && throw(TinySimSyntaxError(
                "$filename:$line: unterminated block comment"))
            position = closing + 2
            continue
        end

        if isdigit(character) || (character == '.' && position < total &&
                                  isdigit(characters[position + 1]))
            start = position
            while position <= total && isdigit(characters[position])
                position += 1
            end
            if position <= total && characters[position] == '.'
                position += 1
                while position <= total && isdigit(characters[position])
                    position += 1
                end
            end
            if position <= total && (characters[position] in ('e', 'E'))
                lookahead = position + 1
                if lookahead <= total && characters[lookahead] in ('+', '-')
                    lookahead += 1
                end
                if lookahead <= total && isdigit(characters[lookahead])
                    position = lookahead
                    while position <= total && isdigit(characters[position])
                        position += 1
                    end
                end
            end
            push!(tokens, Token(:number, String(characters[start:position - 1]), line))
            continue
        end

        if is_identifier_start(character)
            start = position
            while position <= total && is_identifier_part(characters[position])
                position += 1
            end
            push!(tokens, Token(:identifier, String(characters[start:position - 1]), line))
            continue
        end

        if character == '"'
            closing = position + 1
            while closing <= total && characters[closing] != '"'
                characters[closing] == '\n' && (line += 1)
                closing += 1
            end
            closing > total && throw(TinySimSyntaxError(
                "$filename:$line: unterminated string"))
            push!(tokens, Token(:string, String(characters[position + 1:closing - 1]), line))
            position = closing + 1
            continue
        end

        if position < total
            pair = String(characters[position:position + 1])
            if pair in TWO_CHARACTER_OPERATORS
                push!(tokens, Token(:operator, pair, line))
                position += 2
                continue
            end
        end
        if character in ONE_CHARACTER_OPERATORS
            push!(tokens, Token(:operator, string(character), line))
            position += 1
            continue
        end

        throw(TinySimSyntaxError(
            "$filename:$line: unexpected character $(repr(character))"))
    end

    push!(tokens, Token(:eof, "", line))
    return tokens
end
