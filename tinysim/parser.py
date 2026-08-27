"""
Stage 2 of the pipeline: turning tokens into an abstract syntax tree.

This is a hand-written *recursive descent* parser: one small method per rule of
the grammar in `docs/language.md`.  Reading the methods top to bottom is
reading the grammar.  There is no parser generator involved, so students can
follow exactly how `C * der(v) = i;` becomes an `Equation` node.

Expression parsing uses the usual precedence ladder, one method per level:

    expression -> or -> and -> not -> relation -> sum -> product -> power -> atom

with the loosest operator at the top.  That is why `a + b * c` groups as
`a + (b * c)` without any special handling.
"""

from typing import List, Optional

from .ast_nodes import (
    Assign, BinOp, Call, ClassDef, ConnectEquation, Decl, Equation, Expr,
    IfExpr, Num, Program, Ref, Reinit, UnOp, WhenEquation, to_string,
)
from .lexer import Token, TinySimSyntaxError, tokenize

# Words that may not be used as identifiers, and prefixes on declarations.
KEYWORDS = {
    "model", "connector", "partial", "extends", "end", "equation", "initial",
    "parameter", "constant", "discrete", "flow", "potential",
    "connect", "when", "then", "if", "else", "and", "or", "not", "der", "pre",
    "reinit", "Real", "time",
}
VARIABILITY_PREFIXES = ("parameter", "constant", "discrete")
CONNECTION_PREFIXES = ("flow", "potential")
RELATIONAL_OPERATORS = ("<", "<=", ">", ">=", "==", "<>")

# Functions a model may call.  Keeping this list closed means a typo such as
# `sinn(x)` is caught while parsing rather than at simulation time.
BUILTIN_FUNCTIONS = {
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "exp", "log",
    "log10", "sqrt", "abs", "sign", "tanh", "min", "max", "der", "pre",
}


