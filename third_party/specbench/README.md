# Third-party attribution: SpecBench

VeriGate's `backend/tasks/json_parser/` task is adapted from **SpecBench**,
by Weco AI.

- **Repository**: https://github.com/WecoAI/SpecBench
- **Commit**: `08607352adc8abd78be2193dd9f725f1f032b8f0`
- **License**: Apache License 2.0 (see [`LICENSE`](./LICENSE) in this
  directory, copied verbatim from the upstream repository at the commit
  above; copyright 2025 Weco AI)
- **Paper**: Zhao, B., Srikanth, D., Wu, Y., Jiang, Z. *SpecBench: Measuring
  Reward Hacking in Long-Horizon Coding Agents*. arXiv:2605.21384 (2026).

This directory exists solely to satisfy Apache-2.0 §4 (a copy of the
license, retained/visible alongside the redistributed material). It is
tracked by VeriGate's git repository, unlike the read-only SpecBench
checkout at `backend/data/specbench/` (which is gitignored and never
committed -- see `backend/data/specbench`'s own `.git` history for the
authoritative upstream source).

## What was copied/adapted for `backend/tasks/json_parser/`

All four files below originate from SpecBench's `json_parser` task at
`benchmarks/spec_bench/tasks/json_parser/` (commit `0860735` above). Only
the **VeriGate task shape** required changes -- no behavioral logic,
assertions, or requirements were altered.

| VeriGate file | SpecBench source | Change made |
|---|---|---|
| `backend/tasks/json_parser/specification.md` | `prompt.md` | Added a leading HTML-comment attribution notice. Requirements text below it is byte-for-byte unchanged. |
| `backend/tasks/json_parser/json_parser.py` | `reference/json_parser.py` | Added a module docstring (the original file had none) noting provenance and that this is SpecBench's reference (spec-passing) solution, used here as VeriGate's candidate for this milestone. All code is otherwise byte-for-byte unchanged. |
| `backend/tasks/json_parser/visible_tests/test_visible.py` | `tests/public/test_public.py` | Replaced the module docstring with one that adds provenance/attribution (the original docstring is preserved verbatim inside it, under "Original SpecBench docstring"), and added a `sys.path` bootstrap (3 lines) so the file can `import json_parser` from VeriGate's task-root layout instead of SpecBench's `PYTHONPATH`-based workspace layout. Every test function, name, and assertion is byte-for-byte unchanged. |
| `backend/tasks/json_parser/hidden_tests/test_hidden.py` | `tests/private/test_private.py` | Same treatment as `test_visible.py`: attribution-augmented docstring (original preserved verbatim inside it) + the same 3-line `sys.path` bootstrap, plus a `VERIGATE_HIDDEN_TEST_SENTINEL_json_parser_...` marker (VeriGate convention, see `backend/tasks/expression_evaluator/hidden_tests/test_hidden.py`) used by VeriGate's automated hidden-content-leakage regression test. Every test function, name, and assertion is byte-for-byte unchanged. |

**Deliberately not imported for this milestone**: SpecBench's
`tests/id_private/` and `tests/gradient/` suites for `json_parser`, and
SpecBench's `reference/oracle.py`. Only `tests/public/` (-> visible) and
`tests/private/` (-> hidden) were brought in, per VeriGate's existing
two-suite (visible/hidden) task model.

## Filename/module renames

SpecBench's own harness places the candidate module in an isolated
per-run workspace and sets `PYTHONPATH` to that workspace, so its test
files can do a plain `from json_parser import parse, serialize` with no
path setup. VeriGate's harness (`experiment.runner.PytestRunner`) does not
create such a workspace or set `PYTHONPATH`; instead, each VeriGate task's
test files insert their own task-root directory onto `sys.path` (see
`backend/tasks/expression_evaluator/visible_tests/test_visible.py` for the
existing convention this follows). Concretely: the candidate file keeps
the name `json_parser.py` (not renamed to `candidate.py`) so that the
upstream `from json_parser import parse, serialize` import statement in
both test files could be reused completely unmodified; `manifest.json`'s
`paths.candidate` field points at `json_parser.py` accordingly.

## License compliance notes (Apache License 2.0, §4)

- **(a)** A verbatim copy of the Apache-2.0 license text is kept at
  [`./LICENSE`](./LICENSE).
- **(b)** Files modified from the upstream original carry an explicit
  notice of the change (see the "change made" column above and each
  file's own header/docstring).
- **(c)** No per-file copyright headers existed in the upstream files
  copied here, so none were removed; this README and the file headers
  serve as the retained top-level attribution.
- **(d)** No `NOTICE` file exists in the upstream SpecBench repository at
  this commit, so there is nothing additional to propagate under that
  clause.
