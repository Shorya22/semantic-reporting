import { Sparkles, AlertTriangle, Lightbulb, CheckCircle2 } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CritiqueReport, InsightReport } from '../types'

interface Props {
  insight: InsightReport
  critique?: CritiqueReport | null
}

/**
 * Executive narrative panel — renders the structured InsightReport produced
 * by the Insight Agent (headline, exec summary, findings, anomalies, recos).
 * Optionally appends Critic warnings/issues at the bottom for transparency.
 */
export function InsightSection({ insight, critique }: Props) {
  const hasInsight =
    insight.headline ||
    insight.executive_summary ||
    insight.key_findings.length ||
    insight.anomalies.length ||
    insight.recommendations.length

  if (!hasInsight) return null

  return (
    <section
      aria-label="AI Analysis"
      className="bg-gradient-to-br from-indigo-950/20 to-slate-900/60 border border-indigo-700/30 rounded-2xl px-5 py-4 space-y-4"
    >
      {/* Header */}
      <div className="flex items-start gap-2">
        <div className="shrink-0 w-7 h-7 rounded-lg bg-indigo-600/30 border border-indigo-500/40 flex items-center justify-center mt-0.5">
          <Sparkles className="w-3.5 h-3.5 text-indigo-300" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[10px] uppercase tracking-wider text-indigo-300 font-semibold">
            AI Analysis
          </div>
          {insight.headline && (
            <div className="text-sm font-semibold text-slate-100 mt-0.5 leading-snug">
              {insight.headline}
            </div>
          )}
        </div>
      </div>

      {/* Executive summary */}
      {insight.executive_summary && (
        <div className="prose prose-invert prose-sm max-w-none prose-p:my-1.5 prose-p:leading-relaxed text-slate-300">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {insight.executive_summary}
          </ReactMarkdown>
        </div>
      )}

      {/* Key findings + anomalies + recommendations grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {insight.key_findings.length > 0 && (
          <Block
            icon={<CheckCircle2 className="w-3.5 h-3.5 text-emerald-300" aria-hidden="true" />}
            title="Key findings"
            tone="emerald"
            items={insight.key_findings}
          />
        )}
        {insight.anomalies.length > 0 && (
          <Block
            icon={<AlertTriangle className="w-3.5 h-3.5 text-amber-300" aria-hidden="true" />}
            title="Anomalies"
            tone="amber"
            items={insight.anomalies}
          />
        )}
        {insight.recommendations.length > 0 && (
          <Block
            icon={<Lightbulb className="w-3.5 h-3.5 text-indigo-300" aria-hidden="true" />}
            title="Recommendations"
            tone="indigo"
            items={insight.recommendations}
          />
        )}
      </div>

      {/* Critic issues (advisory) */}
      {critique && critique.issues.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-slate-500 hover:text-slate-300 select-none">
            Quality review · {critique.issues.length} issue{critique.issues.length === 1 ? '' : 's'} ·
            score {(critique.score * 100).toFixed(0)}%
          </summary>
          <ul className="mt-2 space-y-1.5 pl-4">
            {critique.issues.map((iss, idx) => (
              <li
                key={idx}
                className="flex items-start gap-2 text-slate-400"
              >
                <span
                  className={`shrink-0 mt-1 w-1.5 h-1.5 rounded-full ${
                    iss.severity === 'error' ? 'bg-red-400'
                      : iss.severity === 'warning' ? 'bg-amber-400'
                      : 'bg-slate-500'
                  }`}
                  aria-hidden="true"
                />
                <div>
                  <span className="font-semibold text-slate-300">
                    [{iss.severity}]
                  </span>{' '}
                  {iss.message}
                  {iss.location && (
                    <span className="text-slate-600 ml-1">({iss.location})</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

interface BlockProps {
  icon: React.ReactNode
  title: string
  items: string[]
  tone: 'emerald' | 'amber' | 'indigo'
}

const TONE_CLASSES: Record<BlockProps['tone'], { border: string; text: string }> = {
  emerald: { border: 'border-emerald-700/30', text: 'text-emerald-300' },
  amber:   { border: 'border-amber-700/30',   text: 'text-amber-300'   },
  indigo:  { border: 'border-indigo-700/30',  text: 'text-indigo-300'  },
}

function Block({ icon, title, items, tone }: BlockProps) {
  const t = TONE_CLASSES[tone]
  return (
    <div className={`bg-slate-900/50 border ${t.border} rounded-xl p-3`}>
      <div className="flex items-center gap-1.5 mb-2">
        {icon}
        <span className={`text-[10px] uppercase tracking-wider font-semibold ${t.text}`}>
          {title}
        </span>
      </div>
      <ul className="space-y-1.5 text-xs text-slate-300 leading-relaxed">
        {items.map((it, i) => (
          <li key={i} className="flex gap-1.5">
            <span aria-hidden="true" className="text-slate-600 shrink-0">·</span>
            <span>{it}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
