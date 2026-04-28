import { useCallback } from 'react'
import { api } from '../api/client'
import { useStore } from '../store'

function nanoid(): string {
  return Math.random().toString(36).slice(2, 11)
}

/**
 * Drive an analysis run end-to-end:
 *   * start a local analysis card (optimistic UI)
 *   * stream from /api/v1/query/stream, threading in the active
 *     conversation_id so the backend persists both prompt and reply
 *   * when the server emits the `conversation` event we sync
 *     activeConversationId + the conversations list, so the sidebar
 *     reflects the new thread immediately
 *   * on stream end, refresh the conversations list to pick up the
 *     bumped `updated_at` ordering and the auto-generated title
 */
export function useAnalysis() {
  const {
    activeSessionId,
    activeConversationId,
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
    attachServerIds,
    setActiveConversation,
    upsertConversation,
    setCurrentAbort,
    setIntent,
    setPlanInfo,
    upsertQueryProgress,
    addVisual,
    setInsightReport,
    setCritique,
  } = useStore()

  const runAnalysis = useCallback(
    (question: string) => {
      if (!activeSessionId || isQuerying || !question.trim()) return

      const id = nanoid()
      startAnalysis(id, question)

      const cancel = api.streamQuery(
        activeSessionId,
        question,
        model,
        provider,
        {
          onConversation: (info) => {
            attachServerIds(id, info.conversation_id, info.assistant_message_id)
            // First message in a brand-new conversation → bind the active
            // thread so future runs continue in this same conversation.
            if (!activeConversationId) {
              setActiveConversation(info.conversation_id)
            }
            // Optimistically reflect the new (or updated) conversation in
            // the sidebar — server reorders by updated_at so we mirror that.
            upsertConversation({
              id:            info.conversation_id,
              title:         info.title ?? 'New chat',
              connection_id: activeSessionId,
              model,
              provider,
              created_at:    new Date().toISOString(),
              updated_at:    new Date().toISOString(),
              message_count: 0,  // backend recomputes on next list call
            })
          },
          onToken: (t) => appendToken(id, t),
          onChart: (cid, option, title, sql) =>
            addChart(id, { id: cid, option, title, sql }),
          onTable: (tid, columns, rows, sql, title) =>
            addTable(id, { id: tid, columns, rows, sql, title }),
          onStep: (type, tool, input, output) =>
            addStep(id, { type, tool, input, output }),
          onExportCtx: (sql, sid) => setExportCtx(id, sql, sid),
          onUsage: (u) => setUsage(id, u),
          // ── Multi-agent pipeline events ────────────────────────────
          onIntent:      (info) => setIntent(id, info),
          onPlan:        (info) => setPlanInfo(id, info),
          onQueryStart:  (q)    => upsertQueryProgress(id, q),
          onQueryDone:   (q)    => upsertQueryProgress(id, q),
          onViz:         (v)    => addVisual(id, v),
          onLayout:      (_)    => { /* layout already implied by visuals + planInfo */ },
          onInsight:     (r)    => setInsightReport(id, r),
          onCritique:    (r)    => setCritique(id, r),
          onDone: () => {
            finalizeAnalysis(id)
            setCurrentAbort(null)
            // Re-pull conversations to reflect the new ordering + final title.
            api.listConversations()
              .then((list) => useStore.getState().setConversations(list))
              .catch(() => {})
          },
          onError: (err) => {
            const isGone =
              err.includes('Session not found') || err.includes('reconnect')
            if (isGone) {
              useStore.setState((s) => ({
                sessions:        s.sessions.filter((x) => x.session_id !== s.activeSessionId),
                activeSessionId: null,
              }))
            }
            setAnalysisError(id, err)
            setCurrentAbort(null)
          },
        },
        activeConversationId,
      )

      // Expose the cancel function to the rest of the app — the QueryBar's
      // Stop button and the `/stop` slash command both consume this.
      setCurrentAbort(cancel)
      return cancel
    },
    [
      activeSessionId,
      activeConversationId,
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
      attachServerIds,
      setActiveConversation,
      upsertConversation,
      setCurrentAbort,
      setIntent,
      setPlanInfo,
      upsertQueryProgress,
      addVisual,
      setInsightReport,
      setCritique,
    ],
  )

  return { runAnalysis, isQuerying }
}
