import { useState } from 'react'
import ErrorBlock from './ErrorBlock.jsx'
import TableArtifact from './TableArtifact.jsx'
import TraceSection from './TraceSection.jsx'
import ContextSection from './ContextSection.jsx'
import ReproSection from './ReproSection.jsx'
import RunProvenance from './RunProvenance.jsx'
import ProposalDiff from './ProposalDiff.jsx'
import { scrollToRun } from '../utils.js'

export default function RunCard({ run, proposals, rejectedProposals, onEditCode, onAcceptProposal, onRejectProposal, onRestoreProposal, onSaveWorkflow, onRerun, streaming, parentRun, childRuns, files }) {
  const [showCode, setShowCode] = useState(true)
  const [showAnalysis, setShowAnalysis] = useState(false)
  const [localCode, setLocalCode] = useState(null)
  const displayCode = localCode ?? run.editedCode ?? run.code ?? ''
  return (
    <div id={`run-${run.id}`} className={`run-card ${run.streaming ? 'streaming' : ''}`}>
      {parentRun && (
        <div className="run-chain-parent">
          ↩ rerun of{' '}
          <button className="run-chain-link" onClick={() => scrollToRun(parentRun.id)}>
            {parentRun.prompt?.slice(0, 60)}{parentRun.prompt?.length > 60 ? '…' : ''}
          </button>
        </div>
      )}
      {run.prompt && (
        <div className="run-prompt">
          {run.prompt}
          {run.version > 1 && <span className="version-badge">v{run.version}</span>}
          {run.jobStatus === 'running' && <span className="job-status-badge running">⚡ running</span>}
          {run.jobStatus === 'error'   && <span className="job-status-badge error">⚡ failed</span>}
          {run.jobStatus === 'done' && !run.streaming && <span className="job-status-badge done">⚡ bg</span>}
          {run.language && run.language !== 'r' && (
            <span className="run-lang-badge">{run.language}</span>
          )}
        </div>
      )}

      {run.installingPkg && (
        <div className="run-installing">⚙ Installing <strong>{run.installingPkg}</strong>…</div>
      )}
      {run.installedPkgs?.length > 0 && !run.streaming && (
        <div className="run-installed">📦 Installed: {run.installedPkgs.join(', ')}</div>
      )}

      {run.summary && !run.streaming && (
        <div className="run-summary">{run.summary}</div>
      )}

      {run.plots.map((src, i) => (
        <img key={i} className="plot-img" src={src} alt="plot" />
      ))}

      {(run.tables || []).map((t, i) => <TableArtifact key={i} table={t} />)}

      {run.output && <pre className="output-block">{run.output}</pre>}

      {(run.exports || []).length > 0 && (
        <div className="artifact-list">
          {run.exports.map((exp, i) => (
            <a key={i} className="artifact-download"
              href={`/artifact/${exp.artifact_id}`} download={exp.filename}>
              ↓ {exp.filename}
            </a>
          ))}
        </div>
      )}

      {run.retryError && (
        <details className="retry-error-details">
          <summary className="retry-error-summary">
            ⚠ First attempt failed — {run.errors.length === 0 ? 'retried successfully' : 'retry also failed'} · show error
          </summary>
          <ErrorBlock raw={run.retryError} />
        </details>
      )}
      {run.stopped && run.errors.length === 0 && (
        <div className="run-stopped-badge">⬛ Stopped</div>
      )}
      {run.errors.map((e, i) => <ErrorBlock key={i} raw={e} />)}
      <TraceSection trace={run.trace} />
      <ContextSection contextSnapshot={run.contextSnapshot} />
      <ReproSection runId={run.id} snapshot={run.envSnapshot} />

      {proposals?.length > 0 && (
        <div className="proposals-section">
          {proposals.map(prop => (
            <div key={prop.id} className="proposal-card">
              <div className="proposal-header">
                <strong className="proposal-varname">{prop.var_name}</strong>
                {prop.description && <span className="proposal-desc">{prop.description}</span>}
              </div>
              {prop.nrow_before != null && (
                <div className="proposal-delta">
                  {prop.nrow_before.toLocaleString()} → {prop.nrow_after.toLocaleString()} rows
                  <span className={prop.nrow_after >= prop.nrow_before ? 'delta-neutral' : 'delta-loss'}>
                    {' '}({prop.nrow_after - prop.nrow_before > 0 ? '+' : ''}{(prop.nrow_after - prop.nrow_before).toLocaleString()})
                  </span>
                </div>
              )}
              <div className="proposal-actions">
                <button className="proposal-accept-btn" onClick={() => onAcceptProposal(run.id, prop.id)}>
                  ✓ Apply
                </button>
                <button className="proposal-reject-btn" onClick={() => onRejectProposal(run.id, prop.id)}>
                  ✕ Discard
                </button>
                <ProposalDiff runId={run.id} propId={prop.id} />
              </div>
            </div>
          ))}
        </div>
      )}

      {rejectedProposals?.length > 0 && (
        <div className="rejected-proposals-section">
          <div className="rejected-proposals-label">Dismissed</div>
          {rejectedProposals.map(prop => (
            <div key={prop.id} className="rejected-proposal-row">
              <span className="rejected-varname">{prop.var_name}</span>
              {prop.description && <span className="rejected-desc">{prop.description}</span>}
              <button
                className="proposal-restore-btn"
                onClick={() => onRestoreProposal(run.id, prop.id)}
                title="Restore this proposal"
              >↩ Restore</button>
            </div>
          ))}
        </div>
      )}

      {!run.streaming && (
        <RunProvenance
          activeFileVersions={run.activeFileVersions}
          producedVersions={run.producedVersions}
          files={files}
        />
      )}
      {!run.streaming && run.agentText && (
        <div className="run-analysis-section">
          <button className="run-analysis-toggle" onClick={() => setShowAnalysis(v => !v)}>
            {showAnalysis ? '⌃ Analysis' : '⌄ Analysis'}
          </button>
          {showAnalysis && (
            <div className="run-analysis-body">{run.agentText}</div>
          )}
        </div>
      )}

      {!run.streaming && run.code && (
        <>
          <div className="run-code-toggle-row">
            <button className="run-code-toggle-btn" onClick={() => setShowCode(v => !v)} title={showCode ? 'Hide code' : 'Show code'}>
              {showCode ? '⌃ Code' : '⌄ Code'}
            </button>
            {showCode && localCode != null && localCode !== (run.editedCode ?? run.code) && (
              <button className="run-code-rerun-btn" disabled={streaming} onClick={() => { onRerun(run.id, localCode) }} title="Rerun with this code">
                ↺ Rerun
              </button>
            )}
            <button className="run-save-wf-btn" onClick={() => onSaveWorkflow(run.id)} title="Save as workflow">★</button>
            <a className="run-code-dl-link" href={`/run/${run.id}/clean_script`} download
               title="Download clean standalone script">↓ script</a>
            <a className="run-code-dl-link" href={`/run/${run.id}/notebook`} download
               title={run.language === 'python' ? 'Download Jupyter notebook (.ipynb)' : 'Download R Markdown (.Rmd)'}>↓ notebook</a>
            <button className="run-code-btn" onClick={() => onEditCode(run)} title="Open code editor">
              &lt;/&gt;
            </button>
          </div>
          {showCode && (
            <textarea
              className="run-code-inline"
              value={displayCode}
              onChange={e => setLocalCode(e.target.value)}
              spellCheck={false}
            />
          )}
        </>
      )}
      {!run.streaming && childRuns?.length > 0 && (
        <div className="run-chain-children">
          ↪ reruns:{' '}
          {childRuns.map(cr => (
            <button key={cr.id} className="run-chain-link" onClick={() => scrollToRun(cr.id)}>
              v{cr.version}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
