import { useState, useRef, useEffect } from 'react'

export default function CompactChat({ messages, streaming, globalError, hasData, onSend }) {
  const [inputText, setInputText] = useState('')
  const bottomRef = useRef()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function handleSend(text) {
    const t = (text || inputText).trim()
    if (!t || streaming || !hasData) return
    setInputText('')
    onSend(t)
  }

  return (
    <div className="compact-chat">
      <div className="compact-messages">
        {messages.map((msg, i) => (
          msg.role === 'user'
            ? <div key={i} className="compact-msg user"><span>{msg.content}</span></div>
            : msg.text
              ? <div key={i} className="compact-msg assistant"><p>{msg.text}</p></div>
              : null
        ))}
        {globalError && <div className="compact-error">{globalError}</div>}
        {streaming && <div className="compact-msg assistant"><span className="spinner" /></div>}
        <div ref={bottomRef} />
      </div>
      <div className="compact-input-row">
        <textarea
          className="compact-input"
          value={inputText}
          onChange={e => setInputText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }}
          placeholder={hasData ? 'Ask…' : 'Upload a file first'}
          rows={1}
          disabled={streaming || !hasData}
        />
        <button
          className="compact-send-btn"
          onClick={() => handleSend()}
          disabled={streaming || !hasData || !inputText.trim()}
        >↑</button>
      </div>
    </div>
  )
}
