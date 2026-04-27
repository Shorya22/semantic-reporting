import { useState } from 'react'
import {
  HardDrive, Server, FileText, Table2, X, Plus, ChevronDown, ChevronRight,
  MessageSquare, Trash2, Pencil, Database,
} from 'lucide-react'
import { useStore } from '../store'
import { Conversation, Session } from '../types'
import { api } from '../api/client'
import { ConnectionPanel } from './ConnectionPanel'

function typeIcon(type: Session['type']) {
  if (type === 'sqlite')     return <HardDrive className="w-3.5 h-3.5 text-amber-400"  aria-hidden="true" />
  if (type === 'postgresql') return <Server    className="w-3.5 h-3.5 text-blue-400"   aria-hidden="true" />
  return                            <FileText  className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
}


/**
 * Bucket conversations into ChatGPT-style date groups based on
 * `updated_at`. Order within each group is server-provided (DESC).
 */
function groupByDate(convs: Conversation[]): { label: string; items: Conversation[] }[] {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfYesterday = startOfToday - 24 * 60 * 60 * 1000
  const startOfWeek = startOfToday - 7 * 24 * 60 * 60 * 1000

  const buckets: Record<string, Conversation[]> = { Today: [], Yesterday: [], 'Previous 7 days': [], Older: [] }

  for (const c of convs) {
    const t = c.updated_at ? new Date(c.updated_at).getTime() : 0
    if (t >= startOfToday)               buckets.Today.push(c)
    else if (t >= startOfYesterday)      buckets.Yesterday.push(c)
    else if (t >= startOfWeek)           buckets['Previous 7 days'].push(c)
    else                                 buckets.Older.push(c)
  }

  return Object.entries(buckets)
    .filter(([, items]) => items.length > 0)
    .map(([label, items]) => ({ label, items }))
}


// ---------------------------------------------------------------------------
// Conversation row
// ---------------------------------------------------------------------------

function ConversationRow({ conv }: { conv: Conversation }) {
  const {
    activeConversationId, setActiveConversation,
    removeConversation, upsertConversation,
  } = useStore()
  const isActive = conv.id === activeConversationId
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(conv.title)

  async function commitRename() {
    setEditing(false)
    const next = draft.trim() || conv.title
    if (next === conv.title) return
    try {
      const updated = await api.renameConversation(conv.id, next)
      upsertConversation(updated)
    } catch { /* swallow — UI just reverts on next list refresh */ }
  }

  async function handleDelete(e: React.MouseEvent) {
    e.stopPropagation()
    if (!confirm(`Delete conversation "${conv.title}"?`)) return
    try { await api.deleteConversation(conv.id) } catch { /* ignore */ }
    removeConversation(conv.id)
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => !editing && setActiveConversation(conv.id)}
      onKeyDown={(e) => {
        if (editing) return
        if (e.key === 'Enter') setActiveConversation(conv.id)
      }}
      aria-pressed={isActive}
      className={`group relative flex items-center gap-2 rounded-lg px-2 py-1.5 cursor-pointer transition-colors ${
        isActive
          ? 'bg-indigo-600/15 text-indigo-100'
          : 'text-slate-300 hover:bg-slate-800/60'
      }`}
    >
      <MessageSquare className="w-3.5 h-3.5 shrink-0 text-slate-500" aria-hidden="true" />

      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitRename()
            if (e.key === 'Escape') { setEditing(false); setDraft(conv.title) }
          }}
          className="flex-1 bg-transparent border border-slate-700/60 rounded px-1 text-xs text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500/50"
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span className="flex-1 truncate text-xs leading-relaxed">{conv.title}</span>
      )}

      {!editing && (
        <span className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 transition-opacity">
          <button
            onClick={(e) => { e.stopPropagation(); setEditing(true); setDraft(conv.title) }}
            aria-label={`Rename ${conv.title}`}
            className="p-1 rounded hover:bg-slate-700 text-slate-400 hover:text-slate-200"
          >
            <Pencil className="w-3 h-3" aria-hidden="true" />
          </button>
          <button
            onClick={handleDelete}
            aria-label={`Delete ${conv.title}`}
            className="p-1 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300"
          >
            <Trash2 className="w-3 h-3" aria-hidden="true" />
          </button>
        </span>
      )}
    </div>
  )
}


// ---------------------------------------------------------------------------
// Connection row
// ---------------------------------------------------------------------------

