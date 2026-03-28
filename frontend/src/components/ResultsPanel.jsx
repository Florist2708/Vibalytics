import { useState, useRef, useEffect } from 'react'
import RunCard from './RunCard.jsx'

export default function ResultsPanel({ runs, pendingProposals, rejectedProposals, onEditCode, onExport, onReport, onAcceptProposal, onRejectProposal, onRestoreProposal, onSaveWorkflow, onRerun, streaming, style, files }) {
  const bottomRef = useRef()
  const [filter, setFilter] = useState('')

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [runs])

  const filtered = filter
    ? runs.filter(r => (r.prompt || '').toLowerCase().includes(filter.toLowerCase()))
    : runs

  // Build parent/child maps for version chain visualization
  const runById = {}
  for (const r of runs) runById[r.id] = r
  const childrenOf = {}
  for (const r of runs) {
    if (r.parentId) {
      if (!childrenOf[r.parentId]) childrenOf[r.parentId] = []
      childrenOf[r.parentId].push(r)
    }
  }

  return (
    <aside className="results-panel" style={style}>
      <div className="results-header">
        <div className="sidebar-title">Results</div>
        {runs.length > 0 && (
          <>
            <button className="export-btn" onClick={onReport} title="Download HTML report">↓ Report</button>
            <button className="export-btn" onClick={onExport} title="Download ZIP">↓ Export</button>
          </>
        )}
      </div>
      {runs.length > 3 && (
        <div className="results-search-row">
          <input
            className="results-search"
            type="text"
            placeholder="Filter runs…"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          />
          {filter && <button className="results-search-clear" onClick={() => setFilter('')}>✕</button>}
        </div>
      )}
      {runs.length === 0
        ? <div className="results-empty">Code and plots will appear here</div>
        : filtered.length === 0
          ? <div className="results-empty">No runs match "{filter}"</div>
          : (
            <div className="runs-list">
              {filtered.map(run => (
                <RunCard
                  key={run.id}
                  run={run}
                  proposals={pendingProposals[run.id] || []}
                  rejectedProposals={rejectedProposals[run.id] || []}
                  onEditCode={onEditCode}
                  onAcceptProposal={onAcceptProposal}
                  onRejectProposal={onRejectProposal}
                  onRestoreProposal={onRestoreProposal}
                  onSaveWorkflow={onSaveWorkflow}
                  onRerun={onRerun}
                  streaming={streaming}
                  parentRun={run.parentId ? runById[run.parentId] : null}
                  childRuns={childrenOf[run.id] || []}
                  files={files}
                />
              ))}
              <div ref={bottomRef} />
            </div>
          )
      }
    </aside>
  )
}
