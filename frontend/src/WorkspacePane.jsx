import { useState, useEffect } from 'react'
import {
  API, STORAGE_KEY,
  createSession, uploadFile, fetchContext, fetchWorkspaceList,
  patchWorkspace, renameWorkspace, fetchWorkflows, createWorkflow,
  apiDeleteWorkflow, fetchPersistedRuns, saveEditedCode,
  streamSSE, streamSSEGet, stopRun, fetchConfig,
} from './api.js'

import { scrollToRun, stripCodeBlocks } from './utils.js'
import { usePanelSize } from './hooks.js'
import Splitter from './components/Splitter.jsx'
import FilePanel from './components/FilePanel.jsx'
import ResultsPanel from './components/ResultsPanel.jsx'
import ChatArea from './components/ChatArea.jsx'
import CodeEditorPage from './components/CodeEditorPage.jsx'
import HistoryPage from './components/HistoryPage.jsx'
import StoragePage from './components/StoragePage.jsx'
import InspectorPage from './components/InspectorPage.jsx'

async function requireOk(response, fallback) {
  if (response.ok) return response
  const payload = await response.json().catch(() => ({}))
  throw new Error(payload.detail || fallback)
}

function mkTab(id, n) {
  return { id, label: `Chat ${n}`, messages: [], runs: [], streaming: false, pendingProposals: {}, rejectedProposals: {}, pendingSuggestion: null }
}

