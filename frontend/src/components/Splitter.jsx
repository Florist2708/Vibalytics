export default function Splitter({ onDrag, onDoubleClick }) {
  function handleMouseDown(e) {
    e.preventDefault()
    let lastX = e.clientX
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    function onMove(e) {
      const delta = e.clientX - lastX
      lastX = e.clientX
      onDrag(delta)
    }
    function onUp() {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }
  return <div className="splitter" onMouseDown={handleMouseDown} onDoubleClick={onDoubleClick} title="Drag to resize · double-click to reset" />
}
