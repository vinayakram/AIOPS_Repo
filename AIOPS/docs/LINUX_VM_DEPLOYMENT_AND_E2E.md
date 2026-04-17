# Linux VM Deployment And End-To-End Test Guide

## What This Package Now Supports

- upstream-triggered remediation through REST APIs
- fallback manual UI entry
- project resolution from configured project map or upstream repo details
- remediation type selection:
  - `code_change`
  - `infra_change`
  - `config_change`
  - `runbook_change`
  - `investigation_only`
  - `human_handoff`
- human-in-the-loop approval before branch push / PR

## Files Added For Linux

- [scripts/bootstrap_linux.sh](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\scripts\bootstrap_linux.sh)
- [scripts/start_linux.sh](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\scripts\start_linux.sh)
- [systemd/codex-remediation.service](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\systemd\codex-remediation.service)

## Linux VM Setup

1. Copy the package to the Linux VM.
2. Install prerequisites:
   - Python 3.11+
   - Node.js + npm
   - Git
   - optional GitHub CLI
3. Run:

```bash
chmod +x scripts/bootstrap_linux.sh scripts/start_linux.sh
./scripts/bootstrap_linux.sh
```

4. Edit `.env` and set at minimum:

```env
CODEX_API_KEY=your_new_codex_key
CODEX_COMMAND=codex
CODEX_MODEL=gpt-5-codex
RUNS_DIR=./runs
MANAGED_REPOS_DIR=./managed_repos
DEFAULT_VALIDATION_COMMAND=pytest -q
```

5. Start the service:

```bash
./scripts/start_linux.sh
```

6. Open:

`http://<vm-host>:8000`

## Upstream Integration Contract

When the upstream product handles the first screen and the user clicks `AI Remediation`, it should call:

`POST /api/issues`

Example payload:

```json
{
  "issue_id": "SUP-5001",
  "project_name": "multiagent-support-copilot",
  "title": "Escalation payload loses context",
  "description": "Payload context is dropped during agent handoff.",
  "acceptance_criteria": [
    "Context is preserved end to end",
    "Validation checks pass"
  ],
  "source_system": "upstream-remediation-ui",
  "source_issue_id": "SUP-5001",
  "source_issue_url": "https://example.local/issues/SUP-5001",
  "remediation_type": "code_change",
  "github_repo": "vinayakram/multiagent-support-copilot",
  "upstream_repo": "https://github.com/vinayakram/multiagent-support-copilot.git",
  "base_branch": "main",
  "validation_command": "pytest -q",
  "requested_by": "operator@example.com",
  "environment": "staging"
}
```

Response shape:

```json
{
  "ok": true,
  "issue_id": "SUP-5001",
  "status": "ISSUE_SAVED",
  "view_url": "/api/issues/SUP-5001/view",
  "state_url": "/api/issues/SUP-5001/status"
}
```

## End-To-End API Test

### 1. Create the remediation run

```bash
curl -X POST http://127.0.0.1:8000/api/issues \
  -H "Content-Type: application/json" \
  -d @sample_issue.json
```

### 2. Start plan generation

```bash
curl -X POST http://127.0.0.1:8000/api/issues/SUP-5001/plan
```

### 3. Poll status

```bash
curl http://127.0.0.1:8000/api/issues/SUP-5001/status
```

### 4. Approve the plan

```bash
curl -X POST http://127.0.0.1:8000/api/issues/SUP-5001/plan/approve
```

### 5. Start implementation

```bash
curl -X POST http://127.0.0.1:8000/api/issues/SUP-5001/implementation
```

### 6. Review artifacts

```bash
curl http://127.0.0.1:8000/api/issues/SUP-5001/diff
curl http://127.0.0.1:8000/api/issues/SUP-5001/tests
curl http://127.0.0.1:8000/api/issues/SUP-5001/artifacts
```

### 7. Approve the reviewed changes

```bash
curl -X POST http://127.0.0.1:8000/api/issues/SUP-5001/review/approve \
  -H "Content-Type: application/json" \
  -d '{"review_notes":"Diff and validation look good."}'
```

### 8. Trigger branch push / PR

```bash
curl -X POST http://127.0.0.1:8000/api/issues/SUP-5001/pr
```

## Where The Human Checks Are

Human checks now exist in these places:

1. Plan approval
   - Route: `POST /approve-plan`
   - API: `POST /api/issues/{issue_id}/plan/approve`
   - Purpose: no implementation starts until a human approves the plan

2. Change review after implementation
   - UI: Implementation screen review banner and review buttons
   - API approve: `POST /api/issues/{issue_id}/review/approve`
   - API reject: `POST /api/issues/{issue_id}/review/reject`
   - Purpose: branch push / PR is blocked until a human reviews diff, summary, and validation output

3. Delivery gate
   - Route: `POST /create-branch-pr`
   - API: `POST /api/issues/{issue_id}/pr`
   - Enforcement: backend rejects the request unless review status is `approved`

## Artifacts Reviewers Should Inspect

- `runs/<issue_id>/plan.md`
- `runs/<issue_id>/git_diff.patch`
- `runs/<issue_id>/head_show.txt`
- `runs/<issue_id>/test_results.json`
- `runs/<issue_id>/change_summary.json`
- `runs/<issue_id>/pr_view.json`

## Notes On Non-Code Remediation

Non-code remediation types are accepted and routed through the same human review flow.

Practical meaning:

- the prompt adapts to the remediation type
- validation can be command-based or manual
- delivery still requires human approval
- `investigation_only` and `human_handoff` can be used when a code PR is not the right outcome
