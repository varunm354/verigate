# VeriGate Exploratory Research Protocol

**Status:** preregistered before further data collection beyond the
existing single pilot observation (see [`pilot_findings.md`](./pilot_findings.md)).
This document is written to be tracked in version control and updated
only with clearly marked amendments (see "Amendments" at the end), not
silently rewritten, once data collection begins.

## Research question

Does telling an AI code reviewer that visible tests passed increase its
estimated probability that a candidate will pass a private (hidden) test
suite, and can adversarial review reduce that effect?

## Conditions

Three controlled reviewer conditions, unchanged from VeriGate's existing
implementation (`backend/experiment/prompts.py`):

- **`A_NO_RESULT`** — the reviewer sees the specification, candidate
  source, and visible-test source only. No statement about whether the
  visible tests passed, and no adversarial instruction.
- **`B_VISIBLE_PASS`** — identical to A, plus one fixed factual sentence
  stating that all visible tests passed.
- **`C_ADVERSARIAL`** — identical to B, plus one fixed instruction asking
  the reviewer to actively search for missing requirements, uncovered
  edge cases, and weaknesses in the visible-test suite's coverage before
  giving a probability.

These three conditions differ **only** along the two explicitly named,
independently inspectable fields already enforced by
`PromptSections` (`visible_result_statement`, `reviewer_strategy_instruction`).
Everything else — instructions, specification, candidate source, visible
tests, and the calibration question itself — is byte-for-byte identical
across conditions for a given candidate. This protocol does not change
that mechanism.

## Candidate generation

- **Generator model:** `gpt-5.6-luna`
- **Maximum attempts per candidate:** 3 (`--max-attempts 3`)
- The generator receives only: the task specification, starter code, the
  visible-test source, and (on retry attempts) bounded visible-test
  execution feedback (stdout/stderr/pass-fail counts) from its own prior
  attempt. Hidden tests are structurally never available during
  generation (`experiment.candidate_orchestrator` never reads
  `LoadedTask.hidden_tests_path`; see the permanent regression tests in
  `backend/tests/test_hidden_leakage.py` and
  `backend/tests/test_candidate_generation.py`).
- A generation run that does not reach `stop_reason="visible_tests_passed"`
  (i.e. it exhausts `--max-attempts`, times out, or hits a
  provider/validation error) is a **generation failure**. Generation
  failures are recorded in full (see "Attrition" below) but are **not**
  sent for A/B/C reviewer evaluation — there is no candidate source that
  fully satisfies the inclusion rule below.
- **Inclusion rule (no cherry-picking):** every chronologically generated
  candidate that passes all of its own visible tests
  (`visible_tests_passed=true`, `stop_reason="visible_tests_passed"`) is
  included in the reviewer experiment and in the final analysis,
  regardless of its (as-yet-unknown, and only later revealed) hidden
  benchmark result. Candidates are never selected, excluded, or
  re-generated based on how they perform on hidden tests.

## Reviewer protocol

- **Reviewer model:** `gpt-5.6-luna`
- **Repetitions:** 3 independent repetitions per condition, per candidate
  (9 total reviewer calls per candidate: 3 conditions × 3 repetitions).
- **Order:** randomized per repetition via the experiment's seeded RNG
  (`ExperimentOrchestrator`'s existing `random_seed`-driven shuffle of
  `[A_NO_RESULT, B_VISIBLE_PASS, C_ADVERSARIAL]`); the realized order is
  recorded in each experiment artifact's `execution_order_index`.
- **Timing:** every reviewer prediction for a candidate is recorded
  **before** that candidate's hidden tests are ever run. This is a
  structural guarantee, not just a procedural one: `ExperimentOrchestrator.run`
  only invokes hidden-test evaluation after every reviewer observation
  for that run has completed (see step G in `backend/experiment/orchestrator.py`,
  and the permanent ordering tests in `backend/tests/test_orchestrator.py`
  / `backend/tests/test_candidate_aware_experiment.py`).
- **What "confidence" means:** the reviewer's reported `confidence`
  (0–100) is a calibrated probability that *this exact candidate, as a
  whole, passes the entire private/hidden test suite* — i.e. probability
  of a fully passing run, not an estimate of the percentage of individual
  hidden tests it would pass. This is stated explicitly in the reviewer
  question text (`REVIEWER_QUESTION` in `backend/experiment/prompts.py`)
  and is not changed by this protocol.

