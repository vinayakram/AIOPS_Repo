// Internationalization module providing all UI string translations for English and Japanese.
// Exports a UI lookup table keyed by string identifier and language code, plus a t() helper function.
// Covers navigation labels, form fields, agent names, pipeline status terms, and summary section titles.
// Falls back to the English value when a Japanese translation is missing for a given key.
//
// 英語と日本語のすべてのUI文字列翻訳を提供する国際化モジュール。
// 文字列識別子と言語コードをキーとするUIルックアップテーブルとt()ヘルパー関数をエクスポートする。
// ナビゲーションラベル、フォームフィールド、エージェント名、パイプラインステータス用語、サマリーセクションタイトルをカバーする。
// 指定キーの日本語翻訳が不足している場合は英語の値にフォールバックする。
export type Lang = 'en' | 'ja'

export const UI: Record<string, Record<Lang, string>> = {
  // Nav
  appTitle: { en: 'Investigation Monitor', ja: '調査モニター' },
  dashboard: { en: 'Dashboard', ja: 'ダッシュボード' },

  // Dashboard page
  pageTitle: { en: 'Investigation Flow Monitor', ja: '調査フローモニター' },
  pageSubtitle: {
    en: 'Trigger investigations and watch each agent step execute in real time.',
    ja: '調査を起動し、各エージェントのステップをリアルタイムで確認します。',
  },
  triggerCard: { en: 'Trigger New Investigation', ja: '新しい調査を開始' },
  agentNameLabel: { en: 'Agent Name', ja: 'エージェント名' },
  agentNamePlaceholder: { en: 'e.g. sample-agent, summarizer-v2', ja: '例: sample-agent、summarizer-v2' },
  traceIdLabel: { en: 'Trace ID', ja: 'トレースID' },
  traceIdPlaceholder: { en: 'trace-abc-123', ja: 'trace-abc-123' },
  timestampLabel: { en: 'Timestamp (ISO-8601)', ja: 'タイムスタンプ（ISO-8601）' },
  timestampPlaceholder: { en: '2025-01-15T10:32:00Z', ja: '2025-01-15T10:32:00Z' },
  now: { en: 'Now', ja: '現在' },
  startBtn: { en: '▶ Start Investigation', ja: '▶ 調査を開始' },
  starting: { en: 'Starting…', ja: '開始中…' },
  agentRequired: { en: 'Agent name is required', ja: 'エージェント名が必要です' },
  traceRequired: { en: 'Trace ID is required', ja: 'トレースIDが必要です' },
  timestampRequired: { en: 'Timestamp is required', ja: 'タイムスタンプが必要です' },

  // Recent traces table
  recentTraces: { en: 'Recent Traces', ja: '最近のトレース' },
  refresh: { en: 'Refresh', ja: '更新' },
  colTraceId: { en: 'Trace ID', ja: 'トレースID' },
  colAgent: { en: 'Agent', ja: 'エージェント' },
  colTimestamp: { en: 'Timestamp', ja: 'タイムスタンプ' },
  colStatus: { en: 'Status', ja: 'ステータス' },
  colCreated: { en: 'Created', ja: '作成日時' },
  colActions: { en: 'Actions', ja: 'アクション' },
  watchLive: { en: 'Watch Live', ja: 'ライブ監視' },
  viewDetail: { en: 'View Detail', ja: '詳細を表示' },
  noTraces: { en: 'No traces yet — trigger your first investigation above.', ja: 'トレースがありません — 上のフォームから最初の調査を開始してください。' },
  failedTraces: { en: 'Failed to load traces:', ja: 'トレースの読み込みに失敗しました:' },

  // Trace detail
  traceDetail: { en: 'Trace Detail', ja: 'トレース詳細' },
  backDashboard: { en: '← Dashboard', ja: '← ダッシュボード' },
  watchLiveArrow: { en: 'Watch Live →', ja: 'ライブ監視 →' },
  traceNotFound: { en: 'Trace not found', ja: 'トレースが見つかりません' },

  // Agent labels
  normalization: { en: 'Normalization', ja: '正規化' },
  correlation: { en: 'Correlation', ja: '相関分析' },
  error_analysis: { en: 'Error Analysis', ja: 'エラー分析' },
  rca: { en: 'Root Cause Analysis', ja: '根本原因分析' },
  recommendation: { en: 'Recommendation', ja: '推奨アクション' },

  // Agent card tabs
  tabSummary: { en: 'Summary', ja: 'サマリー' },
  tabLogs: { en: 'Logs', ja: 'ログ' },
  tabInput: { en: 'Input', ja: '入力' },
  tabOutput: { en: 'Output', ja: '出力' },

  // Agent card fields
  processing: { en: 'Processing…', ja: '処理中…' },
  noOutput: { en: 'No output yet', ja: 'まだ出力がありません' },
  noInput: { en: 'No input yet', ja: 'まだ入力がありません' },
  noData: { en: 'No data sources queried yet', ja: 'データソースがまだ照会されていません' },
  queried: { en: 'Queried — waiting for entries…', ja: '照会中 — エントリを待機しています…' },
  totalEntries: { en: 'total', ja: '合計' },
  error: { en: 'Error', ja: 'エラー' },

  // Normalization summary
  errorType: { en: 'Error Type', ja: 'エラー種別' },
  summary: { en: 'Summary', ja: 'サマリー' },
  confidence: { en: 'Confidence', ja: '信頼度' },
  signals: { en: 'Signals', ja: 'シグナル' },
  noIncidentData: { en: 'No incident data', ja: 'インシデントデータがありません' },

  // Correlation summary
  correlationChain: { en: 'Correlation Chain', ja: '相関チェーン' },
  rootCauseCandidate: { en: 'Root Cause Candidate', ja: '根本原因候補' },
  reason: { en: 'Reason', ja: '理由' },
  rcConfidence: { en: 'RC Confidence', ja: 'RC信頼度' },
  peerComponents: { en: 'Peer Components', ja: 'ピアコンポーネント' },
  timeline: { en: 'Timeline', ja: 'タイムライン' },
  noCorrelationData: { en: 'No correlation data', ja: '相関データがありません' },

  // Error analysis summary
  propagation: { en: 'Propagation', ja: '伝播経路' },
  errors: { en: 'Errors', ja: 'エラー' },
  patterns: { en: 'Patterns', ja: 'パターン' },
  noAnalysisData: { en: 'No analysis data', ja: '分析データがありません' },

  // RCA summary
  rootCause: { en: 'Root Cause', ja: '根本原因' },
  causalChain: { en: 'Causal Chain', ja: '因果チェーン' },
  links: { en: 'links', ja: 'リンク' },
  blastRadius: { en: 'Blast Radius', ja: '影響範囲' },
  contributingFactors: { en: 'Contributing Factors', ja: '寄与要因' },
  fiveWhysAnalysis: { en: 'Five Whys Analysis', ja: '5つのなぜ分析' },
  problemStatement: { en: 'Problem Statement', ja: '問題の陳述' },
  fundamentalRootCause: { en: 'Fundamental Root Cause', ja: '根本的な原因' },
  component: { en: 'Component', ja: 'コンポーネント' },
  evidence: { en: 'Evidence', ja: '証拠' },
  noRCAData: { en: 'No RCA data', ja: 'RCAデータがありません' },

  // Recommendation summary
  addresses: { en: 'Addresses', ja: '対象根本原因' },
  solutions: { en: 'Solutions', ja: 'ソリューション' },
  rootCauseFix: { en: 'Root Cause Fix', ja: '根本原因の修正' },
  expected: { en: 'Expected:', ja: '期待される効果:' },
  noRecommendationData: { en: 'No recommendation data', ja: '推奨データがありません' },

  // Live monitor
  liveMonitor: { en: 'Live Monitor', ja: 'ライブモニター' },
  connectionStatus: { en: 'Connection', ja: '接続状態' },
  connecting: { en: 'connecting', ja: '接続中' },
  connected: { en: 'connected', ja: '接続済み' },
  closed: { en: 'closed', ja: 'クローズ' },
  eventLog: { en: 'Event Log', ja: 'イベントログ' },
  noEvents: { en: 'Waiting for events…', ja: 'イベントを待機しています…' },

  // Theme toggle
  switchLight: { en: 'Switch to light mode', ja: 'ライトモードに切り替え' },
  switchDark: { en: 'Switch to dark mode', ja: 'ダークモードに切り替え' },
}

export function t(key: string, lang: Lang): string {
  return UI[key]?.[lang] ?? UI[key]?.['en'] ?? key
}
