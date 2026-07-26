import { useState, useEffect } from 'react'
import { fetchAssertions, createAssertion, patchAssertion, deleteAssertion, runFileChecks } from '../api.js'

const CHECK_TYPES = [
  { value: 'unique',        label: 'unique values',     needsCol: true,  needsVal: false, valLabel: '' },
  { value: 'not_null',      label: 'no nulls',          needsCol: true,  needsVal: false, valLabel: '' },
  { value: 'gte',           label: '>= value',          needsCol: true,  needsVal: true,  valLabel: 'Min value' },
  { value: 'gt',            label: '> value',           needsCol: true,  needsVal: true,  valLabel: 'Min value (exclusive)' },
  { value: 'lte',           label: '<= value',          needsCol: true,  needsVal: true,  valLabel: 'Max value' },
  { value: 'lt',            label: '< value',           needsCol: true,  needsVal: true,  valLabel: 'Max value (exclusive)' },
  { value: 'date_parseable',label: 'parseable as date', needsCol: true,  needsVal: false, valLabel: '' },
  { value: 'in_set',        label: 'value in set',      needsCol: true,  needsVal: true,  valLabel: 'Allowed values (comma-separated)' },
  { value: 'regex',         label: 'matches regex',     needsCol: true,  needsVal: true,  valLabel: 'Regex pattern' },
  { value: 'row_count_gte', label: 'row count >=',      needsCol: false, needsVal: true,  valLabel: 'Min rows' },
]

