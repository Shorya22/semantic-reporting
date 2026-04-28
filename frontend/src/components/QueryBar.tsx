import { useState, useRef, useMemo, useEffect, KeyboardEvent } from 'react'
import { Search, Loader2, ChevronRight, ShieldCheck, Square, Terminal } from 'lucide-react'
import { useStore } from '../store'
import { matchCommands, runSlashCommand, parseSlashCommand, SlashCommand } from '../commands'
import { CommandPalette } from './CommandPalette'

const SUGGESTIONS = [
  'Show total revenue by category with a bar chart',
  'What are the top 10 customers by spending?',
  'Show monthly trend with a line chart',
  'Give me a complete analysis of sales performance',
  'Show revenue breakdown as a pie chart',
  'What are the key insights from this data?',
]

interface Props {
  onSubmit:    (q: string) => void
  isQuerying:  boolean
  disabled:    boolean
  /** When set, pre-fills the textarea and focuses it. The parent clears this
   *  after use by setting it back to an empty string. */
  prefillValue?: string
  onPrefillConsumed?: () => void
}

export function QueryBar({ onSubmit, isQuerying, disabled, prefillValue, onPrefillConsumed }: Props) {
  const [value, setValue] = useState('')
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [paletteIdx, setPaletteIdx] = useState(0)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const stopAnalysis = useStore((s) => s.stopAnalysis)

  // When the parent provides a prefill value, populate the textarea and focus it.
  useEffect(() => {
    if (prefillValue) {
      setValue(prefillValue)
      setShowSuggestions(false)
      setTimeout(() => {
        inputRef.current?.focus()
        // Place cursor at end of text
        const len = prefillValue.length
        inputRef.current?.setSelectionRange(len, len)
      }, 0)
      onPrefillConsumed?.()
    }
  }, [prefillValue, onPrefillConsumed])

  // Detect command mode whenever the input begins with "/".
  const isCommand = value.trimStart().startsWith('/')
  const commandMatches: SlashCommand[] = useMemo(
    () => (isCommand ? matchCommands(value.trimStart()) : []),
    [isCommand, value],
  )

  const submit = async () => {
    const q = value.trim()
    if (!q || disabled) return

    if (isCommand) {
      const ok = await runSlashCommand(q, { runAnalysis: onSubmit })
      if (ok) {
        setValue('')
        setShowSuggestions(false)
      }
      return
    }

    if (isQuerying) return  // gate normal queries while one is in flight
    onSubmit(q)
    setValue('')
    setShowSuggestions(false)
  }

  const completeCommand = (cmd: SlashCommand) => {
    // If the command takes args, leave a trailing space so the user can
    // type the argument; otherwise insert and submit immediately.
    if (cmd.argHint) {
      setValue(`/${cmd.name} `)
      setTimeout(() => inputRef.current?.focus(), 0)
    } else {
      // Run directly.
      runSlashCommand(`/${cmd.name}`, { runAnalysis: onSubmit })
      setValue('')
    }
  }

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Command-palette navigation
    if (isCommand && commandMatches.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setPaletteIdx((i) => Math.min(commandMatches.length - 1, i + 1))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setPaletteIdx((i) => Math.max(0, i - 1))
        return
      }
      if (e.key === 'Tab') {
        e.preventDefault()
        completeCommand(commandMatches[paletteIdx] ?? commandMatches[0])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setValue('')
        return
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      // For commands without arg-hints, Enter should select the highlighted
      // palette item rather than submit the literal text — unless what the
      // user typed already exactly matches a command.
      if (isCommand && commandMatches.length > 0) {
        const parsed = parseSlashCommand(value)
        const exact  = parsed && commandMatches.find((c) => c.name === parsed.name || c.aliases?.includes(parsed.name))
        if (exact) {
          submit()
        } else {
          completeCommand(commandMatches[paletteIdx] ?? commandMatches[0])
        }
        return
      }
      submit()
    }
  }

  // Stop button while a stream is running — replaces the submit button.
  const showStop = isQuerying && !isCommand

  return (
    <div className="relative">
      <div
        className={`relative flex items-end gap-3 bg-[#0c1120] border rounded-2xl px-4 py-3 transition-all ${
          disabled
            ? 'border-slate-800/40 opacity-50'
            : isCommand
              ? 'border-indigo-500/40 shadow-[0_0_20px_rgba(99,102,241,0.10)]'
              : 'border-slate-700/40 focus-within:border-indigo-500/50 focus-within:shadow-[0_0_20px_rgba(99,102,241,0.08)]'
        }`}
      >
        {isCommand
          ? <Terminal className="w-4 h-4 text-indigo-400 shrink-0 mb-0.5" aria-hidden="true" />
          : <Search   className="w-4 h-4 text-slate-500 shrink-0 mb-0.5" aria-hidden="true" />}
        <label htmlFor="query-input" className="sr-only">
          Ask about your data, or type / for slash commands
        </label>
        <textarea
          id="query-input"
          ref={inputRef}
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            setShowSuggestions(e.target.value.length === 0)
            setPaletteIdx(0)
          }}
          onFocus={() => setShowSuggestions(value.length === 0)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
          onKeyDown={handleKey}
          placeholder={
            disabled
              ? 'Connect to a database first…'
              : 'Ask about your data — or type / for commands · Enter to run, Shift+Enter for newline'
          }
          disabled={disabled}
          rows={1}
          className="flex-1 bg-transparent text-sm text-slate-200 placeholder-slate-600 resize-none outline-none leading-relaxed min-h-[1.5rem] max-h-32 overflow-y-auto"
        />

        {/* Stop / Submit button. Stop replaces Submit while streaming. */}
        {showStop ? (
          <button
            type="button"
            onClick={() => stopAnalysis()}
            aria-label="Stop the in-flight analysis"
            title="Stop  ·  /stop"
            className="shrink-0 w-8 h-8 rounded-xl flex items-center justify-center transition-all bg-red-500/15 text-red-300 hover:bg-red-500/25 ring-1 ring-red-500/40 shadow-lg shadow-red-900/20"
          >
            <Square className="w-3.5 h-3.5 fill-current" aria-hidden="true" />
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!value.trim() || (isQuerying && !isCommand) || disabled}
            aria-label={isCommand ? 'Run slash command' : isQuerying ? 'Analysing…' : 'Run query'}
            className={`shrink-0 w-8 h-8 rounded-xl flex items-center justify-center transition-all shadow-lg ${
              isCommand
                ? 'bg-gradient-to-br from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 shadow-indigo-500/20'
                : 'bg-gradient-to-br from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 shadow-indigo-500/20'
            } disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            {isQuerying && !isCommand ? (
              <Loader2 className="w-4 h-4 text-white animate-spin" aria-hidden="true" />
            ) : (
              <ChevronRight className="w-4 h-4 text-white" aria-hidden="true" />
            )}
          </button>
        )}
      </div>

      {/* Read-only / data-only scope hint */}
      <div className="flex items-center justify-between mt-1.5 px-1 text-[10px] text-slate-600 select-none">
        <span className="inline-flex items-center gap-1.5">
          <ShieldCheck className="w-3 h-3 text-emerald-500/80" aria-hidden="true" />
          <span>
            <span className="text-emerald-400/80 font-medium">Read-only</span>
            <span className="mx-1">·</span>
            Data analysis only — try
            <span className="ml-1 font-mono text-indigo-400/80">/help</span>
            <span className="ml-1">for slash commands</span>
          </span>
        </span>
        <span className="hidden sm:flex items-center gap-1">
          <kbd className="font-mono text-[9px] bg-slate-900/80 border border-slate-800 rounded px-1 py-0.5">↵</kbd>
          <span>to run</span>
        </span>
      </div>

      {/* Slash-command palette — shown when the input starts with "/" */}
      <CommandPalette
        visible={isCommand && !disabled}
        query={value.trimStart()}
        selectedIdx={Math.min(paletteIdx, Math.max(0, commandMatches.length - 1))}
        onSelect={completeCommand}
        onHover={setPaletteIdx}
      />

      {/* Plain-text suggestions — only when not in command mode */}
      {!isCommand && showSuggestions && !disabled && !isQuerying && (
        <div
          role="listbox"
          aria-label="Query suggestions"
          className="absolute top-full left-0 right-0 mt-1.5 bg-[#0c1120] border border-slate-800/80 rounded-xl shadow-2xl shadow-black/40 z-20 overflow-hidden"
        >
          <div className="px-3 py-2 text-xs text-slate-600 border-b border-slate-800/80">
            Try asking…
          </div>
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              role="option"
              aria-selected={false}
              onMouseDown={() => {
                setValue(s)
                setShowSuggestions(false)
                setTimeout(() => inputRef.current?.focus(), 0)
              }}
              className="w-full text-left px-4 py-2.5 text-sm text-slate-400 hover:bg-slate-800/60 hover:text-slate-200 transition-colors"
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
