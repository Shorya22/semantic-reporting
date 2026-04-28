/**
 * Slash-command system.
 *
 * Architecture
 * ------------
 *  • Each command is a self-contained `SlashCommand` definition with a
 *    `run(rawArgs, ctx)` callback. Adding a new command means appending
 *    one entry to `COMMANDS` — no UI plumbing needed.
 *  • `parseSlashCommand(input)` turns a raw text line that starts with
 *    "/" into `{ name, rawArgs }` or null.
 *  • `runSlashCommand(input, ctx)` parses + dispatches; returns true if
 *    the input was a slash command (handled), false otherwise.
 *  • `matchCommands(prefix)` powers the autocomplete palette.
 *
 * The CommandPalette UI just consumes these — keep all behaviour here.
 */

import { api }                from '../api/client'
import { GROQ_MODELS, OLLAMA_FALLBACK_MODELS, LlmProvider } from '../types'
import { useStore }           from '../store'

// Re-export for documentation completeness — every command reaches state
// through ``useStore.getState()`` rather than a context-passed handle.

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type CommandCategory = 'chat' | 'connection' | 'navigation' | 'utility'

export interface CommandContext {
  /** Issue a regular natural-language query. Slash commands that decide
   *  to forward to the LLM (e.g. ``/retry``, ``/continue``) call this. */
  runAnalysis: (q: string) => void
}

export interface SlashCommand {
  name: string
  aliases?:    string[]
  description: string
  /** Hint shown next to the name in the palette, e.g. "csv|excel|pdf". */
  argHint?:    string
  category:    CommandCategory
  run: (rawArgs: string, ctx: CommandContext) => Promise<void> | void
}


// ---------------------------------------------------------------------------
// Helpers used by command implementations
// ---------------------------------------------------------------------------

function lastUserQuestion(): string | null {
  const analyses = useStore.getState().analyses
  for (let i = analyses.length - 1; i >= 0; i--) {
    const a = analyses[i]
    // Synthetic info / error cards have a question that starts with "/" — skip them.
    if (a.question && !a.question.startsWith('/')) return a.question
  }
  return null
}

function lastExportCtx(): { sql: string; sessionId: string; title: string } | null {
  const analyses = useStore.getState().analyses
  for (let i = analyses.length - 1; i >= 0; i--) {
    const a = analyses[i]
    if (a.exportSql && a.exportSessionId) {
      return {
        sql:       a.exportSql,
        sessionId: a.exportSessionId,
        title:     a.question.slice(0, 40) || 'Data Report',
      }
    }
  }
  return null
}


// ---------------------------------------------------------------------------
// Built-in commands
// ---------------------------------------------------------------------------

