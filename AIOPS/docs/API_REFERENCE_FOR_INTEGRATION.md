# API Reference For Integration

## Purpose

This remediation service is API-first.

Primary operating model:

1. Upstream app sends minimal issue details.
2. Remediation service resolves the application to a candidate repository.
3. User approves the repository choice.
4. User generates and reviews the plan.
5. If the plan is approved, implementation starts automatically.
6. Upstream app reads implementation summary and artifacts through APIs.

UI is fallback only.

## Base URL

Example:

```text
http://<host>:8000
```

## Supported Remediation Types

- `code_change`
- `infra_change`
- `config_change`
- `runbook_change`
- `investigation_only`
- `human_handoff`

## Project Resolution Prerequisites

Project resolution works only if at least one of these is true:

1. The application name matches an entry in `config/project_map.json`.
2. The upstream caller provides repository hints such as:
   - `github_repo`
   - `upstream_repo`
   - `repo_root`
   - `allowed_folder`

What this means in practice:

- Best option: maintain `config/project_map.json` with application-to-repository mappings.
- Fallback option: upstream sends direct repository information in the `POST /api/issues` request.

If neither exists:

- the service can create the issue record
- but it may not be able to produce a confident repository resolution
- in that case the response will indicate that no confident repository match was found

### Minimum project map expectation

Each application should ideally define:

```json
{
  "your-application-name": {
    "repo_root": "/repos/your-app",
    "allowed_folder": "/repos/your-app/src",
    "test_command": "pytest -q",
    "base_branch": "main",
    "github_repo": "org/your-app",
    "matchers": ["your-app", "your app", "known issue keywords"]
  }
}
```

### Resolution approval prerequisite

Before plan generation:

- the user must approve the resolved project/repository through
  `POST /api/issues/{issue_id}/project/approve`

After that approval:

- the remediation service stores the resolved repo details internally
- later APIs reuse those stored details
- the upstream app does not need to resend issue details for plan generation

## How Issue State Is Maintained

The remediation service persists workflow state by `issue_id`.

State is stored under:

- `runs/<issue_id>/issue.json`
  - issue details, resolved repo details, remediation type, validation command
- `runs/<issue_id>/state.json`
  - workflow status, resolution status, review status, latest plan file, errors
- `runs/<issue_id>/plan_v*.md`
  - generated plans
- `runs/<issue_id>/plan.md`
  - approved plan
- `runs/<issue_id>/implementation.json`
  - implementation result
- additional artifacts such as:
  - `git_diff.patch`
  - `head_show.txt`
  - `test_results.json`
  - `change_summary.json`

What this means:

- during initial intake, `issue_id` alone is not enough
- after the issue is created and the repo is approved, later APIs can work using only `issue_id`
- for example, implementation can be triggered later with:
  - `POST /api/issues/{issue_id}/implementation`

because the service reloads the saved issue details and approved plan from disk using that `issue_id`

## Recommended Flow

1. `POST /api/issues`
2. `GET /api/issues/{issue_id}/resolution`
3. `POST /api/issues/{issue_id}/project/approve`
4. `POST /api/issues/{issue_id}/plan`
5. `GET /api/issues/{issue_id}/plan`
6. Optional: `POST /api/issues/{issue_id}/plan/revise`
7. Either:
   - `POST /api/issues/{issue_id}/plan/approve`
   - or `POST /api/issues/{issue_id}/plan/reject`
8. `GET /api/issues/{issue_id}/status`
9. `GET /api/issues/{issue_id}/implementation/summary`
10. `GET /api/issues/{issue_id}/artifacts`
11. Optional human delivery approval:
   - `POST /api/issues/{issue_id}/review/approve`
   - `POST /api/issues/{issue_id}/pr`

## 1. Create Issue With Minimal Input

### Endpoint

`POST /api/issues`

### Purpose

Creates a remediation run from minimal upstream details and resolves candidate repositories using the application name.

### Input

Content type:

`application/json`

Minimum request:

