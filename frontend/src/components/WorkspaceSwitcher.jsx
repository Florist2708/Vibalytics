import { useState, useEffect } from 'react'

export default function WorkspaceSwitcher({ workspaceId, workspaceName, workspaces, onSwitch, onCreate, onRename, language, onSetLanguage, onOpenStorage }) {
  const [editing, setEditing] = useState(false)
  const [open, setOpen]       = useState(false)
  const [draft, setDraft]     = useState(workspaceName)

  useEffect(() => setDraft(workspaceName), [workspaceName])

  function commitRename() {
    const t = draft.trim()
    if (t && t !== workspaceName) onRename(t)
    setEditing(false)
  }

  return (
    <div className="workspace-switcher">
      <div className="workspace-header">
        {editing ? (
          <input
            className="workspace-name-input"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onBlur={commitRename}
            onKeyDown={e => {
              if (e.key === 'Enter') commitRename()
              if (e.key === 'Escape') { setDraft(workspaceName); setEditing(false) }
            }}
            autoFocus
          />
        ) : (
          <span className="workspace-name" onClick={() => setEditing(true)} title="Click to rename">
            {workspaceName || 'workspace'}
          </span>
        )}
        <div className="lang-toggle" title="Execution language for this workspace">
          <button
            className={`lang-btn${language !== 'python' ? ' active' : ''}`}
            onClick={() => onSetLanguage('r')}
          >R</button>
          <button
            className={`lang-btn${language === 'python' ? ' active' : ''}`}
            onClick={() => onSetLanguage('python')}
          >Py</button>
        </div>
        {onOpenStorage && (
          <button className="workspace-storage-btn" onClick={onOpenStorage} title="Storage & cleanup">⚙</button>
        )}
        <button className="workspace-toggle-btn" onClick={() => setOpen(v => !v)} title="Switch workspace">
          {open ? '▲' : '▼'}
        </button>
      </div>

      {open && (
        <div className="workspace-list">
          {workspaces.map(ws => (
            <div
              key={ws.id}
              className={`workspace-item ${ws.id === workspaceId ? 'active' : ''}`}
              onClick={() => { onSwitch(ws.id); setOpen(false) }}
            >
              <span className="workspace-item-name">{ws.name}</span>
              <span className="workspace-item-date">{ws.updated_at?.slice(0, 10)}</span>
            </div>
          ))}
          <button className="workspace-new-btn" onClick={() => { onCreate(); setOpen(false) }}>
            + New workspace
          </button>
        </div>
      )}
    </div>
  )
}
