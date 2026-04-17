interface Props {
  value: number | null | undefined
  label?: string
}

export default function ConfidenceBadge({ value, label = 'Confidence' }: Props) {
  if (value == null) return null

  const pct = Math.round(value * 100)
  const cls = value >= 0.8 ? 'confidence-high' : value >= 0.5 ? 'confidence-mid' : 'confidence-low'

  return (
    <div className="confidence-bar" title={`${label}: ${pct}%`}>
      <span style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
        {label}
      </span>
      <div className="confidence-track">
        <div className={`confidence-fill ${cls}`} style={{ width: `${pct}%` }} />
      </div>
      <span style={{ fontSize: 11, fontWeight: 600, minWidth: 32 }}>{pct}%</span>
    </div>
  )
}
