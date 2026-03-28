export default function JoinDiscovery({ files, onOpenJoin }) {
  if (files.length < 2) return null
  const colToFiles = {}
  for (const f of files) {
    for (const col of Object.keys(f.schema || {})) {
      if (!colToFiles[col]) colToFiles[col] = []
      colToFiles[col].push(f.name)
    }
  }
  const joins = Object.entries(colToFiles)
    .filter(([, fnames]) => fnames.length >= 2)
    .slice(0, 5)

  if (joins.length === 0) return null

  return (
    <div className="join-discovery">
      <div className="join-discovery-label">Possible joins</div>
      {joins.map(([col, fnames]) => (
        <div key={col} className="join-hint-row">
          <span className="join-col">{col}</span>
          <span className="join-files">{fnames.join(' ↔ ')}</span>
          <button
            className="chip join-chip"
            onClick={() => onOpenJoin({ leftVar: fnames[0], rightVar: fnames[1], key: col })}
            title={`Join ${fnames[0]} and ${fnames[1]} on ${col}`}
          >→ Join</button>
        </div>
      ))}
    </div>
  )
}
