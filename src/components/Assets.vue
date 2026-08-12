<script setup>
defineOptions({ name: 'AssetsPhase' })
import { ref, reactive, computed, onMounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getProject, listAssets, listProjectAssets, listSegments,
  uploadGlobalAsset, uploadProjectAsset, uploadImage,
  importRole, importScene, importProp,
  updateAsset, deleteAsset,
  listStagesWithPoses, addStage, delStage, addPose, delPose,
  extractAssetsFromStory, batchCreateProjectAssets, getOllamaStatus,
  getLocalModels, testModelConnection, generateCharacterCardV2,
} from '../api'
import { saveDraft, loadDraft, clearDraft, draftAge, draftKey } from '../composables/useDraft'
import { pushHistory, genKey } from '../composables/useGenHistory'
import { pushGeneration } from '../composables/useDirectorOrb'
import { toast } from '../store'
import { splitVoice, joinVoice, stageNameFor } from '../assetMeta'
import { pruneRefs } from '../composables/useProject'
import ProjectFlowNav from './ProjectFlowNav.vue'
import RoleEditorModal from './RoleEditorModal.vue'
import HistoryPanel from './HistoryPanel.vue'
import {
  ASSET_TAB_OPTIONS, ASSET_STAGE_OPTIONS, ASSET_POSE_OPTIONS, ASSET_ROLE_TYPE_OPTIONS,
} from '../mock'
import { parseDocx } from '../parseDocx'

const route = useRoute()
const router = useRouter()
const pid = route.params.id

const TAB_MAP = Object.fromEntries(ASSET_TAB_OPTIONS.map((t) => [t.key, t.type]))
// 本集/全集 Tab 文案（直接内联到模板，避免任何 setup 短 const + minifier 重名引起的 `Cannot read properties of undefined` 报错。
// 历史：曾用 `const SCOPE = { project: '本集', global: '全集' }` + `{{ SCOPE.project }}`，prod build 时 minifier 把 SCOPE 与其它 const 重名，
// 触发 `r.project` 访问 `undefined`。改长名仍非零风险 → 直接字面量字符串最稳。）
const STAGE_OPTS = ASSET_STAGE_OPTIONS
const POSE_OPTS = ASSET_POSE_OPTIONS
const ROLE_TYPE_OPTS = ASSET_ROLE_TYPE_OPTIONS

const PLACEHOLDER = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0%25' stop-color='%2320242e'/><stop offset='100%25' stop-color='%233a3f4d'/></linearGradient></defs><rect width='200' height='200' fill='url(%23g)'/><text x='50%25' y='55%25' dominant-baseline='middle' text-anchor='middle' fill='%236b7283' font-size='70'>🖼️</text></svg>"

const project = reactive({ title: '我的项目', assets: { characters: [], scenes: [], props: [] } })
const tab = ref('characters')
const scope = ref('project') // project(本集) | global(全集)
const preview = ref('')
const busy = ref(false)
const search = ref('')
const tagFilter = ref('')

// 角色阶段/姿势编辑面板
const selectedRole = ref(null)
const stages = ref([])
const loadingStages = ref(false)
const stageForm = reactive({ open: false, assetId: '', stage_name: 'youth', age: 25, file: null })
const poseForm = reactive({ open: false, stageId: '', pose_name: 'front', file: null })

// 上传元数据弹窗（单文件/批量）
const uploadDialog = reactive({
  open: false,
  files: [], // { file, name, type, roleType, description, tags }
  isBatch: false,
  batchType: 'role',
  batchRoleType: 'protagonist',
})

// 从全集导入弹窗
const importDialog = reactive({
  open: false,
  loading: false,
  keyword: '',
  tab: 'characters',
  items: [],
})

// 从剧本一键提取资产
const extractDialog = reactive({
  open: false,
  step: 'source', // source | review | done
  loading: false,
  storySource: 'project', // project | paste
  pastedStory: '',
  segmentCount: 0,
  candidates: { roles: [], scenes: [], props: [] },
  meta: {},
  createdCount: 0,
  skippedNames: [],
  dragOver: false,
  dragCounter: 0,
  uploadedFileName: '',
  logs: [], // {time,level,msg} 提取过程实时日志
  // ---- 模型选择 & 在线服务配置（P1：assets v1.1.0 新增） ----
  localModels: [],                          // GET /models/local 拉取的本地模型名列表
  modelKind: 'local',                       // 'local' | 'online'
  selectedModel: '',                        // 当前选中的本地模型名
  selectedOnlineId: '',                     // 当前选中的在线服务 id
  providers: [],                            // 在线服务配置 [{id,name,baseUrl,apiKey,model,enabled}]，localStorage 持久化
  providerEditorOpen: false,                // provider 编辑弹窗开关
  editingProvider: null,                    // null=新增；字符串=编辑中的 provider id
  providerForm: { id: '', name: '', baseUrl: '', apiKey: '', model: '', enabled: true },
  testing: false,                           // 测试连接中
  testResult: '',                           // 测试结果文案
})
const selectedCandidates = ref({}) // { 'roles:林浩': true }
const editedCandidates = ref({}) // { 'roles:林浩': {...} }

const pushExtractLog = (msg, level = 'info') => {
  extractDialog.logs.push({
    time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
    level,
    msg,
  })
}

// ---- 模型选择 & 在线服务配置（localStorage 持久化） ----
const PROVIDERS_LS_KEY = 'manju_agent_model_providers_v1'
const loadProviders = () => {
  try {
    const arr = JSON.parse(localStorage.getItem(PROVIDERS_LS_KEY) || '[]')
    return Array.isArray(arr) ? arr : []
  } catch { return [] }
}
const saveProviders = () => {
  try { localStorage.setItem(PROVIDERS_LS_KEY, JSON.stringify(extractDialog.providers)) } catch {}
}
const newProviderId = () => 'p_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8)
// 刷新本地模型列表（GET /models/local），并默认选中第一个
const refreshLocalModels = async () => {
  try {
    extractDialog.localModels = await getLocalModels()
    if (!extractDialog.selectedModel && extractDialog.localModels.length) {
      extractDialog.selectedModel = extractDialog.localModels[0]
    }
  } catch (e) {
    console.warn('load local models failed:', e.message)
    extractDialog.localModels = []
  }
}
// 组装最终发给后端的 model_config
const buildModelConfig = () => {
  if (extractDialog.modelKind === 'local') {
    return extractDialog.selectedModel ? { provider: 'local', model: extractDialog.selectedModel } : null
  }
  const p = extractDialog.providers.find((x) => x.id === extractDialog.selectedOnlineId)
  if (!p) return null
  return { provider: 'openai-compatible', model: p.model, apiBase: p.baseUrl, apiKey: p.apiKey }
}
const currentOnlineProvider = computed(() =>
  extractDialog.providers.find((p) => p.id === extractDialog.selectedOnlineId) || null
)
// 打开 provider 编辑弹窗（p=null 新增；p=对象 编辑）
const openProviderEditor = (p) => {
  if (p) {
    extractDialog.editingProvider = p.id
    extractDialog.providerForm = {
      id: p.id, name: p.name || '', baseUrl: p.baseUrl || '', apiKey: p.apiKey || '', model: p.model || '', enabled: p.enabled !== false,
    }
  } else {
    extractDialog.editingProvider = null
    extractDialog.providerForm = { id: '', name: '', baseUrl: '', apiKey: '', model: '', enabled: true }
  }
  extractDialog.testResult = ''
  extractDialog.providerEditorOpen = true
}
const saveProvider = () => {
  const f = extractDialog.providerForm
  if (!String(f.name).trim()) { toast('请填写服务名'); return }
  if (!String(f.baseUrl).trim()) { toast('请填写 Base URL'); return }
  if (!String(f.model).trim()) { toast('请填写模型名'); return }
  if (extractDialog.editingProvider) {
    const idx = extractDialog.providers.findIndex((x) => x.id === extractDialog.editingProvider)
    if (idx >= 0) extractDialog.providers[idx] = { ...f }
  } else {
    f.id = newProviderId()
    extractDialog.providers.push({ ...f })
    extractDialog.selectedOnlineId = f.id
    extractDialog.modelKind = 'online'
  }
  saveProviders()
  extractDialog.providerEditorOpen = false
  toast('已保存')
}
const deleteProvider = (id) => {
  if (!confirm('删除该在线服务配置？')) return
  extractDialog.providers = extractDialog.providers.filter((p) => p.id !== id)
  if (extractDialog.selectedOnlineId === id) extractDialog.selectedOnlineId = ''
  saveProviders()
}
const toggleProviderEnabled = (id) => {
  const p = extractDialog.providers.find((x) => x.id === id)
  if (p) { p.enabled = !p.enabled; saveProviders() }
}
// 测试连接（POST /models/test）
const testProvider = async (p) => {
  extractDialog.testing = true
  extractDialog.testResult = ''
  try {
    const r = await testModelConnection({
      provider: 'openai-compatible',
      apiBase: p.baseUrl, apiKey: p.apiKey, model: p.model,
    })
    extractDialog.testResult = '✅ 连接成功' + (r && r.model ? `（模型 ${r.model}）` : '')
    toast('连接成功')
  } catch (e) {
    extractDialog.testResult = '❌ ' + (e.message || '连接失败')
    toast('连接失败：' + (e.message || ''))
  } finally {
    extractDialog.testing = false
  }
}

// 提取可中断：持有在途请求，停止按钮调用 abort()
// gpuTimer 改模块级单例：避免用户多次点「开始」造成 setInterval 累积（曾经出现过 15 个并发的风暴）
let _gpuTimer = null
function _clearGpuTimer() { if (_gpuTimer) { clearInterval(_gpuTimer); _gpuTimer = null } }
const extractController = ref(null)
const stopExtract = () => {
  if (extractController.value) extractController.value.abort()
}
// 模型输出自动草稿（P1）：打开弹窗时检测，提取成功后保存
const extractDraft = ref(null)
const extractHist = ref(null)
// 把候选集回填到 review 步骤（草稿恢复 / 历史覆盖共用）
const applyCandidates = (candidates, meta) => {
  extractDialog.candidates = candidates || { roles: [], scenes: [], props: [] }
  extractDialog.meta = meta || {}
  const next = {}
  ;['roles', 'scenes', 'props'].forEach((t) => {
    (extractDialog.candidates[t] || []).forEach((it) => {
      next[candidateKey(t, it.name)] = !isExistingAssetName(it.name)
    })
  })
  selectedCandidates.value = next
  editedCandidates.value = {}
  extractDialog.step = 'review'
}
const restoreExtractDraft = () => {
  const d = extractDraft.value
  if (!d) return
  applyCandidates(d.data?.candidates, d.data?.meta)
  extractDraft.value = null
  toast(`已恢复 ${draftAge(d.ts)} 的提取草稿`)
}
// 历史生成面板：覆盖使用某次提取结果
const onUseExtractHist = (record) => {
  applyCandidates(record.payload?.candidates, record.payload?.meta)
  toast('已覆盖使用历史提取结果')
}
const discardExtractDraft = () => {
  const d = extractDraft.value
  if (d) clearDraft(draftKey.extract(route.params.id))
  extractDraft.value = null
}