```json
{
  "application_name": "multiagent-support-copilot",
  "issue_id": "SUP-5001",
  "description": "Payload context is dropped during agent handoff."
}
```

Recommended request:

```json
{
  "application_name": "multiagent-support-copilot",
  "issue_id": "SUP-5001",
  "description": "Payload context is dropped during agent handoff.",
  "title": "Escalation payload loses context",
  "acceptance_criteria": [
    "Context is preserved end to end",
    "Validation checks pass"
  ],
  "source_system": "upstream-remediation-ui",
  "source_issue_id": "SUP-5001",
  "source_issue_url": "https://example.local/issues/SUP-5001",
  "remediation_type": "code_change",
  "requested_by": "operator@example.com",
  "environment": "staging"
}
```

Optional repo hints:

- `github_repo`
- `upstream_repo`
- `repo_root`
- `allowed_folder`
- `validation_command`

### Output

Example response:

```json
{
  "ok": true,
  "issue_id": "SUP-5001",
  "status": "PROJECT_REVIEW_PENDING",
  "view_url": "/api/issues/SUP-5001/view",
  "state_url": "/api/issues/SUP-5001/status",
  "recommended_resolution": {
    "project_name": "multiagent-support-copilot",
    "repo_root": "...",
    "allowed_folder": "...",
    "test_command": "pytest -q",
    "base_branch": "main",
    "github_repo": "vinayakram/multiagent-support-copilot",
    "confidence": 1.0,
    "reasoning": "...",
    "source": "config"
  },
  "resolution_candidates": [
    {
      "project_name": "multiagent-support-copilot",
      "repo_root": "...",
      "allowed_folder": "...",
      "test_command": "pytest -q",
      "base_branch": "main",
      "github_repo": "vinayakram/multiagent-support-copilot",
      "confidence": 1.0,
      "reasoning": "...",
      "source": "config"
    }
  ],
  "message": "Resolved application to candidate repositories. Waiting for user approval."
}
```

## 2. Get Resolution Candidates

### Endpoint

`GET /api/issues/{issue_id}/resolution`

### Purpose

Returns the candidate repositories resolved from the application name.

### Input

Path parameter:

- `issue_id`

### Output

Example response:

```json
{
  "issue_id": "SUP-5001",
  "status": "PROJECT_REVIEW_PENDING",
  "resolution_status": "pending",
  "resolution_message": "Resolved application to candidate repositories. Waiting for user approval.",
  "selected_project": {
    "issue_id": "SUP-5001",
    "project_name": "multiagent-support-copilot",
    "title": "Escalation payload loses context",
    "description": "Payload context is dropped during agent handoff."
  },
  "resolution_candidates": [
    {
      "project_name": "multiagent-support-copilot",
      "repo_root": "...",
      "allowed_folder": "...",
      "github_repo": "vinayakram/multiagent-support-copilot"
    }
  ]
}
```

## 3. Approve Project Resolution

### Endpoint

`POST /api/issues/{issue_id}/project/approve`

### Purpose

Approves the selected application-to-repository mapping. After this, the remediation service has enough stored detail to generate a plan without asking upstream again.

### Input

Content type:

`application/json`

Example request:

```json
{
  "project_name": "multiagent-support-copilot"
}
```

Optional override request:

```json
{
  "project_name": "multiagent-support-copilot",
  "repo_root": "/repos/multiagent-support-copilot",
  "allowed_folder": "/repos/multiagent-support-copilot/src/support_copilot",
  "github_repo": "vinayakram/multiagent-support-copilot",
  "base_branch": "main",
  "validation_command": "pytest -q"
}
```

### Output

Example response:

```json
{
  "ok": true,
  "issue_id": "SUP-5001",
  "status": "PROJECT_APPROVED",
  "message": "Project resolution approved.",
  "issue": {
    "issue_id": "SUP-5001",
    "project_name": "multiagent-support-copilot",
    "repo_root": "...",
    "allowed_folder": "...",
    "base_branch": "main",
    "validation_command": "pytest -q"
  }
}
```

