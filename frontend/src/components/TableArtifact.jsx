import { useState, useEffect } from 'react'

export default function TableArtifact({ table }) {
  const [result, setResult] = useState({ url: null, html: null, error: null })

  useEffect(() => {
    if (table.html || !table.url) return
    const controller = new AbortController()
    fetch(table.url, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error(`Table load failed (${response.status})`)
        return response.text()
      })
      .then(html => setResult({ url: table.url, html, error: null }))
      .catch(e => {
        if (e.name !== 'AbortError') setResult({ url: table.url, html: null, error: e.message })
      })
    return () => controller.abort()
  }, [table.html, table.url])

  const loaded = result.url === table.url ? result : { html: null, error: null }
  const html = table.html || loaded.html

  if (loaded.error) return <div className="table-artifact-loading">{loaded.error}</div>
  if (!html) return <div className="table-artifact-loading">Loading table…</div>
  return <div className="table-artifact" dangerouslySetInnerHTML={{ __html: html }} />
}
