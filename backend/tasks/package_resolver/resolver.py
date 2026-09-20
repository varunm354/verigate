"""Package Dependency Resolver (Semantic Versioning) -- VeriGate candidate implementation.

Adapted from SpecBench (Apache-2.0) -- https://github.com/WecoAI/SpecBench
Source: benchmarks/spec_bench/tasks/package_resolver/reference/resolver.py
Commit: 08607352adc8abd78be2193dd9f725f1f032b8f0 -- arXiv:2605.21384
See third_party/specbench/README.md for full attribution details.

This is SpecBench's reference solution for the package_resolver task, used
unmodified (aside from this docstring) as VeriGate's "candidate" for this
task -- i.e. a solution expected to pass both the visible and hidden
suites, like json_parser's reference.

Original SpecBench docstring:
Package Dependency Resolver — Reference Implementation

Implements semantic versioning, constraint parsing, and dependency resolution
with backtracking. No external libraries used.
"""

import re
from collections import defaultdict


# ── Version Parsing ──────────────────────────────────────────────────────────


def _parse_version(version_str):
    """Parse a semver string into (major, minor, patch, pre_release_ids, build_meta).

    pre_release_ids is a list of identifiers (int or str), or None if no pre-release.
    build_meta is ignored for comparison but preserved.
    """
    # Strip build metadata
    build = None
    if "+" in version_str:
        version_str, build = version_str.split("+", 1)

    # Split pre-release
    pre = None
    if "-" in version_str:
        base, pre_str = version_str.split("-", 1)
        pre_ids = []
        for ident in pre_str.split("."):
            if ident.isdigit():
                pre_ids.append(int(ident))
            else:
                pre_ids.append(ident)
        pre = pre_ids
    else:
        base = version_str

    parts = base.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid version: {version_str}")

    try:
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        raise ValueError(f"Invalid version: {version_str}")

    return major, minor, patch, pre, build


def _version_base(version_str):
    """Return version string without build metadata."""
    if "+" in version_str:
        return version_str.split("+", 1)[0]
    return version_str


# ── Version Comparison ───────────────────────────────────────────────────────


def _compare_pre(pre1, pre2):
    """Compare two pre-release identifier lists.

    None means release (no pre-release) — higher than any pre-release.
    """
    if pre1 is None and pre2 is None:
        return 0
    if pre1 is None:
        return 1  # release > pre-release
    if pre2 is None:
        return -1  # pre-release < release

    for a, b in zip(pre1, pre2):
        if type(a) == type(b):
            if a < b:
                return -1
            if a > b:
                return 1
        else:
            # numeric < alphanumeric
            if isinstance(a, int):
                return -1
            return 1

    # All compared identifiers are equal; shorter is lower
    if len(pre1) < len(pre2):
        return -1
    if len(pre1) > len(pre2):
        return 1
    return 0


def compare_versions(v1, v2):
    """Compare two semver strings. Returns -1, 0, or 1."""
    m1, n1, p1, pre1, _ = _parse_version(v1)
    m2, n2, p2, pre2, _ = _parse_version(v2)

    # Compare major.minor.patch
    for a, b in [(m1, m2), (n1, n2), (p1, p2)]:
        if a < b:
            return -1
        if a > b:
            return 1

    # Compare pre-release
    return _compare_pre(pre1, pre2)


# ── Constraint Parsing ───────────────────────────────────────────────────────


def parse_constraint(constraint):
    """Parse a version constraint string into operator-version pairs.

    E.g., ">=1.0.0,<2.0.0" -> [(">=", "1.0.0"), ("<", "2.0.0")]
    Supports: ==, !=, >=, <=, >, <, ^(caret), ~(tilde), *(wildcard)
    """
    constraint = constraint.strip()
    if constraint == "*":
        return [("*", "*")]

    parts = [p.strip() for p in constraint.split(",")]
    result = []
    for part in parts:
        if part == "*":
            result.append(("*", "*"))
            continue

        # Match operator and version
        m = re.match(r"^(==|!=|>=|<=|>|<|\^|~)(.+)$", part)
        if m:
            op, ver = m.group(1), m.group(2).strip()
            result.append((op, ver))
        else:
            # Bare version means exact match
            result.append(("==", part))

    return result


