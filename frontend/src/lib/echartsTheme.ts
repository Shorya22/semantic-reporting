/**
 * Theme-aware ECharts option post-processor.
 *
 * The backend emits ECharts options pre-styled for the dark canvas
 * (``backgroundColor: "#0a0f1e"``, light text, dim grid lines, indigo
 * pointer ring). When the app runs in light mode those options leave
 * the chart looking like a black box floating on white.
 *
 * This module deep-clones the option and selectively re-skins it for
 * the active theme without touching the data. The dark theme returns
 * the option as-is (after a clone) so the existing visual style is
 * preserved.
 *
 * Re-skinned surfaces (light theme):
 *   • chart background → transparent (the wrapper's bg shows through)
 *   • title / tooltip / legend / axis text → slate-700/600/500 family
 *   • grid + split lines → slate-200
 *   • axis lines / ticks → slate-300
 *   • tooltip box → white with slate-200 border
 *   • toolbox icons → indigo-600
 *   • per-series ``borderColor: "#0a0f1e"`` → white (so segment seams
 *     in pie/treemap/sunburst stay visible against the new bg)
 *   • visualMap / calendar / radar chrome → light variants
 *   • candlestick bullish / bearish keep their semantic colors
 */

type Opt = Record<string, unknown> & { [key: string]: any }

const LIGHT = {
  bgTransparent: 'transparent',
  textPrimary:   '#0f172a',  // slate-900
  textBody:      '#1e293b',  // slate-800
  textMuted:     '#475569',  // slate-600
  textFaint:     '#64748b',  // slate-500
  axisLine:      '#cbd5e1',  // slate-300
  splitLine:     '#e2e8f0',  // slate-200
  tooltipBg:     '#ffffff',
  tooltipBorder: '#e2e8f0',
  segmentBorder: '#ffffff',  // pie/treemap segment seams
  pointerShadow: 'rgba(99,102,241,0.06)',
  toolboxIcon:   '#4f46e5',
  radarBg:       ['#f8fafc', '#ffffff'],
}


export function applyThemeToChartOption(option: unknown, theme: 'dark' | 'light'): unknown {
  if (!option || typeof option !== 'object') return option
  // Deep clone so we never mutate the upstream option (the analysis card
  // re-renders with the same option ref).
  const cloned: Opt = JSON.parse(JSON.stringify(option))

  // The card wrapper already shows the chart title + a type badge. The
  // chart-internal title is therefore visually duplicated AND fights the
  // toolbox icons for top-row real estate. Hide it and reclaim that space
  // for the actual data — exports go through the backend and embed their
  // own labelled image, so we don't lose anything for download flows.
  if (cloned.title) {
    cloned.title.show = false
    cloned.title.text = ''
  }

  // With the title gone, the default ``grid.top: 18%`` is far too generous.
  // Tighten the top margin (we only need room for the toolbox row) AND give
  // the grid more breathing space on the right so rotated date labels
  // ("2026-04-01") don't clip at the edge of narrow charts.
  if (cloned.grid && !Array.isArray(cloned.grid)) {
    cloned.grid.top   = 28
    cloned.grid.right = '5%'
  }

  // Move the toolbox slightly inset so it stays out of the chart's
  // drawing area but doesn't crowd against the rounded card border.
  if (cloned.toolbox) {
    cloned.toolbox.right = 10
    cloned.toolbox.top   = 6
    cloned.toolbox.itemSize = 12      // smaller, less visually shouty
    cloned.toolbox.itemGap  = 8
  }

  if (theme === 'dark') return cloned
  return _applyLight(cloned)
}


