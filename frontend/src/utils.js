// Strip markdown code blocks from agent text so only prose shows in chat.
// Handles partial (streaming) blocks by cutting from the opening fence onwards.
export function stripCodeBlocks(text) {
  let s = text.replace(/```[\w]*\r?\n[\s\S]*?```/g, '')  // complete blocks
  s = s.replace(/```[\s\S]*$/, '')                        // unclosed block (mid-stream)
  return s.replace(/\n{3,}/g, '\n\n').trim()
}

export function scrollToRun(runId) {
  document.getElementById(`run-${runId}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

export function makeSuggestions(file) {
  const cols    = Object.entries(file.schema)
  const numCols = cols.filter(([, t]) => t === 'numeric' || t === 'integer')
  const catCols = cols.filter(([, t]) => t === 'character' || t === 'factor')
  const dateCols = cols.filter(([, t]) => t === 'Date' || t.includes('POSIX'))

  const s = ["What's interesting in this data?", "Summarize all columns"]
  if (numCols.length >= 2)
    s.push(`Correlate ${numCols[0][0]} and ${numCols[1][0]}`)
  if (numCols.length >= 1)
    s.push(`Plot the distribution of ${numCols[0][0]}`)
  if (catCols.length >= 1)
    s.push(`Count rows by ${catCols[0][0]}`)
  if (dateCols.length >= 1 && numCols.length >= 1)
    s.push(`Plot ${numCols[0][0]} over time`)

  return s.slice(0, 5)
}

export function friendlyError(raw) {
  if (!raw) return raw
  const nf = raw.match(/object '(.+?)' not found/)
  if (nf) return `Column or variable "${nf[1]}" doesn't exist — check the column name`
  const fn = raw.match(/could not find function "(.+?)"/)
  if (fn) return `Function "${fn[1]}" not available — you may need to load a library`
  if (raw.includes('subscript out of bounds')) return 'Index out of range — check your row/column numbers'
  const m = raw.match(/Error[^:]*:\s*(.+)/s)
  if (m) return m[1].trim()
  return raw
}

export function typeIcon(type) {
  if (!type) return 'Aa'
  // R types
  if (type === 'numeric' || type === 'integer' || type === 'double') return '#'
  if (type === 'logical') return '✓'
  if (type === 'Date' || type.includes('POSIX')) return 'D'
  // Python/pandas types
  if (/^(float|int|uint)\d*/.test(type)) return '#'
  if (type === 'bool') return '✓'
  if (type.startsWith('datetime')) return 'D'
  return 'Aa'
}

export function fmtBytes(n) {
  if (!n) return '0 B'
  if (n < 1024)       return `${n} B`
  if (n < 1024 ** 2)  return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 ** 2).toFixed(1)} MB`
}

export function timeAgo(isoStr) {
  if (!isoStr) return ''
  const diff = Date.now() - new Date(isoStr).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1)  return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}
