import { useState, useEffect } from 'react'
import { API } from '../api.js'
import { fmtBytes } from '../utils.js'

function CleanupCard({ title, description, impact, impactZero, onConfirm, danger }) {
  const [confirming, setConfirming] = useState(false)
  const [done,       setDone]       = useState(false)
  const [error,      setError]      = useState(null)

  async function go() {
    setConfirming(true)
    setError(null)
    try {
      await onConfirm()
      setDone(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setConfirming(false)
    }
  }

  return (
    <div className={`cleanup-card${danger ? ' cleanup-card-danger' : ''}`}>
      <div className="cleanup-card-left">
        <div className="cleanup-card-title">{title}</div>
        <div className="cleanup-card-desc">{description}</div>
      </div>
      <div className="cleanup-card-right">
        <div className={`cleanup-impact${impactZero ? ' cleanup-impact-zero' : ''}`}>{impact}</div>
        {done
          ? <span className="cleanup-done">✓ done</span>
          : <button className={`cleanup-btn${danger ? ' cleanup-btn-danger' : ''}`}
              onClick={go} disabled={confirming || impactZero}>
              {confirming ? '…' : 'Delete'}
            </button>
        }
        {error && <span className="cleanup-error">{error}</span>}
      </div>
    </div>
  )
}

export default function StoragePage({
  workspaceId, workspaceName, workspaces = [],
  onBack, onWorkspacesDeleted,
  onRefreshRuns, onRefreshMessages,
}) {
  const [stats,    setStats]    = useState(null)
  const [loading,  setLoading]  = useState(true)
  const [loadError, setLoadError] = useState(null)

  // Multi-select delete state
  const [selected,    setSelected]    = useState(new Set())
  const [confirmed,   setConfirmed]   = useState(false)
  const [deleting,    setDeleting]    = useState(false)
  const [delError,    setDelError]    = useState(null)
  const [selectAll,   setSelectAll]   = useState(false)

  async function loadStats() {
    setLoading(true)
    setLoadError(null)
    try {
      const res = await fetch(`${API}/workspace/${workspaceId}/storage`)
      if (!res.ok) throw new Error(`Storage summary failed (${res.status})`)
      setStats(await res.json())
    } catch (e) {
      setLoadError(e.message)
    } finally { setLoading(false) }
  }

  useEffect(() => { loadStats() }, [workspaceId])  // eslint-disable-line react-hooks/exhaustive-deps

  async function cleanup(action) {
    const response = await fetch(`${API}/workspace/${workspaceId}/cleanup/${action}`, { method: 'POST' })
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      throw new Error(payload.detail || 'Cleanup failed')
    }
    await loadStats()
    if (action === 'chat')        onRefreshMessages?.()
    if (action === 'run_history') onRefreshRuns?.()
  }

  function toggleWorkspace(id) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
    setConfirmed(false)
    setDelError(null)
  }

  function toggleAll(checked) {
    setSelectAll(checked)
    setSelected(checked ? new Set(workspaces.map(w => w.id)) : new Set())
    setConfirmed(false)
    setDelError(null)
  }

  async function handleDeleteSelected() {
    if (!confirmed || selected.size === 0) return
    setDeleting(true)
    setDelError(null)
    const ids = [...selected]
    const failed = []
    for (const id of ids) {
      try {
        const res = await fetch(`${API}/workspace/${id}`, { method: 'DELETE' })
        if (!res.ok) {
          const msg = (await res.json().catch(() => ({}))).detail || 'Delete failed'
          failed.push(msg)
        }
      } catch (e) {
        failed.push(e.message)
      }
    }
    setDeleting(false)
    if (failed.length > 0) {
      setDelError(failed.join('; '))
    } else {
      onWorkspacesDeleted?.(ids)
    }
  }

  if (loading && !stats) {
    return (
      <div className="storage-page">
        <div className="storage-header">
          <button className="back-btn" onClick={onBack}>← Back</button>
          <h2 className="storage-title">Storage &amp; Cleanup</h2>
        </div>
        <div className="storage-loading">Loading…</div>
      </div>
    )
  }

  const s = stats || {}
  const nSelected = selected.size
  const currentSelected = selected.has(workspaceId)

  return (
    <div className="storage-page">
      <div className="storage-header">
        <button className="back-btn" onClick={onBack}>← Back</button>
        <div>
          <h2 className="storage-title">Storage &amp; Cleanup</h2>
          <div className="storage-workspace-name">{workspaceName}</div>
        </div>
      </div>

      <div className="cleanup-list">
        {loadError && <div className="cleanup-error">{loadError}</div>}
        <CleanupCard
          title="Clear chat history"
          description="Removes all messages from the conversation panel. Run history is kept."
          impact={`${s.chat_messages ?? '—'} messages`}
          impactZero={s.chat_messages === 0}
          onConfirm={() => cleanup('chat')}
        />
        <CleanupCard
          title="Delete archived files"
          description="Permanently deletes files you've already archived. Cannot be undone."
          impact={`${s.archived_files ?? '—'} archived files`}
          impactZero={s.archived_files === 0}
          onConfirm={() => cleanup('archived_files')}
        />
        <CleanupCard
          title="Prune old dataset versions"
          description="Keeps the original upload and the 6 most recent versions per dataset. Removes everything older."
          impact={`${s.old_versions ?? '—'} old versions`}
          impactZero={s.old_versions === 0}
          onConfirm={() => cleanup('old_versions')}
        />
        <CleanupCard
          title="Delete run artifacts"
          description="Removes all plot images and exported files stored as run artifacts. Run history and code are kept."
          impact={`${s.run_artifacts ?? '—'} artifacts · ${fmtBytes(s.run_artifacts_bytes)}`}
          impactZero={s.run_artifacts === 0}
          onConfirm={() => cleanup('run_artifacts')}
        />
        <CleanupCard
          title="Delete run history"
          description="Removes all runs, code, outputs, and artifacts. Chat history is kept."
          impact={`${s.runs ?? '—'} runs`}
          impactZero={s.runs === 0}
          onConfirm={() => cleanup('run_history')}
          danger
        />
      </div>

      {/* ── Workspace bulk delete ─────────────────────────────────────── */}
      <div className="cleanup-danger-zone">
        <div className="cleanup-danger-zone-label">Delete workspaces</div>

        <div className="ws-delete-list">
          {/* Select-all header */}
          <label className="ws-delete-row ws-delete-header">
            <input
              type="checkbox"
              checked={selectAll}
              onChange={e => toggleAll(e.target.checked)}
            />
            <span className="ws-delete-name">All workspaces</span>
            <span className="ws-delete-count">{workspaces.length} total</span>
          </label>

          <div className="ws-delete-divider" />

          {workspaces.map(w => (
            <label key={w.id} className={`ws-delete-row${w.id === workspaceId ? ' ws-delete-current' : ''}`}>
              <input
                type="checkbox"
                checked={selected.has(w.id)}
                onChange={() => toggleWorkspace(w.id)}
              />
              <span className="ws-delete-name">
                {w.name || 'Untitled'}
                {w.id === workspaceId && <span className="ws-delete-current-badge">current</span>}
              </span>
            </label>
          ))}
        </div>

        {nSelected > 0 && (
          <div className="ws-delete-footer">
            <div className="ws-delete-summary">
              {nSelected} workspace{nSelected !== 1 ? 's' : ''} selected
              {currentSelected && ' — includes current workspace'}
            </div>
            <label className="cleanup-toggle-label">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={e => { setConfirmed(e.target.checked); setDelError(null) }}
              />
              <span>I understand this cannot be undone</span>
            </label>
            {delError && <div className="cleanup-error">{delError}</div>}
            <button
              className="cleanup-btn cleanup-btn-danger ws-delete-btn"
              disabled={!confirmed || deleting}
              onClick={handleDeleteSelected}
            >
              {deleting ? 'Deleting…' : `Delete ${nSelected} workspace${nSelected !== 1 ? 's' : ''}`}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
