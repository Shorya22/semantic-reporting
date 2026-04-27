import { useState } from 'react'
import { Download, FileText, FileSpreadsheet } from 'lucide-react'
import { TableResult } from '../types'
import { api } from '../api/client'

interface Props {
  table: TableResult
  sessionId?: string
}

export function DataTable({ table, sessionId }: Props) {
  const [sortCol, setSortCol] = useState<number | null>(null)
  const [sortAsc, setSortAsc] = useState(true)
  const [exporting, setExporting] = useState<string | null>(null)

  const handleSort = (i: number) => {
    if (sortCol === i) setSortAsc((v) => !v)
    else {
      setSortCol(i)
      setSortAsc(true)
    }
  }

  const sorted =
    sortCol === null
      ? table.rows
      : [...table.rows].sort((a, b) => {
          const av = a[sortCol]
          const bv = b[sortCol]
          const toNum = (v: unknown) => Number(v)
          if (!isNaN(toNum(av)) && !isNaN(toNum(bv))) {
            return sortAsc ? toNum(av) - toNum(bv) : toNum(bv) - toNum(av)
          }
          return sortAsc
            ? String(av).localeCompare(String(bv))
            : String(bv).localeCompare(String(av))
        })

  const doExport = async (fmt: 'csv' | 'excel' | 'pdf') => {
    if (!sessionId || exporting) return
    setExporting(fmt)
    try {
      const title = table.title || 'export'
      if (fmt === 'csv') await api.exportCsv(sessionId, table.sql, title)
      if (fmt === 'excel') await api.exportExcel(sessionId, table.sql, title)
      if (fmt === 'pdf') await api.exportPdf(sessionId, table.sql, title)
    } catch (e) {
      console.error('Export error:', e)
    } finally {
      setExporting(null)
    }
  }

  return (
    <div className="bg-slate-900/80 border border-slate-700/50 rounded-xl overflow-hidden">
      <div className="px-4 py-2.5 flex items-center justify-between border-b border-slate-800">
        <span className="text-xs font-medium text-slate-300">
          {table.title || 'Query Result'}
        </span>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-slate-600">{table.rows.length} rows</span>
          {sessionId && (
            <>
              <button
                onClick={() => doExport('csv')}
                disabled={!!exporting}
                aria-label="Export as CSV"
                className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-emerald-300 transition-colors disabled:opacity-50"
              >
                <FileText className="w-3 h-3" />
                {exporting === 'csv' ? '…' : 'CSV'}
              </button>
              <button
                onClick={() => doExport('excel')}
                disabled={!!exporting}
                aria-label="Export as Excel"
                className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-emerald-300 transition-colors disabled:opacity-50"
              >
                <FileSpreadsheet className="w-3 h-3" />
                {exporting === 'excel' ? '…' : 'Excel'}
              </button>
              <button
                onClick={() => doExport('pdf')}
                disabled={!!exporting}
                aria-label="Export as PDF"
                className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-rose-300 transition-colors disabled:opacity-50"
              >
                <Download className="w-3 h-3" />
                {exporting === 'pdf' ? '…' : 'PDF'}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="overflow-x-auto max-h-64 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 z-10">
            <tr className="bg-slate-800/90">
              {table.columns.map((col, i) => (
                <th
                  key={i}
                  onClick={() => handleSort(i)}
                  scope="col"
                  className="px-3 py-2 text-left text-slate-300 font-medium whitespace-nowrap cursor-pointer hover:text-indigo-300 select-none"
                >
                  {col}
                  {sortCol === i && (
                    <span className="ml-1 text-indigo-400">{sortAsc ? '↑' : '↓'}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.slice(0, 100).map((row, ri) => (
              <tr
                key={ri}
                className="border-t border-slate-800/50 hover:bg-slate-800/30 transition-colors"
              >
                {(row as unknown[]).map((cell, ci) => (
                  <td
                    key={ci}
                    className="px-3 py-1.5 text-slate-300 whitespace-nowrap font-mono"
                  >
                    {String(cell ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>

        {table.rows.length > 100 && (
          <div className="px-3 py-2 text-xs text-slate-500 border-t border-slate-800">
            Showing 100 of {table.rows.length} rows
          </div>
        )}
      </div>
    </div>
  )
}
