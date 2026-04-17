# AIops Telemetry — Claude Code Rules

This file is read automatically by Claude Code before every session.
All rules below are **mandatory** — not suggestions.

---

## Project Overview

| Layer | Location | Language | Role |
|-------|----------|----------|------|
| SDK / client library | `aiops_sdk/` | Python 3.11+ | Instrumentation for LangGraph apps |
| Backend API (routes) | `server/api/` | Python / FastAPI | REST endpoints |
| Business logic | `server/engine/` | Python | Issue detection, escalation, autofix |
| ORM / persistence | `server/database/` | Python / SQLAlchemy | SQLite models |
| Frontend SPA | `server/dashboard/` | Vanilla HTML + JS | Web dashboard |

Test infra: **pytest** + **pytest-asyncio** + **httpx** (FastAPI TestClient).
Coverage: **pytest-cov**. Linting: **ruff**. Formatting: **black**. Types: **mypy**.

---

## 1. Version Control

### Branch Policy

- **Never commit directly to `main`**. Every change — feature, bugfix, chore, or docs — lives on a branch.
- Branch off `main` for every piece of work, no matter how small.
- **Push your branch to `origin` at least once per working session** (i.e. every time you make meaningful progress, not just when the work is complete). This keeps work visible to the team and prevents lost work.
- Open a Pull Request on GitHub when the branch is ready for review. PRs must pass all CI checks before merge.
- Delete the remote branch after it is merged into `main`.
- Rebase onto `main` (not merge) to keep a linear history: `git pull --rebase origin main`.

### Continuous Integration Workflow

```
main  ──────────────────────────────────────────────────► (protected)
        │                                    ▲
        │ git checkout -b feature/my-work    │ PR merged
        ▼                                    │
    feature/my-work                          │
        │  commit  ──► git push origin feature/my-work  (push early, push often)
        │  commit  ──► git push
        │  commit  ──► git push
        └──────────────────────────────────► Open Pull Request on GitHub
```

**Push cadence rules:**
1. Push immediately after creating the branch (`git push -u origin <branch>`).
2. Push after every logical commit — do not accumulate multiple sessions of work locally.
3. If a branch has not been pushed in over 24 hours, push before starting new work.
4. Always push before ending a working session, even if the work is incomplete (use `WIP:` prefix in the commit message if needed).

### Branch Naming

```
feature/<short-description>       # new capability
bugfix/<short-description>        # fix a bug on a non-critical branch
hotfix/<short-description>        # urgent fix branched from main
chore/<short-description>         # tooling, deps, config — no prod logic
test/<short-description>          # adding/fixing tests only
refactor/<short-description>      # code restructuring, no behaviour change
docs/<short-description>          # documentation only
```

Examples:
```
feature/nfr-27-db-connection-pool-detection
bugfix/issue-dedup-fingerprint-collision
hotfix/sdk-flush-thread-leak
chore/upgrade-fastapi-0.116
test/issue-detector-coverage
```

### Commit Message Format (Conventional Commits)

```
<type>(<scope>): <short imperative summary>

[optional body — why, not what]

[optional footer: BREAKING CHANGE / Closes #<issue>]
```

**Types:**

| Type | When |
|------|------|
| `feat` | New feature or new NFR rule |
| `fix` | Bug fix |
| `test` | Adding or fixing tests (no prod code change) |
| `refactor` | Restructure without behaviour change |
| `chore` | Dependency bump, tooling, config |
| `docs` | Documentation only |
| `perf` | Performance improvement |
| `ci` | CI/CD pipeline changes |

**Scopes** map to project layers:

| Scope | Maps to |
|-------|---------|
| `sdk` | `aiops_sdk/` |
| `api` | `server/api/` |
| `engine` | `server/engine/` |
| `db` | `server/database/` |
| `dashboard` | `server/dashboard/` |
| `config` | `server/config.py`, `pyproject.toml` |
| `ci` | `.github/` |

Examples:
```
feat(engine): add NFR-27 DB connection pool exhaustion detector
fix(sdk): prevent double-flush when trace_id not found in buffer
test(api): add integration tests for POST /api/ingest/batch
chore(config): pin ruff to 0.4.x
refactor(engine): extract _upsert_issue helper from issue_detector
```

---

## 2. TDD Workflow — Red → Green → Refactor

**This is the mandatory workflow for every code change.**

### Step 1 — Write a failing test (RED)

Before writing any implementation code:

1. Identify the smallest testable behaviour.
2. Write the test in the appropriate file under `tests/`.
3. Run `pytest <test_file> -x` and confirm it **fails** for the right reason
   (not an import error — if it fails due to a missing module, create the module
   with a stub/`pass` body first).
4. Commit the failing test:
   ```
   test(engine): add failing test for NFR-27 pool exhaustion detection
   ```

### Step 2 — Write minimum implementation to pass (GREEN)

