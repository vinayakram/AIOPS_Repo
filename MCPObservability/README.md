# Observability MCP

Small stdio MCP server for RCA agents that need Prometheus and Langfuse evidence.

The server intentionally exposes a constrained tool set instead of arbitrary
observability access. It normalizes returned data into evidence records so RCA
agents can cite what was actually observed.

## Tools

- `prometheus_query` - run an instant PromQL query.
- `prometheus_query_range` - run a bounded PromQL range query.
- `prometheus_window_for_incident` - fetch common service metrics around an incident.
- `langfuse_get_trace` - fetch a Langfuse trace by id.
- `langfuse_list_traces` - list recent Langfuse traces.
- `langfuse_trace_summary` - extract failed observations and latency evidence.
- `correlate_cross_service_incident` - compare root-candidate and affected service timelines.

## Environment

```env
MCP_PROMETHEUS_URL=http://localhost:9092
MCP_PROMETHEUS_TIMEOUT_SECONDS=10
MCP_PROMETHEUS_MAX_RANGE_MINUTES=120

MCP_LANGFUSE_HOST=https://cloud.langfuse.com
MCP_LANGFUSE_PUBLIC_KEY=
MCP_LANGFUSE_SECRET_KEY=
MCP_LANGFUSE_REDACT_INPUTS=true
```

## Run

```bash
python3 MCPObservability/server.py
```

The server speaks MCP over stdio using JSON-RPC `Content-Length` framing.