const openExtractDialog = async () => {
  extractDialog.open = true
  extractDialog.step = 'source'
  extractDialog.storySource = 'project'
  extractDialog.pastedStory = ''
  extractDialog.segmentCount = 0
  extractDialog.candidates = { roles: [], scenes: [], props: [] }
  extractDialog.meta = {}
  extractDialog.createdCount = 0
  extractDialog.skippedNames = []
  extractDialog.dragOver = false
  extractDialog.dragCounter = 0
  extractDialog.uploadedFileName = ''
  extractDialog.logs = []
  // 初始化模型选择 & provider 状态（v1.1.0 新增）
  extractDialog.modelKind = 'local'
  extractDialog.selectedModel = ''
  extractDialog.selectedOnlineId = ''
  extractDialog.localModels = []
  extractDialog.providerEditorOpen = false
  extractDialog.editingProvider = null
  extractDialog.providerForm = { id: '', name: '', baseUrl: '', apiKey: '', model: '', enabled: true }
  extractDialog.testing = false
  extractDialog.testResult = ''
  extractDialog.providers = loadProviders()
  // 默认选中第一个已启用的在线服务（如果有）
  if (extractDialog.providers.length && !extractDialog.selectedOnlineId) {
    const firstEnabled = extractDialog.providers.find((p) => p.enabled)
    if (firstEnabled) extractDialog.selectedOnlineId = firstEnabled.id
  }
  selectedCandidates.value = {}
  editedCandidates.value = {}
  try {
    const segs = await listSegments(pid)
    extractDialog.segmentCount = (segs || []).length
  } catch (e) {
    console.warn('load segments count failed:', e.message)
  }
  // 拉取本地模型列表（异步，不阻塞弹窗）
  refreshLocalModels()
  // 检测模型输出草稿（P1）
  const draft = loadDraft(draftKey.extract(pid))
  extractDraft.value = draft && draft.data && (draft.data.candidates?.roles?.length || draft.data.candidates?.scenes?.length || draft.data.candidates?.props?.length) ? draft : null
}

const closeExtractDialog = () => {
  extractDialog.open = false
}

const extractFileInput = ref(null)
const pickExtractFile = () => extractFileInput.value?.click()
const onExtractFileInput = (e) => {
  const f = e.target.files && e.target.files[0]
  if (f) readExtractScriptFile(f)
  e.target.value = '' // 允许重复选择同一文件
}

const onExtractDragEnter = (e) => {
  e.preventDefault()
  extractDialog.dragCounter++
  extractDialog.dragOver = true
}
const onExtractDragOver = (e) => {
  e.preventDefault()
}
const onExtractDragLeave = (e) => {
  e.preventDefault()
  extractDialog.dragCounter--
  if (extractDialog.dragCounter <= 0) {
    extractDialog.dragCounter = 0
    extractDialog.dragOver = false
  }
}
const onExtractDrop = (e) => {
  e.preventDefault()
  extractDialog.dragCounter = 0
  extractDialog.dragOver = false
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]
  if (f) readExtractScriptFile(f)
}

const readExtractScriptFile = async (file) => {
  const name = (file.name || '').toLowerCase()
  try {
    if (name.endsWith('.docx')) {
      const buf = await file.arrayBuffer()
      const text = await parseDocx(buf)
      if (!text.trim()) throw new Error('未从 .docx 中解析出任何文本')
      extractDialog.pastedStory = text.trim()
      extractDialog.uploadedFileName = file.name
      toast(`已导入剧本：${file.name}（${text.length} 字）`)
      return
    }
    const supported = ['.txt', '.md', '.srt']
    if (!supported.some((ext) => name.endsWith(ext))) {
      throw new Error('仅支持 .txt / .md / .srt / .docx 文件')
    }
    const reader = new FileReader()
    reader.onload = () => {
      let text = String(reader.result || '')
      if (name.endsWith('.srt')) {
        text = text.split(/\r?\n/)
          .filter((l) => !/^\d+$/.test(l.trim()) && !/-->/.test(l))
          .join('\n')
      }
      extractDialog.pastedStory = text.trim()
      extractDialog.uploadedFileName = file.name
      toast(`已导入剧本：${file.name}（${text.length} 字）`)
    }
    reader.onerror = () => toast('文件读取失败，请重试')
    reader.readAsText(file, 'utf-8')
  } catch (e) {
    toast('剧本导入失败：' + (e.message || '未知错误'))
  }
}

const isExistingAssetName = (name) => {
  const k = String(name).trim().toLowerCase()
  if (!k) return false
  return [...project.assets.characters, ...project.assets.scenes, ...project.assets.props].some((a) => a.name.toLowerCase() === k)
}

const candidateKey = (type, name) => type + ':' + name

const isCandidateSelected = (type, name) => !!selectedCandidates.value[candidateKey(type, name)]

const toggleCandidate = (type, name) => {
  const key = candidateKey(type, name)
  selectedCandidates.value[key] = !selectedCandidates.value[key]
}

const toggleGroup = (type, items, on) => {
  items.forEach((it) => {
    selectedCandidates.value[candidateKey(type, it.name)] = on
  })
}

const getCandidate = (type, name, raw) => {
  const key = candidateKey(type, name)
  return editedCandidates.value[key] || { ...raw }
}

const updateCandidate = (type, oldName, field, value) => {
  const key = candidateKey(type, oldName)
  const cur = editedCandidates.value[key] || { ...extractDialog.candidates[type].find((x) => x.name === oldName) }
  cur[field] = value
  editedCandidates.value[key] = cur
}

const runExtract = async () => {
  // 防重入：上次提取还在跑就拒绝（防止用户多次点「开始」启动多个并发闭包 = 历史上 15 个 setInterval 风暴的根因）
  if (extractDialog.loading) {
    toast('提取进行中，请先点「⏹ 停止」后再启动')
    return
  }
  let story = ''
  if (extractDialog.storySource === 'paste') {
    story = extractDialog.pastedStory.trim()
    if (!story) { toast('请粘贴剧本/故事文本'); return }
  }
  // 模型必须选好（v1.1.0 新增）
  const modelConfig = buildModelConfig()
  if (!modelConfig) {
    toast(extractDialog.modelKind === 'local' ? '请选择本地模型' : '请选择在线服务')
    return
  }
  extractDialog.loading = true
  extractDialog.logs = []
  extractDialog.meta = {}
  pushGeneration('text', '资产提取', 'running')
  _clearGpuTimer() // 任何残留旧 timer 启动前先清，防爆
  extractController.value = new AbortController()
  // 推理期间轮询 Ollama 运行分布，打印 GPU/CPU 占比，避免误以为在 CPU 跑
  let lastGpuLine = ''
  _gpuTimer = setInterval(async () => {
    try {
      const st = await getOllamaStatus()
      const ms = (st.models || [])
      if (ms.length) {
        const line = '🖥️ Ollama 运行分布：' + ms.map((m) => `${m.name} → GPU ${m.gpu_pct}% / CPU ${m.cpu_pct}%（显存 ${m.size_vram_gb}/${m.size_gb}GB）`).join('；')
        if (line !== lastGpuLine) { lastGpuLine = line; pushExtractLog(line) }
      }
    } catch (e) { /* 轮询失败忽略 */ }
  }, 5000)
  let elapsed = 0
  const elapsedTimer = setInterval(() => {
    elapsed++
    // 每 5 秒更新一次等待提示，避免日志刷屏
    if (elapsed % 5 === 0) {
      pushExtractLog('模型推理中… 已等待 ' + elapsed + ' 秒（长文本可能需要 1~5 分钟）')
    }
  }, 1000)
  try {
    pushExtractLog('准备识别… 文本长度 ' + story.length + ' 字')
    pushExtractLog('使用模型：' + (modelConfig.provider || 'local') + ' / ' + (modelConfig.model || '?'))
    pushExtractLog('请求后端提取接口（项目ID: ' + pid + '），最长等待 300 秒')
    const res = await extractAssetsFromStory(pid, story, modelConfig, { signal: extractController.value.signal })
    clearInterval(elapsedTimer)
    extractDialog.meta = res.meta || {}
    const model = (res.meta || {}).model || 'unknown'
    const provider = (res.meta || {}).provider || 'local'
    pushExtractLog('模型返回（' + provider + '/' + model + '），正在解析候选资产…')
    extractDialog.candidates = {
      roles: res.roles || [],
      scenes: res.scenes || [],
      props: res.props || [],
    }
    const total = (extractDialog.candidates.roles || []).length
      + (extractDialog.candidates.scenes || []).length
      + (extractDialog.candidates.props || []).length
    pushExtractLog('解析完成：识别 ' + total + ' 个候选（角色 ' + (extractDialog.candidates.roles || []).length
      + ' / 场景 ' + (extractDialog.candidates.scenes || []).length
      + ' / 道具 ' + (extractDialog.candidates.props || []).length + '）')
    // 默认全选；已存在的资产默认不选
    const next = {}
    ;['roles', 'scenes', 'props'].forEach((t) => {
      (extractDialog.candidates[t] || []).forEach((it) => {
        next[candidateKey(t, it.name)] = !isExistingAssetName(it.name)
      })
    })
    selectedCandidates.value = next
    editedCandidates.value = {}
    // 模型输出成功后立即写草稿（防误关/误取消丢失）
    const draftSaved = saveDraft(draftKey.extract(pid), {
      candidates: extractDialog.candidates,
      meta: extractDialog.meta,
    })
    if (draftSaved) pushExtractLog('🗂 提取草稿已自动保存（下次开弹窗可恢复）', 'info')
    extractDraft.value = null // 已落到 step=review，
    // 记录到左侧历史生成面板
    const h = pushHistory(genKey.extract(pid), {
      kind: 'text',
      title: '资产提取 · ' + ((res.meta || {}).model || '模型'),
      preview: '角色 ' + (extractDialog.candidates.roles?.length || 0) +
        ' · 场景 ' + (extractDialog.candidates.scenes?.length || 0) +
        ' · 道具 ' + (extractDialog.candidates.props?.length || 0),
      payload: { candidates: extractDialog.candidates, meta: extractDialog.meta },
    })
    if (h && extractHist.value) extractHist.value.refresh()
    extractDialog.step = 'review'
    pushGeneration('text', '资产提取', 'done')
    if ((res.meta || {}).error) {
      pushExtractLog('模型提示：' + res.meta.error, 'warn')
      toast('提取完成（模型异常：' + res.meta.error + '）')
    }
  } catch (e) {
    clearInterval(elapsedTimer)
    if (e && e.name === 'AbortError') {
      pushExtractLog('⏹ 已手动停止提取（在途请求已中断）', 'warn')
      pushGeneration('text', '资产提取', 'failed', '已手动停止')
    } else {
      pushExtractLog('提取失败：' + e.message, 'error')
      toast('提取失败：' + e.message)
      pushGeneration('text', '资产提取', 'failed', e.message)
    }
  } finally {
    _clearGpuTimer() // 模块级单例：清理此轮推理的 timer 引用
    clearInterval(elapsedTimer)
    extractDialog.loading = false
  }
}