1. Write only enough production code to make the test pass.
2. No gold-plating, no extra features not covered by a test.
3. Run `pytest <test_file> -x` — it must pass.
4. Run the full suite: `pytest` — no regressions allowed.
5. Commit the implementation:
   ```
   feat(engine): implement NFR-27 pool exhaustion detector
   ```

### Step 3 — Refactor (REFACTOR)

1. Clean up both the implementation and the test (remove duplication, rename
   for clarity, extract helpers).
2. Run `pytest` after every edit — tests must stay green.
3. Commit if the refactor is non-trivial:
   ```
   refactor(engine): extract threshold helper used by NFR-27 and NFR-25
   ```

### Non-negotiable rules

- **No implementation code without a failing test first.** If you are asked to
  add a feature and there is no test, write the test first.
- **Tests must be in the right tier** (unit vs integration — see §3).
- **Never mock what you own** unless it crosses a process/network boundary.
  In-memory SQLite is preferred over mocking SQLAlchemy sessions.
- A test that asserts `True is True` or is otherwise vacuous counts as no test.

---

## 3. Test Location Rules

### Directory Layout

```
tests/
├── conftest.py           # shared fixtures (DB session, TestClient, etc.)
├── unit/
│   ├── sdk/              # aiops_sdk/* unit tests
│   ├── engine/           # server/engine/* unit tests
│   └── database/         # server/database/* schema / model tests
└── integration/
    └── api/              # server/api/* route integration tests
```

Test files are named `test_<module>.py` matching the source module they test.

### Tier Definitions

#### Unit tests (`tests/unit/`)
- Test a **single function or class** in isolation.
- No real HTTP calls; no real external processes.
- Database: use in-memory SQLite (`sqlite:///:memory:`) via the `db_session`
  fixture in `tests/conftest.py`.
- External services (OpenAI, Langfuse, webhooks): mock with `pytest-mock` /
  `unittest.mock`.
- Run time target: **< 1 second per test**.

#### Integration tests (`tests/integration/api/`)
- Test **FastAPI routes end-to-end** using `httpx.AsyncClient` with the real
  `app` instance from `server.main`.
- Use in-memory SQLite so no real `aiops.db` is touched.
- Do **not** mock the database or internal services — exercise the real stack.
- External HTTP calls (webhooks, Langfuse): mock at the `httpx` level.
- Run time target: **< 5 seconds per test**.

---

## 4. Layer-Specific Rules

### 4a. SDK — `aiops_sdk/`

| Rule | Detail |
|------|--------|
| Coverage | **100%** line coverage required |
| Thread-safety | Every public method that touches `_buffers` must have a concurrent test |
| No real HTTP | All `requests.post` calls must be patched in tests |
| Public API | Every symbol exported from `aiops_sdk/__init__.py` must be tested |

Test file map:

| Source | Test |
|--------|------|
| `aiops_sdk/client.py` | `tests/unit/sdk/test_client.py` |
| `aiops_sdk/config.py` | `tests/unit/sdk/test_config.py` |
| `aiops_sdk/context.py` | `tests/unit/sdk/test_context.py` |
| `aiops_sdk/callback_handler.py` | `tests/unit/sdk/test_callback_handler.py` |
| `aiops_sdk/decorators.py` | `tests/unit/sdk/test_decorators.py` |

### 4b. Business Logic Engines — `server/engine/`

| Rule | Detail |
|------|--------|
| Coverage | **≥ 90%** line coverage |
| DB access | Use in-memory SQLite with real SQLAlchemy sessions |
| External I/O | Mock `requests`, `httpx`, `openai`, `psutil` at module boundary |
| Async engines | Test with `pytest-asyncio`; mark with `@pytest.mark.asyncio` |
| NFR rules | Each individual NFR rule in `issue_detector.py` must have a dedicated test |

Test file map:

| Source | Test |
|--------|------|
| `server/engine/issue_detector.py` | `tests/unit/engine/test_issue_detector.py` |
| `server/engine/escalation_engine.py` | `tests/unit/engine/test_escalation_engine.py` |
| `server/engine/webhook_dispatcher.py` | `tests/unit/engine/test_webhook_dispatcher.py` |
| `server/engine/modifier_agent.py` | `tests/unit/engine/test_modifier_agent.py` |
| `server/engine/autofix_agent.py` | `tests/unit/engine/test_autofix_agent.py` |
| `server/engine/process_manager.py` | `tests/unit/engine/test_process_manager.py` |

### 4c. API Routes — `server/api/`

| Rule | Detail |
|------|--------|
| Coverage | **≥ 85%** line coverage |
| Test client | `httpx.AsyncClient(app=app, base_url="http://test")` |
| Minimum per route | Happy path + validation error (422) + not-found (404) where applicable |
| Auth | Test both with and without `X-AIops-Key` when `AIOPS_API_KEY` is set |

Test file map:

