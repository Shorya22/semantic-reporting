import { useState, useRef, DragEvent } from 'react'
import { HardDrive, Server, FileText, Upload, Loader2, Zap } from 'lucide-react'
import { api } from '../api/client'
import { useStore } from '../store'
import { Session } from '../types'

type Tab = 'sqlite' | 'postgres' | 'file'

interface Props {
  onConnected?: () => void
}

export function ConnectionPanel({ onConnected }: Props) {
  const [tab, setTab] = useState<Tab>('sqlite')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const { addSession } = useStore()

  const [sqlitePath, setSqlitePath] = useState('')
  const [pg, setPg] = useState({ host: 'localhost', port: 5432, database: '', user: '', password: '' })

  async function connect(fn: () => Promise<Session & { session_id: string }>) {
    setError('')
    setLoading(true)
    try {
      const data = await fn()
      addSession({ ...data, session_id: data.session_id })
      setSqlitePath('')
      setPg({ host: 'localhost', port: 5432, database: '', user: '', password: '' })
      onConnected?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Connection failed')
    } finally {
      setLoading(false)
    }
  }

  async function handleFile(file: File) {
    setError('')
    setLoading(true)
    try {
      const data = await api.uploadFile(file)
      addSession({ ...data, session_id: data.session_id })
      onConnected?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setLoading(false)
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  const tabs: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: 'sqlite',   label: 'SQLite',   icon: <HardDrive className="w-3 h-3" aria-hidden="true" /> },
    { key: 'postgres', label: 'Postgres', icon: <Server    className="w-3 h-3" aria-hidden="true" /> },
    { key: 'file',     label: 'CSV/XLS',  icon: <FileText  className="w-3 h-3" aria-hidden="true" /> },
  ]

  const inputCls = 'w-full bg-[#0c1120] border border-slate-700/60 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:ring-1 focus:ring-indigo-500/50 focus:border-indigo-500/50 transition-colors'

  return (
    <div className="space-y-2.5">
      {/* Tab switcher */}
      <div className="flex gap-0.5 bg-[#0c1120] rounded-lg p-0.5 border border-slate-800/60" role="tablist" aria-label="Connection type">
        {tabs.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => { setTab(t.key); setError('') }}
            className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-md text-xs font-medium transition-all ${
              tab === t.key
                ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* SQLite tab */}
      {tab === 'sqlite' && (
        <div className="space-y-2" role="tabpanel">
          <label htmlFor="sqlite-path" className="sr-only">SQLite database path</label>
          <input
            id="sqlite-path"
            type="text"
            placeholder="/path/to/database.db"
            value={sqlitePath}
            onChange={(e) => setSqlitePath(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && connect(() => api.connectSQLite(sqlitePath))}
            className={inputCls}
          />
          <button
            onClick={() => connect(() => api.connectSQLite(sqlitePath))}
            disabled={!sqlitePath.trim() || loading}
            className="w-full bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 disabled:opacity-40 disabled:cursor-not-allowed text-white text-xs font-medium py-2 rounded-lg transition-all flex items-center justify-center gap-1.5 shadow-sm"
          >
            {loading ? <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" /> : <Zap className="w-3 h-3" aria-hidden="true" />}
            Connect
          </button>
        </div>
      )}

      {/* Postgres tab */}
      {tab === 'postgres' && (
        <div className="space-y-2" role="tabpanel">
          <div className="grid grid-cols-3 gap-1.5">
            <label htmlFor="pg-host" className="sr-only">Host</label>
            <input
              id="pg-host"
              placeholder="Host"
              value={pg.host}
              onChange={(e) => setPg({ ...pg, host: e.target.value })}
              className={`col-span-2 ${inputCls}`}
            />
            <label htmlFor="pg-port" className="sr-only">Port</label>
            <input
              id="pg-port"
              placeholder="Port"
              type="number"
              value={pg.port}
              onChange={(e) => setPg({ ...pg, port: Number(e.target.value) })}
              className={inputCls}
            />
          </div>
          {(['database', 'user', 'password'] as const).map((f) => (
            <div key={f}>
              <label htmlFor={`pg-${f}`} className="sr-only">{f.charAt(0).toUpperCase() + f.slice(1)}</label>
              <input
                id={`pg-${f}`}
                type={f === 'password' ? 'password' : 'text'}
                placeholder={f.charAt(0).toUpperCase() + f.slice(1)}
                value={pg[f] as string}
                onChange={(e) => setPg({ ...pg, [f]: e.target.value })}
                className={inputCls}
              />
            </div>
          ))}
          <button
            onClick={() => connect(() => api.connectPostgres(pg))}
            disabled={!pg.database || !pg.user || loading}
            className="w-full bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 disabled:opacity-40 disabled:cursor-not-allowed text-white text-xs font-medium py-2 rounded-lg transition-all flex items-center justify-center gap-1.5"
          >
            {loading ? <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" /> : <Zap className="w-3 h-3" aria-hidden="true" />}
            Connect
          </button>
        </div>
      )}

      {/* File upload tab */}
      {tab === 'file' && (
        <div role="tabpanel">
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
            role="button"
            tabIndex={0}
            aria-label="Upload CSV or Excel file"
            onKeyDown={(e) => e.key === 'Enter' && fileRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-4 text-center cursor-pointer transition-all ${
              dragging ? 'border-indigo-500 bg-indigo-500/5' : 'border-slate-700/60 hover:border-slate-600'
            }`}
          >
            {loading
              ? <Loader2 className="w-5 h-5 animate-spin text-indigo-400 mx-auto mb-1.5" aria-hidden="true" />
              : <Upload className="w-5 h-5 text-slate-600 mx-auto mb-1.5" aria-hidden="true" />}
            <p className="text-xs text-slate-400">{loading ? 'Uploading…' : 'Drop CSV or Excel file'}</p>
            <p className="text-xs text-slate-600 mt-0.5">.csv · .xlsx · .xls</p>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.xlsx,.xls"
            className="hidden"
            aria-label="File upload input"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
          />
        </div>
      )}

      {error && (
        <p role="alert" className="text-xs text-red-400 bg-red-900/20 border border-red-800/40 rounded-lg px-3 py-2">
          {error}
        </p>
      )}
    </div>
  )
}
