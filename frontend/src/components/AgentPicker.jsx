import { useState, useRef, useEffect } from 'react'
import { patchConfig } from '../api.js'

const EFFORT_OPTIONS = [
  { value: '',     label: 'Default' },
  { value: 'low',  label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'max',  label: 'Max' },
]

export default function AgentPicker({ config, onConfigChange }) {
  const [open,      setOpen]      = useState(false)
  const [customCmd, setCustomCmd] = useState('')
  const [saving,    setSaving]    = useState(false)
  const [error,     setError]     = useState(null)
  const ref = useRef()

  const presets       = config?.presets || []
  const currentCmd    = config?.command  || 'claude -p'
  const modelOptions  = config?.model_options || []
  const currentModel  = config?.model || ''
  const currentEffort = config?.effort   || ''
  const currentPreset = config?.preset_id || 'custom'
  const supportsModel  = modelOptions.length > 0
  const supportsEffort = currentCmd.includes('claude') || currentCmd.includes('codex')

  // Label shown in header button
  const agentLabel = currentPreset === 'custom'
    ? currentCmd
    : (presets.find(p => p.id === currentPreset)?.label ?? currentCmd)
  const modelLabel = currentModel
    ? ` · ${modelOptions.find(m => m.value === currentModel)?.label ?? currentModel}`
    : ''
  const effortLabel = currentEffort ? ` · ${currentEffort}` : ''
  const btnLabel = agentLabel + modelLabel + effortLabel

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function handle(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [open])

  async function save(patch) {
    setSaving(true)
    setError(null)
    try {
      await patchConfig(patch)
      onConfigChange()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  async function selectPreset(command) {
    await save({ command, model: '' })
    setCustomCmd('')
    setOpen(false)
  }

  async function selectModel(value) {
    await save({ model: value })
  }

  async function selectEffort(value) {
    await save({ effort: value })
  }

  return (
    <div className="agent-picker" ref={ref}>
      <button
        className="agent-picker-btn"
        onClick={() => setOpen(v => !v)}
        title="Agent, model &amp; effort settings"
      >
        {btnLabel} ▾
      </button>

      {open && (
        <div className="agent-picker-dropdown">

          {/* ── Agent section ── */}
          <div className="agent-picker-label">Agent</div>

          {presets.map(p => (
            <button
              key={p.id}
              className={`agent-picker-option${p.command === currentCmd ? ' active' : ''}`}
              onClick={() => selectPreset(p.command)}
              disabled={saving}
            >
              <span className="agent-option-label">{p.label}</span>
              {p.command === currentCmd && <span className="agent-option-check">✓</span>}
            </button>
          ))}

          <div className="agent-picker-custom">
            <input
              className="agent-custom-input"
              placeholder="Custom command…"
              value={customCmd}
              onChange={e => setCustomCmd(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && customCmd.trim()) selectPreset(customCmd.trim()) }}
            />
            <button
              className="agent-custom-save"
              disabled={!customCmd.trim() || saving}
              onClick={() => selectPreset(customCmd.trim())}
            >
              {saving ? '…' : 'Set'}
            </button>
          </div>

          {/* ── Model section ── */}
          <div className="agent-picker-divider" />
          <div className={`agent-picker-label${!supportsModel ? ' agent-picker-label-dim' : ''}`}>
            Model {!supportsModel && <span className="agent-effort-unsupported">(use the command)</span>}
          </div>

          {modelOptions.map(opt => (
            <button
              key={opt.value}
              className={`agent-picker-option${currentModel === opt.value ? ' active' : ''}`}
              onClick={() => selectModel(opt.value)}
              disabled={saving}
              title={opt.value ? `--model ${opt.value}` : 'CLI/account default'}
            >
              <span className="agent-option-label">{opt.label}</span>
              {currentModel === opt.value && <span className="agent-option-check">✓</span>}
            </button>
          ))}

          {/* ── Effort section ── */}
          <div className="agent-picker-divider" />
          <div className={`agent-picker-label${!supportsEffort ? ' agent-picker-label-dim' : ''}`}>
            Effort {!supportsEffort && <span className="agent-effort-unsupported">(not supported)</span>}
          </div>

          <div className="agent-effort-row">
            {EFFORT_OPTIONS.map(opt => (
              <button
                key={opt.value}
                className={`agent-effort-btn${currentEffort === opt.value ? ' active' : ''}`}
                onClick={() => selectEffort(opt.value)}
                disabled={saving || !supportsEffort}
                title={
                  !opt.value   ? 'Model default (no effort flag)' :
                  currentCmd.includes('codex') ? `-c model_reasoning_effort="${opt.value === 'max' ? 'xhigh' : opt.value}"` :
                  `--effort ${opt.value}`
                }
              >
                {opt.label}
              </button>
            ))}
          </div>

          {error && <div className="agent-picker-error">{error}</div>}
        </div>
      )}
    </div>
  )
}