export const COMMANDS: SlashCommand[] = [
  // ────────────────────────────── chat control ──────────────────────────────

  {
    name: 'help',
    aliases: ['?'],
    category: 'utility',
    description: 'List all available slash commands',
    run: async (_args, _ctx) => {
      const lines = [
        '**Available commands**',
        '',
        ...COMMANDS.map((c) => {
          const aliases = c.aliases?.length ? ` (alias: ${c.aliases.map((a) => `\`/${a}\``).join(', ')})` : ''
          const args    = c.argHint ? ` \`${c.argHint}\`` : ''
          return `• \`/${c.name}\`${args}${aliases} — ${c.description}`
        }),
        '',
        '_Tip: type `/` in the input bar to bring up the autocomplete palette. Use ↑ ↓ to navigate, Enter to select._',
      ]
      useStore.getState().emitInfoCard('/help', lines.join('\n'))
    },
  },

  {
    name: 'stop',
    aliases: ['cancel', 'abort'],
    category: 'chat',
    description: 'Stop the in-flight analysis (keeps partial results visible)',
    run: async (_args, _ctx) => {
      const st = useStore.getState()
      if (!st.isQuerying) {
        st.emitInfoCard('/stop', 'Nothing is running right now.')
        return
      }
      st.stopAnalysis()
    },
  },

  {
    name: 'retry',
    aliases: ['rerun'],
    category: 'chat',
    description: 'Re-run the last user question',
    run: async (_args, ctx) => {
      const last = lastUserQuestion()
      if (!last) {
        useStore.getState().emitInfoCard('/retry', 'No previous question to retry yet.')
        return
      }
      ctx.runAnalysis(last)
    },
  },

  {
    name: 'continue',
    category: 'chat',
    description: 'Ask the assistant to continue the previous answer',
    run: async (_args, ctx) => {
      const last = lastUserQuestion()
      if (!last) {
        useStore.getState().emitInfoCard('/continue', 'There is nothing to continue from.')
        return
      }
      ctx.runAnalysis(`Continue your previous analysis of: "${last}". Add what you missed and dig deeper.`)
    },
  },

  {
    name: 'clear',
    aliases: ['cls'],
    category: 'chat',
    description: 'Clear the visible analysis cards in this conversation',
    run: async () => {
      useStore.getState().setAnalyses([])
    },
  },

  {
    name: 'new',
    aliases: ['n'],
    category: 'chat',
    description: 'Start a new conversation',
    run: async () => {
      const st = useStore.getState()
      try {
        const conv = await api.createConversation({
          title:         'New chat',
          connection_id: st.activeSessionId,
          model:         st.model,
          provider:      st.provider,
        })
        st.upsertConversation(conv)
        st.setActiveConversation(conv.id)
        st.setAnalyses([])
      } catch (e) {
        st.emitErrorCard('/new', e instanceof Error ? e.message : 'Failed to create conversation')
      }
    },
  },

  // ────────────────────────────── data inspection ──────────────────────────

  {
    name: 'tables',
    aliases: ['t'],
    category: 'utility',
    description: 'List the tables available on the active connection',
    run: async () => {
      const st = useStore.getState()
      const sid = st.activeSessionId
      if (!sid) {
        st.emitErrorCard('/tables', 'No active database connection.')
        return
      }
      const session = st.sessions.find((s) => s.session_id === sid)
      const tables  = session?.tables ?? []
      if (!tables.length) {
        st.emitInfoCard('/tables', 'No tables found on the active connection.')
        return
      }
      const md = [
        `**${tables.length} table${tables.length === 1 ? '' : 's'}** in \`${session?.name ?? 'current connection'}\`:`,
        '',
        ...tables.map((t) => `• \`${t}\``),
      ].join('\n')
      st.emitInfoCard('/tables', md)
    },
  },

  {
    name: 'schema',
    category: 'utility',
    description: 'Show the active database schema (DDL)',
    run: async () => {
      const st = useStore.getState()
      const sid = st.activeSessionId
      if (!sid) {
        st.emitErrorCard('/schema', 'No active database connection.')
        return
      }
      try {
        // Re-use /connections/{id} which returns metadata including schema_ddl
        // when the manager has it cached. Fall back to listing tables if not.
        const meta = await fetch(`/api/v1/connections/${sid}`).then((r) => r.json())
        const ddl  = meta?.data?.schema_ddl as string | undefined
        const tables = (meta?.data?.tables as string[] | undefined) ?? []
        if (ddl) {
          st.emitInfoCard('/schema', '```sql\n' + ddl.slice(0, 6000) + '\n```')
        } else if (tables.length) {
          st.emitInfoCard('/schema', `Tables: ${tables.map((t) => '`' + t + '`').join(', ')}`)
        } else {
          st.emitInfoCard('/schema', 'No schema available.')
        }
      } catch (e) {
        st.emitErrorCard('/schema', e instanceof Error ? e.message : 'Failed to fetch schema')
      }
    },
  },

  // ────────────────────────────── exports ──────────────────────────────────

  {
    name: 'export',
    argHint: 'csv|excel|pdf',
    category: 'utility',
    description: 'Download the most recent query result',
    run: async (rawArgs) => {
      const st  = useStore.getState()
      const ctx = lastExportCtx()
      if (!ctx) {
        st.emitErrorCard('/export', 'No exportable result yet — run a query first.')
        return
      }
      const fmt = (rawArgs || 'csv').trim().toLowerCase()
      try {
        if (fmt === 'csv')        await api.exportCsv  (ctx.sessionId, ctx.sql, ctx.title)
        else if (fmt === 'excel' || fmt === 'xlsx') await api.exportExcel(ctx.sessionId, ctx.sql, ctx.title)
        else if (fmt === 'pdf')   await api.exportPdf  (ctx.sessionId, ctx.sql, ctx.title)
        else {
          st.emitErrorCard('/export', `Unknown format \`${fmt}\`. Use \`csv\`, \`excel\`, or \`pdf\`.`)
        }
      } catch (e) {
        st.emitErrorCard('/export', e instanceof Error ? e.message : 'Export failed')
      }
    },
  },

  // ────────────────────────────── connections ──────────────────────────────

  {
    name: 'disconnect',
    category: 'connection',
    description: 'Disconnect the active database',
    run: async () => {
      const st = useStore.getState()
      const sid = st.activeSessionId
      if (!sid) {
        st.emitErrorCard('/disconnect', 'No active database to disconnect.')
        return
      }
      try {
        await api.disconnect(sid)
        st.removeSession(sid)
        st.emitInfoCard('/disconnect', 'Connection closed.')
      } catch (e) {
        st.emitErrorCard('/disconnect', e instanceof Error ? e.message : 'Disconnect failed')
      }
    },
  },

  // ────────────────────────────── settings ─────────────────────────────────

  {
    name: 'model',
    argHint: '<model-id>',
    category: 'utility',
    description: 'Switch the active LLM model',
    run: async (rawArgs) => {
      const st = useStore.getState()
      const id = rawArgs.trim()
      if (!id) {
        const list = st.provider === 'ollama'
          ? (st.ollamaModels.length > 0 ? st.ollamaModels : OLLAMA_FALLBACK_MODELS)
          : GROQ_MODELS
        st.emitInfoCard(
          '/model',
          ['**Available models** for ' + st.provider + ':', '', ...list.map((m) => `• \`${m.id}\` — ${m.label}`)].join('\n'),
        )
        return
      }
      st.setModel(id)
      st.emitInfoCard('/model', `Model switched to \`${id}\`.`)
    },
  },

  {
    name: 'provider',
    argHint: 'groq|ollama',
    category: 'utility',
    description: 'Switch the active LLM provider',
    run: async (rawArgs) => {
      const st = useStore.getState()
      const arg = rawArgs.trim().toLowerCase() as LlmProvider
      if (arg !== 'groq' && arg !== 'ollama') {
        st.emitErrorCard('/provider', 'Use `/provider groq` or `/provider ollama`.')
        return
      }
      st.setProvider(arg)
      if (arg === 'groq') st.setModel(GROQ_MODELS[0].id)
      st.emitInfoCard('/provider', `Provider switched to \`${arg}\`.`)
    },
  },

  // ────────────────────────────── navigation ───────────────────────────────

  {
    name: 'sidebar',
    aliases: ['toggle'],
    category: 'navigation',
    description: 'Show or hide the left sidebar',
    run: async () => {
      useStore.getState().toggleSidebar()
    },
  },
]


// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface ParsedSlashCommand {
  name:    string
  rawArgs: string
  raw:     string
}

export function parseSlashCommand(input: string): ParsedSlashCommand | null {
  const trimmed = input.trimStart()
  if (!trimmed.startsWith('/')) return null
  const stripped = trimmed.slice(1)
  // Split on the first run of whitespace.
  const m = stripped.match(/^([a-zA-Z][\w-]*)(?:\s+([\s\S]*))?$/)
  if (!m) return { name: '', rawArgs: '', raw: input }
  return { name: m[1].toLowerCase(), rawArgs: (m[2] ?? '').trim(), raw: input }
}

/** Resolve a typed name (or alias) to the canonical command. */
export function resolveCommand(name: string): SlashCommand | null {
  if (!name) return null
  const lower = name.toLowerCase()
  for (const cmd of COMMANDS) {
    if (cmd.name === lower) return cmd
    if (cmd.aliases?.includes(lower)) return cmd
  }
  return null
}

/** Run a slash command. Returns false if the input wasn't a command. */
export async function runSlashCommand(
  input: string,
  ctx: CommandContext,
): Promise<boolean> {
  const parsed = parseSlashCommand(input)
  if (!parsed) return false
  const cmd = resolveCommand(parsed.name)
  if (!cmd) {
    useStore.getState().emitErrorCard(
      input,
      `Unknown command \`/${parsed.name || '?'}\`. Type \`/help\` to see what's available.`,
    )
    return true
  }
  try {
    await cmd.run(parsed.rawArgs, ctx)
  } catch (e) {
    useStore.getState().emitErrorCard(
      input,
      e instanceof Error ? e.message : 'Command failed',
    )
  }
  return true
}

/** Filter commands by typed prefix, ranked exact-name first. */
export function matchCommands(prefix: string): SlashCommand[] {
  const q = prefix.replace(/^\//, '').toLowerCase()
  if (!q) return COMMANDS
  const exact:   SlashCommand[] = []
  const starts:  SlashCommand[] = []
  const substr:  SlashCommand[] = []
  for (const cmd of COMMANDS) {
    const allNames = [cmd.name, ...(cmd.aliases ?? [])]
    if      (allNames.some((n) => n === q))             exact.push(cmd)
    else if (allNames.some((n) => n.startsWith(q)))     starts.push(cmd)
    else if (allNames.some((n) => n.includes(q)))       substr.push(cmd)
    else if (cmd.description.toLowerCase().includes(q)) substr.push(cmd)
  }
  return [...exact, ...starts, ...substr]
}
