import { useState } from 'react'
import { HardDrive, Server, FileText, Table2, X, Clock, Plus, ChevronDown, ChevronRight, Sparkles } from 'lucide-react'
import { useStore } from '../store'
import { Session } from '../types'
import { api } from '../api/client'
import { ConnectionPanel } from './ConnectionPanel'

function typeIcon(type: Session['type']) {
  if (type === 'sqlite')     return <HardDrive className="w-3.5 h-3.5 text-amber-400"  aria-hidden="true" />
  if (type === 'postgresql') return <Server    className="w-3.5 h-3.5 text-blue-400"   aria-hidden="true" />
  return                            <FileText  className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
}

interface SessionCardProps {
  session: Session
}

function SessionCard({ session }: SessionCardProps) {
  const { activeSessionId, setActiveSession, removeSession } = useStore()
  const isActive = session.session_id === activeSessionId

  async function handleDisconnect(e: React.MouseEvent) {
    e.stopPropagation()
    try { await api.disconnect(session.session_id) } catch { /* ignore */ }
    removeSession(session.session_id)
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => setActiveSession(session.session_id)}
      onKeyDown={(e) => e.key === 'Enter' && setActiveSession(session.session_id)}
      aria-pressed={isActive}
      className={`group relative rounded-xl border px-3 py-2.5 cursor-pointer transition-all ${
        isActive
          ? 'bg-indigo-600/10 border-indigo-500/40 shadow-[0_0_12px_rgba(99,102,241,0.08)]'
          : 'bg-slate-900/40 border-slate-800/60 hover:border-slate-700/80 hover:bg-slate-900/60'
      }`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <div className={`shrink-0 w-6 h-6 rounded-lg flex items-center justify-center ${isActive ? 'bg-indigo-500/20' : 'bg-slate-800'}`}>
          {typeIcon(session.type)}
        </div>
        <span className="text-xs font-medium text-slate-200 truncate flex-1">{session.name}</span>
        <button
          onClick={handleDisconnect}
          aria-label={`Disconnect ${session.name}`}
          className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-slate-700 text-slate-500 hover:text-slate-300 transition-all"
        >
          <X className="w-3 h-3" aria-hidden="true" />
        </button>
      </div>

      {isActive && (
        <div className="flex items-center gap-1 mt-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" aria-hidden="true" />
          <span className="text-xs text-emerald-400">Active</span>
        </div>
      )}

      {session.tables.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {session.tables.slice(0, 3).map((t) => (
            <span key={t} className="inline-flex items-center gap-0.5 text-xs text-slate-500 bg-slate-800/60 rounded px-1.5 py-0.5">
              <Table2 className="w-2.5 h-2.5" aria-hidden="true" />
              {t}
            </span>
          ))}
          {session.tables.length > 3 && (
            <span className="text-xs text-slate-600">+{session.tables.length - 3}</span>
          )}
        </div>
      )}
    </div>
  )
}

export function Sidebar() {
  const { sessions, analyses, setActiveAnalysis } = useStore()
  const [showConnect, setShowConnect] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(true)

  return (
    <div className="flex flex-col h-full bg-[#080d1a] border-r border-slate-800/60 overflow-hidden">
      {/* New Connection toggle */}
      <div className="shrink-0 p-3 border-b border-slate-800/60">
        <button
          onClick={() => setShowConnect((v) => !v)}
          aria-expanded={showConnect}
          aria-controls="connection-panel"
          className={`w-full flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-medium transition-all border ${
            showConnect
              ? 'bg-indigo-600/15 border-indigo-500/40 text-indigo-300'
              : 'bg-slate-900/60 border-slate-700/50 text-slate-300 hover:border-slate-600 hover:text-slate-200'
          }`}
        >
          <div className="w-5 h-5 rounded-lg bg-indigo-600/20 flex items-center justify-center shrink-0">
            <Plus className="w-3 h-3 text-indigo-400" aria-hidden="true" />
          </div>
          New Connection
          <ChevronDown
            className={`w-3.5 h-3.5 ml-auto text-slate-500 transition-transform duration-200 ${showConnect ? 'rotate-180' : ''}`}
            aria-hidden="true"
          />
        </button>

        {showConnect && (
          <div id="connection-panel" className="mt-2 animate-fadeIn">
            <ConnectionPanel onConnected={() => setShowConnect(false)} />
          </div>
        )}
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto p-3 space-y-5 min-h-0">
        {/* Connected sessions */}
        {sessions.length > 0 && (
          <section aria-label="Connected databases">
            <div className="flex items-center gap-1.5 mb-2 px-1">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Connections</span>
              <span className="ml-auto text-xs bg-slate-800 text-slate-500 rounded-full px-1.5 py-0.5">{sessions.length}</span>
            </div>
            <div className="space-y-2">
              {sessions.map((s) => <SessionCard key={s.session_id} session={s} />)}
            </div>
          </section>
        )}

        {/* Analysis history */}
        {analyses.length > 0 && (
          <section aria-label="Analysis history">
            <button
              onClick={() => setHistoryOpen((v) => !v)}
              aria-expanded={historyOpen}
              className="w-full flex items-center gap-1.5 mb-2 px-1 group"
            >
              <Clock className="w-3 h-3 text-slate-600 group-hover:text-slate-400 transition-colors" aria-hidden="true" />
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider group-hover:text-slate-400 transition-colors">
                History
              </span>
              <span className="ml-auto text-xs bg-slate-800 text-slate-500 rounded-full px-1.5 py-0.5">{analyses.length}</span>
              {historyOpen
                ? <ChevronDown  className="w-3 h-3 text-slate-600" aria-hidden="true" />
                : <ChevronRight className="w-3 h-3 text-slate-600" aria-hidden="true" />}
            </button>

            {historyOpen && (
              <div className="space-y-0.5">
                {[...analyses].reverse().map((a) => (
                  <button
                    key={a.id}
                    onClick={() => setActiveAnalysis(a.id)}
                    className="w-full text-left flex items-start gap-2 px-2 py-2 rounded-lg hover:bg-slate-800/50 transition-colors group"
                  >
                    <Sparkles className="w-3 h-3 mt-0.5 shrink-0 text-slate-700 group-hover:text-indigo-400 transition-colors" aria-hidden="true" />
                    <span className="text-xs text-slate-500 group-hover:text-slate-300 transition-colors line-clamp-2 leading-relaxed flex-1">
                      {a.question}
                    </span>
                    <span
                      aria-hidden="true"
                      className={`shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full ${
                        a.status === 'running' ? 'bg-indigo-400 animate-pulse'
                        : a.status === 'error'  ? 'bg-red-500'
                        : 'bg-emerald-500'
                      }`}
                    />
                  </button>
                ))}
              </div>
            )}
          </section>
        )}
      </div>

      {/* Footer */}
      <div className="shrink-0 px-4 py-3 border-t border-slate-800/60">
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 rounded bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center" aria-hidden="true">
            <span className="text-[8px] text-white font-bold">AI</span>
          </div>
          <span className="text-xs text-slate-600">LangGraph + Groq</span>
        </div>
      </div>
    </div>
  )
}
