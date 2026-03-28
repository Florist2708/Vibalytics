import { useState, useEffect } from 'react'
import { API } from '../api.js'

export default function MissingnessPanel({ fileId }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetch(`${API}/file/${fileId}/missingness`)
      .then(r => r.json())
      .then(setData)
      .catch(() => {})
  }, [fileId])

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
