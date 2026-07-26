export const API = ''
export const STORAGE_KEY = 'vibalytics_workspace_id'

async function responseError(response, fallback) {
  const payload = await response.json().catch(() => ({}))
  return new Error(payload.detail || fallback)
}

export async function createSession() {
  const r = await fetch(`${API}/session`, { method: 'POST' })
  if (!r.ok) throw await responseError(r, 'Failed to create workspace')
  return (await r.json()).session_id
}

export async function uploadFile(sessionId, file) {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('session_id', sessionId)
  const r = await fetch(`${API}/upload`, { method: 'POST', body: fd })
  if (!r.ok) throw await responseError(r, 'Upload failed')
  return r.json()
}

export async function fetchContext(sessionId) {
  const r = await fetch(`${API}/context/${sessionId}`)
  if (!r.ok) throw await responseError(r, 'Workspace not found')
  return r.json()
}

export async function fetchWorkspaceList() {
  const r = await fetch(`${API}/workspaces`)
  if (!r.ok) throw await responseError(r, 'Failed to load workspaces')
  return r.json()
}

export async function patchWorkspace(id, patch) {
  const r = await fetch(`${API}/workspace/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) throw await responseError(r, 'Failed to update workspace')
  return r.json()
}

export async function renameWorkspace(id, name) {
  return patchWorkspace(id, { name })
}

export async function fetchWorkflows() {
  const r = await fetch(`${API}/workflows`)
  if (!r.ok) throw await responseError(r, 'Failed to load workflows')
  return r.json()
}

export async function createWorkflow(name, runId) {
  const r = await fetch(`${API}/workflows`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, run_id: runId }),
  })
  if (!r.ok) throw await responseError(r, 'Save failed')
  return r.json()
}

export async function apiDeleteWorkflow(id) {
  const r = await fetch(`${API}/workflows/${id}`, { method: 'DELETE' })
  if (!r.ok) throw await responseError(r, 'Delete failed')
}

export async function fetchFileVersions(fileId) {
  const r = await fetch(`${API}/file/${fileId}/versions`)
  if (!r.ok) throw await responseError(r, 'Failed to fetch versions')
  return r.json()
}

export async function editFileCells(sessionId, fileId, edits) {
  const r = await fetch(`${API}/data/${sessionId}/${fileId}/edit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ edits }),
  })
  if (!r.ok) throw await responseError(r, 'Edit failed')
  return r.json()
}

export async function revertFileVersion(fileId, versionId, sessionId) {
  const r = await fetch(
    `${API}/file/${fileId}/revert/${versionId}?session_id=${encodeURIComponent(sessionId)}`,
    { method: 'POST' },
  )
  if (!r.ok) throw await responseError(r, 'Revert failed')
  return r.json()
}

export async function stopRun(workspaceId) {
  const r = await fetch(`${API}/workspace/${workspaceId}/stop`, { method: 'POST' })
  if (!r.ok) throw await responseError(r, 'Failed to stop run')
}

export async function uploadChatAttachment(workspaceId, file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${API}/workspace/${workspaceId}/chat/attachment`, { method: 'POST', body: fd })
  if (!r.ok) throw await responseError(r, 'Attachment upload failed')
  return r.json()  // {id, filename, mime_type, size}
}

export async function fetchConfig() {
  const r = await fetch(`${API}/config`)
  if (!r.ok) throw await responseError(r, 'Failed to load agent configuration')
  return r.json()
}

export async function patchConfig(patch) {
  const r = await fetch(`${API}/config`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) throw await responseError(r, 'Save failed')
  return r.json()
}

export async function importData(workspaceId, payload) {
  const r = await fetch(`${API}/workspace/${workspaceId}/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) throw await responseError(r, 'Import failed')
  return r.json()
}

export async function fetchAssertions(fileId) {
  const r = await fetch(`${API}/file/${fileId}/assertions`)
  if (!r.ok) throw await responseError(r, 'Failed to load checks')
  return r.json()
}

