<script setup>
import { reactive, ref, computed, watch, onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useTheme } from '../composables/useTheme'
import { setOrbSkin, ORB_SKINS, ORB_SKINS_CYBER } from '../composables/useDirectorOrb'
import { getSettings, updateSettings, getLocalModels, testModelConnection, post, getResourceCatalog, getConsistencyReadiness, deviceCapabilities } from '../api'
import TemplateEditor from './TemplateEditor.vue'
import ModelManager from './ModelManager.vue'

const router = useRouter()
const LS_KEY = 'wn_settings'
const APP_PREFIX = 'wn_'
const APP_VERSION = 'v1.3.0'

// 配色主题（强调色）映射到 style.css 的 t-* 预设（与 useTheme 的 VALID_PRESETS 严格对齐）
// 每个预设 ⇄ 一个强调色，双向唯一，保证往返一致；'default' = 原始默认主题（不覆盖变量）。
const ACCENT_TO_PRESET = {
  default: 'default',
  pink: 'splice',
  blue: 'blue',
  green: 'green',
  orange: 'gradient',
  gray: 'gray',
  p5: 'p5',
  cyberpunk: 'cyberpunk',
  custom: 'custom',
}
const PRESET_TO_ACCENT = {
  default: 'default',
  splice: 'pink',
  blue: 'blue',
  green: 'green',
  gradient: 'orange',
  gray: 'gray',
  p5: 'p5',
  cyberpunk: 'cyberpunk',
  custom: 'custom',
}

const DEFAULT_OLLAMA_URL = 'http://127.0.0.1:11434'
const DEFAULT_COMFY_URL = 'http://127.0.0.1:8188'

const buildDefaultModelSettings = (existing = {}) => ({
  create: {
    provider: existing.create?.provider || 'local',
    local: {
      baseUrl: existing.create?.local?.baseUrl || DEFAULT_OLLAMA_URL,
      model: existing.create?.local?.model || 'qwen3.5:9b',
    },
    online: {
      baseUrl: existing.create?.online?.baseUrl || '',
      apiKey: existing.create?.online?.apiKey || '',
      model: existing.create?.online?.model || '',
    },
  },
  planning: {
    provider: existing.planning?.provider || 'local',
    local: {
      baseUrl: existing.planning?.local?.baseUrl || DEFAULT_OLLAMA_URL,
      model: existing.planning?.local?.model || 'qwen3.5:9b',
    },
    online: {
      baseUrl: existing.planning?.online?.baseUrl || '',
      apiKey: existing.planning?.online?.apiKey || '',
      model: existing.planning?.online?.model || '',
    },
  },
  video: {
    provider: existing.video?.provider || 'local',
    local: {
      baseUrl: existing.video?.local?.baseUrl || DEFAULT_COMFY_URL,
      model: existing.video?.local?.model || 'ComfyUI 本地视频工作流',
      workflowPath: existing.video?.local?.workflowPath || '',
    },
    online: {
      baseUrl: existing.video?.online?.baseUrl || '',
      apiKey: existing.video?.online?.apiKey || '',
      model: existing.video?.online?.model || '',
    },
  },
  img: {
    provider: existing.img?.provider || 'local',
    local: {
      baseUrl: existing.img?.local?.baseUrl || DEFAULT_COMFY_URL,
      model: existing.img?.local?.model || 'ComfyUI SD1.5 本地',
      checkpoint: existing.img?.local?.checkpoint || 'Realistic_Vision_V6.0_NV_B1_fp16.safetensors',
    },
    online: {
      baseUrl: existing.img?.online?.baseUrl || '',
      apiKey: existing.img?.online?.apiKey || '',
      model: existing.img?.online?.model || '',
    },
  },
})

const defaults = () => ({
  // 外观
  theme: 'dark',
  fontSize: 14,
  language: 'zh-CN',
  accent: 'p5',
  density: 'standard',
  orbSkin: 'sword', // 悬浮球化身皮肤（仅 P5 怪盗主题生效；random/sword/mask/tower/shackle）
  // P5 二次强化：可单独开关的签名元素家族（默认全开；仅 accent==='p5' 时挂 html.p5ft-* 启用类）
  p5StyleTypes: ['dialog', 'banner', 'relic', 'button', 'key', 'texture', 'poster'],
  // AI 模型：本地/在线可切换 + 自定义路径（向后兼容旧字段）
  createModel: 'qwen3.5:9b',
  videoModel: 'ComfyUI 本地视频工作流',
  imgModel: 'ComfyUI SD1.5 本地',
  planningModel: 'qwen3.5:9b',
  modelSettings: buildDefaultModelSettings(),
  defaultResolution: '720p',
  defaultRatio: '16:9',
  defaultDuration: 12,
  safetyLevel: 'standard',
  quality: 'standard',
  // 存储
  storagePath: '%APPDATA%/wanneng-drama/',
  autoCleanupDays: 30,
  maxSizeGb: 50,
  mediaQuality: 'keep',
  // 网络（仅本地后端连接参数）
  apiHost: '127.0.0.1',
  apiPort: 7788,
  timeoutS: 120,
  concurrency: 2,
  // 通知
  desktopNotify: true,
  soundNotify: true,
  quietStart: '22:00',
  quietEnd: '08:00',
  notifyTask: true,
  notifySystem: true,
  // 备份
  autoBackup: true,
  backupKeepDays: 30,
  logRetentionDays: 90,
})

const load = () => {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return { ...defaults(), ...JSON.parse(raw) }
  } catch (e) { /* 忽略损坏数据 */ }
  return defaults()
}

const settings = reactive(load())
const { set: setTheme, setPreset, applyFontSize, applyDensity, hasCustomTheme } = useTheme()
// 是否已保存自定义主题（控制设置页「自定义」色块是否可选；手册 T-2/T-3）
const customThemeSaved = ref(false)
onMounted(() => { customThemeSaved.value = hasCustomTheme() })

// 把旧版平面字段（createModel/planningModel/videoModel/imgModel）迁移到新版 modelSettings
const migrateLegacyModelSettings = () => {
  const ms = settings.modelSettings
  if (settings.createModel && !ms.create.local.model) {
    ms.create.local.model = settings.createModel
  }
  if (settings.planningModel && !ms.planning.local.model) {
    ms.planning.local.model = settings.planningModel
  }
  if (settings.videoModel && !ms.video.local.model) {
    ms.video.local.model = settings.videoModel
  }
  if (settings.imgModel && !ms.img.local.model) {
    ms.img.local.model = settings.imgModel
  }
  // 兼容此前可能只有 workflowPath 没有 model 的情况
  if (!ms.video.local.model && ms.video.local.workflowPath) {
    ms.video.local.model = ms.video.local.workflowPath
  }
}

// 模型来源/测试状态
const testStates = reactive({
  create: { loading: false, result: '' },
  planning: { loading: false, result: '' },
  video: { loading: false, result: '' },
  img: { loading: false, result: '' },
})

const MODEL_LABELS = {
  create: '默认创作模型',
  planning: '默认策划模型',
  video: '默认视频模型',
  img: '默认文生图',
}

