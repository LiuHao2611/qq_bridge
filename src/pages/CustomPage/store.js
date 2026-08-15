// 万能短剧 · 页面自定义 · 状态 + 持久化（多页模型）
// 草稿：localStorage['manju.customPage.v1']（预览/编辑即时写整张页映射）
// 保存：wn_settings.custom_page（页映射：{ pageId: 页面树 }）+ wn_settings.custom_theme（主题包）
import { reactive, ref, computed, watch } from 'vue'
import { defaultPages } from './schema/defaultPages'
import { useEditor } from './editor/useEditor'

const LS_KEY = 'wn_settings'
const DRAFT_KEY = 'manju.customPage.v1'

const clone = (o) => JSON.parse(JSON.stringify(o))

function readSettings(key, fallback) {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const s = JSON.parse(raw)
      if (s[key] !== undefined) return s[key]
    }
  } catch (e) {}
  return fallback
}
function writeSettings(key, value) {
  try {
    const raw = localStorage.getItem(LS_KEY)
    const s = raw ? JSON.parse(raw) : {}
    s[key] = value
    localStorage.setItem(LS_KEY, JSON.stringify(s))
    return true
  } catch (e) { return false }
}
function readDraft() {
  try { const raw = localStorage.getItem(DRAFT_KEY); return raw ? JSON.parse(raw) : null } catch (e) { return null }
}
function writeDraft(map) {
  try { localStorage.setItem(DRAFT_KEY, JSON.stringify(map)); return true } catch (e) { return false }
}

// 是否为「页映射」（普通对象、无 .type 字段）；单页树带 .type==='page'
function isPagesMap(v) {
  return v && typeof v === 'object' && !Array.isArray(v) && v.type === undefined
}

export function useCustomPageStore() {
  const { deselect } = useEditor()

  // 多页映射：pageId → 页面树
  const pages = reactive({})
  const activePageId = ref('home')
  const mode = ref('preview') // 'preview' | 'edit'

  // 加载优先级：settings.custom_page → 草稿 → 默认 6 页
  const saved = readSettings('custom_page', null)
  if (saved && isPagesMap(saved)) {
    Object.assign(pages, clone(saved))
  } else if (saved && saved.type === 'page') {
    // 旧版单页格式迁移
    pages.home = clone(saved)
  } else {
    const d = readDraft()
    if (d && isPagesMap(d)) Object.assign(pages, d)
    else Object.assign(pages, clone(defaultPages))
  }
  if (!pages[activePageId.value]) activePageId.value = Object.keys(pages)[0] || 'home'

  // 当前激活页的树（渲染器绑定的就是它）
  const tree = computed(() => pages[activePageId.value])

  // 即时草稿持久化（编辑中对任意页的改动即时写草稿）
  watch(pages, () => writeDraft(clone(pages)), { deep: true, flush: 'post' })

  function setActivePage(id) {
    if (pages[id]) { activePageId.value = id; deselect() }
  }

  function loadDefault() {
    for (const k of Object.keys(pages)) delete pages[k]
    Object.assign(pages, clone(defaultPages))
    activePageId.value = 'home'
    mode.value = 'preview'
    deselect()
  }

  // 保存沙盒页映射 → wn_settings.custom_page
  function save() {
    const ok = writeSettings('custom_page', clone(pages))
    return ok
  }

  // 保存主题包 → wn_settings.custom_theme
  function saveTheme(pkg) {
    return writeSettings('custom_theme', pkg)
  }

  function getSavedTheme() {
    return readSettings('custom_theme', null)
  }
  function hasSavedTheme() {
    return !!readSettings('custom_theme', null)
  }

  return { pages, activePageId, tree, mode, setActivePage, loadDefault, save, saveTheme, getSavedTheme, hasSavedTheme }
}
