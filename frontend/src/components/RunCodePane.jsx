export default function RunCodePane({ runs, selectedRunId, onSelectRun, onEditCode }) {
  const selectedRun   = runs.find(r => r.id === selectedRunId)
  const code          = selectedRun?.editedCode || selectedRun?.code || ''
  const completedRuns = runs.filter(r => !r.streaming && r.code)

  return (
    <div className="run-code-pane">
      <div className="run-code-header">
        <select
          className="run-picker"
          value={selectedRunId || ''}
          onChange={e => onSelectRun(e.target.value || null)}
        >
          <option value="">Select a run…</option>
          {[...completedRuns].reverse().map(r => (
            <option key={r.id} value={r.id}>
              {(r.prompt?.slice(0, 48) || r.id.slice(0, 8))}
              {r.version > 1 ? ` (v${r.version})` : ''}
            </option>
          ))}
        </select>
        {selectedRun && (
          <button
            className="run-code-edit-btn"
            onClick={() => onEditCode(selectedRun)}
            title="Open in editor"
          >&lt;/&gt;</button>
        )}
      </div>
      {code ? (
        <pre className="run-code-readonly">{code}</pre>
      ) : (
        <div className="run-code-empty">
          {selectedRun ? 'No code for this run' : 'Select a run to view its code'}
        </div>
      )}
    </div>
  )
}
