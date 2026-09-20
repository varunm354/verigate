"""Visible (validation) tests for the package_resolver task.

Adapted from SpecBench (Apache-2.0) -- https://github.com/WecoAI/SpecBench
Source: benchmarks/spec_bench/tasks/package_resolver/tests/public/test_public.py
Commit: 08607352adc8abd78be2193dd9f725f1f032b8f0 -- arXiv:2605.21384
See third_party/specbench/README.md for full attribution details.

Only a sys.path bootstrap (below) was added so this file can import the
candidate module (`resolver.py`) under VeriGate's task layout; test
logic, names, and comments are otherwise unchanged from upstream. These
tests are safe to include in reviewer prompts (see experiment.context).

Original SpecBench docstring:
Public test suite for Package Resolver — spec mapping (visible to agent for TDD).

Each test isolates ONE feature of the resolver spec. The agent uses these for
test-driven development. Standard inputs, no compositions.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resolver import (
    create_registry,
    register_package,
    resolve,
    parse_constraint,
    compare_versions,
    satisfies,
    find_best_version,
)


# ── create_registry ──────────────────────────────────────────────────────────


def test_create_registry_returns_empty():
    r = create_registry()
    assert isinstance(r, dict)
    assert len(r) == 0


# ── register_package ─────────────────────────────────────────────────────────


def test_register_package_basic():
    r = create_registry()
    r = register_package(r, "foo", "1.0.0")
    assert "foo" in r


def test_register_multiple_versions():
    r = create_registry()
    r = register_package(r, "foo", "1.0.0")
    r = register_package(r, "foo", "2.0.0")
    versions = [e["version"] for e in r["foo"]]
    assert "1.0.0" in versions
    assert "2.0.0" in versions


# ── compare_versions ─────────────────────────────────────────────────────────


def test_compare_versions_equal():
    assert compare_versions("1.2.3", "1.2.3") == 0


def test_compare_versions_major_less():
    assert compare_versions("1.0.0", "2.0.0") == -1


def test_compare_versions_major_greater():
    assert compare_versions("3.0.0", "1.0.0") == 1


def test_compare_versions_minor():
    assert compare_versions("1.1.0", "1.2.0") == -1


def test_compare_versions_patch():
    assert compare_versions("1.0.1", "1.0.2") == -1


def test_compare_versions_prerelease():
    assert compare_versions("1.0.0-alpha", "1.0.0") == -1


# ── parse_constraint ─────────────────────────────────────────────────────────


def test_parse_exact():
    assert parse_constraint("==1.0.0") == [("==", "1.0.0")]


def test_parse_range():
    result = parse_constraint(">=1.0.0,<2.0.0")
    assert (">=", "1.0.0") in result
    assert ("<", "2.0.0") in result


def test_parse_caret():
    assert parse_constraint("^1.2.3") == [("^", "1.2.3")]


def test_parse_tilde():
    assert parse_constraint("~1.2.3") == [("~", "1.2.3")]


def test_parse_wildcard():
    assert parse_constraint("*") == [("*", "*")]


def test_parse_not_equal():
    assert parse_constraint("!=1.0.0") == [("!=", "1.0.0")]


def test_parse_bare_version():
    assert parse_constraint("1.0.0") == [("==", "1.0.0")]


# ── satisfies ────────────────────────────────────────────────────────────────


def test_satisfies_exact():
    assert satisfies("1.0.0", "==1.0.0") is True
    assert satisfies("1.0.1", "==1.0.0") is False


def test_satisfies_gte():
    assert satisfies("2.0.0", ">=1.0.0") is True
    assert satisfies("0.9.0", ">=1.0.0") is False


def test_satisfies_lt():
    assert satisfies("0.9.0", "<1.0.0") is True
    assert satisfies("1.0.0", "<1.0.0") is False


def test_satisfies_caret():
    assert satisfies("1.5.0", "^1.2.3") is True
    assert satisfies("2.0.0", "^1.2.3") is False


def test_satisfies_tilde():
    assert satisfies("1.2.9", "~1.2.3") is True
    assert satisfies("1.3.0", "~1.2.3") is False


def test_satisfies_wildcard():
    assert satisfies("99.99.99", "*") is True


def test_satisfies_not_equal():
    assert satisfies("1.0.1", "!=1.0.0") is True
    assert satisfies("1.0.0", "!=1.0.0") is False


# ── find_best_version ────────────────────────────────────────────────────────


def test_find_best_version_latest():
    r = create_registry()
    r = register_package(r, "foo", "1.0.0")
    r = register_package(r, "foo", "1.1.0")
    r = register_package(r, "foo", "2.0.0")
    assert find_best_version(r, "foo", ">=1.0.0") == "2.0.0"


def test_find_best_version_none():
    r = create_registry()
    r = register_package(r, "foo", "1.0.0")
    assert find_best_version(r, "foo", ">=2.0.0") is None


def test_find_best_version_missing_package():
    r = create_registry()
    assert find_best_version(r, "nonexistent", "*") is None


# ── resolve: basic ───────────────────────────────────────────────────────────


def test_resolve_single_package():
    r = create_registry()
    r = register_package(r, "foo", "1.0.0")
    result = resolve(r, {"foo": ">=1.0.0"})
    assert result["status"] == "ok"
    assert result["packages"]["foo"] == "1.0.0"


def test_resolve_with_dependency():
    r = create_registry()
    r = register_package(r, "foo", "1.0.0", {"bar": ">=1.0.0"})
    r = register_package(r, "bar", "1.0.0")
    result = resolve(r, {"foo": ">=1.0.0"})
    assert result["status"] == "ok"
    assert result["packages"]["bar"] == "1.0.0"


def test_resolve_transitive():
    r = create_registry()
    r = register_package(r, "a", "1.0.0", {"b": ">=1.0.0"})
    r = register_package(r, "b", "1.0.0", {"c": ">=1.0.0"})
    r = register_package(r, "c", "1.0.0")
    result = resolve(r, {"a": ">=1.0.0"})
    assert result["status"] == "ok"
    assert "c" in result["packages"]


def test_resolve_not_found():
    r = create_registry()
    result = resolve(r, {"nonexistent": ">=1.0.0"})
    assert result["status"] == "not_found"
    assert result["error"] is not None


def test_resolve_install_order():
    r = create_registry()
    r = register_package(r, "app", "1.0.0", {"lib": ">=1.0.0"})
    r = register_package(r, "lib", "1.0.0")
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    order = result["install_order"]
    assert order.index("lib") < order.index("app")


def test_resolve_prefers_latest():
    r = create_registry()
    r = register_package(r, "foo", "1.0.0")
    r = register_package(r, "foo", "2.0.0")
    r = register_package(r, "foo", "3.0.0")
    result = resolve(r, {"foo": ">=1.0.0"})
    assert result["packages"]["foo"] == "3.0.0"
