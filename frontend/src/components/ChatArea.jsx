import { useState, useRef, useEffect } from 'react'
import { uploadChatAttachment } from '../api.js'
import AgentPicker from './AgentPicker.jsx'

export default function ChatArea({
  messages, streaming, globalError, hasData,
  onSend, onStop, onHistory, runCount,
  workspaceId, autoApprove, onToggleAutoApprove,
  agentConfig, onAgentChange,
}) {
  const [inputText, setInputText]     = useState('')
  const [bgMode, setBgMode]           = useState(false)
  const [attachments, setAttachments] = useState([])   // {id, filename, mime_type, preview}
  const [uploading, setUploading]     = useState(false)
  const bottomRef  = useRef()
  const fileInputRef = useRef()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function handleSend(text) {
    const t = (text || inputText).trim()
    if (!t || streaming) return
    onSend(t, bgMode, attachments.map(a => a.id))
    setInputText('')
    setAttachments([])
  }

  async function handleFiles(files) {
    if (!workspaceId || files.length === 0) return
    setUploading(true)
    for (const file of Array.from(files)) {
      try {
        const info = await uploadChatAttachment(workspaceId, file)
        const preview = file.type.startsWith('image/') ? URL.createObjectURL(file) : null
        setAttachments(prev => [...prev, { id: info.id, filename: info.filename || file.name, mime_type: info.mime_type, preview }])
      } catch (e) {
        console.error('Attachment upload failed:', e)
      }
    }
    setUploading(false)
  }

  function handlePaste(e) {
    const items = e.clipboardData?.items
    if (!items) return
    const imageFiles = Array.from(items)
      .filter(i => i.kind === 'file' && i.type.startsWith('image/'))
      .map(i => i.getAsFile())
      .filter(Boolean)
    if (imageFiles.length > 0) {
      e.preventDefault()
      handleFiles(imageFiles)
    }
  }

  function handleDrop(e) {
    e.preventDefault()
    const files = e.dataTransfer?.files
    if (files?.length) handleFiles(files)
  }

  function removeAttachment(id) {
    setAttachments(prev => prev.filter(a => a.id !== id))
  }

  const canSend = !streaming && (inputText.trim() || attachments.length > 0)

  return (
    <main className="chat-area">
      <header className="chat-header">
        <span className="brand">vibalytics</span>
        {streaming && <span className="spinner" />}
        {streaming && (
          <button className="stop-btn" onClick={onStop} title="Stop the running agent">■ Stop</button>
        )}
        {runCount > 0 && (
          <button className="history-nav-btn" onClick={onHistory} title="View run history">
            ⟳ History ({runCount})
          </button>
        )}
        <button
          className={`auto-approve-btn${autoApprove ? ' active' : ''}`}
          onClick={onToggleAutoApprove}
          title={autoApprove
            ? 'Auto-approve ON — agent applies data changes without asking. Click to disable.'
            : 'Auto-approve OFF — agent will ask before modifying data. Click to enable dangerous mode.'}
        >
          {autoApprove ? '⚡ Auto' : '⚡'}
        </button>
        {agentConfig && (
          <AgentPicker config={agentConfig} onConfigChange={onAgentChange} />
        )}
      </header>

      <div className="messages">
        {!hasData && messages.length === 0 && attachments.length === 0 && (
          <div className="empty-hint">Upload a dataset to get started</div>
        )}

        {messages.map((msg, i) => (
          msg.role === 'user'
            ? <div key={i} className="msg user"><span>{msg.content}</span></div>
            : msg.text
              ? <div key={i} className="msg assistant"><p className="msg-text">{msg.text}</p></div>
              : null
        ))}

        <div ref={bottomRef} />
      </div>

      {globalError && <div className="global-error">{globalError}</div>}

      {attachments.length > 0 && (
        <div className="attachment-chips">
          {attachments.map(a => (
            <div key={a.id} className="attachment-chip">
              {a.preview
                ? <img src={a.preview} className="attachment-thumb" alt="" />
                : <span className="attachment-icon">{a.mime_type === 'application/pdf' ? '📄' : '📎'}</span>
              }
              <span className="attachment-name">{a.filename}</span>
              <button className="attachment-remove" onClick={() => removeAttachment(a.id)}>×</button>
            </div>
          ))}
          {uploading && <span className="attachment-uploading">uploading…</span>}
        </div>
      )}

      <div
        className="input-row"
        onDrop={handleDrop}
        onDragOver={e => e.preventDefault()}
      >
        <button
          className="attach-btn"
          onClick={() => fileInputRef.current?.click()}
          disabled={streaming}
          title="Attach image or PDF"
        >📎</button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*,.pdf"
          multiple
          style={{ display: 'none' }}
          onChange={e => { handleFiles(e.target.files); e.target.value = '' }}
        />
        <textarea
          className="chat-input"
          value={inputText}
          onChange={e => setInputText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }}
          onPaste={handlePaste}
          placeholder="Ask anything… or upload / drop a file"
          rows={1}
          disabled={streaming}
        />
        <button
          className="send-btn"
          onClick={() => handleSend()}
          disabled={!canSend}
        >↑</button>
        <button
          className={`bg-mode-btn${bgMode ? ' active' : ''}`}
          onClick={() => setBgMode(v => !v)}
          title={bgMode ? 'Background mode on — job runs async, you stay unblocked' : 'Click to enable background mode'}
          disabled={!hasData}
        >⚡</button>
      </div>
    </main>
  )
}
