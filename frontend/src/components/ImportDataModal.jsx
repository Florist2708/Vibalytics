import { useState } from 'react'
import { importData } from '../api.js'

export default function ImportDataModal({ workspaceId, sessionId, files, onClose, onSuccess }) {
  const [source, setSource]       = useState('')
  const [mode, setMode]           = useState('new')
  const [varName, setVarName]     = useState('')
  const [targetId, setTargetId]   = useState(files[0]?.id || '')
  const [description, setDesc]    = useState('')
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState('')

  const isUrl = source.trim().toLowerCase().startsWith('http')

  async function handleImport() {
    const src = source.trim()
    if (!src) { setError('Enter a file path or URL'); return }
    if (mode === 'append' && !targetId) { setError('Select a target file'); return }
    setLoading(true)
    setError('')
    try {
      await importData(workspaceId, {
        session_id: sessionId,
        source: src,
        var_name: mode === 'new' ? varName.trim() : '',
        mode,
        target_file_id: mode === 'append' ? targetId : '',
        description: description.trim(),
      })
      onSuccess()
      onClose()
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="import-modal">
        <div className="join-modal-header">
          <span>Import data</span>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="import-field">
          <label className="import-label">Source</label>
          <input
            className="import-input"
            placeholder="Absolute path  (/home/…/data.csv)  or URL  (https://…)"
            value={source}
            onChange={e => setSource(e.target.value)}
            autoFocus
          />
          <div className="import-hint muted">
            {isUrl ? 'Will be downloaded to workspace' : 'Will be copied from your filesystem'}
          </div>
        </div>

        <div className="import-field">
          <label className="import-label">Mode</label>
          <div className="import-mode-row">
            <label className="import-radio">
              <input type="radio" value="new" checked={mode === 'new'} onChange={() => setMode('new')} />
              New dataset
            </label>
            <label className="import-radio">
              <input type="radio" value="append" checked={mode === 'append'} onChange={() => setMode('append')} disabled={files.length === 0} />
              Append to existing
            </label>
          </div>
        </div>

        {mode === 'new' && (
          <div className="import-field">
            <label className="import-label">Variable name <span className="muted">(optional — auto-derived from filename)</span></label>
            <input
              className="import-input"
              placeholder="e.g. sales_q4"
              value={varName}
              onChange={e => setVarName(e.target.value)}
            />
          </div>
        )}

        {mode === 'append' && files.length > 0 && (
          <div className="import-field">
            <label className="import-label">Append to</label>
            <select className="import-select" value={targetId} onChange={e => setTargetId(e.target.value)}>
              {files.map(f => (
                <option key={f.id} value={f.id}>{f.name} ({f.nrow.toLocaleString()} rows)</option>
              ))}
            </select>
            <div className="import-hint muted">Rows from the source will be added to this dataset as a new version</div>
          </div>
        )}

        <div className="import-field">
          <label className="import-label">Description <span className="muted">(optional)</span></label>
          <input
            className="import-input"
            placeholder="e.g. Q4 sales data, appended from remote server"
            value={description}
            onChange={e => setDesc(e.target.value)}
          />
        </div>

        {error && <div className="join-error">{error}</div>}

        <div className="join-modal-footer">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={handleImport} disabled={loading || !source.trim()}>
            {loading ? 'Importing…' : 'Import'}
          </button>
        </div>
      </div>
    </div>
  )
}
