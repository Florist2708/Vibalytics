import { useState, useEffect } from 'react'
import { API } from '../api.js'

function MiniHistogram({ breaks, counts }) {
  if (!breaks || !counts || counts.length === 0) return null
  const maxCount = Math.max(...counts, 1)
  const H = 48
  return (
    <svg className="mini-hist" viewBox={`0 0 ${counts.length * 8} ${H}`} preserveAspectRatio="none">
      {counts.map((c, i) => (
        <rect key={i} x={i * 8} y={H - (c / maxCount) * H} width={7} height={(c / maxCount) * H}
          fill="var(--accent)" opacity="0.7" />
      ))}
    </svg>
  )
}

export default function ColumnDetailPanel({ sessionId, fileId, col, onClose }) {
  const [result, setResult] = useState({ key: null, detail: null, error: null })
  const requestKey = col ? `${sessionId}:${fileId}:${col}` : null

  useEffect(() => {
    if (!col) return
    const controller = new AbortController()
    fetch(`${API}/data/${sessionId}/${fileId}/column/${encodeURIComponent(col)}`, {
      signal: controller.signal,
    })
      .then(async response => {
        if (!response.ok) throw new Error(`Column details failed (${response.status})`)
        return response.json()
      })
      .then(detail => setResult({ key: requestKey, detail, error: null }))
      .catch(e => {
        if (e.name !== 'AbortError') setResult({ key: requestKey, detail: null, error: e.message })
      })
    return () => controller.abort()
  }, [sessionId, fileId, col, requestKey])

  if (!col) return null

  const currentResult = result.key === requestKey ? result : { detail: null, error: null }
  const detail = currentResult.detail
  const loading = result.key !== requestKey

  return (
    <div className="col-detail-panel">
      <div className="col-detail-header">
        <strong>{col}</strong>
        <button className="col-detail-close" onClick={onClose}>✕</button>
      </div>
      {loading && <div className="col-detail-loading">Loading…</div>}
      {currentResult.error && <div className="global-error">{currentResult.error}</div>}
      {detail && !loading && (
        <div className="col-detail-body">
          <div className="col-stat-row"><span>Type</span><span>{detail.type}</span></div>
          <div className="col-stat-row"><span>Total rows</span><span>{detail.total?.toLocaleString()}</span></div>
          <div className="col-stat-row miss-row">
            <span>Missing</span>
            <span className={detail.missing > 0 ? 'miss-val' : ''}>{detail.missing?.toLocaleString()} ({detail.total > 0 ? ((detail.missing/detail.total)*100).toFixed(1) : 0}%)</span>
          </div>
          {detail.min != null && <>
            <div className="col-stat-row"><span>Min</span><span>{detail.min}</span></div>
            <div className="col-stat-row"><span>Mean</span><span>{detail.mean}</span></div>
            <div className="col-stat-row"><span>Median</span><span>{detail.median}</span></div>
            <div className="col-stat-row"><span>Max</span><span>{detail.max}</span></div>
            <div className="col-stat-row"><span>Std dev</span><span>{detail.sd}</span></div>
          </>}
          {detail.histogram && (
            <MiniHistogram breaks={detail.histogram.breaks} counts={detail.histogram.counts} />
          )}
          {detail.value_counts?.length > 0 && (
            <div className="col-value-counts">
              {detail.value_counts.slice(0, 15).map(({value, count}) => (
                <div key={value} className="col-val-row">
                  <span className="col-val-label" title={value}>{value}</span>
                  <div className="col-val-bar-wrap">
                    <div className="col-val-bar" style={{width: `${Math.round(count/detail.value_counts[0].count*100)}%`}} />
                  </div>
                  <span className="col-val-count">{count}</span>
                </div>
              ))}
            </div>
          )}
          {detail.sample?.length > 0 && (
            <div className="col-sample">
              <div className="col-stat-label">Sample values</div>
              <div className="col-sample-vals">{detail.sample.join(' · ')}</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
