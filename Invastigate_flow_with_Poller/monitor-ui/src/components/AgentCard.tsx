import { useState, type ReactNode } from 'react'
import type { AgentName, AgentState, LogEntry } from '../types'
import { AGENT_LABELS } from '../types'
import ConfidenceBadge from './ConfidenceBadge'

interface Props {
  agent: AgentName
  state: AgentState
  defaultOpen?: boolean
}

// ── Helpers ────────────────────────────────────────────────────────────

function statusColor(s: string) {
  if (s === 'completed') return 'var(--green)'
  if (s === 'failed') return 'var(--red)'
  if (s === 'running') return 'var(--accent)'
  return 'var(--text-muted)'
}

function numberBg(s: string) {
  if (s === 'completed') return 'rgba(63,185,80,.2)'
  if (s === 'failed') return 'rgba(248,81,73,.2)'
  if (s === 'running') return 'rgba(88,166,255,.2)'
  return 'var(--border)'
}

function levelColor(level: string) {
  const l = level.toUpperCase()
  if (l === 'ERROR' || l === 'FATAL' || l === 'CRITICAL') return 'var(--red)'
  if (l === 'WARN' || l === 'WARNING') return 'var(--yellow)'
  return 'var(--text-muted)'
}

function effortColor(effort: string) {
  if (effort === 'quick_fix') return 'var(--green)'
  if (effort === 'low') return 'var(--green)'
  if (effort === 'medium') return 'var(--yellow)'
  return 'var(--orange)'
}

function computeComplexity(agent: AgentName, output: Record<string, unknown> | undefined): {
  label: string; level: 'high' | 'mid' | 'low'
} | null {
  if (!output) return null
  try {
    if (agent === 'error_analysis') {
      const analysis = output.analysis as Record<string, unknown> | undefined
      const count = (analysis?.errors as unknown[])?.length ?? 0
      return { label: `${count} errors`, level: count > 5 ? 'high' : count > 2 ? 'mid' : 'low' }
    }
    if (agent === 'correlation') {
      const corr = output.correlation as Record<string, unknown> | undefined
      const chain = (corr?.correlation_chain as string[])?.length ?? 0
      return { label: `chain: ${chain}`, level: chain > 3 ? 'high' : chain > 1 ? 'mid' : 'low' }
    }
    if (agent === 'rca') {
      const rca = output.rca as Record<string, unknown> | undefined
      const fw = rca?.five_why_analysis as Record<string, unknown> | undefined
      const whys = (fw?.whys as unknown[])?.length ?? 0
      return { label: `${whys} whys`, level: whys >= 5 ? 'high' : whys >= 3 ? 'mid' : 'low' }
    }
    if (agent === 'recommendation') {
      const recs = output.recommendations as Record<string, unknown> | undefined
      // solutions lives directly on the RecommendationResult, not nested under .recommendations
      const count = (recs?.solutions as unknown[])?.length ?? 0
      return { label: `${count} solutions`, level: count > 3 ? 'high' : count > 1 ? 'mid' : 'low' }
    }
  } catch {}
  return null
}

// ── Sub-components ─────────────────────────────────────────────────────

function JsonView({ data }: { data: Record<string, unknown> }) {
  return <pre className="json-viewer">{JSON.stringify(data, null, 2)}</pre>
}

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 8, fontSize: 13 }}>
      <span style={{ color: 'var(--text-muted)', fontSize: 12, minWidth: 130, flexShrink: 0, paddingTop: 2 }}>
        {label}
      </span>
      <span style={{ color: 'var(--text)', flex: 1, lineHeight: 1.5 }}>{children}</span>
    </div>
  )
}

function ChainBadges({ chain }: { chain: string[] }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
      {chain.map((c, i) => (
        <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{
            padding: '2px 8px', borderRadius: 4,
            background: 'var(--surface2)', border: '1px solid var(--border)',
            fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--text)',
          }}>{c}</span>
          {i < chain.length - 1 && (
            <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>→</span>
          )}
        </span>
      ))}
    </div>
  )
}

// ── Summary views per agent ────────────────────────────────────────────

