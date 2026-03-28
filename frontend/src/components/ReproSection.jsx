import { useState } from 'react'

export default function ReproSection({ runId, snapshot }) {
  const [open, setOpen] = useState(false)
  if (!snapshot) return null

  const fps  = snapshot.dataset_fingerprints || {}
  const pkgs = snapshot.packages || {}
  const pkgEntries = Object.entries(pkgs).sort(([a], [b]) => a.localeCompare(b))

  return (
    <div className="repro-section">
      <div className="repro-header-row">
        <button className="trace-toggle" onClick={() => setOpen(v => !v)}>
          {open ? '▲' : '▼'} reproducibility
        </button>
        <a
          className="repro-download-link"
          href={`/run/${runId}/repro`}
          download
          title="Download full reproducibility report"
        >↓ repro.txt</a>
      </div>
      {open && (
        <div className="repro-body">
          <div className="repro-meta-row">
            <span className="repro-label">Runtime</span>
            <span className="repro-value">{snapshot.runtime}</span>
          </div>
          {snapshot.working_dir && (
            <div className="repro-meta-row">
              <span className="repro-label">Directory</span>
              <span className="repro-value repro-path">{snapshot.working_dir}</span>
            </div>
          )}
          {snapshot.captured_at && (
            <div className="repro-meta-row">
              <span className="repro-label">Captured</span>
              <span className="repro-value">{snapshot.captured_at}</span>
            </div>
          )}

          {Object.keys(fps).length > 0 && (
            <div className="repro-datasets">
              <div className="repro-sub-header">Datasets</div>
              {Object.entries(fps).map(([name, fp]) => (
                <div key={name} className="repro-dataset-row">
                  <span className="repro-ds-name">{name}</span>
                  <span className="repro-ds-meta">
                    v{fp.version_num} · {fp.nrow?.toLocaleString()} rows
                    {fp.sha256 && fp.sha256 !== '?' && (
                      <span className="repro-hash" title={`SHA-256 prefix: ${fp.sha256}`}>
                        {' '}· <span className="repro-hash-label">sha256:</span>{fp.sha256}
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}

          {pkgEntries.length > 0 && (
            <div className="repro-packages">
              <div className="repro-sub-header">Packages ({pkgEntries.length})</div>
              <div className="repro-pkg-grid">
                {pkgEntries.map(([name, ver]) => (
                  <span key={name} className="repro-pkg"><span className="repro-pkg-name">{name}</span> {ver}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
