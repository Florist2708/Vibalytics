import { useState } from 'react'
import { usePanelSize } from '../hooks.js'
import Splitter from './Splitter.jsx'
import CompactChat from './CompactChat.jsx'

export default function CodeEditorPage({
  target, runs, streaming, onBack, onSave, onRerun,
  messages, globalError, hasData, onSend,
}) {
  const run = runs.find(r => r.id === target.runId)
  const [draft, setDraft]         = useState(target.code)
  const [savedCode, setSavedCode] = useState(target.code)
  const [copied, setCopied]       = useState(false)
  const [saving, setSaving]       = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [editorChatW, dragEditorChat, resetEditorChat] = usePanelSize('editor-chat', 300, 80, 900)

  const isDirty = draft !== savedCode
  const isTemp  = target.runId?.startsWith('temp_')
  const version = run?.version || target.version || 1
  const prompt  = run?.prompt  || target.prompt  || ''

  function copy() {
    navigator.clipboard.writeText(draft)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  async function handleSave() {
    if (!target.runId || isTemp) return
    setSaving(true)
    setSaveError(null)
    try {
      await onSave(target.runId, draft)
      setSavedCode(draft)
    } catch (e) {
      setSaveError(e.message)
    } finally {
      setSaving(false)
    }
  }

  function handleRerun() {
    onRerun(target.runId, draft)
  }

  return (
    <div className="editor-page">
      <div className="editor-header">
        <button className="editor-back" onClick={onBack}>← Results</button>
        <div className="editor-meta">
          {prompt && <span className="editor-prompt" title={prompt}>{prompt}</span>}
          {version > 1 && <span className="version-badge">v{version}</span>}
          {isDirty && <span className="editor-unsaved">unsaved</span>}
        </div>
        <div className="editor-actions">
          {!isTemp && target.runId && (
            <>
              <a className="editor-link" href={`/run/${target.runId}/clean_script`} download title="Clean standalone script">↓ script</a>
              <a className="editor-link" href={`/run/${target.runId}/notebook`} download title={run?.language === 'python' ? 'Jupyter .ipynb' : 'R Markdown .Rmd'}>↓ notebook</a>
              <a className="editor-link" href={`/run/${target.runId}/script`} download title="Raw script (with markers)">↓ raw</a>
              {run?.output && <a className="editor-link" href={`/run/${target.runId}/log`} download>↓ log</a>}
            </>
          )}
          <button onClick={copy}>{copied ? 'Copied!' : 'Copy'}</button>
          <button onClick={handleSave} disabled={!isDirty || isTemp || !target.runId || saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button className="editor-rerun-btn" onClick={handleRerun} disabled={streaming}>↺ Rerun</button>
        </div>
      </div>
      {saveError && <div className="global-error">{saveError}</div>}
      <div className="editor-body">
        <textarea
          className="editor-textarea"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          spellCheck={false}
          autoFocus
          onKeyDown={e => {
            if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); handleSave() }
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); handleRerun() }
          }}
        />
        <Splitter onDrag={d => dragEditorChat(-d)} onDoubleClick={resetEditorChat} />
        <div className="editor-chat-panel" style={{ width: editorChatW }}>
          <CompactChat
            messages={messages}
            streaming={streaming}
            globalError={globalError}
            hasData={hasData}
            onSend={onSend}
          />
        </div>
      </div>
    </div>
  )
}
