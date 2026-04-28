import ReactECharts from 'echarts-for-react'
import { useMemo } from 'react'
import { ChartResult } from '../types'
import { useResolvedTheme } from '../hooks/useTheme'
import { applyThemeToChartOption, chartCardClasses } from '../lib/echartsTheme'

interface Props {
  chart: ChartResult
}

export function EChartCard({ chart }: Props) {
  const theme  = useResolvedTheme()
  const option = useMemo(
    () => applyThemeToChartOption(chart.option, theme),
    [chart.option, theme],
  )

  return (
    <div className={`rounded-xl overflow-hidden ${chartCardClasses(theme)}`}>
      <div className="px-4 pt-3 pb-1 flex items-center justify-between">
        <span
          className={`text-xs font-medium truncate ${
            theme === 'light' ? 'text-slate-700' : 'text-slate-300'
          }`}
        >
          {chart.title || 'Chart'}
        </span>
        <span
          className={`text-xs ml-2 shrink-0 ${
            theme === 'light' ? 'text-slate-400' : 'text-slate-600'
          }`}
        >
          interactive
        </span>
      </div>
      <ReactECharts
        // ECharts v5 honours per-render theme keys to fully reset its
        // internal state between dark/light flips. Combined with notMerge
        // we get a clean repaint whenever the resolved theme changes.
        key={theme}
        option={option}
        style={{ height: 320 }}
        opts={{ renderer: 'canvas' }}
        notMerge
        lazyUpdate
      />
    </div>
  )
}
