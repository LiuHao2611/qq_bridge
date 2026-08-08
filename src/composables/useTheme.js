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

  const set = (t) => { theme.value = t; applyMode(t); persistMode(t) }
  const toggle = () => set(isDark.value ? 'light' : 'dark')
  const setPreset = (p) => {
    if (!VALID_PRESETS.includes(p)) p = ''
    preset.value = p; applyPreset(p); persistPreset(p)
  }

  return { theme, preset, isDark, set, setPreset, toggle }
}