export default function AssertionsPanel({ fileId, schema }) {
  const [assertions, setAssertions] = useState(null)
  const [running, setRunning]       = useState(false)
  const [showAdd, setShowAdd]       = useState(false)
  const [editingId, setEditingId]   = useState(null)
  const [editingName, setEditingName] = useState('')
  const [error, setError]           = useState(null)

  const [newName, setNewName]       = useState('')
  const [newType, setNewType]       = useState('not_null')
  const [newCol, setNewCol]         = useState('')
  const [newVal, setNewVal]         = useState('')

  const cols = Object.keys(schema || {})

  const selectedType = CHECK_TYPES.find(t => t.value === newType) || CHECK_TYPES[0]

  useEffect(() => {
    setError(null)
    fetchAssertions(fileId)
      .then(setAssertions)
      .catch(e => {
        setAssertions([])
        setError(e.message)
      })
  }, [fileId])

  async function handleAdd() {
    if (!newName.trim()) return
    setError(null)
    const params = {}
    if (selectedType.needsVal) {
      if (newType === 'in_set') {
        params.values = newVal.split(',').map(s => s.trim()).filter(Boolean)
      } else if (newType === 'regex') {
        params.pattern = newVal
      } else {
        params.value = newVal
      }
    }
    try {
      await createAssertion(fileId, {
        name: newName.trim(),
        check_type: newType,
        column_name: selectedType.needsCol ? (newCol || cols[0] || null) : null,
        params,
        enabled: true,
      })
      const updated = await fetchAssertions(fileId)
      setAssertions(updated)
      setShowAdd(false)
      setNewName('')
      setNewVal('')
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleToggle(a) {
    setError(null)
    try {
      await patchAssertion(fileId, a.id, { enabled: !a.enabled })
      setAssertions(prev => prev.map(x => x.id === a.id ? { ...x, enabled: !x.enabled } : x))
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleDelete(id) {
    setError(null)
    try {
      await deleteAssertion(fileId, id)
      setAssertions(prev => prev.filter(x => x.id !== id))
    } catch (e) {
      setError(e.message)
    }
  }

  function startRename(a) {
    setEditingId(a.id)
    setEditingName(a.name)
  }

  async function commitRename(id) {
    const name = editingName.trim()
    if (!name) return
    setError(null)
    try {
      await patchAssertion(fileId, id, { name })
      setAssertions(prev => prev.map(x => x.id === id ? { ...x, name } : x))
      setEditingId(null)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleRun() {
    setRunning(true)
    setError(null)
    try {
      const res = await runFileChecks(fileId)
      // merge results back into assertions list
      const byId = {}
      for (const r of res.results) byId[r.assertion_id] = r
      setAssertions(prev => prev.map(a => ({
        ...a,
        last_result: byId[a.id] !== undefined ? byId[a.id] : a.last_result,
      })))
    } catch (e) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }

  if (assertions === null) return <div className="assertions-panel"><span className="muted">Loading…</span></div>

  const passCount = assertions.filter(a => a.last_result?.passed).length
  const failCount = assertions.filter(a => a.last_result && !a.last_result.passed).length
  const pendingCount = assertions.filter(a => !a.last_result).length

  return (
    <div className="assertions-panel">
      <div className="assertions-header">
        <span className="assertions-title">Data contracts</span>
        <div className="assertions-summary">
          {assertions.length > 0 && <>
            {passCount > 0 && <span className="assert-badge pass">{passCount} pass</span>}
            {failCount > 0 && <span className="assert-badge fail">{failCount} fail</span>}
            {pendingCount > 0 && <span className="assert-badge pending">{pendingCount} pending</span>}
          </>}
        </div>
        <div className="assertions-actions">
          <button className="assert-run-btn" onClick={handleRun} disabled={running || assertions.length === 0}>
            {running ? '…' : '▶ run'}
          </button>
          <button className="assert-add-btn" onClick={() => setShowAdd(v => !v)}>+ add</button>
        </div>
      </div>

      {error && <div className="global-error">{error}</div>}

      {assertions.length === 0 && !showAdd && (
        <div className="assertions-empty muted">No contracts yet — add checks to validate this dataset</div>
      )}

      {assertions.map(a => {
        const res = a.last_result
        const status = res === null || res === undefined ? 'pending'
                     : res.passed ? 'pass' : 'fail'
        return (
          <div key={a.id} className={`assertion-row ${a.enabled ? '' : 'disabled'}`}>
            <span className={`assert-status-dot ${status}`} title={
              status === 'pending' ? 'Not yet run' :
              status === 'pass'    ? 'Passed' :
              `Failed: ${res.failure_count} violation${res.failure_count !== 1 ? 's' : ''}` +
              (res.sample_failures?.length ? ` (e.g. ${res.sample_failures[0]})` : '')
            } />
            {editingId === a.id ? (
              <input
                className="assert-rename-input"
                value={editingName}
                autoFocus
                onChange={e => setEditingName(e.target.value)}
                onBlur={() => commitRename(a.id)}
                onKeyDown={e => { if (e.key === 'Enter') commitRename(a.id); if (e.key === 'Escape') setEditingId(null) }}
              />
            ) : (
              <span className="assert-name" title="Double-click to rename" onDoubleClick={() => startRename(a)}>{a.name}</span>
            )}
            <span className="assert-type muted">{a.check_type}{a.column_name ? ` · ${a.column_name}` : ''}</span>
            <button className="assert-toggle-btn" onClick={() => handleToggle(a)} title={a.enabled ? 'Disable' : 'Enable'}>
              {a.enabled ? '●' : '○'}
            </button>
            <button className="assert-del-btn" onClick={() => handleDelete(a.id)}>×</button>
          </div>
        )
      })}

      {showAdd && (
        <div className="assertion-add-form">
          <input
            className="assert-input"
            placeholder="Rule name (e.g. customer_id must be unique)"
            value={newName}
            onChange={e => setNewName(e.target.value)}
          />
          <div className="assert-add-row">
            <select className="assert-select" value={newType} onChange={e => { setNewType(e.target.value); setNewVal('') }}>
              {CHECK_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
            {selectedType.needsCol && cols.length > 0 && (
              <select className="assert-select" value={newCol || cols[0]} onChange={e => setNewCol(e.target.value)}>
                {cols.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            )}
            {selectedType.needsVal && (
              <input
                className="assert-input small"
                placeholder={selectedType.valLabel}
                value={newVal}
                onChange={e => setNewVal(e.target.value)}
              />
            )}
          </div>
          <div className="assert-add-actions">
            <button className="assert-save-btn" onClick={handleAdd} disabled={!newName.trim()}>Add</button>
            <button className="assert-cancel-btn" onClick={() => setShowAdd(false)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}
