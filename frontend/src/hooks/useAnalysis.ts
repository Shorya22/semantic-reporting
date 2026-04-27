import { useCallback } from 'react'
import { api } from '../api/client'
import { useStore } from '../store'

function nanoid(): string {
  return Math.random().toString(36).slice(2, 11)
}

export function useAnalysis() {
  const {
    activeSessionId,
    model,
    provider,
    isQuerying,
    startAnalysis,
    appendToken,
    addChart,
    addTable,
    addStep,
    setExportCtx,
    setUsage,
    finalizeAnalysis,
    setAnalysisError,
  } = useStore()

  const runAnalysis = useCallback(
    (question: string) => {
      if (!activeSessionId || isQuerying || !question.trim()) return

      const id = nanoid()
      startAnalysis(id, question)

      const cancel = api.streamQuery(activeSessionId, question, model, provider, {
        onToken: (t) => appendToken(id, t),
        onChart: (cid, option, title, sql) =>
          addChart(id, { id: cid, option, title, sql }),
        onTable: (tid, columns, rows, sql, title) =>
          addTable(id, { id: tid, columns, rows, sql, title }),
        onStep: (type, tool, input, output) =>
          addStep(id, { type, tool, input, output }),
        onExportCtx: (sql, sid) => setExportCtx(id, sql, sid),
        onUsage: (u) => setUsage(id, u),
        onDone: () => finalizeAnalysis(id),
        onError: (err) => {
          const isGone =
            err.includes('Session not found') || err.includes('reconnect')
          if (isGone) {
            useStore.setState((s) => ({
              sessions: s.sessions.filter(
                (x) => x.session_id !== s.activeSessionId
              ),
              activeSessionId: null,
            }))
          }
          setAnalysisError(id, err)
        },
      })

      return cancel
    },
    [
      activeSessionId,
      model,
      provider,
      isQuerying,
      startAnalysis,
      appendToken,
      addChart,
      addTable,
      addStep,
      setExportCtx,
      setUsage,
      finalizeAnalysis,
      setAnalysisError,
    ]
  )

  return { runAnalysis, isQuerying }
}
