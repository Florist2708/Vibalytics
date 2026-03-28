import { useState } from 'react'

export default function TraceSection({ trace }) {
  const [open, setOpen] = useState(false)
  if (!trace?.length) return null
  return (
    <div className="trace-section">
      <button className="trace-toggle" onClick={() => setOpen(v => !v)}>
        {open ? '▲' : '▼'} trace ({trace.length} steps)
      </button>
      {open && (
        <ol className="trace-list">
          {trace.map((t, i) => <li key={i}>{t}</li>)}
        </ol>
      )}
    </div>
  )
}
