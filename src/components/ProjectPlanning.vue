<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { useProject } from '../composables/useProject'
import { toast } from '../store'
import { splitStory, createSegment, putShots, getLocalModels } from '../api'
import { parseDocx } from '../parseDocx'
import ProjectFlowNav from './ProjectFlowNav.vue'

const route = useRoute()
const router = useRouter()
const pid = route.params.id
const { project: p, load } = useProject(pid, 'short')

const story = ref('')
const loading = ref(false)
const segments = ref([])
const selected = ref([])
const keyword = ref('')
const viewMode = ref('card') // 'card' | 'list'
const expanded = ref([])
const trimMap = ref({})
const lastSelectedIndex = ref(-1)
const splitLogs = ref([])
const splitElapsed = ref(0)
const logBody = ref(null)
const logCollapsed = ref(false)
let elapsedTimer = null
let pendingTimers = []

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

// ---- 策划页草稿自动保存（localStorage 兜底）----
// 策划页内容仅在 doImport 时落库（createSegment + putShots），导入前 story 文本仅存内存。
// 为避免误关页面丢失原始剧本：防抖写 localStorage + 挂载时提示恢复（双保险）。
// CLOSE-02：草稿写入失败显式提示，不静默。
const DRAFT_KEY = 'manju_planning_draft_' + pid
const draftAvailable = ref(false)
const pendingDraft = ref(null)
const draftSavedAt = ref(0)
let draftTimer = null
function persistDraft() {
  if (USE_MOCK) return
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ story: story.value, ts: Date.now() }))
    draftSavedAt.value = Date.now()
  } catch (e) {
    toast('草稿保存失败：' + (e.message || '未知错误'))
  }
}
function scheduleDraft() {
  if (draftTimer) clearTimeout(draftTimer)
  draftTimer = setTimeout(persistDraft, 1200)
}
function clearDraft() {
  try { localStorage.removeItem(DRAFT_KEY) } catch { /* ignore */ }
}
function checkDraft() {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    if (!raw) return
    const d = JSON.parse(raw)
    if (d && typeof d.story === 'string' && d.story.trim() && d.story !== story.value) {
      pendingDraft.value = d
      draftAvailable.value = true
    }
  } catch { /* ignore */ }
}
function restoreDraft() {
  if (pendingDraft.value) {
    story.value = pendingDraft.value.story
    draftSavedAt.value = Date.now()
    draftAvailable.value = false
    pendingDraft.value = null
    toast('已恢复上次未导入的草稿')
  }
}
function discardDraft() {
  clearDraft()
  draftAvailable.value = false
  pendingDraft.value = null
  toast('已丢弃草稿')
}
watch(story, () => { if (!USE_MOCK) scheduleDraft() }, { flush: 'post' })