const submitBatchCreate = async () => {
  const assets = []
  ;['roles', 'scenes', 'props'].forEach((t) => {
    const type = t === 'roles' ? 'role' : t === 'scenes' ? 'scene' : 'prop'
    extractDialog.candidates[t].forEach((it) => {
      if (!isCandidateSelected(t, it.name)) return
      const cur = getCandidate(t, it.name, it)
      const name = (cur.name || it.name).trim()
      if (!name) return
      assets.push({
        name,
        type,
        role_type: type === 'role' ? (cur.role_type || it.role_type || 'supporting') : undefined,
        description: cur.description || it.description || '',
        tags: [],
      })
    })
  })
  if (!assets.length) { toast('请至少选择一个资产'); return }
  extractDialog.loading = true
  try {
    const res = await batchCreateProjectAssets(pid, assets)
    extractDialog.createdCount = res.count || 0
    extractDialog.skippedNames = res.skipped || []
    extractDialog.step = 'done'
    await loadList()
  } catch (e) {
    toast('批量创建失败：' + e.message)
  } finally {
    extractDialog.loading = false
  }
}

const isDragging = ref(false)

// 角色卡 hover 三按钮：用 JS 状态驱动（比纯 CSS :hover 更稳，避免嵌套/指针事件导致不触发）
const hoveredId = ref(null)

// 小云雀式角色编辑弹窗
const editorVisible = ref(false)
const editingRole = ref(null)
const onRoleSaved = (updated) => {
  // 写回完整 description（含音色块），保证本地卡片与后端持久化一致
  const fullDesc = updated.raw?.description ?? joinVoice(updated.prompt, updated.voice)
  const patch = { ...updated, description: fullDesc }
  const list = project.assets.characters
  const idx = list.findIndex((a) => a.id === updated.id)
  if (idx > -1) {
    list[idx] = { ...list[idx], ...patch }
  }
  if (selectedRole.value && selectedRole.value.id === updated.id) {
    selectedRole.value = { ...selectedRole.value, ...patch }
  }
}
const openRoleEditor = (a) => {
  if (a.type !== 'role') return
  editingRole.value = a
  editorVisible.value = true
}

const toCard = (a) => {
  const tags = Array.isArray(a.tags) ? a.tags : []
  const { desc } = splitVoice(a.description || '')
  return {
    id: a.asset_id,
    name: a.name,
    url: a.cover_url || PLACEHOLDER,
    prompt: desc,
    type: a.type,
    isGlobal: a.is_global !== 0,
    roleType: a.role_type,
    hasFeature: a.type === 'role' && a.cover_url && !a.cover_url.includes('placeholder'),
    tags,
    raw: a,
  }
}

const allList = computed(() => project.assets[tab.value] || [])
const filteredList = computed(() => {
  let arr = allList.value
  const k = search.value.trim().toLowerCase()
  if (k) {
    arr = arr.filter((a) => a.name.toLowerCase().includes(k) || a.prompt.toLowerCase().includes(k))
  }
  const t = tagFilter.value.trim().toLowerCase()
  if (t) {
    arr = arr.filter((a) => a.tags.some((tag) => tag.toLowerCase().includes(t)))
  }
  return arr
})

const uniqueTags = computed(() => {
  const set = new Set()
  allList.value.forEach((a) => a.tags.forEach((t) => set.add(t)))
  return Array.from(set).filter(Boolean).slice(0, 20)
})

const emptyStateTitle = computed(() => {
  if (scope.value === 'global') return '全集暂无' + currentTabLabel.value
  return '本集暂无' + currentTabLabel.value
})
const emptyStateDesc = computed(() => {
  if (scope.value === 'global') {
    return '切换到「本集」上传后再复用，或在此上传到全集作为跨项目共享资产。'
  }
  return '拖拽图片到下方区域，或点「上传素材」添加。角色可被子分镜 @引用，场景/道具用于分镜设定。'
})
const currentTabLabel = computed(() => ASSET_TAB_OPTIONS.find((t) => t.key === tab.value)?.label || '')

const load = async () => {
  try {
    const p = await getProject(pid)
    if (p && p.name) project.title = p.name
  } catch (e) {
    console.warn('project title load failed:', e.message)
  }
  await loadList()
}

const loadList = async () => {
  busy.value = true
  try {
    const type = TAB_MAP[tab.value]
    let items = []
    if (scope.value === 'project') {
      items = await listProjectAssets(pid, { type })
    } else {
      const all = await listAssets(type)
      items = all.filter((a) => a.is_global !== 0)
    }
    project.assets.characters = items.filter((a) => a.type === 'role').map(toCard)
    project.assets.scenes = items.filter((a) => a.type === 'scene').map(toCard)
    project.assets.props = items.filter((a) => a.type === 'prop').map(toCard)
  } catch (e) {
    toast('资产库加载失败：' + e.message)
  } finally {
    busy.value = false
  }
}

const save = async (item) => {
  if (scope.value === 'global') { toast('全集资产请在「本集」中编辑'); return }
  try {
    const body = { name: item.name, description: item.prompt }
    if (item.type === 'role' && item.roleType) body.role_type = item.roleType
    await updateAsset(item.id, body)
    toast('已保存')
  } catch (e) {
    toast('保存失败: ' + e.message)
  }
}

const del = async (i) => {
  if (scope.value === 'global') { toast('全集资产不可在本页删除（影响所有项目）'); return }
  const item = filteredList.value[i]
  if (!item) return
  if (!confirm('确定删除本集资产「' + item.name + '」？\n关联的分镜 @引用 将失效。')) return
  try {
    await deleteAsset(item.id)
    // F1：级联清理所有片段中指向该资产的悬空 @引用（DB + keep-alive 编辑器内存态）
    await pruneRefs(pid, { assetId: item.id })
    const idx = project.assets[tab.value].findIndex((a) => a.id === item.id)
    if (idx > -1) project.assets[tab.value].splice(idx, 1)
    if (selectedRole.value && selectedRole.value.id === item.id) selectedRole.value = null
    toast('已删除')
  } catch (e) {
    toast('删除失败: ' + e.message)
  }
}

// ---- 拖拽 / 点击上传 ----
const onFileInput = (ev) => {
  const files = Array.from(ev.target.files || [])
  if (files.length) openUploadDialog(files)
  ev.target.value = ''
}

const onDragOver = (ev) => {
  ev.preventDefault()
  isDragging.value = true
}
const onDragLeave = (ev) => {
  ev.preventDefault()
  isDragging.value = false
}
const onDrop = (ev) => {
  ev.preventDefault()
  isDragging.value = false
  const files = Array.from(ev.dataTransfer.files || []).filter((f) => f.type.startsWith('image/'))
  if (files.length) openUploadDialog(files)
}

const readFileDataUrl = (file) => new Promise((resolve) => {
  const reader = new FileReader()
  reader.onload = (e) => resolve(e.target.result)
  reader.readAsDataURL(file)
})

const openUploadDialog = async (files) => {
  const type = TAB_MAP[tab.value]
  const enriched = await Promise.all(files.map(async (f) => ({
    file: f,
    preview: await readFileDataUrl(f),
    name: (f.name || '素材').replace(/\.[^.]+$/, ''),
    type,
    roleType: 'protagonist',
    description: '',
    tags: [],
  })))
  uploadDialog.files = enriched
  uploadDialog.isBatch = files.length > 1
  uploadDialog.batchType = type
  uploadDialog.batchRoleType = 'protagonist'
  uploadDialog.open = true
}

const tagInput = ref('')
const applyTag = (item) => {
  const v = tagInput.value.trim()
  if (!v) return
  item.tags = Array.from(new Set([...(item.tags || []), v]))
  tagInput.value = ''
}
const removeTag = (item, t) => {
  item.tags = (item.tags || []).filter((x) => x !== t)
}

const submitUpload = async () => {
  if (!uploadDialog.files.length) return
  busy.value = true
  let ok = 0
  try {
    for (const item of uploadDialog.files) {
      const opts = {
        name: item.name,
        type: uploadDialog.isBatch ? uploadDialog.batchType : item.type,
        roleType: (uploadDialog.isBatch ? uploadDialog.batchType : item.type) === 'role'
          ? (uploadDialog.isBatch ? uploadDialog.batchRoleType : item.roleType)
          : undefined,
        description: item.description,
        tags: item.tags,
      }
      let r
      if (scope.value === 'project') {
        r = await uploadProjectAsset(pid, item.file, opts)
      } else {
        r = await uploadGlobalAsset(item.file, opts)
      }
      if (r && r.asset) {
        const typeKey = Object.keys(TAB_MAP).find((k) => TAB_MAP[k] === r.asset.type)
        if (typeKey && project.assets[typeKey]) project.assets[typeKey].push(toCard(r.asset))
        ok++
      }
    }
    uploadDialog.open = false
    toast(`已上传 ${ok} 个${currentTabLabel.value}` + (scope.value === 'project' ? '到本集' : '到全集'))
    await loadList()
    // 自动切到对应 tab（批量上传可能类型与本 tab 不一致）
    if (uploadDialog.isBatch) {
      const target = ASSET_TAB_OPTIONS.find((t) => t.type === uploadDialog.batchType)?.key
      if (target) tab.value = target
    }
  } catch (e) {
    toast('上传失败: ' + e.message)
  } finally {
    busy.value = false
  }
}

// 一键复用：把全集资产深拷贝到本集
const reuse = async (item) => {
  if (scope.value !== 'global') return
  busy.value = true
  try {
    let r
    if (item.type === 'role') r = await importRole(item.id, pid)
    else if (item.type === 'scene') r = await importScene(item.id, pid)
    else r = await importProp(item.id, pid)
    toast('已复用「' + (r?.name || item.name) + '」到本集')
    await loadList()
  } catch (e) {
    toast('复用失败: ' + e.message)
  } finally {
    busy.value = false
  }
}