const refreshLocalLLMLists = () => {
  // 用 settings 中配置的 Ollama 地址拉取模型；失败则保持现有 fallback
  const baseUrl = settings.modelSettings?.create?.local?.baseUrl || DEFAULT_OLLAMA_URL
  getLocalModels().then(models => {
    if (Array.isArray(models) && models.length) {
      localLLMs.value = models
      localPlanningModels.value = models
    }
  }).catch(() => {})
}

const runModelTest = async (key) => {
  // 让当前聚焦的输入框/下拉先失焦，确保 allow-create/filterable 的 el-select 值已同步到 v-model
  if (typeof document !== 'undefined' && document.activeElement) {
    document.activeElement.blur()
  }
  // 等待 Vue 更新一次 tick
  await new Promise(r => setTimeout(r, 50))
  const st = testStates[key]
  st.loading = true
  st.result = ''
  try {
    const cfg = settings.modelSettings[key]
    const payload = {
      kind: key,
      provider: cfg.provider,
      local: cfg.local,
      online: cfg.online,
    }
    const res = await testModelConnection(payload)
    if (res?.code === 0) {
      st.result = res.data?.ok ? `连接成功：${res.data.message || ''}` : `连接失败：${res.data?.message || '未知错误'}`
      if (res.data?.ok) ElMessage.success(st.result)
      else ElMessage.error(st.result)
    } else {
      st.result = `测试失败：${res?.message || '服务异常'}`
      ElMessage.error(st.result)
    }
  } catch (e) {
    st.result = `测试异常：${e.message || e}`
    ElMessage.error(st.result)
  } finally {
    st.loading = false
  }
}

const persist = () => {
  try { localStorage.setItem(LS_KEY, JSON.stringify({ ...settings })) } catch (e) {}
}
// 注：不再用 watch(settings, persist, {deep:true}) 全局自动保存 —— 否则「保存设置」按钮形同虚设。
// 主题/强调色由下方 apply watch 实时生效并持久化（符合用户预期）；
// 其余设置仅在点击「保存设置」时经 saveAll() 落盘（localStorage + 后端），使按钮成为真实闸门。
watch(() => settings.theme, setTheme)
watch(() => settings.accent, (v) => {
  const preset = ACCENT_TO_PRESET[v] ?? 'default'
  setPreset(preset)
})
// 字号 / 密度 / 语言：实时预览（直接作用于 DOM），持久化仍由「保存设置」统一落盘
watch(() => settings.fontSize, (v) => applyFontSize(v), { immediate: true })
watch(() => settings.density, (v) => applyDensity(v), { immediate: true })
watch(() => settings.language, (v) => { document.documentElement.lang = v || 'zh-CN' }, { immediate: true })
// P5 二次强化：accent 切换 / 勾选变化 → 同步 html.p5ft-* 启用类
watch(() => settings.accent, () => applyP5StyleTypes())
watch(() => settings.p5StyleTypes, () => applyP5StyleTypes(), { deep: true })

// 悬浮球皮肤：即时生效 + 即时持久（与配色主题一致，避免进入项目后被 initOrbSkinFromStorage 读回旧值）
const applyOrbSkin = (key) => {
  settings.orbSkin = key
  setOrbSkin(key)
  try { localStorage.setItem(LS_KEY, JSON.stringify({ ...settings })) } catch (e) {}
}
// P5 二次强化：签名元素家族（仅 accent==='p5' 时挂 html.p5ft-* 启用类，对应 style.css 的 html.t-p5.p5ft-* 门控）
const P5_FT_KEYS = ['dialog', 'banner', 'relic', 'button', 'key', 'texture', 'poster']
const P5_STYLE_TYPES = [
  { key: 'dialog', label: '对话框（4 变体）', desc: '云朵气泡 / 姓名+主气泡 / 斜切编号条 / 撕纸边，导演 Agent 发言与系统消息的漫画容器' },
  { key: 'banner', label: '双层错位横幅', desc: '白底黑字(半色调) + 黑底白字(网格) 上下错位斜切，区块标题带 / 功能路牌' },
  { key: 'relic', label: '信物徽章', desc: '多层嵌套菱形宝石（纸白/绯红/白核心）+ 辉光呼吸，任务完成 / 成就 / 秘宝' },
  { key: 'button', label: '按钮家族', desc: '贴纸按钮 / 描边图标按钮 / 快捷键组 / 异形倒计时条' },
  { key: 'key', label: '快捷键标签', desc: '白框黑底快捷键标签框（LSHIFT / TAB / 1 等），编辑器与导演面板提示' },
  { key: 'texture', label: '锯齿纹理带', desc: '页脚 / 面板底缘 / 空状态底部的漫画印刷感锯齿带' },
  { key: 'poster', label: '描边大标题', desc: '白字 + 黑描边 + 绯红硬影 + 倾斜的强化页头 / 空状态大标题' },
]
// 把 settings.p5StyleTypes 同步为 html 上的 p5ft-* 启用类；非 P5 主题清空（双保险，CSS 还需 html.t-p5）
function applyP5StyleTypes() {
  const html = document.documentElement
  P5_FT_KEYS.forEach((k) => html.classList.remove('p5ft-' + k))
  if (settings.accent === 'p5') {
    ;(settings.p5StyleTypes || []).forEach((k) => html.classList.add('p5ft-' + k))
  }
}
// 悬浮球皮肤列表主题感知（手册 §9.3）：夜城→赛博四件套；P5→怪盗四件套；其他主题→空（隐藏）
const orbSkins = computed(() => {
  if (settings.accent === 'cyberpunk') return ORB_SKINS_CYBER
  if (settings.accent === 'p5') return ORB_SKINS
  return []
})

const saveAll = async () => {
  persist()
  try {
    // 同步旧字段，保证旧代码读取 createModel 等仍有效
    settings.createModel = settings.modelSettings.create.local.model
    settings.planningModel = settings.modelSettings.planning.local.model
    settings.videoModel = settings.modelSettings.video.local.model
    settings.imgModel = settings.modelSettings.img.local.model
    await updateSettings({ ...settings })
    ElMessage.success('设置已保存到本地与后端')
  } catch (e) {
    ElMessage.warning('后端保存失败，已保留本地设置')
  }
}

// 真实清除本应用 localStorage
const clearCache = () => {
  const keys = []
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (k && k.startsWith(APP_PREFIX)) keys.push(k)
  }
  keys.forEach(k => localStorage.removeItem(k))
  Object.assign(settings, defaults())
  ElMessage.success('已清理本地缓存')
}