// ---- AI 分场模型选择（本地 Ollama / 在线 OpenAI 兼容）----
const LS_MODEL_KEY = 'manju_split_model_config'
function loadModelConfig() {
  try {
    const raw = localStorage.getItem(LS_MODEL_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* ignore */ }
  return { provider: 'local', model: '', apiBase: '', apiKey: '' }
}
const modelConfig = ref(loadModelConfig())
const localModels = ref([])
const loadingModels = ref(false)
const onlineDialogVisible = ref(false)

const currentModelLabel = computed(() => {
  if (modelConfig.value.provider === 'local') {
    const m = modelConfig.value.model || '本地模型'
    return '🖥️ ' + m
  }
  return '🌐 ' + (modelConfig.value.model || '在线模型')
})

const modelSelectorOptions = computed(() => {
  const opts = []
  if (localModels.value.length) {
    opts.push({ label: '本地模型', options: localModels.value.map((m) => ({ value: 'local:' + m, label: m })) })
  }
  opts.push({ label: '在线模型', options: [{ value: '__online__', label: '⚙️ 配置在线模型…' }] })
  return opts
})

const modelSelectorValue = computed({
  get: () => {
    const cfg = modelConfig.value
    if (cfg.provider === 'local') return 'local:' + (cfg.model || '')
    return 'online:' + cfg.model
  },
  set: (val) => onModelChange(val),
})

async function refreshLocalModels() {
  loadingModels.value = true
  try {
    localModels.value = await getLocalModels()
    if (modelConfig.value.provider === 'local' && !modelConfig.value.model && localModels.value.length) {
      modelConfig.value.model = localModels.value[0]
      saveModelConfig()
    }
  } catch (e) {
    localModels.value = []
  } finally {
    loadingModels.value = false
  }
}
function saveModelConfig() {
  try { localStorage.setItem(LS_MODEL_KEY, JSON.stringify(modelConfig.value)) } catch { /* ignore */ }
}
function onModelChange(val) {
  if (val === '__online__') {
    onlineDialogVisible.value = true
    return
  }
  const [provider, ...rest] = String(val || '').split(':')
  const model = rest.join(':')
  if (provider === 'local' && model) {
    modelConfig.value = { provider: 'local', model, apiBase: '', apiKey: '' }
    saveModelConfig()
  }
}
function beforeOnlineClose(done) {
  const c = modelConfig.value
  const dirty = c.apiBase || c.apiKey || c.model
  if (!dirty) { done(); return }
  ElMessageBox.confirm('在线模型配置尚未保存，确定关闭？', '提示', {
    confirmButtonText: '放弃并关闭',
    cancelButtonText: '继续编辑',
    type: 'warning',
  }).then(() => done()).catch(() => {})
}
function applyOnlineConfig() {
  const cfg = modelConfig.value
  if (!cfg.apiBase || !cfg.model) {
    toast('请填写在线模型的 API 地址和模型名')
    return
  }
  cfg.provider = 'openai-compatible'
  saveModelConfig()
  onlineDialogVisible.value = false
}
function buildSplitPayload() {
  const cfg = modelConfig.value
  const payload = { story: story.value.trim() }
  if (cfg.provider !== 'local' || cfg.model) {
    payload.model_config = {
      provider: cfg.provider,
      model: cfg.model,
      apiBase: cfg.apiBase,
      apiKey: cfg.apiKey,
    }
  }
  return payload
}

onMounted(() => {
  load()
  refreshLocalModels()
  checkDraft()
})

const canSplit = computed(() => (story.value || '').trim().length >= 20)
const selectedFiltered = computed(() => filteredSegments.value.filter((s) => selected.value.includes(s._id)))
const canImport = computed(() => selectedFiltered.value.length > 0)

const filteredSegments = computed(() => {
  if (!keyword.value.trim()) return segments.value
  const k = keyword.value.trim().toLowerCase()
  return segments.value.filter((s) => {
    const hay = [
      s.title,
      s.summary,
      s.scene_description,
      ...(s.characters || []),
    ].join(' ').toLowerCase()
    const shotsText = (s.shots || []).map((sh) => [sh.caption, sh.description].join(' ')).join(' ').toLowerCase()
    return hay.includes(k) || shotsText.includes(k)
  })
})

const logStep = async (msg) => {
  splitLogs.value.push({ time: new Date().toLocaleTimeString(), msg })
  await nextTick()
  if (logBody.value) logBody.value.scrollTop = logBody.value.scrollHeight
}
const clearProgress = () => {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
  pendingTimers.forEach(clearTimeout)
  pendingTimers = []
}
const startProgress = () => {
  splitLogs.value = []
  splitElapsed.value = 0
  logCollapsed.value = false
  pendingTimers.forEach(clearTimeout)
  pendingTimers = []
  logStep('开始 AI 自动分场…')
  elapsedTimer = setInterval(() => splitElapsed.value++, 1000)
  pendingTimers.push(setTimeout(() => logStep('正在校验本地 Ollama 服务…'), 200))
  pendingTimers.push(setTimeout(() => logStep('正在将故事文本发送给本地 Qwen 模型…'), 600))
  pendingTimers.push(setTimeout(() => logStep('模型正在推理分场结构，请稍候…'), 1200))
}
const toggleLog = () => { logCollapsed.value = !logCollapsed.value }
const clearLogs = () => {
  splitLogs.value = []
  splitElapsed.value = 0
}

const doSplit = async () => {
  if (!canSplit.value) { toast('故事文本太短，请至少输入 20 字'); return }
  loading.value = true
  startProgress()
  const t0 = Date.now()
  try {
    const res = await splitStory(pid, buildSplitPayload(), { signal: AbortSignal.timeout(120000) })
    const duration = Date.now() - t0
    const source = res.source || 'regex'
    const meta = res.meta || {}
    const model = meta.model || 'qwen3.5:9b'
    if (source === 'llm') {
      const isOnline = (meta.provider || modelConfig.value.provider) !== 'local'
      logStep(`✅ ${isOnline ? '在线' : '本地'}模型完成分场，耗时 ${duration}ms，共 ${res.count || 0} 个场次`)
      logStep(`🤖 推理模型：${model}（模型耗时 ${meta.duration_ms ?? duration}ms）`)
      if (isOnline) logStep(`🌐 API：${modelConfig.value.apiBase}`)
      if (meta.raw_preview) {
        const prev = String(meta.raw_preview)
        logStep('📝 模型原始输出预览：' + (prev.length > 240 ? prev.slice(0, 240) + '…' : prev))
      }
    } else {
      logStep(`⚠️ 当前未调用本地模型，已回退正则分场。共 ${res.count || 0} 个场次。`)
      logStep('原因：' + (meta.error || 'Ollama 服务未运行，或目标模型 qwen3.5:9b 未加载。'))
      logStep('解决：启动 Ollama 并执行 ollama pull qwen3.5:9b && ollama run qwen3.5:9b')
      logStep('提示：后端需重启一次以识别模型名改动。')
    }
    segments.value = (res.segments || []).map((s, i) => ({
      ...s,
      _id: 'tmp_' + i,
      origIndex: i + 1,
      duration: s.duration || 10,
      shots: (s.shots || []).map((sh, j) => ({ ...sh, no: j + 1, duration: sh.duration || 4 })),
    }))
    selected.value = segments.value.map((s) => s._id)
    keyword.value = ''
    expanded.value = []
    trimMap.value = {}
    toast(`已拆分为 ${segments.value.length} 个场次`)
  } catch (e) {
    logStep('❌ 分场失败：' + (e.message || '未知错误'))
    toast('分场失败：' + (e.message || '未知错误'))
  } finally {
    clearProgress()
    loading.value = false
  }
}

const toggleSelect = (id, event, index) => {
  if (event?.shiftKey && lastSelectedIndex.value >= 0) {
    const start = Math.min(lastSelectedIndex.value, index)
    const end = Math.max(lastSelectedIndex.value, index)
    const rangeIds = filteredSegments.value.slice(start, end + 1).map((s) => s._id)
    selected.value = [...new Set([...selected.value, ...rangeIds])]
  } else {
    const i = selected.value.indexOf(id)
    if (i >= 0) selected.value.splice(i, 1)
    else selected.value.push(id)
    lastSelectedIndex.value = index
  }
}

const selectAll = () => {
  selected.value = [...new Set([...selected.value, ...filteredSegments.value.map((s) => s._id)])]
}
const selectNone = () => {
  selected.value = selected.value.filter((id) => !filteredSegments.value.some((s) => s._id === id))
}
const invertSelect = () => {
  const ids = filteredSegments.value.map((s) => s._id)
  const others = selected.value.filter((id) => !ids.includes(id))
  const picked = ids.filter((id) => !selected.value.includes(id))
  selected.value = [...others, ...picked]
}
const clearKeyword = () => { keyword.value = '' }

const isExpanded = (id) => expanded.value.includes(id)
const toggleExpand = (id) => {
  const i = expanded.value.indexOf(id)
  if (i >= 0) expanded.value.splice(i, 1)
  else expanded.value.push(id)
}
const maxShots = (s) => trimMap.value[s._id] ?? (s.shots?.length || 0)
const setTrim = (id, v) => { trimMap.value[id] = v }
const visibleShots = (s) => (s.shots || []).slice(0, maxShots(s))

// 防御性兜底：split-story 返回的分镜时长之和可能不等于片段时长（BR-003 会拦截导入），
// 导入前按片段时长重新均摊，保证 PUT /shots 不被 BR-003 拦截。
const redistributeDurations = (segDuration, shots) => {
  if (!shots || shots.length === 0) return shots
  const n = shots.length
  const base = Math.floor(segDuration / n)
  let rem = segDuration - base * n
  return shots.map((sh, i) => ({ ...sh, duration: base + (i < rem ? 1 : 0) }))
}

const doImport = async () => {
  if (!canImport.value) { toast('请至少选择一个场次'); return }
  const toImport = segments.value.filter((s) => selected.value.includes(s._id))
  try {
    for (const s of toImport) {
      const segDur = s.duration || 10
      const seg = await createSegment(pid, { title: s.title || '片段', duration: segDur, scene_description: s.scene_description || s.summary || '' })
      const shotsRaw = visibleShots(s)
      if (shotsRaw.length) {
        const shots = redistributeDurations(segDur, shotsRaw.map((sh) => ({
          caption: sh.caption || '分镜',
          description: sh.description || sh.summary || '',
          prompt: sh.prompt || '',
          duration: sh.duration || 4,
          shot_type: sh.shot_type || 'medium',
          camera_angle: sh.camera_angle || 'eye_level',
          camera_movement: sh.camera_movement || 'static',
          focal_length: sh.focal_length || '35mm',
          refs: sh.refs || [],
        })))
        await putShots(pid, seg.segment_id || seg.id, shots)
      }
    }
    clearDraft()
    toast(`已导入 ${toImport.length} 个片段，进入分镜编排`)
    router.push('/projects/' + pid + '/editor')
  } catch (e) {
    toast('导入失败：' + (e.message || '未知错误'))
  }
}

const goEditor = () => router.push('/projects/' + pid + '/editor')

// ---- 拖入 / 选择文件导入剧本 ----
const fileInput = ref(null)
const dragOver = ref(false)
const pickFile = () => fileInput.value?.click()
const onFileInput = (e) => {
  const f = e.target.files && e.target.files[0]
  if (f) readScriptFile(f)
  e.target.value = '' // 允许重复选择同一文件
}
const onDropFile = (e) => {
  dragOver.value = false
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]
  if (f) readScriptFile(f)
}
const readScriptFile = async (file) => {
  const name = (file.name || '').toLowerCase()
  try {
    // .docx 剧本：OOXML(ZIP) 结构，纯前端解压提取正文文本（不依赖后端/第三方库）
    if (name.endsWith('.docx')) {
      const buf = await file.arrayBuffer()
      const text = await parseDocx(buf)
      if (!text.trim()) throw new Error('未从 .docx 中解析出任何文本')
      story.value = text.trim()
      toast(`已导入剧本：${file.name}（${text.length} 字）`)
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      let text = String(reader.result || '')
      // .srt 剧本：剔除序号行与时间轴行，仅保留对话/动作文本
      if (name.endsWith('.srt')) {
        text = text.split(/\r?\n/)
          .filter((l) => !/^\d+$/.test(l.trim()) && !/-->/.test(l))
          .join('\n')
      }
      story.value = text.trim()
      toast(`已导入剧本：${file.name}（${text.length} 字）`)
    }
    reader.onerror = () => toast('文件读取失败，请重试')
    reader.readAsText(file, 'utf-8')
  } catch (e) {
    // CLOSE-02：解析/读取失败显式提示，不静默吞掉
    toast('剧本导入失败：' + (e.message || '未知错误'))
  }
}

