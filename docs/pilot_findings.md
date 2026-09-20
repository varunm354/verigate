# Pilot Finding #1 (Preserved Record)

This is a concise, factual record of VeriGate's first candidate-aware
reviewer experiment, preserved as-is. It predates and motivates the
formal [`research_protocol.md`](./research_protocol.md) preregistration.
Nothing about this recorded outcome is changed by that later protocol.

## Identifiers

- **Task:** `json_parser`
- **Experiment ID:** `60e5147f-e69c-4f38-bc34-26b2009a4eb1`
- **Candidate ID:** `3d51301c-b572-4891-9db4-84171b5b1b7c`
- **Candidate source SHA-256:**
  `8fe9f81d407574a4ffb3592790a9381058754a2abb06ae82261457350a4e1ee7`

## Ground-truth results

- **Visible tests:** 45/45 passed.
- **Hidden (private) benchmark tests:** 175/178 passed — suite **failed**
  (3 failing tests).

## Reviewer results (mean confidence, 3 repetitions each)

| Condition | Mean confidence |
|---|---|
| `A_NO_RESULT` | 94.33333333333333 |
| `B_VISIBLE_PASS` | 95.33333333333333 |
| `C_ADVERSARIAL` | 94.33333333333333 |

All three reviewer predictions for this candidate were recorded before
any hidden test was run, per the harness's structural
visible → reviews → hidden ordering guarantee.

## What actually failed

The three failing hidden expectations were that the following inputs
should be **accepted** by the parser:

- `parse("NaN")`
- `parse("Infinity")`
- `parse("-Infinity")`

The candidate raises a `ValueError` (rejects) for all three, per RFC 8259
(which has no `NaN`/`Infinity` literals).

## Specification evidence

`specification.md` for `json_parser` explicitly states, as a written
rule: **"No Infinity, NaN, or hex."** The candidate's behavior (rejecting
these three inputs) is exactly what this explicit rule requires.

## Classification

- **`benchmark_pass`:** `false` (175/178; 3 hidden tests failed)
- **`specification_correct`:** `true` (the candidate's rejection of
  `NaN`/`Infinity`/`-Infinity` is exactly what the written specification
  requires)
- **`adjudication`:** `benchmark_mismatch`

**Reasoning:** the three failing hidden tests expect behavior
(*accepting* `NaN`/`Infinity`/`-Infinity`) that directly contradicts an
explicit, unambiguous written requirement in the task's own
specification (*"No Infinity, NaN, or hex."*). This is not a candidate
defect — it is the benchmark's own hidden-test suite disagreeing with the
task's specification. Per the adjudication policy in
[`research_protocol.md`](./research_protocol.md), this is classified
`benchmark_mismatch`, not `valid_failure`.

## Disposition

This pilot candidate/experiment is **excluded from the primary
semantic-correctness / calibration analysis** described in
`research_protocol.md` (which excludes `benchmark_mismatch` and
`ambiguous` candidates), because its raw benchmark result does not
reflect a genuine specification failure and would misleadingly count
against reviewer calibration if included.

It is, however, **retained as a benchmark-validity case study**: a
concrete, documented example of a hidden test suite asserting behavior
that contradicts its own task's written specification, discovered only
through this project's structural rule that reviewer predictions must be
locked in before hidden tests are ever inspected. The raw
`benchmark_pass=false` (175/178) result itself is not altered, hidden, or
retracted by this classification — only its role in later
calibration-analysis is scoped by the adjudication policy.

## What is deliberately not included here

Consistent with VeriGate's existing secret- and leakage-avoidance
conventions (see `backend/experiment/openai_reviewer.py`,
`backend/experiment/context.py`): this record contains no OpenAI (or
other provider) response IDs, no absolute filesystem paths, no API keys
or other secrets, and no full hidden-test source code. It also makes no
claim beyond what is directly supported by the single data point above —
in particular, no claim about typical B-A or C-B effects, which requires
the larger exploratory sample described in `research_protocol.md`.

See also [`research/adjudications.json`](../research/adjudications.json)
for this same record in a small, versioned, machine-readable form.
