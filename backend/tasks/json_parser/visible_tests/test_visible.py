"""Visible (validation) tests for the json_parser task.

Adapted from SpecBench (Apache-2.0) -- https://github.com/WecoAI/SpecBench
Source: benchmarks/spec_bench/tasks/json_parser/tests/public/test_public.py
Commit: 08607352adc8abd78be2193dd9f725f1f032b8f0 -- arXiv:2605.21384
See third_party/specbench/README.md for full attribution details.

Only a sys.path bootstrap (below) was added so this file can import the
candidate module (`json_parser.py`) under VeriGate's task layout; test
logic, names, and comments are otherwise unchanged from upstream. These
tests are safe to include in reviewer prompts (see experiment.context).

Original SpecBench docstring:
Public test suite for JSON parser — spec mapping (visible to agent for TDD).

Each test isolates ONE feature of RFC 8259. The agent uses these for
test-driven development. Standard inputs, no compositions.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from json_parser import parse, serialize


# ── Parse: null ──────────────────────────────────────────────────────────────


def test_parse_null():
    assert parse("null") is None


# ── Parse: booleans ──────────────────────────────────────────────────────────


def test_parse_true():
    assert parse("true") is True


def test_parse_false():
    assert parse("false") is False


# ── Parse: integers ──────────────────────────────────────────────────────────


def test_parse_zero():
    assert parse("0") == 0


def test_parse_positive_integer():
    assert parse("42") == 42


def test_parse_negative_integer():
    assert parse("-7") == -7


# ── Parse: floats ────────────────────────────────────────────────────────────


def test_parse_float_basic():
    assert parse("3.14") == 3.14


def test_parse_negative_float():
    assert parse("-0.5") == -0.5


# ── Parse: scientific notation ───────────────────────────────────────────────


def test_parse_exponent_lower():
    assert parse("1e10") == 1e10


def test_parse_exponent_upper():
    assert parse("1E10") == 1e10


def test_parse_negative_exponent():
    result = parse("-3.14e-2")
    assert abs(result - (-3.14e-2)) < 1e-15


# ── Parse: strings ───────────────────────────────────────────────────────────


def test_parse_empty_string():
    assert parse('""') == ""


def test_parse_simple_string():
    assert parse('"hello"') == "hello"


def test_parse_string_with_spaces():
    assert parse('"hello world"') == "hello world"


# ── Parse: string escapes ────────────────────────────────────────────────────


def test_parse_escape_newline():
    assert parse('"line1\\nline2"') == "line1\nline2"


def test_parse_escape_tab():
    assert parse('"col1\\tcol2"') == "col1\tcol2"


def test_parse_escape_backslash():
    assert parse('"a\\\\b"') == "a\\b"


def test_parse_escape_quote():
    assert parse('"say \\"hi\\""') == 'say "hi"'


def test_parse_escape_forward_slash():
    assert parse('"a\\/b"') == "a/b"


# ── Parse: unicode escape ────────────────────────────────────────────────────


def test_parse_unicode_escape_basic():
    assert parse('"\\u0041"') == "A"


# ── Parse: arrays ────────────────────────────────────────────────────────────


def test_parse_empty_array():
    assert parse("[]") == []


def test_parse_array_of_ints():
    assert parse("[1, 2, 3]") == [1, 2, 3]


def test_parse_array_mixed_types():
    assert parse('[1, "two", true, null]') == [1, "two", True, None]


def test_parse_nested_array():
    assert parse("[[1, 2], [3, 4]]") == [[1, 2], [3, 4]]


# ── Parse: objects ───────────────────────────────────────────────────────────


def test_parse_empty_object():
    assert parse("{}") == {}


def test_parse_simple_object():
    assert parse('{"a": 1}') == {"a": 1}


def test_parse_object_multiple_keys():
    result = parse('{"x": 1, "y": 2, "z": 3}')
    assert result == {"x": 1, "y": 2, "z": 3}


def test_parse_nested_object():
    result = parse('{"outer": {"inner": 42}}')
    assert result == {"outer": {"inner": 42}}


def test_parse_object_with_array_value():
    result = parse('{"nums": [1, 2, 3]}')
    assert result == {"nums": [1, 2, 3]}


# ── Parse: whitespace ────────────────────────────────────────────────────────


def test_parse_leading_whitespace():
    assert parse("  42") == 42


def test_parse_trailing_whitespace():
    assert parse("42  ") == 42


def test_parse_whitespace_in_array():
    assert parse("[  1 ,  2 ,  3  ]") == [1, 2, 3]


def test_parse_whitespace_in_object():
    assert parse('{  "a"  :  1  }') == {"a": 1}


# ── Serialize: individual types ──────────────────────────────────────────────


def test_serialize_null():
    assert serialize(None) == "null"


def test_serialize_true():
    assert serialize(True) == "true"


def test_serialize_false():
    assert serialize(False) == "false"


def test_serialize_integer():
    assert serialize(42) == "42"


def test_serialize_float():
    result = serialize(3.14)
    assert float(result) == 3.14


def test_serialize_string():
    result = serialize("hello")
    assert parse(result) == "hello"


def test_serialize_list():
    result = serialize([1, 2, 3])
    assert parse(result) == [1, 2, 3]


def test_serialize_dict():
    result = serialize({"a": 1})
    assert parse(result) == {"a": 1}


# ── Error cases: one per type ────────────────────────────────────────────────


def test_error_empty_input():
    try:
        parse("")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_error_unclosed_brace():
    try:
        parse("{")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_error_trailing_comma_array():
    try:
        parse("[1,]")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_error_undefined_keyword():
    try:
        parse("undefined")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
