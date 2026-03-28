import { useState } from 'react'
import { timeAgo } from '../utils.js'

export default function HistoryRunCard({ run, onResume }) {
  const [showCode, setShowCode] = useState(false)
  const codeLines = (run.editedCode || run.code || '').split('\n').slice(0, 5).join('\n')
  const firstPlot = run.plots?.[0] || null

  return (
    <div className={`history-run-card ${run.errors?.length ? 'history-run-error' : run.streaming ? '' : 'history-run-ok'}`}>
      <div className="history-run-top">
        <div className="history-run-prompt">{run.prompt}</div>
        <div className="history-run-meta">
          {run.version > 1 && <span className="version-badge">v{run.version}</span>}
          <span className={`history-run-status ${run.errors?.length ? 'status-error' : 'status-ok'}`}>
            {run.errors?.length ? '✕' : '✓'}
          </span>
          <span className="history-run-time" title={run.createdAt}>{timeAgo(run.createdAt)}</span>
          {run.durationMs > 0 && (
            <span className="history-run-dur">{(run.durationMs / 1000).toFixed(1)}s</span>
          )}
          <button className="history-resume-btn" onClick={() => onResume(run.id)}>→ Resume</button>
        </div>
      </div>
      {run.summary && <div className="history-run-summary">{run.summary}</div>}
      <div className="history-run-body">
        {firstPlot && <img className="history-plot-thumb" src={firstPlot} alt="plot" />}
        {codeLines && (
          <div className="history-code-wrap">
            <button className="history-code-toggle" onClick={() => setShowCode(v => !v)}>
              {showCode ? '▲ code' : '▼ code'}
            </button>
            {showCode && <pre className="history-code-snippet">{codeLines}{(run.editedCode || run.code || '').split('\n').length > 5 ? '\n…' : ''}</pre>}
          </div>
        )}
      </div>
    </div>
  )
}
