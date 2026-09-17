"""Arithmetic expression evaluator.

Supports +, -, *, /, parentheses, unary +/-, and whitespace-insensitive
integer/decimal literals. See specification.md for full behavior.
"""

from __future__ import annotations

import re

_TOKEN_PATTERN = re.compile(r"\d+\.\d+|\d+|[()+\-*/]")


def _tokenize(expression: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    length = len(expression)
    while pos < length:
        char = expression[pos]
        if char.isspace():
            pos += 1
            continue
        match = _TOKEN_PATTERN.match(expression, pos)
        if not match:
            raise ValueError(f"Unexpected character at position {pos}: {char!r}")
        tokens.append(match.group(0))
        pos = match.end()
    if not tokens:
        raise ValueError("Expression is empty")
    return tokens


class _Parser:
    """Recursive-descent parser for +, -, *, /, and parentheses."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> float:
        value = self._expr()
        if self._pos != len(self._tokens):
            raise ValueError(f"Unexpected token: {self._tokens[self._pos]!r}")
        return value

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> str:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _expr(self) -> float:
        value = self._term()
        op = self._peek()
        if op in ("+", "-"):
            self._advance()
            rhs = self._expr()
            return value + rhs if op == "+" else value - rhs
        return value

    def _term(self) -> float:
        value = self._factor()
        op = self._peek()
        if op in ("*", "/"):
            self._advance()
            rhs = self._term()
            if op == "*":
                return value * rhs
            if rhs == 0:
                raise ZeroDivisionError("division by zero")
            return value / rhs
        return value

    def _factor(self) -> float:
        token = self._peek()
        if token == "-":
            self._advance()
            return -self._factor()
        if token == "+":
            self._advance()
            return self._factor()
        if token == "(":
            self._advance()
            value = self._expr()
            if self._peek() != ")":
                raise ValueError("Missing closing parenthesis")
            self._advance()
            return value
        if token is None:
            raise ValueError("Unexpected end of expression")
        self._advance()
        try:
            return float(token)
        except ValueError as exc:
            raise ValueError(f"Invalid number: {token!r}") from exc


def evaluate(expression: str) -> float:
    """Evaluate a basic arithmetic expression and return its float result."""
    tokens = _tokenize(expression)
    return _Parser(tokens).parse()
