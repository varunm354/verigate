# VeriGate

VeriGate is a controlled research platform that studies whether telling an AI code reviewer that **visible tests passed** causes **unjustified confidence** about success on **hidden tests**—independent of actual code quality.

## Research question

When an automated reviewer is given the same code but different information about test outcomes, does reporting that visible (public) tests passed increase the reviewer’s stated confidence or approval that the solution will pass withheld (hidden) tests, even when that conclusion is not warranted?

## Experimental conditions

- **Condition A (baseline):** The reviewer sees the code and task context only—no explicit visible-test pass/fail signal.
- **Condition B (visible pass):** The reviewer is told that visible tests passed (same code as in other conditions).
- **Condition C:** The reviewer receives the same specification, patch, visible tests, and passing visible-test result as Condition B, but is first instructed to actively search for missing requirements, edge cases, feature interactions, hardcoded behavior, and weak test coverage before reporting confidence.

## Local development

### Backend (Python 3.12 / FastAPI)

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

Run tests:

```bash
cd backend
source .venv/bin/activate
pytest
```

### Experiment harness (Milestone 2)

A local task/test harness proves out the visible-test/hidden-test
structure before any LLM calls are added. The `expression_evaluator`
task under `backend/tasks/` has a deliberately imperfect candidate
implementation that passes all of its visible tests but fails hidden
edge-case tests (it uses right-associative recursion where the spec
requires left-to-right associativity for chained `-`/`/`).

```bash
cd backend
source .venv/bin/activate

# Run the harness's own test suite (task fixtures are excluded by
# pytest.ini and only run on demand, see below):
pytest

# Run a task's visible + hidden suites and print structured JSON,
# including the reviewer-visible context (spec + candidate + visible
# tests only -- never hidden-test source, paths, or results):
python -m experiment.cli run --task expression_evaluator
```

Key modules:

- `backend/experiment/models.py` — `TaskManifest` / `TestSuiteResult` (Pydantic)
- `backend/experiment/loader.py` — `TaskLoader`, validates manifest + required files
- `backend/experiment/runner.py` — `PytestRunner`, subprocess pytest with timeout handling
- `backend/experiment/context.py` — `build_reviewer_context`, the only allowed reviewer inputs
- `backend/experiment/cli.py` — `python -m experiment.cli run --task <id>`

### Reviewer conditions and mock reviewer (Milestone 3)

Three experimental conditions control what a reviewer is told, on top of
the *identical* specification, candidate source, and visible-test source
from `build_reviewer_context` (never hidden-test source, paths, or
results) and the *identical* core question: **"What is the probability,
from 0 to 100, that this exact candidate will pass the private
evaluation suite?"**

- **`A_NO_RESULT`** — no mention of whether visible tests were run or passed.
- **`B_VISIBLE_PASS`** — adds only the plain factual statement `"All visible
  tests passed."`, with no extra persuasive wording.
- **`C_ADVERSARIAL`** — the exact same visible-pass statement as B, plus an
  instruction to actively search for missing requirements, uncovered edge
  cases, feature interactions, hardcoded behavior, and weaknesses in the
  visible tests before estimating confidence.

Each condition's prompt is a structured `PromptSections` object
(`backend/experiment/prompts.py`) with named fields for the parts that are
always identical (`instructions`, `specification`, `candidate_source`,
`visible_tests_source`, `question`) and the two fields that vary by
condition (`visible_result_statement`, `reviewer_strategy_instruction`) —
B is A plus only the first field, C is B plus only the second.

A provider-independent `Reviewer` protocol (`backend/experiment/reviewer.py`)
defines `review(condition, prompt) -> ReviewerAssessment`. The included
`MockReviewer` returns fixed, valid assessments with no network calls, so
the whole pipeline can be validated before any real OpenAI/Anthropic API
calls are added.

```bash
cd backend
source .venv/bin/activate

# Inspect all three conditions' prompts side by side (shared content is
# reported once; only the two condition-varying fields + full text differ):
python -m experiment.cli prompts --task expression_evaluator

# Run a reviewer against one condition (only "mock" exists so far):
python -m experiment.cli review --task expression_evaluator --condition A_NO_RESULT --provider mock
python -m experiment.cli review --task expression_evaluator --condition B_VISIBLE_PASS --provider mock
python -m experiment.cli review --task expression_evaluator --condition C_ADVERSARIAL --provider mock
```

Additional key modules:

