import { useState } from 'react'
import { fetchPreview } from '../api.js'
import { makeSuggestions } from '../utils.js'
import FileProfile from './FileProfile.jsx'
import MissingnessPanel from './MissingnessPanel.jsx'
import AssertionsPanel from './AssertionsPanel.jsx'

export default function FileCard({ file, sessionId, active, onToggleActive, onSuggest, onInspect, onDeleteFile, onSaveNotes }) {
  const [expanded, setExpanded]         = useState(false)
  const [preview, setPreview]           = useState(null)
  const [loadingPrev, setLoading]       = useState(false)
  const [suggestionsOpen, setSuggestionsOpen] = useState(false)
  const [showMiss, setShowMiss]         = useState(false)
  const [showNotes, setShowNotes]       = useState(false)
  const [showProfile, setShowProfile]   = useState(false)
  const [showAssertions, setShowAssertions] = useState(false)
  const [notesText, setNotesText]       = useState(file.notes || '')
  const [previewError, setPreviewError] = useState(null)

  async function togglePreview() {
    if (expanded) { setExpanded(false); return }
    if (preview)  { setExpanded(true);  return }
    setLoading(true)
    setPreviewError(null)
    try {
      const data = await fetchPreview(sessionId, file.name)
      setPreview(data)
      setExpanded(true)
    } catch (e) {
      setPreviewError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const suggestions = makeSuggestions(file)
  const stats = file.stats || {}

  return (
    <div className={`file-card ${active ? '' : 'inactive'}`}>
      <div className="file-name">
        <span
          className="file-name-label"
          onClick={() => setSuggestionsOpen(v => !v)}
          title="Click to show/hide suggestions"
        >
          {suggestionsOpen ? '▾' : '▸'} {file.name}
          {file.current_version_seq > 1 && (
            <span className="file-version-badge" title={`Version ${file.current_version_seq}`}>v{file.current_version_seq}</span>
          )}
        </span>
        <div className="file-name-actions">
          {onInspect && (
            <button className="file-inspect-btn" onClick={() => onInspect(file.id)} title="Inspect data">⊞</button>
          )}
          <button
            className={`file-miss-btn ${showMiss ? 'active' : ''}`}
            onClick={() => setShowMiss(v => !v)}
            title="Show missingness"
          >≋</button>
          <button
            className={`file-notes-btn ${(showNotes || notesText) ? 'active' : ''}`}
            onClick={() => setShowNotes(v => !v)}
            title="Add notes about this file (included in agent context)"
          >📝</button>
          <button
            className={`file-assert-btn ${showAssertions ? 'active' : ''}`}
            onClick={() => setShowAssertions(v => !v)}
            title="Data contracts / assertions"
          >✓</button>
          {onDeleteFile && (
            <button className="file-delete-btn" onClick={() => onDeleteFile(file.id)} title="Delete file">×</button>
          )}
          <button
            className={`file-active-toggle ${active ? '' : 'off'}`}
            onClick={onToggleActive}
            title={active ? 'Active — click to exclude from next run' : 'Excluded — click to include'}
          >
            {active ? '●' : '○'}
          </button>
        </div>
      </div>
      <div className="file-meta">
        {file.nrow.toLocaleString()} rows · {Object.keys(file.schema).length} cols
        <button className={`inspect-btn${showProfile ? ' active' : ''}`} onClick={() => setShowProfile(v => !v)}>
          {showProfile ? '▲ profile' : '▼ profile'}
        </button>
        <button className="inspect-btn" onClick={togglePreview}>
          {loadingPrev ? '…' : expanded ? '▲ hide' : '▼ preview'}
        </button>
      </div>
      {showProfile
        ? <FileProfile schema={file.schema} stats={file.stats} />
        : (
          <div className="col-list">
            {Object.entries(file.schema).slice(0, 20).map(([col, type]) => {
              const s = stats[col] || {}
              const miss = s.miss_pct || 0
              const tip = [
                type,
                miss > 0 ? `${miss}% NA` : '',
                s.min !== undefined ? `min=${s.min} mean=${s.mean} max=${s.max}` : '',
                s.n_unique !== undefined ? `${s.n_unique} unique` : '',
              ].filter(Boolean).join(', ')
              return (
                <span key={col} className="col-tag" title={tip}>
                  {col}
                  {miss > 5 && <span className="miss-dot" />}
                </span>
              )
            })}
            {Object.keys(file.schema).length > 20 && (
              <span className="col-tag muted">+{Object.keys(file.schema).length - 20} more</span>
            )}
          </div>
        )
      }

      {showMiss && <MissingnessPanel fileId={file.id} />}

      {showAssertions && <AssertionsPanel fileId={file.id} schema={file.schema} />}
      {previewError && <div className="global-error">{previewError}</div>}

      {showNotes && (
        <div className="file-notes-panel">
          <textarea
            className="file-notes-input"
            value={notesText}
            onChange={e => setNotesText(e.target.value)}
            onBlur={() => onSaveNotes && onSaveNotes(file.id, notesText)}
            placeholder="Notes about this file — column meanings, relationships, caveats… (included in agent context)"
            rows={3}
          />
        </div>
      )}

      {expanded && preview && (
        <div className="preview-wrap">
          <table className="preview-table">
            <thead>
              <tr>{preview.columns.map(c => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {preview.rows.map((row, i) => (
                <tr key={i}>{row.map((cell, j) => <td key={j}>{cell || <em>NA</em>}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {suggestionsOpen && suggestions.length > 0 && (
        <div className="suggestion-chips">
          {suggestions.map(s => (
            <button key={s} className="chip" onClick={() => onSuggest(s)}>{s}</button>
          ))}
        </div>
      )}
    </div>
  )
}
