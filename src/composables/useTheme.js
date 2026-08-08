import { ref, computed } from 'vue'

const LS_KEY = 'wn_settings'
const PRESET_KEY = 'manju_theme'
// 后台 manju_admin 的 API 端口（8910）；8099 前台从此拉取主题总开关
const REMOTE = 'http://127.0.0.1:8910/api/theme'

const VALID_PRESETS = ['', 'gray', 'green', 'blue', 'gradient', 'splice']
const VALID_MODES = ['dark', 'light', 'auto']
const PRESET_CLASSES = ['t-gray', 't-green', 't-blue', 't-gradient', 't-splice']

/* ---------- 明暗模式（原逻辑保留，兼容 TopBar / Settings） ---------- */
const readMode = () => {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const s = JSON.parse(raw)
      if (s.theme && VALID_MODES.includes(s.theme)) return s.theme
    }
  } catch (e) {}
  return 'dark'
}

const applyMode = (mode) => {
  const html = document.documentElement
  if (mode === 'auto') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    html.classList.remove('light', 'dark')
    html.classList.add(prefersDark ? 'dark' : 'light')
  } else {
    html.classList.remove('light', 'dark')
    html.classList.add(mode)
  }
}

const persistMode = (mode) => {
  try {
    const raw = localStorage.getItem(LS_KEY)
    const s = raw ? JSON.parse(raw) : {}
    s.theme = mode
    localStorage.setItem(LS_KEY, JSON.stringify(s))
  } catch (e) {}
}

/* ---------- 配色预设（与明暗正交，挂在 html.t-*） ---------- */
const readPreset = () => {
  try {
    const v = localStorage.getItem(PRESET_KEY)
    if (v && VALID_PRESETS.includes(v)) return v
  } catch (e) {}
  return ''
}

const applyPreset = (preset) => {
  const html = document.documentElement
  PRESET_CLASSES.forEach((c) => html.classList.remove(c))
  if (preset && VALID_PRESETS.includes(preset)) html.classList.add('t-' + preset)
}

const persistPreset = (preset) => {
  try { localStorage.setItem(PRESET_KEY, preset || '') } catch (e) {}
}

/* ---------- 状态 ---------- */
const theme = ref(readMode())    // 明暗模式（dark/light/auto）
const preset = ref(readPreset()) // 配色预设（gray/green/blue/gradient/splice/''）

// 先按本地值应用，避免首屏闪烁
if (typeof window !== 'undefined') {
  applyMode(theme.value)
  applyPreset(preset.value)
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (theme.value === 'auto') applyMode('auto')
  })
}

/* ---------- 远程同步（从后台 8910 拉取并应用，后台为总开关） ---------- */
let timer = null
const syncRemoteTheme = async () => {
  try {
    const res = await fetch(REMOTE, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
    if (!res.ok) return
    const json = await res.json()
    const data = json && json.data ? json.data : null
    if (!data) return
    const newPreset = VALID_PRESETS.includes(data.preset) ? data.preset : ''
    const newMode = VALID_MODES.includes(data.mode) ? data.mode : 'dark'
    if (newPreset !== preset.value) { preset.value = newPreset; applyPreset(newPreset) }
    if (newMode !== theme.value) { theme.value = newMode; applyMode(newMode) }
    persistPreset(newPreset)
    persistMode(newMode)
  } catch (e) {
    // 后台未连接时静默保留本地值（不破坏前台）
  }
}

export function initRemoteTheme(pollMs = 5000) {
  if (typeof window === 'undefined') return
  syncRemoteTheme()
  if (timer) clearInterval(timer)
  timer = setInterval(syncRemoteTheme, pollMs)
}

export function useTheme() {
  const isDark = computed(() => {
    if (theme.value === 'auto') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches
    }
    return theme.value === 'dark'
  })

  const set = (t) => { theme.value = t; applyMode(t); persistMode(t) }
  const toggle = () => set(isDark.value ? 'light' : 'dark')
  const setPreset = (p) => {
    if (!VALID_PRESETS.includes(p)) p = ''
    preset.value = p; applyPreset(p); persistPreset(p)
  }

  return { theme, preset, isDark, set, setPreset, toggle, syncRemoteTheme, initRemoteTheme }
}