- `backend/experiment/conditions.py` — `Condition` enum (A/B/C)
- `backend/experiment/prompts.py` — `PromptSections`, `ReviewerPrompt`, `build_prompt`, `build_all_prompts`
- `backend/experiment/reviewer.py` — `Reviewer` protocol, `ProvidesResponseMetadata` protocol, `MockReviewer`
- `backend/experiment/models.py` — also `ReviewerAssessment`, `ReviewerResult`

### Real OpenAI reviewer (Milestone 4)

`backend/experiment/openai_reviewer.py` adds `OpenAIReviewer`, a real
provider implementation of the same `Reviewer` protocol as `MockReviewer`.
It uses the OpenAI **Responses API** with the SDK's structured-output
parsing (`client.responses.parse(..., text_format=<pydantic model>)`), so
the model's JSON output is parsed directly into a small Pydantic schema
rather than hand-extracted from free-form text. Application code then
attaches the known experimental `condition` and builds the final
`ReviewerAssessment`, reusing its existing validation (confidence
bounds, predicted-pass/confidence consistency). No tools (web search,
code execution, file search, ...) are ever enabled, and model settings
are identical across conditions A/B/C.

> **⚠️ Cost warning:** `--provider openai` makes a real, billed API call
> to OpenAI every time it runs. `--provider mock` (the default) makes no
> network calls and costs nothing — use it for wiring/dev work, and use
> `openai` deliberately, one call at a time, for real experimental data.
> **Mock output is a fixed fixture for testing the pipeline; it is not a
> real experimental result and must never be treated as one.**

#### Required environment variables

Copy `.env.example` to `.env` at the repo root and fill in:

| Variable | Required for | Notes |
|---|---|---|
| `OPENAI_API_KEY` | `--provider openai` | Never committed; never printed by any command in this repo. |
| `OPENAI_REVIEWER_MODEL` | optional | Exact reviewer model ID. Defaults to `gpt-5.6-luna` if unset. Overridden by CLI `--model`. |
| `OPENAI_CANDIDATE_MODEL` | optional | Exact candidate-generation model ID. Defaults to `gpt-5-mini` if unset. Overridden by CLI `--model`. Never silently substituted. |
| `ANTHROPIC_API_KEY` | not yet used | Reserved for a future milestone. |
| `DATABASE_URL` | not yet used | Reserved for a future milestone. |

Safe local setup:

```bash
cp .env.example .env
# then edit .env and paste in your real OPENAI_API_KEY
```

`.env` is git-ignored and is loaded automatically (via `python-dotenv`)
the first time `--provider openai` is used; a value already present in
your shell environment always takes priority over `.env`. If
`OPENAI_API_KEY` is missing entirely, the CLI prints a concise
configuration error and makes no network call.

#### Running a real OpenAI review

```bash
cd backend
source .venv/bin/activate

# Uses OPENAI_REVIEWER_MODEL from .env, or the gpt-5.6-luna default:
python -m experiment.cli review --task expression_evaluator --condition A_NO_RESULT --provider openai

# Optional exact-model override (takes priority over the environment variable):
python -m experiment.cli review --task expression_evaluator --condition A_NO_RESULT --provider openai --model gpt-5.6-luna
```

On success this prints a `ReviewerResult` JSON object (including, when
available, the OpenAI response ID and input/output token counts). On
failure (missing key, auth error, rate limit, quota/billing, network
error, or a refused/unparseable model response) it prints a concise,
secret-free JSON error to stderr and exits non-zero — it never
fabricates a result.

Automated tests for `OpenAIReviewer` (`backend/tests/test_openai_reviewer.py`)
use a fake OpenAI client and make no real network calls or API charges.

### Reproducible experiments with durable storage (Milestone 5)

`backend/experiment/orchestrator.py` adds `ExperimentOrchestrator`, which
runs every condition (A/B/C) some number of `--repetitions` against one
frozen candidate and saves a complete, durable artifact to disk.

**Execution order (always exactly this, and mechanically tested):**

1. Load and validate the task.
2. Run the **visible** tests.
3. If visible tests don't fully pass: stop immediately. No reviewer is
   ever called, hidden tests are never run, and a `status: "failed"`
   artifact is saved with a clear reason.
4. Freeze and SHA-256-hash the candidate, specification, and visible
   tests (one disk read, reused for every hash and every prompt below).
5. Build the A/B/C prompts from that frozen content.
6. For each repetition, run a **seeded-random** ordering of A/B/C, each
   as a fully independent reviewer call (a brand-new reviewer instance
   per call — no shared response IDs, conversation state, or previous
   responses), checkpointing to disk after every completed observation.
7. **Only after every reviewer call has completed**, run the **hidden**
   tests exactly once.
8. Save the final `status: "completed"` (or `"partial"`, if a reviewer
   call failed) artifact.

