<script setup>
import { reactive, ref, watch, onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useTheme } from '../composables/useTheme'
import { getSettings, updateSettings, getLocalModels, testModelConnection } from '../api'

const router = useRouter()
const LS_KEY = 'wn_settings'
const APP_PREFIX = 'wn_'
const APP_VERSION = 'v1.3.0'

// 主题色圆点映射到 style.css 的配色预设（与 useTheme 的 VALID_PRESETS 对齐）
const ACCENT_TO_PRESET = {
  purple: '',
  pink: 'splice',
  blue: 'blue',
  green: 'green',
  orange: 'gradient',
}
const PRESET_TO_ACCENT = {
  '': 'purple',
  splice: 'pink',
  blue: 'blue',
  green: 'green',
  gradient: 'orange',
  gray: 'purple',
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
  accent: 'purple',
  density: 'standard',
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
const { set: setTheme, setPreset } = useTheme()

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
watch(settings, persist, { deep: true })
watch(() => settings.theme, setTheme)
watch(() => settings.accent, (v) => {
  const preset = ACCENT_TO_PRESET[v] ?? ''
  setPreset(preset)
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
      setPreset(ACCENT_TO_PRESET[settings.accent] ?? '')
    }
  } catch (e) {
    console.warn('settings backend failed, use local', e.message)
  }

  // 初始化 AI 模型配置：优先用后端 modelSettings，不存在则从旧字段迁移
  settings.modelSettings = buildDefaultModelSettings(settings.modelSettings || {})
  migrateLegacyModelSettings()

  // 为本地 Ollama 模型选择器填充列表
  refreshLocalLLMLists()
})

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
  { key: 'purple', color: '#8a5cff' }, { key: 'pink', color: '#ff4d8d' },
  { key: 'blue', color: '#4d9fff' }, { key: 'green', color: '#3ddc84' }, { key: 'orange', color: '#ff7a45' },
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
        <h1>设置</h1>
        <span class="muted">偏好会保存在本机浏览器与后端，刷新后仍生效（个人版·本地）</span>
      </div>
      <el-button type="primary" @click="saveAll">保存设置</el-button>
    </div>

    <el-tabs type="border-card" class="set-tabs">
      <!-- 外观 -->
      <el-tab-pane label="外观">
        <div class="field"><label>主题</label>
          <el-radio-group v-model="settings.theme">
            <el-radio value="light">浅色</el-radio>
            <el-radio value="dark">深色</el-radio>
            <el-radio value="auto">跟随系统</el-radio>
          </el-radio-group>
        </div>
        <div class="field"><label>字体大小</label>
          <el-slider v-model="settings.fontSize" :min="12" :max="20" show-input style="max-width:320px" />
        </div>
        <div class="field"><label>语言</label>
          <el-select v-model="settings.language" style="max-width:220px">
            <el-option label="简体中文" value="zh-CN" />
          </el-select>
        </div>
        <div class="field"><label>主题色</label>
          <div class="accents">
            <span v-for="a in accents" :key="a.key" class="ac" :class="{ on: settings.accent === a.key }"
                  :style="{ background: a.color }" @click="settings.accent = a.key" />
          </div>
        </div>
        <div class="field"><label>界面密度</label>
          <el-radio-group v-model="settings.density">
            <el-radio value="compact">紧凑</el-radio>
            <el-radio value="standard">标准</el-radio>
            <el-radio value="loose">宽松</el-radio>
          </el-radio-group>
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
        <div class="field"><label>存储路径</label>
          <el-input :model-value="settings.storagePath" disabled style="max-width:360px" />
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
    </el-tabs>
  </div>
</template>

<style scoped>
.field label { width: 90px; color: var(--sub); font-size: 13px; flex: 0 0 90px; }
.field { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.accents { display: flex; gap: 8px; }
.ac { width: 22px; height: 22px; border-radius: 6px; cursor: pointer; border: 2px solid transparent; }
.ac.on { border-color: #fff; box-shadow: 0 0 0 2px var(--pur); }
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
</style>
