import ReactECharts from 'echarts-for-react'
import { TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { KPIPayload } from '../types'

interface Props {
  kpi: KPIPayload
  /** Optional title override (defaults to kpi.label). */
  title?: string
  /** Optional subtitle (e.g. time window). */
  subtitle?: string | null
  /** Optional comparison number to compute a delta percentage from. */
  previousValue?: number | null
  /** Compact mode: smaller padding for dense KPI strips. */
  compact?: boolean
}

/**
 * Big-number metric tile. Mirrors the look of modern BI dashboards
 * (Looker, Tableau, Numerics) — gradient ring, animated count, optional
 * delta vs prior period, optional inline sparkline.
 *
 * Render shape (when ``kpi.sparkline`` is non-empty):
 *
 *   ┌────────────────────────────────────┐
 *   │ TOTAL TRANSACTIONS    last 30 days │
 *   │                                    │
 *   │  ↑ 12.4%      ╱╲╱─                 │
 *   │              ╱     ╲╱╲             │
 *   │  4.5M                              │
 *   │                                    │
 *   └────────────────────────────────────┘
 */
export function KPICard({ kpi, title, subtitle, previousValue, compact = false }: Props) {
  const display = kpi.formatted_value || '—'
  const label = title ?? kpi.label

  // Optional delta vs previous period
  let deltaPct: number | null = null
  let deltaUp: boolean | null = null
  if (
    previousValue != null &&
    previousValue !== 0 &&
    typeof kpi.value === 'number'
  ) {
    deltaPct = ((kpi.value - previousValue) / Math.abs(previousValue)) * 100
    deltaUp = deltaPct > 0
  }

  const padY = compact ? 'py-3' : 'py-4'
  const padX = compact ? 'px-4' : 'px-5'
  const valueSize = compact ? 'text-2xl' : 'text-3xl'

  return (
    <div
      className={`relative h-full bg-gradient-to-br from-slate-900/80 to-slate-900/40 border border-slate-700/50 rounded-xl ${padX} ${padY} overflow-hidden flex flex-col justify-between transition-all hover:border-indigo-500/40 hover:shadow-[0_0_20px_rgba(99,102,241,0.06)]`}
    >
      {/* Soft accent ring */}
      <div
        aria-hidden="true"
        className="absolute -top-12 -right-12 w-32 h-32 rounded-full bg-indigo-500/5 blur-2xl pointer-events-none"
      />

      {/* Header row */}
      <div className="flex items-start justify-between gap-3 relative z-10 min-w-0">
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold truncate">
            {label}
          </div>
          {subtitle && (
            <div className="text-[10px] text-slate-600 mt-0.5 truncate">{subtitle}</div>
          )}
        </div>
        {deltaPct != null && (
          <span
            className={`inline-flex items-center gap-0.5 text-[10px] font-semibold px-1.5 py-0.5 rounded-md shrink-0 ${
              deltaUp
                ? 'text-emerald-300 bg-emerald-900/30 border border-emerald-700/30'
                : 'text-red-300 bg-red-900/30 border border-red-700/30'
            }`}
          >
            {deltaUp ? (
              <TrendingUp className="w-3 h-3" aria-hidden="true" />
            ) : deltaPct === 0 ? (
              <Minus className="w-3 h-3" aria-hidden="true" />
            ) : (
              <TrendingDown className="w-3 h-3" aria-hidden="true" />
            )}
            {deltaPct >= 0 ? '+' : ''}
            {deltaPct.toFixed(1)}%
          </span>
        )}
      </div>

      {/* Value + sparkline row */}
      <div className="flex items-end justify-between gap-3 mt-3 relative z-10">
        <div
          className={`${valueSize} font-bold leading-none bg-gradient-to-br from-slate-100 to-slate-300 bg-clip-text text-transparent truncate`}
          title={display}
        >
          {display}
        </div>
        {kpi.sparkline && kpi.sparkline.length > 1 && (
          <div className="w-20 h-8 shrink-0 opacity-80">
            <ReactECharts
              option={sparklineOption(kpi.sparkline)}
              style={{ height: 32, width: 80 }}
              theme="dark"
              opts={{ renderer: 'canvas' }}
              notMerge
              lazyUpdate
            />
          </div>
        )}
      </div>

      {kpi.unit && kpi.unit.length > 3 && (
        <div className="text-[10px] text-slate-600 mt-1 relative z-10">{kpi.unit}</div>
      )}
    </div>
  )
}

/** Minimal inline-sparkline option — no axes, no tooltip, no grid. */
function sparklineOption(values: number[]): Record<string, unknown> {
  return {
    backgroundColor: 'transparent',
    grid: { left: 0, right: 0, top: 2, bottom: 2 },
    xAxis: { type: 'category', show: false, data: values.map((_, i) => i) },
    yAxis: { type: 'value', show: false },
    series: [
      {
        type: 'line',
        data: values,
        showSymbol: false,
        smooth: true,
        lineStyle: { color: '#6366f1', width: 1.8 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(99,102,241,0.35)' },
              { offset: 1, color: 'rgba(99,102,241,0.02)' },
            ],
          },
        },
      },
    ],
  }
}