// 快捷键：Ctrl+A 全选当前筛选、Esc 清空搜索
let keyHandler = null
onMounted(() => {
  keyHandler = (e) => {
    if (e.key === 'a' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      selectAll()
    }
    if (e.key === 'Escape' && keyword.value) {
      clearKeyword()
    }
  }
  window.addEventListener('keydown', keyHandler)
})
onBeforeUnmount(() => {
  if (keyHandler) window.removeEventListener('keydown', keyHandler)
  clearProgress()
})
</script>

<template>
  <div v-if="p" class="planning">
    <ProjectFlowNav :pid="pid" :step="2" />

    <div v-if="draftAvailable" class="draft-banner">
      <span class="draft-tip">💡 检测到上次未导入的剧本草稿，是否恢复？</span>
      <div class="draft-actions">
        <el-button size="small" type="primary" @click="restoreDraft">恢复草稿</el-button>
        <el-button size="small" text @click="discardDraft">丢弃</el-button>
      </div>
    </div>

    <div class="plan-head">
      <button class="back" @click="$router.push('/projects')">← 返回项目中心</button>
      <div class="ftitle">
        <div class="ti">{{ p.title }}</div>
        <div class="tsub">项目策划与分场拆解</div>
      </div>
      <div class="actions">
        <el-button size="small" @click="goEditor">跳过，直接编辑分镜</el-button>
        <el-button size="small" type="primary" :disabled="!canImport" @click="doImport">
          导入选中 {{ selectedFiltered.length }} 场
        </el-button>
      </div>
    </div>

    <div class="plan-body">
      <div
        class="story-col"
        :class="{ 'drag-over': dragOver }"
        @dragover.prevent="dragOver = true"
        @dragenter.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop.prevent="onDropFile"
      >
        <h3>📖 原始故事</h3>
        <el-input
          v-model="story"
          type="textarea"
          :rows="16"
          placeholder="在这里粘贴完整故事文本，或从本地拖入剧本文件，AI 将自动识别角色、场景并拆分为场次…"
        />
        <div class="story-tools">
          <el-button size="small" text @click="pickFile">📎 选择剧本文件</el-button>
          <span class="muted">支持拖入&nbsp;.txt&nbsp;/&nbsp;.md&nbsp;/&nbsp;.srt&nbsp;/&nbsp;.docx</span>
        </div>
        <div class="story-foot">
          <span class="muted">已输入 {{ story.length }} 字<span v-if="draftSavedAt"> · 草稿已自动保存</span></span>
          <div class="split-actions">
            <el-select
              v-model="modelSelectorValue"
              size="small"
              placeholder="选择模型"
              style="width: 170px"
              :loading="loadingModels"
            >
              <el-option-group v-for="g in modelSelectorOptions" :key="g.label" :label="g.label">
                <el-option
                  v-for="opt in g.options"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-option-group>
            </el-select>
            <el-button type="primary" :disabled="!canSplit || loading" :loading="loading" @click="doSplit">
              ✨ AI 自动分场
            </el-button>
          </div>
        </div>
        <input ref="fileInput" type="file" accept=".txt,.md,.srt,.text,.docx" hidden @change="onFileInput" />
        <div v-if="dragOver" class="drop-mask">松开鼠标，导入剧本文本</div>
      </div>

      <div class="result-col">
        <div class="result-head">
          <h3>🎬 拆解结果</h3>
          <div v-if="segments.length" class="result-stats muted">
            共 {{ segments.length }} 场 · 已选 {{ selected.length }} 场
            <span v-if="keyword.trim()">· 筛选后 {{ filteredSegments.length }} 场</span>
          </div>
        </div>

        <div v-if="!segments.length" class="empty-tip">点击「AI 自动分场」后，场次列表将显示在这里</div>
        <div v-else class="result-stack">
          <div class="toolbar">
            <el-input
              v-model="keyword"
              size="small"
              placeholder="搜索场次、摘要、角色、分镜…"
              clearable
              style="width: 220px;"
            />
            <div class="batch-btns">
              <el-button size="small" text @click="selectAll">全选</el-button>
              <el-button size="small" text @click="selectNone">全不选</el-button>
              <el-button size="small" text @click="invertSelect">反选</el-button>
            </div>
            <div class="view-toggle">
              <el-button size="small" :type="viewMode === 'card' ? 'primary' : ''" text @click="viewMode = 'card'">卡片</el-button>
              <el-button size="small" :type="viewMode === 'list' ? 'primary' : ''" text @click="viewMode = 'list'">列表</el-button>
            </div>
          </div>

          <div class="seg-list" :class="{ list: viewMode === 'list' }">
            <div
              v-for="(s, index) in filteredSegments"
              :key="s._id"
              class="seg-card"
              :class="{ on: selected.includes(s._id), expanded: isExpanded(s._id) }"
            >
              <div class="seg-check" @click.stop>
                <el-checkbox
                  :model-value="selected.includes(s._id)"
                  @change="toggleSelect(s._id, null, index)"
                />
              </div>

              <div class="seg-main" @click="toggleSelect(s._id, $event, index)">
                <div class="seg-title-line">
                  <span class="seg-title">场次 {{ s.origIndex }} · {{ s.title }}</span>
                  <span class="seg-dur">{{ s.duration }}秒 · {{ visibleShots(s).length }}/{{ s.shots?.length || 0 }} 分镜</span>
                </div>
                <div class="seg-summary">{{ s.summary || s.scene_description || '暂无概要' }}</div>
                <div v-if="s.characters?.length" class="seg-chars">
                  <el-tag v-for="c in s.characters" :key="c" size="small" effect="plain">{{ c }}</el-tag>
                </div>
              </div>

              <div class="seg-actions" @click.stop>
                <el-button
                  size="small"
                  text
                  @click="toggleExpand(s._id)"
                >{{ isExpanded(s._id) ? '收起分镜' : '展开分镜' }}</el-button>
              </div>

              <div v-if="isExpanded(s._id)" class="seg-shots" @click.stop>
                <div class="trim-row">
                  <span class="muted">保留前</span>
                  <el-input-number
                    :model-value="maxShots(s)"
                    :min="1"
                    :max="s.shots?.length || 1"
                    size="small"
                    controls-position="right"
                    style="width: 90px;"
                    @change="(v) => setTrim(s._id, v)"
                  />
                  <span class="muted">个分镜</span>
                </div>
                <div class="shot-items">
                  <div v-for="(sh, idx) in visibleShots(s)" :key="idx" class="shot-item">
                    <span class="shot-no">{{ idx + 1 }}</span>
                    <span class="shot-caption">{{ sh.caption || '分镜' + (idx + 1) }}</span>
                    <span class="shot-type">{{ sh.shot_type || 'medium' }}</span>
                    <span class="shot-dur">{{ sh.duration }}秒</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="splitLogs.length" class="split-log" :class="{ collapsed: logCollapsed }">
      <div class="log-head" @click="toggleLog">
        <span>🧠 推理进度 <span v-if="logCollapsed" class="muted">（{{ splitLogs.length }} 条）</span></span>
        <div class="log-actions">
          <el-button size="small" text @click.stop="toggleLog">{{ logCollapsed ? '展开' : '最小化' }}</el-button>
          <el-button size="small" text @click.stop="clearLogs">清空</el-button>
          <span v-if="!logCollapsed && loading" class="muted">已运行 {{ splitElapsed }} 秒</span>
        </div>
      </div>
      <div v-show="!logCollapsed" ref="logBody" class="log-body">
        <div v-for="(log, i) in splitLogs" :key="i" class="log-line">
          <span class="log-time">[{{ log.time }}]</span>
          <span class="log-msg">{{ log.msg }}</span>
        </div>
      </div>
    </div>

    <el-dialog v-model="onlineDialogVisible" title="配置在线模型" width="480px" align-center :before-close="beforeOnlineClose">
      <el-form label-width="90px">
        <el-form-item label="API 地址">
          <el-input v-model="modelConfig.apiBase" placeholder="https://api.openai.com/v1" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="modelConfig.apiKey" type="password" placeholder="sk-..." show-password />
        </el-form-item>
        <el-form-item label="模型名">
          <el-input v-model="modelConfig.model" placeholder="gpt-4o-mini / qwen-turbo 等" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="onlineDialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" @click="applyOnlineConfig">保存</el-button>
      </template>
    </el-dialog>
  </div>
  <div v-else class="planning"><div class="loading">加载中…</div></div>
