import { useState, useRef } from 'react'
import WorkspaceSwitcher from './WorkspaceSwitcher.jsx'
import FileCard from './FileCard.jsx'
import JoinModal from './JoinModal.jsx'
import JoinDiscovery from './JoinDiscovery.jsx'
import WorkflowsSection from './WorkflowsSection.jsx'
import ImportDataModal from './ImportDataModal.jsx'

export default function FilePanel({
  files, inactiveFiles, sessionId, onUpload, onToggleActive, onSuggest, uploading,
  workspaceId, workspaceName, workspaces, onSwitchWorkspace, onCreateWorkspace, onRenameWorkspace,
  workspaceLang, onSetLanguage,
  onInspect, onDeleteFile, onSaveNotes,
  archivedFiles, onRestoreFile, onHardDeleteFile,
  workflows, onRunWorkflow, onDeleteWorkflow, streaming,
  onFilesChanged, onOpenStorage,
  style,
}) {
  const inputRef = useRef()
  const [showArchived, setShowArchived] = useState(false)
  const [joinOpen, setJoinOpen]         = useState(false)
  const [joinInitial, setJoinInitial]   = useState(null)
  const [importOpen, setImportOpen]     = useState(false)

  function openJoin(hint) { setJoinInitial(hint || null); setJoinOpen(true) }
  function closeJoin()    { setJoinOpen(false); setJoinInitial(null) }

  return (
    <aside className="sidebar" style={style}>
      <WorkspaceSwitcher
        workspaceId={workspaceId}
        workspaceName={workspaceName}
        workspaces={workspaces}
        onSwitch={onSwitchWorkspace}
        onCreate={onCreateWorkspace}
        onRename={onRenameWorkspace}
        language={workspaceLang}
        onSetLanguage={onSetLanguage}
        onOpenStorage={onOpenStorage}
      />

      <div className="sidebar-title">Data</div>

      {files.length === 0 ? (
        <>
        <div
          className="drop-zone"
          onDragOver={e => e.preventDefault()}
          onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) onUpload(f) }}
          onClick={() => inputRef.current.click()}
        >
          {uploading ? 'Loading…' : <>Drop file here<br /><span>or click to browse</span></>}
          <small>CSV · Excel · Stata · RDS</small>
        </div>
        <button className="add-btn import-open-btn" style={{marginTop: 6}} onClick={() => setImportOpen(true)}>
          ↓ Import from path / URL
        </button>
        </>
      ) : (
        <>
          {files.map(f => (
            <FileCard
              key={f.name}
              file={f}
              sessionId={sessionId}
              workspaceId={workspaceId}
              active={!inactiveFiles.has(f.name)}
              onToggleActive={() => onToggleActive(f.name)}
              onSuggest={onSuggest}
              onInspect={onInspect}
              onDeleteFile={onDeleteFile}
              onSaveNotes={onSaveNotes}
            />
          ))}
          <div className="file-btns-row">
            <button className="add-btn" onClick={() => inputRef.current.click()} disabled={uploading}>
              {uploading ? 'Loading…' : '+ Add file'}
            </button>
            <button className="add-btn import-open-btn" onClick={() => setImportOpen(true)}
              title="Import from a local file path or URL (for large files)">
              ↓ Import
            </button>
            {files.length >= 2 && (
              <button className="add-btn join-open-btn" onClick={() => openJoin(null)}
                title="Join two datasets">
                ⋈ Join
              </button>
            )}
          </div>
          <JoinDiscovery files={files} onOpenJoin={openJoin} />
        </>
      )}

      {(archivedFiles || []).length > 0 && (
        <div className="archived-section">
          <div className="archived-header" onClick={() => setShowArchived(v => !v)}>
            {showArchived ? '▾' : '▸'} Archived ({archivedFiles.length})
          </div>
          {showArchived && archivedFiles.map(f => (
            <div key={f.id} className="archived-file-row">
              <span className="archived-file-name">{f.name}</span>
              <button onClick={() => onRestoreFile(f.id)} title="Restore" className="restore-btn">↩</button>
              <button onClick={() => onHardDeleteFile(f.id)} title="Delete permanently" className="hard-delete-btn">✕</button>
            </div>
          ))}
        </div>
      )}

      <input
        ref={inputRef} type="file" accept=".csv,.xlsx,.xls,.dta,.rds" style={{ display: 'none' }}
        onChange={e => { if (e.target.files[0]) onUpload(e.target.files[0]); e.target.value = '' }}
      />

      <WorkflowsSection
        workflows={workflows || []}
        onRun={onRunWorkflow}
        onDelete={onDeleteWorkflow}
        streaming={streaming}
      />

      {joinOpen && (
        <JoinModal
          files={files}
          sessionId={sessionId}
          workspaceId={workspaceId}
          initialHint={joinInitial}
          onSave={() => onFilesChanged && onFilesChanged()}
          onClose={closeJoin}
        />
      )}

      {importOpen && (
        <ImportDataModal
          workspaceId={workspaceId}
          sessionId={sessionId}
          files={files}
          onClose={() => setImportOpen(false)}
          onSuccess={() => onFilesChanged && onFilesChanged()}
        />
      )}
    </aside>
  )
}
