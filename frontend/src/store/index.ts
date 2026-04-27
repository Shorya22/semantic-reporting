import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import {
  AnalysisResult,
  AgentStep,
  ChartResult,
  Conversation,
  LlmProvider,
  ModelOption,
  PersistedMessage,
  Session,
  TableResult,
  TokenUsage,
} from '../types'

/**
 * Application state.
 *
 * The store is split conceptually:
 *   * **Persisted slice** — UI prefs that should survive a refresh
 *     (model, provider, active conversation/session). Stored in
 *     `localStorage` via zustand `persist` middleware. The server is the
 *     authoritative source; localStorage just gives us instant UI on
 *     reload while we re-fetch from the backend.
 *   * **Ephemeral slice** — the live in-flight analyses, the loaded
 *     `analyses` list for the current conversation, and the connections
 *     list. These are hydrated from the backend on mount and re-fetched
 *     when the user switches conversations.
 *
 * Why not persist everything? Because charts/tables can be large, and
 * server-persisted truth is already perfect — duplicating it in
 * localStorage would just create a sync nightmare.
 */
interface AppStore {
  // ---- Persisted prefs ----------------------------------------------------
  model: string
  provider: LlmProvider
  activeSessionId: string | null
  activeConversationId: string | null
  sidebarCollapsed: boolean

  // ---- Ephemeral state ----------------------------------------------------
  hydrated: boolean
  sessions: Session[]
  conversations: Conversation[]
  analyses: AnalysisResult[]
  isQuerying: boolean
  ollamaModels: ModelOption[]
  activeAnalysisId: string | null
  /** Abort handle for the in-flight stream. Wired by useAnalysis; consumed
   * by `stopAnalysis` and the `/stop` slash command. Never persisted. */
  currentAbort: (() => void) | null

  // ---- Pref setters -------------------------------------------------------
  setModel: (m: string) => void
  setProvider: (p: LlmProvider) => void
  setActiveSession: (id: string | null) => void
  setActiveConversation: (id: string | null) => void
  setSidebarCollapsed: (v: boolean) => void
  toggleSidebar: () => void

  // ---- Hydration ----------------------------------------------------------
  setHydrated: (h: boolean) => void
  setSessions: (s: Session[]) => void
  setConversations: (c: Conversation[]) => void
  upsertConversation: (c: Conversation) => void
  removeConversation: (id: string) => void

  // ---- Sessions / connections --------------------------------------------
  addSession: (s: Session) => void
  removeSession: (id: string) => void

  // ---- Analyses (current conversation thread) ----------------------------
  setAnalyses: (a: AnalysisResult[]) => void
  loadAnalysesFromMessages: (messages: PersistedMessage[]) => void
  startAnalysis: (id: string, question: string) => void
  appendToken: (id: string, token: string) => void
  addChart: (id: string, chart: ChartResult) => void
  addTable: (id: string, table: TableResult) => void
  addStep: (id: string, step: AgentStep) => void
  setExportCtx: (id: string, sql: string, sessionId: string) => void
  setUsage: (id: string, usage: TokenUsage) => void
  finalizeAnalysis: (id: string) => void
  setAnalysisError: (id: string, error: string) => void
  attachServerIds: (id: string, conversationId: string, messageId?: string | null) => void
  setActiveAnalysis: (id: string | null) => void

  // ---- Stream control -----------------------------------------------------
  setCurrentAbort: (fn: (() => void) | null) => void
  stopAnalysis: () => void

  // ---- Synthetic info / error cards (used by slash commands) -------------
  emitInfoCard:  (title: string, markdown: string) => void
  emitErrorCard: (title: string, message: string)  => void

  // ---- Misc ---------------------------------------------------------------
  setOllamaModels: (models: ModelOption[]) => void
  reset: () => void
}


/**
 * Adapt a persisted assistant message back into our streaming
 * `AnalysisResult` shape so the UI renders identically whether the run
 * just completed or was reloaded after a refresh.
 */
