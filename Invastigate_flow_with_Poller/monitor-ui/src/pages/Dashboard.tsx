import { useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { listTraces, triggerInvestigation } from '../api'
import type { TraceSummary } from '../types'

function nowIso() {
  return new Date().toISOString().replace(/\.\d+Z$/, 'Z')
}

function randomId() {
  return 'trace-' + Math.random().toString(36).slice(2, 10)
}

function fmtDate(s: string) {
  try { return new Date(s).toLocaleString() } catch { return s }
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Form state
  const [agentName, setAgentName] = useState('')
  const [traceId, setTraceId] = useState(randomId)
  const [timestamp, setTimestamp] = useState(nowIso)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const fetchTraces = useCallback(async () => {
    try {
      const data = await listTraces(30)
      setTraces(data)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchTraces()
    const id = setInterval(fetchTraces, 5000)
    return () => clearInterval(id)
  }, [fetchTraces])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!agentName.trim()) { setFormError('Agent name is required'); return }
    if (!traceId.trim()) { setFormError('Trace ID is required'); return }
    if (!timestamp.trim()) { setFormError('Timestamp is required'); return }

    setFormError(null)
    setSubmitting(true)
    try {
      const res = await triggerInvestigation(agentName.trim(), traceId.trim(), timestamp.trim())
      navigate(`/live/${res.trace_id}`)
    } catch (e) {
      setFormError(String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* Hero */}
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>
          Investigation Flow Monitor
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: 14 }}>
          Trigger investigations and watch each agent step execute in real time.
        </p>
      </div>

      {/* Trigger form */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">
            <span>▶</span> Trigger New Investigation
          </span>
        </div>
        <div className="card-body">
          <form className="form" onSubmit={handleSubmit}>
            <div className="form-row">
              <div className="form-field">
                <label className="form-label">Agent Name</label>
                <input
                  className="form-input"
                  placeholder="e.g. sample-agent, summarizer-v2"
                  value={agentName}
                  onChange={e => setAgentName(e.target.value)}
                />
              </div>
              <div className="form-field">
                <label className="form-label">Trace ID</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input
                    className="form-input"
                    style={{ flex: 1 }}
                    placeholder="trace-abc-123"
                    value={traceId}
                    onChange={e => setTraceId(e.target.value)}
                  />
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => setTraceId(randomId())}
                    title="Generate random ID"
                  >
                    ↺
                  </button>
                </div>
              </div>
            </div>
            <div className="form-row">
              <div className="form-field">
                <label className="form-label">Timestamp (ISO-8601)</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input
                    className="form-input"
                    style={{ flex: 1 }}
                    placeholder="2025-01-15T10:32:00Z"
                    value={timestamp}
                    onChange={e => setTimestamp(e.target.value)}
                  />
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => setTimestamp(nowIso())}
                    title="Use current time"
                  >
                    Now
                  </button>
                </div>
              </div>
              <div className="form-field" style={{ justifyContent: 'flex-end' }}>
                {formError && (
                  <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 8 }}>
                    {formError}
                  </div>
                )}
                <button className="btn btn-primary" type="submit" disabled={submitting}>
                  {submitting ? <><span className="spinner" />Starting…</> : '▶ Start Investigation'}
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>

      {/* Recent traces */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">
            <span>📋</span> Recent Traces
          </span>
          <button className="btn btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={fetchTraces}>
            Refresh
          </button>
        </div>
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}>
            <span className="spinner" />
          </div>
        ) : error ? (
          <div style={{ padding: 24, color: 'var(--red)', fontSize: 13 }}>
            Failed to load traces: {error}
          </div>
        ) : traces.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">📭</div>
            <div>No traces yet — trigger your first investigation above.</div>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Trace ID</th>
                  <th>Agent</th>
                  <th>Timestamp</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {traces.map(t => (
                  <tr key={t.trace_id}>
                    <td>
                      <span className="table-link" style={{ fontFamily: 'var(--font-mono)' }}>
                        {t.trace_id}
                      </span>
                    </td>
                    <td style={{ color: 'var(--text-muted)' }}>{t.agent_name}</td>
                    <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>{t.timestamp}</td>
                    <td>
                      <span className={`badge badge-${t.status}`}>{t.status}</span>
                    </td>
                    <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>{fmtDate(t.created_at)}</td>
                    <td>
                      <div style={{ display: 'flex', gap: 6 }}>
                        {t.status === 'running' && (
                          <Link
                            to={`/live/${t.trace_id}`}
                            style={{
                              fontSize: 12, padding: '3px 8px', borderRadius: 4,
                              background: 'rgba(88,166,255,.15)', color: 'var(--accent)',
                              border: '1px solid rgba(88,166,255,.3)',
                            }}
                          >
                            Watch Live
                          </Link>
                        )}
                        <Link
                          to={`/trace/${t.trace_id}`}
                          style={{
                            fontSize: 12, padding: '3px 8px', borderRadius: 4,
                            background: 'var(--surface2)', color: 'var(--text-muted)',
                            border: '1px solid var(--border)',
                          }}
                        >
                          View Detail
                        </Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
