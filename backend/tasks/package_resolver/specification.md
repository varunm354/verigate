<!--
Adapted from SpecBench (Apache-2.0) -- https://github.com/WecoAI/SpecBench
Source: benchmarks/spec_bench/tasks/package_resolver/prompt.md
Commit: 08607352adc8abd78be2193dd9f725f1f032b8f0 -- arXiv:2605.21384
See third_party/specbench/README.md for full attribution details.
Content below is unchanged from upstream except for this notice.
-->

# Package Dependency Resolver — Semantic Versioning

Implement a package dependency resolver from scratch in Python. The module
`resolver.py` must provide functions for managing a package registry, parsing
semantic version constraints, and resolving a set of requirements into a
conflict-free installation plan.

## Requirements

Implement these functions in `resolver.py`:

```python
def create_registry() -> dict:
    """Create an empty package registry."""

def register_package(registry: dict, name: str, version: str,
                     dependencies: dict[str, str] | None = None) -> dict:
    """Register a package version with its dependencies.
    dependencies: {"pkg_name": "version_constraint"} e.g. {"requests": ">=2.0.0,<3.0.0"}
    Returns updated registry."""

def resolve(registry: dict, requirements: dict[str, str]) -> dict:
    """Resolve dependencies. Returns {"status": "ok"|"conflict"|"not_found",
    "packages": dict[str, str] (name->resolved version), "install_order": list[str],
    "error": str|None}"""

def parse_constraint(constraint: str) -> list[tuple[str, str]]:
    """Parse a version constraint string into operator-version pairs.
    E.g., ">=1.0.0,<2.0.0" -> [(">=", "1.0.0"), ("<", "2.0.0")]
    Supported: ==, !=, >=, <=, >, <, ^(caret), ~(tilde), *(wildcard)"""

def compare_versions(v1: str, v2: str) -> int:
    """Compare two semver strings. Returns -1, 0, or 1."""

def satisfies(version: str, constraint: str) -> bool:
    """Check if a version satisfies a constraint string."""

def find_best_version(registry: dict, name: str, constraint: str) -> str | None:
    """Find the highest version satisfying constraint. Returns None if none."""
```

## Semantic Versioning

Version format: `major.minor.patch` (e.g., `1.2.3`).

- **Major**: breaking changes.
- **Minor**: backward-compatible new features.
- **Patch**: backward-compatible bug fixes.
- **Pre-release**: appended with `-` and dot-separated identifiers, e.g.,
  `1.0.0-alpha`, `1.0.0-beta.1`, `1.0.0-rc.2`. Pre-release versions have
  *lower* precedence than the associated release version (`1.0.0-alpha < 1.0.0`).
- **Build metadata**: appended with `+`, e.g., `1.0.0+build.123`. Build
  metadata MUST be ignored when comparing versions.

### Pre-release Ordering

When comparing pre-release identifiers left to right:
1. Numeric identifiers are compared as integers.
2. Alphanumeric identifiers are compared lexically (ASCII sort).
3. Numeric identifiers always have lower precedence than alphanumeric.
4. A shorter set of identifiers has lower precedence if all preceding
   identifiers are equal (e.g., `1.0.0-alpha < 1.0.0-alpha.1`).

## Constraint Operators

| Operator | Meaning | Example |
|----------|---------|---------|
| `==1.0.0` | Exact match | Only `1.0.0` |
| `!=1.0.0` | Not equal | Any version except `1.0.0` |
| `>=1.0.0` | Greater or equal | `1.0.0`, `1.1.0`, `2.0.0`, … |
| `<=1.0.0` | Less or equal | `0.9.0`, `1.0.0` |
| `>1.0.0` | Strictly greater | `1.0.1`, `1.1.0`, `2.0.0`, … |
| `<1.0.0` | Strictly less | `0.9.9`, `0.0.1`, … |
| `^1.2.3` | Caret (compatible) | `>=1.2.3, <2.0.0` |
| `^0.2.3` | Caret for 0.x | `>=0.2.3, <0.3.0` |
| `^0.0.3` | Caret for 0.0.x | `>=0.0.3, <0.0.4` |
| `~1.2.3` | Tilde (patch-level) | `>=1.2.3, <1.3.0` |
| `*` | Wildcard | Any version |

Multiple constraints are joined by comma: `">=1.0.0,<2.0.0"` — the version
must satisfy ALL constraints (conjunction).

## Resolution Algorithm

1. **Prefer latest**: among compatible versions, always pick the highest.
2. **Transitive dependencies**: if A depends on B and B depends on C, resolving
   A must also resolve B and C.
3. **Conflict detection**: when two packages require incompatible version ranges
   for a common dependency (diamond dependency), report `"status": "conflict"`
   with an error message identifying the conflicting packages.
4. **Backtracking**: if the first version choice for a package leads to a
   downstream conflict, the resolver must try alternative versions.
5. **Install order**: return a topological ordering — every package appears
   *after* all of its dependencies in `install_order`.
6. **Optional dependencies**: dependency names prefixed with `?` (e.g.,
   `{"?extras": ">=1.0.0"}`) are optional. If the package exists in the
   registry and a compatible version is found, include it. If not, silently
   skip it (no error).
7. **Circular dependencies**: if a dependency cycle is detected, report
   `"status": "conflict"` with an appropriate error message.

## Constraints

- Do NOT use `pip`, `pkg_resources`, `packaging`, `semver`, or any package
  management library.
- Do NOT use `subprocess` to call pip, npm, or any external tool.
- You MAY use `re`, `functools`, `collections` from the standard library.
- Raise `ValueError` for invalid version strings or constraint syntax.

## Evaluation

Your implementation will be tested against a public test suite. Optimize to pass
as many tests as possible.
