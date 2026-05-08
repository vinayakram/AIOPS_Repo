// Reusable confidence indicator component that renders a horizontal progress bar with a percentage label.
// Color-codes the fill: green for high confidence (≥80%), yellow for medium (≥50%), red for low (<50%).
// Accepts a nullable numeric value in the 0.0–1.0 range; renders nothing when the value is null.
//
// 水平プログレスバーとパーセンテージラベルをレンダリングする再利用可能な信頼度インジケーターコンポーネント。
// 高信頼度（≥80%）は緑、中程度（≥50%）は黄色、低（<50%）は赤でフィルを色分けする。
// 0.0〜1.0の範囲のnull許容数値を受け付け、値がnullの場合は何もレンダリングしない。
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
