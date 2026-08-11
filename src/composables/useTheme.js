import { ref, computed } from 'vue'

const LS_KEY = 'wn_settings'
const PRESET_KEY = 'manju_theme'

const VALID_PRESETS = ['', 'gray', 'green', 'blue', 'gradient', 'splice']
const VALID_MODES = ['dark', 'light', 'auto']
const PRESET_CLASSES = ['t-gray', 't-green', 't-blue', 't-gradient', 't-splice']

/* ---------- 明暗模式 ---------- */
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

/* ---------- 主题切换过渡：临时挂 theme-anim，让颜色/背景/边框平滑过渡 ---------- */
let _animTimer = null
const animateTheme = () => {
  if (typeof document === 'undefined') return
  const html = document.documentElement
  html.classList.add('theme-anim')
  if (_animTimer) clearTimeout(_animTimer)
  // 略大于 CSS 过渡时长，确保过渡播完后再移除类（避免 hover 态被长时间锁定过渡）
  _animTimer = setTimeout(() => html.classList.remove('theme-anim'), 460)
}

/* ---------- 状态 ---------- */
const theme = ref(readMode())
const preset = ref(readPreset())

// 先按本地值应用，避免首屏闪烁
if (typeof window !== 'undefined') {
  applyMode(theme.value)
  applyPreset(preset.value)
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (theme.value === 'auto') applyMode('auto')
  })
}

export function useTheme() {
  const isDark = computed(() => {
    if (theme.value === 'auto') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches
    }
    return theme.value === 'dark'
  })

  const set = (t) => { theme.value = t; applyMode(t); persistMode(t); animateTheme() }
  const toggle = () => set(isDark.value ? 'light' : 'dark')
  const setPreset = (p) => {
    if (!VALID_PRESETS.includes(p)) p = ''
    preset.value = p; applyPreset(p); persistPreset(p); animateTheme()
  }

  return { theme, preset, isDark, set, setPreset, toggle }
}
