import { ModelOption, Session } from '../types'

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

  streamQuery(
    sessionId: string,
    question: string,
    model: string,
    provider: string,
    callbacks: {
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
      onUsage: (u: {
        input_tokens: number
        output_tokens: number
        total_tokens: number
      }) => void
      onDone: () => void
      onError: (msg: string) => void
    }
  ): () => void {
    const controller = new AbortController()

    fetch(`${BASE}/query/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, question, model, provider }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok || !res.body) {
          const data = await res.json().catch(() => ({}))
          const msg =
            res.status === 404
              ? 'Session not found — the server may have restarted. Please reconnect your database.'
              : (data as { detail?: string }).detail ?? `HTTP ${res.status}`
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
                    input_tokens: (evt.input_tokens as number) ?? 0,
                    output_tokens: (evt.output_tokens as number) ?? 0,
                    total_tokens: (evt.total_tokens as number) ?? 0,
                  })
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
