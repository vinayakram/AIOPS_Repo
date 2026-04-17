import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getTrace } from '../api'
import type { TraceDetail as TTraceDetail, AgentName, AgentState, LogEntry } from '../types'
import { AGENT_ORDER } from '../types'
import AgentCard from '../components/AgentCard'
import PipelineTimeline from '../components/PipelineTimeline'

function buildAgentsFromTrace(trace: TTraceDetail): Record<AgentName, AgentState> {
  const agents: Partial<Record<AgentName, AgentState>> = {}

  AGENT_ORDER.forEach((agent, idx) => {
    const input = (trace as Record<string, unknown>)[`${agent}_input`] as Record<string, unknown> | undefined
    const output = (trace as Record<string, unknown>)[`${agent}_output`] as Record<string, unknown> | undefined

    let status: AgentState['status'] = 'pending'
    if (output) status = 'completed'
    else if (input) status = 'failed'

    // Extract metadata from output
    let data_sources: string[] | undefined
    let logs_count: number | undefined
    let confidence: number | null | undefined

    if (output) {
      // correlation
      if (agent === 'correlation') {
        data_sources = (output.data_sources as string[]) ?? []
        logs_count = (output.total_logs_analyzed as number) ?? 0
        const corr = output.correlation as Record<string, unknown> | undefined
        const rc = corr?.root_cause_candidate as Record<string, unknown> | undefined
        confidence = rc?.confidence as number | undefined
      }
      // error_analysis
      if (agent === 'error_analysis') {
        data_sources = (output.data_sources as string[]) ?? []
        logs_count = (output.total_logs_analyzed as number) ?? 0
        const analysis = output.analysis as Record<string, unknown> | undefined
        confidence = analysis?.confidence as number | undefined
      }
      // rca
      if (agent === 'rca') {
        data_sources = (output.data_sources as string[]) ?? []
        logs_count = (output.total_logs_analyzed as number) ?? 0
        const rca = output.rca as Record<string, unknown> | undefined
        confidence = rca?.confidence as number | undefined
      }
    }

    // Flatten fetched_logs from all sources for this agent
    let fetched_logs: LogEntry[] | undefined
    if (trace.fetched_logs?.[agent]) {
      const bySource = trace.fetched_logs[agent]
      fetched_logs = Object.values(bySource).flat()
    }

    agents[agent] = {
      agent,
      step: idx + 1,
      status,
      input,
      output,
      data_sources,
      logs_count,
      confidence,
      fetched_logs,
    }
  })

  return agents as Record<AgentName, AgentState>
}

export default function TraceDetail() {
  const { traceId } = useParams<{ traceId: string }>()
  const [trace, setTrace] = useState<TTraceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedAgent, setSelectedAgent] = useState<AgentName | null>(null)

  useEffect(() => {
    if (!traceId) return
    setLoading(true)
    setError(null)
    getTrace(traceId)
      .then(t => { setTrace(t); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [traceId])

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
        <span className="spinner" />
      </div>
    )
  }

  if (error || !trace) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, alignItems: 'flex-start' }}>
        <Link to="/" style={{ color: 'var(--text-muted)', fontSize: 13 }}>← Dashboard</Link>
        <div style={{ color: 'var(--red)' }}>
          {error ?? 'Trace not found'}
        </div>
      </div>
    )
  }

  const agents = buildAgentsFromTrace(trace)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <Link to="/" style={{ color: 'var(--text-muted)', fontSize: 13 }}>← Dashboard</Link>
            <span style={{ color: 'var(--border)' }}>/</span>
            <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Trace Detail</span>
          </div>
          <h2 style={{ fontSize: 18, fontWeight: 700 }}>{trace.agent_name}</h2>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
            {trace.trace_id}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span className={`badge badge-${trace.status}`}>{trace.status}</span>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {trace.timestamp}
          </span>
          {trace.status === 'running' && (
            <Link to={`/live/${trace.trace_id}`} className="btn btn-primary" style={{ fontSize: 12 }}>
              Watch Live →
            </Link>
          )}
        </div>
      </div>

      {/* Main grid */}
      <div className="monitor-grid">
        {/* Left: timeline */}
        <PipelineTimeline
          agents={agents}
          activeAgent={selectedAgent}
          onSelect={setSelectedAgent}
        />

        {/* Right: agent cards */}
        <div>
          {AGENT_ORDER.map(agent => (
            <AgentCard
              key={agent}
              agent={agent}
              state={agents[agent]}
              defaultOpen={agents[agent].status === 'completed'}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