// 从全集导入弹窗
const openImportDialog = async () => {
  importDialog.open = true
  importDialog.loading = true
  importDialog.keyword = ''
  importDialog.tab = tab.value
  importDialog.items = []
  try {
    const all = await listAssets(TAB_MAP[importDialog.tab])
    importDialog.items = (all || []).filter((a) => a.is_global !== 0).map(toCard)
  } catch (e) {
    toast('全集库加载失败: ' + e.message)
  } finally {
    importDialog.loading = false
  }
}

// M3 悬浮球前端化 · 「从剧本生成角色」入口：对本集角色资产批量触发六步确定性管线（v2）。
// 命中真实 ComfyUI / 剧本设定时推送 translator / insight 到悬浮球；失败单条跳过不阻断其余。
const genRolesBusy = ref(false)
const generateRolesFromScript = async () => {
  const roles = (project.assets.characters || []).filter((a) => a.type === 'role')
  if (!roles.length) { toast('本集暂无角色资产，请先「从剧本提取」角色'); return }
  if (genRolesBusy.value) return
  genRolesBusy.value = true
  let okCount = 0, failCount = 0
  for (const r of roles) {
    pushGeneration('image', '角色定妆照·' + (r.name || r.id), 'running', '六步确定性管线生成中…')
    try {
      await generateCharacterCardV2(pid, r.id, {})
      pushGeneration('image', '角色定妆照·' + (r.name || r.id), 'done', '生成完成')
      okCount++
    } catch (e) {
      pushGeneration('image', '角色定妆照·' + (r.name || r.id), 'failed', e.message || '生成失败')
      failCount++
    }
  }
  genRolesBusy.value = false
  toast(`角色生成完成：${okCount} 成功 / ${failCount} 失败（详见悬浮球选型与品控）`)
}
const filteredImportItems = computed(() => {
  const k = importDialog.keyword.trim().toLowerCase()
  if (!k) return importDialog.items
  return importDialog.items.filter((a) => a.name.toLowerCase().includes(k) || a.prompt.toLowerCase().includes(k))
})
const switchImportTab = async (key) => {
  importDialog.tab = key
  importDialog.loading = true
  try {
    const all = await listAssets(TAB_MAP[key])
    importDialog.items = (all || []).filter((a) => a.is_global !== 0).map(toCard)
  } catch (e) {
    toast('全集库加载失败: ' + e.message)
  } finally {
    importDialog.loading = false
  }
}
const doImportFromDialog = async (item) => {
  busy.value = true
  try {
    let r
    if (item.type === 'role') r = await importRole(item.id, pid)
    else if (item.type === 'scene') r = await importScene(item.id, pid)
    else r = await importProp(item.id, pid)
    toast('已导入「' + (r?.name || item.name) + '」到本集')
    // 如果导入的类型与当前 tab 一致，刷新当前列表
    const importedType = ASSET_TAB_OPTIONS.find((t) => t.type === r?.type)?.key
    if (importedType && importedType === tab.value) {
      await loadList()
    } else if (importedType) {
      tab.value = importedType
      await loadList()
    }
  } catch (e) {
    toast('导入失败: ' + e.message)
  } finally {
    busy.value = false
  }
}

// ---- 角色阶段 / 姿势 ----
const selectRole = async (a) => {
  if (a.type !== 'role') return
  if (selectedRole.value && selectedRole.value.id === a.id) { selectedRole.value = null; return }
  selectedRole.value = a
  await loadStages(a)
}

const loadStages = async (a) => {
  loadingStages.value = true
  try {
    stages.value = await listStagesWithPoses(a.id)
  } catch (e) {
    toast('阶段加载失败: ' + e.message)
    stages.value = []
  } finally {
    loadingStages.value = false
  }
}

const openStageForm = (a) => {
  stageForm.open = true
  stageForm.assetId = a.id
  stageForm.stage_name = 'youth'
  stageForm.age = 25
  stageForm.file = null
}
const onStageFile = (ev) => { stageForm.file = ev.target.files?.[0] || null }
const submitStage = async () => {
  if (!stageForm.file) { toast('请选择阶段参考照'); return }
  busy.value = true
  try {
    const up = await uploadImage(stageForm.file)
    // F4：角色阶段统一为层级命名（roleName-suffix），与 RoleEditorModal / AssetStageManager 一致
    await addStage(stageForm.assetId, {
      stage_name: stageNameFor(selectedRole.value?.name, 'role', stageForm.stage_name),
      age: Number(stageForm.age) || 25,
      reference_image_url: up.url || '',
    })
    toast('已添加阶段')
    stageForm.open = false
    await loadStages(selectedRole.value)
  } catch (e) {
    toast('添加阶段失败: ' + e.message)
  } finally {
    busy.value = false
  }
}
const removeStage = async (st) => {
  if (!confirm('删除该阶段及其姿势？')) return
  try {
    await delStage(selectedRole.value.id, st.stage_id)
    // F1：级联清理该角色阶段在所有片段中的悬空 @引用
    await pruneRefs(pid, { assetId: selectedRole.value.id, stageId: st.stage_id })
    await loadStages(selectedRole.value)
  } catch (e) {
    toast('删除失败: ' + e.message)
  }
}

const openPoseForm = (st) => {
  poseForm.open = true
  poseForm.stageId = st.stage_id
  poseForm.pose_name = 'front'
  poseForm.file = null
}
const onPoseFile = (ev) => { poseForm.file = ev.target.files?.[0] || null }
const submitPose = async () => {
  if (!poseForm.file) { toast('请选择姿势参考照'); return }
  busy.value = true
  try {
    const up = await uploadImage(poseForm.file)
    await addPose(poseForm.stageId, { pose_name: poseForm.pose_name, reference_image_url: up.url || '' })
    toast('已添加姿势')
    poseForm.open = false
    await loadStages(selectedRole.value)
  } catch (e) {
    toast('添加姿势失败: ' + e.message)
  } finally {
    busy.value = false
  }
}
const removePose = async (st, p) => {
  try {
    await delPose(st.stage_id, p.pose_id)
    await loadStages(selectedRole.value)
  } catch (e) {
    toast('删除失败: ' + e.message)
  }
}

watch([scope, tab], () => {
  selectedRole.value = null
  stages.value = []
  search.value = ''
  tagFilter.value = ''
  loadList()
})

onMounted(load)
</script>

