// ── Log entry (from Langfuse / Prometheus) ────────────────────────────

export interface LogEntry {
  timestamp: string
  source: string
  service: string
  message: string
  level: string
  metadata?: Record<string, unknown>
}

// ── SSE Event Types ────────────────────────────────────────────────────

export type EventType =
  | 'connected'
  | 'pipeline_started'
  | 'step_started'
  | 'step_completed'
  | 'step_failed'
  | 'pipeline_completed'
  | 'logs_fetched'
  | 'error'
  | 'keepalive'

export interface BaseEvent {
  type: EventType
}

export interface PipelineStartedEvent extends BaseEvent {
  type: 'pipeline_started'
  trace_id: string
  agent_name: string
  timestamp: string
}

export interface StepStartedEvent extends BaseEvent {
  type: 'step_started'
  agent: AgentName
  step: number
  input: Record<string, unknown>
}

export interface StepCompletedEvent extends BaseEvent {
  type: 'step_completed'
  agent: AgentName
  step: number
  status: 'completed'
  processing_time_ms: number
  input: Record<string, unknown>
  output: Record<string, unknown>
  data_sources: string[]
  logs_count: number
  confidence: number | null
}

export interface StepFailedEvent extends BaseEvent {
  type: 'step_failed'
  agent: AgentName
  step: number
  error: string
  processing_time_ms: number
}

export interface PipelineCompletedEvent extends BaseEvent {
  type: 'pipeline_completed'
  trace_id: string
  completed: boolean
  total_processing_time_ms: number
  steps: PipelineStep[]
}

export interface LogsFetchedEvent extends BaseEvent {
  type: 'logs_fetched'
  agent: AgentName
  source: string
  count: number
  entries: LogEntry[]
}

export interface ErrorEvent extends BaseEvent {
  type: 'error'
  message: string
}

export type PipelineEvent =
  | BaseEvent
  | PipelineStartedEvent
  | StepStartedEvent
  | StepCompletedEvent
  | StepFailedEvent
  | PipelineCompletedEvent
  | LogsFetchedEvent
  | ErrorEvent

// ── Agent names ────────────────────────────────────────────────────────

export type AgentName =
  | 'normalization'
  | 'correlation'
  | 'error_analysis'
  | 'rca'
  | 'recommendation'

export const AGENT_LABELS: Record<AgentName, string> = {
  normalization: 'Normalization',
  correlation: 'Correlation',
  error_analysis: 'Error Analysis',
  rca: 'Root Cause Analysis',
  recommendation: 'Recommendation',
}

export const AGENT_ORDER: AgentName[] = [
  'normalization',
  'correlation',
  'error_analysis',
  'rca',
  'recommendation',
]

// ── Pipeline state (built from events) ────────────────────────────────

export type StepStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface AgentState {
  agent: AgentName
  step: number
  status: StepStatus
  processing_time_ms?: number
  input?: Record<string, unknown>
  output?: Record<string, unknown>
  data_sources?: string[]
  logs_count?: number
  confidence?: number | null
  error?: string
  fetched_logs?: LogEntry[]
}

export interface PipelineState {
  trace_id: string | null
  agent_name: string | null
  timestamp: string | null
  agents: Record<AgentName, AgentState>
  completed: boolean
  total_ms?: number
  error?: string
}

// ── Trace list (from /api/v1/traces) ──────────────────────────────────

export interface TraceSummary {
  trace_id: string
  agent_name: string
  timestamp: string
  status: 'running' | 'completed' | 'failed'
  created_at: string
  updated_at: string
}

// ── Trace detail (from /api/v1/traces/:traceId) ───────────────────────

export interface TraceDetail {
  trace_id: string
  agent_name: string
  timestamp: string
  status: string
  created_at: string
  updated_at: string
  normalization_input?: Record<string, unknown>
  normalization_output?: Record<string, unknown>
  correlation_input?: Record<string, unknown>
  correlation_output?: Record<string, unknown>
  error_analysis_input?: Record<string, unknown>
  error_analysis_output?: Record<string, unknown>
  rca_input?: Record<string, unknown>
  rca_output?: Record<string, unknown>
  recommendation_input?: Record<string, unknown>
  recommendation_output?: Record<string, unknown>
  fetched_logs?: Record<string, Record<string, LogEntry[]>>
}

export interface PipelineStep {
  agent: string
  status: string
  processing_time_ms: number
  error?: string
}
