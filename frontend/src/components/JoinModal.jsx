import { useState, useEffect, useRef } from 'react'
import { API } from '../api.js'

export default function JoinModal({ files, sessionId, workspaceId, initialHint, onSave, onClose }) {
  const [leftVar,   setLeftVar]   = useState(initialHint?.leftVar  || files[0]?.name || '')
  const [rightVar,  setRightVar]  = useState(initialHint?.rightVar || files[1]?.name || '')
  const [leftKey,   setLeftKey]   = useState(initialHint?.key || '')
  const [rightKey,  setRightKey]  = useState(initialHint?.key || '')
  const [joinType,  setJoinType]  = useState('inner')
  const [outputVar, setOutputVar] = useState('')
  const [preview,   setPreview]   = useState(null)
  const [loading,   setLoading]   = useState(false)
  const [saving,    setSaving]    = useState(false)
  const [error,     setError]     = useState(null)

  const leftFile  = files.find(f => f.name === leftVar)
  const rightFile = files.find(f => f.name === rightVar)
  const leftCols  = Object.keys(leftFile?.schema  || {})
  const rightCols = Object.keys(rightFile?.schema || {})

  // Auto-pick first shared key when files change (skip on first mount if hint provided)
  const didMount = useRef(false)
  useEffect(() => {
    if (!didMount.current && initialHint?.key) { didMount.current = true; return }
    didMount.current = true
    const shared = leftCols.find(c => rightCols.includes(c))
    setLeftKey(shared || leftCols[0] || '')
    setRightKey(shared || rightCols[0] || '')
    setPreview(null)
    setError(null)
  }, [leftVar, rightVar])   // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-generate output var name
  useEffect(() => {
    setOutputVar(`${leftVar}_${rightVar}`.replace(/[^a-zA-Z0-9_]/g, '_'))
  }, [leftVar, rightVar])

  async function handlePreview() {
    setLoading(true); setError(null); setPreview(null)
    try {
      const res = await fetch(`${API}/workspace/${workspaceId}/join/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId, left_var: leftVar, right_var: rightVar,
          left_key: leftKey, right_key: rightKey, join_type: joinType,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Preview failed')
      setPreview(data)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  async function handleSave() {
    setSaving(true); setError(null)
    try {
      const res = await fetch(`${API}/workspace/${workspaceId}/join/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId, left_var: leftVar, right_var: rightVar,
          left_key: leftKey, right_key: rightKey, join_type: joinType,
          output_var: outputVar,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Save failed')
      onSave(data)
      onClose()
    } catch (e) { setError(e.message) }
    finally { setSaving(false) }
  }

  const IDENT_RE   = /^[A-Za-z_][A-Za-z0-9_.]{0,99}$/
  const outputValid = IDENT_RE.test(outputVar)
  const canPreview = leftVar && rightVar && leftVar !== rightVar && leftKey && rightKey
  const canSave    = canPreview && outputValid && preview

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="join-modal">
        <div className="join-modal-header">
          <span>Join datasets</span>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="join-modal-body">
          {/* Dataset selectors */}
          <div className="join-row">
            <div className="join-col-group">
              <label>Left dataset</label>
              <select value={leftVar} onChange={e => setLeftVar(e.target.value)}>
                {files.map(f => <option key={f.name} value={f.name}>{f.name}</option>)}
              </select>
              <select value={leftKey} onChange={e => { setLeftKey(e.target.value); setPreview(null) }}>
                <option value="">— join key —</option>
                {leftCols.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>

            <div className="join-type-group">
              <label>Join type</label>
              {['inner','left','right','full'].map(t => (
                <button key={t}
                  className={`join-type-btn${joinType === t ? ' active' : ''}`}
                  onClick={() => { setJoinType(t); setPreview(null) }}
                >{t}</button>
              ))}
            </div>

            <div className="join-col-group">
              <label>Right dataset</label>
              <select value={rightVar} onChange={e => setRightVar(e.target.value)}>
                {files.map(f => <option key={f.name} value={f.name}>{f.name}</option>)}
              </select>
              <select value={rightKey} onChange={e => { setRightKey(e.target.value); setPreview(null) }}>
                <option value="">— join key —</option>
                {rightCols.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>

          {/* Output name + preview button */}
          <div className="join-actions-row">
            <div className="join-output-group">
              <label>Save as variable {outputVar && !outputValid && <span className="join-name-error">invalid name</span>}</label>
              <input value={outputVar} onChange={e => { setOutputVar(e.target.value); setPreview(null) }}
                placeholder="output_var_name"
                className={`join-output-input${outputVar && !outputValid ? ' join-input-invalid' : ''}`} />
            </div>
            <button className="btn-primary" onClick={handlePreview}
              disabled={!canPreview || loading}>
              {loading ? 'Running…' : 'Preview'}
            </button>
          </div>

          {error && <div className="join-error">{error}</div>}

          {/* Preview table */}
          {preview && (
            <div className="join-preview">
              <div className="join-preview-meta">
                {preview.nrow} rows · {preview.columns?.length} columns
              </div>
              <div className="join-preview-table-wrap">
                <table className="join-preview-table">
                  <thead>
                    <tr>{preview.columns?.map(c => <th key={c}>{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {preview.rows?.map((row, i) => (
                      <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        <div className="join-modal-footer">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={handleSave}
            disabled={!canSave || saving}>
            {saving ? 'Saving…' : 'Save as new dataset'}
          </button>
        </div>
      </div>
    </div>
  )
}
