import { create } from 'zustand'
import {
  AnalysisResult,
  AgentStep,
  ChartResult,
  LlmProvider,
  ModelOption,
  Session,
  TableResult,
  TokenUsage,
} from '../types'

interface AppStore {
  // DB sessions
  sessions: Session[]
  activeSessionId: string | null

  // Analysis history
  analyses: AnalysisResult[]
  activeAnalysisId: string | null
  isQuerying: boolean

  // Model config
  model: string
  provider: LlmProvider
  ollamaModels: ModelOption[]

  // Session actions
  addSession: (s: Session) => void
  setActiveSession: (id: string | null) => void
  removeSession: (id: string) => void

  // Analysis actions
  startAnalysis: (id: string, question: string) => void
  appendToken: (id: string, token: string) => void
  addChart: (id: string, chart: ChartResult) => void
  addTable: (id: string, table: TableResult) => void
  addStep: (id: string, step: AgentStep) => void
  setExportCtx: (id: string, sql: string, sessionId: string) => void
  setUsage: (id: string, usage: TokenUsage) => void
  finalizeAnalysis: (id: string) => void
  setAnalysisError: (id: string, error: string) => void
  setActiveAnalysis: (id: string | null) => void

  // Model actions
  setModel: (m: string) => void
  setProvider: (p: LlmProvider) => void
  setOllamaModels: (models: ModelOption[]) => void
}

export const useStore = create<AppStore>((set) => ({
  sessions: [],
  activeSessionId: null,
  analyses: [],
  activeAnalysisId: null,
  isQuerying: false,
  model: 'llama-3.3-70b-versatile',
  provider: 'groq',
  ollamaModels: [],

  addSession: (s) =>
    set((st) => ({
      sessions: [...st.sessions, s],
      activeSessionId: s.session_id,
    })),

  setActiveSession: (id) => set({ activeSessionId: id }),

  removeSession: (id) =>
    set((st) => ({
      sessions: st.sessions.filter((x) => x.session_id !== id),
      activeSessionId: st.activeSessionId === id ? null : st.activeSessionId,
    })),

  startAnalysis: (id, question) =>
    set((st) => ({
      analyses: [
        ...st.analyses,
        {
          id,
          question,
          status: 'running',
          startedAt: new Date(),
          insight: '',
          charts: [],
          tables: [],
          steps: [],
        },
      ],
      activeAnalysisId: id,
      isQuerying: true,
    })),

  appendToken: (id, token) =>
    set((st) => ({
      analyses: st.analyses.map((a) =>
        a.id === id ? { ...a, insight: a.insight + token } : a
      ),
    })),

  addChart: (id, chart) =>
    set((st) => ({
      analyses: st.analyses.map((a) =>
        a.id === id ? { ...a, charts: [...a.charts, chart] } : a
      ),
    })),

  addTable: (id, table) =>
    set((st) => ({
      analyses: st.analyses.map((a) =>
        a.id === id ? { ...a, tables: [...a.tables, table] } : a
      ),
    })),

  addStep: (id, step) =>
    set((st) => ({
      analyses: st.analyses.map((a) =>
        a.id === id ? { ...a, steps: [...a.steps, step] } : a
      ),
    })),

  setExportCtx: (id, sql, sessionId) =>
    set((st) => ({
      analyses: st.analyses.map((a) =>
        a.id === id ? { ...a, exportSql: sql, exportSessionId: sessionId } : a
      ),
    })),

  setUsage: (id, usage) =>
    set((st) => ({
      analyses: st.analyses.map((a) =>
        a.id === id ? { ...a, usage } : a
      ),
    })),

  finalizeAnalysis: (id) =>
    set((st) => ({
      analyses: st.analyses.map((a) =>
        a.id === id ? { ...a, status: 'done' } : a
      ),
      isQuerying: false,
    })),

  setAnalysisError: (id, error) =>
    set((st) => ({
      analyses: st.analyses.map((a) =>
        a.id === id ? { ...a, status: 'error', error } : a
      ),
      isQuerying: false,
    })),

  setActiveAnalysis: (id) => set({ activeAnalysisId: id }),

  setModel: (model) => set({ model }),
  setProvider: (provider) => set({ provider }),
  setOllamaModels: (ollamaModels) => set({ ollamaModels }),
}))
