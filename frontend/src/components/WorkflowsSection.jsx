import { useState } from 'react'

export default function WorkflowsSection({ workflows, onRun, onDelete, streaming }) {
  const [open, setOpen] = useState(true)
  if (workflows.length === 0) return null
  return (
    <div className="workflows-section">
      <div className="workflows-header" onClick={() => setOpen(v => !v)}>
        <span>{open ? '▾' : '▸'} Workflows</span>
        <span className="workflows-global-badge" title="Workflows are shared across all workspaces">global</span>
        <span className="workflows-count">{workflows.length}</span>
      </div>
      {open && (
        <div className="workflows-list">
          {workflows.map(wf => {
            const tip = wf.input_vars?.length
              ? `Requires: ${wf.input_vars.join(', ')}`
              : wf.name
            return (
              <div key={wf.id} className="workflow-item">
                <span className="workflow-name" title={tip}>{wf.name}</span>
                <div className="workflow-actions">
                  <button
                    className="workflow-run-btn"
                    onClick={() => onRun(wf.id)}
                    disabled={streaming}
                    title={`Run workflow${wf.input_vars?.length ? ` (requires: ${wf.input_vars.join(', ')})` : ''}`}
                  >▶</button>
                  <button
                    className="workflow-delete-btn"
                    onClick={() => onDelete(wf.id)}
                    title="Delete workflow"
                  >×</button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
