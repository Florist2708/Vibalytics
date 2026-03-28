import { useState } from 'react'

export default function ContextSection({ contextSnapshot }) {
  const [open, setOpen] = useState(false)
  if (!contextSnapshot) return null
  return (
    <div className="context-section">
      <button className="trace-toggle" onClick={() => setOpen(v => !v)}>
        {open ? '▲' : '▼'} data context
      </button>
      {open && <pre className="context-text">{contextSnapshot}</pre>}
    </div>
  )
}
