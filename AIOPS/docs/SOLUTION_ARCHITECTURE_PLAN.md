# Codex Native MVP Evolution Plan

## Objective

Evolve the current remediation POC into a deployable Linux VM package that:

- reduces manual input at issue intake
- lets reviewers inspect the proposed or actual code changes before push/PR
- supports non-code remediation paths such as infra or config changes
- exposes the workflow end-to-end as API operations
- can be deployed with a fresh Codex API key and environment-specific configuration
- supports a production-like concurrent-load incident where telemetry raises a
  Sev ticket, AI agents assist RCA, and remediation becomes either a PR or an
  operator handoff plan

## Upstream Integration Assumption

This remediation system is not the first user-facing layer in the target architecture.

An upstream application owned by another team or developer already exists and will trigger this system when a user clicks `AI Remediation`.

That means the target operating model is:

- upstream platform owns issue discovery and initial user interaction
- this system owns remediation orchestration
- manual entry in this UI becomes a fallback mode, not the primary path

Primary invocation path:

1. User clicks `AI Remediation` in the upstream application.
2. Upstream application sends a structured remediation request to this system.
3. This system creates an issue-scoped workflow run.
4. This system returns:
   - internal remediation `issue_id`
   - workflow status
   - optional deep link into the remediation UI
   - optional artifact/status endpoints
5. Upstream application either:
   - redirects the user into this system for review and approval, or
   - continues driving the workflow entirely through APIs

## System Role In The Overall Platform

In the target architecture, this project should act as a downstream remediation engine.

It should provide:

- normalized API intake
- project and target resolution
- remediation type routing
- Codex-driven plan and implementation orchestration
- review artifacts
- PR or handoff generation

It should not depend on manual typing for normal production usage when an upstream platform is already available.

## Current State Summary

The existing application is already a thin FastAPI orchestrator with persisted run artifacts.

Current strengths:

- FastAPI-based UI and API entrypoint in [app/web.py](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\app\web.py)
- persisted workflow state and artifacts under `runs/<issue_id>/`
- plan generation and implementation orchestration through Codex CLI
- implementation review artifacts already captured:
  - `git_diff.patch`
  - `head_show.txt`
  - `test_results.json`
  - `change_summary.json`
- background-job oriented flow that can be extended into API-first automation
- config-driven project routing through `config/project_map.json`

Current limitations:

- issue intake is manual by design in the first screen
- project resolution is matcher-driven only; there is no issue-system ingestion
- preview of code changes exists after implementation, not before implementation approval
- workflow assumes code remediation and a git/PR destination
- state management is single-session and file-based, not multi-tenant or API-product ready
- deployment guidance exists in README, but there is no hardened Linux packaging bundle

## Review Comment Responses

### 0. How should the demo become more production-realistic?

Reviewer feedback:

- the existing flow is good
- the next use case should look like a production incident, not only a seeded code defect
- concurrent users should make an application slow or unavailable
- telemetry should raise a Sev ticket
- AI agents should assist RCA and select a suitable remediation path
- remediation may be code, config, infra, or human handoff

Target response:

- add a concurrent-load degradation scenario using `SampleAgent`
- detect the incident through latency, error-rate, saturation, and trace evidence
- route the issue into remediation with RCA context already attached
- classify the remediation type before implementation
- create a PR when the change is repo-managed
- produce precise operator steps when the change requires live infra permissions

Detailed design:

- [`PRODUCTION_LOAD_DEGRADATION_USE_CASE.md`](PRODUCTION_LOAD_DEGRADATION_USE_CASE.md)

### 1. Why is the input manual in the first screen?

Reason in current MVP:

- the current POC intentionally starts with minimal manual issue intake
- this keeps the demo deterministic and avoids coupling to Jira, ServiceNow, GitHub Issues, or internal ticketing during early validation
- it also ensures the operator explicitly confirms the target repo and scope before Codex is invoked

Architectural recommendation:

- keep manual mode as a fallback
- add assisted intake and API-driven intake as first-class paths
- make upstream API intake the default production path

Target modes:

- Manual intake: current flow, retained for demos and exceptions
- Upstream application handoff: primary production path triggered by `AI Remediation`
- Ticket URL / ID intake: fetch issue metadata from an external system
- Webhook intake: issue entry arrives from a source system automatically
- API intake: client posts a normalized issue payload directly

### 2. Is there a way to see the code that changed before making the actual changes?

Current answer:

- partially yes, but only after implementation has run
- the system already stores:
  - git diff
  - HEAD summary
  - test proof
- these are visible in the implementation screen before PR creation

Gap:

- there is no explicit review gate between "Codex edited the repo" and "Create branch and PR"
- there is also no pre-change simulation mode

Architectural recommendation:

- introduce a formal "Review Changes" gate
- split the current implementation stage into:
  - plan approved
  - draft implementation generated in working branch
  - human review of diff and tests
  - approve for push/PR

Optional future enhancement:

- add a dry-run planning mode that predicts likely files/components before editing
- this should be presented as an impact estimate, not as a guaranteed diff

### 3. What if the required change is not code, but infra or some other change?

