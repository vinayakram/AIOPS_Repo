# Upstream Integration Contract

## Purpose

This document is for the upstream application that already owns the first user interaction.

When the user clicks `AI Remediation` in the upstream product, that product should call this remediation system as a downstream service.

## Recommended Flow

1. User clicks `AI Remediation` in the upstream UI.
2. Upstream service calls `POST /api/issues`.
3. This remediation system creates an issue-scoped workflow.
4. Upstream service receives:
   - `issue_id`
   - `status`
   - `view_url`
   - `state_url`
5. Upstream service either:
   - redirects the user to the remediation UI using `view_url`, or
   - continues the workflow entirely by API

## Required Endpoint

### Create remediation run

`POST /api/issues`

Request body:

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
  "repo_root": "",
  "allowed_folder": "",
  "base_branch": "main",
  "validation_command": "pytest -q",
  "requested_by": "operator@example.com",
  "environment": "staging"
}
```

Response body:

```json
{
  "ok": true,
  "issue_id": "SUP-5001",
  "status": "ISSUE_SAVED",
  "view_url": "/api/issues/SUP-5001/view",
  "state_url": "/api/issues/SUP-5001/status"
}
```

## Field Notes

- `issue_id`: unique workflow key from the upstream system
- `project_name`: preferred logical application name
- `github_repo`: optional repo identifier like `org/repo`
- `upstream_repo`: optional git URL or direct repo reference from the upstream layer
- `repo_root`: optional local path if the remediation host already has the repo mounted
- `allowed_folder`: optional narrower scope inside the repo
- `validation_command`: validation to run after implementation
- `remediation_type`: one of:
  - `code_change`
  - `infra_change`
  - `config_change`
  - `runbook_change`
  - `investigation_only`
  - `human_handoff`

## Resolver Behavior

The remediation system resolves the target in this order:

1. configured project map match
2. direct repo details from the upstream payload
3. fallback direct-repo resolution using `github_repo` or `upstream_repo`

This means the upstream team can send agent/code repo details directly even if the repo is not pre-modeled in `project_map.json`.

## End-To-End API Lifecycle

### Start plan

`POST /api/issues/{issue_id}/plan`

### Check workflow status

`GET /api/issues/{issue_id}/status`

### Approve generated plan

`POST /api/issues/{issue_id}/plan/approve`

### Start implementation

`POST /api/issues/{issue_id}/implementation`

### Read implementation evidence

- `GET /api/issues/{issue_id}/diff`
- `GET /api/issues/{issue_id}/tests`
- `GET /api/issues/{issue_id}/artifacts`

### Human review approval

`POST /api/issues/{issue_id}/review/approve`

Example:

```json
{
  "review_notes": "Diff and validation output look good."
}
```

### Human review rejection

`POST /api/issues/{issue_id}/review/reject`

Example:

```json
{
  "review_notes": "Do not proceed. Scope is too broad."
}
```

### Branch push / PR creation

`POST /api/issues/{issue_id}/pr`

## Human-In-The-Loop Rules

The upstream team should expect these mandatory checkpoints:

1. Plan must be approved before implementation starts.
2. Implementation artifacts must exist before review approval is accepted.
3. Branch push / PR creation is blocked until human review is approved.

## Deep-Link Behavior

The upstream product can redirect a human reviewer into this system using:

`GET /api/issues/{issue_id}/view`

That returns the issue-specific UI link target for review.

## Minimal Upstream UX Recommendation

For the best operator experience, the upstream application should:

1. call `POST /api/issues`
2. show planning/implementation progress using `state_url`
3. open `view_url` when a human needs to inspect plan or diff
4. only trigger `/pr` after a reviewer has approved the generated changes
