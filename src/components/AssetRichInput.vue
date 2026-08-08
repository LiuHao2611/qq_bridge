<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  assets: { type: Array, default: () => [] },
  placeholder: { type: String, default: '' },
  minRows: { type: Number, default: 2 },
  fixedHeight: { type: Boolean, default: false },
  autoGrow: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue', 'drop-asset', 'remove-asset', 'change'])

const editor = ref(null)
const isFocused = ref(false)
const editorHeight = ref('auto')
const minHeightStyle = computed(() => Math.max(28, props.minRows * 26) + 'px')
const containerStyle = computed(() => ({
  minHeight: minHeightStyle.value,
  height: props.fixedHeight ? minHeightStyle.value : editorHeight.value,
}))

const assetMap = computed(() => {
  const m = new Map()
  ;(props.assets || []).forEach((a) => { if (a && a.name) m.set(a.name, a) })
  return m
})

const assetNames = computed(() => {
  // 按长度降序，保证长名先匹配（避免短名前缀误匹配）
  return [...assetMap.value.keys()].sort((a, b) => b.length - a.length)
})

const escapeHtml = (s) => {
  if (s == null) return ''
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

const escapeRegExp = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const isWordChar = (ch) => /[\w\u4e00-\u9fa5]/.test(ch)

// 把文本中的松散 @资产名 / 遗留 [资产名] 规范化为 @[资产名]（只处理已知资产，且避免重复包装）
const normalizeMarkers = (text) => {
  if (!text) return ''
  let out = text
  for (const name of assetNames.value) {
    const re = new RegExp(`@${escapeRegExp(name)}(?![\\w\\u4e00-\\u9fa5])`, 'g')
    out = out.replace(re, (match, ...args) => {
      const offset = args[args.length - 2]
      if (offset > 0 && out[offset - 1] === '[') return match
      return `@[${name}]`
    })
  }
  // 2. 遗留 [name] -> @[name]（仅已知资产；若前面已有 @ 则保持 @[name] 不变）
  for (const name of assetNames.value) {
    const re = new RegExp(`\\[${escapeRegExp(name)}\\]`, 'g')
    out = out.replace(re, (match, ...args) => {
      const offset = args[args.length - 2]
      if (offset > 0 && out[offset - 1] === '@') return match
      return `@[${name}]`
    })
  }
  return out
}

const chipHtml = (name) => {
  const asset = assetMap.value.get(name)
  const thumb = asset?.thumb || ''
  const thumbStyle = thumb ? `background-image:url(&quot;${escapeHtml(thumb)}&quot;);` : ''
  return `<span class="asset-chip" contenteditable="false" data-name="${escapeHtml(name)}">`
    + `<span class="chip-thumb ${thumb ? 'has-thumb' : ''}" style="${thumbStyle}"></span>`
    + `<span class="chip-name">${escapeHtml(name)}</span>`
    + `</span>`
}

const renderText = (text) => {
  if (!text) return ''
  // 先规范化：松散 @name / 遗留 [name] -> @[name]
  const normalized = normalizeMarkers(text)
  const re = /@\[([^\]]+)\]/g
  const parts = []
  let last = 0
  let m
  while ((m = re.exec(normalized))) {
    if (m.index > last) parts.push({ type: 'text', text: normalized.slice(last, m.index) })
    parts.push({ type: 'chip', name: m[1] })
    last = m.index + m[0].length
  }
  if (last < normalized.length) parts.push({ type: 'text', text: normalized.slice(last) })
  if (!parts.length) return escapeHtml(normalized).replace(/\n/g, '<br>')
  return parts.map((p) => {
    if (p.type === 'chip') return chipHtml(p.name)
    return escapeHtml(p.text).replace(/\n/g, '<br>')
  }).join('')
}

const extractText = () => {
  if (!editor.value) return props.modelValue || ''
  const walk = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return (node.textContent || '').replace(/\u200b/g, '')
    if (node.nodeType === Node.ELEMENT_NODE) {
      if (node.classList && node.classList.contains('asset-chip')) {
        const name = node.getAttribute('data-name')
        return name ? `@[${name}]` : ''
      }
      if (node.classList && node.classList.contains('placeholder')) return ''
      if (node.tagName === 'BR') return '\n'
      return Array.from(node.childNodes).map(walk).join('')
    }
    return ''
  }
  return walk(editor.value)
}