function _applyLight(o: Opt): Opt {
  // Let the wrapping card's bg shine through.
  o.backgroundColor = LIGHT.bgTransparent

  // Title -----------------------------------------------------------------
  if (o.title?.textStyle) o.title.textStyle.color = LIGHT.textPrimary

  // Tooltip ---------------------------------------------------------------
  if (o.tooltip) {
    o.tooltip.backgroundColor = LIGHT.tooltipBg
    o.tooltip.borderColor     = LIGHT.tooltipBorder
    if (o.tooltip.textStyle) o.tooltip.textStyle.color = LIGHT.textBody
    if (o.tooltip.axisPointer?.shadowStyle) {
      o.tooltip.axisPointer.shadowStyle.color = LIGHT.pointerShadow
    }
  }

  // Legend ----------------------------------------------------------------
  if (o.legend) {
    o.legend.backgroundColor = LIGHT.bgTransparent
    if (o.legend.textStyle) o.legend.textStyle.color = LIGHT.textMuted
    o.legend.borderColor = LIGHT.tooltipBorder
  }

  // Axes (xAxis / yAxis can each be array or single object) ---------------
  for (const axisKey of ['xAxis', 'yAxis']) {
    const node = o[axisKey]
    if (!node) continue
    const axes = Array.isArray(node) ? node : [node]
    for (const a of axes) {
      if (!a) continue
      if (a.axisLine?.lineStyle)  a.axisLine.lineStyle.color  = LIGHT.axisLine
      if (a.axisTick?.lineStyle)  a.axisTick.lineStyle.color  = LIGHT.axisLine
      if (a.axisLabel)            a.axisLabel.color           = LIGHT.textMuted
      if (a.splitLine?.lineStyle) a.splitLine.lineStyle.color = LIGHT.splitLine
      if (a.nameTextStyle)        a.nameTextStyle.color       = LIGHT.textMuted
    }
  }

  // Toolbox icons ---------------------------------------------------------
  if (o.toolbox?.feature) {
    for (const f of Object.values(o.toolbox.feature)) {
      const feat = f as { iconStyle?: { borderColor?: string } } | null
      if (feat?.iconStyle) feat.iconStyle.borderColor = LIGHT.toolboxIcon
    }
  }

  // visualMap (heatmap, calendar) ----------------------------------------
  if (o.visualMap) {
    if (o.visualMap.textStyle) o.visualMap.textStyle.color = LIGHT.textMuted
  }

  // Calendar (calendar_heatmap) ------------------------------------------
  if (o.calendar) {
    if (o.calendar.itemStyle) {
      o.calendar.itemStyle.borderColor = LIGHT.segmentBorder
      o.calendar.itemStyle.color       = '#f1f5f9'  // slate-100
    }
    if (o.calendar.yearLabel)  o.calendar.yearLabel.color  = LIGHT.textPrimary
    if (o.calendar.monthLabel) o.calendar.monthLabel.color = LIGHT.textMuted
    if (o.calendar.dayLabel)   o.calendar.dayLabel.color   = LIGHT.textMuted
  }

  // Radar -----------------------------------------------------------------
  if (o.radar) {
    if (o.radar.axisName)        o.radar.axisName.color           = LIGHT.textPrimary
    if (o.radar.splitLine?.lineStyle) o.radar.splitLine.lineStyle.color = LIGHT.splitLine
    if (o.radar.axisLine?.lineStyle)  o.radar.axisLine.lineStyle.color  = LIGHT.splitLine
    if (o.radar.splitArea?.areaStyle) o.radar.splitArea.areaStyle.color = LIGHT.radarBg
  }

  // Series chrome ---------------------------------------------------------
  if (Array.isArray(o.series)) {
    for (const s of o.series as Opt[]) {
      // Per-segment seams (pie, donut, treemap, sunburst, calendar)
      if (s.itemStyle?.borderColor === '#0a0f1e') s.itemStyle.borderColor = LIGHT.segmentBorder
      if (s.itemStyle?.borderColor === '#0f1629') s.itemStyle.borderColor = LIGHT.segmentBorder
      // Top-of-bar / inside labels
      if (s.label?.color === '#94a3b8') s.label.color = LIGHT.textMuted
      if (s.label?.color === '#e2e8f0') s.label.color = LIGHT.textBody
      // Funnel "inside" label was white — keep it white (still on indigo bar).
      // labelLine for pie
      if (s.labelLine?.lineStyle?.color === '#475569') s.labelLine.lineStyle.color = LIGHT.axisLine
      // Pie / donut item color is per-segment (in `data` entries) — leave alone.

      // Line / area emphasis dot border (was #0a0f1e to blend with chart bg)
      if (s.itemStyle?.borderColor === '#0a0f1e') s.itemStyle.borderColor = LIGHT.segmentBorder

      // Sankey "data" (nodes) keep their colors; gradient links stay too.
    }
  }

  return o
}


/** Pick a wrapper background that pairs with the re-skinned canvas. */
export function chartCardClasses(theme: 'dark' | 'light'): string {
  return theme === 'light'
    ? 'bg-white border border-slate-200 shadow-sm'
    : 'bg-slate-900/80 border border-slate-700/50'
}
