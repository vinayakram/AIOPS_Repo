# Medical RAG Agent — Claude Code Rules

This file is read automatically by Claude Code before every session.
All rules below are **mandatory** — not suggestions.

---

## Project Overview

| Layer | Location | Role |
|-------|----------|------|
| API & app | `backend/main.py` | FastAPI app, lifespan, `/api/query`, `/api/traces` |
| Auth | `backend/auth/` | JWT login/register, bcrypt passwords, `get_current_user` |
| RAG pipeline | `backend/rag/` | PubMed fetch → embed → FAISS → PageRank → LLM answer |
| PubMed client | `backend/pubmed/` | NCBI Entrez esearch + efetch |
| Database | `backend/database/` | SQLAlchemy User + TraceLog models (SQLite) |
| Tracing | `backend/tracing/` | Langfuse + AIops Telemetry clients |
| Frontend | `frontend/` | Vanilla-JS SPA (login, register, chat, dashboard) |

---

## 1. Version Control

### Branch Policy

- **Never commit directly to `main`**. Every change — feature, bugfix, chore, or docs — lives on a branch.
- Branch off `main` for every piece of work, no matter how small.
- **Push your branch to `origin` at least once per working session** — every time you make meaningful progress, not only when the work is complete. This keeps the team in sync and prevents lost work.
- Open a Pull Request on GitHub when the branch is ready for review.
- Delete the remote branch after it is merged into `main`.
- Rebase onto `main` to keep a linear history: `git pull --rebase origin main`.

### Continuous Integration Workflow

```
main  ──────────────────────────────────────────────────► (protected)
        │                                    ▲
        │ git checkout -b feature/my-work    │ PR merged
        ▼                                    │
    feature/my-work                          │
        │  commit  ──► git push origin feature/my-work  (push early, push often)
        │  commit  ──► git push
        └──────────────────────────────────► Open Pull Request on GitHub
```

**Push cadence rules:**
1. Push immediately after creating the branch: `git push -u origin <branch>`.
2. Push after every logical commit — do not accumulate multiple sessions of work locally.
3. Always push before ending a working session, even if the work is incomplete (prefix the commit message with `WIP:` if needed).

### Branch Naming

```
feature/<short-description>       # new capability
bugfix/<short-description>        # fix a bug
hotfix/<short-description>        # urgent fix branched from main
chore/<short-description>         # deps, config, tooling
test/<short-description>          # adding/fixing tests only
refactor/<short-description>      # restructuring, no behaviour change
docs/<short-description>          # documentation only
```

Examples:
```
feature/citation-pagerank-fallback
bugfix/faiss-index-empty-result-crash
hotfix/jwt-secret-rotation
chore/upgrade-sentence-transformers
refactor/rag-pipeline-extract-reranker
```

### Commit Message Format (Conventional Commits)

```
<type>(<scope>): <short imperative summary>

[optional body — why, not what]

[optional footer: BREAKING CHANGE / Closes #<issue>]
```

**Types:** `feat` | `fix` | `test` | `refactor` | `chore` | `docs` | `perf` | `ci`

**Scopes:**

| Scope | Maps to |
|-------|---------|
| `rag` | `backend/rag/` |
| `pubmed` | `backend/pubmed/` |
| `auth` | `backend/auth/` |
| `api` | `backend/main.py` |
| `db` | `backend/database/` |
| `tracing` | `backend/tracing/` |
| `frontend` | `frontend/` |
| `config` | `backend/config.py`, `.env`, `requirements.txt` |

Examples:
```
feat(rag): add BM25 hybrid retrieval alongside FAISS
fix(pubmed): handle empty abstract in Entrez XML response
refactor(rag): extract reranker from pipeline into standalone module
chore(config): pin faiss-cpu to 1.8.x
```

---

## 2. Development Rules

### Testing
- Write a failing test before every implementation change (Red → Green → Refactor).
- Unit tests for all RAG pipeline stages — mock `PubMedClient` and LLM API calls.
- Integration tests for all FastAPI routes using `httpx.AsyncClient`.
- Use in-memory SQLite (`StaticPool`) — never touch `medical_rag.db` in tests.
- Coverage target: ≥ 85% on `backend/`.

### Code Quality
- Do not use `print()` — use Python `logging`.
- No `console.log` in frontend JS.
- No hardcoded API keys, JWT secrets, or PubMed API keys — all via `.env`.
- Type annotations required on all new Python functions.
- Run `ruff check .` and `black --check .` before committing.

### Security
- The `medical_rag.db` file is gitignored — never commit it.
- The `.env` file is gitignored — never commit it.
- JWT secret must be a strong random string in production — never use the default value.
- All query endpoints require a valid Bearer token — do not add unauthenticated query routes.

### Frontend
- No `console.log` statements committed to `frontend/`.
- Manually smoke-test login → query → dashboard flow for any frontend change.
- No external CDN dependencies — all assets must be served locally.

---

## 3. PR / Merge Checklist

Before opening a PR and before merging, all of the following must be true:

```
[ ] Branch is up-to-date with main (rebased)
[ ] All tests pass locally: pytest
[ ] No linting errors: ruff check .
[ ] No formatting violations: black --check .
[ ] No console.log in frontend: grep -r "console\.log" frontend/
[ ] Branch has been pushed to origin
[ ] PR description includes: what changed, why, how to test
[ ] No secrets, .env, or *.db files committed
[ ] Manual smoke-test completed for any frontend change
```

---

## 4. What Claude Should Never Do

- Commit directly to `main`
- Start feature work without first creating a branch and pushing it to `origin`
- Let a branch go more than one session without pushing to `origin`
- Commit `medical_rag.db`, `.env`, or any file containing secrets
- Leave `print()` or `console.log` in committed code
- Add a `TODO` without a linked GitHub issue number
- Add unauthenticated routes to the query API
- Use `time.sleep()` in tests — use `freezegun` or mock `datetime`