**Why hidden tests run last:** the whole point of this research is to
measure what a reviewer predicts *before* ground truth is known. Running
hidden tests first (or interleaved) would risk that ground truth ever
leaking into a reviewer call, even by accident — so the orchestrator
makes it structurally impossible: hidden tests are simply never invoked
until the very last step, after every reviewer observation exists.

**Failure/checkpoint behavior:** because real API calls cost money, a
failure partway through never loses already-completed observations. The
artifact file is created (via write-to-temp-file-then-atomic-rename)
*before* any reviewer call begins, and re-saved atomically after each one
succeeds. If a reviewer call fails, prior observations are preserved,
the artifact is marked `"partial"` with a safe error category/message,
hidden tests are skipped, and the CLI exits non-zero. Every run gets a
fresh UUID, so re-running never overwrites a previous experiment.

**Artifact location:** `backend/data/experiments/<experiment_id>.json`
(git-ignored, like the rest of `backend/data/` — see `.gitignore`).

```bash
cd backend
source .venv/bin/activate

# Mock provider: free, deterministic, makes no network calls. Good for
# checking the pipeline/ordering/artifact shape before spending money.
python -m experiment.cli experiment --task expression_evaluator --provider mock --repetitions 1 --seed 42

# Real OpenAI provider: makes repetitions x 3 billed API calls (one per
# condition per repetition) -- e.g. --repetitions 5 makes 15 calls.
python -m experiment.cli experiment --task expression_evaluator --provider openai --repetitions 1 --seed 42
```

> **⚠️ Cost warning:** with `--provider openai`, the number of billed API
> calls is always exactly `repetitions x 3`. `--provider mock` output is a
> fixed fixture for testing the pipeline, not a real experimental result.