function messagesToAnalyses(messages: PersistedMessage[]): AnalysisResult[] {
  const out: AnalysisResult[] = []
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]
    if (msg.role !== 'assistant') continue
    const prev = i > 0 ? messages[i - 1] : null
    const question = prev && prev.role === 'user' ? prev.content : '(question unavailable)'
    out.push({
      id: msg.id,
      question,
      status: msg.status,
      startedAt: msg.created_at ? new Date(msg.created_at) : new Date(),
      insight: msg.content,
      charts: msg.charts ?? [],
      tables: msg.tables ?? [],
      steps:  msg.steps  ?? [],
      exportSql:       msg.export_sql ?? undefined,
      usage:           msg.usage ?? undefined,
      error:           msg.error ?? undefined,
      conversationId:  msg.conversation_id,
      messageId:       msg.id,
    })
  }
  return out
}


export const useStore = create<AppStore>()(
  persist(
    (set) => ({
      // ---- Persisted defaults ---------------------------------------------
      model: 'llama-3.3-70b-versatile',
      provider: 'groq',
      activeSessionId: null,
      activeConversationId: null,
      sidebarCollapsed: false,

      // ---- Ephemeral defaults ---------------------------------------------
      hydrated: false,
      sessions: [],
      conversations: [],
      analyses: [],
      isQuerying: false,
      ollamaModels: [],
      activeAnalysisId: null,
      currentAbort: null,

      // ---- Pref setters ---------------------------------------------------
      setModel:               (model) => set({ model }),
      setProvider:            (provider) => set({ provider }),
      setActiveSession:       (id) => set({ activeSessionId: id }),
      setActiveConversation:  (id) => set({ activeConversationId: id }),
      setSidebarCollapsed:    (v) => set({ sidebarCollapsed: v }),
      toggleSidebar:          () => set((st) => ({ sidebarCollapsed: !st.sidebarCollapsed })),

      // ---- Hydration ------------------------------------------------------
      setHydrated:      (hydrated) => set({ hydrated }),
      setSessions:      (sessions) => set({ sessions }),
      setConversations: (conversations) => set({ conversations }),

      upsertConversation: (c) =>
        set((st) => {
          const idx = st.conversations.findIndex((x) => x.id === c.id)
          if (idx === -1) return { conversations: [c, ...st.conversations] }
          const next = [...st.conversations]
          next[idx] = { ...next[idx], ...c }
          return { conversations: next }
        }),

      removeConversation: (id) =>
        set((st) => ({
          conversations:        st.conversations.filter((c) => c.id !== id),
          activeConversationId: st.activeConversationId === id ? null : st.activeConversationId,
          analyses:             st.activeConversationId === id ? [] : st.analyses,
        })),

      // ---- Sessions -------------------------------------------------------
      addSession: (s) =>
        set((st) => ({
          sessions:        st.sessions.some((x) => x.session_id === s.session_id)
            ? st.sessions.map((x) => (x.session_id === s.session_id ? s : x))
            : [...st.sessions, s],
          activeSessionId: s.session_id,
        })),

      removeSession: (id) =>
        set((st) => ({
          sessions:        st.sessions.filter((x) => x.session_id !== id),
          activeSessionId: st.activeSessionId === id ? null : st.activeSessionId,
        })),

      // ---- Analyses -------------------------------------------------------
      setAnalyses:    (analyses) => set({ analyses }),
      loadAnalysesFromMessages: (messages) =>
        set({ analyses: messagesToAnalyses(messages) }),

      startAnalysis: (id, question) =>
        set((st) => ({
          analyses: [
            ...st.analyses,
            {
              id,
              question,
              status:    'running',
              startedAt: new Date(),
              insight:   '',
              charts:    [],
              tables:    [],
              steps:     [],
            },
          ],
          activeAnalysisId: id,
          isQuerying:       true,
        })),

      appendToken: (id, token) =>
        set((st) => ({
          analyses: st.analyses.map((a) =>
            a.id === id ? { ...a, insight: a.insight + token } : a,
          ),
        })),

      addChart: (id, chart) =>
        set((st) => ({
          analyses: st.analyses.map((a) =>
            a.id === id ? { ...a, charts: [...a.charts, chart] } : a,
          ),
        })),

      addTable: (id, table) =>
        set((st) => ({
          analyses: st.analyses.map((a) =>
            a.id === id ? { ...a, tables: [...a.tables, table] } : a,
          ),
        })),

      addStep: (id, step) =>
        set((st) => ({
          analyses: st.analyses.map((a) =>
            a.id === id ? { ...a, steps: [...a.steps, step] } : a,
          ),
        })),

      setExportCtx: (id, sql, sessionId) =>
        set((st) => ({
          analyses: st.analyses.map((a) =>
            a.id === id ? { ...a, exportSql: sql, exportSessionId: sessionId } : a,
          ),
        })),

      setUsage: (id, usage) =>
        set((st) => ({
          analyses: st.analyses.map((a) => (a.id === id ? { ...a, usage } : a)),
        })),

      finalizeAnalysis: (id) =>
        set((st) => ({
          analyses:   st.analyses.map((a) => (a.id === id ? { ...a, status: 'done' } : a)),
          isQuerying: false,
        })),

      setAnalysisError: (id, error) =>
        set((st) => ({
          analyses:   st.analyses.map((a) =>
            a.id === id ? { ...a, status: 'error', error } : a,
          ),
          isQuerying: false,
        })),

      attachServerIds: (id, conversationId, messageId) =>
        set((st) => ({
          analyses: st.analyses.map((a) =>
            a.id === id
              ? { ...a, conversationId, messageId: messageId ?? a.messageId }
              : a,
          ),
        })),

      setActiveAnalysis: (id) => set({ activeAnalysisId: id }),

      // ---- Stream control --------------------------------------------------

      setCurrentAbort: (fn) => set({ currentAbort: fn }),

      stopAnalysis: () =>
        set((st) => {
          // Trigger the abort if the stream is still live.
          try { st.currentAbort?.() } catch { /* defensive */ }
          // Finalise the in-flight analysis as 'done' so the partial answer,
          // charts and tables stay visible. The user can re-run with /retry.
          const id = st.activeAnalysisId
          const analyses = st.analyses.map((a) =>
            a.id === id && a.status === 'running'
              ? {
                  ...a,
                  status: 'done' as const,
                  insight: a.insight + (a.insight && !a.insight.endsWith('\n') ? '\n\n' : '') + '_— stopped by user_',
                }
              : a,
          )
          return {
            analyses,
            isQuerying:   false,
            currentAbort: null,
          }
        }),

      // ---- Synthetic cards (slash commands inject results this way) ------

      emitInfoCard: (title, markdown) =>
        set((st) => {
          const id = `cmd_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`
          return {
            analyses: [
              ...st.analyses,
              {
                id,
                question:   title,
                status:     'done',
                startedAt:  new Date(),
                insight:    markdown,
                charts:     [],
                tables:     [],
                steps:      [],
              },
            ],
            activeAnalysisId: id,
          }
        }),

      emitErrorCard: (title, message) =>
        set((st) => {
          const id = `cmd_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`
          return {
            analyses: [
              ...st.analyses,
              {
                id,
                question:  title,
                status:    'error',
                startedAt: new Date(),
                insight:   '',
                charts:    [],
                tables:    [],
                steps:     [],
                error:     message,
              },
            ],
            activeAnalysisId: id,
          }
        }),

      setOllamaModels: (ollamaModels) => set({ ollamaModels }),

      reset: () =>
        set({
          analyses:             [],
          activeAnalysisId:     null,
          isQuerying:           false,
        }),
    }),

    // ----- persist config -----------------------------------------------
    {
      name: 'datalens-ai-state',
      version: 1,
      storage: createJSONStorage(() => localStorage),
      // Only persist the prefs slice — never persist big payloads or
      // session/conversation lists; those are fetched from the server.
      partialize: (s) => ({
        model:                s.model,
        provider:             s.provider,
        activeSessionId:      s.activeSessionId,
        activeConversationId: s.activeConversationId,
        sidebarCollapsed:     s.sidebarCollapsed,
      }),
    },
  ),
)
