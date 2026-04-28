import { useMemo, useState } from 'react'
import {
  HardDrive, Server, FileSpreadsheet, Sheet, Database,
  Plus, ChevronDown, ChevronRight, MessageSquare, Trash2, Pencil,
  Search, X, Sparkles, ChevronUp,
} from 'lucide-react'
import { useStore } from '../store'
import { Conversation, Session } from '../types'
import { api } from '../api/client'
import { ConnectionPanel } from './ConnectionPanel'

// ---------------------------------------------------------------------------
// Per-source-type metadata: colour accent, icon, display label, ordering.
// One source of truth — drives the connection bucket headers and the per-row
// type chip. Active state is unified to indigo (see SessionCard) so the
// per-type accents stay quiet and don't fight the rest of the UI.
// ---------------------------------------------------------------------------

type SourceType = Session['type']

interface TypeMeta {
  label: string
  Icon:  typeof HardDrive
  text:  string   // tailwind text colour for icon + count pill
  bg:    string   // tailwind bg colour for icon chip + count pill
  dot:   string   // status dot colour (active session)
}

const TYPE_META: Record<SourceType, TypeMeta> = {
  sqlite:     { label: 'SQLite',     Icon: HardDrive,       text: 'text-amber-300',   bg: 'bg-amber-500/15',   dot: 'bg-amber-400'  },
  postgresql: { label: 'PostgreSQL', Icon: Server,          text: 'text-sky-300',     bg: 'bg-sky-500/15',     dot: 'bg-sky-400'    },
  csv:        { label: 'CSV',        Icon: FileSpreadsheet, text: 'text-emerald-300', bg: 'bg-emerald-500/15', dot: 'bg-emerald-400'},
  excel:      { label: 'Excel',      Icon: Sheet,           text: 'text-green-300',   bg: 'bg-green-500/15',   dot: 'bg-green-400'  },
}

const TYPE_ORDER: SourceType[] = ['sqlite', 'postgresql', 'csv', 'excel']


// ---------------------------------------------------------------------------
// Date bucketing for the EXPANDED chat list. The collapsed view shows a
// flat "Recent" list of 5, so this is only used after the user opens "Show all".
// ---------------------------------------------------------------------------

function groupByDate(convs: Conversation[]): { label: string; items: Conversation[] }[] {
  const now = new Date()
  const startOfToday     = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfYesterday = startOfToday - 24 * 60 * 60 * 1000
  const startOfWeek      = startOfToday - 7  * 24 * 60 * 60 * 1000
  const startOfMonth     = startOfToday - 30 * 24 * 60 * 60 * 1000

  const buckets: Record<string, Conversation[]> = {
    Today:              [],
    Yesterday:          [],
    'Previous 7 days':  [],
    'Previous 30 days': [],
    Older:              [],
  }

  for (const c of convs) {
    const t = c.updated_at ? new Date(c.updated_at).getTime() : 0
    if      (t >= startOfToday)     buckets.Today.push(c)
    else if (t >= startOfYesterday) buckets.Yesterday.push(c)
    else if (t >= startOfWeek)      buckets['Previous 7 days'].push(c)
    else if (t >= startOfMonth)     buckets['Previous 30 days'].push(c)
    else                            buckets.Older.push(c)
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
  const [draft, setDraft]     = useState(conv.title)

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
      aria-label={isActive ? `Active conversation: ${conv.title}` : `Open conversation: ${conv.title}`}
      className={`group relative flex items-center gap-2 rounded-lg px-2.5 py-1.5 cursor-pointer transition-all ${
        isActive
          ? 'bg-gradient-to-r from-indigo-600/20 via-indigo-600/10 to-transparent text-indigo-100 shadow-[inset_2px_0_0_0_rgb(99_102_241)]'
          : 'text-slate-300 hover:bg-slate-800/50'
      }`}
    >
      <MessageSquare
        className={`w-3.5 h-3.5 shrink-0 ${isActive ? 'text-indigo-300' : 'text-slate-500'}`}
        aria-hidden="true"
      />

      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter')  commitRename()
            if (e.key === 'Escape') { setEditing(false); setDraft(conv.title) }
          }}
          className="flex-1 bg-transparent border border-slate-700/60 rounded px-1 text-xs text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500/50"
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span className="flex-1 truncate text-xs leading-relaxed">{conv.title}</span>
      )}

      {!editing && (
        <span className="opacity-0 group-hover:opacity-100 focus-within:opacity-100 flex items-center gap-0.5 transition-opacity">
          <button
            onClick={(e) => { e.stopPropagation(); setEditing(true); setDraft(conv.title) }}
            aria-label={`Rename ${conv.title}`}
            className="p-1 rounded hover:bg-slate-700 text-slate-400 hover:text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500/50"
          >
            <Pencil className="w-3 h-3" aria-hidden="true" />
          </button>
          <button
            onClick={handleDelete}
            aria-label={`Delete ${conv.title}`}
            className="p-1 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300 focus:outline-none focus:ring-1 focus:ring-red-500/50"
          >
            <Trash2 className="w-3 h-3" aria-hidden="true" />
          </button>
        </span>
      )}
    </div>
  )
}


