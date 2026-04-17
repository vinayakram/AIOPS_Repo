import type { TraceSummary, TraceDetail } from './types'

const BASE = ''  // Vite proxies /api → localhost:8000

export async function listTraces(limit = 20): Promise<TraceSummary[]> {
  const res = await fetch(`${BASE}/api/v1/traces?limit=${limit}`)
  if (!res.ok) throw new Error(`Failed to list traces: ${res.status}`)
  const data = await res.json()
  return data.traces as TraceSummary[]
}

export async function getTrace(traceId: string): Promise<TraceDetail> {
  const res = await fetch(`${BASE}/api/v1/traces/${traceId}`)
  if (!res.ok) throw new Error(`Trace not found: ${res.status}`)
  return res.json()
}

export async function triggerInvestigation(
  agentName: string,
  traceId: string,
  timestamp: string,
): Promise<{ trace_id: string; stream_url: string }> {
  const res = await fetch(`${BASE}/api/v1/monitor/investigate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      agent_name: agentName,
      trace_id: traceId,
      timestamp,
    }),
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Failed to start investigation: ${err}`)
  }
  return res.json()
}

export function createEventSource(traceId: string): EventSource {
  return new EventSource(`${BASE}/api/v1/monitor/stream/${traceId}`)
}
