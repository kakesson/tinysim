"""
Stage 1 of the pipeline: turning model text into a stream of tokens.

The lexer is the least interesting part of a compiler, so TinySim keeps it as
short as possible.  It recognises four kinds of token:

    NUMBER   3   3.14   1e-3
    IDENT    R   der    phi   Resistor
    STRING   "resistance [Ohm]"
    OP       + - * / ^ ( ) , ; . = < <= > >= == <>

Comments (`// ...` and `/* ... */`) and all whitespace are thrown away.
Every token remembers its line number, which is what makes readable error
messages possible further down the pipeline.
"""

from dataclasses import dataclass
import re


class TinySimSyntaxError(Exception):
    """Raised for anything the lexer or the parser cannot make sense of."""


@dataclass(frozen=True)
class Token:
    kind: str    # 'NUMBER' | 'IDENT' | 'STRING' | 'OP' | 'EOF'
    text: str    # the token as written in the file
    line: int    # 1-based line number, used for error messages

    def __repr__(self) -> str:                      # pragma: no cover - debug aid
        return f"{self.kind}({self.text!r})@{self.line}"


# The order matters: longer operators must be tried before shorter ones, so
# that "<=" is not read as "<" followed by "=".
_TOKEN_SPEC = [
    ("SPACE",   r"[ \t\r\n]+"),
    ("COMMENT", r"//[^\n]*"),
    ("BLOCK",   r"/\*.*?\*/"),
    ("NUMBER",  r"\d+\.\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?|\d+([eE][+-]?\d+)?"),
    ("IDENT",   r"[A-Za-z_][A-Za-z_0-9]*"),
    ("STRING",  r'"[^"]*"'),
    ("OP",      r"<=|>=|==|<>|:=|->|[-+*/^(),;.=<>\[\]]"),
]
_MASTER_RE = re.compile(
    "|".join(f"(?P<{name}>{pattern})" for name, pattern in _TOKEN_SPEC),
    re.DOTALL,
)


def tokenize(source: str, filename: str = "<string>") -> list:
    """Convert `source` into a list of tokens, ending with an EOF token."""
    tokens = []
    position = 0
    line = 1
    while position < len(source):
        match = _MASTER_RE.match(source, position)
        if match is None:
            raise TinySimSyntaxError(
                f"{filename}:{line}: unexpected character {source[position]!r}"
            )
        kind = match.lastgroup
        text = match.group()
        # Keep the line counter up to date, including for multi-line comments.
        if kind in ("SPACE", "COMMENT", "BLOCK"):
            line += text.count("\n")
        else:
            if kind == "STRING":
                text = text[1:-1]           # strip the surrounding quotes
            tokens.append(Token(kind, text, line))
        position = match.end()
    tokens.append(Token("EOF", "", line))
    return tokens
