import { useEffect, useRef, useState, useCallback } from 'react'
import {
  BarChart2, Sparkles, Database, Zap,
  PanelLeftClose, PanelLeftOpen,
} from 'lucide-react'
import { useStore } from './store'
import { useAnalysis } from './hooks/useAnalysis'
import { useHydrate, useConversationSync, usePreferenceSync } from './hooks/useHydrate'
import { useTheme } from './hooks/useTheme'
import { Header } from './components/Header'
import { Sidebar } from './components/Sidebar'
import { QueryBar } from './components/QueryBar'
import { AnalysisCard } from './components/AnalysisCard'
import { DashboardCard } from './components/DashboardCard'
import { api } from './api/client'
import type { AnalysisResult } from './types'

/**
 * Decide which renderer to use for one analysis.
 *
 * During a live stream: use `intentInfo` (emitted first by the pipeline).
 * After reload from DB: `intentInfo` is not persisted, so fall back to:
 *   - `insightReport` present → always a multi-agent pipeline response
 *   - `visuals.length >= 1` → pipeline produced at least one visual
 *
 * Only pure chat-style answers (no insight, no visuals) use `AnalysisCard`.
 */
function shouldRenderAsDashboard(a: AnalysisResult): boolean {
  if (a.intentInfo) {
    if (a.intentInfo.wants_dashboard) return true
    if (a.intentInfo.intent === 'report') return true
    if (a.intentInfo.wants_chart) return true
  }
  // Persisted message reload heuristics (intentInfo not saved to DB)
  if (a.insightReport) return true
  if ((a.visuals?.length ?? 0) >= 1) return true
  if ((a.charts?.length ?? 0) >= 1) return true   // old messages: charts_json but no visuals_json
  return false
}

function OnboardingScreen() {
  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-12 text-center select-none">
      {/* Hero icon */}
      <div className="relative mb-8">
        <div className="absolute inset-0 rounded-full bg-indigo-500/10 blur-3xl scale-150" aria-hidden="true" />
        <div className="relative w-20 h-20 rounded-2xl bg-gradient-to-br from-indigo-600/20 to-purple-600/20 border border-indigo-500/20 flex items-center justify-center shadow-2xl shadow-indigo-500/10">
          <BarChart2 className="w-10 h-10 text-indigo-400" aria-hidden="true" />
        </div>
      </div>

      <h1 className="text-2xl font-bold mb-3">
        <span className="gradient-text">Agentic Data Analyst</span>
      </h1>
      <p className="text-sm text-slate-400 max-w-md leading-relaxed mb-10">
        Ask questions in plain English. The AI agent autonomously runs SQL queries,
        generates interactive charts, and delivers comprehensive analytical reports.
      </p>

      <div className="flex flex-wrap justify-center gap-2 mb-10" aria-label="Key features">
        {[
          { icon: <Zap       className="w-3 h-3" aria-hidden="true" />, label: 'Autonomous Multi-Query' },
          { icon: <BarChart2 className="w-3 h-3" aria-hidden="true" />, label: 'Interactive Charts'     },
          { icon: <Sparkles  className="w-3 h-3" aria-hidden="true" />, label: 'AI Insights'            },
          { icon: <Database  className="w-3 h-3" aria-hidden="true" />, label: 'Any SQL Database'       },
        ].map((f) => (
          <span
            key={f.label}
            className="flex items-center gap-1.5 text-xs text-indigo-300 bg-indigo-900/20 border border-indigo-700/30 rounded-full px-3 py-1.5"
          >
            {f.icon}
            {f.label}
          </span>
        ))}
      </div>

      <div className="flex items-center gap-3 text-xs text-slate-500 bg-slate-900/40 border border-slate-800/60 rounded-2xl px-5 py-3.5">
        <div className="w-6 h-6 rounded-lg bg-slate-800 flex items-center justify-center shrink-0" aria-hidden="true">
          <span className="text-indigo-400 font-bold text-xs">←</span>
        </div>
        <span>Connect a database from the sidebar to start analyzing</span>
      </div>
    </div>
  )
}

const FALLBACK_EXAMPLES = [
  'Show me what tables are available',
  'How many records are in the database?',
  'Show me a summary of the data',
  'Give me a complete analysis overview',
] as const

interface WorkspaceEmptyProps {
  activeSessionId: string
  onExampleClick: (query: string) => void
}

function WorkspaceEmptySkeleton() {
  return (
    <div className="mt-6 grid grid-cols-2 gap-2 max-w-sm w-full" aria-busy="true" aria-label="Loading example queries">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="h-10 rounded-xl bg-slate-800/50 animate-pulse"
          aria-hidden="true"
        />
      ))}
    </div>
  )
}