Current answer:

- the present workflow is optimized for code changes in a git repository
- Codex is currently instructed to work within an allowed folder and validate with tests
- non-code tasks are not modeled explicitly

Risk if unchanged:

- infra or operational issues may be forced into a code-fix path that is not appropriate
- the system may create low-value code changes when the right output should be:
  - runbook update
  - Terraform/Helm/Kubernetes config change
  - CI/CD pipeline update
  - operational handoff or escalation

Architectural recommendation:

- introduce remediation type classification up front

Proposed remediation types:

- `code_change`
- `infra_change`
- `config_change`
- `runbook_change`
- `investigation_only`
- `human_handoff`

Behavior by type:

- `code_change`: existing git/plan/implement/test/PR workflow
- `infra_change`: route to infra repo or IaC workspace, run infra validation commands, open infra PR
- `config_change`: route to config repo or managed config artifact workflow
- `runbook_change`: generate documentation/update artifacts and optional PR
- `investigation_only`: produce RCA and recommended action, no repo mutation
- `human_handoff`: create an actionable handoff package instead of attempting automation

### 4. Is it possible to expose the endpoints of this project right from issue entry to exit as API calls?

Current answer:

- partially yes
- the app already exposes:
  - `GET /api/state`
  - `POST /api/reset-session`
  - `GET /api/projects/suggest`
  - `POST /api/projects/select`
- core workflow actions like save issue, plan, approve, implement, and PR creation are still mixed between UI-oriented and API-oriented routes

Architectural recommendation:

- make the platform API-first, with the UI acting as one client
- treat the upstream application as the primary API client

Target API surface:

- `POST /api/issues`
- `GET /api/issues/{issue_id}`
- `GET /api/issues/{issue_id}/view`
- `POST /api/issues/{issue_id}/resolve-project`
- `POST /api/issues/{issue_id}/plan`
- `POST /api/issues/{issue_id}/plan/revise`
- `POST /api/issues/{issue_id}/plan/approve`
- `POST /api/issues/{issue_id}/implementation`
- `GET /api/issues/{issue_id}/artifacts`
- `GET /api/issues/{issue_id}/diff`
- `GET /api/issues/{issue_id}/tests`
- `POST /api/issues/{issue_id}/review/approve`
- `POST /api/issues/{issue_id}/review/reject`
- `POST /api/issues/{issue_id}/pr`
- `GET /api/issues/{issue_id}/status`

Design principle:

- every UI action should map to a stable API operation
- workflow state should be issue-scoped, not global singleton state
- the upstream platform should be able to launch, track, and optionally control the full remediation lifecycle

### 5. Can this be deployed anywhere?

Current answer:

- conceptually yes, but operationally not yet hardened
- the current app is portable in principle because it uses Python, FastAPI, git, environment variables, and Codex CLI
- however, packaging, service startup, secrets handling, Linux-specific setup, and dependency bootstrap need productization

Architectural recommendation:

Supported deployment targets after hardening:

- Linux VM
- container image
- internal jump host or bastion
- CI runner or automation worker
- Kubernetes deployment

Target packaging baseline:

- Linux-first deployment package
- `.env.example` with Linux defaults
- startup script
- systemd unit file
- optional Dockerfile
- health endpoint
- artifact retention controls

## Target Architecture

### 1. Intake Layer

Introduce three intake paths:

- Upstream application handoff
- Manual UI intake
- External issue ingestion connector
- Public/internal REST API intake

Normalized issue model should expand to include:

- source request id
- source system
- source issue URL
- source issue id
- remediation type
- environment
- approval requirements
- repository or target system reference
- validation strategy

Recommended request fields from the upstream layer:

- `source_system`
- `source_issue_id`
- `source_issue_url`
- `application_name`
- `title`
- `description`
- `acceptance_criteria`
- `remediation_type`
- `repo_root` or logical target identifier
- `allowed_scope`
- `requested_by`
- `environment`
- `metadata`

### 2. Orchestration Layer

Refactor the current single-file route orchestration into explicit services:

- Intake Service
- Project Resolution Service
- Remediation Classification Service
- Planning Service
- Execution Service
- Review Service
- Delivery Service
- Artifact Service

The existing services can be evolved rather than replaced.

### 3. Execution Routing

Introduce a remediation router that chooses a pathway based on remediation type:

- Code path
- Infra path
- Config path
- Investigation path
- Handoff path

Each path defines:

- target workspace
- allowed mutation scope
- validation command set
- approval gate behavior
- output artifact contract

### 4. Review and Approval Layer

Add an explicit approval checkpoint after implementation artifacts are produced.

Required review artifacts:

- diff summary
- full diff
- changed files
- validation results
- Codex summary
- repo and branch metadata

Decision actions:

- approve for push/PR
- reject and request revision
- convert to handoff
- stop without change

### 5. API Layer

Promote issue-scoped REST endpoints as the system contract.

API principles:

- idempotent reads
- asynchronous job submission for long-running operations
- issue-level status tracking
- artifact retrieval URLs
- audit-friendly workflow events

