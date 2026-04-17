# Branching & Merge Strategy

This document defines the complete branching model for AIops Telemetry —
how branches are created, named, kept up to date, and merged.

---

## Branch Model Overview

```
main  ←─ squash merge ─── feature/*
      ←─ squash merge ─── bugfix/*
      ←─ fast-forward ─── hotfix/*  (urgent only)
      ←─ squash merge ─── chore/*
      ←─ squash merge ─── test/*
      ←─ squash merge ─── refactor/*
      ←─ squash merge ─── docs/*
```

- **`main`** is the single long-lived branch. It is always in a deployable state.
- All work happens on short-lived branches.
- There are no `develop`, `staging`, or `release` branches — `main` is the
  source of truth.

---

## Branch Types

| Prefix | Purpose | Branches from | Merges to |
|--------|---------|---------------|-----------|
| `feature/` | New capability, new NFR rule | `main` | `main` |
| `bugfix/` | Fix a non-critical defect | `main` | `main` |
| `hotfix/` | Urgent production fix | `main` | `main` |
| `chore/` | Dep upgrades, config, tooling | `main` | `main` |
| `test/` | Add/fix tests, no prod change | `main` | `main` |
| `refactor/` | Restructure, no behaviour change | `main` | `main` |
| `docs/` | Documentation only | `main` | `main` |

---

## Naming Convention

```
<type>/<kebab-case-description>
```

- Use lowercase kebab-case only.
- Be descriptive enough that the branch purpose is clear from the name alone.
- Keep it under 60 characters.

**Good examples:**
```
feature/nfr-27-db-pool-exhaustion-detector
feature/dashboard-autofix-modal-progress
bugfix/issue-fingerprint-collision-on-empty-span
hotfix/sdk-flush-thread-leak-on-shutdown
chore/upgrade-sqlalchemy-2.1
test/sdk-client-thread-safety-coverage
refactor/extract-issue-upsert-helper
docs/add-nfr-27-to-non-functional-requirements
```

**Bad examples:**
```
my-changes           # no type prefix
feature/fix          # too vague
Feature/NewThing     # uppercase not allowed
feature/add-the-new-nfr-27-db-connection-pool-exhaustion-rule-detector  # too long
```

---

## Creating a Branch

Always branch from an up-to-date `main`:

```bash
git checkout main
git pull --ff-only          # fast-forward only; fails if you have local main commits
git checkout -b feature/your-description
```

If `--ff-only` fails, your local `main` has diverged — resolve that first:
```bash
git fetch origin
git reset --hard origin/main   # WARNING: discards any local commits on main
```

> **Rule**: Never commit directly to `main`. Push protection is enforced.

---

## Keeping Your Branch Up to Date

Rebase is the **preferred** method. It produces a linear history and avoids
merge-commit noise.

```bash
# Fetch latest main
git fetch origin

# Rebase your branch on top of main
git rebase origin/main
```

If you have already pushed your branch and need to force-update it:
```bash
git push --force-with-lease origin feature/your-description
```

> Use `--force-with-lease`, never bare `--force`. It aborts if someone else
> pushed to your branch.

**When to merge instead of rebase:**
Only when the branch is shared with another developer and rebasing would
rewrite commits they have already built on. In that case, use a merge commit
and note it in the PR description.

---

## Commit Discipline on Feature Branches

