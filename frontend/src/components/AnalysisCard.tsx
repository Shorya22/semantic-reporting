import { Clock } from 'lucide-react'
import { AnalysisResult } from '../types'
import { useStore } from '../store'
import { api } from '../api/client'
import { AgentProgress } from './AgentProgress'
import { EChartCard } from './EChartCard'
import { DataTable } from './DataTable'
import { InsightPanel } from './InsightPanel'

interface Props {
  analysis: AnalysisResult
}

function StatusBadge({ status }: { status: AnalysisResult['status'] }) {
  if (status === 'running') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-indigo-300 bg-indigo-900/30 border border-indigo-700/40 rounded-full px-2 py-0.5 font-medium">
        <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" aria-hidden="true" />
        Analyzing
      </span>
    )
  }
  if (status === 'done') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-300 bg-emerald-900/20 border border-emerald-700/30 rounded-full px-2 py-0.5 font-medium">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" aria-hidden="true" />
        Done
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-red-300 bg-red-900/20 border border-red-700/30 rounded-full px-2 py-0.5 font-medium">
      <span className="w-1.5 h-1.5 rounded-full bg-red-400" aria-hidden="true" />
      Error
    </span>
  )
}

function ExportBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="px-2.5 py-1 rounded-md border border-slate-700/60 bg-slate-900/60 text-slate-300 hover:border-indigo-500/40 hover:text-indigo-300 transition-colors"
    >
      {label}
    </button>
  )
}

export function AnalysisCard({ analysis }: Props) {
  const activeSessionId = useStore((s) => s.activeSessionId)
  const isRunning = analysis.status === 'running'

  return (
    <div className="space-y-4 border-l-2 border-indigo-500/30 pl-4">
      {/* Question header */}
      <div className="flex items-start gap-3">
        <div className="shrink-0 w-7 h-7 rounded-lg bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center mt-0.5">
          <span className="text-xs text-indigo-400 font-bold" aria-hidden="true">Q</span>
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-slate-200 leading-relaxed">
            {analysis.question}
          </p>
          <div className="flex items-center gap-2 mt-1.5">
            <span className="text-xs text-slate-500 flex items-center gap-1">
              <Clock className="w-3 h-3" aria-hidden="true" />
              {analysis.startedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
            <StatusBadge status={analysis.status} />
          </div>
        </div>
      </div>

      {/* Agent reasoning timeline */}
      <AgentProgress steps={analysis.steps} isRunning={isRunning} />

      {/* Charts — 2 columns on lg+ */}
      {analysis.charts.length > 0 && (
        <div
          className={`grid gap-4 ${
            analysis.charts.length === 1 ? 'grid-cols-1' : 'grid-cols-1 lg:grid-cols-2'
          }`}
        >
          {analysis.charts.map((chart) => (
            <EChartCard key={chart.id} chart={chart} />
          ))}
        </div>
      )}

      {/* Data tables */}
      {analysis.tables.length > 0 && (
        <div className="space-y-3">
          {analysis.tables.map((table) => (
            <DataTable
              key={table.id}
              table={table}
              sessionId={activeSessionId ?? undefined}
            />
          ))}
        </div>
      )}

      {/* Streaming AI insight + usage stats */}
      <InsightPanel
        content={analysis.insight}
        isStreaming={isRunning && analysis.insight.length > 0}
        usage={analysis.usage}
      />

      {/* Export buttons — persist after refresh via exportSql stored in DB */}
      {analysis.exportSql && analysis.status === 'done' && activeSessionId && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-500">Export:</span>
          <ExportBtn
            label="CSV"
            onClick={() => api.exportCsv(activeSessionId, analysis.exportSql!, analysis.question.slice(0, 40))}
          />
          <ExportBtn
            label="Excel"
            onClick={() => api.exportExcel(activeSessionId, analysis.exportSql!, analysis.question.slice(0, 40))}
          />
          <ExportBtn
            label="PDF"
            onClick={() => api.exportPdf(activeSessionId, analysis.exportSql!, analysis.question.slice(0, 40))}
          />
        </div>
      )}

      {/* Error state */}
      {analysis.error && (
        <div
          role="alert"
          className="flex items-start gap-2 text-xs text-red-400 bg-red-900/20 border border-red-800/40 rounded-xl px-4 py-3"
        >
          <span className="shrink-0 mt-0.5" aria-hidden="true">⚠</span>
          <span>{analysis.error}</span>
        </div>
      )}
    </div>
  )
}
