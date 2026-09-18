"""JSON Parser (RFC 8259) -- VeriGate candidate implementation.

Adapted from SpecBench (Apache-2.0) -- https://github.com/WecoAI/SpecBench
Source: benchmarks/spec_bench/tasks/json_parser/reference/json_parser.py
Commit: 08607352adc8abd78be2193dd9f725f1f032b8f0 -- arXiv:2605.21384
See third_party/specbench/README.md for full attribution details.

This is SpecBench's reference solution for the json_parser task, used
unmodified (aside from this docstring) as VeriGate's "candidate" for this
milestone -- i.e. a solution expected to pass both the visible and hidden
suites, in contrast to the deliberately imperfect expression_evaluator
candidate.
"""

from __future__ import annotations

import json
import math


def _contains_surrogate(s: str) -> bool:
    for ch in s:
        o = ord(ch)
        if 0xD800 <= o <= 0xDFFF:
            return True
    return False


def _validate_no_surrogates(obj: object) -> None:
    if isinstance(obj, str):
        if _contains_surrogate(obj):
            raise ValueError("Invalid Unicode surrogate in decoded string")
        return

    if isinstance(obj, list):
        for item in obj:
            _validate_no_surrogates(item)
        return

    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and _contains_surrogate(k):
                raise ValueError("Invalid Unicode surrogate in decoded object key")
            _validate_no_surrogates(v)
        return


def parse(text: str) -> object:
    obj = json.loads(text)
    _validate_no_surrogates(obj)
    return obj


def _validate_serializable(obj: object) -> None:
    if obj is None:
        return
    if isinstance(obj, bool):
        return
    if isinstance(obj, int):
        return
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("Non-finite floats are not valid JSON")
        return
    if isinstance(obj, str):
        if _contains_surrogate(obj):
            raise ValueError("Invalid Unicode surrogate in string")
        return
    if isinstance(obj, list):
        for item in obj:
            _validate_serializable(item)
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError("JSON object keys must be strings")
            if _contains_surrogate(k):
                raise ValueError("Invalid Unicode surrogate in object key")
            _validate_serializable(v)
        return
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def serialize(obj: object) -> str:
    _validate_serializable(obj)
    return json.dumps(obj, ensure_ascii=False, allow_nan=False)
