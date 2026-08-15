import { ref, computed } from 'vue'

const LS_KEY = 'wn_settings'
const PRESET_KEY = 'manju_theme'

// 注意：'default' 是「原始默认主题」的**显式**预设键（对应 html.t-default，不覆盖任何变量）。
// 早期用空串 '' 表示默认，但 readPreset 会把空串当作「未设置」回退 p5，导致「默认」无法持久。
// 现用 'default' 与「未设置→p5」明确区分。
const VALID_PRESETS = ['default', 'gray', 'green', 'blue', 'gradient', 'splice', 'p5', 'cyberpunk', 'custom']
const VALID_MODES = ['dark', 'light', 'auto']
const PRESET_CLASSES = ['t-default', 't-gray', 't-green', 't-blue', 't-gradient', 't-splice', 't-p5', 't-cyberpunk', 't-custom']

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
    // 显式选了非空预设 → 尊重选择；空值/缺失/无效 → 按手册默认启用 P5
    if (v && v !== '' && VALID_PRESETS.includes(v)) return v
  } catch (e) {}
  return 'p5'
}

const applyPreset = (preset) => {
  // 切走自定义主题时清理注入的样式标签（手册 T-6：保证无残留）
  removeCustomStyle()
  const html = document.documentElement
  PRESET_CLASSES.forEach((c) => html.classList.remove(c))
  if (preset && VALID_PRESETS.includes(preset)) html.classList.add('t-' + preset)
}

/* ---------- 自定义主题（页面自定义模块保存的「自定义」主题包） ---------- */
const CUSTOM_VARS_ID = 'custom-theme-vars'
const CUSTOM_RULES_ID = 'custom-theme-rules'

function readCustomTheme() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const s = JSON.parse(raw)
      return s.custom_theme || null
    }
  } catch (e) {}
  return null
}
function removeCustomStyle() {
  ;[CUSTOM_VARS_ID, CUSTOM_RULES_ID].forEach((id) => {
    const el = document.getElementById(id)
    if (el) el.remove()
  })
}
function injectCustomStyle(themePkg) {
  removeCustomStyle()
  if (!themePkg) return
  if (themePkg.variables && Object.keys(themePkg.variables).length) {
    const vars = Object.entries(themePkg.variables)
      .map(([k, v]) => `${k}: ${v}`).join('; ')
    const style = document.createElement('style')
    style.id = CUSTOM_VARS_ID
    style.textContent = `html.t-custom { ${vars} }`
    document.head.appendChild(style)
  }
  if (themePkg.rules && themePkg.rules.length) {
    const rules = themePkg.rules
      .map((r) => {
        const body = Object.entries(r.style).map(([k, v]) => `${k}: ${v}`).join('; ')
        return `html.t-custom ${r.selector} { ${body} }`
      })
      .join('\n')
    const style = document.createElement('style')
    style.id = CUSTOM_RULES_ID
    style.textContent = rules
    document.head.appendChild(style)
  }
}
function applyCustomTheme() {
  injectCustomStyle(readCustomTheme())
}
function hasCustomTheme() {
  return !!readCustomTheme()
}

/* ---------- P5 二次强化：签名元素家族启用类（html.p5ft-<key>） ----------
   与配色预设正交：默认全开；仅 preset==='p5' 时挂类，对应 style.css 的
   html.t-p5.p5ft-<key> 门控。必须在全局（此处）应用，而非仅 Settings 页，
   否则非设置页的 P5 家族 CSS 因缺 p5ft-* 而不生效。 */
const P5_FT_KEYS = ['dialog', 'banner', 'relic', 'button', 'key', 'texture', 'poster']
function applyP5FeatureTypes() {
  const html = document.documentElement
  P5_FT_KEYS.forEach((k) => html.classList.remove('p5ft-' + k))
  let sel = P5_FT_KEYS.slice()
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const s = JSON.parse(raw)
      if (Array.isArray(s.p5StyleTypes)) sel = s.p5StyleTypes
    }
  } catch (e) {}
  if (preset.value === 'p5') sel.forEach((k) => html.classList.add('p5ft-' + k))
}

const persistPreset = (preset) => {
  try { localStorage.setItem(PRESET_KEY, preset || '') } catch (e) {}
}

/* ---------- 字号 / 密度（外观，存于 wn_settings.fontSize / .density） ---------- */
const applyFontSize = (size) => {
  const html = document.documentElement
  const px = (typeof size === 'number' && size >= 12 && size <= 20) ? size : 14
  html.style.setProperty('--app-font-size', px + 'px')
  // Element Plus 基础字号变量，覆盖后全站 EP 组件文字随之缩放
  html.style.setProperty('--el-font-size-base', px + 'px')
}

const DENSITY_CLASSES = ['density-compact', 'density-standard', 'density-loose']
const applyDensity = (density) => {
  const html = document.documentElement
  html.classList.remove(...DENSITY_CLASSES)
  if (DENSITY_CLASSES.includes('density-' + density)) html.classList.add('density-' + density)
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
  if (preset.value === 'custom') applyCustomTheme()
  applyP5FeatureTypes()
  // 外观：字号 / 密度（存于 wn_settings，独立于预设/明暗）
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const s = JSON.parse(raw)
      applyFontSize(s.fontSize)
      applyDensity(s.density)
    }
  } catch (e) {}
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
    if (!VALID_PRESETS.includes(p)) p = 'default'
    preset.value = p; applyPreset(p); persistPreset(p); animateTheme()
    if (p === 'custom') applyCustomTheme()
    applyP5FeatureTypes()
  }

  return { theme, preset, isDark, set, setPreset, toggle, applyFontSize, applyDensity, applyCustomTheme, hasCustomTheme, applyP5FeatureTypes }
}
