import ReactECharts from 'echarts-for-react'
import { useMemo } from 'react'
import { LayoutRow, RenderedVisual } from '../types'
import { KPICard } from './KPICard'
import { useResolvedTheme } from '../hooks/useTheme'
import { applyThemeToChartOption, chartCardClasses } from '../lib/echartsTheme'

interface Props {
  /** Dashboard title (from PlanInfo.title). */
  title: string
  /** Optional subtitle (typically PlanInfo.description or InsightReport.headline). */
  subtitle?: string | null
  /** Layout rows produced by the Planner. */
  layout: LayoutRow[]
  /** All rendered visuals; looked up by visual_id from the layout slots. */
  visuals: RenderedVisual[]
}

/**
 * Modern BI-style dashboard renderer.
 *
 * Each ``LayoutRow`` becomes a CSS-grid row of 12 columns. Each ``LayoutSlot``
 * spans ``slot.width`` columns. Visuals fill their slot — KPI tiles are
 * compact, charts are 320px tall, tables scroll vertically.
 *
 * Falls back to "auto-grid" if the layout is empty: KPIs in a top strip, charts
 * 2-up, tables full-width.
 */
export function DashboardCanvas({ title, subtitle, layout, visuals }: Props) {
  const byId = new Map(visuals.map((v) => [v.visual_id, v]))
  const rows = layout.length
    ? layout
    : autoLayout(visuals)

  return (
    <section
      aria-label={title || 'Dashboard'}
      className="space-y-4"
    >
      {/* Header */}
      {(title || subtitle) && (
        <header className="flex items-baseline justify-between gap-4 pb-2 border-b border-slate-800/60">
          <div className="min-w-0">
            {title && (
              <h2 className="text-lg font-semibold text-slate-100 truncate">
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="text-xs text-slate-400 mt-0.5 line-clamp-2">{subtitle}</p>
            )}
          </div>
          <span className="shrink-0 text-[10px] uppercase tracking-wider text-indigo-400 bg-indigo-900/20 border border-indigo-700/40 rounded-full px-2 py-0.5 font-semibold">
            Dashboard
          </span>
        </header>
      )}

      {/* Grid rows */}
      <div className="space-y-4">
        {rows.map((row, ri) => (
          <div
            key={ri}
            className="grid grid-cols-12 gap-4"
          >
            {row.slots.map((slot) => {
              const v = byId.get(slot.visual_id)
              if (!v) return null
              return (
                <div
                  key={slot.visual_id}
                  className={colSpan(slot.width)}
                >
                  <VisualSlot visual={v} />
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Per-visual renderer
// ---------------------------------------------------------------------------

function VisualSlot({ visual }: { visual: RenderedVisual }) {
  if (visual.error) {
    return (
      <div
        role="alert"
        className="h-full bg-slate-900/40 border border-red-800/40 rounded-xl p-4 text-xs text-red-300 flex items-center justify-center text-center"
      >
        <div>
          <div className="font-semibold mb-1">{visual.title}</div>
          <div className="text-red-400/70">{visual.error}</div>
        </div>
      </div>
    )
  }

  // KPI tile
  if (visual.visual_type === 'kpi' && visual.kpi) {
    return (
      <KPICard
        kpi={visual.kpi}
        title={visual.title}
        subtitle={visual.subtitle ?? null}
        compact
      />
    )
  }

  // Tabular drill-down
  if (visual.visual_type === 'table' && visual.table_rows.length > 0) {
    return <DashboardTable visual={visual} />
  }

  // Chart (any echarts visual_type)
  if (visual.echarts_option) {
    return <ChartSlot visual={visual} />
  }

  // Fallback — empty visual (no error, no payload)
  return (
    <div className="h-full bg-slate-900/40 border border-slate-800/60 rounded-xl p-4 text-xs text-slate-500 flex items-center justify-center">
      No data
    </div>
  )
}

// ---------------------------------------------------------------------------
// Chart slot — theme-aware ECharts wrapper used inside the dashboard grid.
// The container has a *fixed* height so cards always line up evenly across
// rows (different chart types would otherwise compute different intrinsic
// heights and the grid would look ragged). echarts-for-react's built-in
// ResizeObserver handles width changes when the sidebar collapses.
// ---------------------------------------------------------------------------

function ChartSlot({ visual }: { visual: RenderedVisual }) {
  const theme  = useResolvedTheme()
  const option = useMemo(
    () => applyThemeToChartOption(visual.echarts_option, theme),
    [visual.echarts_option, theme],
  )

  return (
    <div className={`h-full rounded-xl overflow-hidden flex flex-col ${chartCardClasses(theme)}`}>
      <div className="px-4 pt-3 pb-1 flex items-baseline justify-between gap-2">
        <div className="min-w-0">
          <div className={`text-xs font-semibold truncate ${theme === 'light' ? 'text-slate-700' : 'text-slate-200'}`}>
            {visual.title}
          </div>
          {visual.subtitle && (
            <div className={`text-[10px] truncate ${theme === 'light' ? 'text-slate-500' : 'text-slate-500'}`}>
              {visual.subtitle}
            </div>
          )}
        </div>
        <span className={`shrink-0 text-[10px] uppercase tracking-wider ${theme === 'light' ? 'text-slate-400' : 'text-slate-600'}`}>
          {visual.visual_type.replace('_', ' ')}
        </span>
      </div>
      <div className="flex-1 min-h-[340px]">
        <ReactECharts
          key={theme}                              // force a clean repaint on theme flip
          option={option}
          style={{ height: '100%', width: '100%', minHeight: 340 }}
          opts={{ renderer: 'canvas' }}
          notMerge
          lazyUpdate
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Compact data table (used inside dashboards)
// ---------------------------------------------------------------------------

function DashboardTable({ visual }: { visual: RenderedVisual }) {
  return (
    <div className="h-full bg-slate-900/80 border border-slate-700/50 rounded-xl overflow-hidden flex flex-col">
      <div className="px-4 pt-3 pb-2 flex items-baseline justify-between gap-2 border-b border-slate-800/60">
        <div className="text-xs font-semibold text-slate-200 truncate">{visual.title}</div>
        <span className="shrink-0 text-[10px] text-slate-500">
          {visual.rows_count.toLocaleString()} rows
        </span>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-900/95 backdrop-blur z-10">
            <tr>
              {visual.table_columns.map((c) => (
                <th
                  key={c}
                  className="text-left px-3 py-2 text-[10px] uppercase tracking-wider text-slate-400 font-semibold border-b border-slate-700/60"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visual.table_rows.slice(0, 100).map((row, ri) => (
              <tr key={ri} className="hover:bg-slate-800/40 transition-colors">
                {(row as unknown[]).map((cell, ci) => (
                  <td
                    key={ci}
                    className="px-3 py-1.5 text-slate-300 border-b border-slate-800/40 truncate max-w-[260px]"
                  >
                    {formatCell(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return v.toLocaleString()
  return String(v)
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Map a 1-12 width to a Tailwind col-span class — must be a literal string
 *  so the JIT picks it up. We always start full-width on mobile, then split
 *  at the md breakpoint. */
const COL_SPAN_MAP: Record<number, string> = {
  1:  'col-span-12 md:col-span-1',
  2:  'col-span-12 md:col-span-2',
  3:  'col-span-12 md:col-span-3',
  4:  'col-span-12 md:col-span-4',
  5:  'col-span-12 md:col-span-5',
  6:  'col-span-12 md:col-span-6',
  7:  'col-span-12 md:col-span-7',
  8:  'col-span-12 md:col-span-8',
  9:  'col-span-12 md:col-span-9',
  10: 'col-span-12 md:col-span-10',
  11: 'col-span-12 md:col-span-11',
  12: 'col-span-12',
}
function colSpan(width: number): string {
  const w = Math.max(1, Math.min(12, width || 12))
  return COL_SPAN_MAP[w]
}

/** Build a sensible default layout when the planner emits none:
 *    row 1: KPIs (4 per row max, width 3 each)
 *    rows 2..N: charts in 2-up grid (width 6 each)
 *    final rows: tables full width (width 12) */
function autoLayout(visuals: RenderedVisual[]): LayoutRow[] {
  const rows: LayoutRow[] = []
  const kpis = visuals.filter((v) => v.visual_type === 'kpi')
  const charts = visuals.filter((v) => v.visual_type !== 'kpi' && v.visual_type !== 'table')
  const tables = visuals.filter((v) => v.visual_type === 'table')

  if (kpis.length > 0) {
    const w = kpis.length >= 4 ? 3 : kpis.length === 3 ? 4 : kpis.length === 2 ? 6 : 12
    for (let i = 0; i < kpis.length; i += 4) {
      rows.push({
        slots: kpis.slice(i, i + 4).map((v) => ({ visual_id: v.visual_id, width: w })),
      })
    }
  }
  for (let i = 0; i < charts.length; i += 2) {
    const pair = charts.slice(i, i + 2)
    if (pair.length === 1) {
      rows.push({ slots: [{ visual_id: pair[0].visual_id, width: 12 }] })
    } else {
      rows.push({
        slots: [
          { visual_id: pair[0].visual_id, width: 6 },
          { visual_id: pair[1].visual_id, width: 6 },
        ],
      })
    }
  }
  for (const t of tables) {
    rows.push({ slots: [{ visual_id: t.visual_id, width: 12 }] })
  }
  return rows
}
