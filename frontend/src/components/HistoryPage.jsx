import { useState } from 'react'
import HistoryRunCard from './HistoryRunCard.jsx'

export default function HistoryPage({ runs, onBack, onResume }) {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all') // 'all' | 'ok' | 'error'

  const filtered = runs.filter(r => {
    if (r.streaming) return false
    const matchSearch = !search || (r.prompt || '').toLowerCase().includes(search.toLowerCase())
      || (r.summary || '').toLowerCase().includes(search.toLowerCase())
    const matchStatus = statusFilter === 'all'
      || (statusFilter === 'ok' && !r.errors?.length)
      || (statusFilter === 'error' && r.errors?.length > 0)
    return matchSearch && matchStatus
  })

  const okCount    = runs.filter(r => !r.streaming && !r.errors?.length).length
  const totalRuns  = runs.filter(r => !r.streaming).length

  return (
    <div className="history-page">
      <div className="history-header">
        <button className="history-back-btn" onClick={onBack}>← Analysis</button>
        <span className="history-title">Run History</span>
        <div className="history-stats">
          <span className="history-stat">{totalRuns} runs</span>
          {totalRuns > 0 && (
            <span className="history-stat">{Math.round(okCount / totalRuns * 100)}% success</span>
          )}
        </div>
      </div>
      <div className="history-controls">
        <input
          className="history-search"
          type="text"
          placeholder="Search runs…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          autoFocus
        />
        {search && <button className="results-search-clear" onClick={() => setSearch('')}>✕</button>}
        <div className="history-status-filter">
          {['all', 'ok', 'error'].map(f => (
            <button
              key={f}
              className={`history-filter-btn ${statusFilter === f ? 'active' : ''}`}
              onClick={() => setStatusFilter(f)}
            >{f === 'all' ? 'All' : f === 'ok' ? '✓ Success' : '✕ Errors'}</button>
          ))}
        </div>
      </div>
      {filtered.length === 0
        ? <div className="history-empty">{totalRuns === 0 ? 'No runs yet' : 'No runs match'}</div>
        : (
          <div className="history-list">
            {[...filtered].reverse().map(run => (
              <HistoryRunCard key={run.id} run={run} onResume={onResume} />
            ))}
          </div>
        )
      }
    </div>
  )
}