Recommended additional endpoints:

- `GET /health`
- `GET /api/capabilities`
- `GET /api/projects`
- `GET /api/remediation-types`

Recommended upstream integration endpoints:

- `POST /api/issues`
  - create a remediation workflow from the upstream `AI Remediation` action
- `GET /api/issues/{issue_id}/status`
  - poll current state from the upstream platform
- `GET /api/issues/{issue_id}/view`
  - return a UI deep-link target for human review
- `GET /api/issues/{issue_id}/artifacts`
  - expose plan, diff, test, and PR outputs back to the upstream platform

### 6. State and Persistence Layer

Current state is held in `runs/current_state.json`, which is not suitable for concurrent runs.

Recommended evolution:

- keep artifact files under `runs/<issue_id>/`
- replace global state with issue-scoped state records
- move workflow metadata into SQLite first, then optionally Postgres

Suggested persisted entities:

- issues
- workflow_runs
- workflow_events
- artifacts
- approvals
- deployment_targets

### 7. Deployment Layer

Linux VM package should include:

- Python runtime dependencies
- Codex CLI installation instructions
- env-based Codex authentication
- uvicorn or gunicorn startup
- systemd service definition
- Nginx reverse proxy option
- writable artifact directory
- managed repo workspace directory

## Linux VM Deployment Architecture

### Runtime assumptions

- Ubuntu or RHEL-compatible Linux VM
- outbound network access to:
  - OpenAI/Codex endpoint path used by Codex CLI
  - git host such as GitHub or internal Git server
- git installed
- Node.js installed for Codex CLI
- Python 3.11+

### Secret model

Required secrets:

- Codex API key
- optional GitHub token
- optional issue-system connector credentials

Recommended handling:

- store secrets in environment variables or VM secret manager injection
- do not hardcode tokens into project files
- support rotation without redeploy

### New Linux-friendly environment variables

- `CODEX_API_KEY`
- `CODEX_COMMAND`
- `CODEX_MODEL`
- `RUNS_DIR`
- `MANAGED_REPOS_DIR`
- `DATABASE_URL`
- `APP_HOST`
- `APP_PORT`
- `LOG_LEVEL`
- `GITHUB_TOKEN`

### Packaging outputs to produce next

- deployable source bundle
- Linux `bootstrap.sh`
- Linux `start.sh`
- `systemd/codex-remediation.service`
- optional `docker/Dockerfile`
- deployment README

## Proposed Change Backlog

### Phase 1: Productize current code path

- make workflow fully API-addressable for upstream-system handoff
- replace global state with issue-scoped state
- add explicit review gate before push/PR
- keep manual intake and add structured upstream/API intake
- retain existing code remediation flow

### Phase 2: Support non-code remediation types

- add remediation type field and classifier
- add execution routing by change type
- add validation strategies per type
- add non-code output contracts

### Phase 3: Linux deployment package

- Linux scripts and service files
- path normalization cleanup for cross-platform support
- environment variable hardening
- health checks and startup validation

### Phase 4: Connector and platform hardening

- GitHub Issues, Jira, or ServiceNow intake connectors
- auth and RBAC
- audit logs
- multi-user concurrency support

## Code-Level Impact Areas

Primary files likely to change:

- [app/web.py](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\app\web.py)
- [core/schemas.py](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\core\schemas.py)
- [core/settings.py](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\core\settings.py)
- [services/implementation_service.py](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\services\implementation_service.py)
- [services/repo_service.py](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\services\repo_service.py)
- [services/storage.py](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\services\storage.py)
- [templates/index.html](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\templates\index.html)

New modules recommended:

- `services/intake_service.py`
- `services/remediation_router.py`
- `services/review_service.py`
- `services/workflow_service.py`
- `services/deployment_validation_service.py`
- `api/routes/issues.py`
- `api/routes/workflows.py`

## Key Risks

- current global state prevents safe concurrent issue processing
- Windows-oriented prompts and path assumptions will break Linux portability
- infra remediation without a clear scope model can be unsafe
- exposing APIs without auth will make the platform risky to deploy
- preview-before-change cannot be perfect if interpreted as showing an exact diff before edits exist

## Recommended Implementation Sequence

1. Add the upstream handoff contract for the `AI Remediation` trigger.
2. Refactor the workflow to be issue-scoped and API-first.
3. Add deep-link support so the upstream system can route users into a specific remediation run.
4. Add the explicit review gate between implementation and PR creation.
5. Introduce remediation type classification and routing.
6. Remove Windows-only assumptions from prompts and repository operations.
7. Add Linux deployment scripts, service definition, and startup validation.
8. Create the new package only after the above minimum platform changes are in place.

## Definition of Done For The Next Package

The next package should only be cut once these are true:

- issue lifecycle is available through stable APIs
- upstream `AI Remediation` can create and track a remediation run through those APIs
- reviewers can inspect diff and test evidence before push/PR
- non-code remediation is explicitly supported or safely handed off
- Linux startup is scripted and documented
- new Codex key can be injected through environment configuration
- the package can run on a clean Linux VM without Windows-specific dependencies
