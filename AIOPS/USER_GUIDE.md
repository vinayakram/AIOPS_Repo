# Codex Native MVP User Guide

This guide explains how to use the current remediation package.

## Operating Model

The primary flow is now API-first.

Normal usage:
- an upstream application sends the issue to this remediation service
- this service resolves the application to a candidate repository
- a user approves the repository choice
- the user generates and reviews a plan
- if the plan is approved, implementation starts automatically
- the upstream application reads status, implementation summary, and artifacts through APIs

Fallback usage:
- the built-in UI still exists, but it is now a fallback path only

## What this project does

The package helps a user or upstream system:
- register an issue with minimal input
- resolve the application to a target repository
- get repository approval from a human
- generate a remediation plan
- revise, approve, or reject the plan
- implement the approved plan with Codex CLI
- review implementation results
- approve or reject delivery
- create a branch and PR after approval

## Minimal upstream input

The upstream application should send at least:
- application name
- issue ID
- issue description

Recommended fields:
- title
- acceptance criteria
- remediation type
- source system metadata
- repo hints such as `github_repo`, `upstream_repo`, or `repo_root`

## Project resolution prerequisite

Project resolution works when at least one of these is true:
- the application name matches an entry in `config/project_map.json`
- the upstream payload contains repository hints

If the user rejects the recommended repository:
- call `GET /api/issues/{issue_id}/resolution`
- show the returned `resolution_candidates`
- let the user approve one of those, or send an explicit repo override

## Issue state and later API calls

The remediation service persists state by `issue_id`.

It stores:
- issue details
- approved repository details
- workflow status
- generated plans
- implementation results
- artifacts

Because of that, later APIs can work with only `issue_id` once the earlier steps are already completed.

Example:
- after project approval and plan approval, `POST /api/issues/{issue_id}/implementation` can run using stored state

## Recommended API flow

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
11. Optional:
   - `POST /api/issues/{issue_id}/review/approve`
   - `POST /api/issues/{issue_id}/pr`

## What happens during project approval

When the user approves the resolved project:
- repo root is stored
- allowed folder is stored
- validation command is stored
- base branch is stored

After that, plan generation does not need the upstream application to resend issue details.

## What happens during plan approval

When the user approves the plan:
- the approved plan is saved
- implementation starts automatically in the background

If the user rejects the plan:
- the service returns `Plan rejected`
- no implementation starts

## What happens during implementation

During implementation, the service:
- prepares a remediation branch
- invokes Codex CLI with the approved plan and stored repo details
- runs validation
- stores implementation summary and artifacts
- waits for human delivery approval before push / PR

## How to read implementation results

Use:
- `GET /api/issues/{issue_id}/status`
- `GET /api/issues/{issue_id}/implementation/summary`

Implementation summary includes:
- change summary
- implementation result payload
- HEAD summary
- git diff text
- validation results
- job error, if implementation had issues

## How to read artifacts

Use:
- `GET /api/issues/{issue_id}/artifacts`

This returns the saved files for the issue, such as:
- plan files
- implementation result
- diff
- validation output
- branch / PR metadata

## Human-in-the-loop checkpoints

The current package enforces these checks:
- project resolution approval before plan generation
- plan approval before implementation
- review approval before branch push / PR

## Setup

```powershell
python -m venv .pyenv
.pyenv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## Run locally

```powershell
python -m uvicorn app.web:app --reload
```

Open:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

## Useful docs

- API integration reference: [docs/API_REFERENCE_FOR_INTEGRATION.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\API_REFERENCE_FOR_INTEGRATION.md)
- upstream contract: [docs/UPSTREAM_INTEGRATION_CONTRACT.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\UPSTREAM_INTEGRATION_CONTRACT.md)
- Linux deployment: [docs/LINUX_VM_DEPLOYMENT_AND_E2E.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\LINUX_VM_DEPLOYMENT_AND_E2E.md)

## Troubleshooting

If project resolution is weak:
- check `config/project_map.json`
- improve the application matchers
- provide repo hints from the upstream application

If implementation fails:
- check `GET /api/issues/{issue_id}/status`
- check `GET /api/issues/{issue_id}/implementation/summary`
- review artifacts in `GET /api/issues/{issue_id}/artifacts`
