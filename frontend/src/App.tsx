import { useEffect, useRef } from 'react'
import { BarChart2, Sparkles, Database, Zap } from 'lucide-react'
import { useStore } from './store'
import { useAnalysis } from './hooks/useAnalysis'
import { Header } from './components/Header'
import { Sidebar } from './components/Sidebar'
import { QueryBar } from './components/QueryBar'
import { AnalysisCard } from './components/AnalysisCard'

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

      {/* Feature pills */}
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

      {/* Connection instruction */}
      <div className="flex items-center gap-3 text-xs text-slate-500 bg-slate-900/40 border border-slate-800/60 rounded-2xl px-5 py-3.5">
        <div className="w-6 h-6 rounded-lg bg-slate-800 flex items-center justify-center shrink-0" aria-hidden="true">
          <span className="text-indigo-400 font-bold text-xs">←</span>
        </div>
        <span>Connect a database from the sidebar to start analyzing</span>
      </div>
    </div>
  )
}

function WorkspaceEmpty() {
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
      <div className="mt-6 grid grid-cols-2 gap-2 max-w-sm w-full" aria-label="Example queries">
        {[
          'Show total revenue by category',
          'What are the top 10 customers?',
          'Monthly trend with a chart',
          'Full performance analysis',
        ].map((q) => (
          <div
            key={q}
            className="text-left text-xs text-slate-500 bg-slate-900/40 border border-slate-800/60 rounded-xl px-3 py-2.5 leading-relaxed hover:border-slate-700 hover:text-slate-400 transition-colors"
          >
            "{q}"
          </div>
        ))}
      </div>
    </div>
  )
}

export default function App() {
  const { activeSessionId, analyses, isQuerying } = useStore()
  const { runAnalysis } = useAnalysis()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (analyses.length) {
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100)
    }
  }, [analyses.length])

  return (
    <div className="flex flex-col h-screen overflow-hidden" style={{ background: '#060b18' }}>
      <Header />

      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left sidebar — connection + session list + history */}
        <aside className="w-60 shrink-0 overflow-hidden flex flex-col" aria-label="Navigation sidebar">
          <Sidebar />
        </aside>

        {/* Main workspace */}
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden border-l border-slate-800/60">
          {/* Scrollable results area */}
          <div className="flex-1 overflow-y-auto min-h-0">
            <div className="max-w-5xl mx-auto px-6 py-6 pb-4 space-y-8">
              {!activeSessionId && <OnboardingScreen />}
              {activeSessionId && analyses.length === 0 && <WorkspaceEmpty />}

              {analyses.map((analysis, i) => (
                <div key={analysis.id} className="animate-fadeIn">
                  {i > 0 && <div className="border-t border-slate-800/50 -mx-6 mb-8" aria-hidden="true" />}
                  <AnalysisCard analysis={analysis} />
                </div>
              ))}

              <div ref={bottomRef} aria-hidden="true" />
            </div>
          </div>

          {/* Query bar — pinned at bottom */}
          <div className="shrink-0 border-t border-slate-800/60 bg-[#060b18]/95 backdrop-blur-sm px-6 py-4">
            <div className="max-w-5xl mx-auto">
              <QueryBar
                onSubmit={runAnalysis}
                isQuerying={isQuerying}
                disabled={!activeSessionId}
              />
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