## 4. Reject Project Resolution

### Endpoint

`POST /api/issues/{issue_id}/project/reject`

### Purpose

Rejects the resolved repository mapping.

Use this when the user says the recommended repository is not the right one.

### Input

Content type:

`application/json`

Example request:

```json
{
  "reason": "Resolved repo is incorrect."
}
```

### Output

Example response:

```json
{
  "ok": true,
  "issue_id": "SUP-5001",
  "status": "PROJECT_REVIEW_PENDING",
  "message": "Resolved repo is incorrect."
}
```

### What The Upstream App Should Do Next

After rejection, the upstream app should call:

`GET /api/issues/{issue_id}/resolution`

and show the `resolution_candidates` list to the user.

That list is the familiar repository list currently available to choose from.

If none of the candidates are correct, the upstream app can send an explicit override through:

`POST /api/issues/{issue_id}/project/approve`

with:

- `repo_root`
- `allowed_folder`
- `github_repo`
- `base_branch`
- `validation_command`

This allows the user to approve a repo outside the top suggested candidate.

## 5. Generate Plan

### Endpoint

`POST /api/issues/{issue_id}/plan`

### Purpose

Starts plan generation using the already stored issue and approved repository details.

### Input

Path parameter:

- `issue_id`

No additional upstream issue details are required after project approval.

### Output

Example response:

```json
{
  "ok": true,
  "message": "Plan started in background."
}
```

## 6. Get Plan

### Endpoint

`GET /api/issues/{issue_id}/plan`

### Purpose

Returns the latest generated plan text.

### Input

Path parameter:

- `issue_id`

### Output

Example response:

```json
{
  "issue_id": "SUP-5001",
  "status": "PLAN_DRAFTED",
  "plan_text": "# Plan for SUP-5001\n...",
  "approved": false,
  "latest_plan_file": "plan_v1.md"
}
```

## 7. Revise Plan

### Endpoint

`POST /api/issues/{issue_id}/plan/revise`

### Purpose

Generates a new plan using review comments.

### Input

Content type:

`application/json`

Example request:

```json
{
  "review_comments": "Keep the plan shorter and focus only on the affected runtime path."
}
```

### Output

Example response:

```json
{
  "ok": true,
  "message": "Plan revision started in background."
}
```

## 8. Approve Plan

### Endpoint

`POST /api/issues/{issue_id}/plan/approve`

### Purpose

Approves the plan and immediately starts implementation in the background.

### Input

Path parameter:

- `issue_id`

### Output

Example response:

```json
{
  "ok": true,
  "message": "Plan approved. Implementation started in background."
}
```

## 9. Reject Plan

### Endpoint

`POST /api/issues/{issue_id}/plan/reject`

### Purpose

Marks the plan as rejected and returns the rejection message.

### Input

Content type:

`application/json`

Example request:

```json
{
  "reason": "Plan is too broad."
}
```

### Output

Example response:

```json
{
  "ok": true,
  "issue_id": "SUP-5001",
  "status": "PLAN_REJECTED",
  "message": "Plan rejected",
  "details": "Plan is too broad."
}
```

## 10. Get Status

### Endpoint

`GET /api/issues/{issue_id}/status`

### Purpose

Returns the current workflow status, including implementation issues if any occur.

### Input

Path parameter:

- `issue_id`

### Output

Example response:

```json
{
  "issue_id": "SUP-5001",
  "status": "IMPLEMENTATION_RUNNING",
  "job_phase": "testing",
  "review_status": "pending",
  "resolution_status": "approved",
  "resolution_message": "Project and repository approved by user.",
  "job_error": "",
  "pr_url": "",
  "current_screen": "implementation"
}
```

## 11. Start Implementation Manually

### Endpoint

`POST /api/issues/{issue_id}/implementation`

### Purpose

Starts implementation in the background using the already approved plan and stored issue details.

Normally this is triggered automatically when the plan is approved through:

- `POST /api/issues/{issue_id}/plan/approve`

This API exists for cases where implementation must be started manually or retried explicitly.

