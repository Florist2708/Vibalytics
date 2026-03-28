import { useState } from 'react'
import { friendlyError } from '../utils.js'

export default function ErrorBlock({ raw }) {
  const [showRaw, setShowRaw] = useState(false)
  const friendly = friendlyError(raw)
  const hasDetails = friendly !== raw

  return (
    <div className="error-inline">
      <span>{friendly}</span>
      {hasDetails && (
        <>
          {' '}
          <button className="error-toggle" onClick={() => setShowRaw(v => !v)}>
            {showRaw ? 'hide details' : 'show details'}
          </button>
          {showRaw && <pre className="error-raw">{raw}</pre>}
        </>
      )}
    </div>
  )
}
