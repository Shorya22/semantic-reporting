import { useEffect, useMemo } from 'react'
import { BarChart3, ChevronDown } from 'lucide-react'
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

  const active = sessions.find((s) => s.session_id === activeSessionId)

  useEffect(() => {
    api.getConfig()
      .then(({ default_model, llm_provider }) => {
        setProvider(llm_provider as LlmProvider)
        setModel(default_model)
      })
      .catch(() => {})
  }, [setModel, setProvider])

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
    setProvider(next)
    if (next === 'groq') setModel(GROQ_MODELS[0].id)
  }

  const modelOptions = useMemo(() => {
    const list = provider === 'ollama'
      ? (ollamaModels.length > 0 ? ollamaModels : OLLAMA_FALLBACK_MODELS)
      : GROQ_MODELS
    return list.some((m) => m.id === model) ? list : [{ id: model, label: model }, ...list]
  }, [model, provider, ollamaModels])

  return (
    <header className="shrink-0 h-12 flex items-center justify-between px-4 border-b border-slate-800/80 bg-[#060b18]/95 backdrop-blur-sm z-30">
      {/* Brand */}
      <div className="flex items-center gap-2.5">
        <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
          <BarChart3 className="w-3.5 h-3.5 text-white" aria-hidden="true" />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold tracking-tight gradient-text">DataLens AI</span>
          {active && (
            <span className="hidden md:flex items-center gap-1.5 text-xs text-slate-500 border-l border-slate-800 pl-2.5 ml-0.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shrink-0" aria-hidden="true" />
              {active.name}
            </span>
          )}
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-2">
        {/* Provider */}
        <div className="relative">
          <label htmlFor="provider-select" className="sr-only">LLM Provider</label>
          <select
            id="provider-select"
            value={provider}
            onChange={(e) => handleProviderChange(e.target.value as LlmProvider)}
            className="appearance-none bg-slate-900/80 border border-slate-700/60 text-slate-300 text-xs rounded-lg pl-3 pr-7 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500/50 cursor-pointer hover:border-slate-600 transition-colors"
          >
            <option value="groq">GroqCloud</option>
            <option value="ollama">Ollama</option>
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-500" aria-hidden="true" />
        </div>

        {/* Provider badge */}
        <span className={`hidden sm:inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full font-medium border ${
          provider === 'ollama'
            ? 'bg-amber-900/20 text-amber-300 border-amber-700/40'
            : 'bg-indigo-900/20 text-indigo-300 border-indigo-700/40'
        }`}>
          <span className={`w-1.5 h-1.5 rounded-full ${provider === 'ollama' ? 'bg-amber-400' : 'bg-indigo-400'}`} aria-hidden="true" />
          {provider === 'ollama' ? 'Local' : 'Cloud'}
        </span>

        {/* Model */}
        <div className="relative">
          <label htmlFor="model-select" className="sr-only">LLM Model</label>
          <select
            id="model-select"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="appearance-none bg-slate-900/80 border border-slate-700/60 text-slate-300 text-xs rounded-lg pl-3 pr-7 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500/50 cursor-pointer hover:border-slate-600 transition-colors max-w-[180px]"
          >
            {modelOptions.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-500" aria-hidden="true" />
        </div>
      </div>
    </header>
  )
}
