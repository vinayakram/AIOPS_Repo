# Product Overview

## What It Is

AIOPS_Repo is a proof-of-concept for an agentic operations assistant. It connects application telemetry, incident detection, RCA, remediation planning, human approval, and implementation tracking into one operational workflow.

The goal is not to replace existing observability tools. The goal is to reduce the gap between "an alert happened" and "a safe, reviewed action is ready."

## Who It Is For

- SRE and platform teams handling recurring service incidents.
- Application operations teams that need faster triage and safer remediation.
- Engineering teams that want traceable AI-assisted fixes rather than opaque automation.
- Demo and solution teams validating agentic AIOps workflows with real service hooks.

## Core User Story

An operations user asks: "Is there an issue right now?"

The system should answer with:

1. What was detected.
2. Which service is affected.
3. Why the issue matters.
4. Which agent is being invoked.
5. What evidence supports the root cause.
6. What action is recommended.
7. Whether human approval is required.
8. What happened after the action was executed.

## End-to-End Flow

```text
Telemetry ingestion
  -> Issue detection
  -> RCA agent investigation
  -> Recommendation
  -> Human approval
  -> Remediation execution
  -> Pull request / change artifact
  -> Tracking and learning
```

## Main Capabilities

- Captures traces, errors, latency, and service health signals.
- Raises operational issues from telemetry and threshold rules.
- Runs RCA using the investigation service.
- Shows issue status, severity, evidence, and recommendations.
- Sends approved incidents into a remediation workflow.
- Produces implementation artifacts suitable for review.
- Keeps local demo scripts for repeatable end-to-end validation.

## Product Differentiation

Traditional observability tools are strong at surfacing symptoms. This POC focuses on the next step: agent-assisted operational resolution.

Key differentiators:

- Incident-to-action workflow, not only dashboards.
- Explicit agent handoffs from detection to RCA to remediation.
- Human approval before sensitive changes.
- Traceable recommendations with evidence.
- Reusable incident and remediation history as the product matures.

## Current Modules

- `AIopsTelemetry` detects and displays operational issues.
- `Invastigate_flow_with_Poller` performs RCA and correlation.
- `AIOPS` handles remediation planning and implementation flow.
- `MedicalAgent` provides the monitored sample workload.
- `SampleAgent_GitHub` is the remediation target copy used for GitHub workflows.
- `demo` contains the local end-to-end launcher and preview experience.

## Demo Scenario

The primary demo creates pressure on the sample agent, causing an availability issue. The telemetry service detects the issue, the RCA service identifies likely resource pressure, and the remediation flow prepares an action for review.

Default local preview:

```text
http://localhost:8088/aiops_preview.html
```

## Product Principles

- Make the issue understandable in the first few seconds.
- Show the agent story clearly: detection, analysis, recommendation, approval, action.
- Prefer explicit evidence over generic AI summaries.
- Keep human approval gates for risky operations.
- Track outcomes so successful fixes can be reused.
- Keep demo flows connected to real service hooks, not only static screens.

## Near-Term Roadmap

- Add a conversational Crystal-style entry point for daily operations.
- Store incident, action, approval, and outcome history as reusable knowledge.
- Improve Japanese operational copy with native-user review.
- Add measurable product metrics: noise reduction, RCA accuracy, remediation success rate, and engagement.
- Strengthen audit views for every AI decision and remediation action.