const autoGrow = () => {
  if (props.fixedHeight || !props.autoGrow) return
  const el = editor.value
  if (!el) return
  // 通过响应式 style 绑定设置高度，避免 Vue :style 重新应用时覆盖 DOM 直写
  editorHeight.value = el.scrollHeight + 'px'
}

const render = (force = false) => {
  if (!editor.value) return
  const html = renderText(props.modelValue || '')
  const current = extractText()
  // 避免与当前 DOM 等价时重刷，防止光标跳动；force 时忽略等价判断
  if (!force && current === (props.modelValue || '')) return
  editor.value.innerHTML = html
  autoGrow()
}

const emitUpdate = () => {
  const text = extractText()
  emit('update:modelValue', text)
}

const onInput = () => {
  autoGrow()
  emitUpdate()
}

const onBlur = () => {
  isFocused.value = false
  // blur 时规范化并把用户手打的 @资产名 提交为气泡
  const text = normalizeMarkers(extractText())
  emit('update:modelValue', text)
  emit('change', text)
  nextTick(() => { render(true); autoGrow() })
}

const onFocus = () => {
  isFocused.value = true
}

const insertChipAtCursor = (name) => {
  if (!editor.value) return
  const html = chipHtml(name)
  const sel = window.getSelection()
  let range = null
  if (sel && sel.rangeCount) {
    const r = sel.getRangeAt(0)
    if (editor.value.contains(r.startContainer)) range = r
  }
  if (!range) {
    range = document.createRange()
    range.selectNodeContents(editor.value)
    range.collapse(false)
  }
  range.deleteContents()
  const frag = range.createContextualFragment(html + '\u00A0')
  range.insertNode(frag)
  range.collapse(false)
  sel.removeAllRanges()
  sel.addRange(range)
  autoGrow()
  emitUpdate()
}

// 取拖放落点的光标范围（跨浏览器），用于在放置位置插入气泡而非开头
const getDropCaret = (event) => {
  if (document.caretRangeFromPoint) {
    return document.caretRangeFromPoint(event.clientX, event.clientY)
  }
  if (document.caretPositionFromPoint) {
    const pos = document.caretPositionFromPoint(event.clientX, event.clientY)
    if (pos) {
      const r = document.createRange()
      r.setStart(pos.offsetNode, pos.offset)
      r.collapse(true)
      return r
    }
  }
  return null
}

const onDrop = (event) => {
  event.preventDefault()
  event.stopPropagation()
  const raw = event.dataTransfer.getData('application/x-manju-asset') || event.dataTransfer.getData('text/plain')
  if (!raw) return
  let asset
  try { asset = JSON.parse(raw) } catch { return }
  if (!asset || !asset.name) return
  // 把光标定位到拖放落点，保证气泡落在拖入位置（而非第一个字符前）
  const caret = getDropCaret(event)
  if (caret && editor.value.contains(caret.startContainer)) {
    const sel = window.getSelection()
    sel.removeAllRanges()
    sel.addRange(caret)
  }
  insertChipAtCursor(asset.name)
  emit('drop-asset', asset)
  emit('change', extractText())
}

const findChipAncestor = (node) => {
  let cur = node
  while (cur && cur !== editor.value) {
    if (cur.nodeType === Node.ELEMENT_NODE && cur.classList && cur.classList.contains('asset-chip')) return cur
    cur = cur.parentNode
  }
  return null
}

const removeChip = (chip) => {
  if (!chip || !editor.value) return
  const name = chip.getAttribute('data-name')
  const parent = chip.parentNode
  const next = chip.nextSibling
  chip.remove()
  // 删除后将光标放回原气泡位置，避免光标丢失/跳到最前
  const sel = window.getSelection()
  if (sel) {
    const range = document.createRange()
    if (next && next.nodeType === Node.TEXT_NODE) range.setStart(next, 0)
    else if (next) range.setStartBefore(next)
    else if (parent) range.setStart(parent, parent.childNodes.length)
    else { range.selectNodeContents(editor.value); range.collapse(false) }
    range.collapse(true)
    sel.removeAllRanges()
    sel.addRange(range)
  }
  const text = extractText()
  emit('update:modelValue', text)
  emit('change', text)
  autoGrow()
  if (name) emit('remove-asset', { name })
}

