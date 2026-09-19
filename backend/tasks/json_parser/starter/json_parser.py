"""JSON Parser (RFC 8259) -- VeriGate starter implementation.

Adapted from SpecBench (Apache-2.0) -- https://github.com/WecoAI/SpecBench
Source: benchmarks/spec_bench/tasks/json_parser/starter/json_parser.py
Commit: 08607352adc8abd78be2193dd9f725f1f032b8f0 -- arXiv:2605.21384
See third_party/specbench/README.md for full attribution details.

This is SpecBench's unimplemented starter skeleton for the json_parser
task. Candidate generation may use only this file (plus the specification
and visible tests); the tracked reference at ``json_parser.py`` is the
known-correct integration baseline and must not be overwritten.

Original SpecBench docstring:
JSON Parser — Starter Implementation

Implement parse() and serialize() following RFC 8259.
Do NOT use Python's built-in json module.
"""


def parse(text: str) -> object:
    """Parse a JSON text string and return the corresponding Python object.

    Raises ValueError for invalid JSON input.
    """
    raise NotImplementedError("Implement JSON parsing from scratch")


def serialize(obj: object) -> str:
    """Serialize a Python object to a JSON text string.

    Supported types: None, bool, int, float, str, list, dict.
    Raises TypeError for unsupported types.
    Raises ValueError for non-finite floats (inf, nan).
    """
    raise NotImplementedError("Implement JSON serialization from scratch")