function SessionCard({ session }: { session: Session }) {
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
      className={`group relative rounded-lg border px-2 py-2 cursor-pointer transition-colors ${
        isActive
          ? 'bg-indigo-600/10 border-indigo-500/40'
          : 'bg-slate-900/40 border-slate-800/60 hover:border-slate-700/80 hover:bg-slate-900/60'
      }`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <div className={`shrink-0 w-5 h-5 rounded flex items-center justify-center ${isActive ? 'bg-indigo-500/20' : 'bg-slate-800'}`}>
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

      {session.tables && session.tables.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5">
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


// ---------------------------------------------------------------------------
// Sidebar root
// ---------------------------------------------------------------------------

export function Sidebar() {
  const {
    sessions, conversations, activeSessionId,
    setActiveConversation, setAnalyses, upsertConversation,
    model, provider,
  } = useStore()

  const [showConnect, setShowConnect] = useState(false)
  const [connectionsOpen, setConnectionsOpen] = useState(true)
  const [creating, setCreating] = useState(false)

  const groups = groupByDate(conversations)

  async function handleNewChat() {
    if (creating) return
    setCreating(true)
    try {
      const conv = await api.createConversation({
        title:         'New chat',
        connection_id: activeSessionId,
        model,
        provider,
      })
      upsertConversation(conv)
      setActiveConversation(conv.id)
      setAnalyses([])
    } catch (e) {
      console.error('Failed to create conversation', e)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="flex flex-col h-full bg-[#080d1a] border-r border-slate-800/60 overflow-hidden">
      {/* New Chat button */}
      <div className="shrink-0 p-3 border-b border-slate-800/60 space-y-2">
        <button
          onClick={handleNewChat}
          disabled={creating}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-all border bg-slate-900/60 border-slate-700/50 text-slate-200 hover:border-slate-600 hover:bg-slate-800/80 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <div className="w-5 h-5 rounded bg-indigo-600/20 flex items-center justify-center shrink-0">
            <Plus className="w-3 h-3 text-indigo-300" aria-hidden="true" />
          </div>
          <span>New chat</span>
        </button>

        {/* New Connection toggle */}
        <button
          onClick={() => setShowConnect((v) => !v)}
          aria-expanded={showConnect}
          aria-controls="connection-panel"
          className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-all border ${
            showConnect
              ? 'bg-indigo-600/15 border-indigo-500/40 text-indigo-300'
              : 'bg-slate-900/40 border-slate-800/60 text-slate-400 hover:border-slate-700 hover:text-slate-300'
          }`}
        >
          <div className="w-5 h-5 rounded bg-slate-800 flex items-center justify-center shrink-0">
            <Database className="w-3 h-3 text-slate-400" aria-hidden="true" />
          </div>
          <span>Connect database</span>
          <ChevronDown
            className={`w-3.5 h-3.5 ml-auto text-slate-500 transition-transform duration-200 ${showConnect ? 'rotate-180' : ''}`}
            aria-hidden="true"
          />
        </button>

        {showConnect && (
          <div id="connection-panel" className="animate-fadeIn">
            <ConnectionPanel onConnected={() => setShowConnect(false)} />
          </div>
        )}
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto px-2 py-3 space-y-4 min-h-0">
        {/* Conversations — primary nav */}
        {groups.length > 0 ? (
          groups.map((g) => (
            <section key={g.label} aria-label={g.label}>
              <div className="px-2 mb-1">
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  {g.label}
                </span>
              </div>
              <div className="space-y-0.5">
                {g.items.map((c) => <ConversationRow key={c.id} conv={c} />)}
              </div>
            </section>
          ))
        ) : (
          <div className="px-3 py-6 text-center">
            <MessageSquare className="w-6 h-6 mx-auto mb-2 text-slate-700" aria-hidden="true" />
            <p className="text-xs text-slate-600 leading-relaxed">
              No conversations yet.<br />Start a new chat to begin.
            </p>
          </div>
        )}

        {/* Connections — collapsible section */}
        {sessions.length > 0 && (
          <section aria-label="Connected databases" className="pt-2 border-t border-slate-800/60">
            <button
              onClick={() => setConnectionsOpen((v) => !v)}
              aria-expanded={connectionsOpen}
              className="w-full flex items-center gap-1.5 px-2 py-1 mb-1 group"
            >
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider group-hover:text-slate-400">
                Connections
              </span>
              <span className="ml-auto text-xs bg-slate-800 text-slate-500 rounded-full px-1.5 py-0.5">
                {sessions.length}
              </span>
              {connectionsOpen
                ? <ChevronDown  className="w-3 h-3 text-slate-600" aria-hidden="true" />
                : <ChevronRight className="w-3 h-3 text-slate-600" aria-hidden="true" />}
            </button>

            {connectionsOpen && (
              <div className="space-y-1.5 px-1">
                {sessions.map((s) => <SessionCard key={s.session_id} session={s} />)}
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
