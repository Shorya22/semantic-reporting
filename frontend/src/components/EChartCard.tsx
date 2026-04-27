import ReactECharts from 'echarts-for-react'
import { ChartResult } from '../types'

interface Props {
  chart: ChartResult
}

export function EChartCard({ chart }: Props) {
  return (
    <div className="bg-slate-900/80 border border-slate-700/50 rounded-xl overflow-hidden">
      <div className="px-4 pt-3 pb-1 flex items-center justify-between">
        <span className="text-xs font-medium text-slate-300 truncate">
          {chart.title || 'Chart'}
        </span>
        <span className="text-xs text-slate-600 ml-2 shrink-0">interactive</span>
      </div>
      <ReactECharts
        option={chart.option}
        style={{ height: 320 }}
        theme="dark"
        opts={{ renderer: 'canvas' }}
        notMerge
        lazyUpdate
      />
    </div>
  )
}
