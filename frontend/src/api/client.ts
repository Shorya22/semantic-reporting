import {
  Conversation,
  CritiqueReport,
  InsightReport,
  IntentInfo,
  ModelOption,
  PersistedMessage,
  PipelineUsage,
  PlanInfo,
  QueryProgress,
  RenderedVisual,
  Session,
  UserPreferences,
} from '../types'

const BASE = '/api/v1'

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(
      (err as { detail?: string; error?: string }).detail ??
      (err as { error?: string }).error ??
      `HTTP ${res.status}`
    )
  }
  const body = await res.json()
  return (body as { data: T }).data ?? body
}

async function downloadBlob(
  path: string,
  body: object,
  filename: string,
  mime: string
): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`Export failed: HTTP ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(new Blob([blob], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export const api = {
  getConfig: () =>
    apiFetch<{ default_model: string; llm_provider: string; ollama_base_url?: string }>('/config'),

  getOllamaModels: () => apiFetch<ModelOption[]>('/ollama/models'),

  // ---- Hydration ---------------------------------------------------------

  listConnections: (): Promise<(Session & { session_id: string })[]> =>
    apiFetch('/connections'),

  // ---- Conversations -----------------------------------------------------

  listConversations: (): Promise<Conversation[]> =>
    apiFetch('/conversations'),

  createConversation: (
    body: { title?: string; connection_id?: string | null; model?: string; provider?: string }
  ): Promise<Conversation> =>
    apiFetch('/conversations', { method: 'POST', body: JSON.stringify(body) }),

  getConversation: (
    id: string
  ): Promise<{ conversation: Conversation; messages: PersistedMessage[] }> =>
    apiFetch(`/conversations/${id}`),

  renameConversation: (id: string, title: string): Promise<Conversation> =>
    apiFetch(`/conversations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),

  updateConversation: (
    id: string,
    patch: Partial<Pick<Conversation, 'title' | 'connection_id' | 'model' | 'provider'>>
  ): Promise<Conversation> =>
    apiFetch(`/conversations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  deleteConversation: (id: string): Promise<{ deleted: string }> =>
    apiFetch(`/conversations/${id}`, { method: 'DELETE' }),

  // ---- Preferences -------------------------------------------------------

  getPreferences: (): Promise<UserPreferences> =>
    apiFetch('/preferences'),

  updatePreferences: (patch: Partial<UserPreferences>): Promise<UserPreferences> =>
    apiFetch('/preferences', { method: 'PATCH', body: JSON.stringify(patch) }),

  // ---- Connections -------------------------------------------------------

  connectSQLite: (db_path: string): Promise<Session & { session_id: string }> =>
    apiFetch('/connections/sqlite', {
      method: 'POST',
      body: JSON.stringify({ db_path }),
    }),

  connectPostgres: (cfg: {
    host: string
    port: number
    database: string
    user: string
    password: string
  }): Promise<Session & { session_id: string }> =>
    apiFetch('/connections/postgres', {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  uploadFile: (file: File): Promise<Session & { session_id: string }> => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`${BASE}/connections/upload`, { method: 'POST', body: fd })
      .then((r) => r.json())
      .then((r: { data: Session & { session_id: string } }) => r.data)
  },

  disconnect: (session_id: string): Promise<void> =>
    apiFetch(`/connections/${session_id}`, { method: 'DELETE' }),

  exportCsv: (sessionId: string, sql: string, title: string) =>
    downloadBlob(
      '/export/csv',
      { session_id: sessionId, sql, title },
      `${title}.csv`,
      'text/csv'
    ),

  exportExcel: (sessionId: string, sql: string, title: string) =>
    downloadBlob(
      '/export/excel',
      { session_id: sessionId, sql, title },
      `${title}.xlsx`,
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    ),

  exportPdf: (sessionId: string, sql: string, title: string) =>
    downloadBlob(
      '/export/pdf',
      { session_id: sessionId, sql, title },
      `${title}.pdf`,
      'application/pdf'
    ),

  downloadReport: (
    sessionId: string,
    question: string,
    format: 'pdf' | 'xlsx',
    title?: string
  ) =>
    downloadBlob(
      '/report',
      { session_id: sessionId, question, format, title },
      `${(title ?? 'report').replace(/\s+/g, '_')}.${format}`,
      format === 'xlsx'
        ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        : 'application/pdf'
    ),

  fetchExampleQueries: async (sessionId: string): Promise<string[]> => {
    try {
      const res = await fetch(`${BASE}/sessions/${sessionId}/example-queries`)
      const json = await res.json() as { data?: { examples?: string[] } }
      return json?.data?.examples ?? []
    } catch {
      return []
    }
  },

  streamQuery(
    sessionId: string,
    question: string,
    model: string,
    provider: string,
    callbacks: {
      onConversation?: (info: {
        conversation_id: string
        user_message_id?: string | null
        assistant_message_id?: string | null
        title?: string | null
      }) => void
      onToken: (t: string) => void
      onChart: (
        id: string,
        option: Record<string, unknown>,
        title: string,
        sql: string
      ) => void
      onTable: (
        id: string,
        columns: string[],
        rows: unknown[][],
        sql: string,
        title: string
      ) => void
      onStep: (
        type: 'tool_start' | 'tool_end',
        tool: string,
        input?: string,
        output?: string
      ) => void
      onExportCtx: (sql: string, sessionId: string) => void
      onUsage: (u: PipelineUsage) => void
      onDone: () => void
      onError: (msg: string) => void
      // ── Multi-agent pipeline callbacks (all optional for back-compat)
      onIntent?:    (info: IntentInfo) => void
      onPlan?:      (info: PlanInfo)   => void
      onQueryStart?: (q: QueryProgress) => void
      onQueryDone?:  (q: QueryProgress) => void
      onViz?:       (v: RenderedVisual) => void
      onLayout?:    (info: { title: string; layout: PlanInfo['layout']; visuals: RenderedVisual[] }) => void
      onInsight?:   (r: InsightReport) => void
      onCritique?:  (r: CritiqueReport) => void
    },
    conversationId?: string | null
  ): () => void {
    const controller = new AbortController()

    fetch(`${BASE}/query/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        question,
        model,
        provider,
        conversation_id: conversationId ?? null,
      }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok || !res.body) {
          const data = await res.json().catch(() => ({}))
          const detail = (data as { detail?: unknown }).detail
          let msg: string
          if (res.status === 404) {
            msg = 'Session not found — the server may have restarted. Please reconnect your database.'
          } else if (Array.isArray(detail)) {
            // FastAPI validation error: [{loc, msg, type, ctx}, ...]
            msg = detail
              .map((d) => {
                const e = d as { loc?: unknown[]; msg?: string }
                const field = Array.isArray(e.loc) ? e.loc.slice(1).join('.') : ''
                return field ? `${field}: ${e.msg ?? ''}` : (e.msg ?? '')
              })
              .filter(Boolean)
              .join('; ')
          } else if (typeof detail === 'string') {
            msg = detail
          } else {
            msg = `HTTP ${res.status}`
          }
          callbacks.onError(msg)
          return
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() ?? ''

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const evt = JSON.parse(line.slice(6)) as Record<string, unknown>
              switch (evt.type) {
                case 'conversation':
                  callbacks.onConversation?.({
                    conversation_id:      evt.conversation_id as string,
                    user_message_id:      (evt.user_message_id as string | null) ?? null,
                    assistant_message_id: (evt.assistant_message_id as string | null) ?? null,
                    title:                (evt.title as string | null) ?? null,
                  })
                  break
                case 'token':
                  callbacks.onToken((evt.content as string) ?? '')
                  break
                case 'chart_spec':
                  callbacks.onChart(
                    evt.id as string,
                    evt.option as Record<string, unknown>,
                    (evt.title as string) ?? '',
                    (evt.sql as string) ?? ''
                  )
                  break
                case 'table_data':
                  callbacks.onTable(
                    evt.id as string,
                    evt.columns as string[],
                    evt.rows as unknown[][],
                    (evt.sql as string) ?? '',
                    (evt.title as string) ?? ''
                  )
                  break
                case 'tool_start':
                  callbacks.onStep(
                    'tool_start',
                    evt.tool as string,
                    evt.input as string | undefined
                  )
                  break
                case 'tool_end':
                  callbacks.onStep(
                    'tool_end',
                    evt.tool as string,
                    undefined,
                    evt.output as string | undefined
                  )
                  break
                case 'export_ctx':
                  callbacks.onExportCtx(
                    evt.sql as string,
                    (evt.session_id as string) ?? sessionId
                  )
                  break
                case 'usage':
                  callbacks.onUsage({
                    input_tokens:        (evt.input_tokens as number) ?? 0,
                    output_tokens:       (evt.output_tokens as number) ?? 0,
                    total_tokens:        (evt.total_tokens as number) ?? 0,
                    latency_ms:          (evt.latency_ms as number) ?? 0,
                    intent_latency_ms:   (evt.intent_latency_ms as number) ?? undefined,
                    plan_latency_ms:     (evt.plan_latency_ms as number) ?? undefined,
                    insight_latency_ms:  (evt.insight_latency_ms as number) ?? undefined,
                    total_elapsed_ms:    (evt.total_elapsed_ms as number) ?? undefined,
                  })
                  break

                // ── Multi-agent pipeline events ─────────────────────
                case 'intent':
                  callbacks.onIntent?.(evt as unknown as IntentInfo)
                  break
                case 'plan':
                  callbacks.onPlan?.({
                    title:        (evt.title as string) ?? '',
                    description:  (evt.description as string) ?? '',
                    query_count:  (evt.query_count as number) ?? 0,
                    visual_count: (evt.visual_count as number) ?? 0,
                    layout:       (evt.layout as PlanInfo['layout']) ?? [],
                    latency_ms:   (evt.latency_ms as number) ?? undefined,
                  })
                  break
                case 'query_start':
                  callbacks.onQueryStart?.({
                    query_id: (evt.query_id as string) ?? '',
                    purpose:  (evt.purpose as string) ?? undefined,
                    status:   'running',
                  })
                  break
                case 'query_done':
                  callbacks.onQueryDone?.({
                    query_id:   (evt.query_id as string) ?? '',
                    success:    (evt.success as boolean) ?? false,
                    rows_count: (evt.rows_count as number) ?? 0,
                    latency_ms: (evt.latency_ms as number) ?? 0,
                    repaired:   (evt.repaired as boolean) ?? false,
                    error:      (evt.error as string | null) ?? null,
                    status:     evt.success ? 'done' : 'error',
                  })
                  break
                case 'viz':
                  callbacks.onViz?.(evt as unknown as RenderedVisual)
                  break
                case 'dashboard_layout':
                  callbacks.onLayout?.({
                    title:   (evt.title as string) ?? '',
                    layout:  (evt.layout as PlanInfo['layout']) ?? [],
                    visuals: (evt.visuals as RenderedVisual[]) ?? [],
                  })
                  break
                case 'insight':
                  callbacks.onInsight?.(evt as unknown as InsightReport)
                  break
                case 'critique':
                  callbacks.onCritique?.(evt as unknown as CritiqueReport)
                  break

                case 'done':
                  callbacks.onDone()
                  break
                case 'error':
                  callbacks.onError((evt.content as string) ?? 'Unknown error')
                  break
              }
            } catch {
              /* skip malformed lines */
            }
          }
        }
      })
      .catch((err: Error) => {
        if (err.name !== 'AbortError') callbacks.onError(err.message)
      })

    return () => controller.abort()
  },
}