export async function createAssertion(fileId, payload) {
  const r = await fetch(`${API}/file/${fileId}/assertions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) throw await responseError(r, 'Failed to create check')
  return r.json()
}

export async function patchAssertion(fileId, assertionId, payload) {
  const r = await fetch(`${API}/file/${fileId}/assertion/${assertionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) throw await responseError(r, 'Failed to update check')
  return r.json()
}

export async function deleteAssertion(fileId, assertionId) {
  const r = await fetch(`${API}/file/${fileId}/assertion/${assertionId}`, { method: 'DELETE' })
  if (!r.ok) throw await responseError(r, 'Failed to delete check')
}

export async function runFileChecks(fileId) {
  const r = await fetch(`${API}/file/${fileId}/check`, { method: 'POST' })
  if (!r.ok) throw await responseError(r, 'Check failed')
  return r.json()
}

import { stripCodeBlocks } from './utils.js'

export async function fetchPersistedRuns(workspaceId) {
  const listResponse = await fetch(`${API}/runs/${workspaceId}`)
  if (!listResponse.ok) throw await responseError(listResponse, 'Failed to load run history')
  const list = await listResponse.json()
  if (!Array.isArray(list) || list.length === 0) return { runs: [], messages: [], pendingProposals: {}, rejectedProposals: {} }
  const detailed = await Promise.all(
    list.map(async run => {
      const response = await fetch(`${API}/run/${run.id}`)
      if (!response.ok) throw await responseError(response, `Failed to load run ${run.id}`)
      return response.json()
    })
  )
  const messages = []
  const runs = []
  const pendingProposals = {}
  const rejectedProposals = {}
  for (const r of detailed) {
    // Only emit chat messages for root chat runs (not reruns, not workflow runs)
    const isWorkflow = r.prompt?.startsWith('[workflow] ')
    if (!r.parent_run_id && !isWorkflow) {
      messages.push({ role: 'user', content: r.prompt })
      if (r.agent_text) messages.push({ role: 'assistant', text: stripCodeBlocks(r.agent_text) })
    }
    const pending = Array.isArray(r.pending_proposals) ? r.pending_proposals : []
    const rejected = Array.isArray(r.rejected_proposals) ? r.rejected_proposals : []
    if (pending.length) pendingProposals[r.id] = pending
    if (rejected.length) rejectedProposals[r.id] = rejected
    runs.push({
      id:                  r.id,
      prompt:              r.prompt,
      code:                r.code || null,
      editedCode:          r.edited_code || r.code || '',
      output:              r.output || null,
      plots:               (r.artifacts || []).filter(a => a.type === 'plot').map(a => `/artifact/${a.id}`),
      tables:              (r.artifacts || []).filter(a => a.type === 'table').map(a => ({ url: `/artifact/${a.id}` })),
      exports:             (r.artifacts || []).filter(a => a.type === 'dataset').map(a => ({
        artifact_id: a.id, filename: a.label || `export_${a.id.slice(0, 8)}.csv`,
      })),
      errors:              r.error ? [r.error] : [],
      trace:               Array.isArray(r.trace_steps) ? r.trace_steps : [],
      streaming:           false,
      editing:             false,
      version:             r.version || 1,
      parentId:            r.parent_run_id || null,
      activeFileVersions:  r.active_file_versions || {},
      producedVersions:    r.produced_versions || [],
      summary:             r.summary || '',
      contextSnapshot:     r.context_snapshot || null,
      agentText:           r.agent_text ? stripCodeBlocks(r.agent_text) : '',
      createdAt:           r.created_at || null,
      durationMs:          r.duration_ms || 0,
      language:            r.language || 'r',
      jobStatus:           r.job_status || null,
      envSnapshot:         r.env_snapshot || null,
      retryError:          r.first_attempt_error || null,
    })
  }
  return { runs, messages, pendingProposals, rejectedProposals }
}

export async function saveEditedCode(runId, code) {
  const r = await fetch(`${API}/run/${runId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ edited_code: code }),
  })
  if (!r.ok) throw await responseError(r, 'Failed to save code')
  return r.json()
}

export async function fetchPreview(sessionId, varName) {
  const r = await fetch(`${API}/preview/${sessionId}/${varName}`)
  if (!r.ok) throw await responseError(r, 'Preview failed')
  return r.json()
}

export async function fetchData(sessionId, fileId, { offset = 0, limit = 100, sortBy = '', sortDir = 'asc', filterCol = '', filterVal = '', filterOp = 'contains' } = {}) {
  const params = new URLSearchParams({ offset, limit })
  if (sortBy) { params.set('sort_by', sortBy); params.set('sort_dir', sortDir) }
  if (filterCol) params.set('filter_col', filterCol)
  if (filterVal) params.set('filter_val', filterVal)
  if (filterOp && filterOp !== 'contains') params.set('filter_op', filterOp)
  const r = await fetch(`${API}/data/${sessionId}/${fileId}?${params}`)
  if (!r.ok) throw await responseError(r, 'Data fetch failed')
  return r.json()
}

export async function _readSSEStream(r, onEvent) {
  let doneSeen = false
  let aborted = false

  const emit = event => {
    if (event.type === 'done') doneSeen = true
    onEvent(event)
  }

  const parseLine = line => {
    const normalized = line.endsWith('\r') ? line.slice(0, -1) : line
    if (!normalized.startsWith('data:')) return
    const payload = normalized.slice(5).trimStart()
    if (!payload) return
    try {
      emit(JSON.parse(payload))
    } catch {
      emit({ type: 'error', content: 'Received a malformed event from the server' })
    }
  }

  try {
    if (!r.ok) {
      const err = await r.json().catch(() => ({}))
      emit({ type: 'error', content: err.detail || `Request failed (${r.status})` })
      return
    }
    if (!r.body) throw new Error('Server returned an empty response')

    const reader = r.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop()
      lines.forEach(parseLine)
    }
    buf += dec.decode()
    if (buf) parseLine(buf)
    if (!doneSeen) emit({ type: 'error', content: 'Connection closed before the run completed' })
  } catch (e) {
    aborted = e.name === 'AbortError'
    if (!aborted) emit({ type: 'error', content: e.message })
  } finally {
    if (!doneSeen) emit({
      type: 'done',
      content: JSON.stringify({ success: false, interrupted: aborted }),
    })
  }
}

function handleConnectionFailure(error, onEvent) {
  if (error.name !== 'AbortError') onEvent({ type: 'error', content: error.message })
  onEvent({
    type: 'done',
    content: JSON.stringify({ success: false, interrupted: error.name === 'AbortError' }),
  })
}

export function streamSSE(url, body, onEvent) {
  const ctrl = new AbortController()
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: ctrl.signal,
  }).then(r => _readSSEStream(r, onEvent))
    .catch(e => handleConnectionFailure(e, onEvent))
  return () => ctrl.abort()
}

export function streamSSEGet(url, onEvent) {
  const ctrl = new AbortController()
  fetch(url, { signal: ctrl.signal })
    .then(r => _readSSEStream(r, onEvent))
    .catch(e => handleConnectionFailure(e, onEvent))
  return () => ctrl.abort()
}
