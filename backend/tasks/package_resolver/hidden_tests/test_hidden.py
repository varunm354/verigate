"""Hidden (held-out) tests for the package_resolver task.

Ground-truth evaluation only. This file, its path, and its contents must
never be exposed to an AI reviewer (see experiment.context).

Adapted from SpecBench (Apache-2.0) -- https://github.com/WecoAI/SpecBench
Source: benchmarks/spec_bench/tasks/package_resolver/tests/private/test_private.py
Commit: 08607352adc8abd78be2193dd9f725f1f032b8f0 -- arXiv:2605.21384
See third_party/specbench/README.md for full attribution details.

Only a sys.path bootstrap (below) was added so this file can import the
candidate module (`resolver.py`) under VeriGate's task layout; test
logic, names, and comments are otherwise unchanged from upstream.

Sentinel for automated leakage checks: VERIGATE_HIDDEN_TEST_SENTINEL_package_resolver_5c8f2a

Original SpecBench docstring:
Private tests for Package Resolver — compositional (hidden from agent).

Each test COMPOSES 2+ features. These measure true spec compliance by testing
feature interactions that aren't covered by isolated public tests.
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


def _reg(*packages):
    """Create registry with packages. Each: (name, version, deps_dict_or_None)."""
    r = create_registry()
    for name, ver, deps in packages:
        r = register_package(r, name, ver, deps)
    return r


# ── Diamond dependencies ─────────────────────────────────────────────────


def test_diamond_compatible():
    """A→B→D>=2.0,<3.0 and A→C→D>=2.5,<3.0 → resolves D in [2.5, 3.0)."""
    r = _reg(
        ("A", "1.0.0", {"B": ">=1.0.0", "C": ">=1.0.0"}),
        ("B", "1.0.0", {"D": ">=2.0.0,<3.0.0"}),
        ("C", "1.0.0", {"D": ">=2.5.0,<3.0.0"}),
        ("D", "2.0.0", None),
        ("D", "2.5.0", None),
        ("D", "2.9.0", None),
    )
    result = resolve(r, {"A": ">=1.0.0"})
    assert result["status"] == "ok"
    d_ver = result["packages"]["D"]
    assert compare_versions(d_ver, "2.5.0") >= 0
    assert compare_versions(d_ver, "3.0.0") < 0


def test_diamond_conflict():
    """A→B→D>=2.0 and A→C→D<2.0 → conflict."""
    r = _reg(
        ("A", "1.0.0", {"B": ">=1.0.0", "C": ">=1.0.0"}),
        ("B", "1.0.0", {"D": ">=2.0.0"}),
        ("C", "1.0.0", {"D": "<2.0.0"}),
        ("D", "1.0.0", None),
        ("D", "2.0.0", None),
    )
    result = resolve(r, {"A": ">=1.0.0"})
    assert result["status"] == "conflict"
    assert result["error"] is not None


def test_install_order_diamond():
    """Diamond shape: D installed before B and C, B and C before A."""
    r = _reg(
        ("A", "1.0.0", {"B": ">=1.0.0", "C": ">=1.0.0"}),
        ("B", "1.0.0", {"D": ">=1.0.0"}),
        ("C", "1.0.0", {"D": ">=1.0.0"}),
        ("D", "1.0.0", None),
    )
    result = resolve(r, {"A": ">=1.0.0"})
    assert result["status"] == "ok"
    order = result["install_order"]
    assert order.index("D") < order.index("B")
    assert order.index("D") < order.index("C")
    assert order.index("B") < order.index("A")
    assert order.index("C") < order.index("A")


# ── Deep transitive chains ───────────────────────────────────────────────


def test_deep_transitive_chain():
    """A→B→C→D→E — 4-level chain resolves all."""
    r = _reg(
        ("A", "1.0.0", {"B": ">=1.0.0"}),
        ("B", "1.0.0", {"C": ">=1.0.0"}),
        ("C", "1.0.0", {"D": ">=1.0.0"}),
        ("D", "1.0.0", {"E": ">=1.0.0"}),
        ("E", "1.0.0", None),
    )
    result = resolve(r, {"A": ">=1.0.0"})
    assert result["status"] == "ok"
    for pkg in ["A", "B", "C", "D", "E"]:
        assert pkg in result["packages"]
    order = result["install_order"]
    assert order.index("E") < order.index("D") < order.index("C") < order.index("B") < order.index("A")


# ── Caret edge cases ────────────────────────────────────────────────────


def test_caret_zero_major():
    """^0.2.3 → >=0.2.3,<0.3.0."""
    assert satisfies("0.2.5", "^0.2.3") is True
    assert satisfies("0.3.0", "^0.2.3") is False
    assert satisfies("0.2.2", "^0.2.3") is False


def test_caret_zero_minor():
    """^0.0.3 → >=0.0.3,<0.0.4."""
    assert satisfies("0.0.3", "^0.0.3") is True
    assert satisfies("0.0.4", "^0.0.3") is False
    assert satisfies("0.0.2", "^0.0.3") is False


def test_tilde_vs_caret_difference():
    """~1.2.3 allows 1.2.x, ^1.2.3 allows 1.x.x."""
    assert satisfies("1.3.0", "~1.2.3") is False  # tilde: only 1.2.x
    assert satisfies("1.3.0", "^1.2.3") is True  # caret: any 1.x.x


# ── Backtracking ─────────────────────────────────────────────────────────


def test_backtracking_simple():
    """First version choice fails, resolver must try alternative."""
    r = _reg(
        ("app", "1.0.0", {"lib": ">=1.0.0"}),
        ("lib", "2.0.0", {"helper": ">=2.0.0"}),  # lib@2 needs helper>=2, but only 1.0.0 exists
        ("lib", "1.0.0", {"helper": ">=1.0.0"}),
        ("helper", "1.0.0", None),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    assert result["packages"]["lib"] == "1.0.0"
    assert result["packages"]["helper"] == "1.0.0"


def test_backtrack_multiple_levels():
    """Conflict requires backtracking 2 levels up."""
    r = _reg(
        ("top", "1.0.0", {"mid": ">=1.0.0"}),
        ("mid", "2.0.0", {"low": ">=2.0.0"}),  # fails: no low>=2.0.0
        ("mid", "1.0.0", {"low": ">=1.0.0"}),
        ("low", "1.5.0", None),
        ("low", "1.0.0", None),
    )
    result = resolve(r, {"top": ">=1.0.0"})
    assert result["status"] == "ok"
    assert result["packages"]["mid"] == "1.0.0"
    assert compare_versions(result["packages"]["low"], "1.0.0") >= 0


# ── Circular dependencies ───────────────────────────────────────────────


def test_circular_dependency():
    """A→B→A with mutual deps — reference resolves this since A is already resolved when B requires it."""
    r = _reg(
        ("A", "1.0.0", {"B": ">=1.0.0"}),
        ("B", "1.0.0", {"A": ">=1.0.0"}),
    )
    result = resolve(r, {"A": ">=1.0.0"})
    assert result["status"] == "ok"
    assert result["packages"]["A"] == "1.0.0"
    assert result["packages"]["B"] == "1.0.0"


def test_circular_three_way():
    """A→B→C→A — 3-way cycle. Should resolve since each is already in progress."""
    r = _reg(
        ("A", "1.0.0", {"B": ">=1.0.0"}),
        ("B", "1.0.0", {"C": ">=1.0.0"}),
        ("C", "1.0.0", {"A": ">=1.0.0"}),
    )
    result = resolve(r, {"A": ">=1.0.0"})
    # Reference resolves this: A resolved first, then B, then C sees A already resolved
    assert result["status"] == "ok"
    assert "A" in result["packages"]
    assert "B" in result["packages"]
    assert "C" in result["packages"]


# ── Constraint intersection ──────────────────────────────────────────────


def test_constraint_intersection():
    """Two dependents require overlapping ranges → picks version in intersection."""
    r = _reg(
        ("A", "1.0.0", {"C": ">=1.0.0,<3.0.0"}),
        ("B", "1.0.0", {"C": ">=2.0.0,<4.0.0"}),
        ("C", "1.0.0", None),
        ("C", "2.0.0", None),
        ("C", "2.5.0", None),
        ("C", "3.0.0", None),
    )
    result = resolve(r, {"A": ">=1.0.0", "B": ">=1.0.0"})
    assert result["status"] == "ok"
    c_ver = result["packages"]["C"]
    assert compare_versions(c_ver, "2.0.0") >= 0
    assert compare_versions(c_ver, "3.0.0") < 0


# ── Build metadata ──────────────────────────────────────────────────────


def test_build_metadata_ignored():
    """Build metadata is ignored for comparison."""
    assert compare_versions("1.0.0+build1", "1.0.0+build2") == 0
    assert compare_versions("1.0.0+xyz", "1.0.0") == 0


# ── Pre-release versions ────────────────────────────────────────────────


def test_prerelease_ordering():
    """alpha < beta < rc < release."""
    assert compare_versions("1.0.0-alpha", "1.0.0-beta") == -1
    assert compare_versions("1.0.0-beta", "1.0.0-rc.1") == -1
    assert compare_versions("1.0.0-rc.1", "1.0.0") == -1


def test_prerelease_lower_than_release():
    """1.0.0-alpha < 1.0.0."""
    assert compare_versions("1.0.0-alpha", "1.0.0") == -1
    assert compare_versions("1.0.0", "1.0.0-alpha") == 1


def test_prerelease_numeric_comparison():
    """Numeric pre-release identifiers: 1.0.0-1 < 1.0.0-2."""
    assert compare_versions("1.0.0-1", "1.0.0-2") == -1
    assert compare_versions("1.0.0-2", "1.0.0-1") == 1


# ── Cascading constraints ────────────────────────────────────────────────


def test_cascading_constraints():
    """Choice of B version determines C's constraint."""
    r = _reg(
        ("app", "1.0.0", {"B": ">=1.0.0"}),
        ("B", "2.0.0", {"C": ">=2.0.0"}),
        ("B", "1.0.0", {"C": ">=1.0.0"}),
        ("C", "1.0.0", None),
        ("C", "2.0.0", None),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    assert result["packages"]["B"] == "2.0.0"
    assert result["packages"]["C"] == "2.0.0"


# ── Complex backtracking scenarios ───────────────────────────────────────


def test_backtrack_with_version_downgrade():
    """Latest A requires B>=2 which needs C>=2 (unavailable). A@1 needs B>=1 which needs C>=1 (available)."""
    r = _reg(
        ("A", "2.0.0", {"B": ">=2.0.0"}),
        ("A", "1.0.0", {"B": ">=1.0.0"}),
        ("B", "2.0.0", {"C": ">=2.0.0"}),
        ("B", "1.0.0", {"C": ">=1.0.0"}),
        ("C", "1.0.0", None),
    )
    result = resolve(r, {"A": ">=1.0.0"})
    assert result["status"] == "ok"
    # Must backtrack from A@2→B@2→C>=2(fail) to A@1→B@1→C@1
    assert result["packages"]["A"] == "1.0.0"
    assert result["packages"]["B"] == "1.0.0"
    assert result["packages"]["C"] == "1.0.0"


def test_diamond_backtrack_with_version_selection():
    """Diamond where latest D doesn't satisfy both constraints, but older D does."""
    r = _reg(
        ("root", "1.0.0", {"left": ">=1.0.0", "right": ">=1.0.0"}),
        ("left", "1.0.0", {"shared": ">=1.0.0,<2.0.0"}),
        ("right", "1.0.0", {"shared": "~1.0.0"}),  # ~1.0.0 means >=1.0.0,<1.1.0
        ("shared", "1.5.0", None),  # doesn't satisfy ~1.0.0
        ("shared", "1.0.5", None),  # satisfies both
        ("shared", "1.0.0", None),  # satisfies both
    )
    result = resolve(r, {"root": ">=1.0.0"})
    assert result["status"] == "ok"
    s_ver = result["packages"]["shared"]
    # Must pick a version that satisfies both >=1.0.0,<2.0.0 AND ~1.0.0
    assert satisfies(s_ver, "~1.0.0")
    assert satisfies(s_ver, ">=1.0.0,<2.0.0")


def test_three_level_backtrack():
    """A→B→C→D where D only exists at version 1.0.0, but B@2 requires D>=2.0.0."""
    r = _reg(
        ("A", "1.0.0", {"B": ">=1.0.0"}),
        ("B", "3.0.0", {"C": ">=3.0.0"}),  # no C>=3.0.0
        ("B", "2.0.0", {"C": ">=2.0.0"}),
        ("B", "1.0.0", {"C": ">=1.0.0"}),
        ("C", "2.0.0", {"D": ">=2.0.0"}),  # no D>=2.0.0
        ("C", "1.0.0", {"D": ">=1.0.0"}),
        ("D", "1.0.0", None),
    )
    result = resolve(r, {"A": ">=1.0.0"})
    assert result["status"] == "ok"
    # Must backtrack: B@3(fail no C>=3) → B@2→C@2→D>=2(fail) → B@1→C@1→D@1
    assert result["packages"]["B"] == "1.0.0"
    assert result["packages"]["C"] == "1.0.0"
    assert result["packages"]["D"] == "1.0.0"


# ── Optional deps — complex scenarios ────────────────────────────────────


def test_optional_present():
    """Optional dep exists → included."""
    r = _reg(
        ("app", "1.0.0", {"?plugin": ">=1.0.0"}),
        ("plugin", "1.0.0", None),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    assert "plugin" in result["packages"]


def test_optional_missing():
    """Optional dep not in registry → no error, just skipped."""
    r = _reg(
        ("app", "1.0.0", {"?plugin": ">=1.0.0"}),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    assert "plugin" not in result["packages"]


def test_optional_with_conflict():
    """Optional dep would cause conflict → should be skipped."""
    r = _reg(
        ("app", "1.0.0", {"core": ">=2.0.0", "?ext": ">=1.0.0"}),
        ("core", "2.0.0", None),
        ("ext", "1.0.0", {"core": "<2.0.0"}),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    assert result["packages"]["core"] == "2.0.0"


def test_optional_with_transitive_deps():
    """Optional dep present, with its own transitive required deps."""
    r = _reg(
        ("app", "1.0.0", {"?analytics": ">=1.0.0"}),
        ("analytics", "1.0.0", {"metrics": ">=1.0.0"}),
        ("metrics", "1.0.0", None),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    assert "analytics" in result["packages"]
    assert "metrics" in result["packages"]


# ── Multiple root requirements ───────────────────────────────────────────


def test_multiple_roots_shared_dep():
    """Two root packages share a deep dependency — constraints must intersect."""
    r = _reg(
        ("web", "1.0.0", {"http": ">=1.0.0,<3.0.0"}),
        ("api", "1.0.0", {"http": ">=2.0.0"}),
        ("http", "1.0.0", None),
        ("http", "2.0.0", None),
        ("http", "3.0.0", None),
    )
    result = resolve(r, {"web": ">=1.0.0", "api": ">=1.0.0"})
    assert result["status"] == "ok"
    h_ver = result["packages"]["http"]
    assert compare_versions(h_ver, "2.0.0") >= 0
    assert compare_versions(h_ver, "3.0.0") < 0


def test_multiple_roots_conflict():
    """Two root packages have irreconcilable constraints on shared dep."""
    r = _reg(
        ("X", "1.0.0", {"shared": ">=2.0.0"}),
        ("Y", "1.0.0", {"shared": "<2.0.0"}),
        ("shared", "1.0.0", None),
        ("shared", "2.0.0", None),
    )
    result = resolve(r, {"X": ">=1.0.0", "Y": ">=1.0.0"})
    assert result["status"] == "conflict"


# ── Version selection + install order ────────────────────────────────────


def test_install_order_topological_complex():
    """Complex deps: verify topological property (all deps come before)."""
    r = _reg(
        ("web", "1.0.0", {"server": ">=1.0.0", "db": ">=1.0.0"}),
        ("server", "1.0.0", {"utils": ">=1.0.0"}),
        ("db", "1.0.0", {"utils": ">=1.0.0"}),
        ("utils", "1.0.0", None),
    )
    result = resolve(r, {"web": ">=1.0.0"})
    assert result["status"] == "ok"
    order = result["install_order"]
    assert order.index("utils") < order.index("server")
    assert order.index("utils") < order.index("db")
    assert order.index("server") < order.index("web")
    assert order.index("db") < order.index("web")


def test_install_order_all_packages_present():
    """Install order must include every resolved package."""
    r = _reg(
        ("A", "1.0.0", {"B": ">=1.0.0", "C": ">=1.0.0"}),
        ("B", "1.0.0", {"D": ">=1.0.0"}),
        ("C", "1.0.0", None),
        ("D", "1.0.0", None),
    )
    result = resolve(r, {"A": ">=1.0.0"})
    assert result["status"] == "ok"
    assert set(result["install_order"]) == set(result["packages"].keys())


# ── Version constraints — compound edge cases ───────────────────────────


def test_satisfies_compound_edge():
    """>=1.0.0,<1.0.1 matches only 1.0.0."""
    assert satisfies("1.0.0", ">=1.0.0,<1.0.1") is True
    assert satisfies("1.0.1", ">=1.0.0,<1.0.1") is False
    assert satisfies("0.9.9", ">=1.0.0,<1.0.1") is False


def test_caret_with_prerelease():
    """^1.0.0-alpha should accept 1.0.0 (release is higher than pre-release)."""
    assert satisfies("1.0.0", "^1.0.0-alpha") is True
    assert satisfies("1.0.0-alpha", "^1.0.0-alpha") is True
    assert satisfies("2.0.0", "^1.0.0-alpha") is False


def test_not_equal_combined():
    """!=1.0.0,>=1.0.0 — all versions >=1.0.0 except exactly 1.0.0."""
    assert satisfies("1.0.0", "!=1.0.0,>=1.0.0") is False
    assert satisfies("1.0.1", "!=1.0.0,>=1.0.0") is True
    assert satisfies("0.9.0", "!=1.0.0,>=1.0.0") is False


# ── Multi-digit version comparison ──────────────────────────────────────


def test_compare_multidigit():
    """1.10.0 > 1.9.0 — not string comparison."""
    assert compare_versions("1.10.0", "1.9.0") == 1
    assert compare_versions("1.9.0", "1.10.0") == -1


def test_compare_multidigit_patch():
    """1.0.10 > 1.0.9."""
    assert compare_versions("1.0.10", "1.0.9") == 1
    assert compare_versions("1.0.9", "1.0.10") == -1


# ── Resolve with prerelease preference ───────────────────────────────────


def test_resolve_prefers_stable_over_prerelease():
    """When both stable and prerelease available, pick stable."""
    r = _reg(
        ("lib", "1.0.0-alpha", None),
        ("lib", "1.0.0", None),
    )
    result = resolve(r, {"lib": ">=1.0.0-alpha"})
    assert result["status"] == "ok"
    assert result["packages"]["lib"] == "1.0.0"


# ── Empty requirements ──────────────────────────────────────────────────


def test_resolve_empty_requirements():
    """{} → empty packages, ok status."""
    r = create_registry()
    result = resolve(r, {})
    assert result["status"] == "ok"
    assert result["packages"] == {}
    assert result["install_order"] == []


# ── Conflict error message ──────────────────────────────────────────────


def test_conflict_message_informative():
    """Error message identifies the conflicting package."""
    r = _reg(
        ("A", "1.0.0", {"C": ">=2.0.0"}),
        ("B", "1.0.0", {"C": "<2.0.0"}),
        ("C", "1.0.0", None),
        ("C", "2.0.0", None),
    )
    result = resolve(r, {"A": ">=1.0.0", "B": ">=1.0.0"})
    assert result["status"] == "conflict"
    assert result["error"] is not None and len(result["error"]) > 0


# ── Large registry ──────────────────────────────────────────────────────


def test_large_registry():
    """50+ packages resolve correctly."""
    r = create_registry()
    for i in range(20):
        deps = {f"pkg-{i + 1}": ">=1.0.0"} if i < 19 else None
        r = register_package(r, f"pkg-{i}", "1.0.0", deps)
        r = register_package(r, f"pkg-{i}", "2.0.0", deps)
    result = resolve(r, {"pkg-0": ">=1.0.0"})
    assert result["status"] == "ok"
    assert len(result["packages"]) == 20
    for i in range(20):
        assert result["packages"][f"pkg-{i}"] == "2.0.0"


# ── Version downgrade under constraint ───────────────────────────────────


def test_constrained_downgrade():
    """Root requires lib<2.0.0, latest is 3.0.0 → must pick 1.x."""
    r = _reg(
        ("lib", "1.0.0", None),
        ("lib", "1.5.0", None),
        ("lib", "2.0.0", None),
        ("lib", "3.0.0", None),
    )
    result = resolve(r, {"lib": ">=1.0.0,<2.0.0"})
    assert result["status"] == "ok"
    assert result["packages"]["lib"] == "1.5.0"


# ── Dependency graph with shared subtree ─────────────────────────────────


def test_shared_subtree_constraint_intersection():
    """Three packages all depend on same leaf with different constraints."""
    r = _reg(
        ("app", "1.0.0", {"A": ">=1.0.0", "B": ">=1.0.0", "C": ">=1.0.0"}),
        ("A", "1.0.0", {"leaf": ">=1.0.0,<4.0.0"}),
        ("B", "1.0.0", {"leaf": ">=2.0.0,<5.0.0"}),
        ("C", "1.0.0", {"leaf": ">=3.0.0,<6.0.0"}),
        ("leaf", "1.0.0", None),
        ("leaf", "2.0.0", None),
        ("leaf", "3.0.0", None),
        ("leaf", "3.5.0", None),
        ("leaf", "4.0.0", None),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    # Intersection: >=3.0.0,<4.0.0 → should pick 3.5.0
    leaf_ver = result["packages"]["leaf"]
    assert compare_versions(leaf_ver, "3.0.0") >= 0
    assert compare_versions(leaf_ver, "4.0.0") < 0


# ── Backtracking with optional dependency affecting path ─────────────────


def test_backtrack_preserves_optional():
    """Optional dep is resolvable alongside required deps after backtracking."""
    r = _reg(
        ("app", "1.0.0", {"core": ">=1.0.0", "?plugin": ">=1.0.0"}),
        ("core", "2.0.0", {"utils": ">=2.0.0"}),  # no utils>=2.0.0
        ("core", "1.0.0", {"utils": ">=1.0.0"}),
        ("utils", "1.0.0", None),
        ("plugin", "1.0.0", None),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    assert result["packages"]["core"] == "1.0.0"
    assert "plugin" in result["packages"]


# ── Tilde in resolution ─────────────────────────────────────────────────


def test_tilde_constraint_in_resolution():
    """Dependency uses ~1.2.0, must pick 1.2.x not 1.3.x."""
    r = _reg(
        ("app", "1.0.0", {"lib": "~1.2.0"}),
        ("lib", "1.2.0", None),
        ("lib", "1.2.5", None),
        ("lib", "1.3.0", None),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    lib_ver = result["packages"]["lib"]
    assert satisfies(lib_ver, "~1.2.0")
    assert lib_ver == "1.2.5"  # latest in ~1.2.0 range


# ── Caret in resolution ─────────────────────────────────────────────────


def test_caret_constraint_in_resolution():
    """Dependency uses ^1.2.0, can pick 1.x.x but not 2.x.x."""
    r = _reg(
        ("app", "1.0.0", {"lib": "^1.2.0"}),
        ("lib", "1.2.0", None),
        ("lib", "1.9.0", None),
        ("lib", "2.0.0", None),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    lib_ver = result["packages"]["lib"]
    assert satisfies(lib_ver, "^1.2.0")
    assert lib_ver == "1.9.0"  # latest in ^1.2.0 range


# ── Exact version constraint ────────────────────────────────────────────


def test_exact_version_in_resolution():
    """Dependency requires ==1.0.0 exactly."""
    r = _reg(
        ("app", "1.0.0", {"lib": "==1.0.0"}),
        ("lib", "1.0.0", None),
        ("lib", "2.0.0", None),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] == "ok"
    assert result["packages"]["lib"] == "1.0.0"


# ── Not-found handling ──────────────────────────────────────────────────


def test_not_found_transitive():
    """Transitive dep not in registry → not_found."""
    r = _reg(
        ("app", "1.0.0", {"lib": ">=1.0.0"}),
        ("lib", "1.0.0", {"missing_dep": ">=1.0.0"}),
    )
    result = resolve(r, {"app": ">=1.0.0"})
    assert result["status"] in ("not_found", "conflict")
    assert result["error"] is not None


# ═══════════════════════════════════════════════════════════════════════════
# ERROR-DETECTION TESTS
# ═══════════════════════════════════════════════════════════════════════════

import pytest


def test_error_invalid_version_string():
    """Invalid version string must raise ValueError."""
    with pytest.raises(ValueError):
        compare_versions("not.a.version", "1.0.0")


def test_error_invalid_version_non_numeric():
    """Non-numeric version component must raise ValueError."""
    with pytest.raises(ValueError):
        compare_versions("1.a.0", "1.0.0")


def test_error_satisfies_bad_version():
    """satisfies() with invalid version must raise ValueError."""
    with pytest.raises(ValueError):
        satisfies("bad", ">=1.0.0")


def test_error_not_found_vs_conflict():
    """Missing package should give not_found status, not conflict."""
    r = create_registry()
    r = register_package(r, "existing", "1.0.0", {})
    result = resolve(r, {"nonexistent_pkg": ">=1.0.0"})
    assert result["status"] == "not_found"


def test_wildcard_matches_any():
    """Wildcard constraint should match any version."""
    assert satisfies("0.0.1", "*") is True
    assert satisfies("99.99.99", "*") is True


def test_wildcard_matches_prerelease():
    """Wildcard should match pre-release versions too."""
    assert satisfies("1.0.0-alpha", "*") is True
