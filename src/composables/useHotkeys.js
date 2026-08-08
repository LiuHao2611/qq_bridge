import { onMounted, onUnmounted } from 'vue'

// 全局快捷键：Ctrl/Cmd+S 保存、Ctrl/Cmd+Z 撤销（非输入态）、F11 全屏、Ctrl+1~9 主导航。
export function useHotkeys(handlers = {}) {
  const onKey = (e) => {
    const t = e.target
    const tag = (t && t.tagName ? t.tagName : '').toLowerCase()
    const editable = tag === 'input' || tag === 'textarea' || (t && t.isContentEditable)
    const mod = e.ctrlKey || e.metaKey

    if (mod && e.key.toLowerCase() === 's') {
      e.preventDefault()
      handlers.save && handlers.save()
    } else if (mod && e.key.toLowerCase() === 'z') {
      if (editable) return
      e.preventDefault()
      handlers.undo && handlers.undo()
    } else if (e.key === 'F11') {
      e.preventDefault()
      handlers.fullscreen && handlers.fullscreen()
    } else if (mod && /^[1-9]$/.test(e.key)) {
      if (editable) return
      handlers.nav && handlers.nav(Number(e.key))
    }
  }
  onMounted(() => window.addEventListener('keydown', onKey))
  onUnmounted(() => window.removeEventListener('keydown', onKey))
}