# ── Constraint Satisfaction ──────────────────────────────────────────────────


def _expand_caret(version_str):
    """Expand caret constraint to (lower_bound_inclusive, upper_bound_exclusive)."""
    m, n, p, pre, _ = _parse_version(version_str)
    if m != 0:
        upper = f"{m + 1}.0.0"
    elif n != 0:
        upper = f"0.{n + 1}.0"
    else:
        upper = f"0.0.{p + 1}"
    return version_str, upper


def _expand_tilde(version_str):
    """Expand tilde constraint to (lower_bound_inclusive, upper_bound_exclusive)."""
    m, n, p, pre, _ = _parse_version(version_str)
    upper = f"{m}.{n + 1}.0"
    return version_str, upper


def satisfies(version, constraint):
    """Check if a version satisfies a constraint string."""
    parsed = parse_constraint(constraint)
    ver_base = _version_base(version)

    for op, target in parsed:
        if op == "*":
            continue

        if op == "^":
            lower, upper = _expand_caret(target)
            if compare_versions(ver_base, lower) < 0:
                return False
            if compare_versions(ver_base, upper) >= 0:
                return False
            continue

        if op == "~":
            lower, upper = _expand_tilde(target)
            if compare_versions(ver_base, lower) < 0:
                return False
            if compare_versions(ver_base, upper) >= 0:
                return False
            continue

        target_base = _version_base(target)
        cmp = compare_versions(ver_base, target_base)

        if op == "==" and cmp != 0:
            return False
        elif op == "!=" and cmp == 0:
            return False
        elif op == ">=" and cmp < 0:
            return False
        elif op == "<=" and cmp > 0:
            return False
        elif op == ">" and cmp <= 0:
            return False
        elif op == "<" and cmp >= 0:
            return False

    return True


# ── Registry ─────────────────────────────────────────────────────────────────


def create_registry():
    """Create an empty package registry."""
    return {}


def register_package(registry, name, version, dependencies=None):
    """Register a package version with its dependencies.

    Returns updated registry.
    """
    if name not in registry:
        registry[name] = []

    entry = {
        "version": version,
        "dependencies": dependencies or {},
    }
    registry[name].append(entry)
    return registry


def find_best_version(registry, name, constraint):
    """Find the highest version satisfying constraint. Returns None if none."""
    if name not in registry:
        return None

    candidates = []
    for entry in registry[name]:
        ver = entry["version"]
        if satisfies(ver, constraint):
            candidates.append(ver)

    if not candidates:
        return None

    # Sort by version descending, pick latest
    candidates.sort(key=lambda v: _sort_key(v), reverse=True)
    return candidates[0]


def _sort_key(version_str):
    """Return a sort key for a version string."""
    m, n, p, pre, _ = _parse_version(version_str)
    # For sorting: release > pre-release
    # Encode pre-release as a comparable tuple
    if pre is None:
        # Release: higher than any pre-release
        pre_key = (1,)
    else:
        # Build a comparable key from pre-release identifiers
        # Numeric ids come before alpha ids in semver
        encoded = []
        for ident in pre:
            if isinstance(ident, int):
                # Numeric: sort before alpha, use (0, number, "")
                encoded.append((0, ident, ""))
            else:
                # Alpha: sort after numeric, use (1, 0, string)
                encoded.append((1, 0, ident))
        pre_key = (0, tuple(encoded))

    return (m, n, p, pre_key)


# ── Resolution ───────────────────────────────────────────────────────────────


