import { useState, useCallback } from 'react'

export function usePanelSize(key, defaultVal, min, max) {
  const [size, setSize] = useState(() => {
    const stored = localStorage.getItem(`vibalytics-panel-${key}`)
    const parsed = stored ? parseInt(stored, 10) : NaN
    return isNaN(parsed) ? defaultVal : Math.min(max, Math.max(min, parsed))
  })
  const drag = useCallback((delta) => {
    setSize(prev => {
      const next = Math.min(max, Math.max(min, prev + delta))
      localStorage.setItem(`vibalytics-panel-${key}`, String(next))
      return next
    })
  }, [key, min, max])
  const reset = useCallback(() => {
    setSize(defaultVal)
    localStorage.setItem(`vibalytics-panel-${key}`, String(defaultVal))
  }, [key, defaultVal])
  return [size, drag, reset]
}
