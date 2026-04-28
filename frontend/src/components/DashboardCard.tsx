import { Activity, Clock, Loader2, ListChecks, FileDown, Zap } from 'lucide-react'
import { useState } from 'react'
import { AnalysisResult, CritiqueReport, TokenUsage } from '../types'
import { DashboardCanvas } from './DashboardCanvas'
import { InsightSection } from './InsightSection'
import { InsightPanel } from './InsightPanel'
import { EChartCard } from './EChartCard'
import { DataTable } from './DataTable'
import { useStore } from '../store'
import { api } from '../api/client'

function fmtMs(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`
}

/** Bottom-right telemetry strip — total wall-clock + input/output token totals.
 *
 * Always shows all three numbers when usage data is present, even if the
 * token counts are zero (so the user has explicit confirmation that the
 * pipeline reported nothing — this distinguishes "0 tokens" from
 * "metric not available").
 *
 * Renders right-aligned so it sits in the bottom-right corner of the
 * dashboard card after charts, insights, and exports. */
function UsageBar({ usage }: { usage: TokenUsage }) {
  const elapsed =
    typeof usage.total_elapsed_ms === 'number' && usage.total_elapsed_ms > 0
      ? usage.total_elapsed_ms
      : typeof usage.latency_ms === 'number' && usage.latency_ms > 0
        ? usage.latency_ms
        : null

  const inTok  = usage.input_tokens  ?? 0
  const outTok = usage.output_tokens ?? 0

  if (elapsed == null && inTok === 0 && outTok === 0) return null

  return (
    <div className="flex justify-end mt-2">
      <span
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-indigo-500/10 text-indigo-300 ring-1 ring-indigo-500/20 text-[11px] font-medium tabular-nums"
        title={`Total latency: ${elapsed != null ? fmtMs(elapsed) : '—'}\nInput tokens: ${inTok.toLocaleString()}\nOutput tokens: ${outTok.toLocaleString()}`}
      >
        <Zap className="w-3 h-3" aria-hidden="true" />
        {elapsed != null && <span>{fmtMs(elapsed)}</span>}
        {elapsed != null && <span aria-hidden="true" className="opacity-60">·</span>}
        <span>↑{inTok.toLocaleString()}</span>
        <span aria-hidden="true" className="opacity-60">·</span>
        <span>↓{outTok.toLocaleString()}</span>
        <span className="opacity-60 ml-0.5">tok</span>
      </span>
    </div>
  )
}

interface Props {
  analysis: AnalysisResult
}

/**
 * Multi-agent "Dashboard" rendering of one AnalysisResult.
 *
 * Used for any analysis whose intent is dashboard / report / exploration with
 * 2+ visuals. Composes:
 *
 *   * Title bar — question, intent badge, status pill
 *   * Live agent timeline — query progress (only while running)
 *   * Insight section — exec summary + findings + anomalies + recos + critique
 *   * DashboardCanvas — the actual KPI strip + chart grid + tables
 *   * Export bar — CSV / Excel / PDF buttons (when exportSql is available)
 *
 * Falls back gracefully when partial data is available (e.g. plan loaded but
 * visuals still streaming in).
 */
export function DashboardCard({ analysis }: Props) {
  const activeSessionId = useStore((s) => s.activeSessionId)
  const isRunning = analysis.status === 'running'
  const [reportLoading, setReportLoading] = useState<'pdf' | 'xlsx' | null>(null)

  const intent = analysis.intentInfo
  const plan = analysis.planInfo
  const visuals = analysis.visuals ?? []
  const insight = analysis.insightReport
  const critique = analysis.critique
  const progress = analysis.queryProgress ?? []

  const queriesRunning = progress.filter((q) => q.status === 'running').length
  const queriesDone = progress.filter((q) => q.status === 'done').length
  const queriesError = progress.filter((q) => q.status === 'error').length

  return (
    <div className="space-y-4">
      {/* ─── Question header ─────────────────────────────────────── */}
      <div className="flex items-start gap-3 border-l-2 border-indigo-500/30 pl-4">
        <div className="shrink-0 w-7 h-7 rounded-lg bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center mt-0.5">
          <span className="text-xs text-indigo-400 font-bold" aria-hidden="true">Q</span>
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-slate-200 leading-relaxed">
            {analysis.question}
          </p>
          <div className="flex flex-wrap items-center gap-2 mt-1.5">
            <span className="text-xs text-slate-500 flex items-center gap-1">
              <Clock className="w-3 h-3" aria-hidden="true" />
              {analysis.startedAt.toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit',
              })}
            </span>
            {intent && <IntentBadge intent={intent} />}
            <StatusPill status={analysis.status} />
            {critique && <QualityBadge critique={critique} />}
          </div>
        </div>
      </div>

      {/* ─── Live agent progress (only while running) ─────────────── */}
      {isRunning && (plan || progress.length > 0) && (
        <div className="ml-7 bg-slate-900/40 border border-slate-700/40 rounded-xl p-3 text-xs space-y-2">
          {plan && (
            <div className="flex items-center gap-2 text-slate-400">
              <ListChecks className="w-3.5 h-3.5 text-indigo-400" aria-hidden="true" />
              <span>
                Plan: <span className="text-slate-200">{plan.title}</span>{' '}
                <span className="text-slate-600">
                  · {plan.query_count} queries · {plan.visual_count} visuals
                </span>
              </span>
            </div>
          )}
          {progress.length > 0 && (
            <div className="flex items-center gap-2 text-slate-400">
              <Activity className="w-3.5 h-3.5 text-indigo-400" aria-hidden="true" />
              <span>
                {queriesDone}/{progress.length} queries done
                {queriesRunning > 0 && (
                  <span className="text-indigo-300 ml-1.5 inline-flex items-center gap-1">
                    <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" />
                    {queriesRunning} running
                  </span>
                )}
                {queriesError > 0 && (
                  <span className="text-red-400 ml-1.5">· {queriesError} failed</span>
                )}
              </span>
            </div>
          )}
        </div>
      )}

      {/* ─── Dashboard canvas (KPIs + charts + tables) ─────────── */}
      {visuals.length > 0 && (
        <div className="ml-7">
          <DashboardCanvas
            title={plan?.title ?? analysis.question}
            subtitle={
              insight?.headline ??
              plan?.description ??
              null
            }
            layout={plan?.layout ?? []}
            visuals={visuals}
          />
        </div>
      )}

      {/* ─── Fallback: old messages that have charts_json but no visuals_json ── */}
      {visuals.length === 0 && analysis.charts.length > 0 && (
        <div
          className={`ml-7 grid gap-4 ${
            analysis.charts.length === 1 ? 'grid-cols-1' : 'grid-cols-1 lg:grid-cols-2'
          }`}
        >
          {analysis.charts.map((chart) => (
            <EChartCard key={chart.id} chart={chart} />
          ))}
        </div>
      )}

      {visuals.length === 0 && analysis.tables.length > 0 && (
        <div className="ml-7 space-y-3">
          {analysis.tables.map((table) => (
            <DataTable key={table.id} table={table} sessionId={activeSessionId ?? undefined} />
          ))}
        </div>
      )}

      {/* ─── Insight section (executive narrative) ──────────────── */}
      {insight && (
        <div className="ml-7">
          <InsightSection insight={insight} critique={critique ?? null} />
        </div>
      )}

      {/* ─── Fallback: old messages with plain text insight only ────── */}
      {!insight && analysis.insight && (
        <div className="ml-7">
          <InsightPanel
            content={analysis.insight}
            isStreaming={false}
            usage={analysis.usage}
          />
        </div>
      )}

      {/* ─── Export bar ───────────────────────────────────────────
        Single row, three buttons. To remove the previous Export/Report
        duplication we keep CSV as a raw-data dump (when ``exportSql`` is
        available) and route the Excel + PDF buttons to the multi-agent
        report generator — which produces a richer file (charts, KPIs,
        insights) than the simple SQL-result export ever did. */}
      {analysis.status === 'done' && activeSessionId && (
        <div className="ml-7 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Export:</span>

          {analysis.exportSql && (
            <ExportButton
              label="CSV"
              onClick={() =>
                api.exportCsv(activeSessionId, analysis.exportSql!, plan?.title || 'export')
              }
            />
          )}

          <ReportButton
            label="Excel"
            loading={reportLoading === 'xlsx'}
            onClick={async () => {
              setReportLoading('xlsx')
              try {
                await api.downloadReport(activeSessionId, analysis.question, 'xlsx', plan?.title)
              } finally {
                setReportLoading(null)
              }
            }}
          />

          <ReportButton
            label="PDF"
            loading={reportLoading === 'pdf'}
            onClick={async () => {
              setReportLoading('pdf')
              try {
                await api.downloadReport(activeSessionId, analysis.question, 'pdf', plan?.title)
              } finally {
                setReportLoading(null)
              }
            }}
          />
        </div>
      )}

      {/* ─── Error state ────────────────────────────────────────── */}
      {analysis.error && (
        <div
          role="alert"
          className="ml-7 flex items-start gap-2 text-xs text-red-400 bg-red-900/20 border border-red-800/40 rounded-xl px-4 py-3"
        >
          <span className="shrink-0 mt-0.5" aria-hidden="true">⚠</span>
          <span>{analysis.error}</span>
        </div>
      )}

      {/* ─── Telemetry strip — bottom-right of every completed card ─── */}
      {analysis.usage && analysis.status === 'done' && (
        <div className="ml-7">
          <UsageBar usage={analysis.usage} />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusPill({ status }: { status: AnalysisResult['status'] }) {
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

function IntentBadge({ intent }: { intent: NonNullable<AnalysisResult['intentInfo']> }) {
  const tone =
    intent.intent === 'dashboard' || intent.intent === 'report'
      ? 'text-indigo-300 bg-indigo-900/20 border-indigo-700/30'
      : 'text-slate-400 bg-slate-800/40 border-slate-700/40'
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-semibold border rounded-full px-2 py-0.5 ${tone}`}
      title={`complexity: ${intent.complexity}`}
    >
      {intent.intent.replace('_', ' ')}
      {intent.wants_export && (
        <span className="text-amber-300 ml-0.5">· {intent.wants_export}</span>
      )}
    </span>
  )
}