export default function WorkspacePane() {
  const storageKey = STORAGE_KEY
  const [sessionId, setSessionId]         = useState(null)
  const [workspaceName, setWorkspaceName] = useState('workspace')
  const [workspaceLang, setWorkspaceLang] = useState('r')
  const [workspaces, setWorkspaces]       = useState([])
  const [files, setFiles]                 = useState([])
  const [inactiveFiles, setInactiveFiles] = useState(new Set())
  const [uploading, setUploading]         = useState(false)
  const [globalError, setGlobalError]     = useState(null)

  const [workflows, setWorkflows]       = useState([])
  const [archivedFiles, setArchivedFiles] = useState([])
  const [autoApprove, setAutoApprove]   = useState(false)
  const [agentConfig, setAgentConfig]   = useState(null)

  // Workspace-level overlay modes
  const [mode, setMode]                               = useState('analysis')
  const [inspectorFileIds, setInspectorFileIds]       = useState([])
  const [activeInspectorFileId, setActiveInspectorFileId] = useState(null)
  const [selectedRunId, setSelectedRunId]             = useState(null)
  const [editMode, setEditMode]                       = useState(null)

  // ── Per-chat-tab state ────────────────────────────────────────────────────
  const [chatTabs, setChatTabs]           = useState(() => [mkTab('ct0', 1)])
  const [activeChatTabId, setActiveChatTabId] = useState('ct0')

  const activeTab  = chatTabs.find(t => t.id === activeChatTabId) || chatTabs[0]
  const anyStreaming = chatTabs.some(t => t.streaming)

  function tabSetters(tabId) {
    const upd = (field, val) => setChatTabs(prev => prev.map(t =>
      t.id === tabId ? { ...t, [field]: typeof val === 'function' ? val(t[field]) : val } : t
    ))
    return {
      setMessages:          v => upd('messages', v),
      setRuns:              v => upd('runs', v),
      setStreaming:         v => upd('streaming', v),
      setPendingProposals:  v => upd('pendingProposals', v),
      setRejectedProposals: v => upd('rejectedProposals', v),
      setPendingSuggestion: v => upd('pendingSuggestion', v),
    }
  }

  function addChatTab() {
    const id = `ct${Date.now()}`
    setChatTabs(prev => [...prev, mkTab(id, prev.length + 1)])
    setActiveChatTabId(id)
  }

  function removeChatTab(id) {
    setChatTabs(prev => {
      if (prev.length <= 1) return prev
      const next = prev.filter(t => t.id !== id)
      if (id === activeChatTabId) setActiveChatTabId(next[next.length - 1].id)
      return next
    })
  }

  // Load workspace list whenever session changes
  useEffect(() => {
    if (sessionId) fetchWorkspaceList().then(setWorkspaces).catch(e => setGlobalError(e.message))
  }, [sessionId])

  useEffect(() => {
    const stored = localStorage.getItem(storageKey)
    initSession(stored)
    fetchConfig().then(setAgentConfig).catch(e => setGlobalError(e.message))
    // initSession reads the initial persisted workspace; later workspace changes
    // go through switchWorkspace and must not retrigger boot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (activeTab.pendingSuggestion && sessionId && files.length > 0 && !activeTab.streaming) {
      const text = activeTab.pendingSuggestion
      tabSetters(activeChatTabId).setPendingSuggestion(null)
      handleSend(text, false, activeChatTabId)
    }
    // handleSend changes with workspace state; the explicit state dependencies
    // below are the conditions that should release a queued suggestion.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab.pendingSuggestion, activeChatTabId, sessionId, files.length, activeTab.streaming])

  async function initSession(storedId) {
    fetchWorkflows().then(setWorkflows).catch(e => setGlobalError(e.message))
    if (storedId) {
      try {
        const ctx = await fetchContext(storedId)
        setSessionId(storedId)
        setWorkspaceName(ctx.workspace_name || 'workspace')
        setWorkspaceLang(ctx.language || 'r')
        setAutoApprove(ctx.auto_approve || false)
        if (ctx.files?.length) {
          setFiles(ctx.files)
          setInactiveFiles(new Set())
        }
        if (ctx.archived_files) setArchivedFiles(ctx.archived_files)
        if (ctx.run_count > 0) {
          const s = tabSetters('ct0')
          fetchPersistedRuns(storedId)
            .then(({ runs, messages, pendingProposals, rejectedProposals }) => {
              s.setRuns(runs)
              s.setMessages(messages)
              if (Object.keys(pendingProposals).length) s.setPendingProposals(pendingProposals)
              if (Object.keys(rejectedProposals).length) s.setRejectedProposals(rejectedProposals)
            })
            .catch(e => setGlobalError(e.message))
        }
        return
      } catch {
        localStorage.removeItem(storageKey)
      }
    }
    try {
      const id = await createSession()
      localStorage.setItem(storageKey, id)
      setSessionId(id)
      setWorkspaceName('workspace')
    } catch (e) {
      setGlobalError(e.message)
    }
  }

  function resetChatTabs() {
    setChatTabs([mkTab('ct0', 1)])
    setActiveChatTabId('ct0')
  }

  async function switchWorkspace(wsId) {
    if (wsId === sessionId) return
    setFiles([])
    setInactiveFiles(new Set())
    setMode('analysis')
    setInspectorFileIds([])
    setActiveInspectorFileId(null)
    setSelectedRunId(null)
    resetChatTabs()
    localStorage.setItem(storageKey, wsId)
    await initSession(wsId)
  }

  async function handleCreateWorkspace() {
    try {
      const id = await createSession()
      localStorage.setItem(storageKey, id)
      setFiles([])
      setInactiveFiles(new Set())
      setMode('analysis')
      setInspectorFileIds([])
      setActiveInspectorFileId(null)
      setSelectedRunId(null)
      resetChatTabs()
      setSessionId(id)
      setWorkspaceName('workspace')
      setWorkspaceLang('r')
      fetchWorkspaceList().then(setWorkspaces).catch(e => setGlobalError(e.message))
    } catch (e) {
      setGlobalError(e.message)
    }
  }

  async function handleRenameWorkspace(name) {
    if (!sessionId) return
    try {
      await renameWorkspace(sessionId, name)
      setWorkspaceName(name)
      setWorkspaces(await fetchWorkspaceList())
    } catch (e) {
      setGlobalError(e.message)
      throw e
    }
  }

  async function handleSetLanguage(lang) {
    if (!sessionId) return
    try {
      await patchWorkspace(sessionId, { language: lang })
      setWorkspaceLang(lang)
    } catch (e) {
      setGlobalError(e.message)
    }
  }

  function handleStop() {
    if (!sessionId) return
    stopRun(sessionId).catch(e => setGlobalError(e.message))
  }

  async function handleToggleAutoApprove() {
    if (!sessionId) return
    const next = !autoApprove
    try {
      await patchWorkspace(sessionId, { auto_approve: next })
      setAutoApprove(next)
    } catch (e) {
      setGlobalError(e.message)
    }
  }

  async function handleUpload(file) {
    if (!sessionId) return
    setUploading(true)
    setGlobalError(null)
    try {
      const result = await uploadFile(sessionId, file)
      setFiles(prev => [...prev.filter(f => f.name !== result.name), {
        id:                  result.id,
        name:                result.name,
        nrow:                result.nrow,
        schema:              result.schema,
        stats:               result.stats || {},
        version_num:         result.version_num || 1,
        current_version_seq: result.current_version_seq || 1,
      }])
    } catch (e) {
      setGlobalError(e.message)
    } finally {
      setUploading(false)
    }
  }

  function toggleFileActive(name) {
    setInactiveFiles(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  function handleInspect(fileId) {
    setInspectorFileIds(prev => prev.includes(fileId) ? prev : [...prev, fileId])
    setActiveInspectorFileId(fileId)
    setMode('inspect')
  }

  function handleCloseInspectorFile(fileId) {
    const newIds = inspectorFileIds.filter(id => id !== fileId)
    setInspectorFileIds(newIds)
    if (activeInspectorFileId === fileId) {
      setActiveInspectorFileId(newIds[0] || null)
    }
  }

  async function handleDeleteFile(fileId) {
    const file = files.find(f => f.id === fileId)
    if (!file) return
    if (!window.confirm(`Remove "${file.name}" from this workspace?`)) return
    try {
      const response = await fetch(`${API}/workspace/${sessionId}/file/${fileId}`, { method: 'DELETE' })
      await requireOk(response, 'Failed to remove file')
      setFiles(prev => prev.filter(f => f.id !== fileId))
      const newIds = inspectorFileIds.filter(id => id !== fileId)
      setInspectorFileIds(newIds)
      if (activeInspectorFileId === fileId) {
        setActiveInspectorFileId(newIds[0] || null)
      }
    } catch (e) {
      setGlobalError(e.message)
    }
  }

  async function handleRestoreFile(fileId) {
    try {
      const response = await fetch(
        `${API}/workspace/${sessionId}/file/${fileId}/restore?session_id=${sessionId}`,
        { method: 'POST' },
      )
      await requireOk(response, 'Failed to restore file')
      const ctx = await fetchContext(sessionId)
      if (ctx.files) setFiles(ctx.files)
      if (ctx.archived_files) setArchivedFiles(ctx.archived_files)
    } catch (e) {
      setGlobalError(e.message)
    }
  }

  async function handleHardDeleteFile(fileId) {
    if (!window.confirm('Permanently delete this file? This cannot be undone.')) return
    try {
      const response = await fetch(`${API}/workspace/${sessionId}/file/${fileId}/hard`, { method: 'DELETE' })
      await requireOk(response, 'Failed to permanently delete file')
      setArchivedFiles(prev => prev.filter(f => f.id !== fileId))
    } catch (e) {
      setGlobalError(e.message)
    }
  }

  async function handleSaveNotes(fileId, notes) {
    try {
      const response = await fetch(`${API}/workspace/${sessionId}/file/${fileId}/notes`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes }),
      })
      await requireOk(response, 'Failed to save notes')
      setFiles(prev => prev.map(f => f.id === fileId ? { ...f, notes } : f))
    } catch (e) {
      setGlobalError(e.message)
      throw e
    }
  }

  async function handleAcceptProposal(runId, proposalId, tabId = activeChatTabId) {
    const s = tabSetters(tabId)
    try {
      const res = await fetch(`${API}/run/${runId}/accept_version/${proposalId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setGlobalError(err.detail || 'Failed to apply version')
        return
      }
      s.setPendingProposals(prev => {
        const updated = { ...prev }
        if (updated[runId]) updated[runId] = updated[runId].filter(p => p.id !== proposalId)
        return updated
      })
      fetchContext(sessionId).then(ctx => {
        if (ctx.files) setFiles(ctx.files)
        if (ctx.archived_files) setArchivedFiles(ctx.archived_files)
      }).catch(e => setGlobalError(e.message))
      fetch(`${API}/run/${runId}`).then(r => requireOk(r, 'Failed to refresh run')).then(r => r.json()).then(r => {
        s.setRuns(prev => prev.map(run => run.id === runId
          ? { ...run, producedVersions: r.produced_versions || [] }
          : run
        ))
      }).catch(e => setGlobalError(e.message))
    } catch (e) {
      setGlobalError(e.message)
    }
  }

  async function handleRejectProposal(runId, proposalId, tabId = activeChatTabId) {
    const tab = chatTabs.find(t => t.id === tabId) || activeTab
    const prop = (tab.pendingProposals[runId] || []).find(p => p.id === proposalId)
    const s = tabSetters(tabId)
    try {
      const response = await fetch(`${API}/run/${runId}/reject_version/${proposalId}`, { method: 'POST' })
      await requireOk(response, 'Failed to reject proposal')
    } catch (e) {
      setGlobalError(e.message)
      return
    }
    s.setPendingProposals(prev => {
      const updated = { ...prev }
      if (updated[runId]) updated[runId] = updated[runId].filter(p => p.id !== proposalId)
      return updated
    })
    if (prop) {
      s.setRejectedProposals(prev => ({
        ...prev,
        [runId]: [...(prev[runId] || []), prop],
      }))
    }
  }

  async function handleRestoreProposal(runId, proposalId, tabId = activeChatTabId) {
    const tab = chatTabs.find(t => t.id === tabId) || activeTab
    const prop = (tab.rejectedProposals[runId] || []).find(p => p.id === proposalId)
    const s = tabSetters(tabId)
    try {
      const res = await fetch(`${API}/run/${runId}/restore_version/${proposalId}`, { method: 'POST' })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setGlobalError(err.detail || 'Failed to restore proposal')
        return
      }
    } catch (e) {
      setGlobalError(e.message)
      return
    }
    s.setRejectedProposals(prev => {
      const updated = { ...prev }
      if (updated[runId]) updated[runId] = updated[runId].filter(p => p.id !== proposalId)
      return updated
    })
    if (prop) {
      s.setPendingProposals(prev => ({
        ...prev,
        [runId]: [...(prev[runId] || []), prop],
      }))
    }
  }

  async function handleSaveWorkflow(runId) {
    const name = window.prompt('Save as workflow — enter a name:')?.trim()
    if (!name) return
    try {
      const wf = await createWorkflow(name, runId)
      setWorkflows(prev => [wf, ...prev])
    } catch (e) {
      setGlobalError(e.message)
    }
  }

  async function handleDeleteWorkflow(id) {
    try {
      await apiDeleteWorkflow(id)
      setWorkflows(prev => prev.filter(w => w.id !== id))
    } catch (e) {
      setGlobalError(`Failed to delete workflow: ${e.message}`)
    }
  }

  function handleRunWorkflow(workflowId) {
    if (!sessionId || anyStreaming) return

    const wf = workflows.find(w => w.id === workflowId)
    if (!wf) return

    // Validate required datasets are present in current workspace
    if (wf.input_vars?.length) {
      const currentVars = new Set(files.map(f => f.name))
      const missing = wf.input_vars.filter(v => !currentVars.has(v))
      if (missing.length > 0) {
        setGlobalError(`Workflow "${wf.name}" requires: ${missing.join(', ')} — not in this workspace`)
        return
      }
    }

    const tabId = activeChatTabId
    const s = tabSetters(tabId)
    setGlobalError(null)
    s.setStreaming(true)
    const tempId = `temp_${Date.now()}`
    const runRef = { tempId, serverRunIdRef: { current: null }, agentText: '', addedPlaceholder: false }

    s.setRuns(prev => [...prev, {
      id: tempId,
      prompt: `[workflow] ${wf?.name || ''}`,
      code: null, editedCode: '', output: null,
      plots: [], tables: [], exports: [], errors: [], trace: [], retryError: null,
      streaming: true, editing: false, version: 1, parentId: null, stopped: false, installingPkg: null, installedPkgs: [],
    }])
    streamSSE(`${API}/workflows/${workflowId}/run`, { session_id: sessionId },
      ev => applySSEEvent(ev, runRef, s)
    )
  }

  // Shared SSE event handler — used by chat, rerun, and workflow.
  // setters: tab-scoped { setRuns, setMessages, setStreaming, setPendingProposals }
  function applySSEEvent(ev, runRef, { setRuns, setMessages, setStreaming, setPendingProposals }) {
    const { tempId, serverRunIdRef } = runRef
    const appendRunError = content => setRuns(prev => prev.map(r =>
      r.id === tempId ? { ...r, errors: [...r.errors, content] } : r
    ))

    if (ev.type === 'run_id') {
      serverRunIdRef.current = ev.content
      const now = new Date().toISOString()
      runRef.startTime = Date.now()
      setRuns(prev => prev.map(r => r.id === tempId ? { ...r, createdAt: now } : r))

    } else if (ev.type === 'text') {
      runRef.agentText += ev.content
      const displayText = stripCodeBlocks(runRef.agentText)
      setMessages(prev => {
        const next = [...prev]
        next[next.length - 1] = { role: 'assistant', text: displayText }
        return next
      })

    } else if (ev.type === 'code') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, code: ev.content, editedCode: ev.content } : r
      ))

    } else if (ev.type === 'output_chunk') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, output: (r.output || '') + ev.content + '\n' } : r
      ))

    } else if (ev.type === 'output') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, output: ev.content } : r
      ))

    } else if (ev.type === 'plot') {
      const src = `data:image/png;base64,${ev.content}`
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, plots: [...r.plots, src] } : r
      ))

    } else if (ev.type === 'table') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, tables: [...(r.tables || []), { html: ev.content }] } : r
      ))

    } else if (ev.type === 'export') {
      try {
        const exp = JSON.parse(ev.content)
        setRuns(prev => prev.map(r =>
          r.id === tempId ? { ...r, exports: [...(r.exports || []), exp] } : r
        ))
      } catch {
        appendRunError('The server returned an invalid export')
      }

    } else if (ev.type === 'dataset_proposal') {
      try {
        const prop = JSON.parse(ev.content)
        setPendingProposals(prev => ({
          ...prev,
          [prop.run_id]: [...(prev[prop.run_id] || []), prop],
        }))
      } catch {
        appendRunError('The server returned an invalid dataset proposal')
      }

    } else if (ev.type === 'dataset_auto_accepted') {
      // Auto-approve mode: change was committed, refresh file list
      fetchContext(sessionId)
        .then(ctx => {
          if (ctx.files) setFiles(ctx.files)
          if (ctx.archived_files) setArchivedFiles(ctx.archived_files)
        })
        .catch(e => setGlobalError(e.message))

    } else if (ev.type === 'installing') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, installingPkg: ev.content } : r
      ))

    } else if (ev.type === 'installed') {
      setRuns(prev => prev.map(r =>
        r.id === tempId
          ? { ...r, installingPkg: null, installedPkgs: [...(r.installedPkgs || []), ev.content] }
          : r
      ))

    } else if (ev.type === 'install_error') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, installingPkg: null, errors: [...r.errors, ev.content] } : r
      ))

    } else if (ev.type === 'stopped') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, stopped: true } : r
      ))

    } else if (ev.type === 'retry_error') {
      // First-attempt error before an auto-retry — stored separately so the
      // RunCard can show it collapsed rather than as a primary error block.
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, retryError: ev.content } : r
      ))

    } else if (ev.type === 'error') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, errors: [...r.errors, ev.content] } : r
      ))

    } else if (ev.type === 'context_snapshot') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, contextSnapshot: ev.content } : r
      ))

    } else if (ev.type === 'summary') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, summary: ev.content } : r
      ))

    } else if (ev.type === 'trace') {
      setRuns(prev => prev.map(r =>
        r.id === tempId ? { ...r, trace: [...(r.trace || []), ev.content] } : r
      ))

    } else if (ev.type === 'done') {
      const donePayload = (() => { try { return JSON.parse(ev.content) } catch { return {} } })()
      const succeeded = donePayload.success !== false
      const finalId = serverRunIdRef.current || tempId
      const capturedAgentText = stripCodeBlocks(runRef.agentText)
      const durationMs = runRef.startTime ? Date.now() - runRef.startTime : 0
      setRuns(prev => {
        const updated = prev.map(r =>
          r.id === tempId
            ? { ...r, id: finalId, streaming: false, agentText: capturedAgentText, durationMs,
                jobStatus: r.jobStatus === 'running' ? (succeeded ? 'done' : 'error') : r.jobStatus }
            : r
        )
        return updated.filter(r =>
          r.id !== finalId || r.code || r.output || r.plots.length || r.errors.length
        )
      })
      if (!runRef.isBackground) setStreaming(false)

      if (serverRunIdRef.current) setSelectedRunId(serverRunIdRef.current)

      if (runRef.addedPlaceholder && !runRef.agentText.trim()) {
        setMessages(prev => prev.filter((_, i) => i !== prev.length - 1))
      }

      fetchContext(sessionId)
        .then(ctx => {
          if (ctx.files) setFiles(ctx.files)
          if (ctx.archived_files) setArchivedFiles(ctx.archived_files)
        })
        .catch(e => setGlobalError(e.message))
    }
  }

  function handleSend(text, background = false, tabId = activeChatTabId, attachmentIds = []) {
    const tab = chatTabs.find(t => t.id === tabId) || activeTab
    if (!sessionId || tab.streaming) return
    setGlobalError(null)
    const s = tabSetters(tabId)

    const activeFileNames = files.map(f => f.name).filter(n => !inactiveFiles.has(n))
    const activeFilesParam = activeFileNames.length < files.length ? activeFileNames : null
    const attachmentsParam = attachmentIds.length > 0 ? attachmentIds : null

    if (background) {
      const tempId = `bg_${Date.now()}`
      s.setRuns(prev => [...prev, {
        id: tempId, prompt: text, code: null, editedCode: '', output: null,
        plots: [], tables: [], exports: [], errors: [], trace: [], retryError: null,
        streaming: true, editing: false, version: 1, parentId: null, stopped: false, installingPkg: null, installedPkgs: [],
        jobStatus: 'running',
      }])

      fetch(`${API}/chat/background`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message: text, active_files: activeFilesParam, attachments: attachmentsParam }),
      }).then(async r => {
        if (!r.ok) {
          const err = await r.json().catch(() => ({}))
          setGlobalError(err.detail || 'Failed to start background job')
          s.setRuns(prev => prev.filter(r => r.id !== tempId))
          return
        }
        const { run_id } = await r.json()
        s.setRuns(prev => prev.map(r => r.id === tempId ? { ...r, id: run_id, createdAt: new Date().toISOString() } : r))
        const runRef = { tempId: run_id, serverRunIdRef: { current: run_id }, agentText: '', addedPlaceholder: false, isBackground: true }
        s.setMessages(prev => [...prev, { role: 'user', content: text }, { role: 'assistant', text: '' }])
        runRef.addedPlaceholder = true
        runRef.startTime = Date.now()
        streamSSEGet(`${API}/run/${run_id}/stream`, ev => applySSEEvent(ev, runRef, s))
      }).catch(e => {
        setGlobalError(e.message)
        s.setRuns(prev => prev.filter(r => r.id !== tempId))
      })
      return
    }

    // ── Foreground ────────────────────────────────────────────────────────
    s.setMessages(prev => [...prev,
      { role: 'user', content: text },
      { role: 'assistant', text: '' },
    ])

    const tempId = `temp_${Date.now()}`
    const runRef = { tempId, serverRunIdRef: { current: null }, agentText: '', addedPlaceholder: true }

    s.setRuns(prev => [...prev, {
      id: tempId, prompt: text, code: null, editedCode: '', output: null,
      plots: [], tables: [], exports: [], errors: [], trace: [], retryError: null,
      streaming: true, editing: false, version: 1, parentId: null, stopped: false, installingPkg: null, installedPkgs: [],
    }])
    s.setStreaming(true)

    streamSSE(`${API}/chat/stream`,
      { session_id: sessionId, message: text, active_files: activeFilesParam, attachments: attachmentsParam },
      ev => applySSEEvent(ev, runRef, s)
    )
  }

  function handleRerun(runId, code, tabId) {
    const resolvedTabId = tabId || chatTabs.find(t => t.runs.some(r => r.id === runId))?.id || activeChatTabId
    const tab = chatTabs.find(t => t.id === resolvedTabId) || activeTab
    if (tab.streaming) return
    setGlobalError(null)
    const s = tabSetters(resolvedTabId)
    s.setStreaming(true)

    const parentRun = tab.runs.find(r => r.id === runId)
    const tempId    = `temp_${Date.now()}`
    const runRef    = { tempId, serverRunIdRef: { current: null }, agentText: '', addedPlaceholder: false }

    s.setRuns(prev => [...prev, {
      id:         tempId,
      prompt:     parentRun?.prompt || '',
      code:       null,
      editedCode: code || '',
      output:     null,
      plots:      [],
      tables:     [],
      exports:    [],
      errors:     [],
      trace:      [],
      streaming:  true,
      editing:    false,
      version:    (parentRun?.version || 1) + 1,
      parentId:   runId,
    }])

    streamSSE(`${API}/run/${runId}/rerun`, { session_id: sessionId, code },
      ev => applySSEEvent(ev, runRef, s)
    )
  }

  function handleOpenEdit(run) {
    setEditMode({
      runId:   run.id,
      code:    run.editedCode || run.code || '',
      version: run.version,
      prompt:  run.prompt,
    })
  }

  async function handleEditorSave(runId, code) {
    const tabId = chatTabs.find(t => t.runs.some(r => r.id === runId))?.id || activeChatTabId
    try {
      await saveEditedCode(runId, code)
      tabSetters(tabId).setRuns(prev => prev.map(r =>
        r.id === runId ? { ...r, editedCode: code } : r
      ))
    } catch (e) {
      setGlobalError(e.message)
      throw e
    }
  }

  function handleEditorRerun(runId, code) {
    setEditMode(null)
    handleRerun(runId, code)
  }

  function handleExport() {
    window.location.href = `${API}/export/${sessionId}`
  }

  function handleReport() {
    window.location.href = `${API}/workspace/${sessionId}/report`
  }

  function handleSuggest(text) {
    if (!activeTab.streaming) handleSend(text, false, activeChatTabId)
    else tabSetters(activeChatTabId).setPendingSuggestion(text)
  }

  const [mainFileW,    dragMainFile,    resetMainFile]    = usePanelSize(`${storageKey}-main-file`,    220, 60, 700)
  const [mainResultsW, dragMainResults, resetMainResults] = usePanelSize(`${storageKey}-main-results`, 440, 80, 1100)

  function handleFilesChanged() {
    fetchContext(sessionId).then(ctx => {
      if (ctx.files) setFiles(ctx.files)
      if (ctx.archived_files) setArchivedFiles(ctx.archived_files)
    }).catch(e => setGlobalError(e.message))
  }

  function handleWorkspacesDeleted(deletedIds) {
    const deletedSet = new Set(deletedIds)
    const remaining = workspaces.filter(w => !deletedSet.has(w.id))
    setMode('analysis')
    if (deletedSet.has(sessionId)) {
      if (remaining.length > 0) {
        switchWorkspace(remaining[0].id)
      } else {
        handleCreateWorkspace()
      }
    } else {
      fetchWorkspaceList().then(setWorkspaces).catch(e => setGlobalError(e.message))
    }
  }

  const sharedFilePanelProps = {
    files, inactiveFiles, sessionId,
    onUpload: handleUpload, onToggleActive: toggleFileActive, onSuggest: handleSuggest,
    uploading,
    workspaceId: sessionId, workspaceName, workspaces,
    onSwitchWorkspace: switchWorkspace, onCreateWorkspace: handleCreateWorkspace,
    onRenameWorkspace: handleRenameWorkspace,
    workspaceLang, onSetLanguage: handleSetLanguage,
    onInspect: handleInspect, onDeleteFile: handleDeleteFile, onSaveNotes: handleSaveNotes,
    archivedFiles, onRestoreFile: handleRestoreFile, onHardDeleteFile: handleHardDeleteFile,
    workflows, onRunWorkflow: handleRunWorkflow, onDeleteWorkflow: handleDeleteWorkflow, streaming: anyStreaming,
    onFilesChanged: handleFilesChanged,
    onOpenStorage: () => setMode('storage'),
  }

  if (editMode) {
    return (
      <CodeEditorPage
        target={editMode}
        runs={activeTab.runs}
        streaming={activeTab.streaming}
        onBack={() => setEditMode(null)}
        onSave={handleEditorSave}
        onRerun={handleEditorRerun}
        messages={activeTab.messages}
        globalError={globalError}
        hasData={files.length > 0}
        onSend={(text, bg) => handleSend(text, bg, activeChatTabId)}
      />
    )
  }

  if (mode === 'history') {
    return (
      <HistoryPage
        runs={activeTab.runs}
        onBack={() => setMode('analysis')}
        onResume={runId => {
          setMode('analysis')
          setTimeout(() => scrollToRun(runId), 100)
        }}
      />
    )
  }

  if (mode === 'storage') {
    return (
      <StoragePage
        workspaceId={sessionId}
        workspaceName={workspaceName}
        workspaces={workspaces}
        onBack={() => setMode('analysis')}
        onWorkspacesDeleted={handleWorkspacesDeleted}
        onRefreshRuns={() => tabSetters(activeChatTabId).setRuns([])}
        onRefreshMessages={() => tabSetters(activeChatTabId).setMessages([])}
      />
    )
  }

  if (mode === 'inspect') {
    return (
      <InspectorPage
        {...sharedFilePanelProps}
        inspectorFileIds={inspectorFileIds}
        activeInspectorFileId={activeInspectorFileId}
        onSetActiveFile={setActiveInspectorFileId}
        onCloseInspectorFile={handleCloseInspectorFile}
        runs={activeTab.runs}
        selectedRunId={selectedRunId}
        onSelectRun={setSelectedRunId}
        messages={activeTab.messages}
        streaming={activeTab.streaming}
        globalError={globalError}
        onSend={(text, bg) => handleSend(text, bg, activeChatTabId)}
        onEditCode={handleOpenEdit}
        onBack={() => setMode('analysis')}
        pendingProposals={activeTab.pendingProposals}
        rejectedProposals={activeTab.rejectedProposals}
        onAcceptProposal={(runId, propId) => handleAcceptProposal(runId, propId, activeChatTabId)}
        onRejectProposal={(runId, propId) => handleRejectProposal(runId, propId, activeChatTabId)}
        onRestoreProposal={(runId, propId) => handleRestoreProposal(runId, propId, activeChatTabId)}
        onFileReverted={() => fetchContext(sessionId).then(ctx => {
          if (ctx.files) setFiles(ctx.files)
          if (ctx.archived_files) setArchivedFiles(ctx.archived_files)
        }).catch(e => setGlobalError(e.message))}
      />
    )
  }

  return (
    <div className="layout">
      <FilePanel {...sharedFilePanelProps} style={{ width: mainFileW }} />
      <Splitter onDrag={dragMainFile} onDoubleClick={resetMainFile} />
      <div className="chat-tabs-root">
        <div className="chat-tab-bar">
          {chatTabs.map(tab => (
            <div
              key={tab.id}
              className={`chat-tab${tab.id === activeChatTabId ? ' active' : ''}${tab.streaming ? ' busy' : ''}`}
              onClick={() => setActiveChatTabId(tab.id)}
            >
              <span className="chat-tab-label">{tab.label}</span>
              {chatTabs.length > 1 && (
                <button className="chat-tab-close" onClick={e => { e.stopPropagation(); removeChatTab(tab.id) }}>✕</button>
              )}
            </div>
          ))}
          <button className="chat-tab-add" onClick={addChatTab} disabled={chatTabs.length >= 5} title="Open a parallel chat on this workspace">+</button>
        </div>
        <div className="chat-tabs-content">
          {chatTabs.map(tab => (
            <div key={tab.id} className={`chat-tab-pane${tab.id === activeChatTabId ? ' active' : ''}`}>
              <ResultsPanel
                runs={tab.runs}
                files={files}
                pendingProposals={tab.pendingProposals}
                rejectedProposals={tab.rejectedProposals}
                onEditCode={handleOpenEdit}
                onExport={handleExport}
                onReport={handleReport}
                onAcceptProposal={(runId, propId) => handleAcceptProposal(runId, propId, tab.id)}
                onRejectProposal={(runId, propId) => handleRejectProposal(runId, propId, tab.id)}
                onRestoreProposal={(runId, propId) => handleRestoreProposal(runId, propId, tab.id)}
                onSaveWorkflow={handleSaveWorkflow}
                onRerun={(runId, code) => handleRerun(runId, code, tab.id)}
                streaming={tab.streaming}
                style={{ width: mainResultsW }}
              />
              <Splitter onDrag={dragMainResults} onDoubleClick={resetMainResults} />
              <ChatArea
                messages={tab.messages}
                streaming={tab.streaming}
                globalError={globalError}
                hasData={files.length > 0}
                onSend={(text, bg, attachments) => handleSend(text, bg, tab.id, attachments)}
                onStop={handleStop}
                onHistory={() => setMode('history')}
                runCount={tab.runs.filter(r => !r.streaming).length}
                workspaceId={sessionId}
                autoApprove={autoApprove}
                onToggleAutoApprove={handleToggleAutoApprove}
                agentConfig={agentConfig}
                onAgentChange={() => fetchConfig().then(setAgentConfig).catch(e => setGlobalError(e.message))}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
