export type DbType = 'sqlite' | 'postgresql' | 'csv' | 'excel'
export type LlmProvider = 'groq' | 'ollama'

export interface Session {
  session_id: string
  type: DbType
  name: string
  tables: string[]
  path?: string
  host?: string
  database?: string
  file?: string
  rows?: number
  columns?: string[]
  sheets?: string[]
}

export interface AgentStep {
  type: 'tool_start' | 'tool_end'
  tool: string
  input?: string
  output?: string
}

export interface TokenUsage {
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  latency_ms?: number
  // New per-agent breakdown surfaced by the multi-agent orchestrator.
  // Old runs / persisted messages won't have these.
  intent_latency_ms?: number
  plan_latency_ms?: number
  insight_latency_ms?: number
  total_elapsed_ms?: number
}

export interface ChartResult {
  id: string
  option: Record<string, unknown>
  title: string
  sql: string
}

export interface TableResult {
  id: string
  columns: string[]
  rows: unknown[][]
  sql: string
  title: string
}

// ---------------------------------------------------------------------------
// Multi-agent pipeline types (mirror the backend Pydantic models)
// ---------------------------------------------------------------------------

export type IntentLabel =
  | 'greeting' | 'help' | 'simple_qa' | 'metric'
  | 'exploration' | 'dashboard' | 'report' | 'comparison'

export type ExportFormat = 'pdf' | 'excel' | 'csv'

export interface IntentInfo {
  intent: IntentLabel
  wants_chart: boolean
  wants_dashboard: boolean
  wants_export: ExportFormat | null
  chart_hints: string[]
  time_window: string | null
  complexity: 'simple' | 'moderate' | 'complex'
  keywords: string[]
  confidence: number
  latency_ms?: number
}

export interface LayoutSlot {
  visual_id: string
  width: number
}

export interface LayoutRow {
  slots: LayoutSlot[]
}

export interface PlanInfo {
  title: string
  description: string
  query_count: number
  visual_count: number
  layout: LayoutRow[]
  latency_ms?: number
}

export interface KPIPayload {
  label: string
  value: unknown
  formatted_value: string
  unit: string | null
  sparkline: number[]
}

export interface RenderedVisual {
  visual_id: string
  visual_type: string
  title: string
  subtitle: string | null
  from_query: string
  kpi: KPIPayload | null
  echarts_option: Record<string, unknown> | null
  table_columns: string[]
  table_rows: unknown[][]
  rows_count: number
  error: string | null
}

export interface InsightReport {
  headline: string
  executive_summary: string
  key_findings: string[]
  anomalies: string[]
  recommendations: string[]
  latency_ms?: number
}

export interface CritiqueIssue {
  severity: 'info' | 'warning' | 'error'
  category: string
  message: string
  location: string | null
}

export interface CritiqueReport {
  passed: boolean
  score: number
  issues: CritiqueIssue[]
  latency_ms?: number
}

export interface QueryProgress {
  query_id: string
  purpose?: string
  success?: boolean
  rows_count?: number
  latency_ms?: number
  repaired?: boolean
  error?: string | null
  status: 'pending' | 'running' | 'done' | 'error'
}

/** Alias kept for new multi-agent code clarity; same shape as TokenUsage. */
export type PipelineUsage = TokenUsage

export interface AnalysisResult {
  id: string
  question: string
  status: 'running' | 'done' | 'error'
  startedAt: Date

  insight: string
  charts: ChartResult[]
  tables: TableResult[]
  steps: AgentStep[]

  exportSql?: string
  exportSessionId?: string
  usage?: TokenUsage
  error?: string

  // Server-side identifiers once the conversation/persistence flow links them.
  // Used to reconcile streamed runs with persisted messages on reload.
  conversationId?: string
  messageId?: string

  // ── Multi-agent pipeline payloads ─────────────────────────────────────
  // All optional; old/chat-style answers won't have any of these.
  intentInfo?: IntentInfo
  planInfo?: PlanInfo
  visuals?: RenderedVisual[]
  insightReport?: InsightReport
  critique?: CritiqueReport
  queryProgress?: QueryProgress[]
}

export interface Conversation {
  id: string
  title: string
  connection_id: string | null
  model: string | null
  provider: string | null
  created_at: string | null
  updated_at: string | null
  message_count: number
}

export interface PersistedMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  charts: ChartResult[]
  tables: TableResult[]
  steps: AgentStep[]
  usage?: TokenUsage | null
  export_sql?: string | null
  status: 'running' | 'done' | 'error'
  error?: string | null
  created_at: string | null
  visuals?: RenderedVisual[] | null
  insight_report?: InsightReport | null
  critique?: CritiqueReport | null
}

export interface UserPreferences {
  model: string | null
  provider: LlmProvider | null
  active_connection_id: string | null
  active_conversation_id: string | null
  updated_at?: string | null
}

export interface ModelOption {
  id: string
  label: string
}

export const GROQ_MODELS: ModelOption[] = [
  { id: 'llama-3.3-70b-versatile',                    label: 'Llama 3.3 70B' },
  { id: 'llama-3.1-8b-instant',                       label: 'Llama 3.1 8B (fast)' },
  { id: 'openai/gpt-oss-120b',                        label: 'GPT-OSS 120B' },
  { id: 'openai/gpt-oss-20b',                         label: 'GPT-OSS 20B' },
  { id: 'groq/compound',                              label: 'Groq Compound' },
  { id: 'groq/compound-mini',                         label: 'Groq Compound Mini' },
  { id: 'meta-llama/llama-4-scout-17b-16e-instruct',  label: 'Llama 4 Scout 17B' },
  { id: 'qwen/qwen3-32b',                             label: 'Qwen3 32B' },
  { id: 'mixtral-8x7b-32768',                         label: 'Mixtral 8x7B' },
  { id: 'gemma2-9b-it',                               label: 'Gemma2 9B' },
]

export const OLLAMA_FALLBACK_MODELS: ModelOption[] = [
  { id: 'llama3.2',       label: 'Llama 3.2' },
  { id: 'llama3.1',       label: 'Llama 3.1' },
  { id: 'mistral',        label: 'Mistral 7B' },
  { id: 'codellama',      label: 'Code Llama' },
  { id: 'phi3',           label: 'Phi-3' },
  { id: 'qwen2.5-coder',  label: 'Qwen 2.5 Coder' },
]
