import { useEffect, useMemo } from 'react'
import { BarChart3, ChevronDown, Cloud, Cpu } from 'lucide-react'
import { GROQ_MODELS, OLLAMA_FALLBACK_MODELS, LlmProvider } from '../types'
import { useStore } from '../store'
import { api } from '../api/client'


export function Header() {
  const {
    model, setModel,
    provider, setProvider,
    ollamaModels, setOllamaModels,
    activeSessionId, sessions,
  } = useStore()

  const hydrated = useStore((s) => s.hydrated)
  const active   = sessions.find((s) => s.session_id === activeSessionId)
  const isOnline = Boolean(active)

  // Seed defaults from the server only on the very first run, before
  // hydration completes. Once hydrated, persisted user choices win.
  useEffect(() => {
    if (hydrated) return
    api.getConfig()
      .then(({ default_model, llm_provider }) => {
        const s = useStore.getState()
        if (s.model === 'llama-3.3-70b-versatile' && s.provider === 'groq') {
          setProvider(llm_provider as LlmProvider)
          setModel(default_model)
        }
      })
      .catch(() => {})
  }, [hydrated, setModel, setProvider])

  // Refresh Ollama model list whenever the provider switches to Ollama.
  useEffect(() => {
    if (provider !== 'ollama') return
    api.getOllamaModels()
      .then((models) => {
        if (models.length > 0) {
          setOllamaModels(models)
          if (!models.some((m) => m.id === model)) setModel(models[0].id)
        } else {
          setOllamaModels(OLLAMA_FALLBACK_MODELS)
          setModel(OLLAMA_FALLBACK_MODELS[0].id)
        }
      })
      .catch(() => setOllamaModels(OLLAMA_FALLBACK_MODELS))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider])

  const handleProviderChange = (next: LlmProvider) => {
    if (next === provider) return
    setProvider(next)
    if (next === 'groq') setModel(GROQ_MODELS[0].id)
  }

  // Compose the model-options list. Always include the currently-selected
  // model id even if it isn't in the canonical list yet (e.g. a custom
  // Ollama tag that hasn't been re-fetched), so the <select> never resets.
  const modelOptions = useMemo(() => {
    const list = provider === 'ollama'
      ? (ollamaModels.length > 0 ? ollamaModels : OLLAMA_FALLBACK_MODELS)
      : GROQ_MODELS
    return list.some((m) => m.id === model) ? list : [{ id: model, label: model }, ...list]
  }, [model, provider, ollamaModels])

  const activeModelLabel = modelOptions.find((m) => m.id === model)?.label ?? model

  return (
    <header
      className="
        shrink-0 h-14 flex items-center justify-between gap-4 px-4
        border-b border-slate-800/80
        bg-[#060b18]/95 backdrop-blur-md
        z-30
        relative
      "
    >
      {/* Subtle accent line at the bottom edge of the header */}
      <span
        aria-hidden="true"
        className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-indigo-500/20 to-transparent"
      />

      {/* ───────────── Left: Brand (with DB-connected status dot) ───────────── */}
      <div className="flex items-center gap-3 min-w-0">
        <div className="flex items-center gap-2.5 shrink-0">
          <div className="relative w-8 h-8">
            <span
              aria-hidden="true"
              className="absolute inset-0 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 blur-md opacity-40"
            />
            <div className="relative w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 via-indigo-600 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-900/40 ring-1 ring-white/10">
              <BarChart3 className="w-4 h-4 text-white" aria-hidden="true" />
            </div>
            {/* DB-connected status dot — green when an active connection exists,
                slate when nothing is connected. Sits on the bottom-right of the
                logo as a small "live" beacon. */}
            <span
              aria-hidden="true"
              className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full ring-2 ring-[#060b18] transition-colors ${
                isOnline
                  ? 'bg-emerald-400 shadow-[0_0_6px_rgba(74,222,128,0.7)] animate-pulse'
                  : 'bg-slate-600'
              }`}
              title={isOnline ? 'Database connected' : 'No database connected'}
            />
          </div>

          <div className="leading-tight">
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-semibold tracking-tight gradient-text">DataLens AI</span>
              <span
                className="text-[9px] font-semibold text-indigo-300/80 bg-indigo-500/10 border border-indigo-500/20 rounded px-1 py-px tracking-wide"
                aria-label="Version 1.1"
              >
                v1.1
              </span>
            </div>
            <span
              className="hidden lg:flex items-center gap-1.5 text-[10px] leading-tight"
              aria-label={isOnline ? 'Database connected' : 'No database connected'}
            >
              <span className={isOnline ? 'text-emerald-400/90 font-medium' : 'text-slate-600'}>
                {isOnline ? 'Connected' : 'Offline'}
              </span>
              <span className="text-slate-700">·</span>
              <span className="text-slate-600">NL → SQL Analytics</span>
            </span>
          </div>
        </div>
      </div>

      {/* ───────────── Right: Provider + Model ───────────── */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Provider segmented control (replaces dropdown + redundant badge) */}
        <div
          role="radiogroup"
          aria-label="LLM provider"
          className="hidden sm:flex items-center bg-slate-900/80 border border-slate-800/80 rounded-lg p-0.5 shadow-inner shadow-black/20"
        >
          <button
            type="button"
            role="radio"
            aria-checked={provider === 'groq'}
            onClick={() => handleProviderChange('groq')}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all ${
              provider === 'groq'
                ? 'bg-indigo-500/15 text-indigo-200 ring-1 ring-indigo-500/30 shadow-sm shadow-indigo-900/30'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            <Cloud className="w-3 h-3" aria-hidden="true" />
            Cloud
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={provider === 'ollama'}
            onClick={() => handleProviderChange('ollama')}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all ${
              provider === 'ollama'
                ? 'bg-amber-500/15 text-amber-200 ring-1 ring-amber-500/30 shadow-sm shadow-amber-900/30'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            <Cpu className="w-3 h-3" aria-hidden="true" />
            Local
          </button>
        </div>

        <span aria-hidden="true" className="hidden sm:block h-7 w-px bg-slate-800/80" />

        {/* Model selector */}
        <div className="relative">
          <label htmlFor="model-select" className="sr-only">LLM Model</label>
          <span
            aria-hidden="true"
            className={`absolute left-3 top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full animate-pulse ${
              provider === 'ollama' ? 'bg-amber-400 shadow-[0_0_4px_rgba(251,191,36,0.6)]' : 'bg-indigo-400 shadow-[0_0_4px_rgba(129,140,248,0.6)]'
            }`}
          />
          <select
            id="model-select"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            title={`Active model: ${activeModelLabel}`}
            className="
              appearance-none
              bg-slate-900/80 border border-slate-800/80 hover:border-slate-700
              text-slate-100 text-xs font-medium
              rounded-lg pl-7 pr-8 py-1.5 max-w-[200px]
              focus:outline-none focus:ring-1 focus:ring-indigo-500/50 focus:border-indigo-500/40
              cursor-pointer transition-colors
              shadow-inner shadow-black/20
            "
          >
            {modelOptions.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
          <ChevronDown
            className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-500"
            aria-hidden="true"
          />
        </div>
      </div>
    </header>
  )
}
