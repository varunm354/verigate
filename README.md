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

### Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

Copy `.env.example` to `.env` at the repo root (or as documented later) and fill in API keys and `DATABASE_URL` when those features are implemented.
