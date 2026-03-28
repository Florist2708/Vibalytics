import { useState, useEffect } from 'react'
import { usePanelSize } from '../hooks.js'
import Splitter from './Splitter.jsx'
import FilePanel from './FilePanel.jsx'
import DataTable from './DataTable.jsx'
import VersionHistory from './VersionHistory.jsx'
import CompactChat from './CompactChat.jsx'
import RunCodePane from './RunCodePane.jsx'
import MissingnessPanel from './MissingnessPanel.jsx'

export default function InspectorPage({
  files, inactiveFiles, sessionId, onUpload, onToggleActive, onSuggest, uploading,
  workspaceId, workspaceName, workspaces, onSwitchWorkspace, onCreateWorkspace, onRenameWorkspace,
  onInspect, onDeleteFile,
  archivedFiles, onRestoreFile, onHardDeleteFile,
  inspectorFileIds, activeInspectorFileId, onSetActiveFile, onCloseInspectorFile,
  runs, selectedRunId, onSelectRun,
  messages, streaming, globalError, onSend,
  onEditCode, onBack,
  pendingProposals, rejectedProposals, onAcceptProposal, onRejectProposal, onRestoreProposal,
  onFileReverted,
}) {
  const [showHistory, setShowHistory] = useState(false)
  const [inspFileW,  dragInspFile,  resetInspFile]  = usePanelSize('insp-file',  220, 60, 700)
  const [inspRightW, dragInspRight, resetInspRight] = usePanelSize('insp-right', 360, 80, 900)

  // Reset history view when active file changes
  useEffect(() => setShowHistory(false), [activeInspectorFileId])

  const allProposals = Object.entries(pendingProposals).flatMap(([runId, props]) =>
    props.map(p => ({ ...p, runId }))
  )
  const allRejected = Object.entries(rejectedProposals || {}).flatMap(([runId, props]) =>
    props.map(p => ({ ...p, runId }))
  )

  const activeFile = files.find(f => f.id === activeInspectorFileId)

  return (
    <div className="inspector-layout">
      <FilePanel
        files={files}
        inactiveFiles={inactiveFiles}
        sessionId={sessionId}
        onUpload={onUpload}
        onToggleActive={onToggleActive}
        onSuggest={onSuggest}
        uploading={uploading}
        workspaceId={workspaceId}
        workspaceName={workspaceName}
        workspaces={workspaces}
        onSwitchWorkspace={onSwitchWorkspace}
        onCreateWorkspace={onCreateWorkspace}
        onRenameWorkspace={onRenameWorkspace}
        onInspect={onInspect}
        onDeleteFile={onDeleteFile}
        archivedFiles={archivedFiles}
        onRestoreFile={onRestoreFile}
        onHardDeleteFile={onHardDeleteFile}
        style={{ width: inspFileW }}
      />
      <Splitter onDrag={dragInspFile} onDoubleClick={resetInspFile} />

      <div className="data-pane">
        <div className="data-tabs">
          <div className="data-tabs-list">
            {inspectorFileIds.map(fid => {
              const f = files.find(f => f.id === fid)
              return (
                <button
                  key={fid}
                  className={`data-tab ${fid === activeInspectorFileId ? 'active' : ''}`}
                  onClick={() => onSetActiveFile(fid)}
                >
                  {f?.name || fid.slice(0, 8)}
                  <span
                    className="data-tab-close"
                    onClick={e => { e.stopPropagation(); onCloseInspectorFile(fid) }}
                  >×</span>
                </button>
              )
            })}
          </div>
          <div className="data-tabs-actions">
            {activeInspectorFileId && activeFile?.version_num > 1 && (
              <button
                className={`history-toggle-btn ${showHistory ? 'active' : ''}`}
                onClick={() => setShowHistory(v => !v)}
                title="Version history"
              >
                ⟳ History
              </button>
            )}
            <button className="inspector-back-btn" onClick={onBack} title="Return to analysis mode">
              ← Analysis
            </button>
          </div>
        </div>

        {inspectorFileIds.length === 0 ? (
          <div className="data-pane-empty">Click ⊞ on a file to inspect its data</div>
        ) : activeInspectorFileId ? (
          showHistory ? (
            <VersionHistory
              fileId={activeInspectorFileId}
              sessionId={sessionId}
              onReverted={() => { onFileReverted(); setShowHistory(false) }}
            />
          ) : (
            <DataTable
              sessionId={sessionId}
              fileId={activeInspectorFileId}
              version={activeFile?.version_num}
              onEdited={onFileReverted}
            />
          )
        ) : (
          <div className="data-pane-empty">Select a tab</div>
        )}
      </div>

      <Splitter onDrag={d => dragInspRight(-d)} onDoubleClick={resetInspRight} />
      <div className="inspector-right" style={{ width: inspRightW }}>
        <div className="inspector-chat-section">
          <CompactChat
            messages={messages}
            streaming={streaming}
            globalError={globalError}
            hasData={files.length > 0}
            onSend={onSend}
          />
        </div>

        {allProposals.length > 0 && (
          <div className="inspector-proposals-section">
            <div className="inspector-proposals-title">Pending changes</div>
            <div className="inspector-proposals-list">
              {allProposals.map(prop => (
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
                    <button className="proposal-accept-btn" onClick={() => onAcceptProposal(prop.runId, prop.id)}>
                      ✓ Apply
                    </button>
                    <button className="proposal-reject-btn" onClick={() => onRejectProposal(prop.runId, prop.id)}>
                      ✕ Discard
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {allRejected.length > 0 && (
          <div className="inspector-proposals-section">
            <div className="inspector-proposals-title">Dismissed</div>
            <div className="inspector-proposals-list">
              {allRejected.map(prop => (
                <div key={prop.id} className="rejected-proposal-row">
                  <span className="rejected-varname">{prop.var_name}</span>
                  {prop.description && <span className="rejected-desc">{prop.description}</span>}
                  <button
                    className="proposal-restore-btn"
                    onClick={() => onRestoreProposal(prop.runId, prop.id)}
                    title="Restore this proposal"
                  >↩ Restore</button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="inspector-code-section">
          <RunCodePane
            runs={runs}
            selectedRunId={selectedRunId}
            onSelectRun={onSelectRun}
            onEditCode={onEditCode}
          />
        </div>
        {activeInspectorFileId && (
          <MissingnessPanel fileId={activeInspectorFileId} />
        )}
      </div>
    </div>
  )
}