<template>
  <div class="page">
    <ProjectFlowNav :pid="pid" :step="1" />
    <div class="page-head">
      <button class="back" @click="router.push('/projects')">← 返回项目中心</button>
      <div>
        <h1>🖼 资产初始化 <span class="cold">（冷启动）</span></h1>
        <p class="muted">{{ project.title }} · 上传角色定妆照 / 场景参考 / 道具图，系统预处理；全集可一键复用至本集</p>
      </div>
      <div class="head-actions">
        <div class="tabs">
          <div class="tab" :class="{ on: scope === 'project' }" @click="scope = 'project'">本集</div>
          <div class="tab" :class="{ on: scope === 'global' }" @click="scope = 'global'">全集</div>
        </div>
        <div class="tabs">
          <div v-for="t in ASSET_TAB_OPTIONS" :key="t.key" class="tab" :class="{ on: tab === t.key }" @click="tab = t.key">{{ t.label }}</div>
        </div>
      </div>
    </div>

    <!-- 工具条 -->
    <div class="toolbar">
      <div class="search-box">
        <span class="search-icon">🔍</span>
        <input v-model="search" class="search-input" placeholder="搜索资产名称 / 描述…" />
      </div>
      <div v-if="uniqueTags.length" class="tag-bar">
        <span class="tag" :class="{ on: !tagFilter }" @click="tagFilter = ''">全部</span>
        <span v-for="t in uniqueTags" :key="t" class="tag" :class="{ on: tagFilter === t }" @click="tagFilter = tagFilter === t ? '' : t">{{ t }}</span>
      </div>
      <div class="spacer"></div>
      <label class="upload-inline" v-if="scope === 'project'">
        ＋ 上传素材
        <input type="file" multiple accept="image/*" style="display:none" @change="onFileInput" />
      </label>
      <label class="upload-inline" v-else>
        ＋ 上传到全集
        <input type="file" multiple accept="image/*" style="display:none" @change="onFileInput" />
      </label>
      <button v-if="scope === 'project'" class="btn import-btn" @click="openExtractDialog">📝 从剧本提取</button>
      <button v-if="scope === 'project'" class="btn import-btn" @click="openImportDialog">🌐 从全集导入</button>
      <button v-if="scope === 'project'" class="btn import-btn accent" :disabled="genRolesBusy" @click="generateRolesFromScript">
        🎬 从剧本生成角色{{ genRolesBusy ? '…' : '' }}
      </button>
    </div>

    <!-- 拖拽上传区（空状态 or 常驻快捷区） -->
    <div
      v-if="!filteredList.length && !busy"
      class="drop-zone"
      :class="{ drag: isDragging }"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
      @click="$event.target.querySelector('input')?.click()"
    >
      <div class="drop-inner">
        <div class="drop-icon">📁</div>
        <div class="drop-title">{{ emptyStateTitle }}</div>
        <div class="drop-desc">{{ emptyStateDesc }}</div>
        <label class="drop-btn">
          选择图片上传
          <input type="file" multiple accept="image/*" style="display:none" @change="onFileInput" />
        </label>
        <div v-if="scope === 'project'" class="drop-hint">支持 JPG / PNG / WEBP，角色定妆照将自动提取人脸特征向量</div>
      </div>
    </div>

    <div
      v-else
      class="drop-hint-bar"
      :class="{ drag: isDragging }"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
    >
      <span>📤 拖拽图片到此处可批量上传</span>
      <label class="link-upload">
        选择文件
        <input type="file" multiple accept="image/*" style="display:none" @change="onFileInput" />
      </label>
    </div>

    <!-- 加载骨架 -->
    <div v-if="busy && !filteredList.length" class="skeleton-grid">
      <div v-for="n in 6" :key="n" class="skeleton-card">
        <div class="skeleton-thumb"></div>
        <div class="skeleton-line"></div>
        <div class="skeleton-line short"></div>
      </div>
    </div>

    <!-- 资产网格 -->
    <div v-else class="asset-grid">
      <div class="asset-card" v-for="(a, i) in filteredList" :key="a.id">
        <div class="thumb-wrap" :class="{ draggable: a.type !== 'role', 'role-hovered': hoveredId === a.id }" :draggable="a.type !== 'role'" @mouseenter="hoveredId = a.id" @mouseleave="hoveredId = null" @dragstart="a.type !== 'role' ? $event.dataTransfer.setData('application/x-manju-asset', JSON.stringify({ asset_id: a.id, asset_type: a.type, name: a.name })) : null">
          <img :src="a.url" loading="lazy" @click="preview = a.url" />
          <!-- 角色卡片 hover 三按钮：拖动 / 编辑 / 预览 -->
          <div v-if="a.type === 'role'" class="thumb-overlay role-overlay">
            <button class="role-btn" title="角色卡不可直接拖入提示词（按 R1，请拖拽阶段卡引用）" @mousedown.stop>
              <el-icon><Grid /></el-icon>
            </button>
            <button class="role-btn" title="编辑角色" @click.stop="openRoleEditor(a)">
              <el-icon><EditPen /></el-icon>
            </button>
            <button class="role-btn" title="预览角色图" @click.stop="preview = a.url">
              <el-icon><ZoomIn /></el-icon>
            </button>
          </div>
          <!-- 非角色卡片保持原交互 -->
          <div v-else class="thumb-overlay">
            <button class="btn sm" @click="preview = a.url">预览</button>
            <button v-if="scope === 'global'" class="btn sm" @click="reuse(a)">复用至本集</button>
            <button v-else class="btn sm ghost" @click="del(i)">删除</button>
          </div>
          <span v-if="a.hasFeature" class="badge feature">特征已提取</span>
          <span v-if="a.isGlobal" class="badge global">全集</span>
          <span v-if="a.roleType" class="badge role" :class="a.roleType">{{ ROLE_TYPE_OPTS.find(o=>o.value===a.roleType)?.label || a.roleType }}</span>
          <button v-if="a.type === 'role'" class="role-toggle" @click="selectRole(a)">
            {{ (selectedRole && selectedRole.id === a.id) ? '收起阶段' : '阶段 / 姿势' }}
          </button>
        </div>
        <div class="info">
          <div class="name">
            <input class="input" v-model="a.name" :disabled="scope === 'global'" @change="save(a)" placeholder="资产名称" />
          </div>
          <textarea class="input" v-model="a.prompt" :disabled="scope === 'global'" placeholder="提示词 / 描述…" @change="save(a)"></textarea>
          <div v-if="a.tags.length" class="tag-row">
            <span v-for="tag in a.tags.slice(0, 3)" :key="tag" class="asset-tag" :title="tag">{{ tag }}</span>
          </div>
        </div>
      </div>
      <div v-if="!filteredList.length && !busy" class="empty" style="grid-column: 1 / -1">
        没有匹配「{{ search || tagFilter }}」的资产，换个关键词试试。
      </div>
    </div>

    <!-- 角色多阶段 + 多姿势（KB §02/§04 §4.4.1） -->
    <div v-if="selectedRole && tab === 'characters'" class="role-panel">
      <div class="rp-head">
        <div class="rp-title">🎭 {{ selectedRole.name }} · 阶段与姿势</div>
        <button class="btn sm" @click="openStageForm(selectedRole)">＋ 添加阶段</button>
      </div>
      <div v-if="loadingStages" class="muted" style="padding:12px">加载阶段中…</div>
      <div v-else-if="!stages.length" class="empty">该角色暂无阶段，点「添加阶段」上传参考照（如童年/青年/老年）。</div>
      <div v-else class="stage-list">
        <div class="stage-card" v-for="st in stages" :key="st.stage_id">
          <div class="stage-head">
            <span class="stage-name">{{ (STAGE_OPTS.find(o=>o.value===st.stage_name)||{label:st.stage_name}).label }}（{{ st.age }}岁）</span>
            <button class="btn sm ghost" @click="removeStage(st)">删除阶段</button>
          </div>
          <div class="stage-img"><img :src="st.reference_image_url || PLACEHOLDER" @click="preview = st.reference_image_url" /></div>
          <div class="pose-row">
            <div class="pose-chip" v-for="p in (st.poses||[])" :key="p.pose_id">
              <img :src="p.reference_image_url || PLACEHOLDER" @click="preview = p.reference_image_url" />
              <span class="pose-label">{{ (POSE_OPTS.find(o=>o.value===p.pose_name)||{label:p.pose_name}).label }}</span>
              <button class="pose-del" @click="removePose(st, p)">×</button>
            </div>
            <button class="pose-add" @click="openPoseForm(st)">＋ 姿势</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 上传元数据弹窗 -->
    <div class="modal-mask" v-if="uploadDialog.open" @click="uploadDialog.open=false">
      <div class="modal-box wide" @click.stop>
        <h3>上传 {{ ASSET_TAB_OPTIONS.find(t=>t.type===(uploadDialog.isBatch?uploadDialog.batchType:uploadDialog.files[0]?.type))?.label }}</h3>
        <!-- 批量设置 -->
        <div v-if="uploadDialog.isBatch" class="batch-bar">
          <label>统一类型</label>
          <select v-model="uploadDialog.batchType" class="input">
            <option v-for="t in ASSET_TAB_OPTIONS" :key="t.key" :value="t.type">{{ t.label }}</option>
          </select>
          <template v-if="uploadDialog.batchType === 'role'">
            <label>统一角色定位</label>
            <select v-model="uploadDialog.batchRoleType" class="input">
              <option v-for="o in ROLE_TYPE_OPTS" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </template>
        </div>
        <!-- 文件列表 -->
        <div class="upload-file-list">
          <div v-for="(item, idx) in uploadDialog.files" :key="idx" class="upload-file-row">
            <div class="preview-thumb"><img v-if="item.preview" :src="item.preview" /></div>
            <div class="file-fields">
              <input v-model="item.name" class="input" placeholder="名称" />
              <select v-if="!uploadDialog.isBatch" v-model="item.type" class="input">
                <option v-for="t in ASSET_TAB_OPTIONS" :key="t.key" :value="t.type">{{ t.label }}</option>
              </select>
              <select v-if="(uploadDialog.isBatch ? uploadDialog.batchType : item.type) === 'role'" v-model="item.roleType" class="input">
                <option v-for="o in ROLE_TYPE_OPTS" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
              <input v-model="item.description" class="input" placeholder="描述 / 提示词" />
              <div class="tag-editor">
                <span v-for="t in item.tags" :key="t" class="asset-tag">{{ t }} <i @click="removeTag(item, t)">×</i></span>
                <input v-model="tagInput" class="input tag-input" placeholder="回车添加标签" @keydown.enter.prevent="applyTag(item)" />
              </div>
            </div>
          </div>
        </div>
        <div class="modal-acts">
          <button class="btn sm ghost" @click="uploadDialog.open=false">取消</button>
          <button class="btn sm" :disabled="busy" @click="submitUpload">{{ busy ? '上传中…' : '确认上传' }}</button>
        </div>
      </div>
    </div>

    <!-- 从全集导入弹窗 -->
    <div class="modal-mask" v-if="importDialog.open" @click="importDialog.open=false">
      <div class="modal-box wide" @click.stop>
        <h3>🌐 从全集导入到本集</h3>
        <div class="import-toolbar">
          <div class="tabs">
            <div v-for="t in ASSET_TAB_OPTIONS" :key="t.key" class="tab" :class="{ on: importDialog.tab === t.key }" @click="switchImportTab(t.key)">{{ t.label }}</div>
          </div>
          <div class="search-box" style="width:220px">
            <span class="search-icon">🔍</span>
            <input v-model="importDialog.keyword" class="search-input" placeholder="搜索全集资产…" />
          </div>
        </div>
        <div v-if="importDialog.loading" class="muted" style="padding:20px;text-align:center">加载中…</div>
        <div v-else-if="!filteredImportItems.length" class="empty">全集中没有匹配资产</div>
        <div v-else class="import-grid">
          <div v-for="a in filteredImportItems" :key="a.id" class="import-card">
            <div class="import-thumb"><img :src="a.url" /></div>
            <div class="import-name">{{ a.name }}</div>
            <button class="btn sm" @click="doImportFromDialog(a)">导入</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 从剧本提取资产弹窗 -->
    <div class="modal-mask" v-if="extractDialog.open" @click="closeExtractDialog">
      <div class="modal-box wide extract-box" @click.stop>
        <!-- 左侧：历史生成记录（嵌入弹窗左侧，默认折叠，点击展开可选择覆盖使用） -->
        <HistoryPanel
          ref="extractHist"
          :storage-key="genKey.extract(route.params.id)"
          title="提取历史"
          kind-filter="text"
          :embedded="true"
          @use="onUseExtractHist"
        />

        <div class="extract-main">
        <h3>📝 从剧本一键提取资产</h3>

        <!-- 步骤 1：选择剧本来源 -->
        <div v-if="extractDialog.step === 'source'">
          <!-- 模型输出草稿恢复提示（P1） -->
          <div v-if="extractDraft" class="extract-draft-banner">
            <span>🗂 检测到 {{ draftAge(extractDraft.ts) }} 的提取草稿（角色 {{ (extractDraft.data.candidates.roles||[]).length }} / 场景 {{ (extractDraft.data.candidates.scenes||[]).length }} / 道具 {{ (extractDraft.data.candidates.props||[]).length }}）</span>
            <button class="btn sm" style="margin-left:8px" @click="restoreExtractDraft">恢复</button>
            <button class="btn sm ghost" @click="discardExtractDraft">丢弃</button>
          </div>
          <p class="muted" style="margin-bottom:12px">AI 将从剧本/故事中识别角色、场景、道具，供你确认后批量创建为本集资产。</p>
          <div class="extract-source">
            <label class="extract-option" :class="{ on: extractDialog.storySource === 'project' }">
              <input type="radio" value="project" v-model="extractDialog.storySource" />
              <div>
                <div class="opt-title">使用项目已导入的场次/分镜</div>
                <div class="opt-desc">共 {{ extractDialog.segmentCount }} 个场次；将读取所有片段的场景描述与分镜文本进行分析</div>
              </div>
            </label>
            <label class="extract-option" :class="{ on: extractDialog.storySource === 'paste' }">
              <input type="radio" value="paste" v-model="extractDialog.storySource" />
              <div>
                <div class="opt-title">粘贴剧本/故事全文</div>
                <div class="opt-desc">适用于尚未进行分场拆解的项目</div>
              </div>
            </label>
          </div>
          <div v-if="extractDialog.storySource === 'paste'" style="margin-top:12px">
            <div class="extract-drop">
              <textarea
                v-model="extractDialog.pastedStory"
                class="extract-story"
                :class="{ over: extractDialog.dragOver }"
                placeholder="在此粘贴完整剧本或故事…"
                @dragenter.prevent="onExtractDragEnter"
                @dragover.prevent="onExtractDragOver"
                @dragleave.prevent="onExtractDragLeave"
                @drop.prevent="onExtractDrop"
              ></textarea>
              <div v-if="!extractDialog.pastedStory.trim() && !extractDialog.dragOver" class="extract-drop-hint">
                <span>📎 拖入 .txt / .md / .srt / .docx 文件到此处</span>
                <span class="sub">或直接粘贴文本</span>
              </div>
              <div v-if="extractDialog.dragOver" class="extract-drop-hint over">松开即可导入</div>
            </div>
            <input ref="extractFileInput" type="file" accept=".txt,.md,.srt,.docx" style="display:none" @change="onExtractFileInput" />
            <div class="extract-formats">
              <span>支持格式：txt / markdown / srt（自动剔除时间轴） / docx（纯前端 OOXML 解压）</span>
              <span class="link" @click="pickExtractFile">点击选择文件</span>
            </div>
            <div v-if="extractDialog.uploadedFileName" class="extract-file-tag">
              <span>📄 {{ extractDialog.uploadedFileName }}</span>
              <span class="link" @click="extractDialog.uploadedFileName = ''; extractDialog.pastedStory = ''">清除</span>
            </div>
          </div>

          <!-- ====== 选择识别模型（v1.1.0 新增） ====== -->
          <div class="extract-model">
            <div class="em-head">
              <span>🤖 选择识别模型</span>
              <span class="muted" style="font-size:11px">本地 Ollama 直连，或调用已配置的在线 OpenAI 兼容服务</span>
            </div>
            <el-radio-group v-model="extractDialog.modelKind" class="em-kind">
              <el-radio value="local">
                本地 Ollama
                <span class="muted">（{{ extractDialog.localModels.length || '加载中…' }} 个模型）</span>
              </el-radio>
              <el-radio value="online" :disabled="!extractDialog.providers.some(p => p.enabled)">
                在线服务
                <span class="muted">（{{ extractDialog.providers.filter(p => p.enabled).length || 0 }} 个已启用）</span>
              </el-radio>
            </el-radio-group>
            <div class="em-pick">
              <el-select
                v-if="extractDialog.modelKind === 'local'"
                v-model="extractDialog.selectedModel"
                placeholder="选择本地模型"
                size="small"
                style="width:100%"
              >
                <el-option v-for="m in extractDialog.localModels" :key="m" :value="m" :label="m" />
              </el-select>
              <template v-else>
                <el-select
                  v-model="extractDialog.selectedOnlineId"
                  placeholder="选择已配置的在线服务"
                  size="small"
                  style="width:100%"
                  @change="extractDialog.selectedModel = ''"
                >
                  <el-option
                    v-for="p in extractDialog.providers.filter(x => x.enabled)"
                    :key="p.id"
                    :value="p.id"
                    :label="`${p.name}（${p.model}）`"
                  />
                </el-select>
                <div v-if="currentOnlineProvider" class="em-hint muted">
                  base_url: <code>{{ currentOnlineProvider.baseUrl }}</code> · 模型: <code>{{ currentOnlineProvider.model }}</code>
                </div>
                <div v-else-if="!extractDialog.providers.length" class="em-hint muted">
                  尚未配置在线服务，请展开下方「管理在线服务」新增。
                </div>
              </template>
            </div>

            <!-- 在线服务管理（折叠面板） -->
            <details class="em-providers">
              <summary>🛠 管理在线服务（{{ extractDialog.providers.length }}）</summary>
              <div class="em-providers-body">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                  <span class="muted" style="font-size:12px">支持任何 OpenAI 兼容接口（通义千问 / 豆包 / OpenAI / DeepSeek / 自建网关…）</span>
                  <button class="btn sm" @click="openProviderEditor(null)">＋ 新增在线服务</button>
                </div>
                <div v-if="!extractDialog.providers.length" class="muted" style="padding:12px;text-align:center;background:var(--bg-2);border-radius:8px">
                  还没有配置任何在线服务，点击右上角「＋ 新增在线服务」开始。
                </div>
                <div v-else class="em-provider-list">
                  <div v-for="p in extractDialog.providers" :key="p.id" class="em-provider-row">
                    <div class="em-provider-info">
                      <div class="em-provider-name">
                        {{ p.name }}
                        <span class="muted" style="font-weight:400">（{{ p.model }}）</span>
                        <span v-if="!p.enabled" class="em-disabled-tag">已停用</span>
                      </div>
                      <div class="muted em-provider-url">{{ p.baseUrl }}</div>
                    </div>
                    <div class="em-provider-acts">
                      <el-switch v-model="p.enabled" @change="saveProviders" />
                      <button class="btn sm ghost" @click="testProvider(p)" :disabled="extractDialog.testing">
                        {{ extractDialog.testing ? '测试中…' : '测试' }}
                      </button>
                      <button class="btn sm ghost" @click="openProviderEditor(p)">编辑</button>
                      <button class="btn sm ghost" @click="deleteProvider(p.id)">删除</button>
                    </div>
                  </div>
                </div>
                <div v-if="extractDialog.testResult" class="extract-error" style="margin-top:10px">{{ extractDialog.testResult }}</div>
              </div>
            </details>
          </div>

          <!-- 实时日志 / 状态 -->
          <div v-if="extractDialog.logs.length" class="extract-log-panel">
            <div class="extract-log-head">
              <span>🪵 运行日志</span>
              <span class="link" @click="extractDialog.logs = []">清空</span>
            </div>
            <div class="extract-log-body">
              <div v-for="(lg, idx) in extractDialog.logs" :key="idx" class="extract-log-line" :class="lg.level">
                <span class="time">{{ lg.time }}</span>
                <span class="msg">{{ lg.msg }}</span>
              </div>
            </div>
          </div>

          <div class="modal-acts">
            <button class="btn sm ghost" @click="closeExtractDialog">取消</button>
            <button v-if="extractDialog.loading" class="btn sm" style="border-color:#f56c6c;color:#f56c6c" @click="stopExtract">⏹ 停止</button>
            <button
              class="btn sm"
              :disabled="extractDialog.loading || (extractDialog.storySource === 'paste' && !extractDialog.pastedStory.trim()) || !buildModelConfig()"
              @click="runExtract"
            >
              {{ extractDialog.loading ? '提取中…' : '开始提取' }}
            </button>
          </div>
        </div>

        <!-- 步骤 2：确认/编辑候选 -->
        <div v-else-if="extractDialog.step === 'review'">
          <p class="muted" style="margin-bottom:12px">已识别 {{ (extractDialog.candidates.roles||[]).length }} 个角色、{{ (extractDialog.candidates.scenes||[]).length }} 个场景、{{ (extractDialog.candidates.props||[]).length }} 个道具。勾选需要创建的资产，可编辑名称与描述。</p>
          <div v-if="extractDialog.meta && extractDialog.meta.error" class="extract-error">⚠️ 模型提示：{{ extractDialog.meta.error }}（结果可能不完整）</div>

          <!-- 实时日志（review 步骤仍可查看） -->
          <div v-if="extractDialog.logs.length" class="extract-log-panel">
            <div class="extract-log-head">
              <span>🪵 运行日志</span>
              <span class="link" @click="extractDialog.logs = []">清空</span>
            </div>
            <div class="extract-log-body">
              <div v-for="(lg, idx) in extractDialog.logs" :key="idx" class="extract-log-line" :class="lg.level">
                <span class="time">{{ lg.time }}</span>
                <span class="msg">{{ lg.msg }}</span>
              </div>
            </div>
          </div>

          <!-- 模型原始返回预览（排查用） -->
          <details v-if="extractDialog.meta && extractDialog.meta.raw_preview" class="extract-raw-preview">
            <summary>查看模型原始返回（排查）</summary>
            <pre>{{ extractDialog.meta.raw_preview }}</pre>
          </details>

          <div class="extract-groups">
            <!-- 角色 -->
            <div class="extract-group" v-if="extractDialog.candidates.roles.length">
              <div class="eg-head">
                <span>🎭 角色（{{ extractDialog.candidates.roles.length }}）</span>
                <span class="link" @click="toggleGroup('roles', extractDialog.candidates.roles, true)">全选</span>
                <span class="link" @click="toggleGroup('roles', extractDialog.candidates.roles, false)">取消</span>
              </div>
              <div v-for="it in extractDialog.candidates.roles" :key="'r_'+it.name" class="extract-row" :class="{ dup: isExistingAssetName(getCandidate('roles', it.name, it).name || it.name) }">
                <input type="checkbox" :checked="isCandidateSelected('roles', it.name)" @change="toggleCandidate('roles', it.name)" />
                <input class="input" :value="getCandidate('roles', it.name, it).name || it.name" @input="updateCandidate('roles', it.name, 'name', $event.target.value)" placeholder="角色名" />
                <select class="input" :value="getCandidate('roles', it.name, it).role_type || it.role_type || 'supporting'" @change="updateCandidate('roles', it.name, 'role_type', $event.target.value)">
                  <option v-for="o in ROLE_TYPE_OPTS" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
                <input class="input" :value="getCandidate('roles', it.name, it).description || it.description" @input="updateCandidate('roles', it.name, 'description', $event.target.value)" placeholder="描述/身份" />
                <span v-if="isExistingAssetName(getCandidate('roles', it.name, it).name || it.name)" class="dup-tag">已存在</span>
              </div>
            </div>

            <!-- 场景 -->
            <div class="extract-group" v-if="extractDialog.candidates.scenes.length">
              <div class="eg-head">
                <span>🏞️ 场景（{{ extractDialog.candidates.scenes.length }}）</span>
                <span class="link" @click="toggleGroup('scenes', extractDialog.candidates.scenes, true)">全选</span>
                <span class="link" @click="toggleGroup('scenes', extractDialog.candidates.scenes, false)">取消</span>
              </div>
              <div v-for="it in extractDialog.candidates.scenes" :key="'s_'+it.name" class="extract-row" :class="{ dup: isExistingAssetName(getCandidate('scenes', it.name, it).name || it.name) }">
                <input type="checkbox" :checked="isCandidateSelected('scenes', it.name)" @change="toggleCandidate('scenes', it.name)" />
                <input class="input" :value="getCandidate('scenes', it.name, it).name || it.name" @input="updateCandidate('scenes', it.name, 'name', $event.target.value)" placeholder="场景名" />
                <input class="input" :value="getCandidate('scenes', it.name, it).description || it.description" @input="updateCandidate('scenes', it.name, 'description', $event.target.value)" placeholder="环境/氛围" />
                <span v-if="isExistingAssetName(getCandidate('scenes', it.name, it).name || it.name)" class="dup-tag">已存在</span>
              </div>
            </div>

            <!-- 道具 -->
            <div class="extract-group" v-if="extractDialog.candidates.props.length">
              <div class="eg-head">
                <span>🧰 道具（{{ extractDialog.candidates.props.length }}）</span>
                <span class="link" @click="toggleGroup('props', extractDialog.candidates.props, true)">全选</span>
                <span class="link" @click="toggleGroup('props', extractDialog.candidates.props, false)">取消</span>
              </div>
              <div v-for="it in extractDialog.candidates.props" :key="'p_'+it.name" class="extract-row" :class="{ dup: isExistingAssetName(getCandidate('props', it.name, it).name || it.name) }">
                <input type="checkbox" :checked="isCandidateSelected('props', it.name)" @change="toggleCandidate('props', it.name)" />
                <input class="input" :value="getCandidate('props', it.name, it).name || it.name" @input="updateCandidate('props', it.name, 'name', $event.target.value)" placeholder="道具名" />
                <input class="input" :value="getCandidate('props', it.name, it).description || it.description" @input="updateCandidate('props', it.name, 'description', $event.target.value)" placeholder="形态/作用" />
                <span v-if="isExistingAssetName(getCandidate('props', it.name, it).name || it.name)" class="dup-tag">已存在</span>
              </div>
            </div>
          </div>

          <div class="modal-acts">
            <button class="btn sm ghost" @click="extractDialog.step = 'source'">上一步</button>
            <button class="btn sm" :disabled="extractDialog.loading" @click="submitBatchCreate">
              {{ extractDialog.loading ? '创建中…' : '确认创建本集资产' }}
            </button>
          </div>
        </div>

        <!-- 步骤 3：完成 -->
        <div v-else-if="extractDialog.step === 'done'">
          <div class="extract-done">
            <div class="done-icon">✅</div>
            <div class="done-title">成功创建 {{ extractDialog.createdCount }} 个本集资产</div>
            <div v-if="extractDialog.skippedNames.length" class="muted">已跳过重复：{{ extractDialog.skippedNames.join('、') }}</div>
          </div>
          <div class="modal-acts">
            <button class="btn sm" @click="closeExtractDialog">完成</button>
          </div>
        </div>
        </div>
      </div>
    </div>

    <!-- 在线服务编辑弹窗（v1.1.0 新增） -->
    <div v-if="extractDialog.providerEditorOpen" class="modal-mask" @click="extractDialog.providerEditorOpen = false">
      <div class="modal-box" @click.stop>
        <h3>{{ extractDialog.editingProvider ? '编辑在线服务' : '新增在线服务' }}</h3>
        <p class="muted" style="font-size:12px;margin:-8px 0 8px">支持任何 OpenAI 兼容接口（通义千问 / 豆包 / OpenAI / DeepSeek / 自建网关…）</p>
        <label class="lbl">服务名（仅本地标识）</label>
        <input class="input" v-model="extractDialog.providerForm.name" placeholder="如：通义千问 / 豆包 / OpenAI" />
        <label class="lbl">Base URL</label>
        <input class="input" v-model="extractDialog.providerForm.baseUrl" placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
        <label class="lbl">API Key</label>
        <input class="input" type="password" v-model="extractDialog.providerForm.apiKey" placeholder="sk-..." />
        <label class="lbl">模型名</label>
        <input class="input" v-model="extractDialog.providerForm.model" placeholder="如 qwen-plus / doubao-pro / gpt-4o-mini" />
        <label style="display:flex;align-items:center;gap:6px;margin-top:8px;font-size:12px;color:var(--sub);cursor:pointer">
          <input type="checkbox" v-model="extractDialog.providerForm.enabled" /> 启用（在「选择模型」中可见）
        </label>
        <div v-if="extractDialog.testResult" class="extract-error" style="margin-top:10px">{{ extractDialog.testResult }}</div>
        <div class="modal-acts">
          <button class="btn sm ghost" @click="extractDialog.providerEditorOpen = false">取消</button>
          <button class="btn sm ghost" @click="testProvider(extractDialog.providerForm)" :disabled="extractDialog.testing || !extractDialog.providerForm.baseUrl || !extractDialog.providerForm.model">
            {{ extractDialog.testing ? '测试中…' : '测试连接' }}
          </button>
          <button class="btn sm" @click="saveProvider">保存</button>
        </div>
      </div>
    </div>

    <!-- 阶段表单 -->
    <div class="modal-mask" v-if="stageForm.open" @click="stageForm.open=false">
      <div class="modal-box" @click.stop>
        <h3>添加阶段</h3>
        <label class="lbl">阶段</label>
        <select v-model="stageForm.stage_name" class="input">
          <option v-for="o in STAGE_OPTS" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <label class="lbl">年龄</label>
        <input class="input" type="number" v-model="stageForm.age" min="0" max="120" />
        <label class="lbl">参考照</label>
        <input type="file" accept="image/*" @change="onStageFile" />
        <div class="modal-acts">
          <button class="btn sm ghost" @click="stageForm.open=false">取消</button>
          <button class="btn sm" :disabled="busy" @click="submitStage">保存</button>
        </div>
      </div>
    </div>

    <!-- 姿势表单 -->
    <div class="modal-mask" v-if="poseForm.open" @click="poseForm.open=false">
      <div class="modal-box" @click.stop>
        <h3>添加姿势</h3>
        <label class="lbl">姿势</label>
        <select v-model="poseForm.pose_name" class="input">
          <option v-for="o in POSE_OPTS" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <label class="lbl">参考照</label>
        <input type="file" accept="image/*" @change="onPoseFile" />
        <div class="modal-acts">
          <button class="btn sm ghost" @click="poseForm.open=false">取消</button>
          <button class="btn sm" :disabled="busy" @click="submitPose">保存</button>
        </div>
      </div>
    </div>

    <div class="modal-mask" v-if="preview" @click="preview = ''"><div class="modal"><img :src="preview" @click.stop /></div></div>

    <!-- 角色编辑弹窗 -->
    <RoleEditorModal v-model="editorVisible" :role="editingRole" :project-id="pid" @saved="onRoleSaved" @preview="preview = $event" />
  </div>
