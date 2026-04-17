# Remediation POC - Stack, Scope, and Delivery Notes

## Frontend Stack
- `FastAPI` serves the web application.
- `Jinja2` renders the main multi-screen UI from [`templates/index.html`](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\templates\index.html).
- `Vanilla JavaScript` in the HTML handles screen switching, polling, action triggers, and history loading.
- `CSS` in [`static/style.css`](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\static\style.css) provides the workflow cards, plan panels, implementation summary, and PR status presentation.

## Backend Stack
- `Python` is the main application runtime.
- `FastAPI` in [`app/web.py`](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\app\web.py) exposes the UI routes and background job orchestration.
- `Pydantic` models in [`core/schemas.py`](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\core\schemas.py) define issue, test, implementation, and PR data contracts.
- `Settings/config` are centralized in [`core/settings.py`](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\core\settings.py).
- `Service layer` under [`services`](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\services) handles project resolution, plan generation, implementation orchestration, repo actions, testing, storage, and optional GitHub/RCA hooks.

## Execution and Integration Stack
- `Codex CLI` is the implementation and planning engine.
- `Git` is the source-control execution path for branch preparation, commit, push, and PR handoff.
- `Repo-local test commands` are used for verification after implementation.
- `Local filesystem artifacts` under `runs/<issue-id>` provide traceability and auditability.
- `GitHub integration` is supported through branch push, PR creation, or compare-link fallback.

## What Is In Scope In This POC
- Manual issue intake with minimal fields.
- Project resolution from application name using configured project mappings.
- Plan generation, revision, approval, and rejection workflow.
- Human-in-the-loop approval before code changes.
- Bounded implementation using Codex with a demo-fast-path and watchdog protection.
- Issue-relevant validation using the configured test command.
- Controlled branch creation, push, and PR or compare-link creation after validation.
- Artifact capture for plan versions, logs, summaries, test results, and PR metadata.
- Demo scenarios using seeded multi-agent application defects such as timeout, handoff, and OOM-style failures.

## What Is Currently Out of Scope
- Automatic issue ingestion from all external systems by default.
- Deep environment provisioning across real customer infrastructure.
- Secret manager integration, vault rotation, and enterprise credential brokering.
- Full database lifecycle management for integration tests across arbitrary customer estates.
- End-to-end deployment execution into production environments.
- Multi-repo orchestration, rollback management, and change management approval systems.
- Rich observability integrations such as Datadog, Splunk, Sentry, or PagerDuty as first-class product features.

## Production Use Case Requested After Demo Review
- Keep the existing remediation workflow because the reviewers accepted the core flow.
- Add a real-life production scenario where concurrent users access an application, causing slow responses or failed loads.
- Raise a Sev ticket from telemetry/NFR evidence instead of relying only on manual issue entry.
- Use AI-assisted RCA over traces, logs, metrics, and code/config context.
- Classify remediation as code, config, infra, runbook, investigation-only, or human handoff.
- For repo-manageable code/config/IaC changes, Codex should prepare a branch, validation evidence, and PR.
- For live infra changes where Codex lacks permission, Codex should generate an operator plan with exact steps, validation checks, and rollback guidance.

Detailed design: [`docs/PRODUCTION_LOAD_DEGRADATION_USE_CASE.md`](docs/PRODUCTION_LOAD_DEGRADATION_USE_CASE.md).

## What Can Potentially Be Covered For An Infrastructure-Independent Remediation AI
- Intake from multiple sources:
  - GitHub issues
  - AI-Ops consoles
  - incident systems
  - logs and alerts
- Dynamic project resolution using:
  - service catalogs
  - repo metadata
  - ownership mappings
  - labels and runtime identifiers
- Environment-aware execution adapters for:
  - standalone applications
  - Dockerized services
  - Kubernetes workloads
  - VM-based or bare-metal systems
- Validation adapters for:
  - unit tests
  - integration tests
  - DB-backed tests
  - container build checks
  - deployment safety checks
- Customer-specific CI/CD handoff patterns such as:
  - GitHub Actions
  - Azure DevOps
  - GitLab CI
  - Jenkins
  - custom internal pipelines
- Secret and configuration handling through customer-approved practices such as:
  - environment variables
  - mounted config files
  - secret managers
  - staging-only credentials
- Policy-based remediation boundaries such as:
  - allowed folders
  - approved commands
  - review gates
  - environment restrictions

## How Human In Loop Helps
- Prevents unauthorized or poorly scoped code changes.
- Ensures the plan is reviewed in business language before implementation starts.
- Allows reviewer comments to narrow or redirect the remediation approach.
- Reduces risk when issues have ambiguity around ownership, severity, or acceptance criteria.
- Provides an explicit approval checkpoint before code modification and PR creation.
- Improves auditability by capturing review intent alongside the final implementation.
- Makes the system easier to trust in regulated or production-sensitive environments.

## Recommended Positioning For This POC
- This POC is best presented as a controlled remediation workflow rather than a fully autonomous production remediator.
- The strongest story is:
  - production-like incident signal from telemetry or minimal intake fallback
  - correct project identification
  - AI-assisted RCA
  - concise human-reviewed plan
  - explicit remediation type selection
  - bounded Codex implementation or infra handoff plan
  - issue-relevant verification
  - branch and PR generation with artifacts
- The natural next step is to replace more static project and environment assumptions with pluggable adapters, while keeping the human approval gate in place.
