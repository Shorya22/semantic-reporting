import { useState } from 'react'
import { ChevronDown, ChevronRight, BarChart2, Table2, Zap, BrainCircuit } from 'lucide-react'
import { AgentStep } from '../types'

interface Props {
  steps: AgentStep[]
  isRunning: boolean
}

const TOOL_ICONS: Record<string, React.ReactNode> = {
  execute_sql:    <Table2    className="w-3 h-3" aria-hidden="true" />,
  generate_chart: <BarChart2 className="w-3 h-3" aria-hidden="true" />,
}

export function AgentProgress({ steps, isRunning }: Props) {
  const [open, setOpen] = useState(true)

  if (!steps.length && !isRunning) return null

  return (
    <div className="bg-[#0c1120] border border-slate-800/60 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center justify-between px-4 py-2.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
      >
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded-md bg-indigo-600/20 border border-indigo-500/20 flex items-center justify-center">
            <BrainCircuit className="w-3 h-3 text-indigo-400" aria-hidden="true" />
          </div>
          <span className="font-medium text-slate-300">Agent Reasoning</span>
          <span className="px-1.5 py-0.5 rounded-md bg-slate-800/80 border border-slate-700/40 text-slate-500">
            {steps.length} {steps.length === 1 ? 'step' : 'steps'}
          </span>
          {isRunning && (
            <span className="text-xs text-indigo-400/80 italic">AI is reasoning autonomously…</span>
          )}
        </div>
        {open
          ? <ChevronDown  className="w-3.5 h-3.5 text-slate-600" aria-hidden="true" />
          : <ChevronRight className="w-3.5 h-3.5 text-slate-600" aria-hidden="true" />}
      </button>

      {open && (
        <div className="px-4 pb-3 space-y-1.5 border-t border-slate-800/60">
          {steps.map((step, i) => (
            <div key={i} className="flex items-start gap-2.5 py-1">
              <div
                className={`mt-0.5 shrink-0 w-5 h-5 rounded-md flex items-center justify-center text-xs ${
                  step.type === 'tool_start'
                    ? 'bg-indigo-900/50 border border-indigo-700/30 text-indigo-400'
                    : 'bg-emerald-900/30 border border-emerald-700/20 text-emerald-400'
                }`}
              >
                {TOOL_ICONS[step.tool] ?? <Zap className="w-3 h-3" aria-hidden="true" />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className={`text-xs font-medium font-mono ${
                    step.type === 'tool_start' ? 'text-indigo-300' : 'text-emerald-300'
                  }`}>
                    {step.type === 'tool_start' ? '→' : '←'} {step.tool}
                  </span>
                </div>
                {step.input && (
                  <p className="text-xs text-slate-500 truncate mt-0.5 font-mono">
                    {step.input.slice(0, 120)}
                  </p>
                )}
                {step.output && !step.output.startsWith('Chart rendered') && (
                  <p className="text-xs text-slate-600 truncate mt-0.5 font-mono">
                    {step.output.slice(0, 120)}
                  </p>
                )}
              </div>
            </div>
          ))}

          {isRunning && (
            <div className="flex items-center gap-2 py-1.5 text-xs text-slate-500">
              <span className="flex gap-0.5" aria-label="Analyzing">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="w-1 h-1 rounded-full bg-indigo-400 animate-bounce"
                    style={{ animationDelay: `${i * 150}ms` }}
                    aria-hidden="true"
                  />
                ))}
              </span>
              Processing…
            </div>
          )}
        </div>
      )}
    </div>
  )
}