</template>

<style scoped>
.page { padding: 0 24px 24px; }
.page-head { display: flex; align-items: center; justify-content: space-between; margin: 16px 0 12px; gap: 12px; flex-wrap: wrap; }
.page-head h1 { margin: 0; font-size: 20px; }
.cold { font-size: 12px; color: var(--sub); font-weight: 400; }
.muted { color: var(--sub); font-size: 13px; }
.head-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.tabs { display: flex; gap: 6px; background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 4px; }
.tab { padding: 6px 12px; border-radius: 8px; font-size: 13px; color: var(--sub); cursor: pointer; transition: .15s; white-space: nowrap; }
.tab:hover { color: var(--txt); }
.tab.on { background: var(--card-2); color: var(--txt); }

.toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
.spacer { flex: 1; }
.search-box { position: relative; width: 260px; }
.search-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); font-size: 12px; }
.search-input { width: 100%; background: var(--card); color: var(--txt); border: 1px solid var(--line); border-radius: 10px; padding: 7px 12px 7px 30px; font-size: 13px; outline: none; }
.search-input:focus { border-color: var(--pur); }
.tag-bar { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.tag-bar .tag { font-size: 12px; padding: 4px 10px; border-radius: 20px; background: var(--card); border: 1px solid var(--line); color: var(--sub); cursor: pointer; transition: .15s; }
.tag-bar .tag.on { background: var(--pur); color: #fff; border-color: var(--pur); }
.upload-inline { background: var(--grad); color: #fff; border: 0; border-radius: 10px; padding: 8px 14px; font-size: 13px; font-weight: 700; cursor: pointer; }
.upload-inline:hover { opacity: .9; }
.import-btn { background: var(--card); border: 1px solid var(--line); color: var(--txt); }
.import-btn:hover { border-color: var(--pur); color: var(--pur); }

.drop-zone { border: 2px dashed var(--line); border-radius: 18px; padding: 48px 24px; text-align: center; cursor: pointer; transition: .2s; background: var(--card); }
.drop-zone.drag { border-color: var(--pur); background: rgba(138,92,255,.08); }
.drop-inner { max-width: 520px; margin: 0 auto; }
.drop-icon { font-size: 48px; margin-bottom: 12px; }
.drop-title { font-size: 18px; font-weight: 700; margin-bottom: 8px; }
.drop-desc { color: var(--sub); font-size: 13px; margin-bottom: 18px; line-height: 1.6; }
.drop-btn { display: inline-block; background: var(--grad); color: #fff; border-radius: 10px; padding: 10px 22px; font-size: 14px; font-weight: 700; cursor: pointer; }
.drop-hint { color: var(--sub); font-size: 12px; margin-top: 14px; }
.drop-hint-bar { border: 1px dashed var(--line); border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between; color: var(--sub); font-size: 13px; transition: .2s; }
.drop-hint-bar.drag { border-color: var(--pur); background: rgba(138,92,255,.06); }
 .link-upload { color: var(--blue); cursor: pointer; }

.skeleton-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; }
.skeleton-card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; padding: 10px; }
.skeleton-thumb { aspect-ratio: 1; background: var(--bg-2); border-radius: 10px; animation: pulse 1.4s infinite; }
.skeleton-line { height: 14px; background: var(--bg-2); border-radius: 6px; margin-top: 10px; animation: pulse 1.4s infinite; }
.skeleton-line.short { width: 60%; }
@keyframes pulse { 0% { opacity: .6 } 50% { opacity: 1 } 100% { opacity: .6 } }

.asset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; }
.asset-card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; transition: .15s; }
.asset-card:hover { border-color: var(--pur); transform: translateY(-2px); }
.thumb-wrap { position: relative; aspect-ratio: 1 / 1; background: var(--bg-2); }
.thumb-wrap img { width: 100%; height: 100%; object-fit: cover; cursor: zoom-in; }
.thumb-overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; gap: 8px; background: rgba(8,9,14,.65); opacity: 0; transition: .15s; z-index: 2; }
.asset-card:hover .thumb-overlay, .thumb-wrap.role-hovered .thumb-overlay { opacity: 1; }
.thumb-overlay.role-overlay { background: transparent; align-items: flex-start; justify-content: flex-end; padding: 10px; gap: 8px; }
.role-btn { width: 34px; height: 34px; border-radius: 10px; background: rgba(0,0,0,.55); color: #fff; border: 0; display: flex; align-items: center; justify-content: center; cursor: pointer; backdrop-filter: blur(4px); transition: .15s; }
.role-btn:hover { background: rgba(138,92,255,.9); transform: scale(1.05); }
.thumb-wrap.draggable { cursor: grab; }
.thumb-wrap.draggable:active { cursor: grabbing; }
.badge { position: absolute; font-size: 10px; padding: 2px 7px; border-radius: 6px; font-weight: 700; }
.badge.feature { left: 6px; bottom: 6px; background: rgba(138,92,255,.9); color: #fff; }
.badge.global { right: 6px; top: 6px; background: rgba(0,0,0,.55); color: var(--txt); }
.badge.role { left: 6px; top: 6px; }
.badge.role.protagonist { background: rgba(255,77,141,.9); color: #fff; }
.badge.role.supporting { background: rgba(77,159,255,.9); color: #fff; }
.badge.role.antagonist { background: rgba(255,176,32,.9); color: #000; }
.role-toggle { position: absolute; bottom: 6px; right: 6px; background: rgba(0,0,0,.6); color: #fff; border: 0; border-radius: 6px; font-size: 11px; padding: 3px 8px; cursor: pointer; }
.role-toggle:hover { background: rgba(138,92,255,.9); }
.info { padding: 10px 12px; }
.info .name input { width: 100%; background: transparent; color: var(--txt); border: 1px solid transparent; border-radius: 6px; padding: 4px 6px; font-size: 13px; }
.info .name input:hover, .info .name input:focus { background: var(--bg-2); border-color: var(--line); outline: none; }
.info textarea { width: 100%; background: transparent; color: var(--txt); border: 1px solid transparent; border-radius: 6px; padding: 4px 6px; font-size: 12px; margin-top: 6px; resize: vertical; min-height: 44px; }
.info textarea:hover, .info textarea:focus { background: var(--bg-2); border-color: var(--line); outline: none; }
.tag-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.asset-tag { font-size: 10px; padding: 2px 8px; border-radius: 10px; background: var(--bg-2); color: var(--sub); border: 1px solid var(--line); max-width: 80px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.asset-tag i { cursor: pointer; margin-left: 4px; }

.btn { border: 0; border-radius: 8px; padding: 6px 12px; font-size: 12px; cursor: pointer; }
.btn.sm { font-size: 12px; padding: 5px 10px; }
.btn:not(.ghost) { background: var(--grad); color: #fff; font-weight: 700; }
.btn.ghost { background: transparent; border: 1px solid var(--line); color: var(--sub); }
.btn.ghost:hover { border-color: var(--pink); color: var(--pink); }
.empty { text-align: center; color: var(--sub); padding: 50px 0; }
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.7); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { max-width: 90vw; max-height: 90vh; }
.modal img { max-width: 90vw; max-height: 90vh; border-radius: 12px; }

.role-panel { margin-top: 20px; background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
.rp-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.rp-title { font-size: 15px; font-weight: 700; }
.stage-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
.stage-card { background: var(--bg-2); border: 1px solid var(--line); border-radius: 12px; padding: 12px; }
.stage-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.stage-name { font-weight: 700; font-size: 13px; }
.stage-img { aspect-ratio: 16/9; background: var(--bg); border-radius: 8px; overflow: hidden; }
.stage-img img { width: 100%; height: 100%; object-fit: contain; cursor: zoom-in; }
.pose-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; align-items: center; }
.pose-chip { position: relative; width: 64px; }
.pose-chip img { width: 64px; height: 64px; object-fit: cover; border-radius: 8px; cursor: zoom-in; border: 1px solid var(--line); }
.pose-label { position: absolute; bottom: 0; left: 0; right: 0; font-size: 10px; text-align: center; background: rgba(0,0,0,.6); color: #fff; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; }
.pose-del { position: absolute; top: -6px; right: -6px; width: 18px; height: 18px; border-radius: 50%; border: 0; background: rgba(255,80,80,.9); color: #fff; cursor: pointer; font-size: 11px; line-height: 1; }
.pose-add { width: 64px; height: 64px; border-radius: 8px; border: 1px dashed var(--line); background: transparent; color: var(--sub); cursor: pointer; font-size: 12px; }
.pose-add:hover { border-color: var(--pur); color: var(--pur); }

.modal-box { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 20px; width: 320px; max-height: 85vh; overflow-y: auto; }
.modal-box.wide { width: 680px; }
.modal-box h3 { margin: 0 0 16px; font-size: 16px; }
.lbl { display: block; font-size: 12px; color: var(--sub); margin: 10px 0 4px; }
.input { width: 100%; background: var(--bg-2); color: var(--txt); border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; font-size: 13px; margin-bottom: 4px; outline: none; }
.input:focus { border-color: var(--pur); }
.modal-acts { display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }

.batch-bar { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; flex-wrap: wrap; }
.batch-bar label { font-size: 12px; color: var(--sub); }
.batch-bar .input { width: auto; min-width: 120px; }

.upload-file-list { max-height: 55vh; overflow-y: auto; }
.upload-file-row { display: flex; gap: 12px; padding: 12px; background: var(--bg-2); border: 1px solid var(--line); border-radius: 10px; margin-bottom: 10px; }
.preview-thumb { width: 70px; height: 70px; flex: 0 0 70px; background: var(--bg); border-radius: 8px; overflow: hidden; }
.preview-thumb img { width: 100%; height: 100%; object-fit: cover; }
.file-fields { flex: 1; display: flex; flex-direction: column; gap: 6px; }
.tag-editor { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.tag-input { width: 100px; }

.import-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.import-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; max-height: 420px; overflow-y: auto; }
.import-card { background: var(--bg-2); border: 1px solid var(--line); border-radius: 10px; padding: 10px; text-align: center; }
.import-thumb { aspect-ratio: 1; background: var(--bg); border-radius: 8px; overflow: hidden; margin-bottom: 8px; }
.import-thumb img { width: 100%; height: 100%; object-fit: cover; }
.import-name { font-size: 12px; font-weight: 700; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 从剧本提取 */
.extract-box { width: 760px; max-height: 82vh; display: flex; overflow: hidden; padding: 0; gap: 0; }
.extract-main { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; padding: 20px; overflow-y: auto; }
.extract-source { display: flex; flex-direction: column; gap: 10px; }
.extract-option { display: flex; align-items: flex-start; gap: 10px; padding: 12px; border-radius: 10px; border: 1px solid var(--line); cursor: pointer; transition: .15s; }
.extract-option.on { border-color: var(--pur); background: rgba(138,92,255,.08); }
.extract-option input { margin-top: 3px; }
.opt-title { font-weight: 700; font-size: 14px; }
.opt-desc { font-size: 12px; color: var(--sub); margin-top: 2px; }
.extract-story { width: 100%; min-height: 160px; background: var(--bg-2); color: var(--txt); border: 1px solid var(--line); border-radius: 10px; padding: 12px; font-size: 13px; resize: vertical; }
.extract-story.over { border-color: var(--blue); background: rgba(59,130,246,.08); }
.extract-drop { position: relative; }
.extract-drop-hint { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none; color: var(--sub); font-size: 13px; text-align: center; gap: 4px; padding: 12px; }
.extract-drop-hint .sub { font-size: 12px; opacity: .8; }
.extract-drop-hint.over { color: var(--blue); font-weight: 700; }
.extract-formats { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; font-size: 11px; color: var(--sub); opacity: .85; }
.extract-formats .link { color: var(--blue); cursor: pointer; }
.extract-file-tag { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; padding: 6px 10px; background: var(--bg-2); border: 1px solid var(--line); border-radius: 8px; font-size: 12px; color: var(--sub); }
.extract-file-tag .link { color: var(--red); cursor: pointer; }
.extract-error { background: rgba(255,176,32,.12); color: #ffb020; border: 1px solid rgba(255,176,32,.3); border-radius: 8px; padding: 10px 12px; font-size: 13px; margin-bottom: 12px; }
.extract-log-panel { background: rgba(0,0,0,.25); border: 1px solid var(--line); border-radius: 10px; margin-top: 12px; overflow: hidden; }
.extract-log-head { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: var(--bg-2); font-size: 12px; font-weight: 700; }
.extract-log-head .link { font-size: 11px; color: var(--sub); cursor: pointer; }
.extract-log-body { max-height: 160px; overflow-y: auto; padding: 8px 12px; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.extract-log-line { display: flex; gap: 10px; padding: 3px 0; color: var(--txt); }
.extract-log-line .time { color: var(--sub); opacity: .8; white-space: nowrap; }
.extract-log-line.warn .msg { color: #ffb020; }
.extract-log-line.error .msg { color: #ff6b6b; }
.extract-raw-preview { margin: 12px 0; background: var(--bg-2); border: 1px solid var(--line); border-radius: 10px; }
.extract-raw-preview summary { padding: 8px 12px; font-size: 12px; color: var(--sub); cursor: pointer; user-select: none; }
.extract-raw-preview pre { margin: 0; padding: 10px 12px; max-height: 220px; overflow: auto; white-space: pre-wrap; word-break: break-all; font-size: 11px; color: var(--sub); background: rgba(0,0,0,.2); }
.extract-groups { max-height: 50vh; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; }
.extract-group { background: var(--bg-2); border: 1px solid var(--line); border-radius: 12px; padding: 12px; }
.eg-head { display: flex; align-items: center; gap: 12px; font-weight: 700; font-size: 14px; margin-bottom: 10px; }
.eg-head .link { font-size: 12px; color: var(--blue); cursor: pointer; }
.extract-row { display: flex; align-items: center; gap: 8px; padding: 8px; border-radius: 8px; transition: .15s; }
.extract-row:hover { background: var(--card); }
.extract-row.dup { opacity: .75; }
.extract-row input[type="checkbox"] { flex: 0 0 16px; accent-color: var(--pur); }
.extract-row .input { flex: 1; min-width: 0; }
.dup-tag { font-size: 10px; color: #ffb020; background: rgba(255,176,32,.12); padding: 2px 6px; border-radius: 4px; white-space: nowrap; }
.extract-done { text-align: center; padding: 30px 0; }
.done-icon { font-size: 48px; margin-bottom: 10px; }
.done-title { font-size: 16px; font-weight: 700; }

/* 选择识别模型（v1.1.0 新增） */
.extract-model { margin-top: 14px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 12px; background: var(--bg-2); }
.em-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 10px; font-weight: 700; font-size: 14px; }
.em-kind { display: flex; gap: 18px; margin-bottom: 10px; flex-wrap: wrap; }
.em-pick { margin-bottom: 8px; }
.em-hint { font-size: 11px; margin-top: 6px; line-height: 1.6; word-break: break-all; }
.em-hint code { background: var(--card); padding: 1px 6px; border-radius: 4px; font-size: 11px; color: var(--txt); }
.em-providers { margin-top: 10px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; }
.em-providers > summary { cursor: pointer; font-size: 12px; font-weight: 600; color: var(--txt); user-select: none; padding: 4px 0; }
.em-providers-body { margin-top: 10px; }
.em-provider-list { display: flex; flex-direction: column; gap: 8px; }
.em-provider-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; background: var(--bg-2); border: 1px solid var(--line); border-radius: 8px; transition: .15s; }
.em-provider-row:hover { border-color: var(--pur); }
.em-provider-info { flex: 1; min-width: 0; }
.em-provider-name { font-size: 13px; font-weight: 700; }
.em-disabled-tag { display: inline-block; margin-left: 6px; font-size: 10px; color: var(--sub); background: var(--bg); padding: 1px 6px; border-radius: 4px; font-weight: 400; }
.em-provider-url { font-size: 11px; word-break: break-all; margin-top: 2px; }
.em-provider-acts { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
</style>