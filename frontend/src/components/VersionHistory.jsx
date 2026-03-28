import { useState, useEffect } from 'react'
import { fetchFileVersions, revertFileVersion, API } from '../api.js'

export default function VersionHistory({ fileId, sessionId, onReverted }) {
  const [versions, setVersions]           = useState(null)
  const [currentVersionId, setCurrentVId] = useState(null)
  const [loading, setLoading]             = useState(true)
  const [reverting, setReverting]         = useState(null)
  const [error, setError]                 = useState(null)
  const [compareId, setCompareId]         = useState(null)
  const [diff, setDiff]                   = useState(null)

  useEffect(() => {
    if (!fileId) return
    setLoading(true)
    setError(null)
    fetchFileVersions(fileId)
      .then(data => {
        setVersions(data.versions)
        setCurrentVId(data.current_version_id)
        setLoading(false)
      })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [fileId])

  async function handleRevert(versionId) {
    setReverting(versionId)
    setError(null)
    try {
      await revertFileVersion(fileId, versionId, sessionId)
      onReverted()  // refreshes files in parent + auto-switches to data view
      const data = await fetchFileVersions(fileId)
      setVersions(data.versions)
      setCurrentVId(data.current_version_id)
    } catch (e) {
      setError(e.message)
    } finally {
      setReverting(null)
    }
  }

  function handleCompare(versionId) {
    if (compareId === versionId) { setCompareId(null); setDiff(null); return }
    setCompareId(versionId)
    const currentId = versions?.find(v => v.id === currentVersionId)?.id
    if (!currentId) return
    fetch(`${API}/file/${fileId}/diff?a=${versionId}&b=${currentId}`)
      .then(r => r.json())
      .then(setDiff)
      .catch(() => {})
  }

  if (loading) return <div className="version-history-state">Loading history…</div>
  if (error)   return <div className="version-history-state error">{error}</div>
  if (!versions?.length) return <div className="version-history-state">No version history yet</div>

  return (
    <div className="version-history">
      {[...versions].reverse().map(v => {
        const isCurrent = v.id === currentVersionId
        return (
          <div key={v.id} className={`version-row ${isCurrent ? 'current' : ''}`}>
            <div className="version-row-main">
              <span className="version-row-num">v{v.version_num}</span>
              {v.is_original && <span className="version-row-tag">original</span>}
              {isCurrent    && <span className="version-row-tag current">current</span>}
              <span className="version-row-nrow">{v.nrow.toLocaleString()} rows</span>
            </div>
            {v.description && <div className="version-row-desc">{v.description}</div>}
            <div className="version-row-footer">
              <span className="version-row-date">{v.created_at?.slice(0, 16).replace('T', ' ')}</span>
              <div style={{display:'flex', gap: 4}}>
                {!isCurrent && (
                  <button
                    className="version-revert-btn"
                    onClick={() => handleCompare(v.id)}
                    style={compareId === v.id ? {color: 'var(--accent)', borderColor: 'var(--accent)'} : {}}
                    disabled={!!reverting}
                    title="Compare with current version"
                  >
                    {compareId === v.id ? '△ Diff' : '⊿ Diff'}
                  </button>
                )}
                {!isCurrent && (
                  <button
                    className="version-revert-btn"
                    onClick={() => handleRevert(v.id)}
                    disabled={!!reverting}
                  >
                    {reverting === v.id ? '…' : '↩ Revert'}
                  </button>
                )}
              </div>
            </div>
          </div>
        )
      })}
      {diff && (
        <div className="version-diff-view">
          <div className="diff-summary">
            v{diff.version_a?.version_num} → v{diff.version_b?.version_num}:
            <span className={diff.row_delta > 0 ? 'delta-neutral' : diff.row_delta < 0 ? 'delta-loss' : ''}>
              {' '}{diff.row_delta > 0 ? '+' : ''}{diff.row_delta} rows
            </span>
            {diff.changed_cols?.length > 0 && (
              <span> · {diff.changed_cols.length} col{diff.changed_cols.length > 1 ? 's' : ''} changed</span>
            )}
          </div>
          {diff.changed_cols?.length > 0 && (
            <div className="diff-cols">Changed: {diff.changed_cols.join(', ')}</div>
          )}
          {diff.added_cols?.length > 0 && (
            <div className="diff-cols">Added: {diff.added_cols.join(', ')}</div>
          )}
          {diff.removed_cols?.length > 0 && (
            <div className="diff-cols">Removed: {diff.removed_cols.join(', ')}</div>
          )}
          {diff.sample_rows?.length > 0 && (
            <div className="diff-samples">
              <div className="diff-samples-title">Sample changes (showing {diff.sample_rows.length})</div>
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
    </div>
  )
}
