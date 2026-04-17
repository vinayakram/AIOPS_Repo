# Upstream Developer Handoff

## What You Need To Do

When the user clicks `AI Remediation` in your application, call this remediation service:

`POST /api/issues`

Send:

- issue id
- title
- description
- acceptance criteria
- project/application name
- remediation type
- repo details from your layer if available

## Minimum Example

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
  "validation_command": "pytest -q"
}
```

## What You Get Back

The service returns:

- `issue_id`
- `status`
- `view_url`
- `state_url`

Use them like this:

- poll `state_url` for progress
- open `view_url` when a human needs to review plan or diff

## Main Lifecycle Calls

1. Create run:
   - `POST /api/issues`
2. Start planning:
   - `POST /api/issues/{issue_id}/plan`
3. Approve plan:
   - `POST /api/issues/{issue_id}/plan/approve`
4. Start implementation:
   - `POST /api/issues/{issue_id}/implementation`
5. Review evidence:
   - `GET /api/issues/{issue_id}/diff`
   - `GET /api/issues/{issue_id}/tests`
6. Human approves generated change:
   - `POST /api/issues/{issue_id}/review/approve`
7. Create branch / PR:
   - `POST /api/issues/{issue_id}/pr`

## Important Human Checks

These are mandatory:

1. Plan approval before implementation.
2. Human review of diff and validation output after implementation.
3. Branch push / PR only after review approval.

If review is not approved, `/pr` is blocked.

## Repo Resolution Behavior

This service can resolve the target repo from:

- configured project map entries
- `github_repo`
- `upstream_repo`
- direct repo path if your environment already mounts it

So if your agent layer already knows the repo, send it.

## Which Doc To Read If You Need More

For full details, see:

- [UPSTREAM_INTEGRATION_CONTRACT.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\UPSTREAM_INTEGRATION_CONTRACT.md)
