# Production Use Case: Concurrent User Load Degradation

## Purpose

This use case evolves the POC from a code-defect demo into a production-style
incident flow:

1. A real application receives concurrent user traffic.
2. Response time increases or requests fail.
3. A Sev ticket is raised from telemetry and NFR rules.
4. AI agents assist with RCA across traces, logs, metrics, and recent changes.
5. The remediation engine proposes the safest fix path.
6. Codex either prepares a PR or produces an operator handoff plan when infra
   permissions are not available.

## Scenario

Application:

- `MedicalAgent`, the medical RAG application.

Incident:

- 75 to 200 concurrent users hit the chat/query endpoint.
- The application becomes slow or intermittently unavailable.
- Example symptoms:
  - p95 latency exceeds 5 seconds for 5 minutes.
  - HTTP 5xx rate exceeds 2 percent.
  - worker queue length grows continuously.
  - CPU is saturated or DB connections are exhausted.
  - LLM/RAG calls are serialized behind a constrained worker pool.

Severity:

- Sev-2 if degraded service continues but partial traffic succeeds.
- Sev-1 if the app cannot serve requests for most users.

## Detection Flow

1. `MedicalAgent` emits traces and spans through `aiops_sdk`.
2. `AIopsTelemetry` collects:
   - request latency
   - error rate
   - active request count
   - system CPU and memory
   - trace-level failures
   - downstream LLM/RAG latency
3. The issue detector evaluates NFR rules such as:
   - p95 latency threshold breached
   - error-rate threshold breached
   - repeated timeout pattern
   - resource saturation
   - downstream dependency slowdown
4. The escalation engine creates a Sev ticket and links:
   - affected service
   - start time
   - impact summary
   - candidate traces
   - metric windows
   - detected NFR rule

## AI Agent RCA Flow

The RCA should be multi-agent or multi-step, even if implemented in one service:

- Signal correlation agent:
  - groups slow traces by endpoint, user path, and time window
  - separates app-level latency from dependency latency
- Metrics analysis agent:
  - checks CPU, memory, worker count, DB connection saturation, and queue growth
  - identifies whether the issue is capacity, contention, or dependency wait
- Code/config reasoning agent:
  - inspects service config, worker settings, timeout values, connection pool sizes,
    async/sync bottlenecks, and recent commits
- Remediation selection agent:
  - classifies the fix as `code_change`, `config_change`, `infra_change`,
    `runbook_change`, or `human_handoff`
  - chooses the lowest-risk action that directly addresses the RCA

Example RCA outcomes:

- App server has too few workers for expected concurrency.
- DB connection pool is smaller than the concurrent request load.
- RAG embedding or LLM calls are blocking the request path without timeout/circuit
  breaker protection.
- Kubernetes CPU limits are too low and cause throttling.
- Autoscaling is disabled or has thresholds that react too late.
- A recent config change lowered request timeout or worker count.

## Remediation Paths

### Config Change

Use when the fix is safe to express in repository-managed configuration.

Examples:

- increase app worker count in a service config
- tune DB connection pool size
- increase request timeout within approved limits
- enable bounded concurrency or queue limits
- add a circuit breaker threshold

Codex output:

- branch with config/code changes
- validation results
- PR with summary, RCA evidence, and rollback guidance

### Infra Change

Use when the fix requires infrastructure access.

Examples:

- scale Kubernetes deployment replicas
- update HPA min/max replicas
- change CPU/memory requests or limits
- resize VM/container instance class
- update load balancer or ingress timeout
- alter managed database capacity

Codex behavior:

- if IaC files are available and editable, prepare a PR against the IaC repo
- if live infra access is unavailable, generate a human-executable plan with:
  - exact commands or console steps
  - expected risk
  - validation checks
  - rollback steps
  - ownership and approval notes

### Code Change

Use when the bottleneck is in application behavior.

Examples:

- make blocking work asynchronous
- add timeout and retry policy around downstream calls
- add request-level caching for repeated retrieval work
- move expensive work to background jobs
- fix unbounded memory or request fan-out

Codex output:

- patch in the application repo
- targeted tests
- PR with RCA-linked explanation

### Investigation Only Or Human Handoff

Use when the system cannot safely determine or apply the change.

Output:

- RCA report
- suspected causes ranked by confidence
- required access or data
- recommended next diagnostic steps
- operational runbook

## Demo Script

1. Start `MedicalAgent`, `AIopsTelemetry`, and the remediation service.
2. Generate load against the chat/query endpoint with a load tool such as `k6`,
   `hey`, `ab`, or a small Python concurrent request script.
3. Show the dashboard detecting elevated latency/error rate.
4. Show the Sev ticket created from the NFR rule.
5. Open RCA:
   - latency trend
   - affected endpoint
   - slow trace samples
   - resource metrics
   - likely bottleneck
6. Click `AI Remediation`.
7. Review the generated plan:
   - RCA summary
   - remediation type
   - proposed change
   - validation and rollback
8. Approve one of two endings:
   - repo-managed config/code change creates a PR
   - infra-only fix creates an operator handoff plan when Codex lacks access

## Acceptance Criteria

- The incident starts from production-like concurrent traffic, not manual defect
  entry alone.
- A Sev issue is raised from telemetry evidence.
- RCA cites metrics, traces, and code/config context.
- Remediation type is explicit.
- For repo-manageable changes, the flow creates a branch/PR.
- For infra changes without permission, the flow produces clear execution steps,
  validation checks, and rollback instructions.
- The human approval gate remains before any push, PR, or handoff execution.

## Recommended Implementation Increments

1. Add a load-generation script and demo issue seed.
2. Add NFR detection rules for p95 latency and 5xx rate.
3. Add RCA prompt/context assembly for traces plus system metrics.
4. Add remediation-type classification in the plan output.
5. Add infra handoff artifact generation.
6. Add one repo-managed config remediation path and one infra-handoff path to
   demonstrate both outcomes.
