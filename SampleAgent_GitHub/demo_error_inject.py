"""
Demo script: inject a synthetic error trace into AIops Telemetry.

Usage:
    python demo_error_inject.py

Simulates a failed medical query where:
  - pubmed_fetch succeeded
  - embedding succeeded
  - openai_generation FAILED (e.g. rate-limit / context-length error)

The trace appears in the AIops dashboard with status=error and a
failing span with an error_message — great for demoing error capture.
"""
import uuid
import requests
from datetime import datetime, timezone, timedelta

AIOPS_URL = "http://localhost:7000"
API_KEY   = None  # set to your X-AIops-Key value if configured

# ── Timeline: pretend this happened ~2 minutes ago ────────────────────────────
now     = datetime.now(tz=timezone.utc)
t_start = now - timedelta(minutes=2, seconds=14)

def ms(dt: datetime) -> str:
    return dt.isoformat()

trace_id = str(uuid.uuid4())

# ── Build spans ────────────────────────────────────────────────────────────────
span_pubmed = {
    "id":             str(uuid.uuid4()),
    "trace_id":       trace_id,
    "name":           "pubmed_fetch",
    "span_type":      "retriever",
    "status":         "ok",
    "started_at":     ms(t_start),
    "ended_at":       ms(t_start + timedelta(seconds=3, milliseconds=420)),
    "duration_ms":    3420.0,
    "input_preview":  '{"query": "metformin lactic acidosis risk elderly patients", "max_articles": 30}',
    "output_preview": '{"articles_count": 28, "cached": false}',
}

span_embedding = {
    "id":             str(uuid.uuid4()),
    "trace_id":       trace_id,
    "name":           "embedding",
    "span_type":      "chain",
    "status":         "ok",
    "started_at":     ms(t_start + timedelta(seconds=3, milliseconds=500)),
    "ended_at":       ms(t_start + timedelta(seconds=5, milliseconds=180)),
    "duration_ms":    1680.0,
    "input_preview":  '{"articles_count": 28, "model": "all-MiniLM-L6-v2"}',
    "output_preview": '{"dimension": 384}',
}

span_pagerank = {
    "id":             str(uuid.uuid4()),
    "trace_id":       trace_id,
    "name":           "pagerank",
    "span_type":      "chain",
    "status":         "ok",
    "started_at":     ms(t_start + timedelta(seconds=5, milliseconds=200)),
    "ended_at":       ms(t_start + timedelta(seconds=6, milliseconds=50)),
    "duration_ms":    850.0,
    "input_preview":  '{"mode": "citation"}',
    "output_preview": '{"method": "similarity"}',
}

span_faiss = {
    "id":             str(uuid.uuid4()),
    "trace_id":       trace_id,
    "name":           "faiss_retrieval",
    "span_type":      "retriever",
    "status":         "ok",
    "started_at":     ms(t_start + timedelta(seconds=6, milliseconds=100)),
    "ended_at":       ms(t_start + timedelta(seconds=6, milliseconds=390)),
    "duration_ms":    290.0,
    "input_preview":  '{"top_k": 5, "query": "metformin lactic acidosis risk elderly patients"}',
    "output_preview": '{"candidates_evaluated": 20, "top_k_returned": 5, "top_score": 0.8821}',
}

span_llm = {
    "id":             str(uuid.uuid4()),
    "trace_id":       trace_id,
    "name":           "openai_generation",
    "span_type":      "llm",
    "status":         "error",
    "started_at":     ms(t_start + timedelta(seconds=6, milliseconds=450)),
    "ended_at":       ms(t_start + timedelta(seconds=14)),
    "duration_ms":    7550.0,
    "input_preview":  '{"model": "gpt-4o", "articles_used": 5}',
    "output_preview": None,
    "error_message":  (
        "openai.RateLimitError: 429 — You exceeded your current quota. "
        "Please check your plan and billing details."
    ),
    "tokens_input":   3842,
    "tokens_output":  0,
    "model_name":     "gpt-4o",
}

# ── Build trace ────────────────────────────────────────────────────────────────
trace_payload = {
    "id":                trace_id,
    "app_name":          "medical-rag",
    "user_id":           "demo-user",
    "status":            "error",
    "started_at":        ms(t_start),
    "ended_at":          ms(t_start + timedelta(seconds=14)),
    "total_duration_ms": 14000.0,
    "input_preview":     "metformin lactic acidosis risk elderly patients",
    "output_preview":    None,
    "spans": [span_pubmed, span_embedding, span_pagerank, span_faiss, span_llm],
    "logs": [
        {
            "trace_id":  trace_id,
            "level":     "ERROR",
            "logger":    "rag.pipeline",
            "message":   "openai_generation failed: 429 Rate limit exceeded — quota exhausted",
            "timestamp": ms(t_start + timedelta(seconds=14)),
        }
    ],
}

# ── POST to AIops ──────────────────────────────────────────────────────────────
headers = {"Content-Type": "application/json"}
if API_KEY:
    headers["X-AIops-Key"] = API_KEY

print(f"Injecting error trace  {trace_id[:8]}...  into {AIOPS_URL}")
resp = requests.post(
    f"{AIOPS_URL}/api/ingest/trace",
    json=trace_payload,
    headers=headers,
    timeout=5,
)

if resp.status_code == 200:
    print(f"✓ Trace injected successfully")
    print(f"  Trace ID : {trace_id}")
    print(f"  Status   : error")
    print(f"  Spans    : pubmed_fetch(ok)  embedding(ok)  pagerank(ok)  faiss(ok)  openai_generation(ERROR)")
    print(f"\n  Open the AIops dashboard and look for a red trace from 'demo-user'")
else:
    print(f"✗ Failed: HTTP {resp.status_code}")
    print(resp.text)
