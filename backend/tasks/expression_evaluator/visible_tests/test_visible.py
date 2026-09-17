"""Visible tests for the expression_evaluator task.

These check ordinary, everyday usage of `evaluate` and are safe to
include in a future reviewer prompt. They deliberately do not exercise
chains of three or more same-precedence, non-associative operators (e.g.
`a - b - c` or `a / b / c`); that coverage lives in this task's separate
ground-truth suite, which is not part of the reviewer-visible context.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from candidate import evaluate


def test_addition():
    assert evaluate("2 + 3") == 5


def test_single_subtraction():
    assert evaluate("5 - 3") == 2


def test_multiplication():
    assert evaluate("4 * 3") == 12


def test_single_division():
    assert evaluate("7 / 2") == 3.5


def test_division_returns_float():
    result = evaluate("8 / 2")
    assert result == 4.0
    assert isinstance(result, float)


def test_parentheses_override_precedence():
    assert evaluate("(2 + 3) * 4") == 20


def test_leading_unary_minus():
    assert evaluate("-5 + 10") == 5


def test_decimal_literals():
    assert evaluate("2.5 + 3.25") == 5.75


def test_mixed_precedence_without_chaining():
    assert evaluate("2 + 3 * 4") == 14


def test_associative_addition_chain():
    assert evaluate("1 + 2 + 3 + 4") == 10


def test_associative_multiplication_chain():
    assert evaluate("2 * 3 * 4") == 24


def test_whitespace_is_ignored():
    assert evaluate("  2   +   3  ") == 5


def test_division_by_zero_raises():
    with pytest.raises(ZeroDivisionError):
        evaluate("5 / 0")


def test_trailing_operator_raises_value_error():
    with pytest.raises(ValueError):
        evaluate("2 + ")


def test_unknown_characters_raise_value_error():
    with pytest.raises(ValueError):
        evaluate("abc")


def test_empty_expression_raises_value_error():
    with pytest.raises(ValueError):
        evaluate("")
