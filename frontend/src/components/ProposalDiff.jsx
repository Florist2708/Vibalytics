import { useState, useEffect } from 'react'
import { API } from '../api.js'

export default function ProposalDiff({ runId, propId }) {
  const [diff, setDiff] = useState(null)
  const [open, setOpen] = useState(true)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/run/${runId}/proposal/${propId}/diff`)
      .then(r => r.json())
      .then(d => { setDiff(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [runId, propId])

  return (
    <>
      <button className="proposal-diff-btn" onClick={() => setOpen(v => !v)} disabled={loading}>
        {loading ? '…' : open ? '△ Diff' : '⊿ Diff'}
      </button>
      {open && diff && (
        <div className="version-diff-view proposal-diff-inline">
          <div className="diff-summary">
            <span className={diff.row_delta > 0 ? 'delta-neutral' : diff.row_delta < 0 ? 'delta-loss' : ''}>
              {diff.row_delta > 0 ? '+' : ''}{diff.row_delta} rows
            </span>
            {diff.changed_cols?.length > 0 && (
              <span> · {diff.changed_cols.length} col{diff.changed_cols.length > 1 ? 's' : ''} changed</span>
            )}
          </div>
          {diff.changed_cols?.length > 0 && <div className="diff-cols">Changed: {diff.changed_cols.join(', ')}</div>}
          {diff.added_cols?.length > 0 && <div className="diff-cols">Added: {diff.added_cols.join(', ')}</div>}
          {diff.removed_cols?.length > 0 && <div className="diff-cols">Removed: {diff.removed_cols.join(', ')}</div>}
          {diff.sample_rows?.length > 0 && (
            <div className="diff-samples">
              <div className="diff-samples-title">Sample changes ({diff.sample_rows.length})</div>
              {diff.sample_rows.map(({row, changes}) => (
                <div key={row} className="diff-sample-row">
                  <span className="diff-row-num">row {row + 1}</span>
                  {Object.entries(changes).map(([col, {before, after}]) => (
                    <span key={col} className="diff-cell-change">
                      <span className="diff-col-name">{col}:</span>
                      <span className="diff-before">{before || '∅'}</span>
                      <span className="diff-arrow">→</span>
                      <span className="diff-after">{after || '∅'}</span>
                    </span>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  )
}
