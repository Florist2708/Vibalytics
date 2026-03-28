import { useState, useEffect } from 'react'

export default function TableArtifact({ table }) {
  const [html, setHtml] = useState(table.html || null)

  useEffect(() => {
    if (!html && table.url) {
      fetch(table.url).then(r => r.text()).then(setHtml).catch(() => setHtml('<em>Failed to load table</em>'))
    }
  }, [table.url])

  if (!html) return <div className="table-artifact-loading">Loading table…</div>
  return <div className="table-artifact" dangerouslySetInnerHTML={{ __html: html }} />
}