function WorkspaceEmpty({ activeSessionId, onExampleClick }: WorkspaceEmptyProps) {
  const [examples, setExamples] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setExamples([])

    api.fetchExampleQueries(activeSessionId).then((fetched) => {
      if (cancelled) return
      setExamples(
        fetched.length > 0 ? fetched.slice(0, 4) : [...FALLBACK_EXAMPLES],
      )
      setLoading(false)
    })

    return () => {
      cancelled = true
    }
  }, [activeSessionId])

  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-16 text-center select-none">
      <div className="w-14 h-14 rounded-2xl bg-indigo-600/10 border border-indigo-500/20 flex items-center justify-center mb-5">
        <Sparkles className="w-7 h-7 text-indigo-400/70" aria-hidden="true" />
      </div>
      <h2 className="text-base font-semibold text-slate-300 mb-2">Ready to Analyze</h2>
      <p className="text-sm text-slate-500 max-w-xs leading-relaxed">
        Ask anything about your data below. The agent will autonomously query,
        visualize, and surface insights.
      </p>
      {loading ? (
        <WorkspaceEmptySkeleton />
      ) : (
        <div className="mt-6 grid grid-cols-2 gap-2 max-w-sm w-full" aria-label="Example queries">
          {examples.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => onExampleClick(q)}
              className="text-left text-xs text-slate-500 bg-slate-900/40 border border-slate-800/60 rounded-xl px-3 py-2.5 leading-relaxed hover:border-indigo-500/40 hover:text-slate-300 hover:bg-slate-800/40 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50 focus-visible:ring-offset-1 focus-visible:ring-offset-transparent"
            >
              "{q}"
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function App() {
  // Hydrate from backend on mount, keep the active conversation's
  // messages in sync, and persist preference changes back to the server.
  useHydrate()
  useConversationSync()
  usePreferenceSync()
  // Apply the persisted dark / light / system theme to <html>.
  useTheme()

  const { activeSessionId, analyses, isQuerying } = useStore()
  const sidebarCollapsed = useStore((s) => s.sidebarCollapsed)
  const toggleSidebar    = useStore((s) => s.toggleSidebar)
  const { runAnalysis } = useAnalysis()
  const bottomRef = useRef<HTMLDivElement>(null)

  // Controlled prefill for QueryBar — set when user clicks an example query.
  // Cleared immediately after QueryBar consumes it so subsequent clicks on
  // the same example still trigger the effect.
  const [prefillValue, setPrefillValue] = useState('')

  const handleExampleClick = useCallback((query: string) => {
    setPrefillValue(query)
  }, [])

  const handlePrefillConsumed = useCallback(() => {
    setPrefillValue('')
  }, [])

  // ⌘B / Ctrl+B toggles the sidebar globally — except when typing in inputs.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const meta = e.metaKey || e.ctrlKey
      if (!meta || e.key.toLowerCase() !== 'b') return
      const t = e.target as HTMLElement | null
      const tag = t?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || t?.isContentEditable) return
      e.preventDefault()
      toggleSidebar()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggleSidebar])

  useEffect(() => {
    if (analyses.length) {
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100)
    }
  }, [analyses.length])

  return (
    <div className="flex flex-col h-screen overflow-hidden" style={{ background: 'var(--app-bg)' }}>
      <Header />

      <div className="flex flex-1 min-h-0 overflow-hidden relative">
        {/* Left sidebar — conversations + connections.
            Width animates between collapsed (0) and expanded (16rem). The
            `Sidebar` component is always mounted so its scroll position and
            local state survive the toggle; it's clipped by `overflow-hidden`
            when collapsed. */}
        <aside
          aria-label="Navigation sidebar"
          aria-hidden={sidebarCollapsed}
          className={`shrink-0 overflow-hidden flex flex-col transition-[width] duration-300 ease-in-out ${
            sidebarCollapsed ? 'w-0' : 'w-64'
          }`}
        >
          <Sidebar />
        </aside>

        {/* Edge-tab toggle — anchored to the seam between sidebar and main.
            Slides with the sidebar via the same `left-X` width transition.
            Always visible, even when the sidebar is collapsed. */}
        <button
          type="button"
          onClick={toggleSidebar}
          aria-label={sidebarCollapsed ? 'Show sidebar (Ctrl+B)' : 'Hide sidebar (Ctrl+B)'}
          aria-pressed={!sidebarCollapsed}
          title={sidebarCollapsed ? 'Show sidebar  ⌘B' : 'Hide sidebar  ⌘B'}
          className={`
            group absolute top-1/2 z-30
            -translate-x-1/2 -translate-y-1/2
            w-7 h-7 rounded-lg
            flex items-center justify-center
            bg-slate-900/95 backdrop-blur-sm
            border border-slate-700/60 hover:border-indigo-500/50
            text-slate-400 hover:text-indigo-300
            shadow-lg shadow-black/40
            focus:outline-none focus:ring-2 focus:ring-indigo-500/40
            transition-[left,colors,border-color,box-shadow]
            duration-300 ease-in-out
            ${sidebarCollapsed ? 'left-0' : 'left-64'}
          `}
        >
          {sidebarCollapsed
            ? <PanelLeftOpen  className="w-3.5 h-3.5" aria-hidden="true" />
            : <PanelLeftClose className="w-3.5 h-3.5" aria-hidden="true" />}
        </button>

        {/* Main workspace */}
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden border-l border-slate-800/60">
          <div className="flex-1 overflow-y-auto min-h-0">
            <div className="max-w-5xl mx-auto px-6 py-6 pb-4 space-y-8">
              {!activeSessionId && <OnboardingScreen />}
              {activeSessionId && analyses.length === 0 && (
                <WorkspaceEmpty
                  activeSessionId={activeSessionId}
                  onExampleClick={handleExampleClick}
                />
              )}

              {analyses.map((analysis, i) => (
                <div key={analysis.id} className="animate-fadeIn">
                  {i > 0 && <div className="border-t border-slate-800/50 -mx-6 mb-8" aria-hidden="true" />}
                  {shouldRenderAsDashboard(analysis)
                    ? <DashboardCard analysis={analysis} />
                    : <AnalysisCard   analysis={analysis} />}
                </div>
              ))}

              <div ref={bottomRef} aria-hidden="true" />
            </div>
          </div>

          <div className="shrink-0 border-t border-slate-800/60 bg-[#060b18]/95 backdrop-blur-sm px-6 py-4">
            <div className="max-w-5xl mx-auto">
              <QueryBar
                onSubmit={runAnalysis}
                isQuerying={isQuerying}
                disabled={!activeSessionId}
                prefillValue={prefillValue}
                onPrefillConsumed={handlePrefillConsumed}
              />
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
