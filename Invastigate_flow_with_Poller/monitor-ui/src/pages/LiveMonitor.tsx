import { useEffect, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { createEventSource, getTrace } from '../api'
import type {
  AgentName,
  AgentState,
  LogEntry,
  LogsFetchedEvent,
  PipelineEvent,
  PipelineState,
  StepCompletedEvent,
  StepFailedEvent,
  StepStartedEvent,
} from '../types'
import { AGENT_ORDER } from '../types'
import AgentCard from '../components/AgentCard'
import PipelineTimeline from '../components/PipelineTimeline'

function makeInitialState(): PipelineState {
  const agents: Partial<Record<AgentName, AgentState>> = {}
  AGENT_ORDER.forEach((a, i) => {
    agents[a] = { agent: a, step: i + 1, status: 'pending' }
  })
  return {
    trace_id: null,
    agent_name: null,
    timestamp: null,
    agents: agents as Record<AgentName, AgentState>,
    completed: false,
  }
}

export default function LiveMonitor() {
  const { traceId } = useParams<{ traceId: string }>()
  const [state, setState] = useState<PipelineState>(makeInitialState)
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'connected' | 'closed' | 'error'>('connecting')
  const [events, setEvents] = useState<Array<{ ts: string; raw: string }>>([])
  const [selectedAgent, setSelectedAgent] = useState<AgentName | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const eventLogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!traceId) return

    setState(makeInitialState())
    setEvents([])
    setConnectionStatus('connecting')

    // Pre-populate fetched_logs from DB so logs survive page refresh.
    // The SSE replay buffer will then deliver any events from the live session
    // on top of these persisted entries (duplicates are acceptable — the UI
    // deduplicates by rendering what it has).
    getTrace(traceId).then(trace => {
      if (!trace.fetched_logs) return
      setState(prev => {
        const next = { ...prev, agents: { ...prev.agents } }
        AGENT_ORDER.forEach(agent => {
          const bySource = trace.fetched_logs?.[agent]
          if (!bySource) return
          const entries: LogEntry[] = Object.values(bySource).flat()
          if (entries.length === 0) return
          next.agents[agent] = {
            ...next.agents[agent],
            fetched_logs: entries,
          }
        })
        return next
      })
    }).catch(() => { /* trace may not exist yet if pipeline just started */ })

    const es = createEventSource(traceId)
    esRef.current = es

    es.onopen = () => setConnectionStatus('connected')
    es.onerror = () => setConnectionStatus('error')

    es.onmessage = (e: MessageEvent) => {
      const raw = e.data as string
      const ts = new Date().toLocaleTimeString()
      setEvents(prev => [...prev, { ts, raw }])

      let event: PipelineEvent
      try { event = JSON.parse(raw) } catch { return }

      setState(prev => applyEvent(prev, event))
    }

    return () => {
      es.close()
      esRef.current = null
    }
  }, [traceId])

  // Auto-scroll event log
  useEffect(() => {
    if (eventLogRef.current) {
      eventLogRef.current.scrollTop = eventLogRef.current.scrollHeight
    }
  }, [events])

  function applyEvent(prev: PipelineState, event: PipelineEvent): PipelineState {
    const next = { ...prev, agents: { ...prev.agents } }

    switch (event.type) {
      case 'pipeline_started': {
        const e = event as { trace_id: string; agent_name: string; timestamp: string }
        next.trace_id = e.trace_id
        next.agent_name = e.agent_name
        next.timestamp = e.timestamp
        break
      }
      case 'step_started': {
        const e = event as StepStartedEvent
        next.agents[e.agent] = {
          ...next.agents[e.agent],
          status: 'running',
          input: e.input,
        }
        setSelectedAgent(e.agent)
        break
      }
      case 'step_completed': {
        const e = event as StepCompletedEvent
        next.agents[e.agent] = {
          ...next.agents[e.agent],
          status: 'completed',
          processing_time_ms: e.processing_time_ms,
          input: e.input,
          output: e.output,
          data_sources: e.data_sources,
          logs_count: e.logs_count,
          confidence: e.confidence ?? undefined,
        }
        break
      }
      case 'step_failed': {
        const e = event as StepFailedEvent
        next.agents[e.agent] = {
          ...next.agents[e.agent],
          status: 'failed',
          processing_time_ms: e.processing_time_ms,
          error: e.error,
        }
        break
      }
      case 'logs_fetched': {
        const e = event as LogsFetchedEvent
        if (next.agents[e.agent]) {
          const existing = next.agents[e.agent].fetched_logs ?? []
          // Deduplicate: skip entries whose timestamp+message already exist
          const existingKeys = new Set(existing.map(l => `${l.timestamp}|${l.message}`))
          const newEntries = e.entries.filter(l => !existingKeys.has(`${l.timestamp}|${l.message}`))
          next.agents[e.agent] = {
            ...next.agents[e.agent],
            fetched_logs: [...existing, ...newEntries],
          }
        }
        break
      }
      case 'pipeline_completed': {
        const e = event as { completed: boolean; total_processing_time_ms: number }
        next.completed = e.completed
        next.total_ms = e.total_processing_time_ms
        setConnectionStatus('closed')
        break
      }
      case 'error': {
        const e = event as { message: string }
        next.error = e.message
        setConnectionStatus('error')
        break
      }
    }

    return next
  }

  const totalCompleted = AGENT_ORDER.filter(a => state.agents[a].status === 'completed').length
  const hasFailed = AGENT_ORDER.some(a => state.agents[a].status === 'failed')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <Link to="/" style={{ color: 'var(--text-muted)', fontSize: 13 }}>← Dashboard</Link>
            <span style={{ color: 'var(--border)' }}>/</span>
            <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Live Monitor</span>
          </div>
          <h2 style={{ fontSize: 18, fontWeight: 700 }}>
            {state.agent_name ? `${state.agent_name}` : traceId}
          </h2>
          {state.trace_id && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
              {state.trace_id}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* Connection status */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
            <span
              style={{
                width: 8, height: 8, borderRadius: '50%',
                background: connectionStatus === 'connected' ? 'var(--green)'
                  : connectionStatus === 'connecting' ? 'var(--yellow)'
                  : connectionStatus === 'error' ? 'var(--red)'
                  : 'var(--text-muted)',
              }}
            />
            <span style={{ color: 'var(--text-muted)' }}>
              {connectionStatus === 'connected' ? 'Live' : connectionStatus}
            </span>
          </div>

          {/* Progress */}
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {totalCompleted}/5 steps
          </span>

          {/* Total time */}
          {state.total_ms != null && (
            <span className={`badge badge-${state.completed && !hasFailed ? 'completed' : 'failed'}`}>
              {state.completed && !hasFailed ? 'Done' : 'Partial'} — {(state.total_ms / 1000).toFixed(1)}s
            </span>
          )}

          <Link to={`/trace/${traceId}`} className="btn btn-ghost" style={{ fontSize: 12 }}>
            View Stored →
          </Link>
        </div>
      </div>

      {/* Error banner */}
      {state.error && (
        <div style={{ background: 'rgba(248,81,73,.1)', border: '1px solid rgba(248,81,73,.3)', borderRadius: 6, padding: '10px 16px', color: 'var(--red)', fontSize: 13 }}>
          Pipeline error: {state.error}
        </div>
      )}

      {/* Main grid */}
      <div className="monitor-grid">
        {/* Left: timeline */}
        <div>
          <PipelineTimeline
            agents={state.agents}
            activeAgent={selectedAgent}
            onSelect={setSelectedAgent}
          />

          {/* Event log */}
          <div className="card" style={{ marginTop: 12 }}>
            <div className="card-header">
              <span className="card-title" style={{ fontSize: 12 }}>Event Log</span>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{events.length} events</span>
            </div>
            <div
              ref={eventLogRef}
              style={{
                maxHeight: 200,
                overflowY: 'auto',
                padding: 8,
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
              }}
            >
              {events.length === 0 ? (
                <div style={{ color: 'var(--text-muted)', padding: 8 }}>Waiting for events…</div>
              ) : events.map((e, i) => {
                let parsed: { type?: string } = {}
                try { parsed = JSON.parse(e.raw) } catch {}
                return (
                  <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 4px', borderRadius: 3 }}>
                    <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{e.ts}</span>
                    <span style={{ color: 'var(--accent)' }}>{parsed.type}</span>
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        {/* Right: agent cards */}
        <div>
          {AGENT_ORDER.map(agent => (
            <AgentCard
              key={agent}
              agent={agent}
              state={state.agents[agent]}
              defaultOpen={
                state.agents[agent].status === 'running' ||
                (state.completed && state.agents[agent].status === 'completed') ||
                agent === selectedAgent
              }
            />
          ))}
        </div>
      </div>
    </div>
  )
}