- Commit often and atomically: each commit should leave the tests green.
- Follow [Conventional Commits](https://www.conventionalcommits.org/) format
  (see `CLAUDE.md` §1 for the full type/scope table).
- Use `git commit --fixup` + `git rebase -i --autosquash` to tidy up before
  opening a PR.

**Commit sequence for a typical feature:**
```
test(engine): add failing test for NFR-27 pool exhaustion    ← RED
feat(engine): implement NFR-27 pool exhaustion detector       ← GREEN
refactor(engine): extract _error_message_matches helper       ← REFACTOR
docs(engine): document NFR-27 thresholds in config.py         ← optional
```

---

## Opening a Pull Request

### PR Title

Follow Conventional Commits format:
```
feat(engine): NFR-27 — DB connection pool exhaustion detector
```

### PR Description Template

```markdown
## What
Brief one-sentence summary of the change.

## Why
Link to the issue this resolves: Closes #<issue-number>
Explain the motivation — why now, what triggered this.

## How
- List key implementation decisions
- Explain non-obvious choices
- Note any trade-offs

## Test Approach
- Which test tier(s) were used (unit / integration)
- What edge cases are covered
- Coverage delta (before → after)

## Checklist
- [ ] All tests pass: `pytest`
- [ ] Coverage not reduced: `pytest --cov --cov-fail-under=80`
- [ ] No linting errors: `ruff check .`
- [ ] No formatting violations: `black --check .`
- [ ] Type checks pass: `mypy server/ aiops_sdk/`
- [ ] No `console.log` in dashboard JS
- [ ] No unlinked `TODO` comments
- [ ] Branch rebased on latest `main`
- [ ] PR description complete
```

### PR Size Guidelines

| PR Type | Ideal size | Rationale |
|---------|-----------|-----------|
| Single NFR rule | < 200 lines | One detector function + its tests |
| New API route | < 300 lines | Route + integration tests |
| Bug fix | < 100 lines | Targeted fix + regression test |
| Refactor | < 400 lines | Keep diff reviewable |

If your PR exceeds these, consider splitting it. A PR that adds a new feature
AND refactors surrounding code is two PRs.

---

## Review Process

1. **Self-review first**: re-read your own diff before requesting a reviewer.
   Look for: missing tests, console.log, TODO without issue, hardcoded values.

2. **One approval required** from a project maintainer.

3. **CI must be green**: all GitHub Actions checks must pass before merge.

4. **Address all comments**: either apply the change or explain why not.
   Don't resolve reviewer threads yourself — the reviewer resolves them.

---

## Merge Strategy

### Squash merge (default for feature/bugfix/chore/test/refactor/docs)

```bash
# On GitHub: "Squash and merge"
# Locally:
git checkout main
git merge --squash feature/your-description
git commit -m "feat(engine): NFR-27 DB pool exhaustion detector (#42)"
git push origin main
```

**Result:** One clean commit on `main` per PR.
The squash commit message should follow Conventional Commits and include
the PR number.

### Fast-forward merge (hotfixes only)

A hotfix is deployed immediately and must land as-is:
```bash
git checkout main
git merge --ff-only hotfix/sdk-thread-leak
git push origin main
```

### Never use `--no-ff` merge commits on `main`
They pollute the `git log` with branching noise that makes `git bisect` slower.

---

## Deleting Branches

Delete your branch immediately after merge — both remote and local:

```bash
# Remote (usually done automatically by GitHub after merge)
git push origin --delete feature/your-description

# Local
git branch -d feature/your-description
```

If the branch was squash-merged, `-d` may refuse because the commits are not
reachable. Use `-D` (force delete) in that case:
```bash
git branch -D feature/your-description
```

---

## Hotfix Process

A hotfix is for **urgent production defects** that cannot wait for the normal
feature-branch cycle.

```bash
# 1. Branch from main
git checkout main && git pull
git checkout -b hotfix/sdk-flush-thread-leak

# 2. Fix + minimal test (still required — even for hotfixes)
# ... write test first, then fix ...

# 3. Verify
pytest -x
ruff check .

# 4. Open PR — label it "hotfix" in GitHub
# Abbreviated review (one approval still required)

# 5. Fast-forward merge
git checkout main
git merge --ff-only hotfix/sdk-flush-thread-leak
git push origin main

# 6. Tag the fix
git tag -a v1.0.1 -m "hotfix: fix SDK thread leak on shutdown"
git push origin --tags

# 7. Delete branch
git push origin --delete hotfix/sdk-flush-thread-leak
git branch -D hotfix/sdk-flush-thread-leak
```

---

## Versioning

This project uses **Semantic Versioning** (semver: `MAJOR.MINOR.PATCH`).

| Change type | Version bump | Example |
|------------|-------------|---------|
| New NFR rule, new API endpoint | MINOR | 1.3.0 → 1.4.0 |
| Bug fix, performance improvement | PATCH | 1.4.0 → 1.4.1 |
| Breaking API or SDK change | MAJOR | 1.4.1 → 2.0.0 |

Tags are created on `main` after merging. The SDK version in `setup.py` must
match the git tag.

---

## Summary Cheat Sheet

```bash
# Start work
git checkout main && git pull && git checkout -b feature/my-thing

# Keep up to date
git fetch origin && git rebase origin/main

# Commit (RED phase)
git commit -m "test(engine): add failing test for X"

# Commit (GREEN phase)
git commit -m "feat(engine): implement X"

# Before PR
pytest --cov=server --cov=aiops_sdk --cov-fail-under=80
ruff check . && black --check . && mypy server/ aiops_sdk/

# Push
git push origin feature/my-thing

# After merge — clean up
git checkout main && git pull
git branch -D feature/my-thing
git push origin --delete feature/my-thing
```