// 导出当前设置（及部分运行信息）为 JSON
const exportData = () => {
  const payload = {
    exportedAt: new Date().toISOString(),
    version: APP_VERSION,
    settings: { ...settings },
    userAgent: navigator.userAgent,
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'manju_settings_' + Date.now() + '.json'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  ElMessage.success('已导出设置到本地 JSON')
}

// 本地备份：导出设置 JSON（个人版无云端备份，以本地导出作为备份补偿）
const backupNow = () => {
  const payload = {
    backupAt: new Date().toISOString(),
    version: APP_VERSION,
    settings: { ...settings },
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'manju_backup_' + Date.now() + '.json'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  ElMessage.success('已创建本地备份文件')
}

// 一键重构前端：调用后端 /system/rebuild-frontend 执行 build_frontend.sh
const rebuildIng = ref(false)
const rebuildResult = ref(null)   // { success, exit_code, busy, output } | null
const reloadPage = () => location.reload()
const rebuildFrontend = async () => {
  try {
    await ElMessageBox.confirm(
      '将重新构建整个前端（执行 vite build，约 15–30 秒）。构建期间页面可能短暂不可用，完成后请刷新页面。是否继续？',
      '一键重构前端',
      { confirmButtonText: '开始重构', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  rebuildIng.value = true
  rebuildResult.value = null
  try {
    const r = await post('/system/rebuild-frontend', {}, { timeout: 180000 })
    rebuildResult.value = r
    if (r && r.success) {
      ElMessage.success('前端重构完成，请刷新页面以加载新版本')
    } else if (r && r.busy) {
      ElMessage.warning(r.output || '已有构建任务在执行')
    } else {
      ElMessage.error('前端重构失败（详见下方输出）')
    }
  } catch (e) {
    rebuildResult.value = { success: false, exit_code: -1, output: (e && e.message) || String(e) }
    ElMessage.error('前端重构请求失败：' + ((e && e.message) || e))
  } finally {
    rebuildIng.value = false
  }
}

// 真实网络测速：ping 本地后端 /health
const speedProgress = ref(0)
const speedIng = ref(false)
const speedResult = ref('')
let timer = null
const PING_COUNT = 5
const runSpeedTest = async () => {
  if (speedIng.value) return
  speedIng.value = true
  speedProgress.value = 0
  speedResult.value = ''
  const latencies = []
  const baseUrl = `http://${settings.apiHost}:${settings.apiPort}`
  for (let i = 0; i < PING_COUNT; i++) {
    const start = performance.now()
    try {
      await fetch(baseUrl + '/api/v1/health', { method: 'GET', cache: 'no-store' })
      latencies.push(Math.round(performance.now() - start))
    } catch (e) {
      latencies.push(null)
    }
    speedProgress.value = Math.round(((i + 1) / PING_COUNT) * 100)
    await new Promise(r => setTimeout(r, 180))
  }
  speedIng.value = false
  const valid = latencies.filter(x => x !== null)
  if (!valid.length) {
    speedResult.value = '无法连接到本地后端，请检查 API 服务器地址'
    ElMessage.error(speedResult.value)
  } else {
    const avg = Math.round(valid.reduce((a, b) => a + b, 0) / valid.length)
    const min = Math.min(...valid)
    const max = Math.max(...valid)
    speedResult.value = `平均延迟 ${avg}ms / 最低 ${min}ms / 最高 ${max}ms`
    ElMessage.success(speedResult.value)
  }
}
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

onMounted(async () => {
  // 拉取本地 Ollama 模型列表，填充创作/策划模型下拉
  try {
    const models = await getLocalModels()
    if (Array.isArray(models) && models.length) {
      localLLMs.value = models
      localPlanningModels.value = models
    }
  } catch (e) {
    // 保持 fallback 静态列表
  }

  // 同步后端保存的设置（本地主题/主题色选择优先）
  try {
    const backend = await getSettings()
    if (backend && typeof backend === 'object') {
      const preserved = { theme: settings.theme, accent: settings.accent }
      Object.assign(settings, backend)
      settings.theme = preserved.theme
      const validAccents = accents.map(a => a.key)
      settings.accent = validAccents.includes(backend.accent) ? backend.accent : preserved.accent
      // 全局预设（manju_theme）为全站统一基线：以启动期已由 useTheme 应用的 html.t-* 为准，
      // 并将 accent 选择器对齐到它。无 t-* 类 = 显式「默认」预设（original）。
      const _presetClass = [...document.documentElement.classList].find((c) => c.startsWith('t-'))
      const _globalPreset = _presetClass ? _presetClass.slice(2) : 'default'
      settings.accent = PRESET_TO_ACCENT[_globalPreset] || settings.accent
      // 自定义主题未保存时，不允许落入 custom（色块应置灰）
      if (settings.accent === 'custom' && !customThemeSaved.value) settings.accent = 'p5'
      setPreset(_globalPreset === 'custom' && !customThemeSaved.value ? 'p5' : _globalPreset)
    }
  } catch (e) {
    console.warn('settings backend failed, use local', e.message)
  }

  // P5 二次强化：按当前 accent 同步 html.p5ft-* 启用类（默认全开）
  applyP5StyleTypes()

  // 初始化 AI 模型配置：优先用后端 modelSettings，不存在则从旧字段迁移
  settings.modelSettings = buildDefaultModelSettings(settings.modelSettings || {})
  migrateLegacyModelSettings()

  // 为本地 Ollama 模型选择器填充列表
  refreshLocalLLMLists()

  // M1/M2：加载能力维度 + 资源注册表（能力维度声明 + 首尾帧分级）
  loadCapabilityCatalog()
  // P1 M3/M5：权重前置诚实探测（四杠杆所需权重就绪状态）
  loadWeightReadiness()
  // 多设备兼容：探测本机算力（GPU / 显存），诚实告知图像/视频生成可行性
  loadDeviceCaps()
})

// 设备算力探测与能力分级（用户诉求：多设备兼容，不止适配 1660Ti）
// 1660Ti 仅 6GB 显存 → 图像生成可行、视频生成不可行；前端如实展示，避免误导。
const deviceCaps = ref(null)   // { cuda_available, gpu_name, vram_total_mb, vram_total_gb, capabilities, video_gen_reason, recommendation }
const deviceLoading = ref(false)
async function loadDeviceCaps() {
  deviceLoading.value = true
  try {
    deviceCaps.value = await deviceCapabilities()
  } catch (e) {
    console.warn('device/capabilities failed', e && e.message)
    deviceCaps.value = { cuda_available: false, capabilities: { image_generation: false, video_generation: false }, video_gen_reason: '设备探测失败', recommendation: '' }
  } finally {
    deviceLoading.value = false
  }
}

// ---- M1/M2 能力维度 + 资源注册表（模型无关）----
const capabilityDimensions = ref([])   // 6 个能力维度（key/name/implementer/declare）
const resourceCatalog = ref(null)      // 资源清单（models/loras/ip_adapters/controlnets）
const catalogLoading = ref(false)
const catalogOnline = ref(false)

async function loadCapabilityCatalog() {
  catalogLoading.value = true
  try {
    const r = await getResourceCatalog()
    capabilityDimensions.value = (r && r.capability_dimensions) || []
    resourceCatalog.value = r || null
    catalogOnline.value = !!(r && r.online)
  } catch (e) {
    console.warn('resource/catalog failed', e.message)
    capabilityDimensions.value = []
    resourceCatalog.value = null
    catalogOnline.value = false
  } finally {
    catalogLoading.value = false
  }
}

// P1 M3/M5 权重前置诚实探测：四杠杆所需权重就绪状态
// 缺失时红色"缺失" + 缺失文件名/错误明细，避免静默降级成"无锁脸"。
const weightReadiness = ref(null)   // { weights:{ipadapter,clip_vision,controlnet,insightface}, all_ready, missing:[{name,error}] }
const weightLoading = ref(false)
const WEIGHT_LABELS = {
  ipadapter: 'IP-Adapter 权重',
  clip_vision: 'CLIP Vision 权重',
  controlnet: 'ControlNet 权重',
  insightface: 'InsightFace（人脸引擎）',
}
function weightStatus(name) {
  const w = weightReadiness.value
  if (!w || !w.weights) return 'unknown'
  return w.weights[name] ? 'ready' : 'missing'
}
function weightError(name) {
  const w = weightReadiness.value
  if (!w) return ''
  const m = (w.missing || []).find((x) => x.name === name)
  return m ? m.error : ''
}
async function loadWeightReadiness() {
  weightLoading.value = true
  try {
    weightReadiness.value = await getConsistencyReadiness()
  } catch (e) {
    console.warn('consistency/readiness failed', e && e.message)
    weightReadiness.value = { weights: {}, all_ready: false, missing: [] }
  } finally {
    weightLoading.value = false
  }
}

// 资源清单分区（与 /resource/catalog 返回键严格对齐）
const capBuckets = [
  { key: 'models', label: '模型 / 视频底模' },
  { key: 'ip_adapters', label: 'IP-Adapter' },
  { key: 'controlnets', label: 'ControlNet' },
  { key: 'loras', label: 'LoRA' },
]

// 声明参数值 / 首尾帧分级 格式化
function fmtVal(v) {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'object') {
    try { return JSON.stringify(v) } catch (e) { return String(v) }
  }
  return String(v)
}

function faInterp(item) {
  const fa = item.frame_anchor_grading || (item.capabilities && item.capabilities.frame_anchor)
  return !!(fa && fa.interpolation)
}

const checkUpdate = () => ElMessage(`当前版本 ${APP_VERSION} · 个人本地版通过 Git/版本管理器更新`)
const goHelp = () => router.push('/help')
const resetSettings = () => {
  Object.assign(settings, defaults())
  ElMessage.success('已恢复默认设置')
}

// 本地模型选项（Ollama 列表会从 /models/local 动态拉取；ComfyUI 保持静态常用项 + 可自定义输入）
const localLLMs = ref(['qwen3.5:9b', 'qwen2.5:7b', 'llama3.1:8b', '本地 Ollama 模型'])
const localPlanningModels = ref(['qwen3.5:9b', 'qwen2.5:7b', 'llama3.1:8b', '本地 Ollama 模型'])
const comfyVideoModels = ['ComfyUI 本地视频工作流', '本地 FFmpeg 合成']
const comfyImgModels = ['ComfyUI SD1.5 本地', 'ComfyUI SDXL 本地']
const resolutions = ['480p', '720p', '1080p', '2K', '4K']
const ratios = ['16:9', '9:16', '1:1', '4:3', '21:9']
const durations = [5, 8, 10, 12, 15]
const accents = [
  { key: 'default', label: '默认(原始紫)', color: '#8a5cff' },
  { key: 'pink', label: '粉紫拼接', color: '#ff4d8d' },
  { key: 'blue', label: '商务蓝', color: '#4d9fff' },
  { key: 'green', label: '清新绿', color: '#3ddc84' },
  { key: 'orange', label: '暖橙渐变', color: '#ff7a45' },
  { key: 'gray', label: '灰阶', color: '#9aa3b2' },
  { key: 'p5', label: 'P5 怪盗', color: '#E4002B' },
  { key: 'cyberpunk', label: '夜城 HUD', color: 'linear-gradient(135deg,#FEE100 0 50%,#00F0FF 50% 100%)' },
  { key: 'custom', label: '自定义', color: 'conic-gradient(#E4002B,#FEE100,#00F0FF)', custom: true },
]
const hotkeys = [
  { k: 'Ctrl + S', d: '保存当前编辑' }, { k: 'Ctrl + Z', d: '撤销' },
  { k: 'F11', d: '全屏' }, { k: 'Ctrl + 1~9', d: '切换主导航' },
]
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="p5-title">设置</h1>
        <span class="muted">偏好会保存在本机浏览器与后端，刷新后仍生效（个人版·本地）</span>
      </div>
      <el-button type="primary" @click="saveAll">保存设置</el-button>
    </div>

    <el-tabs type="border-card" class="set-tabs">
      <!-- 外观 -->
      <el-tab-pane label="外观">
        <div class="field"><label>明暗模式</label>
          <el-radio-group v-model="settings.theme">
            <el-radio value="light">浅色</el-radio>
            <el-radio value="dark">深色</el-radio>
            <el-radio value="auto">跟随系统</el-radio>
          </el-radio-group>
          <span class="muted">（与下方「配色主题」相互独立：明暗控制深/浅底，配色控制主色系）</span>
        </div>
        <div class="field"><label>字体大小</label>
          <el-slider v-model="settings.fontSize" :min="12" :max="20" show-input style="max-width:320px" />
          <span class="muted">{{ settings.fontSize }}px（全站生效，保存后持久）</span>
        </div>
        <div class="field"><label>语言</label>
          <el-select v-model="settings.language" style="max-width:220px">
            <el-option label="简体中文" value="zh-CN" />
            <el-option label="English" value="en" />
          </el-select>
        </div>
        <div class="field" style="align-items:flex-start"><label>配色主题</label>
          <div class="accents">
            <template v-for="a in accents" :key="a.key">
              <el-tooltip v-if="a.custom && !customThemeSaved" content="请先在「页面自定义」中保存主题" placement="top">
                <button type="button" class="ac-item locked" disabled @click.prevent>
                  <span class="ac" :style="{ background: a.color }"><span class="lock">🔒</span></span>
                  <span class="ac-label">{{ a.label }}</span>
                </button>
              </el-tooltip>
              <button v-else type="button" class="ac-item" :class="{ on: settings.accent === a.key }" @click="settings.accent = a.key">
                <span class="ac" :style="{ background: a.color }"></span>
                <span class="ac-label">{{ a.label }}</span>
              </button>
            </template>
          </div>
        </div>
        <div class="field" style="align-items:flex-start" v-if="orbSkins.length"><label>悬浮球皮肤</label>
          <div class="accents">
            <button v-for="s in orbSkins" :key="s.key" type="button" class="ac-item" :class="{ on: settings.orbSkin === s.key }" @click="applyOrbSkin(s.key)">
              <span class="ac" :style="{ background: s.key === 'random' ? (settings.accent === 'cyberpunk' ? 'conic-gradient(#00F0FF,#FEE100,#00F0FF)' : 'conic-gradient(#E4002B,#0B0B0B,#E4002B)') : (settings.accent === 'cyberpunk' ? '#00F0FF' : '#E4002B') }"></span>
              <span class="ac-label">{{ s.label }}</span>
            </button>
          </div>
          <span class="muted">（{{ settings.accent === 'cyberpunk' ? '夜城赛博四件套；悬浮球统一呈现 HUD 扫描框皮肤，四形态视觉换肤为增强项' : '仅 P5 怪盗主题生效；双击悬浮球可临时切换形态，设置项决定默认形态' }}）</span>
        </div>
        <div class="field" v-else><label>悬浮球皮肤</label><span class="muted">当前主题不支持悬浮球皮肤（仅 P5 / 夜城主题可用）</span></div>
        <div class="field" style="align-items:flex-start" v-if="settings.accent === 'p5'"><label>P5 风格元素</label>
          <div>
            <el-checkbox-group v-model="settings.p5StyleTypes" style="max-width:560px">
              <el-checkbox v-for="t in P5_STYLE_TYPES" :key="t.key" :value="t.key" :title="t.desc">{{ t.label }}</el-checkbox>
            </el-checkbox-group>
            <span class="muted">（仅 P5 怪盗主题生效；勾选的签名元素家族才会在全站显示，可单独关闭某类而不影响其余——区分 P5 7 类样式类型）</span>
          </div>
        </div>
        <div class="field"><label>界面密度</label>
          <el-radio-group v-model="settings.density">
            <el-radio value="compact">紧凑</el-radio>
            <el-radio value="standard">标准</el-radio>
            <el-radio value="loose">宽松</el-radio>
          </el-radio-group>
          <span class="muted">（影响表单间距，保存后持久）</span>
        </div>
      </el-tab-pane>

      <!-- AI 模型 -->
      <el-tab-pane label="AI 模型">
        <div class="model-cards">
          <el-card v-for="key in ['create','planning','video','img']" :key="key" class="model-card" shadow="never">
            <template #header>
              <div class="mc-header">
                <span class="mc-title">{{ MODEL_LABELS[key] }}</span>
                <el-radio-group v-model="settings.modelSettings[key].provider" size="small">
                  <el-radio-button value="local">本地</el-radio-button>
                  <el-radio-button value="online">在线</el-radio-button>
                </el-radio-group>
              </div>
            </template>

            <div v-if="settings.modelSettings[key].provider === 'local'">
              <div class="field"><label>服务地址</label>
                <el-input v-model="settings.modelSettings[key].local.baseUrl" style="max-width:380px" :placeholder="key === 'create' || key === 'planning' ? 'http://127.0.0.1:11434' : 'http://127.0.0.1:8188'" />
              </div>
              <div v-if="key === 'create'" class="field"><label>模型</label>
                <el-select v-model="settings.modelSettings.create.local.model" style="max-width:240px" filterable allow-create>
                  <el-option v-for="m in localLLMs" :key="m" :label="m" :value="m" />
                </el-select>
                <el-button size="small" text @click="refreshLocalLLMLists">刷新模型列表</el-button>
              </div>
              <div v-else-if="key === 'planning'" class="field"><label>模型</label>
                <el-select v-model="settings.modelSettings.planning.local.model" style="max-width:240px" filterable allow-create>
                  <el-option v-for="m in localPlanningModels" :key="m" :label="m" :value="m" />
                </el-select>
                <el-button size="small" text @click="refreshLocalLLMLists">刷新模型列表</el-button>
              </div>
              <div v-else-if="key === 'video'" class="field"><label>工作流</label>
                <el-select v-model="settings.modelSettings.video.local.model" style="max-width:280px" filterable allow-create>
                  <el-option v-for="m in comfyVideoModels" :key="m" :label="m" :value="m" />
                </el-select>
              </div>
              <div v-else-if="key === 'img'" class="field"><label>模型/工作流</label>
                <el-select v-model="settings.modelSettings.img.local.model" style="max-width:280px" filterable allow-create>
                  <el-option v-for="m in comfyImgModels" :key="m" :label="m" :value="m" />
                </el-select>
              </div>
              <div v-if="key === 'img'" class="field"><label>底模文件(checkpoint)</label>
                <el-input v-model="settings.modelSettings.img.local.checkpoint" style="max-width:420px"
                  placeholder="Realistic_Vision_V6.0_NV_B1_fp16.safetensors" />
                <span class="muted" style="margin-left:8px">对应秋葉 ComfyUI <code>models/checkpoints/</code> 下的 .safetensors 文件名</span>
              </div>
            </div>

            <div v-else>
              <div class="field"><label>API 地址</label>
                <el-input v-model="settings.modelSettings[key].online.baseUrl" style="max-width:380px" placeholder="https://api.openai.com/v1" />
              </div>
              <div class="field"><label>API Key</label>
                <el-input v-model="settings.modelSettings[key].online.apiKey" type="password" show-password style="max-width:380px" placeholder="sk-..." />
              </div>
              <div class="field"><label>模型名</label>
                <el-input v-model="settings.modelSettings[key].online.model" style="max-width:280px" placeholder="gpt-4o-mini / seedance-2.0-pro" />
              </div>
            </div>

            <div class="field" style="margin-bottom:0">
              <label></label>
              <el-button size="small" :loading="testStates[key].loading" @click="runModelTest(key)">
                {{ testStates[key].loading ? '测试中…' : '测试连接' }}
              </el-button>
              <span v-if="testStates[key].result" class="muted test-result">{{ testStates[key].result }}</span>
            </div>
          </el-card>
        </div>

        <div class="field"><label>默认参数</label>
          <el-select v-model="settings.defaultResolution" style="max-width:120px"><el-option v-for="r in resolutions" :key="r" :label="r" :value="r" /></el-select>
          <el-select v-model="settings.defaultRatio" style="max-width:110px;margin-left:8px"><el-option v-for="r in ratios" :key="r" :label="r" :value="r" /></el-select>
          <el-select v-model="settings.defaultDuration" style="max-width:110px;margin-left:8px"><el-option v-for="d in durations" :key="d" :label="d + '秒'" :value="d" /></el-select>
        </div>
        <div class="field"><label>安全等级</label>
          <el-radio-group v-model="settings.safetyLevel"><el-radio value="strict">严格</el-radio><el-radio value="standard">标准</el-radio><el-radio value="loose">宽松</el-radio></el-radio-group>
        </div>
        <div class="field"><label>生成质量</label>
          <el-radio-group v-model="settings.quality"><el-radio value="draft">草稿</el-radio><el-radio value="standard">标准</el-radio><el-radio value="hd">HD</el-radio></el-radio-group>
        </div>
      </el-tab-pane>

      <!-- 存储 -->
      <el-tab-pane label="存储">
        <div class="field"><label>存储位置</label>
          <el-input :model-value="settings.storagePath" disabled style="max-width:360px" />
          <span class="muted">（自动管理，跟随系统 APPDATA，无需手动修改）</span>
        </div>
        <div class="field"><label>缓存清理</label>
          <el-popconfirm title="确认清空本应用本地缓存？" confirm-button-text="清空" cancel-button-text="取消" @confirm="clearCache">
            <template #reference><el-button type="danger" plain>清除本地缓存</el-button></template>
          </el-popconfirm>
        </div>
        <div class="field"><label>自动清理</label>
          <el-input-number v-model="settings.autoCleanupDays" :min="0" :max="365" /><span class="muted" style="margin-left:8px">天前任务自动清理</span>
        </div>
        <div class="field"><label>最大占用</label>
          <el-input-number v-model="settings.maxSizeGb" :min="1" :max="500" /><span class="muted" style="margin-left:8px">GB 警告</span>
        </div>
        <div class="field"><label>媒体质量</label>
          <el-radio-group v-model="settings.mediaQuality"><el-radio value="keep">保留</el-radio><el-radio value="compress">压缩</el-radio><el-radio value="ondemand">按需</el-radio></el-radio-group>
        </div>
      </el-tab-pane>

      <!-- 网络 -->
      <el-tab-pane label="网络">
        <div class="field"><label>API 服务器</label>
          <el-input v-model="settings.apiHost" style="max-width:200px" /><span class="muted" style="margin:0 6px">:</span>
          <el-input-number v-model="settings.apiPort" :min="1" :max="65535" controls-position="right" style="width:140px" />
        </div>
        <div class="field"><label>请求超时</label>
          <el-input-number v-model="settings.timeoutS" :min="10" :max="300" /><span class="muted" style="margin-left:8px">秒</span>
        </div>
        <div class="field"><label>并发数</label>
          <el-input-number v-model="settings.concurrency" :min="1" :max="10" /><span class="muted" style="margin-left:8px">同时进行的生成任务（个人版单任务）</span>
        </div>
        <div class="field" style="align-items:flex-start"><label>后端测速</label>
          <div style="flex:1">
            <el-button type="primary" :loading="speedIng" @click="runSpeedTest">{{ speedIng ? '测速中…' : '开始测速' }}</el-button>
            <el-progress :percentage="speedProgress" :stroke-width="10" style="max-width:360px;margin-top:12px" :status="speedProgress >= 100 ? 'success' : ''" />
            <div v-if="speedResult" style="margin-top:8px;font-size:13px;color:var(--txt)">{{ speedResult }}</div>
          </div>
        </div>
      </el-tab-pane>

      <!-- 通知 -->
      <el-tab-pane label="通知">
        <div class="field"><label>桌面通知</label><el-switch v-model="settings.desktopNotify" /></div>
        <div class="field"><label>声音</label><el-switch v-model="settings.soundNotify" /></div>
        <div class="field"><label>静默时段</label>
          <el-time-picker v-model="settings.quietStart" format="HH:mm" value-format="HH:mm" style="width:130px" />
          <span class="muted" style="margin:0 6px">至</span>
          <el-time-picker v-model="settings.quietEnd" format="HH:mm" value-format="HH:mm" style="width:130px" />
        </div>
        <div class="field"><label>接收类型</label>
          <el-checkbox v-model="settings.notifyTask">任务</el-checkbox>
          <el-checkbox v-model="settings.notifySystem">系统</el-checkbox>
        </div>
      </el-tab-pane>

      <!-- 快捷键 -->
      <el-tab-pane label="快捷键">
        <div class="field" style="align-items:flex-start"><label>快捷键</label>
          <div class="hk-list">
            <div v-for="h in hotkeys" :key="h.k" class="hk"><kbd>{{ h.k }}</kbd><span>{{ h.d }}</span></div>
          </div>
        </div>
        <div class="field"><label>恢复默认</label><el-button @click="resetSettings">恢复默认设置</el-button></div>
      </el-tab-pane>

      <!-- 备份 -->
      <el-tab-pane label="备份">
        <div class="field"><label>自动备份</label><el-switch v-model="settings.autoBackup" /></div>
        <div class="field"><label>保留时长</label>
          <el-select v-model="settings.backupKeepDays" style="max-width:160px"><el-option :value="7" label="7 天" /><el-option :value="30" label="30 天" /><el-option :value="90" label="90 天" /></el-select>
        </div>
        <div class="field"><label>日志保留</label>
          <el-select v-model="settings.logRetentionDays" style="max-width:160px"><el-option :value="30" label="30 天" /><el-option :value="90" label="90 天" /><el-option :value="365" label="365 天" /></el-select>
        </div>
        <div class="field"><label>一键备份</label><el-button @click="backupNow">立即备份</el-button></div>
        <div class="field"><label>导出数据</label><el-button type="primary" plain @click="exportData">导出我的数据</el-button></div>
      </el-tab-pane>

      <!-- 维护 -->
      <el-tab-pane label="维护">
        <div class="field" style="align-items:flex-start">
          <label>一键重构</label>
          <div style="flex:1">
            <el-button type="warning" :loading="rebuildIng" @click="rebuildFrontend">
              {{ rebuildIng ? '重构中…' : '一键重构前端' }}
            </el-button>
            <span class="muted" style="margin-left:10px">重新构建整个前端（vite build），构建完成后请刷新页面</span>
            <div v-if="rebuildResult" style="margin-top:14px">
              <el-alert
                :type="rebuildResult.success ? 'success' : (rebuildResult.busy ? 'warning' : 'error')"
                :title="rebuildResult.success ? '重构成功' : (rebuildResult.busy ? '构建进行中' : '重构失败')"
                :closable="false" show-icon
              />
              <div v-if="rebuildResult.success" style="margin-top:10px">
                <el-button size="small" type="primary" @click="reloadPage">刷新页面</el-button>
              </div>
              <pre v-if="rebuildResult.output" class="rebuild-log">{{ rebuildResult.output }}</pre>
            </div>
          </div>
        </div>
        <div class="field"><label>版本</label><span class="muted">前端 {{ APP_VERSION }} · 后端 v1.4-agent</span></div>
      </el-tab-pane>

      <!-- 关于 -->
      <el-tab-pane label="关于">
        <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
          <div style="width:56px;height:56px;border-radius:14px;background:var(--grad);display:flex;align-items:center;justify-content:center;font-size:28px;color:#fff">◆</div>
          <div>
            <div style="font-size:18px;font-weight:800">万能短剧 · AI 创作平台</div>
            <div class="muted">版本 {{ APP_VERSION }} · 个人本地版</div>
          </div>
        </div>
        <p style="line-height:1.8;color:var(--txt)">本平台为个人本地版，所有 AI 能力均运行在本机：Ollama 本地大模型负责剧本拆解与提示词润色，ComfyUI 本地工作流负责图像/视频生成，FFmpeg 负责最终合成。数据保存在本地 SQLite，无需云端 Key。</p>
        <p class="muted" style="line-height:1.8">开源协议：基于 Apache-2.0 授权，仅供学习与非商业用途。图标与示例素材版权归各自权利人所有。</p>
        <div style="display:flex;gap:12px;margin-top:16px">
          <el-button @click="checkUpdate">检查更新</el-button>
          <el-button type="primary" plain @click="goHelp">反馈入口</el-button>
        </div>
      </el-tab-pane>

      <!-- 能力维度（M1/M2 · 模型无关能力声明）-->
      <el-tab-pane label="能力维度">
        <div v-loading="catalogLoading">
          <div class="cap-banner">
            <el-tag :type="catalogOnline ? 'success' : 'info'" size="small">
              {{ catalogOnline ? 'ComfyUI 在线探测' : '离线 / ComfyUI 不可达' }}
            </el-tag>
            <span class="muted">共 {{ capabilityDimensions.length }} 个能力维度；视频生成 = 能力维度组合，模型只是能力的可替换实现。</span>
          </div>

          <!-- 多设备兼容：本机算力 / 显存 / 图像·视频生成可行性分级 -->
          <h4 class="cap-h">本机算力（多设备兼容 · 诚实分级）</h4>
          <div class="device-panel" v-loading="deviceLoading">
            <template v-if="deviceCaps">
              <div class="dev-row">
                <span class="dev-label">GPU</span>
                <span class="dev-val">{{ deviceCaps.gpu_name || (deviceCaps.cuda_available ? '未知 CUDA 设备' : '未检测到（CUDA 不可用）') }}</span>
              </div>
              <div class="dev-row">
                <span class="dev-label">显存</span>
                <span class="dev-val">{{ deviceCaps.vram_total_gb != null ? deviceCaps.vram_total_gb + ' GB（' + deviceCaps.vram_total_mb + ' MB）' : '无法读取' }}</span>
              </div>
              <div class="dev-caps">
                <span class="dev-cap" :class="deviceCaps.capabilities && deviceCaps.capabilities.image_generation ? 'ok' : 'bad'">
                  图像生成 {{ deviceCaps.capabilities && deviceCaps.capabilities.image_generation ? '✓ 可用' : '✕ 不可用' }}
                </span>
                <span class="dev-cap" :class="deviceCaps.capabilities && deviceCaps.capabilities.video_generation ? 'ok' : 'bad'">
                  视频生成 {{ deviceCaps.capabilities && deviceCaps.capabilities.video_generation ? '✓ 可用' : '✕ 不可用' }}
                </span>
              </div>
              <div class="dev-note" v-if="deviceCaps.video_gen_reason">{{ deviceCaps.video_gen_reason }}</div>
              <div class="dev-note muted" v-if="deviceCaps.recommendation">{{ deviceCaps.recommendation }}</div>
            </template>
            <el-empty v-else description="设备探测中…" :image-size="48" />
          </div>

          <!-- P1 M3/M5 权重前置诚实探测：四杠杆所需权重就绪状态 -->
          <h4 class="cap-h">权重就绪状态（Lock-Then-Animate 四杠杆所需）</h4>
          <div class="weight-readiness" v-loading="weightLoading">
            <div v-if="weightReadiness" class="wr-summary" :class="weightReadiness.all_ready ? 'ok' : 'bad'">
              {{ weightReadiness.all_ready ? '✓ 全部权重就绪，锁脸能力可用' : '⚠ 部分权重缺失，生成时将诚实报错（不会静默降级为无锁脸）' }}
            </div>
            <div class="wr-grid">
              <div v-for="(label, key) in WEIGHT_LABELS" :key="key" class="wr-item" :class="weightStatus(key)">
                <span class="wr-name">{{ label }}</span>
                <span class="wr-state" v-if="weightStatus(key) === 'ready'">✓ 就绪</span>
                <span class="wr-state missing" v-else-if="weightStatus(key) === 'missing'">✕ 缺失</span>
                <span class="wr-state unknown" v-else>探测中…</span>
                <span v-if="weightStatus(key) === 'missing' && weightError(key)" class="wr-err">{{ weightError(key) }}</span>
              </div>
            </div>
          </div>

          <!-- 6 个能力维度卡片 -->
          <h4 class="cap-h">能力维度清单（系统已实现）</h4>
          <div class="cap-grid">
            <div v-for="dim in capabilityDimensions" :key="dim.key" class="cap-card">
              <div class="cap-card-h">
                <span class="cap-name">{{ dim.name }}</span>
                <el-tag size="small" effect="plain">{{ dim.key }}</el-tag>
              </div>
              <div class="cap-imp">实现：{{ dim.implementer }}</div>
              <div class="cap-desc">{{ dim.description }}</div>
              <div class="cap-declare" v-if="dim.declare">
                <div class="cap-declare-t">声明参数</div>
                <div v-for="(v, k) in dim.declare" :key="k" class="cap-kv">
                  <span class="cap-k">{{ k }}</span><span class="cap-v">{{ fmtVal(v) }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- 资源清单：模型/节点/LoRA 各自声明能力维度 + 首尾帧分级 -->
          <template v-if="resourceCatalog">
            <h4 class="cap-h">资源清单（模型 / 节点 / LoRA 的能力维度声明 + 首尾帧分级）</h4>

            <div v-for="bucket in capBuckets" :key="bucket.key">
              <div class="cap-bucket-h" v-if="(resourceCatalog[bucket.key] || []).length">
                {{ bucket.label }}（{{ (resourceCatalog[bucket.key] || []).length }}）
              </div>
              <div v-for="item in (resourceCatalog[bucket.key] || [])" :key="bucket.key + '-' + item.name" class="res-card">
                <div class="res-h">
                  <span class="res-name">{{ item.name }}</span>
                  <span v-if="faInterp(item)" class="fa-badge fa-good">首尾帧插值</span>
                  <span v-else-if="item.frame_anchor_grading || (item.capabilities && item.capabilities.frame_anchor)" class="fa-badge">首帧外推</span>
                  <span v-if="item.available !== undefined" class="muted" style="font-size:12px">
                    {{ item.available ? '· 磁盘已确认' : '· 未确认' }}
                  </span>
                </div>
                <div class="res-caps">
                  <span v-for="(detail, ckey) in (item.capabilities || {})" :key="ckey"
                        class="cap-chip" :title="fmtVal(detail)">{{ ckey }}</span>
                  <span v-if="!item.capabilities || !Object.keys(item.capabilities).length" class="muted">
                    未声明能力维度
                  </span>
                </div>
              </div>
            </div>

            <!-- 节点族已声明实现 -->
            <div class="cap-bucket-h" v-if="(resourceCatalog.declared_implementations || []).length">
              节点族已声明实现（{{ resourceCatalog.declared_implementations.length }}）
            </div>
            <div v-for="item in (resourceCatalog.declared_implementations || [])" :key="'di-' + item.name" class="res-card">
              <div class="res-h">
                <span class="res-name">{{ item.name }}</span>
                <el-tag size="small" effect="plain">{{ item.type }}</el-tag>
                <span v-if="faInterp(item)" class="fa-badge fa-good">首尾帧插值</span>
                <span v-else-if="item.capabilities && item.capabilities.frame_anchor" class="fa-badge">首帧外推</span>
              </div>
              <div class="res-caps">
                <span v-for="(detail, ckey) in (item.capabilities || {})" :key="ckey" class="cap-chip">{{ ckey }}</span>
              </div>
            </div>
          </template>

          <el-empty v-else description="暂无资源注册表数据（已离线降级）" />
        </div>
      </el-tab-pane>

      <!-- P3 M2 用户自定义模板（ComfyUI workflow → 语义模板 → 注册进 P0 映射）-->
      <el-tab-pane label="模板">
        <p class="muted" style="margin:0 0 14px">
          把 ComfyUI 工作流导入为「语义模板」并注册进 P0 的 task_type→模板映射（同名覆盖系统模板，导演链路自动选用）。
          支持自动识别 checkpoint / 提示词 / seed / steps / 尺寸等占位符。
        </p>
        <TemplateEditor />
      </el-tab-pane>

      <!-- P4 M1 新模型零代码接入 -->
      <el-tab-pane label="模型">
        <ModelManager />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.field label { width: 90px; color: var(--sub); font-size: 13px; flex: 0 0 90px; }
.field { display: flex; align-items: center; gap: var(--density-gap, 12px); margin-bottom: var(--density-item-mb, 16px); flex-wrap: wrap; }
.accents { display: flex; flex-wrap: wrap; gap: 10px; }
.ac-item { display: flex; flex-direction: column; align-items: center; gap: 6px; background: transparent; border: 0; cursor: pointer; padding: 4px 6px; border-radius: 8px; }
.ac-item .ac { width: 26px; height: 26px; border-radius: 6px; border: 2px solid transparent; transition: transform .12s; }
.ac-item:hover .ac { transform: scale(1.08); }
.ac-item.on { background: rgba(138,92,255,.10); }
.ac-item.on .ac { border-color: #fff; box-shadow: 0 0 0 2px var(--pur); }
.ac-item.locked { opacity: .45; cursor: not-allowed; }
.ac-item.locked .ac { position: relative; }
.ac-item.locked .lock { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 13px; }
.ac-label { font-size: 11px; color: var(--sub); }
.ac-item.on .ac-label { color: var(--txt); font-weight: 600; }
.hk-list { display: flex; flex-direction: column; gap: 8px; }
.hk { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--txt); }
.hk kbd { background: var(--card-2); border: 1px solid var(--line); border-radius: 6px; padding: 2px 8px; font-family: monospace; }

.model-cards { display: flex; flex-direction: column; gap: 16px; margin-bottom: 24px; }
.model-card { background: var(--card-2); border: 1px solid var(--line); }
.model-card :deep(.el-card__header) { padding: 12px 16px; border-bottom: 1px solid var(--line); }
.model-card :deep(.el-card__body) { padding: 16px; }
.mc-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.mc-title { font-weight: 700; font-size: 14px; color: var(--txt); }
.test-result { font-size: 13px; max-width: 420px; line-height: 1.4; }
.rebuild-log {
  margin-top: 12px; max-height: 320px; overflow: auto; white-space: pre-wrap; word-break: break-all;
  background: var(--card-2); border: 1px solid var(--line); border-radius: 8px; padding: 12px;
  font-size: 12px; line-height: 1.5; color: var(--sub); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

/* M1/M2 能力维度 + 资源注册表 */
.cap-banner { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.cap-h { font-size: 14px; font-weight: 700; color: var(--txt); margin: 18px 0 12px; }
/* 多设备兼容：本机算力分级面板 */
.device-panel { background: var(--bg-2, #20202c); border: 1px solid var(--line, #2a2a38); border-radius: 12px; padding: 14px 16px; margin-bottom: 6px; }
.dev-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.dev-label { font-size: 12px; color: var(--sub, #7a8094); flex: 0 0 44px; font-weight: 600; }
.dev-val { font-size: 13px; color: var(--txt); font-weight: 600; }
.dev-caps { display: flex; gap: 10px; margin: 4px 0 10px; flex-wrap: wrap; }
.dev-cap { font-size: 12px; padding: 4px 12px; border-radius: 8px; font-weight: 700; }
.dev-cap.ok { background: rgba(74,222,128,.16); color: #4ade80; border: 1px solid rgba(74,222,128,.4); }
.dev-cap.bad { background: rgba(255,94,125,.16); color: #ff8da0; border: 1px solid rgba(255,94,125,.45); }
.dev-note { font-size: 12px; color: #ff8da0; line-height: 1.6; margin-top: 4px; }
.dev-note.muted { color: var(--sub, #7a8094); }
/* P1 M3/M5 权重就绪状态卡 */
.weight-readiness { margin: 4px 0 4px; }
.wr-summary { font-size: 12.5px; padding: 8px 12px; border-radius: 10px; margin-bottom: 10px; font-weight: 600; }
.wr-summary.ok { background: rgba(74,222,128,.12); color: #4ade80; }
.wr-summary.bad { background: rgba(255,94,125,.12); color: #ff8da0; }
.wr-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.wr-item { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px 12px; border-radius: 10px; background: var(--bg-2, #20202c); border: 1px solid var(--line, #2a2a38); }
.wr-item.ready { border-color: rgba(74,222,128,.4); }
.wr-item.missing { border-color: rgba(255,94,125,.45); background: rgba(255,94,125,.07); }
.wr-name { font-size: 12.5px; color: var(--txt); font-weight: 600; }
.wr-state { font-size: 11px; padding: 2px 9px; border-radius: 8px; font-weight: 700; }
.wr-state.ready { background: rgba(74,222,128,.16); color: #4ade80; }
.wr-state.missing { background: rgba(255,94,125,.16); color: #ff8da0; }
.wr-state.unknown { background: rgba(150,150,170,.14); color: var(--sub, #7a8094); }
.wr-err { flex-basis: 100%; font-size: 10.5px; color: #ff8da0; line-height: 1.4; word-break: break-all; margin-top: 2px; }
.cap-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
.cap-card { background: var(--card-2); border: 1px solid var(--line); border-radius: 10px; padding: 14px; }
.cap-card-h { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.cap-name { font-weight: 700; font-size: 14px; color: var(--txt); }
.cap-imp { font-size: 12px; color: var(--pur); margin-bottom: 6px; }
.cap-desc { font-size: 12px; color: var(--sub); line-height: 1.6; margin-bottom: 10px; }
.cap-declare { border-top: 1px dashed var(--line); padding-top: 8px; }
.cap-declare-t { font-size: 11px; color: var(--sub); margin-bottom: 6px; }
.cap-kv { display: flex; gap: 8px; font-size: 12px; line-height: 1.5; margin-bottom: 3px; }
.cap-k { color: var(--sub); flex: 0 0 84px; }
.cap-v { color: var(--txt); word-break: break-all; }
.cap-bucket-h { font-size: 13px; font-weight: 700; color: var(--txt); margin: 16px 0 8px; }
.res-card { background: var(--card-2); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; margin-bottom: 10px; }
.res-h { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.res-name { font-weight: 700; font-size: 13px; color: var(--txt); }
.res-caps { display: flex; flex-wrap: wrap; gap: 6px; }
.cap-chip { font-size: 11px; padding: 2px 8px; border-radius: 6px; background: var(--pur); color: #fff; opacity: .9; }
.fa-badge { font-size: 11px; padding: 2px 8px; border-radius: 6px; background: var(--sub); color: #fff; }
.fa-badge.fa-good { background: #2ecc71; }
</style>