</template>

<style scoped>
.planning { display: flex; flex-direction: column; height: 100%; min-height: 0; background: var(--bg-2); }
.plan-head {
  flex: 0 0 auto;
  display: flex; align-items: center; gap: 14px;
  padding: 10px 18px; background: var(--bg-2); border-bottom: 1px solid var(--line);
}
.back { background: var(--card); border: 1px solid var(--line); color: var(--sub); border-radius: 10px; padding: 7px 14px; cursor: pointer; font-size: 13px; font-weight: 600; }
.back:hover { color: var(--txt); border-color: var(--pur); background: var(--card-2); }
.ftitle { display: flex; flex-direction: column; flex: 1; }
.ti { font-size: 16px; font-weight: 800; color: var(--txt); }
.tsub { font-size: 12px; color: var(--sub); }
.actions { display: flex; gap: 10px; }
.plan-body { flex: 1; display: grid; grid-template-columns: 1fr 1fr; min-height: 0; overflow: hidden; }
.story-col, .result-col { padding: 16px; overflow-y: auto; border-right: 1px solid var(--line); }
.story-col { position: relative; }
.story-col.drag-over { border-color: var(--pur); background: rgba(138, 92, 255, 0.06); }
.result-col { border-right: 0; display: flex; flex-direction: column; min-height: 0; }
.story-tools { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
.drop-mask {
  position: absolute; inset: 0; z-index: 5;
  display: flex; align-items: center; justify-content: center;
  background: rgba(138, 92, 255, 0.12); color: var(--pur);
  font-size: 15px; font-weight: 700; border: 2px dashed var(--pur); border-radius: 12px;
  pointer-events: none;
}
h3 { font-size: 13px; color: var(--sub); text-transform: uppercase; letter-spacing: 1px; margin: 0; }
.result-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; }
.result-stats { font-size: 12px; }
.story-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; }
.split-actions { display: flex; align-items: center; gap: 8px; }
.muted { color: var(--sub); font-size: 12px; }
.empty-tip { color: var(--sub); font-size: 13px; padding: 40px 0; text-align: center; border: 1px dashed var(--line); border-radius: 12px; }
.result-stack { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.toolbar {
  flex: 0 0 auto;
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
  padding-bottom: 12px; margin-bottom: 12px;
  border-bottom: 1px solid var(--line);
}
.batch-btns { display: flex; gap: 4px; }
.view-toggle { margin-left: auto; display: flex; gap: 4px; }
.seg-list { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; padding-right: 4px; }
.seg-list.list .seg-card { flex-wrap: wrap; align-items: flex-start; padding: 10px 12px; }
.seg-list.list .seg-main { flex: 1 1 0; display: flex; align-items: center; gap: 12px; min-width: 0; }
.seg-list.list .seg-title-line { flex: 0 0 180px; flex-direction: column; align-items: flex-start; gap: 2px; }
.seg-list.list .seg-summary { flex: 1; margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.seg-list.list .seg-chars { display: none; }
.seg-list.list .seg-actions { padding-top: 0; }
.seg-list.list .seg-shots { width: 100%; margin-top: 10px; }
.seg-card {
  display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-start;
  background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px;
  cursor: pointer; transition: .15s;
}
.seg-card:hover { border-color: var(--pur); }
.seg-card.on { border-color: var(--pur); background: rgba(138, 92, 255, 0.08); }
.seg-card.expanded { border-color: var(--pur); }
.seg-check { padding-top: 2px; flex: 0 0 auto; }
.seg-main { flex: 1 1 0; min-width: 0; }
.seg-title-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.seg-title { font-weight: 700; color: var(--txt); font-size: 14px; }
.seg-dur { font-size: 12px; color: var(--sub); background: var(--bg-2); padding: 2px 8px; border-radius: 20px; }
.seg-summary { font-size: 12px; color: var(--txt); line-height: 1.5; margin: 6px 0; }
.seg-chars { display: flex; gap: 6px; flex-wrap: wrap; }
.seg-actions { flex: 0 0 auto; display: flex; align-items: flex-start; }
.seg-shots {
  width: 100%; margin-top: 6px; padding-top: 10px;
  border-top: 1px dashed var(--line);
}
.trim-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.shot-items { display: flex; flex-direction: column; gap: 6px; }
.shot-item {
  display: flex; align-items: center; gap: 10px;
  background: var(--bg-2); border-radius: 8px; padding: 8px 10px; font-size: 12px;
}
.shot-no { width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; border-radius: 50%; background: var(--pur); color: #fff; font-size: 11px; font-weight: 700; }
.shot-caption { flex: 1; min-width: 0; color: var(--txt); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.shot-type { color: var(--sub); text-transform: lowercase; }
.shot-dur { color: var(--sub); background: var(--card); padding: 2px 8px; border-radius: 20px; }
.loading { padding: 40px; text-align: center; color: var(--sub); }

.draft-banner {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  background: rgba(138, 92, 255, .12); border: 1px solid rgba(138, 92, 255, .4);
  color: var(--txt); border-radius: 10px; padding: 10px 14px; margin: 0 16px 14px; font-size: 13px;
}
.draft-tip { color: var(--pur); }
.draft-actions { display: flex; gap: 8px; flex: 0 0 auto; }

/* 推理进度日志面板（底部实时打印 AI 分场过程） */
.split-log {
  margin: 12px 16px 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--bg-2);
  overflow: hidden;
}
.log-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px;
  font-size: 13px; font-weight: 700; color: var(--txt);
  background: var(--card);
  border-bottom: 1px solid var(--line);
  cursor: pointer;
}
.split-log.collapsed .log-head { border-bottom: 0; }
.log-actions { display: flex; align-items: center; gap: 6px; }
.log-body {
  max-height: 220px; overflow-y: auto;
  padding: 8px 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px; line-height: 1.7;
}
.log-line { display: flex; gap: 8px; color: var(--txt); }
.log-time { color: var(--sub); flex: 0 0 auto; }
.log-msg { color: var(--txt); word-break: break-word; white-space: pre-wrap; }
</style>