class Parser:
    """A recursive-descent parser for one TinySim source file."""

    def __init__(self, source: str, filename: str = "<string>"):
        self.tokens: List[Token] = tokenize(source, filename)
        self.filename = filename
        self.pos = 0

    # -- token handling ------------------------------------------------------

    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, offset: int = 1) -> Token:
        index = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def at(self, text: str) -> bool:
        """True if the current token is exactly `text` (an operator or word)."""
        return self.current.text == text and self.current.kind in ("OP", "IDENT")

    def accept(self, text: str) -> bool:
        """Consume the current token if it matches; report whether it did."""
        if self.at(text):
            self.pos += 1
            return True
        return False

    def expect(self, text: str) -> Token:
        """Consume the current token, or fail with a helpful message."""
        if not self.at(text):
            self.error(f"expected {text!r} but found {self.current.text!r}")
        token = self.current
        self.pos += 1
        return token

    def expect_kind(self, kind: str) -> Token:
        if self.current.kind != kind:
            self.error(f"expected {kind.lower()} but found {self.current.text!r}")
        token = self.current
        self.pos += 1
        return token

    def identifier(self) -> str:
        """Consume a plain identifier (not a keyword)."""
        token = self.expect_kind("IDENT")
        if token.text in KEYWORDS:
            self.error(f"{token.text!r} is a keyword and cannot be used as a name")
        return token.text

    def error(self, message: str):
        raise TinySimSyntaxError(f"{self.filename}:{self.current.line}: {message}")

    # =========================================================================
    # program  =  { class definition }
    # =========================================================================

    def parse_program(self) -> Program:
        program = Program()
        while self.current.kind != "EOF":
            definition = self.parse_class()
            if definition.name in program.classes:
                self.error(f"class {definition.name!r} is defined twice")
            program.classes[definition.name] = definition
        return program

    # -- class definition ----------------------------------------------------

    def parse_class(self) -> ClassDef:
        line = self.current.line
        partial = self.accept("partial")
        if self.at("model"):
            kind = "model"
        elif self.at("connector"):
            kind = "connector"
        else:
            self.error(f"expected 'model' or 'connector' but found {self.current.text!r}")
        self.pos += 1

        name = self.identifier()
        description = self.current.text if self.current.kind == "STRING" else ""
        if description:
            self.pos += 1

        definition = ClassDef(kind=kind, name=name, partial=partial,
                              description=description, line=line)

        # The body is a sequence of sections in any order, closed by `end`.
        while not self.at("end"):
            if self.current.kind == "EOF":
                self.error(f"unterminated {kind} {name!r}: missing 'end'")
            if self.accept("initial"):
                self.expect("equation")
                definition.initial_equations.extend(self.parse_equation_section())
            elif self.accept("equation"):
                definition.equations.extend(self.parse_equation_section())
            elif self.accept("extends"):
                definition.extends.append(self.identifier())
                if self.at("("):            # modifiers on `extends` are parsed
                    self.parse_modification()   # but not yet applied (see flatten.py)
                self.expect(";")
            else:
                definition.decls.extend(self.parse_declaration())

        self.expect("end")
        # `end;` and `end Resistor;` are both accepted; a mismatched name is an error.
        if self.current.kind == "IDENT":
            closing = self.identifier()
            if closing != name:
                self.error(f"'end {closing}' does not match '{kind} {name}'")
        self.expect(";")
        return definition

    # -- declarations --------------------------------------------------------

    def parse_declaration(self) -> List[Decl]:
        """
        One declaration line, which may declare several names:

            parameter Real R = 100 "resistance";
            Real v(start = 0), i;
            Capacitor c(C = 1e-3, v(start = 0));
        """
        line = self.current.line
        prefixes = []
        while self.current.text in VARIABILITY_PREFIXES + CONNECTION_PREFIXES:
            prefixes.append(self.current.text)
            self.pos += 1

        if self.current.kind != "IDENT":
            self.error(f"expected a declaration but found {self.current.text!r}")
        type_name = self.current.text        # 'Real', or a connector/model name
        self.pos += 1
        if type_name != "Real" and type_name in KEYWORDS:
            self.error(f"{type_name!r} cannot be used as a type")

        items = [self.parse_declaration_item()]
        while self.accept(","):
            items.append(self.parse_declaration_item())

        description = ""
        if self.current.kind == "STRING":
            description = self.current.text
            self.pos += 1
        self.expect(";")

        return [Decl(name=name, type_name=type_name, prefixes=tuple(prefixes),
                     modifiers=modifiers, value=value, description=description,
                     line=line)
                for name, modifiers, value in items]

    def parse_declaration_item(self):
        """`name`, `name(start = 0)`, `name = 100`, or a combination."""
        name = self.identifier()
        modifiers = self.parse_modification() if self.at("(") else {}
        value = self.parse_expression() if self.accept("=") else None
        return name, modifiers, value

    def parse_modification(self) -> dict:
        """
        `(start = 0)`, `(C = 1e-3, v(start = 0))`.

        Returns a dict whose values are either expressions (`start = 0`) or
        nested dicts (`v(start = 0)`), mirroring the nesting in the source.
        """
        self.expect("(")
        modifiers = {}
        while True:
            key = self.identifier()
            if self.at("("):
                modifiers[key] = self.parse_modification()
            else:
                self.expect("=")
                modifiers[key] = self.parse_expression()
            if not self.accept(","):
                break
        self.expect(")")
        return modifiers

    # -- equations -----------------------------------------------------------

    def parse_equation_section(self) -> list:
        """Equations up to the next section keyword or `end`."""
        equations = []
        while not (self.at("end") or self.at("equation") or self.at("initial")
                   or self.at("extends") or self.current.kind == "EOF"):
            # A declaration would have to start with a prefix or a type name
            # followed by an identifier; equations never look like that.
            if self._looks_like_declaration():
                break
            equations.append(self.parse_equation())
        return equations

    def _looks_like_declaration(self) -> bool:
        if self.current.text in VARIABILITY_PREFIXES + CONNECTION_PREFIXES:
            return True
        return (self.current.kind == "IDENT" and self.current.text not in KEYWORDS
                and self.peek().kind == "IDENT")

    def parse_equation(self):
        line = self.current.line
        if self.at("connect"):
            return self.parse_connect()
        if self.at("when"):
            return self.parse_when()
        lhs = self.parse_expression()
        self.expect("=")
        rhs = self.parse_expression()
        self.expect(";")
        equation = Equation(lhs=lhs, rhs=rhs, line=line)
        equation.source = f"{to_string(lhs)} = {to_string(rhs)}"
        return equation

    def parse_connect(self) -> ConnectEquation:
        line = self.current.line
        self.expect("connect")
        self.expect("(")
        first = self.parse_component_reference()
        self.expect(",")
        second = self.parse_component_reference()
        self.expect(")")
        self.expect(";")
        return ConnectEquation(a=first, b=second, line=line)

    def parse_when(self) -> WhenEquation:
        line = self.current.line
        self.expect("when")
        condition = self.parse_expression()
        self.expect("then")
        body = []
        while not self.at("end"):
            if self.current.kind == "EOF":
                self.error("unterminated 'when': missing 'end'")
            body.append(self.parse_when_statement())
        self.expect("end")
        self.expect(";")
        if not body:
            raise TinySimSyntaxError(
                f"{self.filename}:{line}: empty 'when' body")
        return WhenEquation(condition=condition, body=body, line=line)

    def parse_when_statement(self):
        """Inside a `when`: either `reinit(x, expr);` or `x = expr;`."""
        line = self.current.line
        if self.at("reinit"):
            self.expect("reinit")
            self.expect("(")
            name = self.parse_component_reference()
            self.expect(",")
            value = self.parse_expression()
            self.expect(")")
            self.expect(";")
            return Reinit(name=name, value=value, line=line)
        name = self.parse_component_reference()
        self.expect("=")
        value = self.parse_expression()
        self.expect(";")
        return Assign(name=name, value=value, line=line)

    def parse_component_reference(self) -> str:
        """A possibly dotted name: `v`, `c.v`, `emf.flange.tau`."""
        parts = [self.identifier()]
        while self.accept("."):
            parts.append(self.identifier())
        return ".".join(parts)

    # =========================================================================
    # Expressions, loosest operator first
    # =========================================================================

    def parse_expression(self) -> Expr:
        if self.at("if"):
            self.expect("if")
            condition = self.parse_expression()
            self.expect("then")
            then_expr = self.parse_expression()
            self.expect("else")
            else_expr = self.parse_expression()
            return IfExpr(condition, then_expr, else_expr)
        return self.parse_or()

    def parse_or(self) -> Expr:
        expr = self.parse_and()
        while self.accept("or"):
            expr = BinOp("or", expr, self.parse_and())
        return expr

    def parse_and(self) -> Expr:
        expr = self.parse_not()
        while self.accept("and"):
            expr = BinOp("and", expr, self.parse_not())
        return expr

    def parse_not(self) -> Expr:
        if self.accept("not"):
            return UnOp("not", self.parse_not())
        return self.parse_relation()

    def parse_relation(self) -> Expr:
        expr = self.parse_sum()
        if self.current.text in RELATIONAL_OPERATORS and self.current.kind == "OP":
            operator = self.current.text
            self.pos += 1
            return BinOp(operator, expr, self.parse_sum())
        return expr

    def parse_sum(self) -> Expr:
        # A leading sign, as in `-m * g * sin(phi)`.
        if self.accept("-"):
            expr = UnOp("-", self.parse_product())
        else:
            self.accept("+")
            expr = self.parse_product()
        while self.current.kind == "OP" and self.current.text in ("+", "-"):
            operator = self.current.text
            self.pos += 1
            expr = BinOp(operator, expr, self.parse_product())
        return expr

    def parse_product(self) -> Expr:
        expr = self.parse_power()
        while self.current.kind == "OP" and self.current.text in ("*", "/"):
            operator = self.current.text
            self.pos += 1
            expr = BinOp(operator, expr, self.parse_power())
        return expr

    def parse_power(self) -> Expr:
        base = self.parse_atom()
        if self.current.kind == "OP" and self.current.text == "^":
            self.pos += 1
            # Right-associative: a^b^c means a^(b^c).
            exponent = self.parse_power() if not self.at("-") else self.parse_unary_power()
            return BinOp("^", base, exponent)
        return base

    def parse_unary_power(self) -> Expr:
        self.expect("-")
        return UnOp("-", self.parse_power())

    def parse_atom(self) -> Expr:
        token = self.current
        if token.kind == "NUMBER":
            self.pos += 1
            return Num(float(token.text))
        if self.accept("("):
            expr = self.parse_expression()
            self.expect(")")
            return expr
        if self.accept("-"):
            return UnOp("-", self.parse_atom())
        if token.kind == "IDENT":
            # A function call, or a (dotted) variable reference.
            if self.peek().text == "(" and self.peek().kind == "OP":
                name = token.text
                if name not in BUILTIN_FUNCTIONS:
                    self.error(f"unknown function {name!r}; known functions are "
                               f"{', '.join(sorted(BUILTIN_FUNCTIONS))}")
                self.pos += 2                       # consume name and '('
                args = [self.parse_expression()]
                while self.accept(","):
                    args.append(self.parse_expression())
                self.expect(")")
                self._check_call_arity(name, args, token.line)
                return Call(name, tuple(args))
            if token.text == "time":
                self.pos += 1
                return Ref("time")
            return Ref(self.parse_component_reference())
        self.error(f"unexpected {token.text!r} in an expression")

    def _check_call_arity(self, name: str, args: list, line: int):
        expected = 2 if name in ("atan2", "min", "max") else 1
        if len(args) != expected:
            raise TinySimSyntaxError(
                f"{self.filename}:{line}: {name}() takes {expected} argument(s), "
                f"got {len(args)}")
        if name in ("der", "pre") and not isinstance(args[0], Ref):
            raise TinySimSyntaxError(
                f"{self.filename}:{line}: {name}() may only be applied to a "
                f"variable, not to an expression")


def parse(source: str, filename: str = "<string>") -> Program:
    """Parse TinySim source text into a `Program`."""
    return Parser(source, filename).parse_program()


def parse_file(path) -> Program:
    """Parse a `.tiny` file from disk."""
    with open(path, "r") as handle:
        return parse(handle.read(), str(path))
