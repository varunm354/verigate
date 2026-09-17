"""Hidden tests for the expression_evaluator task.

Ground-truth evaluation only. This file, its path, and its contents must
never be exposed to an AI reviewer.

Sentinel for automated leakage checks: VERIGATE_HIDDEN_TEST_SENTINEL_7f3c1a
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from candidate import evaluate


def test_chained_subtraction_is_left_associative():
    # (10 - 2) - 3 = 5, not 10 - (2 - 3) = 11
    assert evaluate("10 - 2 - 3") == 5


def test_chained_division_is_left_associative():
    # (100 / 10) / 2 = 5.0, not 100 / (10 / 2) = 20.0
    assert evaluate("100 / 10 / 2") == 5.0


def test_mixed_addition_and_subtraction_order():
    # (10 - 2) + 3 = 11, evaluated strictly left-to-right
    assert evaluate("10 - 2 + 3") == 11


def test_unary_minus_before_parenthesized_group():
    assert evaluate("-(2 + 3) * 2") == -10


def test_division_by_zero_inside_nested_parentheses():
    with pytest.raises(ZeroDivisionError):
        evaluate("(2 + 3) / (1 - 1)")


def test_missing_operator_between_numbers_raises_value_error():
    with pytest.raises(ValueError):
        evaluate("2 3")
