import type { AgentName, AgentState, StepStatus } from '../types'
import { AGENT_LABELS, AGENT_ORDER } from '../types'

interface Props {
  agents: Record<AgentName, AgentState>
  activeAgent?: AgentName | null
  onSelect?: (agent: AgentName) => void
}

function icon(status: StepStatus) {
  if (status === 'completed') return '✓'
  if (status === 'failed') return '✗'
  if (status === 'running') return '…'
  return ''
}

export default function PipelineTimeline({ agents, activeAgent, onSelect }: Props) {
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Pipeline Steps</span>
      </div>
      <div className="card-body">
        <div className="timeline">
          {AGENT_ORDER.map((agent, idx) => {
            const state = agents[agent]
            const status = state?.status ?? 'pending'
            const isActive = activeAgent === agent

            return (
              <div
                key={agent}
                className="timeline-step"
                style={{ cursor: onSelect ? 'pointer' : 'default', marginBottom: idx < AGENT_ORDER.length - 1 ? 8 : 0 }}
                onClick={() => onSelect?.(agent)}
              >
                <div className={`timeline-dot ${status}`}>
                  {icon(status) || (idx + 1)}
                </div>
                <div
                  className="timeline-content"
                  style={{
                    padding: '4px 8px',
                    borderRadius: 6,
                    background: isActive ? 'var(--surface2)' : 'transparent',
                    flex: 1,
                  }}
                >
                  <div
                    className="timeline-label"
                    style={{
                      color: status === 'pending' ? 'var(--text-muted)' : 'var(--text)',
                      lineHeight: '24px',
                    }}
                  >
                    {AGENT_LABELS[agent]}
                  </div>
                  <div className="timeline-meta" style={{ marginTop: 2 }}>
                    {state?.processing_time_ms != null && (
                      <span>{state.processing_time_ms.toFixed(0)} ms</span>
                    )}
                    {(state?.logs_count ?? 0) > 0 && (
                      <span>{state?.logs_count} logs</span>
                    )}
                    {state?.confidence != null && (
                      <span>{Math.round(state.confidence * 100)}% conf</span>
                    )}
                    {state?.data_sources && state.data_sources.length > 0 && (
                      <span>{state.data_sources.join(', ')}</span>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
