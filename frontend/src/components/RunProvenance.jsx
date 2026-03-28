export default function RunProvenance({ activeFileVersions, producedVersions, files }) {
  const usedEntries  = Object.entries(activeFileVersions || {})
  const produced     = producedVersions || []
  if (usedEntries.length === 0 && produced.length === 0) return null

  // Detect drift: files whose version has advanced since this run
  const fileByName = {}
  for (const f of (files || [])) fileByName[f.name] = f
  const drifted = usedEntries.filter(([name, seq]) => {
    const f = fileByName[name]
    return f && (f.current_version_seq || f.version_num || 1) > seq
  })

  return (
    <div className="run-provenance">
      {drifted.length > 0 && (
        <div className="prov-drift-warning">
          ⚠ {drifted.map(([n]) => n).join(', ')} {drifted.length === 1 ? 'has' : 'have'} changed since this run
        </div>
      )}
      <div className="prov-tags-row">
        {usedEntries.length > 0 && (
          <span className="prov-group">
            <span className="prov-label">used</span>
            {usedEntries.map(([name, seq]) => {
              const isDrifted = drifted.some(([n]) => n === name)
              return (
                <span key={name} className={`prov-tag${isDrifted ? ' prov-tag-drifted' : ''}`}>
                  {name} v{seq}{isDrifted ? ' ↑' : ''}
                </span>
              )
            })}
          </span>
        )}
        {produced.length > 0 && (
          <span className="prov-group">
            <span className="prov-arrow">→</span>
            <span className="prov-label prov-label-produced">produced</span>
            {produced.map(v => (
              <span key={v.var_name + v.version_num} className="prov-tag prov-tag-produced">{v.var_name} v{v.version_num}</span>
            ))}
          </span>
        )}
      </div>
    </div>
  )
}
