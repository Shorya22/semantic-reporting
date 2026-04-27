import { useState, useRef, KeyboardEvent } from 'react'
import { Search, Loader2, ChevronRight, ShieldCheck } from 'lucide-react'

const SUGGESTIONS = [
  'Show total revenue by category with a bar chart',
  'What are the top 10 customers by spending?',
  'Show monthly trend with a line chart',
  'Give me a complete analysis of sales performance',
  'Show revenue breakdown as a pie chart',
  'What are the key insights from this data?',
]

interface Props {
  onSubmit: (q: string) => void
  isQuerying: boolean
  disabled: boolean
}

export function QueryBar({ onSubmit, isQuerying, disabled }: Props) {
  const [value, setValue] = useState('')
  const [showSuggestions, setShowSuggestions] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const submit = () => {
    const q = value.trim()
    if (!q || isQuerying || disabled) return
    onSubmit(q)
    setValue('')
    setShowSuggestions(false)
  }

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="relative">
      <div
        className={`relative flex items-end gap-3 bg-[#0c1120] border rounded-2xl px-4 py-3 transition-all ${
          disabled
            ? 'border-slate-800/40 opacity-50'
            : 'border-slate-700/40 focus-within:border-indigo-500/50 focus-within:shadow-[0_0_20px_rgba(99,102,241,0.08)]'
        }`}
      >
        <Search className="w-4 h-4 text-slate-500 shrink-0 mb-0.5" aria-hidden="true" />
        <label htmlFor="query-input" className="sr-only">
          Ask anything about your data
        </label>
        <textarea
          id="query-input"
          ref={inputRef}
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            setShowSuggestions(e.target.value.length === 0)
          }}
          onFocus={() => setShowSuggestions(value.length === 0)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
          onKeyDown={handleKey}
          placeholder={
            disabled
              ? 'Connect to a database first…'
              : 'Ask about your data — read-only · Enter to run, Shift+Enter for newline'
          }
          disabled={disabled || isQuerying}
          rows={1}
          className="flex-1 bg-transparent text-sm text-slate-200 placeholder-slate-600 resize-none outline-none leading-relaxed min-h-[1.5rem] max-h-32 overflow-y-auto"
        />
        <button
          onClick={submit}
          disabled={!value.trim() || isQuerying || disabled}
          aria-label={isQuerying ? 'Analyzing…' : 'Run query'}
          className="shrink-0 w-8 h-8 rounded-xl flex items-center justify-center transition-all bg-gradient-to-br from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 disabled:opacity-40 disabled:cursor-not-allowed shadow-lg shadow-indigo-500/20"
        >
          {isQuerying ? (
            <Loader2 className="w-4 h-4 text-white animate-spin" aria-hidden="true" />
          ) : (
            <ChevronRight className="w-4 h-4 text-white" aria-hidden="true" />
          )}
        </button>
      </div>

      {/* Read-only / data-only scope hint — surfaces the guardrail policy
          so users see at a glance what the assistant will and won't do. */}
      <div className="flex items-center justify-between mt-1.5 px-1 text-[10px] text-slate-600 select-none">
        <span className="inline-flex items-center gap-1.5">
          <ShieldCheck className="w-3 h-3 text-emerald-500/80" aria-hidden="true" />
          <span>
            <span className="text-emerald-400/80 font-medium">Read-only</span>
            <span className="mx-1">·</span>
            Data analysis only — no writes, deletes, or off-topic requests
          </span>
        </span>
        <span className="hidden sm:block">
          <kbd className="font-mono text-[9px] bg-slate-900/80 border border-slate-800 rounded px-1 py-0.5">↵</kbd>
          <span className="ml-1">to run</span>
        </span>
      </div>

      {/* Suggestion dropdown */}
      {showSuggestions && !disabled && !isQuerying && (
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
