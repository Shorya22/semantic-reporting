import { useEffect, useMemo, useRef } from 'react'
import {
  HelpCircle, Square, RotateCcw, Plus, Database, Eraser, FileDown,
  Trash2, Cpu, Cloud, PanelLeft, ChevronRight, Terminal, ArrowDown, ArrowUp,
} from 'lucide-react'
import { matchCommands, SlashCommand } from '../commands'

const ICONS: Record<string, typeof HelpCircle> = {
  help:       HelpCircle,
  stop:       Square,
  retry:      RotateCcw,
  continue:   ChevronRight,
  clear:      Eraser,
  new:        Plus,
  tables:     Database,
  schema:     Database,
  export:     FileDown,
  disconnect: Trash2,
  model:      Cpu,
  provider:   Cloud,
  sidebar:    PanelLeft,
}


interface Props {
  query:        string
  visible:      boolean
  selectedIdx:  number
  onSelect:     (cmd: SlashCommand) => void
  onHover:      (idx: number) => void
}

export function CommandPalette({ query, visible, selectedIdx, onSelect, onHover }: Props) {
  const list = useMemo(() => matchCommands(query), [query])
  const refs = useRef<Array<HTMLButtonElement | null>>([])

  // Keep the highlighted item in view when the user navigates with arrows.
  useEffect(() => {
    refs.current[selectedIdx]?.scrollIntoView({ block: 'nearest' })
  }, [selectedIdx])

  if (!visible) return null

  return (
    <div
      role="listbox"
      aria-label="Slash command palette"
      className="absolute bottom-full left-0 right-0 mb-2 bg-[#0c1120] border border-slate-800/80 rounded-xl shadow-2xl shadow-black/50 z-30 overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-800/80 bg-slate-900/40">
        <Terminal className="w-3 h-3 text-indigo-400" aria-hidden="true" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          Slash commands
        </span>
        <span className="ml-auto flex items-center gap-1 text-[10px] text-slate-600">
          <kbd className="font-mono bg-slate-900/80 border border-slate-800 rounded px-1 py-0.5"><ArrowUp className="w-2.5 h-2.5 inline" aria-hidden="true" /></kbd>
          <kbd className="font-mono bg-slate-900/80 border border-slate-800 rounded px-1 py-0.5"><ArrowDown className="w-2.5 h-2.5 inline" aria-hidden="true" /></kbd>
          <span className="ml-1">navigate</span>
          <kbd className="ml-2 font-mono bg-slate-900/80 border border-slate-800 rounded px-1 py-0.5">↵</kbd>
          <span>select</span>
          <kbd className="ml-2 font-mono bg-slate-900/80 border border-slate-800 rounded px-1 py-0.5">esc</kbd>
          <span>close</span>
        </span>
      </div>

      {/* List */}
      {list.length === 0 ? (
        <div className="px-4 py-6 text-center">
          <p className="text-xs text-slate-500">
            No commands match <span className="text-slate-300 font-medium">"{query}"</span>.
          </p>
          <p className="text-[10px] text-slate-700 mt-1">Try <kbd className="font-mono bg-slate-900/80 border border-slate-800 rounded px-1 py-0.5">/help</kbd></p>
        </div>
      ) : (
        <div className="max-h-72 overflow-y-auto py-1">
          {list.map((cmd, i) => {
            const Icon     = ICONS[cmd.name] ?? Terminal
            const selected = i === selectedIdx
            return (
              <button
                key={cmd.name}
                ref={(el) => { refs.current[i] = el }}
                role="option"
                aria-selected={selected}
                onMouseDown={(e) => { e.preventDefault(); onSelect(cmd) }}
                onMouseEnter={() => onHover(i)}
                className={`w-full flex items-center gap-3 px-3 py-2 text-left transition-colors ${
                  selected ? 'bg-indigo-500/10' : 'hover:bg-slate-800/50'
                }`}
              >
                <span className={`shrink-0 w-7 h-7 rounded-md flex items-center justify-center ${
                  selected ? 'bg-indigo-500/20 text-indigo-300' : 'bg-slate-800/80 text-slate-400'
                }`}>
                  <Icon className="w-3.5 h-3.5" aria-hidden="true" />
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-1.5">
                    <span className={`font-mono text-xs font-semibold ${
                      selected ? 'text-indigo-200' : 'text-slate-200'
                    }`}>
                      /{cmd.name}
                    </span>
                    {cmd.argHint && (
                      <span className={`font-mono text-[10px] ${
                        selected ? 'text-indigo-400/70' : 'text-slate-600'
                      }`}>
                        {cmd.argHint}
                      </span>
                    )}
                    {cmd.aliases && cmd.aliases.length > 0 && (
                      <span className="ml-auto text-[9px] text-slate-700 font-mono">
                        {cmd.aliases.map((a) => `/${a}`).join(' · ')}
                      </span>
                    )}
                  </div>
                  <p className={`text-[11px] truncate ${
                    selected ? 'text-slate-300' : 'text-slate-500'
                  }`}>
                    {cmd.description}
                  </p>
                </div>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
