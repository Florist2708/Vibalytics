import { useState, useEffect } from 'react'
import { API } from '../api.js'

export default function MissingnessPanel({ fileId }) {
  const [result, setResult] = useState({ fileId: null, data: null, error: null })

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API}/file/${fileId}/missingness`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error(`Missingness failed (${response.status})`)
        return response.json()
      })
      .then(data => setResult({ fileId, data, error: null }))
      .catch(e => {
        if (e.name !== 'AbortError') setResult({ fileId, data: null, error: e.message })
      })
    return () => controller.abort()
  }, [fileId])

  const current = result.fileId === fileId ? result : { data: null, error: null }
  const data = current.data
  if (current.error) return <div className="miss-panel"><div className="global-error">{current.error}</div></div>
  if (!data) return null
  const cols = data.columns.filter(c => c.miss_pct > 0)

  if (cols.length === 0) return (
    <div className="miss-panel">
      <div className="miss-panel-title">Missingness</div>
      <div className="miss-all-complete">No missing values ✓</div>
    </div>
  )

  return (
    <div className="miss-panel">
      <div className="miss-panel-title">Missingness ({cols.length} columns)</div>
      {cols.sort((a,b) => b.miss_pct - a.miss_pct).map(c => (
        <div key={c.col} className="miss-col-row">
          <span className="miss-col-name" title={c.col}>{c.col}</span>
          <div className="miss-bar-wrap">
            <div className="miss-bar" style={{width: `${c.miss_pct}%`}} />
          </div>
          <span className="miss-pct">{c.miss_pct}%</span>
        </div>
      ))}
    </div>
  )
}