// 在光标处插入软换行（<br>）。Chrome 行尾的孤立 <br> 不渲染新行，
// 需在其后补一个零宽节点（\u200b）以保证新行可见；该零宽字符在 extractText 时会被剥离。
const insertLineBreakAtCursor = () => {
  const sel = window.getSelection()
  if (!sel || !sel.rangeCount || !editor.value) return false
  const range = sel.getRangeAt(0)
  range.deleteContents()
  const br = document.createElement('br')
  range.insertNode(br)
  if (!br.nextSibling) {
    const zw = document.createTextNode('\u200b')
    br.parentNode.insertBefore(zw, br.nextSibling)
    range.setStartAfter(zw)
  } else {
    range.setStartAfter(br)
  }
  range.collapse(true)
  sel.removeAllRanges()
  sel.addRange(range)
  return true
}

const onKeydown = (event) => {
  // 输入法合成期间不拦截按键，避免破坏中文上屏
  if (event.isComposing) return
  // 默认禁止回车提交；Shift+Enter 允许换行（适合画面描述等多行场景）
  if (event.key === 'Enter') {
    if (event.shiftKey) {
      event.preventDefault()
      if (insertLineBreakAtCursor()) {
        autoGrow()
        emitUpdate()
      }
    } else {
      event.preventDefault()
    }
    return
  }
  if (!window.getSelection) return
  const sel = window.getSelection()
  if (!sel.rangeCount) return
  const range = sel.getRangeAt(0)

  // 选区包含气泡时，删除/输入应先整体删掉气泡
  if (!sel.isCollapsed) {
    let container = range.commonAncestorContainer
    if (container.nodeType === Node.TEXT_NODE) container = container.parentNode
    const chip = findChipAncestor(container)
    if (chip && editor.value.contains(chip)) {
      // Backspace / Delete / 任意字符输入都会替换选区；先整体移除气泡
      if (event.key === 'Backspace' || event.key === 'Delete' || event.key.length === 1) {
        removeChip(chip)
        if (event.key === 'Backspace' || event.key === 'Delete') event.preventDefault()
      }
      return
    }
  }

  if (!sel.isCollapsed) return

  const node = range.startContainer
  const offset = range.startOffset

  if (event.key === 'Backspace') {
    // 光标在文本节点开头，前一个兄弟是气泡
    if (offset === 0 && node.nodeType === Node.TEXT_NODE && node.previousSibling) {
      const prev = node.previousSibling
      if (prev.nodeType === Node.ELEMENT_NODE && prev.classList && prev.classList.contains('asset-chip')) {
        event.preventDefault()
        removeChip(prev)
        return
      }
    }
    // 光标紧跟在气泡后（在气泡内部末尾不应出现，但防御）
    if (node.nodeType === Node.ELEMENT_NODE && node.classList && node.classList.contains('asset-chip')) {
      event.preventDefault()
      removeChip(node)
      return
    }
  }

  if (event.key === 'Delete') {
    // 光标在文本节点末尾，后一个兄弟是气泡
    if (node.nodeType === Node.TEXT_NODE && offset >= (node.textContent || '').length && node.nextSibling) {
      const nxt = node.nextSibling
      if (nxt.nodeType === Node.ELEMENT_NODE && nxt.classList && nxt.classList.contains('asset-chip')) {
        event.preventDefault()
        removeChip(nxt)
        return
      }
    }
    // 光标在气泡前（在气泡内部开头不应出现，但防御）
    if (node.nodeType === Node.ELEMENT_NODE && node.classList && node.classList.contains('asset-chip')) {
      event.preventDefault()
      removeChip(node)
      return
    }
  }
}