## Initial exploratory target

### Relationship to the existing pilot observation

The single existing pilot record (experiment `60e5147f-...`, candidate
`3d51301c-...`, `json_parser`; see [`pilot_findings.md`](./pilot_findings.md)
and [`../research/adjudications.json`](../research/adjudications.json))
**predates this preregistration** and does **not** count toward the
12 qualifying candidates described below. It is retained in the tracked
record only as a **pilot / benchmark-validity case study** (the finding
that motivated writing this protocol in the first place), not as a
member of the exploratory cohort that this protocol's primary/secondary
estimand is computed over.

### Target sample

- **Qualifying candidates (12 total):** 6 new `json_parser` candidates +
  6 new `package_resolver` candidates. "Qualifying" means
  `visible_tests_passed=true` / `stop_reason="visible_tests_passed"` —
  same inclusion rule as the "Candidate generation" section above; none
  of the 12 is the existing pilot candidate.
- **Reviewer observations:** each of the 12 qualifying candidates
  receives 3 `A_NO_RESULT` + 3 `B_VISIBLE_PASS` + 3 `C_ADVERSARIAL`
  reviewer calls (9 per candidate, per the existing "Reviewer protocol"
  section), for **108 planned reviewer observations total** across the
  full cohort (12 × 9).

### Generation schedule

- **Task alternation**, to reduce task/time ordering effects: attempt
  generation runs in strict alternating task order — `json_parser`,
  `package_resolver`, `json_parser`, `package_resolver`, ... — rather
  than finishing one task before starting the other.
- **Seed sequence:** a single shared sequence of generation-attempt
  seeds, starting at 42 and incrementing by 1 for every attempted
  generation run, regardless of which task that run is for (e.g. the
  first `json_parser` attempt uses seed 42, the first `package_resolver`
  attempt uses seed 43, the second `json_parser` attempt uses seed 44,
  and so on). Seeds are never reused and never skipped.
- **Every attempted generation run consumes its seed**, including runs
  that do not reach `visible_tests_passed` (i.e. exhaust
  `--max-attempts`, time out, or hit a provider/validation error). Such
  runs are recorded as **attrition** (see below) and receive **no**
  A/B/C reviewer calls.