def resolve(registry, requirements):
    """Resolve dependencies with backtracking.

    Returns {"status": "ok"|"conflict"|"not_found",
             "packages": dict[str, str],
             "install_order": list[str],
             "error": str|None}
    """
    if not requirements:
        return {
            "status": "ok",
            "packages": {},
            "install_order": [],
            "error": None,
        }

    # Build constraint map: package -> list of (constraint_str, required_by)
    # Use backtracking search

    result = _resolve_backtrack(registry, requirements)
    return result


def _get_all_versions_sorted(registry, name):
    """Get all versions for a package, sorted descending (latest first)."""
    if name not in registry:
        return []
    versions = [entry["version"] for entry in registry[name]]
    versions.sort(key=lambda v: _sort_key(v), reverse=True)
    return versions


def _get_deps_for_version(registry, name, version):
    """Get dependencies dict for a specific package version."""
    if name not in registry:
        return {}
    for entry in registry[name]:
        if _version_base(entry["version"]) == _version_base(version):
            return dict(entry.get("dependencies", {}))
    return {}


def _resolve_backtrack(registry, requirements):
    """Backtracking resolution algorithm.

    State: resolved = {name: version}, constraints = {name: [(constraint, required_by)]}
    We process packages one at a time, trying versions in descending order.
    If a conflict is found, we backtrack.
    """
    # Convert requirements to initial constraints
    initial_constraints = {}
    for name, constraint in requirements.items():
        optional = name.startswith("?")
        clean_name = name.lstrip("?")
        if clean_name not in initial_constraints:
            initial_constraints[clean_name] = []
        initial_constraints[clean_name].append((constraint, "root", optional))

    # Use recursive backtracking
    resolved = {}
    result = _backtrack(registry, initial_constraints, resolved, set(), [])

    if result is None:
        return {
            "status": "conflict",
            "packages": {},
            "install_order": [],
            "error": "Unable to resolve dependencies: conflicting constraints",
        }

    if isinstance(result, dict) and result.get("status") == "not_found":
        return result

    if isinstance(result, dict) and result.get("status") == "conflict":
        return result

    resolved = result
    # Build install order via topological sort
    install_order = _topological_sort(registry, resolved)

    return {
        "status": "ok",
        "packages": resolved,
        "install_order": install_order,
        "error": None,
    }