const onPaste = (event) => {
  event.preventDefault()
  const text = ((event.clipboardData || window.clipboardData).getData('text/plain') || '')
    .replace(/\r?\n/g, ' ')
  document.execCommand('insertText', false, text)
  autoGrow()
}

const onClick = (event) => {
  // 点击气泡时把光标放到气泡后面，避免进入不可编辑区域
  const chip = findChipAncestor(event.target)
  if (!chip || !editor.value) return
  const sel = window.getSelection()
  const range = document.createRange()
  range.setStartAfter(chip)
  range.collapse(true)
  sel.removeAllRanges()
  sel.addRange(range)
  event.preventDefault()
}

watch(() => props.modelValue, (newVal, oldVal) => {
  if (newVal === oldVal) return
  // 非 force：仅当 DOM 与 modelValue 不一致时（外部赋值/切换分镜/拖入）才重建，
  // 用户自身输入时 DOM 已是新值，跳过重建以保留光标位置（修复光标跳到气泡前问题）
  render()
}, { immediate: true })

onMounted(() => {
  nextTick(() => {
    render(true)
    autoGrow()
    // 兜底：某些异步/缓存场景下，mounted 时 modelValue 尚未就绪或 DOM 未稳定，50ms 后二次确认
    setTimeout(() => {
      if (!editor.value) return
      const html = editor.value.innerHTML || ''
      const hasChip = html.includes('asset-chip')
      const hasRawMarker = /@\[[^\]]+\]/.test(html) || /@[\u4e00-\u9fa5A-Za-z0-9_·.\-]+/.test(html)
      if (!hasChip && hasRawMarker) {
        console.debug('[AssetRichInput] fallback render triggered')
        render(true)
      }
      autoGrow()
    }, 80)
  })
})
</script>

<template>
  <div
    ref="editor"
    class="asset-rich-input"
    :class="{ 'is-fixed': fixedHeight }"
    contenteditable="true"
    :data-placeholder="placeholder"
    :style="containerStyle"
    @input="onInput"
    @blur="onBlur"
    @focus="onFocus"
    @drop="onDrop"
    @dragover.prevent
    @keydown="onKeydown"
    @paste="onPaste"
    @click="onClick"
  ></div>
</template>

<style>
.asset-rich-input {
  box-sizing: border-box;
  width: 100%;
  min-height: var(--el-input-height, 28px);
  height: auto;
  line-height: 1.5;
  padding: 4px 11px;
  font-size: var(--el-font-size-base, 13px);
  color: var(--el-input-text-color, var(--txt));
  background: var(--el-input-bg-color, var(--card));
  border: 1px solid var(--el-input-border-color, var(--line));
  border-radius: var(--el-input-border-radius, 4px);
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
  cursor: text;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.asset-rich-input.is-fixed {
  overflow: hidden;
  resize: none;
  cursor: pointer;
}
.asset-rich-input:focus {
  border-color: var(--el-color-primary, #409eff);
  box-shadow: 0 0 0 1px var(--el-color-primary, #409eff) inset;
}
.asset-rich-input:empty::before {
  content: attr(data-placeholder);
  color: var(--el-text-color-placeholder, #a8abb2);
  pointer-events: none;
}

.asset-chip {
  display: inline-flex;
  align-items: center;
  vertical-align: middle;
  height: 1.5em;
  max-width: 180px;
  margin: 0 3px;
  padding: 0 8px 0 0;
  background: rgba(64, 158, 255, 0.08);
  border: 1px solid rgba(64, 158, 255, 0.35);
  border-radius: 12px;
  color: var(--txt);
  font-size: 0.9em;
  line-height: 1;
  user-select: none;
  cursor: default;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08), 0 0 0 1px rgba(64, 158, 255, 0.06) inset;
}
.chip-thumb {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.15em;
  height: 1.15em;
  margin-right: 5px;
  background-color: rgba(64, 158, 255, 0.2);
  background-size: cover;
  background-position: center;
  border-radius: 10px 0 0 10px;
  flex-shrink: 0;
}
.chip-thumb:not(.has-thumb)::after {
  content: '@';
  font-size: 0.72em;
  color: #409eff;
}
.chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