### Prerequisites

These must already be true:

1. Project resolution is approved.
2. A plan has already been generated.
3. The plan has already been approved.
4. Repository details are already stored for the issue.

### Input

Path parameter:

- `issue_id`

No request body is required.

### Output

Example response:

```json
{
  "ok": true,
  "message": "Implementation started in background."
}
```

## 12. Get Implementation Summary

### Endpoint

`GET /api/issues/{issue_id}/implementation/summary`

### Purpose

Returns the implementation summary, test results, diff, and other details after implementation or in failure cases.

### Input

Path parameter:

- `issue_id`

### Output

Example response:

```json
{
  "issue_id": "SUP-5001",
  "status": "REVIEW_PENDING",
  "job_phase": "completed",
  "job_error": "",
  "review_status": "pending",
  "change_summary": {
    "summary": "...",
    "files_changed": [
      "src/support_copilot/handoff.py"
    ]
  },
  "implementation_result": "{...}",
  "head_show_text": "...",
  "git_diff_text": "...",
  "test_results": [
    {
      "command": "pytest -q",
      "return_code": 0,
      "stdout": "...",
      "stderr": ""
    }
  ]
}
```

## 13. Get Artifacts

### Endpoint

`GET /api/issues/{issue_id}/artifacts`

### Purpose

Returns the artifact list for the issue.

### Input

Path parameter:

- `issue_id`

### Output

Example response:

```json
{
  "issue_id": "SUP-5001",
  "artifacts": [
    {
      "name": "plan.md",
      "path": "...",
      "modified_at": 1770000000.0
    },
    {
      "name": "git_diff.patch",
      "path": "...",
      "modified_at": 1770000005.0
    }
  ]
}
```

## 14. Get Full Issue State

### Endpoint

`GET /api/issues/{issue_id}`

### Purpose

Returns the full saved state including plan text, review state, artifacts metadata, and logs.

### Input

Path parameter:

- `issue_id`

### Output

Full workflow state as JSON.

## 15. Review Approve

### Endpoint

`POST /api/issues/{issue_id}/review/approve`

### Purpose

Human delivery approval after checking the implementation summary and diff.

### Input

Content type:

`application/json`

Example request:

```json
{
  "review_notes": "Diff and validation look good."
}
```

### Output

Example response:

```json
{
  "ok": true,
  "issue_id": "SUP-5001",
  "review_status": "approved"
}
```

## 16. Review Reject

### Endpoint

`POST /api/issues/{issue_id}/review/reject`

### Purpose

Rejects the implemented change after review.

### Input

Content type:

`application/json`

Example request:

```json
{
  "review_notes": "Implementation is not acceptable. Reduce scope."
}
```

### Output

Example response:

```json
{
  "ok": true,
  "issue_id": "SUP-5001",
  "review_status": "changes_requested"
}
```

## 17. Push Branch Or Create PR

### Endpoint

`POST /api/issues/{issue_id}/pr`

### Purpose

Pushes the branch and creates the PR or compare link.

This is allowed only after review approval.

### Input

Path parameter:

- `issue_id`

### Output

Example response:

```json
{
  "ok": true,
  "message": "Branch / PR started in background."
}
```

## 18. View Link For Fallback UI

### Endpoint

`GET /api/issues/{issue_id}/view`

### Purpose

Returns the fallback UI deep link. UI is not the main flow, but this can still be used if needed.

### Input

Path parameter:

- `issue_id`

### Output

```json
{
  "issue_id": "SUP-5001",
  "view_url": "/?issue_id=SUP-5001"
}
```

## Important Notes

1. `issue_id` alone is not enough to fetch business details unless a source-system connector is added later.
2. Today, the upstream app should send the minimal issue payload during `POST /api/issues`.
3. After project approval, later plan and implementation APIs reuse the stored issue details and do not ask upstream again.
4. If implementation fails, the error is available in:
   - `GET /api/issues/{issue_id}/status`
   - `GET /api/issues/{issue_id}/implementation/summary`
