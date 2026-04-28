import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { TokenUsage } from '../types'

interface Props {
  content: string
  isStreaming: boolean
  usage?: TokenUsage
}

function fmtMs(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`
}

export function InsightPanel({ content, isStreaming, usage }: Props) {
  if (!content && !isStreaming) return null

  // Prefer latency_ms (LangGraph agent), fall back to total_elapsed_ms (multi-agent pipeline)
  const displayLatency =
    typeof usage?.latency_ms === 'number' && usage.latency_ms > 0
      ? usage.latency_ms
      : typeof usage?.total_elapsed_ms === 'number' && usage.total_elapsed_ms > 0
        ? usage.total_elapsed_ms
        : null

  const inTok    = usage?.input_tokens  ?? 0
  const outTok   = usage?.output_tokens ?? 0
  const showStrip = !isStreaming && (displayLatency != null || inTok > 0 || outTok > 0)

  return (
    <div className="bg-slate-900/80 border border-slate-700/50 rounded-xl px-5 py-4">
      <div className="flex items-center mb-3">
        <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">
          AI Analysis
        </span>
      </div>

      <div
        className="
          prose prose-invert prose-sm max-w-none
          prose-p:leading-relaxed prose-p:my-1.5
          prose-headings:text-slate-200 prose-headings:font-semibold
          prose-strong:text-indigo-300
          prose-a:text-indigo-400
          prose-code:text-indigo-300 prose-code:bg-slate-800 prose-code:rounded prose-code:px-1 prose-code:text-xs
          prose-pre:bg-transparent prose-pre:p-0 prose-pre:my-2
          prose-table:text-xs
          prose-th:border prose-th:border-slate-600 prose-th:px-3 prose-th:py-1.5 prose-th:bg-slate-800/60
          prose-td:border prose-td:border-slate-700/60 prose-td:px-3 prose-td:py-1.5
          prose-li:my-0.5
        "
      >
        {content ? (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              code({ className, children, ...props }) {
                const match = /language-(\w+)/.exec(className ?? '')
                const isBlock = className?.includes('language-')
                return isBlock && match ? (
                  <SyntaxHighlighter
                    style={vscDarkPlus}
                    language={match[1]}
                    PreTag="div"
                    customStyle={{
                      borderRadius: '0.5rem',
                      fontSize: '0.7rem',
                      margin: 0,
                      padding: '0.75rem',
                    }}
                  >
                    {String(children).replace(/\n$/, '')}
                  </SyntaxHighlighter>
                ) : (
                  <code className={className} {...props}>
                    {children}
                  </code>
                )
              },
            }}
          >
            {content}
          </ReactMarkdown>
        ) : null}
        {isStreaming && (
          <span
            aria-hidden="true"
            className="inline-block w-0.5 h-4 bg-indigo-400 ml-0.5 animate-blink align-middle"
          />
        )}
      </div>

      {/* Telemetry strip — bottom-right of the panel.
          Shows latency + input/output tokens explicitly so the user always
          sees all three numbers (zero counts mean "no LLM was called",
          which is meaningful for guardrail-blocked runs). */}
      {showStrip && (
        <div className="flex justify-end mt-3 pt-3 border-t border-slate-800/60">
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-indigo-500/10 text-indigo-300 ring-1 ring-indigo-500/20 text-[11px] font-medium tabular-nums"
            title={`Total latency: ${displayLatency != null ? fmtMs(displayLatency) : '—'}\nInput tokens: ${inTok.toLocaleString()}\nOutput tokens: ${outTok.toLocaleString()}`}
          >
            {displayLatency != null && <span>{fmtMs(displayLatency)}</span>}
            {displayLatency != null && <span aria-hidden="true" className="opacity-60">·</span>}
            <span>↑{inTok.toLocaleString()}</span>
            <span aria-hidden="true" className="opacity-60">·</span>
            <span>↓{outTok.toLocaleString()}</span>
            <span className="opacity-60 ml-0.5">tok</span>
          </span>
        </div>
      )}
    </div>
  )
}
