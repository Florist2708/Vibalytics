import { useState, useEffect } from 'react'
import { fetchData, editFileCells } from '../api.js'
import ColumnDetailPanel from './ColumnDetailPanel.jsx'

const FILTER_OPS = [
  { value: 'contains',    label: 'contains' },
  { value: '=',           label: '=' },
  { value: '!=',          label: '≠' },
  { value: 'starts_with', label: 'starts with' },
  { value: 'ends_with',   label: 'ends with' },
  { value: '>',           label: '>' },
  { value: '>=',          label: '≥' },
  { value: '<',           label: '<' },
  { value: '<=',          label: '≤' },
  { value: 'is_null',     label: 'is empty' },
  { value: 'not_null',    label: 'not empty' },
]
const NO_VAL_OPS = new Set(['is_null', 'not_null'])

export default function DataTable({ sessionId, fileId, onEdited }) {
  const [offset, setOffset]         = useState(0)
  const [sortBy, setSortBy]         = useState('')
  const [sortDir, setSortDir]       = useState('asc')
  const [filterCol, setFilterCol]   = useState('')
  const [filterOp, setFilterOp]     = useState('contains')
  const [filterVal, setFilterVal]   = useState('')
  const [pendingFilter, setPendingFilter] = useState({col: '', val: '', op: 'contains'})
  const [selectedCol, setSelectedCol] = useState(null)
  const [data, setData]             = useState(null)
  const [loading, setLoading]       = useState(false)
  const [editingCell, setEditingCell] = useState(null)  // {rowAbs, col}
  const [editValue, setEditValue]   = useState('')
  const [pendingEdits, setPendingEdits] = useState(new Map())  // key → {row, col, value}
  const [saving, setSaving]         = useState(false)
  const [saveError, setSaveError]   = useState(null)
  const LIMIT = 100

  useEffect(() => {
    if (!fileId || !sessionId) return

    let cancelled = false
    setLoading(true)
    fetchData(sessionId, fileId, { offset, limit: LIMIT, sortBy, sortDir, filterCol: pendingFilter.col, filterVal: pendingFilter.val, filterOp: pendingFilter.op })
      .then(d => { if (!cancelled) { setData(d); setLoading(false) } })
      .catch(e => { if (!cancelled) { setData({ error: e.message }); setLoading(false) } })
    return () => { cancelled = true }
  }, [sessionId, fileId, offset, sortBy, sortDir, pendingFilter])

  function handleSort(col) {
    if (sortBy === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(col)
      setSortDir('asc')
    }
    setOffset(0)
  }

  function startEdit(rowAbs, col, currentValue) {
    setEditingCell({ rowAbs, col })
    setEditValue(currentValue === '' ? '' : String(currentValue))
  }

  function commitEdit() {
    if (!editingCell) return
    const key = `${editingCell.rowAbs}:${editingCell.col}`
    setPendingEdits(prev => {
      const next = new Map(prev)
      next.set(key, { row: editingCell.rowAbs, col: editingCell.col, value: editValue })
      return next
    })
    setEditingCell(null)
  }

  async function saveEdits() {
    setSaving(true)
    setSaveError(null)
    try {
      await editFileCells(sessionId, fileId, Array.from(pendingEdits.values()))
      setPendingEdits(new Map())
      onEdited?.()
    } catch (e) {
      setSaveError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (!data && !loading) return <div className="data-pane-empty">Loading…</div>
  if (loading && !data) return <div className="data-pane-empty">Loading…</div>
  if (data?.error) return <div className="data-pane-empty" style={{ color: 'var(--error)' }}>{data.error}</div>

  const { columns = [], rows = [], total_rows = 0 } = data || {}
  const startRow = offset + 1
  const endRow   = Math.min(offset + LIMIT, total_rows)
  const canEdit  = !sortBy  // only allow editing when unsorted (row indices are stable)

  return (
    <div className="data-table-container">
      <div className="data-filter-row">
        <select value={filterCol} onChange={e => setFilterCol(e.target.value)} className="filter-col-select">
          <option value="">All columns</option>
          {columns.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select
          className="filter-op-select"
          value={filterOp}
          onChange={e => { setFilterOp(e.target.value); if (NO_VAL_OPS.has(e.target.value)) setFilterVal('') }}
        >
          {FILTER_OPS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        {!NO_VAL_OPS.has(filterOp) && (
          <input
            className="filter-val-input"
            type="text"
            placeholder="Value…"
            value={filterVal}
            onChange={e => setFilterVal(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { setOffset(0); setPendingFilter({col: filterCol, val: filterVal, op: filterOp}) } }}
          />
        )}
        <button className="filter-apply-btn" onClick={() => { setOffset(0); setPendingFilter({col: filterCol, val: filterVal, op: filterOp}) }}>⌕</button>
        {(pendingFilter.col || pendingFilter.val || pendingFilter.op !== 'contains') && (
          <button className="filter-clear-btn" onClick={() => { setFilterCol(''); setFilterOp('contains'); setFilterVal(''); setPendingFilter({col:'',val:'',op:'contains'}); setOffset(0) }}>✕</button>
        )}
      </div>
      <div style={{position: 'relative', flex: 1, display: 'flex', overflow: 'hidden'}}>
      <div className="data-table-wrap">
        {loading && <div className="data-table-overlay">Loading…</div>}
        <table className="data-table">
          <thead>
            <tr>
              {columns.map(col => (
                <th key={col}>
                  <div className="col-header-inner">
                    <span className="col-sort-btn" onClick={() => handleSort(col)}>
                      {col}{sortBy === col ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                    </span>
                    <button className="col-info-btn" onClick={() => setSelectedCol(selectedCol === col ? null : col)} title="Column detail">ℹ</button>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const rowAbs = offset + i
              return (
                <tr key={i}>
                  {row.map((cell, j) => {
                    const col = columns[j]
                    const key = `${rowAbs}:${col}`
                    const isPending = pendingEdits.has(key)
                    const isEditing = editingCell?.rowAbs === rowAbs && editingCell?.col === col

                    if (isEditing) {
                      return (
                        <td key={j} className="cell-editing">
                          <input
                            autoFocus
                            value={editValue}
                            onChange={e => setEditValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); commitEdit() }
                              if (e.key === 'Escape') setEditingCell(null)
                            }}
                            onBlur={commitEdit}
                          />
                        </td>
                      )
                    }

                    const displayValue = isPending ? pendingEdits.get(key).value : cell
                    return (
                      <td
                        key={j}
                        className={isPending ? 'cell-pending' : canEdit ? 'cell-editable' : ''}
                        onClick={() => canEdit && startEdit(rowAbs, col, cell)}
                        title={canEdit ? 'Click to edit' : undefined}
                      >
                        {displayValue === '' ? <em>NA</em> : displayValue}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {selectedCol && (
        <ColumnDetailPanel
          sessionId={sessionId}
          fileId={fileId}
          col={selectedCol}
          onClose={() => setSelectedCol(null)}
        />
      )}
      </div>
      {pendingEdits.size > 0 && (
        <div className="pending-edits-bar">
          <span className="pending-edits-count">{pendingEdits.size} unsaved change{pendingEdits.size !== 1 ? 's' : ''}</span>
          <button className="pending-discard-btn" onClick={() => { setPendingEdits(new Map()); setEditingCell(null) }} disabled={saving}>Discard</button>
          <button className="pending-save-btn" onClick={saveEdits} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          {saveError && <span className="pending-edits-error">{saveError}</span>}
        </div>
      )}
      <div className="data-table-controls">
        <button
          className="data-page-btn"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - LIMIT))}
        >‹ Prev</button>
        <span className="data-page-info">
          {total_rows > 0 ? `Rows ${startRow}–${endRow} of ${total_rows.toLocaleString()}` : 'No rows'}
        </span>
        <button
          className="data-page-btn"
          disabled={offset + LIMIT >= total_rows}
          onClick={() => setOffset(offset + LIMIT)}
        >Next ›</button>
      </div>
    </div>
  )
}