// ---------------------------------------------------------------------------
// Connection card — single persisted DB connection.
// Active state uses a unified indigo accent; the source-type colour is kept
// only on the small icon chip and the status dot to avoid a noisy ring.
// ---------------------------------------------------------------------------

function SessionCard({ session, showTypeBadge }: { session: Session; showTypeBadge: boolean }) {
  const { activeSessionId, toggleConnection, removeSession } = useStore()
  const isActive = session.session_id === activeSessionId
  const meta     = TYPE_META[session.type] ?? TYPE_META.sqlite
  const Icon     = meta.Icon

  async function handleDisconnect(e: React.MouseEvent) {
    e.stopPropagation()
    try { await api.disconnect(session.session_id) } catch { /* ignore */ }
    removeSession(session.session_id)
  }

  function handleToggle(e: React.MouseEvent | React.KeyboardEvent) {
    e.stopPropagation()
    toggleConnection(session.session_id)
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => toggleConnection(session.session_id)}
      onKeyDown={(e) => e.key === 'Enter' && toggleConnection(session.session_id)}
      aria-pressed={isActive}
      aria-label={isActive ? `${session.name} is on (active). Press to turn off.` : `${session.name} is off. Press to turn on.`}
      className={`group relative rounded-lg border px-2.5 py-2 cursor-pointer transition-all ${
        isActive
          ? 'bg-indigo-600/10 border-indigo-500/40'
          : 'bg-slate-900/30 border-slate-800/60 hover:border-slate-700 hover:bg-slate-900/60'
      }`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <div
          className={`shrink-0 w-6 h-6 rounded-md flex items-center justify-center ${meta.bg} transition-opacity ${
            isActive ? '' : 'opacity-60'
          }`}
        >
          <Icon className={`w-3.5 h-3.5 ${meta.text}`} aria-hidden="true" />
        </div>

        <div className="flex-1 min-w-0">
          <span
            className={`block text-xs font-medium truncate transition-colors ${
              isActive ? 'text-indigo-100' : 'text-slate-400'
            }`}
          >
            {session.name}
          </span>
          <div className="flex items-center gap-1.5 mt-0.5">
            {isActive ? (
              <span
                className="shrink-0 inline-flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
                title="This connection is active and ready for queries"
              >
                <span
                  className="w-1 h-1 rounded-full bg-emerald-400 shadow-[0_0_4px_rgba(74,222,128,0.7)] animate-pulse"
                  aria-hidden="true"
                />
                On
              </span>
            ) : (
              <span className="shrink-0 inline-flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-slate-800/60 text-slate-500 ring-1 ring-slate-700/40">
                Off
              </span>
            )}
            {!isActive && showTypeBadge && (
              <span className={`shrink-0 text-[9px] font-semibold uppercase tracking-wide px-1 py-px rounded ${meta.bg} ${meta.text} opacity-70`}>
                {meta.label}
              </span>
            )}
            {session.tables && session.tables.length > 0 && (
              <span className="text-[10px] text-slate-500 truncate">
                {session.tables.length} {session.tables.length === 1 ? 'table' : 'tables'}
                {session.rows ? ` · ${session.rows.toLocaleString()} rows` : ''}
              </span>
            )}
          </div>
        </div>

        {/* Toggle switch + (hover-revealed) disconnect button */}
        <div className="flex items-center gap-1 shrink-0">
          <ToggleSwitch
            on={isActive}
            onChange={handleToggle}
            label={`Turn ${session.name} ${isActive ? 'off' : 'on'}`}
          />
          <button
            onClick={handleDisconnect}
            aria-label={`Disconnect ${session.name}`}
            className="opacity-0 group-hover:opacity-100 focus:opacity-100 p-1 rounded hover:bg-slate-700 text-slate-500 hover:text-slate-300 transition-all focus:outline-none focus:ring-1 focus:ring-indigo-500/50"
          >
            <X className="w-3 h-3" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  )
}


// ---------------------------------------------------------------------------
// Tiny iOS-style toggle switch — used per-connection.
// ---------------------------------------------------------------------------