def _backtrack(registry, constraints, resolved, resolving_stack, path):
    """Recursive backtracking resolver.

    constraints: {name: [(constraint_str, required_by, optional)]}
    resolved: {name: version} — already locked versions
    resolving_stack: set of names currently being resolved (cycle detection)
    path: list of names in current resolution path

    Returns resolved dict on success, or None/error dict on failure.
    """
    # Find next unresolved package that has constraints
    unresolved = []
    for name in constraints:
        if name not in resolved:
            unresolved.append(name)

    if not unresolved:
        return dict(resolved)

    # Pick the next package to resolve
    name = unresolved[0]

    # Check for circular dependency
    if name in resolving_stack:
        return {
            "status": "conflict",
            "packages": {},
            "install_order": [],
            "error": f"Circular dependency detected: {' -> '.join(path + [name])}",
        }

    # Gather all constraints for this package
    pkg_constraints = constraints[name]

    # Check if all constraints are optional
    all_optional = all(c[2] for c in pkg_constraints)

    # Check if package exists in registry
    if name not in registry:
        if all_optional:
            # Skip optional package that doesn't exist
            new_constraints = dict(constraints)
            del new_constraints[name]
            return _backtrack(registry, new_constraints, resolved, resolving_stack, path)
        return {
            "status": "not_found",
            "packages": {},
            "install_order": [],
            "error": f"Package not found: {name}",
        }

    # Get candidate versions (sorted descending = latest first)
    all_versions = _get_all_versions_sorted(registry, name)

    # Filter by all constraints
    candidates = []
    for ver in all_versions:
        ok = True
        for constraint_str, req_by, optional in pkg_constraints:
            if not satisfies(ver, constraint_str):
                ok = False
                break
        if ok:
            candidates.append(ver)

    if not candidates:
        if all_optional:
            # Skip optional package with no compatible version
            new_constraints = dict(constraints)
            del new_constraints[name]
            return _backtrack(registry, new_constraints, resolved, resolving_stack, path)

        # Find conflicting info for error message
        constraint_descs = [f"{req_by} requires {name} {c}" for c, req_by, _ in pkg_constraints]
        return {
            "status": "conflict",
            "packages": {},
            "install_order": [],
            "error": f"No compatible version for {name}: {'; '.join(constraint_descs)}",
        }

    # Try each candidate version
    for ver in candidates:
        new_resolved = dict(resolved)
        new_resolved[name] = ver

        # Get this version's dependencies
        deps = _get_deps_for_version(registry, name, ver)

        # Add transitive dependencies as new constraints
        new_constraints = {}
        for k, v in constraints.items():
            new_constraints[k] = list(v)

        conflict = False
        for dep_name, dep_constraint in deps.items():
            optional = dep_name.startswith("?")
            clean_dep = dep_name.lstrip("?")

            # Check if already resolved with incompatible version
            if clean_dep in new_resolved:
                if not satisfies(new_resolved[clean_dep], dep_constraint):
                    if optional:
                        continue  # Skip optional dep that conflicts
                    conflict = True
                    break
                # Already resolved and compatible, no new constraint needed
                continue

            if clean_dep not in new_constraints:
                new_constraints[clean_dep] = []
            new_constraints[clean_dep].append((dep_constraint, name, optional))

        if conflict:
            continue

        # Recurse
        new_stack = resolving_stack | {name}
        result = _backtrack(registry, new_constraints, new_resolved, new_stack, path + [name])

        if result is not None and not (isinstance(result, dict) and result.get("status") in ("conflict", "not_found")):
            return result

    # All candidates exhausted
    if all_optional:
        new_constraints = dict(constraints)
        del new_constraints[name]
        return _backtrack(registry, new_constraints, resolved, resolving_stack, path)

    constraint_descs = [f"{req_by} requires {name} {c}" for c, req_by, _ in pkg_constraints]
    return {
        "status": "conflict",
        "packages": {},
        "install_order": [],
        "error": f"Unable to resolve {name}: {'; '.join(constraint_descs)}",
    }


def _topological_sort(registry, resolved):
    """Topological sort of resolved packages. Dependencies come before dependents."""
    # Build dependency graph from resolved packages
    graph = defaultdict(set)  # name -> set of dependency names
    for name, version in resolved.items():
        deps = _get_deps_for_version(registry, name, version)
        for dep_name in deps:
            clean_dep = dep_name.lstrip("?")
            if clean_dep in resolved:
                graph[name].add(clean_dep)

    # Kahn's algorithm
    in_degree = defaultdict(int)
    for name in resolved:
        if name not in in_degree:
            in_degree[name] = 0
    for name, deps in graph.items():
        for dep in deps:
            in_degree[name]  # ensure exists
        # Actually in_degree counts how many depend on this? No.
        # in_degree[name] = number of deps name has that are in resolved

    # Recompute: in_degree[x] = number of packages in graph that x depends on
    in_degree = {name: 0 for name in resolved}
    for name, deps in graph.items():
        in_degree[name] = len(deps)

    # Start with packages that have no dependencies
    queue = sorted([n for n in resolved if in_degree[n] == 0])
    order = []

    while queue:
        node = queue.pop(0)
        order.append(node)
        # For each package that depends on node, decrease its in_degree
        for name, deps in graph.items():
            if node in deps:
                in_degree[name] -= 1
                if in_degree[name] == 0:
                    # Insert in sorted position for deterministic ordering
                    queue.append(name)
                    queue.sort()

    return order