The CLI prints a concise JSON summary only (experiment ID, artifact path,
task/provider/model, repetition/observation counts, the actual randomized
execution order, visible/hidden ground truth, and mean confidence by
condition) — never the full prompts, and never `OPENAI_API_KEY`. The
complete data (every observation's full `ReviewerResult`) lives only in
the saved artifact file.

Additional key modules:

- `backend/experiment/experiment_models.py` — `ExperimentMetadata`, `ReviewerObservation`, `GroundTruth`, `ExperimentError`, `ExperimentArtifact`
- `backend/experiment/orchestrator.py` — `ExperimentOrchestrator`, `default_experiments_dir`
- `backend/experiment/cli.py` — `python -m experiment.cli experiment --task <id> --provider <mock|openai> --repetitions <n> --seed <n> [--model ...] [--output-dir ...]`

Automated tests (`backend/tests/test_orchestrator.py`) use fake reviewers
and a fake pytest runner with `tmp_path` for artifacts, and mechanically
verify the execution order above (visible → all reviewer calls → hidden,
exactly once), reproducible seeded ordering, checkpoint durability across
a simulated reviewer failure, and that no hidden-test or secret content
ever appears in a saved artifact.

### Hidden-blind candidate generation (Milestone 7)

`backend/experiment/candidate_orchestrator.py` adds a bounded coding-agent
loop that produces an independent candidate implementation from **only**:

1. the task specification,
2. starter code,
3. visible-test source,
4. visible-test execution feedback from the candidate's own attempts.

Hidden tests are never read, copied, executed, summarized, or referenced
during generation. Hidden evaluation remains a later step of the existing
experiment orchestrator. The tracked `json_parser` reference implementation
is the known-correct integration baseline and is never overwritten.

**Execution order:**

1. Load the task and freeze specification, starter, and visible tests.
2. Ask the generator for a complete implementation.
3. Materialize it in a temporary workspace that contains **only** the
   candidate source and visible tests (never a copy of the full task
   directory).
4. Run visible tests.
5. If they fail and attempts remain, send the prior source plus visible
   stdout/stderr and pass/fail counts, and ask for a corrected complete
   implementation.
6. Stop when visible tests pass, `--max-attempts` is reached (default 3),
   or a timeout / provider / validation failure occurs.
7. Persist every attempt's source hash, visible result, timing, token
   metadata, and summary, plus the final source, under
   `backend/data/candidates/<task_id>/<candidate_id>/` (git-ignored).

```bash
cd backend
source .venv/bin/activate

# Mock provider: free, deterministic, makes no network calls. Returns the
# starter source each attempt (so json_parser visible tests will fail).
python -m experiment.cli generate-candidate \
  --task json_parser --provider mock --max-attempts 3 --seed 42

# Real OpenAI provider: one billed API call per attempt.
# Model priority: --model > OPENAI_CANDIDATE_MODEL > gpt-5-mini
# (never a silent fallback to a different model).
python -m experiment.cli generate-candidate \
  --task json_parser --provider openai --max-attempts 3 --seed 42
```

> **⚠️ Cost warning:** `--provider openai` makes a real, billed API call
> per generation attempt. `--provider mock` makes no network calls.
> `random_seed` is recorded for workflow reproducibility; it does **not**
> make OpenAI model sampling deterministic.

`expression_evaluator` has no starter and cannot be used with
`generate-candidate`. `json_parser` declares `starter/json_parser.py`.

Additional key modules:

- `backend/experiment/candidate_models.py` — request, attempt, feedback, artifact metadata
- `backend/experiment/candidate_prompts.py` — frozen generation context and prompts
- `backend/experiment/candidate_generator.py` — `CandidateGenerator` protocol, `MockCandidateGenerator`
- `backend/experiment/openai_candidate_generator.py` — `OpenAICandidateGenerator`
- `backend/experiment/candidate_orchestrator.py` — bounded loop and artifact storage

### Candidate-aware reviewer experiments (Milestone 8)

`--candidate-id <UUID>` on the `experiment` command makes an experiment
review a previously **generated** candidate artifact (see Milestone 7
above) instead of the task's tracked reference implementation.
`backend/experiment/candidate_loader.py` (`CandidateArtifactLoader`) loads
and verifies that artifact defensively before it is ever used:

1. `candidate_id` is validated as a UUID (never treated as a path).
2. The artifact directory is resolved strictly under
   `backend/data/candidates/<task_id>/<candidate_id>/`; traversal and any
   symlink at the candidate directory, `metadata.json`, or the source file
   are rejected.
3. `metadata.json` is validated against the existing
   `CandidateArtifactMetadata` model, its `task_id` must match the
   requested task, and its `required_module_filename` must match the task
   manifest's candidate filename.
4. The source file's SHA-256 is recomputed and must match the hash
   recorded in metadata.
5. The candidate must have `status="completed"`,
   `stop_reason="visible_tests_passed"`, and `visible_tests_passed=true`.

No path supplied *by* metadata is ever trusted as a filesystem location —
the source file's location is always computed from the harness-controlled
artifact directory and the task manifest's own filename.

**Candidate-aware execution order** (`--candidate-id` given) is identical
to the tracked-candidate order above, except steps 2, 4, and 7 use the
supplied candidate source (evaluated in fresh, isolated workspaces — never
mixing visible/hidden tests, and never overwriting the tracked task file)
instead of the tracked reference implementation:

1. Load and validate the task.
2. Run the **visible** tests against the *supplied candidate source*.
3. If they don't fully pass: stop before any reviewer call; hidden tests
   are never run.
4. Freeze/hash that same supplied candidate source (not the tracked one).
5. Build the A/B/C prompts from that frozen content — the reviewer sees
   the generated candidate, never the tracked reference.
6. Run the seeded-random A/B/C repetitions, checkpointing as before.
7. Only after every reviewer call completes, run **hidden** tests against
   that same supplied candidate source.
8. Save the artifact, now also recording candidate provenance:
   `candidate_id`, `candidate_source_sha256`, `generator_provider`,
   `generator_model`, `generation_prompt_version`,
   `generation_attempt_count`, and a project-relative
   `generation_artifact_path` (never an absolute path). All of these are
   `None`/absent for tracked-candidate (no `--candidate-id`) experiments —
   fully backward-compatible with experiments saved before Milestone 8.

```bash
cd backend
source .venv/bin/activate

# Tracked-candidate behavior (unchanged, no --candidate-id):
python -m experiment.cli experiment --task json_parser --provider mock --repetitions 1 --seed 42

# Candidate-aware: review a specific saved candidate artifact instead.
python -m experiment.cli experiment \
  --task json_parser --candidate-id <UUID> --provider mock --repetitions 3 --seed 42
```

`--candidate-id` is optional; omitting it preserves the exact prior
tracked-candidate behavior. Supplying a candidate generated for a
*different* task, or one that fails any verification check above, is a
safe, secret-free error on stderr with a nonzero exit code — never a
silent fallback. The CLI summary always prints `candidate_id` and
`candidate_source_sha256` (both `null` when not candidate-aware).

Additional key modules:

- `backend/experiment/candidate_loader.py` — `CandidateArtifactLoader`, `LoadedCandidate`
- `backend/experiment/candidate_workspace.py` — isolated visible/hidden candidate-evaluation workspaces

### Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

Copy `.env.example` to `.env` at the repo root (or as documented later) and fill in API keys and `DATABASE_URL` when those features are implemented.