function ToggleSwitch({
  on,
  onChange,
  label,
}: {
  on: boolean
  onChange: (e: React.MouseEvent | React.KeyboardEvent) => void
  label: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onChange}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onChange(e)
        }
      }}
      className={`
        relative inline-flex items-center
        w-7 h-4 rounded-full p-0.5
        transition-colors
        focus:outline-none focus:ring-1 focus:ring-indigo-500/40 focus:ring-offset-1 focus:ring-offset-[#080d1a]
        ${on
          ? 'bg-gradient-to-r from-indigo-600 to-indigo-500 shadow-inner shadow-indigo-900/40'
          : 'bg-slate-800 ring-1 ring-slate-700/60'}
      `}
    >
      <span
        aria-hidden="true"
        className={`
          inline-block w-3 h-3 rounded-full bg-white shadow-md
          transition-transform duration-200
          ${on ? 'translate-x-3' : 'translate-x-0'}
        `}
      />
    </button>
  )
}


// ---------------------------------------------------------------------------
// Per-type bucket header — used only when multiple source types are present.
// One-liner, no chevron (buckets are always-expanded; collapsing a 1-3 row
// list adds visual weight without saving space).
// ---------------------------------------------------------------------------

function BucketHeader({ type, count }: { type: SourceType; count: number }) {
  const meta = TYPE_META[type]
  const Icon = meta.Icon
  return (
    <div className="flex items-center gap-1.5 px-2 mb-1">
      <Icon className={`w-3 h-3 ${meta.text}`} aria-hidden="true" />
      <span className="text-[10px] font-semibold tracking-wider uppercase text-slate-500">
        {meta.label}
      </span>
      <span className={`ml-auto text-[10px] tabular-nums px-1.5 rounded-full font-medium ${meta.bg} ${meta.text}`}>
        {count}
      </span>
    </div>
  )
}


// ---------------------------------------------------------------------------
// Sidebar root
// ---------------------------------------------------------------------------

const COLLAPSED_CHATS = 5      // chats shown in the collapsed view
const SEARCH_THRESHOLD = 8     // chat search field appears after this many chats

