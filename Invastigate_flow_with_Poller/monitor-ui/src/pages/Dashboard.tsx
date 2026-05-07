import { useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { listTraces, triggerInvestigation } from '../api'
import type { TraceSummary } from '../types'
import { useLang } from '../LanguageContext'
import { t } from '../i18n'

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
  const { lang } = useLang()
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

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
    if (!agentName.trim()) { setFormError(t('agentRequired', lang)); return }
    if (!traceId.trim()) { setFormError(t('traceRequired', lang)); return }
    if (!timestamp.trim()) { setFormError(t('timestampRequired', lang)); return }

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
          {t('pageTitle', lang)}
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: 14 }}>
          {t('pageSubtitle', lang)}
        </p>
      </div>

      {/* Trigger form */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">
            <span>▶</span> {t('triggerCard', lang)}
          </span>
        </div>
        <div className="card-body">
          <form className="form" onSubmit={handleSubmit}>
            <div className="form-row">
              <div className="form-field">
                <label className="form-label">{t('agentNameLabel', lang)}</label>
                <input
                  className="form-input"
                  placeholder={t('agentNamePlaceholder', lang)}
                  value={agentName}
                  onChange={e => setAgentName(e.target.value)}
                />
              </div>
              <div className="form-field">
                <label className="form-label">{t('traceIdLabel', lang)}</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input
                    className="form-input"
                    style={{ flex: 1 }}
                    placeholder={t('traceIdPlaceholder', lang)}
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
                <label className="form-label">{t('timestampLabel', lang)}</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input
                    className="form-input"
                    style={{ flex: 1 }}
                    placeholder={t('timestampPlaceholder', lang)}
                    value={timestamp}
                    onChange={e => setTimestamp(e.target.value)}
                  />
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => setTimestamp(nowIso())}
                    title="Use current time"
                  >
                    {t('now', lang)}
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
                  {submitting ? <><span className="spinner" />{t('starting', lang)}</> : t('startBtn', lang)}
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
            <span>📋</span> {t('recentTraces', lang)}
          </span>
          <button className="btn btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={fetchTraces}>
            {t('refresh', lang)}
          </button>
        </div>
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}>
            <span className="spinner" />
          </div>
        ) : error ? (
          <div style={{ padding: 24, color: 'var(--red)', fontSize: 13 }}>
            {t('failedTraces', lang)} {error}
          </div>
        ) : traces.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">📭</div>
            <div>{t('noTraces', lang)}</div>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="table">
              <thead>
                <tr>
                  <th>{t('colTraceId', lang)}</th>
                  <th>{t('colAgent', lang)}</th>
                  <th>{t('colTimestamp', lang)}</th>
                  <th>{t('colStatus', lang)}</th>
                  <th>{t('colCreated', lang)}</th>
                  <th>{t('colActions', lang)}</th>
                </tr>
              </thead>
              <tbody>
                {traces.map(tr => (
                  <tr key={tr.trace_id}>
                    <td>
                      <span className="table-link" style={{ fontFamily: 'var(--font-mono)' }}>
                        {tr.trace_id}
                      </span>
                    </td>
                    <td style={{ color: 'var(--text-muted)' }}>{tr.agent_name}</td>
                    <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>{tr.timestamp}</td>
                    <td>
                      <span className={`badge badge-${tr.status}`}>{tr.status}</span>
                    </td>
                    <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>{fmtDate(tr.created_at)}</td>
                    <td>
                      <div style={{ display: 'flex', gap: 6 }}>
                        {tr.status === 'running' && (
                          <Link
                            to={`/live/${tr.trace_id}`}
                            style={{
                              fontSize: 12, padding: '3px 8px', borderRadius: 4,
                              background: 'rgba(88,166,255,.15)', color: 'var(--accent)',
                              border: '1px solid rgba(88,166,255,.3)',
                            }}
                          >
                            {t('watchLive', lang)}
                          </Link>
                        )}
                        <Link
                          to={`/trace/${tr.trace_id}`}
                          style={{
                            fontSize: 12, padding: '3px 8px', borderRadius: 4,
                            background: 'var(--surface2)', color: 'var(--text-muted)',
                            border: '1px solid var(--border)',
                          }}
                        >
                          {t('viewDetail', lang)}
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