| Source | Test |
|--------|------|
| `server/api/health.py` | `tests/integration/api/test_health.py` |
| `server/api/ingest.py` | `tests/integration/api/test_ingest.py` |
| `server/api/traces.py` | `tests/integration/api/test_traces.py` |
| `server/api/issues.py` | `tests/integration/api/test_issues.py` |
| `server/api/escalations.py` | `tests/integration/api/test_escalations.py` |
| `server/api/metrics.py` | `tests/integration/api/test_metrics.py` |
| `server/api/autofix.py` | `tests/integration/api/test_autofix.py` |

### 4d. Database Models — `server/database/`

| Rule | Detail |
|------|--------|
| Coverage | **100%** on `models.py` |
| Scope | Schema correctness: create, read, FK constraints, unique constraints |
| Database | In-memory SQLite only — never touch `aiops.db` in tests |

### 4e. Frontend Dashboard — `server/dashboard/`

The dashboard is a vanilla-JS SPA served by FastAPI.
Automated browser tests are **not yet set up** (future: Playwright).

Current rules:
- Manual smoke-test checklist required in the PR description for any JS change.
- The FastAPI route that serves `index.html` must be covered by an integration
  test asserting `200 OK` and `Content-Type: text/html`.
- No `console.log` statements committed — use the toast/log panel in the UI.

---

## 5. PR / Merge Checklist

Before opening a PR and before merging, **all** of the following must be true:

```
[ ] All tests pass locally:     pytest
[ ] Coverage not reduced:       pytest --cov --cov-fail-under=80
[ ] No linting errors:          ruff check .
[ ] No formatting violations:   black --check .
[ ] Type checks pass:           mypy server/ aiops_sdk/
[ ] No console.log in JS:       grep -r "console\.log" server/dashboard/
[ ] No TODO without issue link: grep -rn "TODO" . | grep -v "#[0-9]"
[ ] Branch is up-to-date with main (rebase preferred)
[ ] PR description includes: what changed, why, test approach
```

The CI pipeline (`.github/workflows/ci.yml`) enforces all of the above automatically.

---

## 6. Code Quality Rules

### No `console.log` in committed JS
The dashboard uses its own toast/log panel. Remove all `console.log` / `console.warn`
from `server/dashboard/index.html` before committing.

```bash
# Check before committing
grep -n "console\.log\|console\.warn\|console\.error" server/dashboard/index.html
```

### No `TODO` without a linked issue
Every `TODO`, `FIXME`, or `HACK` comment must include a GitHub issue reference.

```python
# BAD
# TODO: handle pagination

# GOOD
# TODO(#42): handle pagination when result set > 500
```

### No secrets in code
Never hardcode API keys, passwords, or URLs pointing to internal systems.
All configuration lives in `.env` (gitignored) and is accessed via `server/config.py`.

### Type annotations
New Python functions must include type annotations.
Run `mypy server/ aiops_sdk/` to check — it must exit `0`.

### Logging not printing
Use Python's `logging` module. Never use `print()` in server or SDK code.

```python
# BAD
print("Flushing trace", trace_id)

# GOOD
logger = logging.getLogger("aiops.client")
logger.debug("Flushing trace %s", trace_id)
```

---

## 7. Running the Test Suite

```bash
# Full suite
pytest

# With coverage report
pytest --cov=server --cov=aiops_sdk --cov-report=term-missing

# Specific tier
pytest tests/unit/
pytest tests/integration/

# Specific layer
pytest tests/unit/sdk/
pytest tests/unit/engine/test_issue_detector.py

# Watch mode (install pytest-watch first)
ptw tests/ -- -x

# Fail fast on first failure
pytest -x

# Run only tests matching a keyword
pytest -k "test_nfr"
```

---

## 8. Development Environment Setup

```bash
# 1. Create venv
python -m venv venv
source venv/Scripts/activate   # Windows
# source venv/bin/activate     # macOS/Linux

# 2. Install server + test dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 3. Install SDK in editable mode
pip install -e .

# 4. Copy env template
cp .env.example .env
# Fill in AIOPS_LANGFUSE_*, ANTHROPIC_API_KEY, OPENAI_API_KEY

# 5. Verify everything works
pytest
ruff check .
black --check .
mypy server/ aiops_sdk/
```

---

## 9. What Claude Should Never Do

- Commit directly to `main`
- Start feature work without first creating a branch and pushing it to `origin`
- Let a branch go more than one session without pushing to `origin`
- Write implementation code without writing a failing test first
- Skip tests for "trivial" changes (there are no trivial changes in production code)
- Leave `print()` statements in `server/` or `aiops_sdk/`
- Leave `console.log` in `server/dashboard/index.html`
- Mock the database when in-memory SQLite would work
- Use `time.sleep()` in tests — use `freezegun` or mock `datetime` instead
- Reduce test coverage below the thresholds defined in `pyproject.toml`
- Add a `TODO` comment without a linked GitHub issue number
- Hardcode secrets or API keys