function NormalizationSummary({ output }: { output: Record<string, unknown> }) {
  const incident = output.incident as Record<string, unknown> | undefined
  if (!incident) return <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>No incident data</span>
  const signals = (incident.signals as string[]) ?? []
  return (
    <div>
      <InfoRow label="Error Type">
        <span className="badge badge-running">{String(incident.error_type ?? '')}</span>
      </InfoRow>
      <InfoRow label="Summary">{String(incident.error_summary ?? '')}</InfoRow>
      <InfoRow label="Confidence">
        <ConfidenceBadge value={incident.confidence as number} />
      </InfoRow>
      {signals.length > 0 && (
        <InfoRow label="Signals">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {signals.map((s, i) => (
              <span key={i} style={{
                padding: '1px 6px', borderRadius: 3, fontSize: 11,
                background: 'var(--surface2)', border: '1px solid var(--border)', color: 'var(--text)',
              }}>{s}</span>
            ))}
          </div>
        </InfoRow>
      )}
    </div>
  )
}

function CorrelationSummary({ output }: { output: Record<string, unknown> }) {
  const corr = output.correlation as Record<string, unknown> | undefined
  if (!corr) return <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>No correlation data</span>
  const chain = (corr.correlation_chain as string[]) ?? []
  const rc = corr.root_cause_candidate as Record<string, unknown> | undefined
  const peers = (corr.peer_components as Record<string, unknown>[]) ?? []
  const timeline = (corr.timeline as Record<string, unknown>[]) ?? []
  return (
    <div>
      <InfoRow label="Correlation Chain">
        {chain.length > 0 ? <ChainBadges chain={chain} /> : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>—</span>}
      </InfoRow>
      {rc && (
        <>
          <InfoRow label="Root Cause Candidate">
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent)', fontSize: 12 }}>
              {String(rc.component ?? '')}
            </span>
          </InfoRow>
          <InfoRow label="Reason">{String(rc.reason ?? '')}</InfoRow>
          <InfoRow label="RC Confidence">
            <ConfidenceBadge value={rc.confidence as number} />
          </InfoRow>
        </>
      )}
      {peers.length > 0 && (
        <InfoRow label="Peer Components">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {peers.map((p, i) => (
              <span key={i} style={{
                padding: '2px 8px', borderRadius: 4, fontSize: 11,
                background: 'var(--surface2)', border: '1px solid var(--border)', color: 'var(--text)',
              }}>
                {String(p.component ?? '')}
                <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>({String(p.role ?? '')})</span>
              </span>
            ))}
          </div>
        </InfoRow>
      )}
      {timeline.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
            Timeline
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {timeline.map((te, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, fontSize: 12 }}>
                <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>
                  {String(te.timestamp ?? '').slice(11, 19)}
                </span>
                <span style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>
                  {String(te.service ?? '')}
                </span>
                <span style={{ color: 'var(--text)' }}>{String(te.event ?? '')}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ErrorAnalysisSummary({ output }: { output: Record<string, unknown> }) {
  const analysis = output.analysis as Record<string, unknown> | undefined
  if (!analysis) return <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>No analysis data</span>
  const errors = (analysis.errors as Record<string, unknown>[]) ?? []
  const patterns = (analysis.error_patterns as Record<string, unknown>[]) ?? []
  const propagation = (analysis.error_propagation_path as string[]) ?? []
  return (
    <div>
      <InfoRow label="Summary">{String(analysis.analysis_summary ?? '')}</InfoRow>
      <InfoRow label="Confidence">
        <ConfidenceBadge value={analysis.confidence as number} />
      </InfoRow>
      {propagation.length > 0 && (
        <InfoRow label="Propagation">
          <ChainBadges chain={propagation} />
        </InfoRow>
      )}
      {errors.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
            Errors ({errors.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {errors.map((err, i) => (
              <div key={i} style={{
                padding: '8px 12px', borderRadius: 6,
                background: 'var(--bg)', border: '1px solid var(--border)',
                fontSize: 12,
              }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4, flexWrap: 'wrap' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--text-muted)', fontSize: 11 }}>
                    {String(err.error_id ?? '')}
                  </span>
                  <span className="badge badge-failed" style={{ fontSize: 10 }}>{String(err.severity ?? '')}</span>
                  <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{String(err.category ?? '')}</span>
                  <span style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{String(err.component ?? '')}</span>
                </div>
                <div style={{ color: 'var(--text)', lineHeight: 1.4 }}>{String(err.error_message ?? '')}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      {patterns.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
            Patterns ({patterns.length})
          </div>
          {patterns.map((p, i) => (
            <div key={i} style={{
              padding: '6px 10px', borderRadius: 6, marginBottom: 4,
              background: 'var(--bg)', border: '1px solid var(--border)', fontSize: 12,
            }}>
              <span style={{ fontWeight: 600, color: 'var(--yellow)' }}>{String(p.pattern_name ?? '')}</span>
              <span style={{ color: 'var(--text-muted)', marginLeft: 8 }}>×{String(p.occurrence_count ?? '')}</span>
              <span style={{ display: 'block', color: 'var(--text)', marginTop: 2 }}>{String(p.description ?? '')}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function FiveWhysView({ analysis }: { analysis: Record<string, unknown> }) {
  const [openStep, setOpenStep] = useState<number | null>(null)
  const whys = (analysis.whys as Record<string, unknown>[]) ?? []

  return (
    <div>
      {/* Problem Statement */}
      <div style={{
        padding: '10px 14px', borderRadius: 6, marginBottom: 16,
        background: 'rgba(248,81,73,.08)', border: '1px solid rgba(248,81,73,.25)',
      }}>
        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>
          Problem Statement
        </div>
        <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.5 }}>
          {String(analysis.problem_statement ?? '')}
        </div>
      </div>

      {/* Why Steps */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 16 }}>
        {whys.map((why, i) => {
          const step = Number(why.step ?? i + 1)
          const isOpen = openStep === step
          return (
            <div
              key={step}
              style={{
                borderRadius: 6,
                border: `1px solid ${isOpen ? 'var(--accent)' : 'var(--border)'}`,
                overflow: 'hidden',
                transition: 'border-color 0.15s',
              }}
            >
              {/* Step header — always visible */}
              <div
                onClick={() => setOpenStep(isOpen ? null : step)}
                style={{
                  display: 'flex', gap: 10, alignItems: 'flex-start',
                  padding: '10px 14px', cursor: 'pointer',
                  background: isOpen ? 'var(--surface2)' : 'var(--bg)',
                  transition: 'background 0.15s',
                }}
              >
                {/* Step pill */}
                <div style={{
                  width: 24, height: 24, borderRadius: '50%', flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: isOpen ? 'var(--accent)' : 'var(--surface2)',
                  color: isOpen ? '#000' : 'var(--text-muted)',
                  fontSize: 11, fontWeight: 700, marginTop: 1,
                  transition: 'background 0.15s, color 0.15s',
                }}>
                  {step}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 2 }}>
                    {String(why.question ?? '')}
                  </div>
                  <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 500, lineHeight: 1.4 }}>
                    {String(why.answer ?? '')}
                  </div>
                </div>
                <svg
                  viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
                  style={{ width: 14, height: 14, flexShrink: 0, color: 'var(--text-muted)', marginTop: 4, transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}
                >
                  <path d="M6 4l4 4-4 4" />
                </svg>
              </div>

              {/* Step detail — expanded */}
              {isOpen && (
                <div style={{ padding: '0 14px 12px 48px', background: 'var(--surface2)' }}>
                  {why.component && (
                    <div style={{ marginBottom: 6 }}>
                      <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>Component</span>
                      <span style={{ marginLeft: 8, fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--accent)' }}>
                        {String(why.component)}
                      </span>
                    </div>
                  )}
                  {why.evidence && (
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>
                        Evidence
                      </div>
                      <div style={{
                        fontSize: 12, color: 'var(--text)', lineHeight: 1.5,
                        background: 'var(--bg)', borderRadius: 4,
                        padding: '8px 10px', border: '1px solid var(--border)',
                        fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                      }}>
                        {String(why.evidence)}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Fundamental Root Cause */}
      {analysis.fundamental_root_cause && (
        <div style={{
          padding: '10px 14px', borderRadius: 6,
          background: 'rgba(63,185,80,.08)', border: '1px solid rgba(63,185,80,.3)',
        }}>
          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--green)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>
            Fundamental Root Cause
          </div>
          <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.5 }}>
            {String(analysis.fundamental_root_cause)}
          </div>
        </div>
      )}
    </div>
  )
}

function RCASummary({ output }: { output: Record<string, unknown> }) {
  const rca = output.rca as Record<string, unknown> | undefined
  if (!rca) return <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>No RCA data</span>
  const rc = rca.root_cause as Record<string, unknown> | undefined
  const fw = rca.five_why_analysis as Record<string, unknown> | undefined
  const causalChain = (rca.causal_chain as Record<string, unknown>[]) ?? []
  const blastRadius = (rca.blast_radius as string[]) ?? []
  const factors = (rca.contributing_factors as Record<string, unknown>[]) ?? []

  return (
    <div>
      {/* RCA Summary text */}
      {rca.rca_summary && (
        <InfoRow label="Summary">{String(rca.rca_summary)}</InfoRow>
      )}
      <InfoRow label="Confidence">
        <ConfidenceBadge value={rca.confidence as number} />
      </InfoRow>

      {/* Root Cause */}
      {rc && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
            Root Cause
          </div>
          <div style={{
            padding: '10px 14px', borderRadius: 6,
            background: 'var(--bg)', border: '1px solid var(--border)',
          }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
              <span className="badge badge-failed">{String(rc.category ?? '')}</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent)', fontSize: 12 }}>
                {String(rc.component ?? '')}
              </span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.5 }}>
              {String(rc.description ?? '')}
            </div>
          </div>
        </div>
      )}

      {/* Causal Chain */}
      {causalChain.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
            Causal Chain ({causalChain.length} links)
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {causalChain.map((link, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
                <span style={{ padding: '2px 8px', borderRadius: 4, background: 'var(--surface2)', border: '1px solid var(--border)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                  {String(link.source_event ?? '')}
                </span>
                <span style={{ color: 'var(--text-muted)', flexShrink: 0, fontSize: 10 }}>
                  —{String(link.link_type ?? '').replace(/_/g, ' ')}→
                </span>
                <span style={{ padding: '2px 8px', borderRadius: 4, background: 'var(--surface2)', border: '1px solid var(--border)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                  {String(link.target_event ?? '')}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Blast Radius */}
      {blastRadius.length > 0 && (
        <InfoRow label="Blast Radius">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {blastRadius.map((c, i) => (
              <span key={i} style={{
                padding: '2px 8px', borderRadius: 4, fontSize: 11,
                background: 'rgba(248,81,73,.1)', border: '1px solid rgba(248,81,73,.25)',
                color: 'var(--red)', fontFamily: 'var(--font-mono)',
              }}>{c}</span>
            ))}
          </div>
        </InfoRow>
      )}

      {/* Contributing Factors */}
      {factors.length > 0 && (
        <InfoRow label="Contributing Factors">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {factors.map((f, i) => (
              <div key={i} style={{ fontSize: 12 }}>
                <span style={{ color: 'var(--orange)' }}>({String(f.severity ?? '')})</span>
                <span style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)', marginLeft: 6, marginRight: 6 }}>
                  {String(f.component ?? '')}
                </span>
                <span style={{ color: 'var(--text)' }}>{String(f.factor ?? '')}</span>
              </div>
            ))}
          </div>
        </InfoRow>
      )}

      {/* Five Whys */}
      {fw && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>
            Five Whys Analysis
          </div>
          <FiveWhysView analysis={fw} />
        </div>
      )}
    </div>
  )
}

function RecommendationSummary({ output }: { output: Record<string, unknown> }) {
  const rec = output.recommendations as Record<string, unknown> | undefined
  if (!rec) return <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>No recommendation data</span>
  const solutions = (rec.solutions as Record<string, unknown>[]) ?? []

  return (
    <div>
      {rec.recommendation_summary && (
        <InfoRow label="Summary">{String(rec.recommendation_summary)}</InfoRow>
      )}
      {rec.root_cause_addressed && (
        <InfoRow label="Addresses">{String(rec.root_cause_addressed)}</InfoRow>
      )}
      <InfoRow label="Confidence">
        <ConfidenceBadge value={rec.confidence as number} />
      </InfoRow>

      {solutions.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>
            Solutions ({solutions.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {solutions
              .slice()
              .sort((a, b) => Number(a.rank ?? 0) - Number(b.rank ?? 0))
              .map((sol, i) => {
                const isRootCause = Boolean(sol.addresses_root_cause)
                return (
                  <div key={i} style={{
                    borderRadius: 8,
                    border: `1px solid ${isRootCause ? 'rgba(88,166,255,.4)' : 'var(--border)'}`,
                    background: isRootCause ? 'rgba(88,166,255,.05)' : 'var(--bg)',
                    overflow: 'hidden',
                  }}>
                    {/* Solution header */}
                    <div style={{
                      display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
                      padding: '10px 14px', borderBottom: '1px solid var(--border)',
                      background: 'var(--surface2)',
                    }}>
                      <span style={{
                        width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        background: isRootCause ? 'var(--accent)' : 'var(--border)',
                        color: isRootCause ? '#000' : 'var(--text-muted)',
                        fontSize: 11, fontWeight: 700,
                      }}>{String(sol.rank ?? i + 1)}</span>
                      <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text)', flex: 1 }}>
                        {String(sol.title ?? '')}
                      </span>
                      {isRootCause && (
                        <span style={{
                          fontSize: 10, padding: '1px 6px', borderRadius: 3,
                          background: 'rgba(88,166,255,.15)', color: 'var(--accent)',
                          border: '1px solid rgba(88,166,255,.3)', fontWeight: 600,
                        }}>Root Cause Fix</span>
                      )}
                      <span style={{
                        fontSize: 11, padding: '2px 8px', borderRadius: 12,
                        background: 'var(--surface)', border: '1px solid var(--border)',
                        color: effortColor(String(sol.effort ?? '')),
                      }}>
                        {String(sol.effort ?? '').replace(/_/g, ' ')}
                      </span>
                      <span style={{
                        fontSize: 11, padding: '2px 8px', borderRadius: 12,
                        background: 'var(--surface)', border: '1px solid var(--border)',
                        color: 'var(--text-muted)',
                      }}>
                        {String(sol.category ?? '').replace(/_/g, ' ')}
                      </span>
                    </div>

                    {/* Solution body */}
                    <div style={{ padding: '10px 14px' }}>
                      <p style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.55, marginBottom: 8 }}>
                        {String(sol.description ?? '')}
                      </p>
                      {sol.expected_outcome && (
                        <div style={{
                          fontSize: 12, color: 'var(--green)',
                          padding: '6px 10px', borderRadius: 4,
                          background: 'rgba(63,185,80,.07)', border: '1px solid rgba(63,185,80,.2)',
                        }}>
                          Expected: {String(sol.expected_outcome)}
                        </div>
                      )}
                      {(sol.affected_components as string[] | undefined)?.length ? (
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
                          {(sol.affected_components as string[]).map((c, j) => (
                            <span key={j} style={{
                              fontSize: 11, padding: '1px 6px', borderRadius: 3,
                              background: 'var(--surface2)', border: '1px solid var(--border)',
                              fontFamily: 'var(--font-mono)', color: 'var(--text-muted)',
                            }}>{c}</span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>
                )
              })}
          </div>
        </div>
      )}
    </div>
  )
}

function AgentSummary({ agent, output }: { agent: AgentName; output: Record<string, unknown> }) {
  switch (agent) {
    case 'normalization': return <NormalizationSummary output={output} />
    case 'correlation': return <CorrelationSummary output={output} />
    case 'error_analysis': return <ErrorAnalysisSummary output={output} />
    case 'rca': return <RCASummary output={output} />
    case 'recommendation': return <RecommendationSummary output={output} />
  }
}

// ── Log viewer ─────────────────────────────────────────────────────────

function LogDetailPanel({ entry, onClose }: { entry: LogEntry; onClose: () => void }) {
  const meta = entry.metadata && Object.keys(entry.metadata).length > 0 ? entry.metadata : null
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'rgba(0,0,0,.35)' }} />
      <div style={{
        position: 'fixed', right: 0, top: 0, bottom: 0, width: 420, zIndex: 9999,
        background: 'var(--surface)', borderLeft: '1px solid var(--border)',
        display: 'flex', flexDirection: 'column', boxShadow: '-8px 0 32px rgba(0,0,0,.4)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
          <span className={`log-source log-source-${entry.source}`}>{entry.source}</span>
          {entry.level && <span style={{ fontSize: 11, fontWeight: 700, color: levelColor(entry.level) }}>{entry.level.toUpperCase()}</span>}
          {entry.service && <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--accent)' }}>{entry.service}</span>}
          <button onClick={onClose} style={{ marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 18, lineHeight: 1, padding: '2px 6px', borderRadius: 4 }}>×</button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          {entry.timestamp && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>Timestamp</div>
              <div style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>{entry.timestamp}</div>
            </div>
          )}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Message</div>
            <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.6, wordBreak: 'break-word', whiteSpace: 'pre-wrap', background: 'var(--bg)', borderRadius: 6, padding: '10px 12px', border: '1px solid var(--border)' }}>
              {entry.message}
            </div>
          </div>
          {meta ? (
            <div>
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Metadata</div>
              <pre style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'var(--bg)', borderRadius: 6, padding: 12, border: '1px solid var(--border)', lineHeight: 1.6 }}>
                {JSON.stringify(meta, null, 2)}
              </pre>
            </div>
          ) : <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>No metadata</div>}
        </div>
      </div>
    </>
  )
}

function LogList({ logs, dataSources, logsCount }: { logs: LogEntry[]; dataSources?: string[]; logsCount?: number }) {
  const [selected, setSelected] = useState<LogEntry | null>(null)
  const effectiveSources = (dataSources && dataSources.length > 0) ? dataSources : [...new Set(logs.map(e => e.source))]

  if (effectiveSources.length === 0 && logs.length === 0) {
    return <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>No data sources queried yet</span>
  }
  if (logs.length === 0) {
    return (
      <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
        {effectiveSources.map(src => (
          <div key={src} style={{ padding: '4px 0' }}>
            <span className={`log-source log-source-${src}`}>{src}</span>
            <span style={{ marginLeft: 8 }}>Queried — waiting for entries…</span>
          </div>
        ))}
      </div>
    )
  }

  const bySource: Record<string, LogEntry[]> = {}
  for (const entry of logs) { ;(bySource[entry.source] ??= []).push(entry) }

  return (
    <div className="log-viewer" style={{ position: 'relative' }}>
      <div style={{ display: 'flex', gap: 8, padding: '4px 8px', borderBottom: '1px solid var(--border)', marginBottom: 4, flexWrap: 'wrap' }}>
        {effectiveSources.map(src => (
          <span key={src} style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            <span className={`log-source log-source-${src}`}>{src}</span>
            <span style={{ marginLeft: 4 }}>{bySource[src]?.length ?? 0} entries</span>
          </span>
        ))}
        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>{logsCount ?? logs.length} total</span>
      </div>
      {logs.map((entry, i) => (
        <div key={i} className="log-entry" style={{ cursor: 'pointer' }} title="Click to view full details" onClick={() => setSelected(entry)}>
          <span className={`log-source log-source-${entry.source}`}>{entry.source}</span>
          {entry.level && <span style={{ fontSize: 10, fontWeight: 700, color: levelColor(entry.level), minWidth: 36, textAlign: 'center' }}>{entry.level.slice(0, 4).toUpperCase()}</span>}
          {entry.service && <span style={{ fontSize: 11, color: 'var(--accent)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>{entry.service}</span>}
          {entry.timestamp && <span className="log-ts">{entry.timestamp.slice(11, 19)}</span>}
          <span className="log-msg">{entry.message}</span>
        </div>
      ))}
      {selected && <LogDetailPanel entry={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

// ── Main AgentCard ─────────────────────────────────────────────────────

type Tab = 'summary' | 'logs' | 'input' | 'output'

export default function AgentCard({ agent, state, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen || state.status !== 'pending')
  const [tab, setTab] = useState<Tab>('summary')

  const fetchedLogs: LogEntry[] = state.fetched_logs ?? []
  const complexity = computeComplexity(agent, state.output)
  const hasContent = !!(state.input || state.output || state.data_sources || fetchedLogs.length > 0)

  const tabs: { id: Tab; label: string }[] = [
    { id: 'summary', label: 'Summary' },
    { id: 'logs', label: `Logs (${fetchedLogs.length})` },
    { id: 'input', label: 'Input' },
    { id: 'output', label: 'Output' },
  ]

  return (
    <div className="card agent-card">
      {/* ── Header ─────────────────────────────────────── */}
      <div className="agent-card-header" onClick={() => setOpen(o => !o)}>
        <div className="agent-number" style={{ background: numberBg(state.status), color: statusColor(state.status) }}>
          {state.step}
        </div>
        <span className="agent-card-title">{AGENT_LABELS[agent]}</span>
        <div className="agent-meta">
          <span className={`badge badge-${state.status}`}>
            {state.status === 'running' && <span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} />}
            {state.status}
          </span>
          {state.processing_time_ms != null && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{state.processing_time_ms.toFixed(0)} ms</span>
          )}
          {state.data_sources?.map(src => (
            <span key={src} className={`badge badge-${src}`}>{src}</span>
          ))}
          {(state.logs_count ?? 0) > 0 && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{state.logs_count} logs</span>
          )}
          {complexity && (
            <span className={`complexity-${complexity.level}`} style={{ fontSize: 11 }}>{complexity.label}</span>
          )}
        </div>
        <svg className={`chevron${open ? ' open' : ''}`} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M6 4l4 4-4 4" />
        </svg>
      </div>

      {/* ── Expanded body ──────────────────────────────── */}
      {open && (
        <div className="agent-body">
          {/* Error */}
          {state.error && (
            <div className="agent-section">
              <div className="agent-section-title" style={{ color: 'var(--red)' }}>Error</div>
              <div className="agent-section-body">
                <pre style={{ color: 'var(--red)', fontFamily: 'var(--font-mono)', fontSize: 12, whiteSpace: 'pre-wrap' }}>
                  {state.error}
                </pre>
              </div>
            </div>
          )}

          {/* Tab bar + content */}
          {hasContent && (
            <div className="agent-section">
              {/* Tab bar */}
              <div className="agent-section-title" style={{ display: 'flex', gap: 0, padding: 0 }}>
                {tabs.map(t => (
                  <button
                    key={t.id}
                    onClick={() => setTab(t.id)}
                    style={{
                      padding: '8px 16px',
                      background: tab === t.id ? 'var(--surface)' : 'var(--bg)',
                      border: 'none',
                      borderBottom: tab === t.id ? '2px solid var(--accent)' : '2px solid transparent',
                      color: tab === t.id ? 'var(--text)' : 'var(--text-muted)',
                      cursor: 'pointer',
                      fontSize: 12,
                      fontWeight: tab === t.id ? 600 : 400,
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                    }}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {/* Tab content */}
              <div className="agent-section-body">
                {tab === 'summary' && (
                  state.output
                    ? <AgentSummary agent={agent} output={state.output} />
                    : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                        {state.status === 'running' ? 'Processing…' : 'No output yet'}
                      </span>
                )}
                {tab === 'logs' && (
                  <LogList logs={fetchedLogs} dataSources={state.data_sources} logsCount={state.logs_count} />
                )}
                {tab === 'input' && (
                  state.input
                    ? <JsonView data={state.input} />
                    : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>No input yet</span>
                )}
                {tab === 'output' && (
                  state.output
                    ? <JsonView data={state.output} />
                    : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                        {state.status === 'running' ? 'Processing…' : 'No output yet'}
                      </span>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