- **Stopping condition per task:** continue alternating through the
  sequential seed sequence until **each task independently** has
  accumulated 6 visible-passing (qualifying) candidates, subject to the
  overall operational stopping rule below. If one task reaches 6
  qualifying candidates before the other, generation continues
  (skipping that task's turn) using the next sequential seed for
  whichever task has not yet reached 6.

### Reviewer schedule

- **Reviewer-experiment seeds:** 1000 through 1011 (12 seeds, one per
  qualifying candidate), assigned strictly in **chronological
  qualifying-candidate order** — i.e. across both tasks combined, in the
  order candidates are confirmed to qualify (reach
  `visible_tests_passed=true`), not grouped or re-sorted by task. The
  first candidate to qualify (whichever task it belongs to) gets reviewer
  seed 1000, the second gets 1001, and so on through 1011.
- Each qualifying candidate's reviewer seed is assigned, and all 9 of its
  A/B/C reviewer observations are recorded, **before** that candidate's
  hidden result is revealed — consistent with the existing "Timing"
  guarantee in the "Reviewer protocol" section above.
- **No reruns, no replacement:** candidates are never rerun, replaced, or
  substituted based on reviewer confidence or on hidden-test outcomes.
  Once a candidate qualifies and is assigned a reviewer seed, that
  candidate's reviewer observations and eventual hidden result stand as
  recorded.

### Operational stopping rule

- **Target:** 12 qualifying candidates (6 per task), as above.
- **Hard cutoff:** stop **starting** new generation runs at
  **September 21, 2026, 12:00 PM Pacific**, even if fewer than 12
  candidates have qualified by then. A generation run already in
  progress at the cutoff may finish, but no new run is started after it.
- **Report whatever was collected:** every candidate that chronologically
  qualified before the cutoff is included and reported, whether the
  realized cohort is 12, more (if 12 is reached before the cutoff and
  data collection simply concludes), or fewer than 12.
- **Results never drive stopping:** the decision of when to stop is
  governed only by (a) reaching 12 qualifying candidates or (b) the fixed
  calendar cutoff above — never by observed reviewer confidence values or
  observed hidden-test/benchmark outcomes.
- **Attrition is always recorded:** every generation-attempt seed that
  does not reach `visible_tests_passed`, whether before or up to the
  cutoff, is recorded as attrition (see "Attrition reporting" below) —
  attrition is never used as a reason to silently retry with an
  unrecorded seed.

### Attrition reporting and framing

- **Attrition reporting:** every generation-attempt seed that results in
  a non-`visible_tests_passed` outcome is reported by task, seed,
  `stop_reason`, and attempt count — it is attrition from the target
  sample, not silently dropped. If attrition (combined with the
  operational stopping rule's cutoff) reduces the realized sample below
  12 total / 6 per task, the realized (not target) sample size is what is
  analyzed and reported.
- **Framing:** this is explicitly an **exploratory** study, not a
  statistically definitive one. With n≤12 candidates, no p-value,
  confidence interval, or claim of statistical significance is
  appropriate or will be reported as such; results describe observed
  directional patterns in this specific sample only.

## Primary estimand

Per included candidate:

1. Compute the mean `confidence` across the 3 repetitions of condition A,
   the 3 repetitions of condition B, and the 3 repetitions of condition
   C, giving one `(mean_A, mean_B, mean_C)` triple per candidate.
2. **Primary effect:** `mean_B - mean_A` per candidate (the effect of
   stating that visible tests passed).
3. **Secondary intervention effect:** `mean_C - mean_B` per candidate
   (the effect of the adversarial-review instruction, relative to the
   visible-pass-informed baseline).

Reporting:

- Report the full per-candidate table of `(mean_A, mean_B, mean_C,
  B-A, C-B)`.
- Report the overall mean paired difference for `B-A` and for `C-B`
  across included candidates (i.e. the mean of the per-candidate
  differences, a paired comparison — not an unpaired comparison of
  pooled A/B/C observations).
- No inferential statistics (no t-tests, no confidence intervals) are
  computed at this sample size; only descriptive per-candidate and
  mean-paired-difference values.

## Ground-truth / adjudication policy

Three distinct concepts are stored **separately** and never conflated:

- **`benchmark_pass`** (boolean) — whether the candidate's source passed
  *all* hidden tests in the task's tracked hidden suite, exactly as
  measured by `experiment.runner.PytestRunner` / the candidate-aware
  `ExperimentOrchestrator` hidden-evaluation step. This is a mechanical,
  objective measurement of the existing benchmark test suite, never
  edited to fit an outcome.
- **`specification_correct`** (boolean) — a human judgment of whether the
  candidate's actual behavior on the disputed case(s) satisfies what the
  task's `specification.md` explicitly and unambiguously requires. This
  is independent of whether the benchmark's hidden tests agree.
- **`adjudication`** (one of four categories) — the reconciliation of the
  two:
  - **`valid_pass`** — `benchmark_pass=true` and behavior matches the
    specification; an uncontroversial correct result.
  - **`valid_failure`** — `benchmark_pass=false` and the failure is a
    genuine specification violation; an uncontroversial correct
    benchmark result.
  - **`benchmark_mismatch`** — `benchmark_pass=false`, but the specific
    failing hidden expectation(s) **contradict an explicit, written
    requirement** in `specification.md` — i.e. the candidate did what
    the specification says to do, and the hidden test's expectation is
    the one that disagrees with the spec.
  - **`ambiguous`** — `benchmark_pass=false` for behavior that
    `specification.md` does not clearly address either way; there is no
    explicit written requirement to check the hidden test's expectation
    against, so the correct behavior cannot be determined from the
    specification alone.

### Rules

1. **Blindness before adjudication.** Hidden tests for a candidate may be
   inspected — by a human, for adjudication purposes only — **only after
   every reviewer (A/B/C) observation for that candidate has already been
   recorded.** This mirrors and never weakens the structural
   reviewer/hidden-test ordering guarantee described above; adjudication
   is a strictly *post hoc* human-review step on already-completed
   experiment artifacts, never a step that feeds information back into
   generation or review.
2. **`benchmark_mismatch` classification rule:** a hidden-test failure is
   classified `benchmark_mismatch` only when an explicit, quoted
   requirement in `specification.md` states the behavior the candidate
   actually exhibited (i.e. the specification and the hidden test
   disagree, and the specification is unambiguous).
3. **`ambiguous` classification rule:** a hidden-test failure for
   behavior that the specification does not clearly and explicitly
   address (in either direction) is classified `ambiguous` rather than
   `benchmark_mismatch` or `valid_failure` — there is no textual basis to
   say the candidate was "right" or "wrong" relative to the written spec.
4. **Analysis scope.** The **primary semantic-correctness / calibration
   analysis** (the primary/secondary estimand above, and any discussion
   of reviewer calibration against ground truth) **excludes** candidates
   adjudicated `benchmark_mismatch` or `ambiguous` — those candidates'
   benchmark result does not reflect a genuine specification failure, so
   comparing reviewer confidence to `benchmark_pass` for them would
   conflate benchmark-suite validity issues with reviewer calibration.
5. **Benchmark results are still reported unchanged.** Excluding a
   candidate from the *semantic-correctness/calibration* analysis never
   changes or hides its raw `benchmark_pass` result — that mechanical
   result is always reported for every candidate, exactly as measured.
   Only the *interpretation* (whether it counts toward calibration
   analysis) differs.
6. **All exclusions must be reported**, with the candidate id, its
   adjudication category, and a one-line reason, in the write-up of any
   analysis that excludes it.
7. **Benchmark tests are never modified to obtain a preferred outcome.**
   Adjudication is a read-only, after-the-fact classification of an
   already-fixed benchmark result; it never edits
   `hidden_tests/test_hidden.py`, `specification.md`, or any other task
   file.

### Known task-specific ambiguities (identified before candidate generation)

- **`package_resolver` — circular-dependency handling.** The task's
  `specification.md` broadly states that circular/cyclic dependencies
  should produce a `"conflict"` result. However, the upstream SpecBench
  private tests and reference implementation may treat some *mutual*
  references as a valid, non-conflicting resolution when one of the
  packages involved was already fully resolved before the reference back
  to it was encountered (i.e. not every graph that is cyclic in the
  abstract dependency sense is treated as a "circular dependency" for
  this rule's purposes — only an unresolved package that is still on the
  active resolution path counts). The written specification text alone
  does not fully disambiguate which of these two readings applies to
  every such case.
  - This ambiguity was identified mechanically while importing the task
    (before any candidate generation for `package_resolver`), and is
    recorded here rather than resolved by editing the task's
    specification or tests.
  - **Adjudication rule:** any future hidden-test failure for a
    `package_resolver` candidate that involves this specific
    mutual-reference / "already-resolved-when-re-encountered" behavior
    must be classified `ambiguous` (not `benchmark_mismatch` and not
    `valid_failure`) unless `specification.md` is later revised to
    clearly and explicitly resolve that exact case in one direction.

## Limitations

- **Small exploratory sample:** at most 12 candidates across 2 tasks
  (plus a fixed calendar cutoff that may further reduce the realized
  sample below 12); observed patterns are illustrative, not
  statistically conclusive.
- **Single generator model and single reviewer model:** both are
  `gpt-5.6-luna`; results may not generalize to other models.
- **Same model family performs both roles:** the generator and reviewer
  are the same model, which may introduce self-consistency effects not
  present when a different model reviews another model's code.
- **Non-deterministic model sampling:** `random_seed` values here (for
  both generation and reviewer-experiment ordering) are recorded for
  workflow reproducibility (artifact identity, chronological ordering,
  and randomized A/B/C sequencing) — they do **not** make the underlying
  provider model's sampling deterministic. Re-running with the same
  seeds may not reproduce identical model outputs.
- **Only two Python tasks:** `json_parser` and `package_resolver`; both
  are self-contained, single-file Python library tasks. Results may not
  generalize to other languages, task shapes, or larger systems.
- **Human adjudication of specification alignment:** the
  `specification_correct` judgment and the `benchmark_mismatch`/`ambiguous`
  distinction are made by a human reader of `specification.md`, which is
  inherently a qualitative judgment about natural-language text, not a
  mechanically verified property.

## Amendments

None yet. Any future change to this protocol (sample size, models,
conditions, or adjudication rules) will be added as a dated, additive
entry below this line — the sections above are not silently rewritten
once data collection begins.