export function Sidebar() {
  const {
    sessions, conversations, activeSessionId,
    setActiveConversation, setAnalyses, upsertConversation,
    model, provider,
  } = useStore()

  const [showConnect,  setShowConnect]  = useState(false)
  const [chatQuery,    setChatQuery]    = useState('')
  const [chatsOpen,    setChatsOpen]    = useState(true)
  const [chatsExpanded, setChatsExpanded] = useState(false)
  const [connsOpen,    setConnsOpen]    = useState(true)
  const [creating,     setCreating]     = useState(false)

  // ── Filtered chat list (search) ─────────────────────────────────────────
  const filteredConvs = useMemo(() => {
    if (!chatQuery.trim()) return conversations
    const q = chatQuery.trim().toLowerCase()
    return conversations.filter((c) => c.title.toLowerCase().includes(q))
  }, [conversations, chatQuery])

  // The collapsed view shows only the N most recent chats so the
  // Connections section stays anchored at the bottom and never gets pushed
  // off-screen as the chat list grows.
  const showAllToggleVisible = !chatQuery && filteredConvs.length > COLLAPSED_CHATS
  const visibleConvs = (chatsExpanded || chatQuery)
    ? filteredConvs
    : filteredConvs.slice(0, COLLAPSED_CHATS)
  const groups = groupByDate(visibleConvs)

  // ── Connections bucketed by source type ─────────────────────────────────
  // Empty buckets are dropped so the section stays clean. When only one
  // type is present the bucket header is suppressed too — grouping by 1
  // category is just visual noise.
  const sessionsByType = useMemo(() => {
    const out: Record<SourceType, Session[]> = { sqlite: [], postgresql: [], csv: [], excel: [] }
    for (const s of sessions) {
      const k = (s.type in out ? s.type : 'sqlite') as SourceType
      out[k].push(s)
    }
    return out
  }, [sessions])

  const activeTypes  = TYPE_ORDER.filter((t) => sessionsByType[t].length > 0)
  const showBuckets  = activeTypes.length > 1

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
      // Keep the collapsed view anchored on the most-recent 5 — newly created
      // chats are always shown without forcing the user into "Show all".
      setChatsExpanded(false)
    } catch (e) {
      console.error('Failed to create conversation', e)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="flex flex-col h-full bg-[#080d1a] border-r border-slate-800/60 overflow-hidden">
      {/* ───────────── Hero CTA ───────────── */}
      <div className="shrink-0 px-3 pt-3 pb-2">
        <button
          onClick={handleNewChat}
          disabled={creating}
          className="w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-xs font-semibold text-white bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 disabled:opacity-40 disabled:cursor-not-allowed shadow-lg shadow-indigo-900/30 hover:shadow-indigo-700/40 transition-all"
        >
          <span className="w-5 h-5 rounded-md bg-white/15 flex items-center justify-center shrink-0">
            <Plus className="w-3 h-3" aria-hidden="true" />
          </span>
          <span className="flex-1 text-left">New chat</span>
          <Sparkles className="w-3.5 h-3.5 opacity-70" aria-hidden="true" />
        </button>
      </div>

      {/* ───────────── Body ─────────────
        Two siblings — Chats (top) and Connections (below). Both are
        flex-shrink-0 so neither pushes the other off-screen. Long chat
        history is contained inside its own scroll region (see "expanded"
        state) instead of growing the column. */}
      <div className="flex-1 overflow-y-auto min-h-0 flex flex-col">

        {/* ============ CHATS section ============ */}
        <section aria-label="Chats" className="shrink-0 px-2 pt-2 pb-3 space-y-1.5">
          <button
            onClick={() => setChatsOpen((v) => !v)}
            aria-expanded={chatsOpen}
            className="w-full flex items-center gap-1.5 px-2 py-1 rounded-md hover:bg-slate-800/40 group transition-colors"
          >
            {chatsOpen
              ? <ChevronDown  className="w-3 h-3 text-slate-500" aria-hidden="true" />
              : <ChevronRight className="w-3 h-3 text-slate-500" aria-hidden="true" />}
            <MessageSquare className="w-3 h-3 text-slate-400" aria-hidden="true" />
            <span className="text-[11px] font-semibold tracking-wider uppercase text-slate-400 group-hover:text-slate-300">
              Chats
            </span>
            <span className="ml-auto text-[10px] tabular-nums px-1.5 py-0.5 rounded-full font-medium bg-slate-800/80 text-slate-400">
              {conversations.length}
            </span>
          </button>

          {chatsOpen && (
            <>
              {/* Search field (only when many chats) */}
              {conversations.length > SEARCH_THRESHOLD && (
                <div className="relative px-1">
                  <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-600" aria-hidden="true" />
                  <input
                    type="search"
                    placeholder="Search chats…"
                    value={chatQuery}
                    onChange={(e) => setChatQuery(e.target.value)}
                    aria-label="Search chats"
                    className="w-full bg-slate-900/60 border border-slate-800/80 rounded-md pl-7 pr-7 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:ring-1 focus:ring-indigo-500/50 focus:border-indigo-500/50 transition-colors"
                  />
                  {chatQuery && (
                    <button
                      onClick={() => setChatQuery('')}
                      aria-label="Clear search"
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-600 hover:text-slate-400"
                    >
                      <X className="w-3 h-3" aria-hidden="true" />
                    </button>
                  )}
                </div>
              )}

              {/* Chat list */}
              {visibleConvs.length > 0 ? (
                <div
                  className={`space-y-0.5 ${
                    chatsExpanded ? 'max-h-[40vh] overflow-y-auto pr-0.5' : ''
                  }`}
                >
                  {chatsExpanded ? (
                    /* Expanded view: full date-grouped scrollable list */
                    groups.map((g) => (
                      <div key={g.label}>
                        <div className="sticky top-0 px-2 py-1 bg-[#080d1a]/95 backdrop-blur-sm">
                          <span className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider">
                            {g.label}
                          </span>
                        </div>
                        <div className="space-y-0.5 pb-1">
                          {g.items.map((c) => <ConversationRow key={c.id} conv={c} />)}
                        </div>
                      </div>
                    ))
                  ) : (
                    /* Collapsed view: flat list of N most recent */
                    visibleConvs.map((c) => <ConversationRow key={c.id} conv={c} />)
                  )}
                </div>
              ) : (
                <div className="px-3 py-4 text-center">
                  <p className="text-xs text-slate-600 leading-relaxed">
                    {chatQuery
                      ? <>No chats match <span className="text-slate-400">"{chatQuery}"</span></>
                      : <>No conversations yet.<br />Start a new chat above.</>}
                  </p>
                </div>
              )}

              {/* Show all / Show less toggle */}
              {showAllToggleVisible && (
                <button
                  onClick={() => setChatsExpanded((v) => !v)}
                  aria-expanded={chatsExpanded}
                  className="w-full flex items-center justify-center gap-1 px-2 py-1.5 rounded-md text-[11px] font-medium text-slate-500 hover:text-indigo-300 hover:bg-slate-800/40 transition-colors"
                >
                  {chatsExpanded ? (
                    <>
                      <ChevronUp className="w-3 h-3" aria-hidden="true" />
                      Show less
                    </>
                  ) : (
                    <>
                      <ChevronDown className="w-3 h-3" aria-hidden="true" />
                      Show all ({filteredConvs.length})
                    </>
                  )}
                </button>
              )}
            </>
          )}
        </section>

        {/* ============ Section divider ============ */}
        <div className="px-2" aria-hidden="true">
          <div className="border-t border-slate-800/60" />
        </div>

        {/* ============ CONNECTIONS section ============ */}
        <section aria-label="Database connections" className="shrink-0 px-2 pt-3 pb-3 space-y-2">
          <div className="flex items-center gap-1.5 px-2">
            <button
              onClick={() => setConnsOpen((v) => !v)}
              aria-expanded={connsOpen}
              aria-label={connsOpen ? 'Collapse connections' : 'Expand connections'}
              className="flex items-center gap-1.5 group"
            >
              {connsOpen
                ? <ChevronDown  className="w-3 h-3 text-slate-500" aria-hidden="true" />
                : <ChevronRight className="w-3 h-3 text-slate-500" aria-hidden="true" />}
              <Database className="w-3 h-3 text-slate-400" aria-hidden="true" />
              <span className="text-[11px] font-semibold tracking-wider uppercase text-slate-400 group-hover:text-slate-300">
                Connections
              </span>
              <span className="text-[10px] tabular-nums px-1.5 py-0.5 rounded-full font-medium bg-slate-800/80 text-slate-400">
                {sessions.length}
              </span>
            </button>

            <button
              onClick={() => { setShowConnect((v) => !v); setConnsOpen(true) }}
              aria-expanded={showConnect}
              aria-controls="connection-panel"
              aria-label={showConnect ? 'Close connect form' : 'Add new connection'}
              className={`ml-auto p-1 rounded-md transition-colors ${
                showConnect
                  ? 'bg-indigo-600/20 text-indigo-300 ring-1 ring-indigo-500/40'
                  : 'text-slate-500 hover:bg-slate-800/60 hover:text-slate-300'
              }`}
            >
              {showConnect
                ? <X className="w-3.5 h-3.5" aria-hidden="true" />
                : <Plus className="w-3.5 h-3.5" aria-hidden="true" />}
            </button>
          </div>

          {/* Connection form — collapsible, opens above the list */}
          {showConnect && (
            <div
              id="connection-panel"
              className="animate-fadeIn px-2 pt-2 pb-2.5 border border-slate-800/60 rounded-xl bg-slate-900/40 mx-1"
            >
              <ConnectionPanel onConnected={() => setShowConnect(false)} />
            </div>
          )}

          {/* List body
            • Single source type → flat list with a small type badge per row.
            • Multiple types → buckets, but only for non-empty types. */}
          {connsOpen && (
            <>
              {sessions.length === 0 ? (
                <div className="px-3 py-5 text-center border border-dashed border-slate-800/60 rounded-lg mx-1">
                  <Database className="w-5 h-5 mx-auto mb-1.5 text-slate-700" aria-hidden="true" />
                  <p className="text-xs text-slate-600 leading-relaxed">
                    No databases connected.
                  </p>
                  <button
                    onClick={() => setShowConnect(true)}
                    className="mt-2 text-xs text-indigo-400 hover:text-indigo-300 font-medium"
                  >
                    Connect a database →
                  </button>
                </div>
              ) : showBuckets ? (
                <div className="space-y-3 px-1">
                  {activeTypes.map((t) => (
                    <div key={t}>
                      <BucketHeader type={t} count={sessionsByType[t].length} />
                      <div className="space-y-1">
                        {sessionsByType[t].map((s) => (
                          <SessionCard key={s.session_id} session={s} showTypeBadge={false} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                /* Single-type view — no bucket header, just badged rows */
                <div className="space-y-1 px-1">
                  {sessions.map((s) => (
                    <SessionCard key={s.session_id} session={s} showTypeBadge />
                  ))}
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {/* ───────────── Footer ───────────── */}
      <div className="shrink-0 px-3 py-2.5 border-t border-slate-800/60 bg-[#060b18]">
        <div className="flex items-center gap-2">
          <div
            className="w-5 h-5 rounded-md bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-md shadow-indigo-900/30"
            aria-hidden="true"
          >
            <span className="text-[8px] text-white font-bold">AI</span>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-[11px] font-medium text-slate-300 leading-tight">LangGraph + Groq</p>
            <p className="text-[9px] text-slate-600 leading-tight">v1.1 · {provider}</p>
          </div>
        </div>
      </div>
    </div>
  )
}
