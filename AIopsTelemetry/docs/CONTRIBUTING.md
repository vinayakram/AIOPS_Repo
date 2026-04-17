# Contributing to AIops Telemetry

Welcome. This guide explains how to contribute using our **Test-Driven Development (TDD)**
workflow. Every change — from a one-line bug fix to a new NFR detection rule — follows the
same process.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Setting Up Your Environment](#2-setting-up-your-environment)
3. [The TDD Workflow: Step by Step](#3-the-tdd-workflow-step-by-step)
4. [Test Tiers Explained](#4-test-tiers-explained)
5. [Layer-Specific Test Standards](#5-layer-specific-test-standards)
6. [Running the Test Suite](#6-running-the-test-suite)
7. [Code Quality Checks](#7-code-quality-checks)
8. [Creating a Pull Request](#8-creating-a-pull-request)
9. [Worked Example: Adding a New NFR Rule](#9-worked-example-adding-a-new-nfr-rule)

---

## 1. Prerequisites

- Python 3.11 or later
- Git
- Claude Code CLI (for AutoFix feature development)

---

## 2. Setting Up Your Environment

```bash
# Clone the repo
git clone <repo-url>
cd AIopsTelemetry

# Create and activate a virtual environment
python -m venv venv
source venv/Scripts/activate       # Windows
# source venv/bin/activate         # macOS / Linux

# Install runtime + dev dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Install the SDK in editable mode (needed for tests)
pip install -e .

# Copy environment template
cp .env.example .env
# Open .env and add your API keys

# Verify the test suite runs (all tests will be collected, most skipped until filled in)
pytest --tb=short

# Verify linting passes
ruff check .
black --check .
mypy server/ aiops_sdk/
```

---

## 3. The TDD Workflow: Step by Step

TDD is not optional on this project. The workflow has three phases that
**must be followed in order**:

```
RED  →  GREEN  →  REFACTOR
```

### Phase 1 — RED: Write a Failing Test

The goal of this phase is to define the expected behaviour **before** any code exists.

**Step 1.1** — Create a feature branch:
```bash
git checkout -b feature/nfr-27-pool-exhaustion-detector
```

**Step 1.2** — Identify the smallest testable unit of the new behaviour.
For a new NFR rule in `issue_detector.py`, that is:
- "Given a database with X spans that show connection pool exhaustion,
  `detect_issues()` returns a new issue with `issue_type='db_pool_exhaustion'`."

**Step 1.3** — Write the test in the correct file (see §4 for which file):
```python
# tests/unit/engine/test_issue_detector.py

class TestDbPoolExhaustion:
    def test_pool_exhaustion_creates_issue(self, db_session):
        # Arrange — seed the DB
        for i in range(5):
            t = _make_trace(db_session, f"t{i}", status="error")
            _make_span(db_session, f"s{i}", f"t{i}",
                       error_message="connection pool exhausted")

        # Act
        issues = detect_issues(db_session)

        # Assert
        pool_issues = [i for i in issues if i.issue_type == "db_pool_exhaustion"]
        assert len(pool_issues) == 1
        assert pool_issues[0].severity == "high"
```

**Step 1.4** — Run the test and **confirm it fails**:
```bash
pytest tests/unit/engine/test_issue_detector.py::TestDbPoolExhaustion -x -v
```

Expected output: `FAILED` with `AssertionError` (not an import error).

> If it fails due to a missing function/class, add a stub that raises
> `NotImplementedError` so the import succeeds, then rerun.

**Step 1.5** — Commit the failing test:
```bash
git add tests/unit/engine/test_issue_detector.py
git commit -m "test(engine): add failing test for NFR-27 pool exhaustion detector"
```

---

### Phase 2 — GREEN: Write Minimum Implementation

The goal is to make the test pass with the **least code possible**.
Do not add features not covered by a test.

**Step 2.1** — Add the detector function in `server/engine/issue_detector.py`:
```python
def _detect_db_pool_exhaustion(db: Session) -> list[Issue]:
    """NFR-27: detect DB connection pool exhaustion errors."""
    # ... minimal implementation ...
```

**Step 2.2** — Call it from `detect_issues()`:
```python
created.extend(_detect_db_pool_exhaustion(db))
```

**Step 2.3** — Run the test — it must now pass:
```bash
pytest tests/unit/engine/test_issue_detector.py::TestDbPoolExhaustion -x -v
```
Expected: `PASSED`.

**Step 2.4** — Run the full suite — no regressions:
```bash
pytest -x
```

**Step 2.5** — Commit:
```bash
git add server/engine/issue_detector.py
git commit -m "feat(engine): implement NFR-27 pool exhaustion detector"
```

---

### Phase 3 — REFACTOR: Clean Up

Now that the behaviour is locked in by a passing test, clean up the code.

**Step 3.1** — Look for duplication between the new detector and existing ones.
Can you extract a shared helper?

**Step 3.2** — Rename variables for clarity. Remove dead code.

**Step 3.3** — Run tests after **every edit**:
```bash
pytest tests/unit/engine/ -x
```

**Step 3.4** — If the refactor is non-trivial, commit it separately:
```bash
git commit -m "refactor(engine): extract _create_issue_if_absent helper used by NFR-25 through NFR-27"
```

---

## 4. Test Tiers Explained

### Unit Tests (`tests/unit/`)

- Test a single function or class in isolation.
- Database: in-memory SQLite via the `db_session` fixture from `conftest.py`.
- External I/O: always mocked (`pytest-mock`, `unittest.mock.patch`).
- Run time: < 1 second per test.
- `conftest.py` provides: `db_session`, `make_trace`, `make_span`, `make_issue`.

### Integration Tests (`tests/integration/api/`)

- Test complete HTTP request/response cycles using FastAPI's test client.
- Database: still in-memory SQLite (via `app` + `client` fixtures from `conftest.py`).
- External HTTP (webhooks, Langfuse, OpenAI): mocked at the `httpx`/`requests` level.
- Do **not** mock internal services — let the full stack run.
- Run time: < 5 seconds per test.
- `conftest.py` provides: `app`, `client`.

---

## 5. Layer-Specific Test Standards

### SDK — `aiops_sdk/`
| Requirement | Detail |
|-------------|--------|
| Coverage | **100%** line coverage |
| Thread-safety | Concurrent tests with `threading.Thread` for `AIopsClient` |
| No real HTTP | Patch `requests.post` in every test that calls `_flush()` |
| Exports | Every symbol in `__init__.py` must be imported and used in at least one test |

### Engine — `server/engine/`
| Requirement | Detail |
|-------------|--------|
| Coverage | **≥ 90%** |
| NFR rules | One test for "triggers" + one for "does not trigger" per rule |
| psutil | Always patch in engine tests: `patch("server.engine.issue_detector.psutil")` |
| Async | Use `@pytest.mark.asyncio` (or rely on `asyncio_mode = "auto"` in `pyproject.toml`) |

### API Routes — `server/api/`
| Requirement | Detail |
|-------------|--------|
| Coverage | **≥ 85%** |
| Minimum cases | Happy path, 422 validation, 404 not-found, duplicate handling |
| Test client | Use the async `client` fixture — never start a real server |

### Database — `server/database/`
| Requirement | Detail |
|-------------|--------|
| Coverage | **100%** on `models.py` |
| Never touch | `aiops.db` — use `db_session` fixture only |
| Constraints | Test `IntegrityError` is raised on unique violations |

---

## 6. Running the Test Suite

```bash
# Run everything
pytest

# Run with coverage report
pytest --cov=server --cov=aiops_sdk --cov-report=term-missing

# Fail if overall coverage drops below 80%
pytest --cov=server --cov=aiops_sdk --cov-fail-under=80

# Run only unit tests
pytest tests/unit/ -v

# Run only integration tests
pytest tests/integration/ -v

# Run a specific file
pytest tests/unit/engine/test_issue_detector.py -v

# Run tests matching a keyword
pytest -k "token_spike or pool" -v

# Run and stop on first failure
pytest -x

# Watch mode (re-runs on file changes — requires pytest-watch)
ptw tests/ -- -x
```

---

## 7. Code Quality Checks

Run these before every commit and before opening a PR.

```bash
# Linting (ruff)
ruff check .
ruff check . --fix          # auto-fix safe issues

# Formatting (black)
black .                     # format in-place
black --check .             # check only (used in CI)

# Type checking (mypy)
mypy server/ aiops_sdk/

# Check for console.log in dashboard
grep -n "console\.log" server/dashboard/index.html

# Check for unlinked TODOs
grep -rn "TODO\|FIXME\|HACK" . --include="*.py" | grep -v "#[0-9]"
```

All four checks are run by CI on every push. A failing check blocks the PR.

---

## 8. Creating a Pull Request

1. **Rebase your branch** on the latest `main`:
   ```bash
   git fetch origin
   git rebase origin/main
   ```

2. **Run the full checklist**:
   ```bash
   pytest --cov=server --cov=aiops_sdk --cov-fail-under=80
   ruff check .
   black --check .
   mypy server/ aiops_sdk/
   ```

3. **Push your branch**:
   ```bash
   git push origin feature/nfr-27-pool-exhaustion-detector
   ```

4. **Open a PR** against `main`. The PR description must include:
   - **What changed** — a concise summary
   - **Why** — the motivation (link the issue)
   - **Test approach** — what you tested and why that approach
   - **Checklist** (copy from CLAUDE.md §5)

5. **CI must be green** before requesting review.

6. **One approval required** before merge.

7. **Merge strategy**: squash merge preferred for feature branches to keep
   `main` history linear. Rebase merge acceptable for clean branch histories.

---

## 9. Worked Example: Adding a New NFR Rule

This end-to-end example shows every step for adding **NFR-27: DB connection pool
exhaustion** to `issue_detector.py`.

```bash
# 1. Branch
git checkout main && git pull
git checkout -b feature/nfr-27-pool-exhaustion

# 2. RED — write the failing test
# Edit tests/unit/engine/test_issue_detector.py
# Add TestDbPoolExhaustion class (see Phase 1 above)
pytest tests/unit/engine/test_issue_detector.py::TestDbPoolExhaustion -x
# → FAILED ✓

git add tests/unit/engine/test_issue_detector.py
git commit -m "test(engine): add failing tests for NFR-27 pool exhaustion"

# 3. GREEN — minimal implementation
# Edit server/engine/issue_detector.py
# Add _detect_db_pool_exhaustion() + call in detect_issues()
pytest tests/unit/engine/test_issue_detector.py::TestDbPoolExhaustion -x
# → PASSED ✓
pytest -x
# → All tests pass ✓

git add server/engine/issue_detector.py
git commit -m "feat(engine): implement NFR-27 DB pool exhaustion detector"

# 4. REFACTOR — clean up
# Extract shared helpers if found
pytest tests/unit/engine/ -x
# → All tests pass ✓

git commit -m "refactor(engine): extract _error_message_matches helper"

# 5. Quality checks
ruff check .
black --check .
mypy server/
pytest --cov=server --cov-fail-under=80

# 6. Push and open PR
git push origin feature/nfr-27-pool-exhaustion
```

---

## Questions?

Open an issue or reach out to the maintainers. All substantive discussions
should happen in issues, not in Slack threads — so the context is preserved
for future contributors.
