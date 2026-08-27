"""Lexer and parser: does the text become the tree we expect?"""

import pytest

from tinysim.ast_nodes import BinOp, Call, Equation, Num, Ref, UnOp, to_string
from tinysim.lexer import TinySimSyntaxError, tokenize
from tinysim.parser import parse


def test_comments_and_strings_are_skipped():
    tokens = tokenize('// hello\n/* and\n   more */ model A "text";')
    kinds = [token.kind for token in tokens]
    assert kinds == ["IDENT", "IDENT", "STRING", "OP", "EOF"]
    assert tokens[2].text == "text"          # quotes stripped
    assert tokens[0].line == 3               # line numbers survive block comments


def test_declarations_carry_prefixes_modifiers_and_descriptions():
    program = parse('model M parameter Real R = 100 "ohm"; Real v(start = 2), i; end M;')
    declarations = program["M"].decls
    assert [d.name for d in declarations] == ["R", "v", "i"]
    assert declarations[0].prefixes == ("parameter",)
    assert declarations[0].description == "ohm"
    assert to_string(declarations[0].value) == "100"
    assert to_string(declarations[1].modifiers["start"]) == "2"


def test_nested_modifiers_on_components():
    program = parse("connector P Real v; end P;\n"
                    "model C P p; Real v; end C;\n"
                    "model M C c(v(start = 3), p(v(start = 1))); end M;")
    modifiers = program["M"].decls[0].modifiers
    assert to_string(modifiers["v"]["start"]) == "3"
    assert to_string(modifiers["p"]["v"]["start"]) == "1"


def test_operator_precedence_and_associativity():
    program = parse("model M Real y, x; equation y = 1 + 2 * x ^ 2 - -x; end M;")
    equation = program["M"].equations[0]
    assert to_string(equation.rhs) == "1 + 2 * x^2 - -x"


def test_if_expression_and_relations():
    program = parse("model M Real y, x; equation y = if x > 0 then x else -x; end M;")
    assert to_string(program["M"].equations[0].rhs) == "if x > 0 then x else -x"


def test_when_and_reinit_parse():
    program = parse("model M Real h, v; equation der(h) = v; "
                    "when h < 0 then reinit(v, -v); end; end M;")
    when_equation = program["M"].equations[1]
    assert to_string(when_equation.condition) == "h < 0"
    assert when_equation.body[0].name == "v"


def test_end_may_repeat_the_name_or_not():
    assert parse("model A Real x; end;")["A"].name == "A"
    assert parse("model A Real x; end A;")["A"].name == "A"


def test_mismatched_end_name_is_an_error():
    with pytest.raises(TinySimSyntaxError, match="does not match"):
        parse("model A Real x; end B;")


def test_missing_semicolon_is_reported_with_a_line_number():
    # The parser notices at the token that cannot follow: `end`, on line 4.
    with pytest.raises(TinySimSyntaxError, match=":4: expected ';'"):
        parse("model A\n  Real x;\n  Real y\nend A;")


def test_unknown_function_is_rejected():
    with pytest.raises(TinySimSyntaxError, match="unknown function"):
        parse("model A Real x; equation x = sinn(1); end A;")


def test_der_of_an_expression_is_rejected():
    with pytest.raises(TinySimSyntaxError, match="only be applied to a variable"):
        parse("model A Real x, y; equation der(x + y) = 1; x = 0; end A;")


def test_duplicate_class_is_rejected():
    with pytest.raises(TinySimSyntaxError, match="defined twice"):
        parse("model A Real x; end A; model A Real y; end A;")


def test_when_must_be_closed():
    with pytest.raises(TinySimSyntaxError, match="unterminated 'when'"):
        parse("model A discrete Real x; equation when time > 1 then x = 0;")


def test_when_closed_with_a_name_is_explained():
    with pytest.raises(TinySimSyntaxError, match=r"closed with 'end;'"):
        parse("model A discrete Real x; equation when time > 1 then x = 0; "
              "end when; end A;")


def test_modifiers_on_extends_are_rejected_rather_than_ignored():
    with pytest.raises(TinySimSyntaxError, match="modifiers on 'extends'"):
        parse("partial model B parameter Real R = 1; Real x; equation x = R; end B;"
              "model D extends B(R = 5); end D;")
