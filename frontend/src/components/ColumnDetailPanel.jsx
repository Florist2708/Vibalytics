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
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!col) return
    setLoading(true)
    fetch(`${API}/data/${sessionId}/${fileId}/column/${encodeURIComponent(col)}`)
      .then(r => r.json())
      .then(d => { setDetail(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [sessionId, fileId, col])

  if (!col) return null

  return (
    <div className="col-detail-panel">
      <div className="col-detail-header">
        <strong>{col}</strong>
        <button className="col-detail-close" onClick={onClose}>✕</button>
      </div>
      {loading && <div className="col-detail-loading">Loading…</div>}
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
