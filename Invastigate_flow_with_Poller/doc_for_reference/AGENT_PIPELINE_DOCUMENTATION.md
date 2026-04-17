# Investigation Pipeline — Agent Documentation

A detailed technical reference for all five agents in the multi-agent observability pipeline:
Normalization → Correlation → Analysis → RCA → Recommendations

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Data Sources](#2-data-sources)
3. [Agent 1 — Normalization Agent](#3-agent-1--normalization-agent)
4. [Agent 2 — Correlation Agent](#4-agent-2--correlation-agent)
5. [Agent 3 — Error Analysis Agent](#5-agent-3--error-analysis-agent)
6. [Agent 4 — RCA Agent](#6-agent-4--rca-agent)
7. [Agent 5 — Recommendation Agent](#7-agent-5--recommendation-agent)
8. [Pipeline Data Flow](#8-pipeline-data-flow)
9. [Efficiency & Design Decisions](#9-efficiency--design-decisions)

---

## 1. System Overview

The Investigation Pipeline is a sequential five-agent system designed to automatically diagnose failures in AI agent infrastructure. Each agent has a single, clearly scoped responsibility. No agent overlaps with another.

```
Frontend / Poller
      │
      ▼
┌─────────────────┐
│  Normalization  │  ← fetches raw logs, classifies error type
└────────┬────────┘
         │ NormalizedIncident
         ▼
┌─────────────────┐
│   Correlation   │  ← builds cross-system causal graph
└────────┬────────┘
         │ CorrelationResult + analysis_target
         ▼
┌─────────────────┐
│ Error Analysis  │  ← deep-dives errors, assigns IDs, detects patterns
└────────┬────────┘
         │ ErrorAnalysisResult + rca_target
         ▼
┌─────────────────┐
│      RCA        │  ← determines definitive root cause, builds 5-Why
└────────┬────────┘
         │ RCAResult
         ▼
┌─────────────────┐
│ Recommendation  │  ← synthesises findings into ranked action plan
└─────────────────┘
```

**Key architectural principles:**
- Every agent calls GPT-4o with `temperature=0.0` — deterministic outputs
- Every LLM response is validated against a strict Pydantic model before proceeding
- The schema for each LLM call is auto-generated from the Pydantic model itself — no manual JSON in prompts
- Each agent is isolated: it only knows what the previous agent gave it
- All failures are graceful: if an external data source is unreachable, the agent continues with reduced confidence rather than crashing

---

## 2. Data Sources

### Langfuse (AI Agent Traces)

**What it is:** An LLM observability platform that records traces, spans, and generations for every AI agent interaction.

**How it is accessed:** Via Langfuse REST API using HTTP Basic Auth (`public_key:secret_key`).

**Two API calls per fetch:**
1. `GET /api/public/traces/{trace_id}` — top-level trace metadata (name, status, input, output, latency, cost)
2. `GET /api/public/observations?traceId={trace_id}&limit=100` — all child spans/generations with per-span status, model, token usage, errors

**How raw data is transformed:**
- Each observation is flattened into a normalised log entry: `{ timestamp, source, service, message, level, metadata }`
- Error detection runs a multi-field check (status → statusMessage → output JSON → input JSON) to classify each entry as `ERROR`, `WARN`, or `INFO`
- GENERATION type entries include model name and token counts (`Tokens: in=X out=Y`)
- Input payloads are selectively summarised (only `query`, `model`, `articles_count`, `mode`, `top_k`, `max_articles` fields to avoid token bloat)

**Accessed by:** Normalization (if `trace_id` provided), Correlation (always if `trace_id` available), Error Analysis (if `analysis_target == Agent or Unknown`), RCA (if `rca_target == Agent or Unknown`)

---

### Prometheus (Infrastructure Metrics)

**What it is:** A time-series metrics database. Queried via PromQL range queries.

**How it is accessed:** `GET /api/v1/query_range` with PromQL, start time, end time, and 15-second step resolution.

**Time window:** ±5 minutes around the incident timestamp, capturing the failure and its surroundings.

**Six default PromQL queries run for every fetch:**

| Query Name | PromQL Pattern | What It Detects |
|---|---|---|
| `error_rate` | `sum(rate(http_requests_total{status=~"5..",job=~".*{agent}.*"}[window]))` | HTTP 5xx error rate |
| `latency_p99` | `histogram_quantile(0.99, ...)` | P99 request latency spikes |
| `up_status` | `up{job=~".*{agent}.*"}` | Service up/down state |
| `memory_usage` | `container_memory_usage_bytes{pod=~".*{agent}.*"}` | Container memory consumption |
| `restart_count` | `kube_pod_container_status_restarts_total{pod=~".*{agent}.*"}` | Pod crash-loop detection |
| `dns_failures` | `sum(rate(coredns_dns_responses_total{rcode="SERVFAIL"}[window]))` | DNS resolution failures |

**How raw data is transformed:**
- Each PromQL result (time series) → last data point in the range is extracted
- Severity is automatically set: `error_rate > 0 → ERROR`, `up_status == 0 → ERROR`, `restart_count > 0 → WARN`
- Labels (job, pod, namespace) are embedded into the message for context
- Failed queries produce a WARN-level placeholder entry so the LLM knows data was missing

**Accessed by:** Normalization (if no `trace_id`), Correlation (always), Error Analysis (if `analysis_target == InfraLogs or Unknown`), RCA (if `rca_target == InfraLogs or Unknown`)

---

## 3. Agent 1 — Normalization Agent

### Purpose

The first contact point for every incident. Takes raw identifiers (timestamp, trace ID, agent name) from the frontend and converts raw observability data into a single, structured `NormalizedIncident` object. It classifies the error type and extracts atomic signals — nothing more.

**Strict scope:** Normalization only. No causality, no root cause, no recommendations.

---

### Step-by-Step Internal Flow

```
Request received (timestamp + trace_id? + agent_name)
         │
         ▼
   ┌─────────────┐
   │   Routing   │  trace_id present? → Langfuse
   │   Decision  │  trace_id absent?  → Prometheus
   └──────┬──────┘
          │
          ▼
   ┌─────────────┐
   │  Fetch Raw  │  Langfuse: trace + up to 100 observations
   │    Data     │  Prometheus: 6 PromQL queries × ±5min window
   └──────┬──────┘
          │
          ▼
   ┌─────────────────────┐
   │  Pre-LLM Error Scan │  Check each log entry:
   │  (_has_error_signals)│  1. level in {ERROR,FATAL,CRITICAL,WARN}?
   │                     │  2. keyword match (whole-word regex)?
   └──────────┬──────────┘
              │
        ┌─────┴─────┐
        │           │
     No errors   Errors found
        │           │
        ▼           ▼
  Return      Build prompt:
  NO_ERROR    - Incident context
  immediately - Raw logs formatted
  (no LLM)    - Pydantic schema injected
                    │
                    ▼
              Call GPT-4o
              (temperature=0, json_object mode)
                    │
                    ▼
              Pydantic validate
              NormalizedIncident
                    │
                    ▼
              Return NormalizationResponse
```

---

### Data Fetching Detail

**Langfuse path (trace_id provided):**
- Makes 2 HTTP calls: trace + observations
- `httpx.AsyncClient` with 10-second timeout per call
- Observations limited to 100 entries per request
- Each observation → 1 log entry with `level` auto-detected from status/output

**Prometheus path (no trace_id):**
- Makes 6 HTTP calls in sequence (one per PromQL query)
- 15-second step resolution within ±5 minute window
- Takes only the **last data point** from each time series (most recent state)
- Failed queries produce WARN placeholder entries (do not abort)

---

### Pre-LLM Short-Circuit (Key Efficiency Feature)

Before calling the LLM, the agent runs `_has_error_signals()` — a fast regex scan over all log entries. Two-phase detection:

1. **Explicit level check:** If `level` is `ERROR`, `FATAL`, `CRITICAL`, `WARN`, or `WARNING` → error confirmed immediately
2. **Keyword scan (only for ambiguous levels):** Whole-word regex search for terms like `error`, `fail`, `timeout`, `crash`, `refused` — but only if the level is **not** `INFO` or `DEBUG`. This avoids false positives from metric names like `error_rate=0`

If no error signals are found, the agent returns `NO_ERROR` with `confidence=1.0` **without calling the LLM at all**. This short-circuits the entire 5-agent pipeline and saves significant cost and latency.

---

### LLM Prompt Design

- **System prompt** — strict role definition: extract facts only, no inference, no causality
- **Schema injection** — `NormalizedIncident.model_json_schema()` is auto-generated and embedded; the LLM must output a JSON object that validates against it
- **User message** — formatted log lines: `[N] ts=... src=... svc=... level=... msg=... meta={...}`
- **`response_format={"type": "json_object"}`** — forces OpenAI to output valid JSON (never markdown)

---

### Output Contract

```python
NormalizedIncident:
  error_type:    NO_ERROR | AI_AGENT | INFRA | NETWORK | UNKNOWN
  error_summary: str (max 300 chars, factual only)
  timestamp:     ISO-8601 string
  confidence:    float 0.0–1.0
  entities:      { agent_id, service, trace_id }
  signals:       list[str]  # e.g. ["LLM_access_disabled", "timeout"]
```

**Priority rule when error types are mixed:** `INFRA > NETWORK > AI_AGENT`

---

### What Makes It Efficient

- Pre-LLM scan avoids LLM calls for clean traces (the most common case in production)
- Pydantic schema is built once at startup (`_build_response_schema`) and reused for every request
- Whole-word regex (pre-compiled at module level) avoids false positives from metric names
- `AsyncOpenAI` with `await` — non-blocking, compatible with FastAPI's async runtime

---

## 4. Agent 2 — Correlation Agent

### Purpose

Takes the `NormalizedIncident` and builds a cross-system **causal failure graph**. Its job is to answer: *which system or component caused what, and in what order?* It also makes the critical routing decision that drives every subsequent agent — `analysis_target`.

**Strict scope:** Cross-system correlation and causal graph construction only. No per-error deep-dive, no root cause determination beyond a hypothesis.

---

### Step-by-Step Internal Flow

```
Request received (NormalizedIncident + trace_id? + agent_name)
         │
         ▼
   ┌──────────────────────────────────┐
   │  Fetch Prometheus (always)       │  Infrastructure baseline
   └────────────────┬─────────────────┘
                    │
                    ▼ (if trace_id present)
   ┌──────────────────────────────────┐
   │  Fetch Langfuse (conditional)    │  Agent execution trace
   └────────────────┬─────────────────┘
                    │
                    ▼
   ┌──────────────────────────────────┐
   │  Sort all logs chronologically   │
   │  Group by source for prompt      │
   └────────────────┬─────────────────┘
                    │
                    ▼
   ┌──────────────────────────────────┐
   │  Build user message:             │
   │  - Normalization context         │
   │  - Logs grouped by source        │
   │  - Failure context injected      │
   └────────────────┬─────────────────┘
                    │
                    ▼
              Call GPT-4o
              (with CorrelationResult schema)
                    │
                    ▼
              Pydantic validate
              CorrelationResult
                    │
                    ▼
              Return CorrelationResponse
              (includes analysis_target routing decision)
```

---

### Data Fetching Detail

**Always fetches Prometheus** regardless of whether a trace_id is available — infrastructure is always relevant.

**Conditionally fetches Langfuse** if `trace_id` is present (AI agent trace available).

Both fetches run **sequentially** (Prometheus first, then Langfuse) because:
- Prometheus establishes the infrastructure baseline
- Langfuse adds the AI-layer view on top

If either fetch fails, a WARN-level placeholder is inserted into the log list — the LLM is explicitly told data was missing, enabling it to set lower confidence.

All logs are **sorted chronologically** before being sent to the LLM — essential for causality reasoning (earliest failure = root candidate).

---

### What the LLM Produces

```python
CorrelationResult:
  correlation_chain:     list[str]       # e.g. ["DNS failure → proxy timeout → gateway 502"]
  peer_components:       list[PeerComponent]  # each with role + evidence
  timeline:              list[TimelineEvent]  # chronological events
  root_cause_candidate:  RootCauseCandidate   # component + confidence + reason
  analysis_target:       Agent | InfraLogs | Unknown  ← ROUTING DECISION
```

**The `analysis_target` field is the most important output** — it controls which data sources the next three agents will query:
- `Agent` → Error Analysis, RCA, all use Langfuse only
- `InfraLogs` → Error Analysis, RCA, all use Prometheus only
- `Unknown` → Error Analysis, RCA, all use both sources

---

### LLM Prompt Design

- **System prompt** injects normalization context (error_type, signals, summary) and data source description into the template
- **User message** groups logs by source (`### Logs from langfuse (N entries)`, `### Logs from prometheus (N entries)`) for clear separation
- **Fallback path:** If no external logs are available, the LLM is explicitly instructed to correlate from normalization context alone with lower confidence

**Causality rules baked into the prompt:**
- Causality is time-based only — earliest failure is the root candidate
- No hallucinated edges — every causal link must have log evidence
- Peer components must be evidence-backed

---

### What Makes It Efficient

- Prometheus + Langfuse fetched once here; subsequent agents re-fetch only what their target requires (no redundant double-fetching at this stage)
- Log grouping by source in the prompt preserves signal locality — the LLM can see "all Langfuse entries" together, making cross-source correlation easier
- `analysis_target` routing decision eliminates unnecessary data source queries in all downstream agents

---

## 5. Agent 3 — Error Analysis Agent

### Purpose

Takes the `CorrelationResult` and performs a deep-dive error analysis on the targeted data source. Every distinct error gets a unique ID, is categorised, severity-rated, and linked to evidence. It also detects recurring patterns and assesses impact per component.

**Strict scope:** Error identification and classification only. No root cause determination, no recommendations.

---

### Step-by-Step Internal Flow

```
Request received (CorrelationResult + NormalizedIncident + trace_id? + agent_name)
         │
         ▼
   ┌─────────────────────────────────────────┐
   │  Read analysis_target from correlation  │
   │  Agent → Langfuse only                  │
   │  InfraLogs → Prometheus only            │
   │  Unknown → Both                         │
   └────────────────────┬────────────────────┘
                        │
             ┌──────────┴──────────┐
             │                     │
        (if Agent/Unknown)   (if InfraLogs/Unknown)
             │                     │
             ▼                     ▼
      Fetch Langfuse         Fetch Prometheus
             │                     │
             └──────────┬──────────┘
                        │ (merge + sort)
                        ▼
   ┌─────────────────────────────────────────┐
   │  Build user message:                    │
   │  - Incident context                     │
   │  - Correlation context (chain, peer     │
   │    components, timeline, root candidate)│
   │  - Logs grouped by source               │
   └────────────────────┬────────────────────┘
                        │
                        ▼
                  Call GPT-4o
                  (with ErrorAnalysisResult schema)
                        │
                        ▼
                  Pydantic validate
                  ErrorAnalysisResult
                        │
                        ▼
                  Return ErrorAnalysisResponse
                  (analysis + rca_target passthrough)
```

---

### Data Fetching Detail

**Smart routing based on `analysis_target`:**

| analysis_target | Langfuse fetched? | Prometheus fetched? |
|---|---|---|
| `Agent` | Yes (requires trace_id) | No |
| `InfraLogs` | No | Yes |
| `Unknown` | Yes (requires trace_id) | Yes |

If `analysis_target` is `Agent` or `Unknown` but no `trace_id` is available, a WARN placeholder is inserted and the LLM is told Langfuse data was inaccessible.

**This is the most targeted data fetch in the pipeline** — other agents fetch broadly; Error Analysis fetches precisely what correlation decided is relevant.

---

### What the LLM Produces

```python
ErrorAnalysisResult:
  analysis_summary:       str (max 500 chars)
  analysis_target:        Agent | InfraLogs | Unknown
  errors:                 list[ErrorDetail]    # min 1 required
  error_patterns:         list[ErrorPattern]   # 2+ occurrences
  error_impacts:          list[ErrorImpact]
  error_propagation_path: list[str]
  confidence:             float 0.0–1.0

ErrorDetail:
  error_id:      "ERR-001", "ERR-002", ...   ← unique, sequential
  category:      llm_failure | configuration_error | timeout | dns_failure | ...
  severity:      critical | high | medium | low | info
  component:     service/component name
  error_message: exact error message from logs
  timestamp:     ISO-8601
  evidence:      raw log line that proves this error
  source:        langfuse | prometheus
```

**The `error_id` scheme is critical** — it creates named references that RCA and Recommendation agents use to link their findings back to specific errors.

---

### LLM Prompt Design

The system prompt is the richest of all agents — it injects four contexts:
1. **Correlation context** — chain, root cause candidate, peer components, timeline
2. **Normalization context** — error_type, signals, summary
3. **Data sources description** — explains which sources were queried and why
4. **Pydantic schema** — auto-generated from `ErrorAnalysisResult`

The user message adds a fifth layer: the actual log lines grouped by source.

**Rules baked into the prompt:**
- Patterns require 2+ occurrences
- Every error must have direct log evidence
- Severity defined by impact: `critical=system down`, `high=major feature broken`
- Propagation path must be time-ordered

---

### What Makes It Efficient

- Targeted data fetch (only what `analysis_target` dictates) avoids redundant API calls
- The `rca_target` field in the response is a **passthrough** from `analysis_target` — it carries the routing decision forward to the RCA agent without recomputation
- Error IDs (`ERR-001`, `ERR-002`) create a lightweight cross-agent reference system — no need to re-embed full error objects in downstream prompts

---

## 6. Agent 4 — RCA Agent

### Purpose

The most analytically demanding agent. Takes the `ErrorAnalysisResult` and performs root cause determination — going beyond error identification to answer **why** those errors occurred. Produces a definitive root cause, a causal chain, a failure timeline, blast radius, and a structured Five Whys analysis.

**Strict scope:** Root cause analysis only. No recommendations, no remediation steps.

---

### Step-by-Step Internal Flow

```
Request received (ErrorAnalysisResult + rca_target + NormalizedIncident
                  + trace_id? + agent_name)
         │
         ▼
   ┌───────────────────────────────────────────────┐
   │  Route by rca_target:                         │
   │  Agent     → Langfuse only                    │
   │  InfraLogs → Prometheus only                  │
   │  Unknown   → Both Langfuse + Prometheus        │
   └──────────────────────┬────────────────────────┘
                          │
               ┌──────────┴──────────┐
          (if Agent/Unknown)    (if InfraLogs/Unknown)
               │                     │
               ▼                     ▼
        Fetch Langfuse         Fetch Prometheus
               │                     │
               └──────────┬──────────┘
                          │ (merge + sort chronologically)
                          ▼
   ┌───────────────────────────────────────────────┐
   │  Build user message:                          │
   │  - Incident context                           │
   │  - Full ErrorAnalysisResult (all errors,      │
   │    patterns, impacts, propagation path)        │
   │  - Individual errors formatted with error_ids │
   │  - Fresh logs grouped by source               │
   └──────────────────────┬────────────────────────┘
                          │
                          ▼
   ┌───────────────────────────────────────────────┐
   │  Build system prompt with:                    │
   │  - Error Analysis context (counts, categories,│
   │    propagation, affected components)           │
   │  - Normalization context                      │
   │  - Data sources description                   │
   │  - Five Whys methodology rules                │
   │  - RCAResult JSON schema                      │
   └──────────────────────┬────────────────────────┘
                          │
                          ▼
                    Call GPT-4o
                    (with RCAResult schema)
                          │
                          ▼
                    Pydantic validate
                    RCAResult
                          │
                          ▼
                    Return RCAResponse
```

---

### Data Fetching Detail

Follows the same routing logic as Error Analysis but uses `rca_target` (passed through from `analysis_target`). The RCA agent **re-fetches fresh logs** rather than reusing Error Analysis logs — this is intentional. RCA needs the most recent data with full context for causality determination, not data pre-filtered for error extraction.

| rca_target | Langfuse | Prometheus |
|---|---|---|
| `Agent` | Yes (trace_id required) | No |
| `InfraLogs` | No | Yes |
| `Unknown` | Yes | Yes |

If the external source fetch fails, the agent falls back to performing RCA from the Error Analysis context alone with a lower confidence score — it never returns an empty response.

---

### What the LLM Produces

```python
RCAResult:
  rca_summary:         str (max 800 chars)
  root_cause:          RootCause
  causal_chain:        list[CausalLink]    # min 1 required
  contributing_factors: list[ContributingFactor]
  failure_timeline:    list[FailureTimeline]
  blast_radius:        list[str]
  five_why_analysis:   FiveWhyAnalysis     # exactly 5 steps required
  confidence:          float 0.0–1.0

RootCause:
  category:    llm_provider | agent_logic | network | dns | memory | ...
  component:   specific service name
  description: detailed explanation of what went wrong and why
  evidence:    list[str]   # min 1 log entry required
  error_ids:   list[str]   # references to ErrorAnalysisResult error_ids
  confidence:  float 0.0–1.0

CausalLink:
  source_event: causing event
  target_event: caused event
  link_type:    direct_cause | indirect_cause | trigger | amplifier
  evidence:     log line supporting this link

FiveWhyAnalysis:
  problem_statement:    str   # the initial observed symptom
  whys:                 list[WhyStep]  # exactly 5 items
  fundamental_root_cause: str

WhyStep:
  step:      int (1–5)
  question:  "Why did X occur?"
  answer:    explanation of the cause at this level
  evidence:  specific log or metric evidence
  component: the implicated service
```

---

### Five Whys Analysis

The Five Whys is an iterative interrogation technique where each answer becomes the subject of the next "Why?" question, drilling from the observed symptom to the fundamental root cause.

**How it is enforced:**
- `whys` field has `min_length=5, max_length=5` — Pydantic rejects any response with ≠ 5 steps
- The system prompt defines the exact chaining methodology: each step's answer feeds the next step's question
- Evidence is required at every step; if evidence is thin at deeper steps, the agent is instructed to acknowledge limited visibility rather than speculate

**Example chain for an LLM access disabled incident:**
```
Problem: medical-rag fails to process requests
Why 1: LLM access is disabled
Why 2: Service is in demo error mode
Why 3: Demo mode config flag was toggled
Why 4: No pre-flight validation guards against this during startup
Why 5: Demo error mode was added without a readiness gate
Fundamental: Feature lacks an enforcement mechanism for live traffic
```

---

### LLM Prompt Design

The most information-rich prompt in the pipeline. The system prompt contains:
- All error details from Error Analysis (error_ids, categories, propagation path)
- Distinction rules: direct cause vs. indirect cause vs. trigger vs. amplifier
- Five Whys methodology with exact step-by-step chaining rules
- Evidence requirements for every causal link

The user message adds the fresh logs layered on top of the error analysis context.

**Rules baked in:**
- Root cause = EARLIEST verifiable failure point in the causal chain
- Every causal link MUST have direct log evidence — temporal proximity alone is not causation
- `blast_radius` must include every component that experienced degradation, not just the origin

---

### What Makes It Efficient

- `error_ids` from Error Analysis allow the LLM to reference specific errors by short IDs rather than re-embedding full error messages
- Re-fetching fresh logs (rather than reusing Error Analysis logs) ensures RCA operates on the most complete evidence
- Five Whys is schema-enforced — no post-processing or retry needed; the LLM must produce exactly 5 steps
- `confidence` is explicitly tied to evidence strength in the prompt — avoids false confidence in thin-evidence scenarios

---

## 7. Agent 5 — Recommendation Agent

### Purpose

The final synthesis agent. Takes the complete `ErrorAnalysisResult` and `RCAResult` and produces 1–4 ranked, actionable solutions. Unlike every other agent, it does **not fetch any external data** — it works purely from upstream findings.

**Strict scope:** Actionable recommendations only. No diagnosis, no root cause analysis.

---

### Step-by-Step Internal Flow

```
Request received (ErrorAnalysisResult + RCAResult + agent_name)
         │
         ▼
   ┌─────────────────────────────────────────┐
   │  Build user message:                    │
   │  - RCA summary + root cause details     │
   │  - Causal chain                         │
   │  - Contributing factors                 │
   │  - Blast radius                         │
   │  - Error analysis summary               │
   │  - Individual errors with error_ids     │
   │  - Error patterns                       │
   │  - Error impacts                        │
   └──────────────────────┬──────────────────┘
                          │
                          ▼
   ┌─────────────────────────────────────────┐
   │  Build system prompt:                   │
   │  - Full RCA context (8 fields injected) │
   │  - Full Error Analysis context          │
   │    (5 fields injected)                  │
   │  - Ranking rules (1-4 solutions)        │
   │  - RecommendationResult schema          │
   └──────────────────────┬──────────────────┘
                          │
                          ▼
                    Call GPT-4o
                    (with RecommendationResult schema)
                          │
                          ▼
                    Pydantic validate
                    (includes rank uniqueness check)
                          │
                          ▼
                    Return RecommendationResponse
```

---

### No External Data Fetching

The Recommendation Agent is the only agent in the pipeline that makes **zero external API calls**. It is purely a synthesis and reasoning layer on top of the upstream agents' outputs. This makes it:
- The fastest agent in the pipeline (LLM call only, no I/O wait)
- The most reliable (no external failure modes)
- The most focused (every recommendation is evidence-grounded from upstream findings)

---

### What the LLM Produces

```python
RecommendationResult:
  recommendation_summary: str (max 500 chars)
  solutions:              list[Solution]  # 1–4 items, validated by Pydantic
  root_cause_addressed:   str
  confidence:             float 0.0–1.0

Solution:
  rank:                 int 1–4  # must be sequential, unique (validated)
  title:               str (max 120 chars)
  description:         str (detailed action + why it works)
  category:            config_change | code_fix | infrastructure | scaling |
                       retry_logic | fallback | monitoring | access_management |
                       network | dependency_update | process_change | architecture
  effort:              quick_fix | low | medium | high
  addresses_root_cause: bool  # rank 1 must always be True
  affected_components: list[str]
  expected_outcome:    str
  error_ids:           list[str]  # links back to ErrorAnalysis error_ids
```

**Pydantic rank validation** — a `@model_validator` runs after parsing and enforces that solution ranks are **sequential from 1 to N** (e.g., for 3 solutions: [1, 2, 3]). Any gap or duplicate causes a validation error and the LLM is retried.

---

### Ranking Rules (enforced in prompt)

| Rank | Purpose |
|---|---|
| 1 | **Must** directly fix the root cause — `addresses_root_cause=True` |
| 2 | Addresses most critical secondary concern or prevents propagation |
| 3 | Addresses a contributing factor or adds resilience |
| 4 | Preventive measure for future recurrence (only if genuinely useful) |

**Anti-padding rule:** The agent is explicitly instructed to output **only as many solutions as genuinely apply** — 1 or 2 good solutions is better than 4 vague ones.

---

### LLM Prompt Design

The richest context injection of any agent — 13 fields from upstream:

**From RCA:** `rca_summary`, `root_cause_category`, `root_cause_component`, `root_cause_description`, `root_cause_confidence`, `causal_chain`, `contributing_factors`, `blast_radius`

**From Error Analysis:** `error_analysis_summary`, `error_count`, `error_categories`, `error_propagation_path`, individual errors

This ensures recommendations are **anchored to specific evidence** — the LLM cannot invent problems not present in the upstream analysis.

---

### What Makes It Efficient

- No external I/O → fastest step in the pipeline, typically 2–3 seconds
- `error_ids` from Error Analysis allow solutions to reference specific errors by ID
- Pydantic rank validation catches invalid ranking before the response is returned — automatic quality gate without manual post-processing
- The system prompt contains the full RCA context in the template, meaning the LLM sees the complete picture without requiring conversational history

---

## 8. Pipeline Data Flow

```
              ┌──────────────────┐
              │   Frontend/UI    │
              │ trace_id +       │
              │ timestamp +      │
              │ agent_name       │
              └────────┬─────────┘
                       │
         ┌─────────────▼──────────────┐
         │    Normalization Agent      │
         │                            │
         │  DATA IN:                  │
         │   Langfuse (if trace_id)   │
         │     └─ 1 trace             │
         │     └─ up to 100 obs       │
         │   Prometheus (if no tid)   │
         │     └─ 6 PromQL queries    │
         │                            │
         │  DATA OUT:                 │
         │   NormalizedIncident       │
         │   (error_type, summary,    │
         │    signals, entities,      │
         │    confidence)             │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │    Correlation Agent        │
         │                            │
         │  DATA IN:                  │
         │   NormalizedIncident       │
         │   Prometheus (always)      │
         │     └─ 6 PromQL queries    │
         │   Langfuse (if trace_id)   │
         │     └─ 1 trace + 100 obs   │
         │                            │
         │  DATA OUT:                 │
         │   CorrelationResult        │
         │   (chain, timeline, peer   │
         │    components, root_candidate,│
         │    analysis_target)        │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │    Error Analysis Agent     │
         │                            │
         │  DATA IN:                  │
         │   CorrelationResult        │
         │   NormalizedIncident       │
         │   Langfuse (if Agent/Unk)  │
         │   Prometheus (if Infra/Unk)│
         │                            │
         │  DATA OUT:                 │
         │   ErrorAnalysisResult      │
         │   (errors with IDs,        │
         │    patterns, impacts,      │
         │    propagation, rca_target)│
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │         RCA Agent           │
         │                            │
         │  DATA IN:                  │
         │   ErrorAnalysisResult      │
         │   NormalizedIncident       │
         │   Langfuse (if Agent/Unk)  │
         │   Prometheus (if Infra/Unk)│
         │                            │
         │  DATA OUT:                 │
         │   RCAResult                │
         │   (root_cause, causal_chain│
         │    five_why_analysis,      │
         │    blast_radius, timeline) │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │    Recommendation Agent     │
         │                            │
         │  DATA IN:                  │
         │   ErrorAnalysisResult      │
         │   RCAResult                │
         │   (no external data fetch) │
         │                            │
         │  DATA OUT:                 │
         │   RecommendationResult     │
         │   (1–4 ranked solutions,   │
         │    each with effort,       │
         │    category, error_ids)    │
         └────────────────────────────┘
```

---

## 9. Efficiency & Design Decisions

### Schema-Driven Contracts

Every agent auto-generates its LLM output schema from the Pydantic model at startup:

```python
schema = RCAResult.model_json_schema()  # called once in __init__
```

This means:
- Adding a new field to `RCAResult` automatically updates the LLM's output contract
- No manual JSON schema maintenance
- No drift between the model definition and the prompt

### Deterministic LLM Calls

All agents use `temperature=0.0`. This ensures reproducible analysis for the same input — critical for debugging and for comparing results across pipeline runs.

### Graceful Degradation at Every Stage

No agent fails hard when a data source is unavailable:
- Langfuse unreachable → WARN placeholder injected, analysis continues with lower confidence
- Prometheus unreachable → same
- LLM returns empty response → explicit `ValueError` raised, caught by the route handler, returns HTTP 500

### NO_ERROR Short-Circuit

When the Normalization Agent detects clean logs (no error signals), the entire pipeline terminates immediately. No LLM calls are made for the remaining 4 agents. This is the most common outcome in production (healthy traces) and eliminates the majority of LLM cost.

### Cross-Agent Referencing via Error IDs

The `error_id` scheme (`ERR-001`, `ERR-002`, ...) created by the Error Analysis Agent creates a lightweight cross-agent linking system:
- RCA references `error_ids` in its root cause and causal links
- Recommendation references `error_ids` in each solution
- No need to re-embed full error objects — just a short string identifier

### Routing Decisions Flow Forward

`analysis_target` (from Correlation) → `rca_target` (passthrough in ErrorAnalysisResponse) → used by RCA

This single field eliminates redundant routing computation. Correlation makes the routing decision once; every downstream agent reads it without re-analysing the situation.

### Re-fetching Strategy

Error Analysis and RCA both fetch fresh logs from external sources even though Correlation already fetched some of the same data. This is intentional:
- Correlation fetches broadly to build the causal graph
- Error Analysis and RCA fetch targeted data for their specific analytical task
- The routing decision (`analysis_target`) means downstream agents fetch **less** data than Correlation, not more

### Async Throughout

Every external call uses `httpx.AsyncClient` and `AsyncOpenAI`. All agents use `await` with FastAPI's async runtime. The Prometheus client runs 6 PromQL queries sequentially (not concurrently) to avoid overwhelming the Prometheus server — a deliberate throttling choice.

### Data Isolation by Agent

Each agent receives only the data it needs:
- Recommendation never sees raw logs — it only sees the analysed results
- Normalization never sees correlation output — it only sees raw logs
- This prevents agents from being influenced by another agent's interpretation of the data