/**
 * Quality badge shown next to the status pill when a critic ran.
 *
 * - passed=true  + any resolved error issues → "Quality verified" (emerald)
 * - passed=false                             → "Quality issues"   (amber)
 *
 * When the critique has no issues at all, the badge is suppressed — there is
 * nothing meaningful to surface to the user.
 */
function QualityBadge({ critique }: { critique: CritiqueReport }) {
  const hasErrors = critique.issues.some((i) => i.severity === 'error')

  // Only show the badge when the critic actually flagged something.
  if (!hasErrors && critique.passed) return null

  if (critique.passed) {
    return (
      <span
        className="inline-flex items-center gap-1 text-[10px] text-emerald-300 bg-emerald-900/20 border border-emerald-700/30 rounded-full px-2 py-0.5 font-medium"
        title={`Quality score: ${Math.round(critique.score * 100)}%`}
        aria-label="Quality verified — errors were detected and resolved by the critic"
      >
        <span aria-hidden="true">✓</span>
        Quality verified
      </span>
    )
  }

  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] text-amber-300 bg-amber-900/20 border border-amber-700/30 rounded-full px-2 py-0.5 font-medium"
      title={`Quality score: ${Math.round(critique.score * 100)}% — ${critique.issues.length} issue${critique.issues.length !== 1 ? 's' : ''} flagged`}
      aria-label={`Quality issues detected — ${critique.issues.length} issue${critique.issues.length !== 1 ? 's' : ''} flagged`}
    >
      <span aria-hidden="true">⚠</span>
      Quality issues
    </span>
  )
}

/** Unified export button — single visual style for every format in the
 *  export bar (CSV / Excel / PDF). Pass ``loading`` for the buttons that
 *  re-run the slow report pipeline (Excel / PDF); CSV passes nothing
 *  since the SQL export is fast and doesn't need a spinner. */
function ExportButton({
  label,
  loading = false,
  onClick,
}: {
  label:    string
  loading?: boolean
  onClick:  () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className="
        inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md
        border border-indigo-600/50 bg-indigo-900/20 text-indigo-300
        hover:border-indigo-400/60 hover:bg-indigo-900/40 hover:text-indigo-200
        disabled:opacity-50 disabled:cursor-wait
        transition-colors
      "
    >
      {loading
        ? <Loader2  className="w-3 h-3 animate-spin" aria-hidden="true" />
        : <FileDown className="w-3 h-3" aria-hidden="true" />}
      {label}
    </button>
  )
}

// `ReportButton` is now an alias of `ExportButton` so the existing call
// sites keep working with no further refactor.
const ReportButton = ExportButton
