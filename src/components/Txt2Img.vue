<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, onActivated, onDeactivated } from 'vue'
import { ElMessage } from 'element-plus'
import { usePersistedState } from '../composables/usePersist'
import { useServiceStatus } from '../composables/useServiceStatus'
import { pushHistory, genKey } from '../composables/useGenHistory'
import { pushGeneration } from '../composables/useDirectorOrb'
import { t2iGenerate, t2iTask, t2iCancel, t2iHistory, t2iComic, difyConfig, updateSettings, difyStart, difyStop } from '../api.js'
import HistoryPanel from './HistoryPanel.vue'

defineOptions({ name: 'Txt2Img' })

// ---- 模型（本地仅 ComfyUI Realistic_Vision 可用，云端模型标注未接入）----
const models = [
  { id: 'realistic_vision', label: '秋叶 ComfyUI · Realistic_Vision（本地 · 可用）', local: true },
  { id: 'sd35', label: 'Stable Diffusion 3.5（云端 · 本机未接入）', local: false },
  { id: 'gpt_image_2', label: 'GPT Image 2（云端 · 本机未接入）', local: false },
]
const resolutions = ['512', '768', '1024', '1280', '1536']
const ratios = ['16:9', '9:16', '1:1', '4:3', '21:9', '3:4']
const styles = ['写实', '动漫', '电影感', '水彩']
const orchestrators = [
  { id: 'ollama', label: 'Ollama（本地大模型润色）' },
  { id: 'dify', label: 'Dify（编排大脑）' },
  { id: 'none', label: '无（使用原文提示词）' },
]

const state = usePersistedState('wn_t2i_state', {
  prompt: '',
  negative: '',
  model: 'realistic_vision',
  resolution: '1024',
  ratio: '16:9',
  style: '电影感',
  steps: 20,
  count: 4,
  seed: -1,
  enhance: true,
  orchestrator: 'ollama',
})

// ---- 服务绑定状态（Docker/Dify · Ollama · ComfyUI · 模型，生命周期安全）----
const { services, loading: loadingServices, difyReady, ollamaReady, refresh: refreshServices } = useServiceStatus({ interval: 10000 })

// ---- Dify / Docker 启停（真实调用后端 docker compose，切页可中断）----
const difyBusy = ref(false)
let difyLoopCancel = null
async function startDify() {
  if (!services.value?.docker?.reachable) {
    ElMessage.warning('请先在任务栏启动 Docker Desktop（鲸鱼变绿）后再试')
    return
  }
  if (difyBusy.value) return
  difyBusy.value = true
  let cancelled = false
  difyLoopCancel = () => { cancelled = true }
  try {
    const r = await difyStart()
    if (r && r.ok) {
      ElMessage.success('已触发 Dify 启动，正在等待容器就绪…')
      for (let i = 0; i < 30 && !cancelled; i++) {
        await new Promise((res) => setTimeout(res, 3000))
        if (cancelled) break
        await refreshServices()
        if (services.value?.dify?.reachable) break
      }
    } else {
      ElMessage.error('启动失败：' + (r?.stderr || r?.error || '未知错误'))
    }
  } catch (e) {
    ElMessage.error('启动出错：' + (e.message || e))
  } finally {
    difyBusy.value = false
    difyLoopCancel = null
    if (!cancelled) refreshServices()
  }
}
async function stopDify() {
  if (difyBusy.value) return
  difyBusy.value = true
  try {
    const r = await difyStop()
    if (r && r.ok) {
      ElMessage.success('已停止 Dify 容器')
    } else {
      ElMessage.error('停止失败：' + (r?.stderr || r?.error || '未知错误'))
    }
  } catch (e) {
    ElMessage.error('停止出错：' + (e.message || e))
  } finally {
    difyBusy.value = false
    refreshServices()
  }
}
onDeactivated(() => {
  if (difyLoopCancel) {
    difyLoopCancel()
    difyLoopCancel = null
  }
})

// ---- Dify 高级配置 ----
const showDify = ref(false)
const difyForm = reactive({ dify_base_url: 'http://127.0.0.1', dify_api_key: '', dify_app_id: '' })
async function loadDifyConfig() {
  try {
    const c = await difyConfig()
    difyForm.dify_base_url = c.dify_base_url || 'http://127.0.0.1'
  } catch (e) { /* ignore */ }
}
async function saveDify() {
  try {
    await updateSettings({
      dify_base_url: difyForm.dify_base_url,
      dify_api_key: difyForm.dify_api_key,
      dify_app_id: difyForm.dify_app_id,
    })
    ElMessage.success('已保存 Dify 编排配置')
    await refreshServices()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.message || e))
  }
}

// ---- 生成流程 ----
const generating = ref(false)
const task = ref(null)
const results = ref([])
const progressMsg = ref('')
let pollTimer = null
let pageActive = true

// ---- 历史记录（持久化）----
const historyTab = ref('current')   // current | history
const histories = ref([])
const loadingHistory = ref(false)
async function loadHistory() {
  loadingHistory.value = true
  try {
    // 限制首屏数量，避免图片过多造成内存/渲染压力；后续可加分页。
    const r = await t2iHistory(1, 20, '')
    histories.value = (r.items || []).filter(it => (it.images || []).length)
  } catch (e) {
    histories.value = []
  } finally {
    loadingHistory.value = false
  }
}
function useHistoryPrompt(item) {
  if (item && item.prompt) {
    state.prompt = item.prompt
    ElMessage.success('已载入历史提示词，可重新生成')
  }
}

// ---- 左侧历史生成面板（覆盖使用）----
const t2iHist = ref(null)
function onUseT2iHist(record) {
  if (record && record.payload && Array.isArray(record.payload.images)) {
    results.value = record.payload.images
    if (record.payload.prompt) state.prompt = record.payload.prompt
    ElMessage.success('已覆盖使用历史生成图像')
  }
}

const totalCount = computed(() => task.value?.params?.count || state.count)
const doneCount = computed(() => task.value?.progress || results.value.length)
const statusText = computed(() => {
  const s = task.value?.status
  if (s === 'succeeded') return '生成完成'
  if (s === 'failed') return '生成失败'
  if (s === 'cancelled') return '已取消'
  if (s === 'running') return '模型推理中…'
  if (s === 'queued') return '排队中…'
  return generating.value ? '提交中…' : ''
})

function finishPoll() {
  generating.value = false
  currentTaskId = null
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function generate() {
  if (!state.prompt.trim()) {
    ElMessage.warning('请先输入提示词')
    return
  }
  if (generating.value) return
  // 选择 Dify 但未就绪 → 直接拦截，不自动回退
  if (state.orchestrator === 'dify' && !difyReady.value) {
    let reason = 'Dify 未就绪'
    if (!services.value?.dify?.reachable) reason = 'Dify 未启动'
    else if (!services.value?.dify?.configured) reason = 'Dify 未配置 API Key'
    else if (!services.value?.dify?.key_valid) reason = 'Dify API Key 无效'
    ElMessage.error(`${reason}，无法使用 Dify 编排。请先配置有效的 API Key，或切换到其他编排方`)
    return
  }
  // Ollama 模型未加载 → 提示但允许继续（走词典兜底）
  if (state.orchestrator === 'ollama' && !ollamaReady.value) {
    ElMessage.warning('Ollama 模型未加载，本次将使用内置词典做基础中英翻译')
  }
  generating.value = true
  task.value = null
  results.value = []
  progressMsg.value = '正在提交文生图任务…'
  try {
    const r = await t2iGenerate({
      prompt: state.prompt,
      negative: state.negative,
      resolution: state.resolution,
      ratio: state.ratio,
      style: state.style,
      steps: state.steps,
      count: state.count,
      seed: state.seed,
      enhance: state.enhance,
      orchestrator: state.orchestrator,
    })
    const tid = r.task_id
    currentTaskId = tid
    startPoll(tid)
  } catch (e) {
    generating.value = false
    ElMessage.error('提交失败：' + (e.message || e))
  }
}

const cancelling = ref(false)
let currentTaskId = null

async function cancelGenerate() {
  if (!currentTaskId || cancelling.value) return
  cancelling.value = true
  try {
    await t2iCancel(currentTaskId)
    ElMessage.success('已取消生成')
    finishPoll()
    progressMsg.value = '已取消'
    if (task.value) task.value = { ...task.value, status: 'cancelled' }
  } catch (e) {
    ElMessage.error('取消失败：' + (e.message || e))
  } finally {
    cancelling.value = false
  }
}

function startPoll(tid) {
  if (pollTimer) clearInterval(pollTimer)
  pushGeneration('image', '文生图', 'running')
  pollTimer = setInterval(async () => {
    if (!pageActive) return
    try {
      const t = await t2iTask(tid)
      task.value = t
      if (t.status === 'succeeded') {
        finishPoll()
        results.value = (t.result && t.result.images) || []
        console.log('[Txt2Img] task succeeded, results=', JSON.parse(JSON.stringify(results.value)), 'task=', JSON.parse(JSON.stringify(t)))
        progressMsg.value = (t.result && t.result.note) || '生成完成'
        ElMessage.success(`已生成 ${results.value.length} 张图`)
        loadHistory()  // 刷新历史画廊
        // 记录到左侧历史生成面板（覆盖使用）
        const h = pushHistory(genKey.t2i('global'), {
          kind: 'image',
          title: (state.prompt || '文生图').slice(0, 20),
          preview: (results.value[0] && results.value[0].url) || '',
          payload: { images: results.value, prompt: state.prompt },
        })
        if (h && t2iHist.value) t2iHist.value.refresh()
        pushGeneration('image', '文生图', 'done')
      } else if (t.status === 'failed') {
        finishPoll()
        console.error('[Txt2Img] task failed', JSON.parse(JSON.stringify(t)))
        progressMsg.value = '失败：' + (t.error_message || '未知错误')
        ElMessage.error('生成失败：' + (t.error_message || '未知错误'))
        pushGeneration('image', '文生图', 'failed', t.error_message || '未知错误')
      } else if (t.status === 'cancelled') {
        finishPoll()
        progressMsg.value = '已取消'
        // 保留已生成的部分图片
        if (t.result && t.result.images) results.value = t.result.images
        ElMessage.info('生成已取消')
      } else if (t.status === 'running') {
        progressMsg.value = '模型推理中（ComfyUI 出图）…'
      } else {
        progressMsg.value = '排队中…'
      }
    } catch (e) { /* 轮询瞬断忽略 */ }
  }, 2500)
}

onActivated(() => {
  pageActive = true
  // 切回时若还有进行中的任务，恢复轮询
  if (generating.value && task.value?.task_id && !pollTimer) {
    startPoll(task.value.task_id)
  }
})
onDeactivated(() => {
  pageActive = false
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
})
onBeforeUnmount(() => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
})

// ---- 预览 / 下载 ----
const previewVisible = ref(false)
const previewImages = ref([])
const previewIndex = ref(0)
const previewMeta = ref(null)
function openPreview(itemOrItems, startIndex = 0, meta = null) {
  const list = Array.isArray(itemOrItems) ? itemOrItems : [itemOrItems]
  previewImages.value = list
  previewIndex.value = Math.max(0, Math.min(startIndex, list.length - 1))
  previewMeta.value = meta || null
  previewVisible.value = true
}
const previewItem = computed(() => {
  const list = previewImages.value
  return list.length ? list[previewIndex.value] : null
})
const previewPanel = computed(() => {
  // 漫画模式下，把当前预览图索引对应到 comicPanels 的元数据
  if (mode.value !== 'comic') return null
  return comicPanels.value[previewIndex.value] || null
})
const previewGenInfo = computed(() => {
  // 优先使用 openPreview 传入的元数据，否则按当前模式回退到 task / comic result
  if (previewMeta.value) return previewMeta.value
  if (mode.value === 'comic') {
    return { params: comicMeta.value?.params || {}, result: comicMeta.value || {}, panels: comicPanels.value }
  }
  return {
    params: task.value?.params || task.value?.result?.params || {},
    result: task.value?.result || {},
    prompt_used: task.value?.result?.prompt_used,
  }
})
function previewPrev() {
  if (previewIndex.value > 0) previewIndex.value--
}
function previewNext() {
  if (previewIndex.value < previewImages.value.length - 1) previewIndex.value++
}
function onPreviewKey(e) {
  if (!previewVisible.value) return
  if (e.key === 'ArrowLeft') previewPrev()
  else if (e.key === 'ArrowRight') previewNext()
  else if (e.key === 'Escape') previewVisible.value = false
}
onMounted(() => window.addEventListener('keydown', onPreviewKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onPreviewKey))
function imgLoadLog(item, ok, ev) {
  const info = { url: item?.url, seed: item?.seed, width: item?.width, height: item?.height, ok, err: ev?.target?.src }
  console.log('[Txt2Img] image ' + (ok ? 'loaded' : 'FAILED'), info)
}
function download(item) {
  const a = document.createElement('a')
  a.href = item.url
  a.download = (item.url.split('name=')[1] || 'image.png')
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}
function formatMs(ms) {
  if (!ms && ms !== 0) return '-'
  if (ms < 1000) return ms + ' ms'
  if (ms < 60000) return (ms / 1000).toFixed(1) + ' s'
  const m = Math.floor(ms / 60000)
  const s = ((ms % 60000) / 1000).toFixed(1)
  return m + ' m ' + s + ' s'
}

// ---- 漫画创作模式（占满文生图区域，复用导演 Agent 生图约束）----
const mode = ref('t2i')  // 't2i' | 'comic'
const comicStyles = ['日漫', '美漫', '港漫', '写实漫画', '水彩绘本']
const comic = reactive({
  story: '',
  style: '日漫',
  panels: 4,
  resolution: '1024',
  ratio: '3:4',
  steps: 28,
  seed: 42,
  charRefs: [],
})
const comicGenerating = ref(false)
const comicResults = ref([])
const comicPanels = ref([])
const comicMeta = ref(null)
const comicStyleRef = ref('')
const comicProgress = reactive({ done: 0, total: 0 })
const comicNote = ref('')

// ---- 漫画生成历史（服务端分页，避免结果丢失）----
const comicHistoryTab = ref('current')   // current | history
const comicHistories = ref([])
const loadingComicHistory = ref(false)
const comicHistPage = ref(1)
const comicHistSize = ref(8)
const comicHistTotal = ref(0)

async function loadComicHistory() {
  loadingComicHistory.value = true
  try {
    const r = await t2iHistory(comicHistPage.value, comicHistSize.value, '')
    const items = (r.items || []).filter((it) => {
      const p = it.params || {}
      return p.kind === 'comic' || (it.result && Array.isArray(it.result.panels))
    })
    comicHistories.value = items
    comicHistTotal.value = r.pagination?.total || items.length
  } catch (e) {
    comicHistories.value = []
    comicHistTotal.value = 0
  } finally {
    loadingComicHistory.value = false
  }
}
function onComicHistPageChange(p) {
  comicHistPage.value = p
  loadComicHistory()
}
function useComicHistory(item) {
  if (item && item.result) {
    comicResults.value = item.result.images || []
    comicPanels.value = item.result.panels || []
    comicMeta.value = {
      params: item.params || item.result.params || {},
      elapsed_ms: item.result.elapsed_ms,
      orchestrator_used: item.result.orchestrator_used,
      model: item.result.model,
      note: item.result.note,
    }
    comicStyleRef.value = item.result.style_ref || ''
    comicNote.value = '已载入历史漫画：' + (item.prompt || '').slice(0, 30)
    comicHistoryTab.value = 'current'
    ElMessage.success('已载入历史漫画')
  }
}

function onRefFiles(e) {
  const files = Array.from(e.target.files || [])
  files.forEach((f) => {
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') comic.charRefs.push(reader.result)
    }
    reader.readAsDataURL(f)
  })
  e.target.value = ''
}

async function generateComic() {
  if (!comic.story.trim()) {
    ElMessage.warning('请先输入故事')
    return
  }
  if (comicGenerating.value) return
  comicGenerating.value = true
  comicResults.value = []
  comicPanels.value = []
  comicStyleRef.value = ''
  comicProgress.done = 0
  comicProgress.total = parseInt(comic.panels) || 4
  comicNote.value = '正在生成漫画（ComfyUI 出图，约 1-3 分钟）…'
  pushGeneration('image', '漫画创作', 'running')
  try {
    const r = await t2iComic({
      story: comic.story,
      style: comic.style,
      panels: parseInt(comic.panels) || 4,
      resolution: comic.resolution,
      ratio: comic.ratio,
      steps: comic.steps,
      seed: parseInt(comic.seed) || 42,
      cfg: 8.5,
      enhance: true,
      orchestrator: 'ollama',
      character_refs: comic.charRefs,
    })
    comicResults.value = r.images || []
    comicPanels.value = r.panels || []
    comicMeta.value = {
      params: r.params || {},
      elapsed_ms: r.elapsed_ms,
      orchestrator_used: r.orchestrator_used,
      model: r.model,
      note: r.note,
    }
    comicStyleRef.value = r.style_ref || ''
    comicNote.value = r.note || '生成完成'
    comicProgress.done = comicResults.value.length
    comicProgress.total = comicResults.value.length || comicProgress.total
    ElMessage.success(`已生成 ${comicResults.value.length} 格漫画`)
    // 刷新服务端漫画历史（如果当前在历史标签）
    if (comicHistoryTab.value === 'history') loadComicHistory()
    // 同步进文生图历史（覆盖使用）
    const h = pushHistory(genKey.t2i('global'), {
      kind: 'image',
      title: (comic.story || '漫画').slice(0, 20),
      preview: (comicResults.value[0] && comicResults.value[0].url) || '',
      payload: { images: comicResults.value, prompt: comic.story },
    })
    if (h && t2iHist.value) t2iHist.value.refresh()
    pushGeneration('image', '漫画创作', 'done')
  } catch (e) {
    comicNote.value = '生成失败：' + (e.message || e)
    ElMessage.error('漫画生成失败：' + (e.message || e))
    pushGeneration('image', '漫画创作', 'failed', e.message || '')
  } finally {
    comicGenerating.value = false
  }
}

// 初始加载（服务状态由 useServiceStatus 自动在 activated 时拉取）
loadDifyConfig()
loadHistory()
</script>

<template>
  <div class="page">
    <!-- 左侧：历史生成记录（页面级，固定在内容区左侧，默认折叠，点击展开可选择覆盖使用） -->
    <HistoryPanel
      v-if="mode === 't2i'"
      ref="t2iHist"
      storage-key="manju_hist_t2i_global"
      title="生成历史"
      kind-filter="all"
      :floating="true"
      @use="onUseT2iHist"
    />
    <div class="page-head">
      <div class="ph-left">
        <div class="segment-tabs" role="tablist">
          <button
            :class="{ active: mode === 't2i' }"
            @click="mode = 't2i'"
            :aria-selected="mode === 't2i'"
          >
            文生图
          </button>
          <button
            :class="{ active: mode === 'comic' }"
            @click="mode = 'comic'"
            :aria-selected="mode === 'comic'"
          >
            漫画创作
          </button>
        </div>
        <p class="page-subtitle" v-if="mode === 't2i'">输入文字描述，调用本地 ComfyUI 真实出图</p>
        <p class="page-subtitle" v-else>输入故事，单次点击完整生成全部 {{ comic.panels }} 格漫画</p>
      </div>
      <div class="premium-badge">
        <span class="dot" />
        本地生成 · 不计积分
      </div>
    </div>

    <!-- 服务绑定状态条：Docker · Dify · Ollama · ComfyUI · 模型（Docker/Dify 可启停） -->
    <div class="svc-bar" v-if="mode === 't2i'">
      <div class="svc" :class="services.docker?.reachable ? 'ok' : 'bad'">
        <span class="dot" /> Docker
        <small>{{ services.docker?.reachable ? '已运行' : '未运行（请启动 Docker Desktop）' }}</small>
      </div>
      <div class="svc" :class="difyReady ? 'ok' : (services.dify?.reachable ? 'warn' : 'bad')">
        <span class="dot" /> Dify
        <small>
          <template v-if="difyReady">编排已就绪</template>
          <template v-else-if="!services.dify?.reachable">未启动</template>
          <template v-else-if="!services.dify?.configured">未配置 API Key</template>
          <template v-else-if="!services.dify?.key_valid">API Key 无效</template>
          <template v-else>未就绪</template>
        </small>
        <button v-if="!services.dify?.reachable && services.docker?.reachable" class="mini-btn"
          :disabled="difyBusy" @click="startDify">{{ difyBusy ? '启动中…' : '启动 Dify' }}</button>
        <button v-else-if="services.dify?.reachable" class="mini-btn ghost"
          :disabled="difyBusy" @click="stopDify">{{ difyBusy ? '停止中…' : '停止' }}</button>
        <button v-else class="mini-btn" disabled title="需先启动 Docker Desktop">启动 Dify</button>
      </div>
      <div class="svc" :class="ollamaReady ? 'ok' : (services.ollama?.reachable ? 'warn' : 'bad')">
        <span class="dot" /> Ollama
        <small>
          <template v-if="ollamaReady">已就绪</template>
          <template v-else-if="services.ollama?.reachable">模型未加载</template>
          <template v-else>未启动</template>
        </small>
      </div>
      <div class="svc" :class="services.comfyui?.reachable ? 'ok' : 'bad'">
        <span class="dot" /> ComfyUI
        <small>{{ services.comfyui?.reachable ? '已连接' : '未启动' }}</small>
      </div>
      <div class="svc ok">
        <span class="dot" /> 模型 Realistic_Vision
        <small>本地 SD1.5</small>
      </div>
      <button class="btn sm ghost svc-refresh" :disabled="loadingServices" @click="refreshServices">
        {{ loadingServices ? '刷新中…' : '刷新状态' }}
      </button>
    </div>

    <div class="ws-grid" v-if="mode === 't2i'">
      <!-- 左侧：参数 -->
      <div class="panel">
        <div class="param-group">
          <div class="g-title">提示词</div>
          <el-input v-model="state.prompt" type="textarea" :rows="3"
            placeholder="描述你想要的画面，如：雨夜霓虹下的赛博朋克城市街景" />
          <div class="g-title" style="margin-top:12px">反向提示词</div>
          <el-input v-model="state.negative" type="textarea" :rows="2"
            placeholder="不希望出现的内容，如：low quality, blurry, watermark" />
        </div>

        <div class="param-group">
          <div class="g-title">模型与参数</div>
          <div class="param-row">
            <span class="lab">模型</span>
            <el-select v-model="state.model" style="width:240px">
              <el-option v-for="m in models" :key="m.id" :label="m.label" :value="m.id"
                :disabled="!m.local" />
            </el-select>
          </div>
          <div class="param-row">
            <span class="lab">分辨率</span>
            <el-select v-model="state.resolution" style="width:240px">
              <el-option v-for="r in resolutions" :key="r" :label="r + '（长边）'" :value="r" />
            </el-select>
          </div>
          <div class="param-row">
            <span class="lab">比例</span>
            <el-select v-model="state.ratio" style="width:240px">
              <el-option v-for="r in ratios" :key="r" :label="r" :value="r" />
            </el-select>
          </div>
          <div class="param-row">
            <span class="lab">风格</span>
            <el-select v-model="state.style" style="width:240px">
              <el-option v-for="s in styles" :key="s" :label="s" :value="s" />
            </el-select>
          </div>
          <div class="param-row" style="align-items:flex-start">
            <span class="lab" style="margin-top:6px">采样步数</span>
            <div style="width:240px"><el-slider v-model="state.steps" :min="1" :max="50" show-input /></div>
          </div>
          <div class="param-row" style="align-items:flex-start">
            <span class="lab" style="margin-top:6px">张数</span>
            <div style="width:240px"><el-slider v-model="state.count" :min="1" :max="9" show-input /></div>
          </div>
          <div class="param-row">
            <span class="lab">种子</span>
            <el-input-number v-model="state.seed" :min="-1" :max="999999999" style="width:240px" />
          </div>
        </div>

        <div class="param-group">
          <div class="g-title">提示词编排</div>
          <div class="param-row">
            <span class="lab">智能润色</span>
            <el-switch v-model="state.enhance" :disabled="state.orchestrator === 'dify'" />
            <small class="muted" style="margin-left:8px">Ollama 中文→英文 SD 提示词</small>
          </div>
          <div class="param-row">
            <span class="lab">编排方</span>
            <el-select v-model="state.orchestrator" style="width:240px">
              <el-option v-for="o in orchestrators" :key="o.id" :label="o.label" :value="o.id" />
            </el-select>
          </div>
          <div v-if="state.orchestrator === 'dify' && !difyReady" class="hint warn">
            <template v-if="!services.dify?.reachable">
              Dify 未启动：需先启动 Docker Desktop，再点击上方「启动 Dify」。
            </template>
            <template v-else-if="!services.dify?.configured">
              Dify 服务已启动，但尚未配置 API Key：请在下方「高级」中填写 API Key 后保存。
            </template>
            <template v-else-if="!services.dify?.key_valid">
              Dify 服务已启动，但 API Key 无效：请检查下方「高级」中的 API Key 是否与 Dify 应用一致。
            </template>
            <template v-else>
              Dify 未就绪，请检查配置。
            </template>
          </div>
          <div v-if="state.orchestrator === 'ollama' && !ollamaReady" class="hint warn">
            <template v-if="services.ollama?.reachable">
              Ollama 服务已运行，但目标模型未加载（或已被卸载）。请在 Ollama 中加载模型后再使用，本次生成将使用内置词典做基础中英翻译。
            </template>
            <template v-else>
              Ollama 未启动：本次生成将使用内置词典做基础中英翻译。
            </template>
          </div>
          <div class="adv-toggle" @click="showDify = !showDify">
            {{ showDify ? '▾' : '▸' }} 高级：Dify 编排配置
          </div>
          <div v-if="showDify" class="adv-box">
            <div class="param-row">
              <span class="lab">Base URL</span>
              <el-input v-model="difyForm.dify_base_url" style="width:240px" placeholder="http://127.0.0.1" />
            </div>
            <div class="param-row">
              <span class="lab">API Key</span>
              <el-input v-model="difyForm.dify_api_key" style="width:240px" placeholder="app-xxxx" show-password />
            </div>
            <div class="param-row">
              <span class="lab">App ID</span>
              <el-input v-model="difyForm.dify_app_id" style="width:240px" placeholder="可选" />
            </div>
            <button class="btn sm" @click="saveDify">保存配置</button>
          </div>
        </div>

        <div class="generate-actions">
          <button class="generate-btn" :disabled="generating" @click="generate">
            <span class="generate-icon">
              <svg v-if="!generating" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
              <span v-else class="spinner" />
            </span>
            <span class="generate-label">{{ generating ? '正在生成…' : '开始生成' }}</span>
          </button>
          <button v-if="generating" class="btn sm ghost cancel-btn"
            :disabled="cancelling" @click="cancelGenerate"
            style="min-width:100px;justify-content:center">
            {{ cancelling ? '取消中…' : '暂停' }}
          </button>
        </div>

        <div class="progress" v-if="generating || task?.status === 'failed' || task?.status === 'cancelled'">
          <div class="lab">
            <span>{{ progressMsg || statusText }}</span>
            <span>{{ doneCount }}/{{ totalCount }}</span>
          </div>
          <el-progress :percentage="totalCount ? Math.round(doneCount / totalCount * 100) : (task?.status==='succeeded'?100:0)"
            :stroke-width="8" :color="['#8B7ED8', '#5A96E3']"
            :status="task?.status==='failed' ? 'exception' : (task?.status==='cancelled' ? 'exception' : undefined)" />
        </div>
      </div>

      <!-- 右侧：生成结果（真实图片） -->
      <div class="panel">
        <div class="res-tabs">
          <span :class="{ active: historyTab === 'current' }" @click="historyTab = 'current'">本次结果</span>
          <span :class="{ active: historyTab === 'history' }" @click="historyTab = 'history'; loadHistory()">
            历史记录 <small v-if="histories.length" class="muted">{{ histories.length }}</small>
          </span>
        </div>

        <!-- 本次结果 -->
        <div v-if="historyTab === 'current'">
          <div class="g-title" style="margin-bottom:4px">
            生成结果 <span class="muted">（{{ results.length }} 张）</span>
          </div>
          <div v-if="!results.length && !generating" class="empty">
            还没有生成结果。左侧填写提示词后点击「开始生成」，图像由本地 ComfyUI 实时产出。
          </div>
          <div class="asset-grid">
            <div class="asset-card" v-for="(item, i) in results" :key="i" @click="openPreview(item, 0, { params: task.value?.params, result: task.value?.result })">
              <div class="ph">
                <img :src="item.url" alt="生成图" loading="lazy" @load="imgLoadLog(item, true, $event)" @error="imgLoadLog(item, false, $event)" />
                <div class="ph-acts" @click.stop>
                  <button class="btn sm ghost" @click.stop="download(item)">下载</button>
                  <button class="btn sm ghost" @click.stop="openPreview(item, 0, { params: task.value?.params, result: task.value?.result })">放大</button>
                </div>
              </div>
              <div class="info">
                <div class="name">图 {{ i + 1 }}</div>
                <div class="type">seed {{ item.seed }} · {{ item.width }}×{{ item.height }}</div>
              </div>
            </div>
          </div>
          <div v-if="task?.result?.orchestrator_used === 'dict_fallback'" class="dict-fallback-warn">
            ⚠️ AI 翻译服务不可用（Dify/Ollama 均未就绪），已使用内置词典做基础中英翻译。
            提示词质量有限，建议启动 Ollama 并加载模型以获得最佳效果。
          </div>
          <div v-if="task?.result?.prompt_used" class="prompt-used">
            <div class="g-title">实际使用的提示词（{{ task.result.orchestrator_used || '原文' }}）</div>
            <pre>{{ task.result.prompt_used }}</pre>
          </div>
        </div>

        <!-- 历史记录（持久化画廊） -->
        <div v-else>
          <div class="g-title" style="margin-bottom:4px">
            历史生成 <span class="muted">（本地数据库留存，刷新不丢失）</span>
          </div>
          <div v-if="loadingHistory" class="empty">加载历史中…</div>
          <div v-else-if="!histories.length" class="empty">
            暂无历史记录。生成成功后会出现在这里。
          </div>
          <div v-else class="asset-grid">
            <div class="asset-card" v-for="(h, i) in histories" :key="h.task_id" @click="openPreview(h.images, 0, h)">
              <div class="ph">
                <img :src="h.images[0].url" alt="历史图" loading="lazy" @error="imgLoadLog(h.images[0], false, $event)" />
                <div v-if="h.images.length > 1" class="img-count">{{ h.images.length }} 张</div>
                <div class="ph-acts" @click.stop>
                  <button class="btn sm ghost" @click.stop="useHistoryPrompt(h)">复用提示词</button>
                  <button class="btn sm ghost" @click.stop="openPreview(h.images, 0, h)">放大</button>
                </div>
              </div>
              <div class="info">
                <div class="name" :title="h.prompt">{{ h.prompt.slice(0, 14) }}{{ h.prompt.length > 14 ? '…' : '' }}</div>
                <div class="type">{{ (h.images || []).length }} 张 · {{ h.status }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 漫画创作（占满整个文生图区域） -->
    <div v-if="mode === 'comic'" class="comic-wrap">
      <div class="comic-head">
        <div class="comic-head-text">
          <h2>漫画创作</h2>
          <p class="page-subtitle">单次点击完整生成全部 {{ comic.panels }} 格，复用导演 Agent 风格参考与角色一致性约束</p>
        </div>
        <div class="premium-badge">
          <span class="dot" />
          本地生成 · 不计积分
        </div>
      </div>

      <div class="comic-grid">
        <!-- 左侧：参数 -->
        <div class="panel comic-params">
          <div class="param-group">
            <div class="g-title">故事 / 剧情</div>
            <el-input v-model="comic.story" type="textarea" :rows="6"
              placeholder="输入一个短故事，例如：男孩在雨夜捡到一只受伤的流浪猫，带回家悉心照料，猫康复后却在一个清晨悄然离开，只留下窗台上一朵小花。" />
          </div>
          <div class="param-group">
            <div class="g-title">漫画设置</div>
            <div class="param-row"><span class="lab">风格</span>
              <el-select v-model="comic.style" style="width:240px">
                <el-option v-for="s in comicStyles" :key="s" :label="s" :value="s" />
              </el-select>
            </div>
            <div class="param-row"><span class="lab">格数</span>
              <el-select v-model="comic.panels" style="width:240px">
                <el-option :value="4" label="4 格（推荐）" />
                <el-option :value="6" label="6 格" />
                <el-option :value="3" label="3 格" />
                <el-option :value="8" label="8 格" />
              </el-select>
            </div>
            <div class="param-row"><span class="lab">分辨率</span>
              <el-select v-model="comic.resolution" style="width:240px">
                <el-option v-for="r in resolutions" :key="r" :label="r + '（长边）'" :value="r" />
              </el-select>
            </div>
            <div class="param-row"><span class="lab">比例</span>
              <el-select v-model="comic.ratio" style="width:240px">
                <el-option v-for="r in ratios" :key="r" :label="r" :value="r" />
              </el-select>
            </div>
            <div class="param-row" style="align-items:flex-start"><span class="lab" style="margin-top:6px">采样步数</span>
              <div style="width:240px"><el-slider v-model="comic.steps" :min="1" :max="50" show-input /></div>
            </div>
          </div>
          <div class="param-group">
            <div class="g-title">角色一致性参考图（可选）</div>
            <p class="muted" style="margin:0 0 8px">上传角色定妆照，保持跨格角色一致（§5.2 IP-Adapter 身份路）</p>
            <div class="comic-refs">
              <div class="ref-thumb" v-for="(r, i) in comic.charRefs" :key="i">
                <img :src="r" /><button class="ref-del" @click="comic.charRefs.splice(i, 1)">×</button>
              </div>
              <label class="ref-add">
                <input type="file" accept="image/*" multiple @change="onRefFiles" hidden />
                <span>＋ 添加</span>
              </label>
            </div>
          </div>
          <div class="generate-actions">
            <button class="generate-btn" :disabled="comicGenerating" @click="generateComic">
              <span class="generate-icon">
                <svg v-if="!comicGenerating" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                <span v-else class="spinner" />
              </span>
              <span class="generate-label">{{ comicGenerating ? '正在生成漫画…' : '一键生成 ' + comic.panels + ' 格漫画' }}</span>
              <span v-if="!comicGenerating" class="generate-hint">Enter</span>
            </button>
          </div>
          <div class="progress" v-if="comicGenerating || comicNote">
            <div class="lab">
              <span>{{ comicNote || '准备中…' }}</span>
              <span>{{ comicProgress.done }}/{{ comicProgress.total }}</span>
            </div>
            <el-progress :percentage="comicProgress.total ? Math.round(comicProgress.done / comicProgress.total * 100) : 0"
              :stroke-width="8" :color="['#8B7ED8', '#5A96E3']" />
          </div>
        </div>

        <!-- 右侧：结果 -->
        <div class="panel comic-result">
          <div v-if="comicStyleRef" class="comic-style-ref">
            <span class="g-title">统一风格参考图（N03）</span>
            <img :src="comicStyleRef" @click="openPreview([{ url: comicStyleRef }], 0, { params: comicMeta.value?.params || {}, result: comicMeta.value || {} })" />
          </div>

          <div class="res-tabs">
            <span :class="{ active: comicHistoryTab === 'current' }" @click="comicHistoryTab = 'current'">本次结果</span>
            <span :class="{ active: comicHistoryTab === 'history' }" @click="comicHistoryTab = 'history'; loadComicHistory()">
              生成历史 <small v-if="comicHistTotal" class="muted">{{ comicHistTotal }}</small>
            </span>
          </div>

          <!-- 本次结果 -->
          <div v-if="comicHistoryTab === 'current'">
            <div class="g-title" style="margin-bottom:4px">
              漫画结果 <span class="muted">（{{ comicResults.length }} 格）</span>
            </div>
            <div v-if="!comicResults.length && !comicGenerating" class="empty">
              还没有生成结果。左侧输入故事后点击「一键生成 {{ comic.panels }} 格漫画」，{{ comic.panels }} 格由本地 ComfyUI 一次性产出。
            </div>
            <div class="comic-panels" :class="'cols-' + comic.panels">
              <div class="comic-cell" v-for="(img, i) in comicResults" :key="i" @click="openPreview(comicResults, i, { params: comicMeta.value?.params || {}, result: comicMeta.value || {}, panels: comicPanels.value })">
                <div class="ph">
                  <img :src="img.url" alt="漫画格" loading="lazy" @load="imgLoadLog(img, true, $event)" @error="imgLoadLog(img, false, $event)" />
                  <div class="ph-acts" @click.stop>
                    <button class="btn sm ghost" @click.stop="download(img)">下载</button>
                    <button class="btn sm ghost" @click.stop="openPreview(comicResults, i, { params: comicMeta.value?.params || {}, result: comicMeta.value || {}, panels: comicPanels.value })">放大</button>
                  </div>
                </div>
                <div class="cap">{{ (comicPanels[i] && comicPanels[i].caption) || ('第 ' + (i + 1) + ' 格') }}</div>
              </div>
            </div>
          </div>

          <!-- 生成历史（分页，避免结果丢失） -->
          <div v-else>
            <div class="g-title" style="margin-bottom:4px">
              漫画生成历史 <span class="muted">（{{ comicHistTotal }} 条）</span>
            </div>
            <div v-if="loadingComicHistory" class="empty">加载历史中…</div>
            <div v-else-if="!comicHistories.length" class="empty">暂无历史漫画记录</div>
            <div v-else class="asset-grid">
              <div class="asset-card" v-for="(h, i) in comicHistories" :key="h.task_id || i" @click="useComicHistory(h)">
                <div class="ph">
                  <img :src="(h.result && h.result.images && h.result.images[0] && h.result.images[0].url) || ''" alt="历史漫画" loading="lazy" />
                  <div class="img-count" v-if="h.result && h.result.images && h.result.images.length">{{ h.result.images.length }} 格</div>
                  <div class="ph-acts" @click.stop>
                    <button class="btn sm ghost" @click.stop="useComicHistory(h)">载入</button>
                  </div>
                </div>
                <div class="info">
                  <div class="name" :title="h.prompt">{{ (h.prompt || '漫画').slice(0, 14) }}{{ (h.prompt || '').length > 14 ? '…' : '' }}</div>
                  <div class="type">{{ ((h.result && h.result.images) || []).length }} 格 · {{ h.status }}</div>
                </div>
              </div>
            </div>
            <div v-if="comicHistTotal > comicHistSize" class="hist-pagination">
              <el-pagination
                v-model:current-page="comicHistPage"
                :page-size="comicHistSize"
                :total="comicHistTotal"
                layout="prev, pager, next"
                @current-change="onComicHistPageChange"
              />
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="previewVisible" title="预览" width="64%" align-center @opened="() => {}" destroy-on-close>
      <div class="preview-wrap">
        <button v-if="previewImages.length > 1" class="nav-btn prev" :disabled="previewIndex <= 0" @click="previewPrev">❮</button>
        <div v-if="previewItem" class="big-ph">
          <img :src="previewItem.url" alt="预览" @load="imgLoadLog(previewItem, true, $event)" @error="imgLoadLog(previewItem, false, $event)" />
        </div>
        <button v-if="previewImages.length > 1" class="nav-btn next" :disabled="previewIndex >= previewImages.length - 1" @click="previewNext">❯</button>
      </div>
      <div class="preview-meta">
        <span v-if="previewImages.length > 1" class="counter">{{ previewIndex + 1 }} / {{ previewImages.length }}</span>
        <span class="muted">seed {{ previewItem?.seed }} · {{ previewItem?.width }}×{{ previewItem?.height }}</span>
      </div>
      <!-- 生成信息：当前格/图的提示词、完整参数与耗时 -->
      <div class="preview-gen-info">
        <div class="info-section" v-if="previewPanel && previewPanel.caption">
          <div class="info-title">画面说明</div>
          <div class="info-body">{{ previewPanel.caption }}</div>
        </div>
        <div class="info-section" v-if="previewPanel && (previewPanel.characters || previewPanel.action || previewPanel.scene)">
          <div class="info-title">分镜要素</div>
          <div class="preview-tags">
            <el-tag v-if="previewPanel.characters" size="small" type="info">角色：{{ previewPanel.characters }}</el-tag>
            <el-tag v-if="previewPanel.action" size="small" type="info">动作：{{ previewPanel.action }}</el-tag>
            <el-tag v-if="previewPanel.scene" size="small" type="info">场景：{{ previewPanel.scene }}</el-tag>
          </div>
        </div>
        <div class="info-section" v-if="previewPanel && previewPanel.prompt">
          <div class="info-title">原始画面描述</div>
          <div class="info-body pre">{{ previewPanel.prompt }}</div>
        </div>
        <div class="info-section" v-if="mode === 'comic' && (previewPanel?.final_prompt || previewItem?.final_prompt)">
          <div class="info-title">实际生成提示词（SD 英文）</div>
          <div class="info-body pre">{{ previewPanel?.final_prompt || previewItem?.final_prompt }}</div>
        </div>
        <div class="info-section" v-if="mode === 't2i' && previewGenInfo.prompt_used">
          <div class="info-title">实际生成提示词（SD 英文）</div>
          <div class="info-body pre">{{ previewGenInfo.prompt_used }}</div>
        </div>
        <div class="info-section" v-if="previewGenInfo.params && Object.keys(previewGenInfo.params).length">
          <div class="info-title">生成参数</div>
          <div class="param-grid">
            <div class="param-cell"><span class="param-key">模型</span><span class="param-val">{{ previewGenInfo.params.model || previewGenInfo.result?.model || '-' }}</span></div>
            <div class="param-cell"><span class="param-key">尺寸</span><span class="param-val">{{ previewGenInfo.params.width && previewGenInfo.params.height ? previewGenInfo.params.width + '×' + previewGenInfo.params.height : (previewItem?.width + '×' + previewItem?.height) }}</span></div>
            <div class="param-cell"><span class="param-key">步数</span><span class="param-val">{{ previewGenInfo.params.steps ?? previewItem?.steps ?? '-' }}</span></div>
            <div class="param-cell"><span class="param-key">CFG</span><span class="param-val">{{ previewGenInfo.params.cfg ?? previewItem?.cfg ?? '-' }}</span></div>
            <div class="param-cell"><span class="param-key">采样器</span><span class="param-val">{{ previewGenInfo.params.sampler || previewItem?.sampler || '-' }}</span></div>
            <div class="param-cell"><span class="param-key">种子</span><span class="param-val">{{ previewItem?.seed ?? previewGenInfo.params.seed ?? '-' }}</span></div>
            <div class="param-cell" v-if="previewGenInfo.params.style !== undefined"><span class="param-key">风格</span><span class="param-val">{{ previewGenInfo.params.style }}</span></div>
            <div class="param-cell" v-if="previewGenInfo.params.orchestrator !== undefined"><span class="param-key">润色</span><span class="param-val">{{ previewGenInfo.params.orchestrator }}</span></div>
            <div class="param-cell"><span class="param-key">耗时</span><span class="param-val">{{ formatMs(previewGenInfo.result?.elapsed_ms ?? previewGenInfo.params?.elapsed_ms) }}</span></div>
          </div>
        </div>
      </div>
      <div v-if="previewImages.length > 1" class="preview-thumbs">
        <img v-for="(it, idx) in previewImages" :key="idx"
          :src="it.url" :class="{ active: idx === previewIndex }"
          @click="previewIndex = idx" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.svc-bar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 20px; }
.svc {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,.08);
  background: rgba(255,255,255,.04);
  font-size: 12px;
  font-weight: 600;
  color: var(--sub);
}
.svc .dot { width: 6px; height: 6px; border-radius: 50%; background: #888; }
.svc.ok { border-color: rgba(129, 199, 132, .35); color: #a5d6a7; background: rgba(129, 199, 132, .08); }
.svc.ok .dot { background: #81c784; box-shadow: 0 0 8px rgba(129, 199, 132, .45); }
.svc.bad { border-color: rgba(239, 83, 80, .35); color: #ef9a9a; background: rgba(239, 83, 80, .08); }
.svc.bad .dot { background: #ef5350; }
.svc.warn { border-color: rgba(255, 202, 40, .35); color: #ffe082; background: rgba(255, 202, 40, .08); }
.svc.warn .dot { background: #ffca28; box-shadow: 0 0 8px rgba(255, 202, 40, .35); }
.svc small { color: inherit; opacity: .75; font-weight: 500; }
.svc-refresh { margin-left: auto; }
.cancel-btn { border-color: #e74c3c !important; color: #e74c3c !important; }
.cancel-btn:hover:not(:disabled) { background: #e74c3c !important; color: #fff !important; }
.mini-btn { margin-left: 8px; padding: 2px 10px; border-radius: 8px; border: 1px solid var(--pur);
  background: var(--pur); color: #fff; font-size: 12px; font-weight: 700; cursor: pointer; transition: .15s; }
.mini-btn:hover:not(:disabled) { filter: brightness(1.1); }
.mini-btn:disabled { opacity: .5; cursor: not-allowed; }
.mini-btn.ghost { background: transparent; color: var(--sub); border-color: var(--line); }
.mini-btn.ghost:hover:not(:disabled) { color: var(--txt); border-color: var(--sub); }

.ws-grid { display: grid; grid-template-columns: 360px 1fr; gap: 20px; align-items: start; }
.panel {
  background: rgba(255,255,255,.025);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 18px;
  padding: 22px;
  box-shadow: 0 8px 32px rgba(0,0,0,.18);
  backdrop-filter: blur(8px);
}
.panel > .param-group + .param-group { margin-top: 22px; }
.panel > .param-group > .g-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--txt);
  margin-bottom: 12px;
  letter-spacing: .04em;
  text-transform: uppercase;
}
.panel > .param-group > .param-row { margin-bottom: 12px; }
.panel > .param-group > .param-row:last-child { margin-bottom: 0; }

.ph { position: relative; aspect-ratio: 1; display: flex; align-items: center; justify-content: center;
  background: var(--card-2); border-radius: 8px; overflow: hidden; }
.ph img { width: 100%; height: 100%; object-fit: cover; display: block; }
.ph-acts { position: absolute; inset: auto 0 0 0; display: flex; gap: 6px; justify-content: center;
  padding: 8px; opacity: 0; transition: .2s; background: linear-gradient(0deg, rgba(0,0,0,.55), transparent); }
.asset-card:hover .ph-acts { opacity: 1; }

.big-ph { width: 100%; max-height: 60vh; border-radius: 12px; display: flex; align-items: center; justify-content: center; background: var(--card-2); overflow: hidden; }
.big-ph img { max-width: 100%; max-height: 60vh; object-fit: contain; }

.preview-wrap { display: flex; align-items: center; gap: 10px; }
.nav-btn { width: 36px; height: 64px; border-radius: 10px; border: 1px solid var(--line); background: var(--card-2);
  color: var(--txt); font-size: 18px; cursor: pointer; transition: .15s; flex-shrink: 0; }
.nav-btn:hover:not(:disabled) { background: var(--pur); color: #fff; border-color: var(--pur); }
.nav-btn:disabled { opacity: .35; cursor: not-allowed; }
.preview-meta { display: flex; align-items: center; gap: 14px; margin-top: 12px; font-size: 13px; }
.preview-meta .counter { background: var(--pur); color: #fff; padding: 2px 10px; border-radius: 20px; font-weight: 700; }
.preview-thumbs { display: flex; gap: 8px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); overflow-x: auto; }
.preview-thumbs img { width: 64px; height: 64px; object-fit: cover; border-radius: 8px; border: 2px solid transparent;
  cursor: pointer; opacity: .65; transition: .15s; }
.preview-thumbs img.active { border-color: var(--pur); opacity: 1; }
.preview-thumbs img:hover { opacity: 1; }

.img-count { position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,.65); color: #fff;
  padding: 3px 8px; border-radius: 12px; font-size: 12px; font-weight: 700; pointer-events: none; }

.hint { font-size: 12px; padding: 6px 10px; border-radius: 8px; margin: 6px 0; }
.hint.warn { background: rgba(231,76,60,.12); color: #e74c3c; border: 1px solid rgba(231,76,60,.3); }
.adv-toggle { cursor: pointer; color: var(--pur); font-size: 13px; margin-top: 8px; user-select: none; }
.adv-box { margin-top: 8px; padding: 10px; border: 1px dashed var(--line); border-radius: 10px; }

.prompt-used { margin-top: 14px; }
.dict-fallback-warn { margin-top: 14px; padding: 10px 14px; border-radius: 10px; font-size: 13px;
  background: rgba(255, 165, 0, 0.12); border: 1px solid rgba(255, 165, 0, 0.4); color: #ffb74d; }

.res-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.res-tabs span { font-size: 13px; font-weight: 700; color: var(--sub); padding: 6px 12px;
  border-radius: 9px; cursor: pointer; border: 1px solid transparent; transition: .15s; }
.res-tabs span:hover { color: var(--txt); }
.res-tabs span.active { color: var(--txt); background: var(--card-2); border-color: var(--pur); }
.prompt-used pre { white-space: pre-wrap; word-break: break-word; background: var(--card-2);
  border: 1px solid var(--line); border-radius: 10px; padding: 10px; font-size: 12px; color: var(--text); max-height: 180px; overflow: auto; }

/* ---- Ambient premium background ---- */
.page { position: relative; }
.page::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background:
    radial-gradient(ellipse 80% 50% at 15% 10%, rgba(139, 126, 216, .08), transparent 55%),
    radial-gradient(ellipse 60% 40% at 85% 90%, rgba(90, 150, 227, .06), transparent 50%),
    radial-gradient(ellipse 50% 30% at 50% 0%, rgba(201, 168, 108, .04), transparent 45%);
}
.page > * { position: relative; z-index: 1; }

/* ---- Page header & segmented tabs ---- */
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 22px;
  padding-bottom: 18px;
  border-bottom: 1px solid rgba(255,255,255,.06);
}
.ph-left { display: flex; flex-direction: column; gap: 10px; }
.page-subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--sub);
  line-height: 1.5;
  letter-spacing: .02em;
}
.segment-tabs {
  display: inline-flex;
  padding: 4px;
  gap: 4px;
  background: rgba(255,255,255,.04);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 12px;
  box-shadow: inset 0 1px 2px rgba(0,0,0,.15);
}
.segment-tabs button {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--sub);
  font-size: 14px;
  font-weight: 600;
  padding: 8px 18px;
  border-radius: 9px;
  cursor: pointer;
  transition: all .2s ease;
  letter-spacing: .02em;
}
.segment-tabs button:hover { color: var(--txt); }
.segment-tabs button.active {
  color: #fff;
  background: linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,255,255,.04));
  box-shadow: 0 2px 8px rgba(0,0,0,.25), inset 0 1px 0 rgba(255,255,255,.10);
  border: 1px solid rgba(255,255,255,.12);
}
.segment-tabs button.active:hover { color: #fff; }

.premium-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  color: #C9A86C;
  background: rgba(201, 168, 108, .08);
  border: 1px solid rgba(201, 168, 108, .22);
  box-shadow: 0 2px 8px rgba(0,0,0,.12);
  letter-spacing: .04em;
  flex-shrink: 0;
}
.premium-badge .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #C9A86C;
  box-shadow: 0 0 8px rgba(201, 168, 108, .55);
}

/* ---- Premium primary generate button ---- */
.generate-actions {
  display: flex;
  gap: 12px;
  margin-top: 18px;
}
.generate-btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  height: 48px;
  padding: 0 24px;
  border: none;
  border-radius: 12px;
  cursor: pointer;
  font-size: 15px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, #8B7ED8 0%, #6A82D8 50%, #5A96E3 100%);
  box-shadow:
    0 4px 14px rgba(90, 116, 227, .35),
    inset 0 1px 0 rgba(255,255,255,.18),
    0 0 0 1px rgba(255,255,255,.08) inset;
  transition: all .2s ease;
  letter-spacing: .02em;
  position: relative;
  overflow: hidden;
}
.generate-btn::before {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.14), transparent);
  transform: translateX(-100%);
  transition: transform .5s ease;
}
.generate-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow:
    0 6px 22px rgba(90, 116, 227, .45),
    inset 0 1px 0 rgba(255,255,255,.20),
    0 0 0 1px rgba(255,255,255,.10) inset;
}
.generate-btn:hover:not(:disabled)::before { transform: translateX(100%); }
.generate-btn:active:not(:disabled) { transform: translateY(0); }
.generate-btn:disabled {
  opacity: .72;
  cursor: not-allowed;
  filter: grayscale(.25);
}
.generate-btn .generate-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
}
.generate-btn .generate-label { position: relative; z-index: 1; }
.generate-btn .generate-hint {
  margin-left: 6px;
  padding: 2px 7px;
  border-radius: 5px;
  font-size: 11px;
  font-weight: 500;
  color: rgba(255,255,255,.75);
  background: rgba(0,0,0,.18);
  border: 1px solid rgba(255,255,255,.10);
}
.spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255,255,255,.25);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ---- Comic mode layout ---- */
.comic-wrap { padding-bottom: 24px; }
.comic-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 22px;
}
.comic-head-text { display: flex; flex-direction: column; gap: 6px; }
.comic-head h2 { margin: 0; font-size: 22px; font-weight: 700; color: var(--txt); letter-spacing: -.01em; }
.comic-grid { display: grid; grid-template-columns: 360px 1fr; gap: 20px; align-items: start; }
.comic-params,
.comic-result {
  background: rgba(255,255,255,.025);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 18px;
  padding: 22px;
  box-shadow: 0 8px 32px rgba(0,0,0,.18);
  backdrop-filter: blur(8px);
}
.comic-params .param-group + .param-group { margin-top: 22px; }
.comic-params .g-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--txt);
  margin-bottom: 12px;
  letter-spacing: .04em;
  text-transform: uppercase;
}
.comic-params .param-row { margin-bottom: 12px; }
.comic-params .param-row:last-child { margin-bottom: 0; }

.comic-refs { display: flex; flex-wrap: wrap; gap: 10px; }
.ref-thumb { position: relative; width: 56px; height: 56px; border-radius: 10px; overflow: hidden; border: 1px solid rgba(255,255,255,.10); }
.ref-thumb img { width: 100%; height: 100%; object-fit: cover; }
.ref-del { position: absolute; top: 2px; right: 2px; width: 18px; height: 18px; border-radius: 50%;
  border: none; background: rgba(0, 0, 0, .6); color: #fff; cursor: pointer; font-size: 12px; line-height: 1; }
.ref-add { width: 56px; height: 56px; border-radius: 10px; border: 1px dashed rgba(255,255,255,.18);
  display: flex; align-items: center; justify-content: center; cursor: pointer; color: var(--sub);
  font-size: 12px; font-weight: 600; background: rgba(255,255,255,.02); transition: .15s; }
.ref-add:hover { border-color: #8B7ED8; color: #8B7ED8; background: rgba(139,126,216,.06); }

.comic-style-ref { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; padding: 12px; border-radius: 12px; background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.06); }
.comic-style-ref .g-title { margin: 0; font-size: 12px; font-weight: 700; color: var(--sub); letter-spacing: .04em; text-transform: uppercase; }
.comic-style-ref img { width: 120px; height: 68px; object-fit: cover; border-radius: 10px;
  cursor: pointer; border: 1px solid rgba(255,255,255,.10); transition: .15s; }
.comic-style-ref img:hover { border-color: rgba(139,126,216,.5); }

.comic-panels { display: grid; gap: 16px; }
.comic-panels.cols-3 { grid-template-columns: repeat(3, 1fr); }
.comic-panels.cols-4 { grid-template-columns: repeat(2, 1fr); }
.comic-panels.cols-6 { grid-template-columns: repeat(3, 1fr); }
.comic-panels.cols-8 { grid-template-columns: repeat(4, 1fr); }
.comic-cell {
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 14px;
  overflow: hidden;
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
}
.comic-cell:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 28px rgba(0,0,0,.22);
  border-color: rgba(139,126,216,.28);
}
.comic-cell .ph { aspect-ratio: 16 / 9; background: rgba(0,0,0,.18); }
.comic-cell .cap { padding: 10px 12px; font-size: 13px; color: var(--txt); line-height: 1.5; font-weight: 500; }

/* Result tabs */
.res-tabs { display: flex; gap: 6px; margin-bottom: 16px; padding: 4px; border-radius: 10px; background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.06); }
.res-tabs span { font-size: 13px; font-weight: 600; color: var(--sub); padding: 7px 14px;
  border-radius: 7px; cursor: pointer; border: 1px solid transparent; transition: .15s; }
.res-tabs span:hover { color: var(--txt); }
.res-tabs span.active { color: #fff; background: rgba(139,126,216,.18); border-color: rgba(139,126,216,.35); }

.preview-gen-info {
  margin-top: 14px;
  padding: 14px;
  background: rgba(255,255,255,.04);
  border-radius: 12px;
  border: 1px solid rgba(255,255,255,.08);
  max-height: 42vh;
  overflow-y: auto;
}
.preview-gen-info .info-section { margin-bottom: 14px; }
.preview-gen-info .info-section:last-child { margin-bottom: 0; }
.preview-gen-info .info-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--sub);
  letter-spacing: .04em;
  text-transform: uppercase;
  margin-bottom: 6px;
}
.preview-gen-info .info-body {
  font-size: 13px;
  color: var(--txt);
  line-height: 1.6;
}
.preview-gen-info .info-body.pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: rgba(0,0,0,.18);
  border-radius: 8px;
  padding: 10px;
  font-size: 12px;
  color: var(--sub);
  max-height: 120px;
  overflow: auto;
}
.preview-gen-info .preview-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.preview-gen-info .param-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px;
}
.preview-gen-info .param-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  background: rgba(0,0,0,.18);
  border-radius: 8px;
}
.preview-gen-info .param-key {
  font-size: 11px;
  color: var(--sub);
}
.preview-gen-info .param-val {
  font-size: 13px;
  color: var(--txt);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.hist-pagination {
  display: flex;
  justify-content: center;
  margin-top: 18px;
}

/* Premium overrides for result cards */
.asset-grid { margin-top: 14px; }
.asset-card {
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 14px;
  overflow: hidden;
  cursor: pointer;
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
  position: relative;
}
.asset-card:hover {
  border-color: rgba(139,126,216,.28);
  transform: translateY(-2px);
  box-shadow: 0 10px 28px rgba(0,0,0,.22);
}
.asset-card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: rgba(0,0,0,.18); }
.asset-card .info { padding: 12px 14px; }
.asset-card .name { font-weight: 700; font-size: 13px; color: var(--txt); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.asset-card .type { font-size: 11px; color: var(--sub); margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.empty {
  padding: 36px 24px;
  text-align: center;
  color: var(--sub);
  font-size: 13px;
  line-height: 1.6;
  background: rgba(255,255,255,.02);
  border: 1px dashed rgba(255,255,255,.10);
  border-radius: 14px;
}

/* Scoped premium button overrides */
:deep(.btn.sm.ghost),
.btn.sm.ghost {
  background: rgba(255,255,255,.04);
  border: 1px solid rgba(255,255,255,.10);
  color: var(--txt);
  border-radius: 8px;
  transition: all .15s ease;
}
:deep(.btn.sm.ghost:hover:not(:disabled)),
.btn.sm.ghost:hover:not(:disabled) {
  background: rgba(255,255,255,.08);
  border-color: rgba(139,126,216,.35);
  color: #fff;
}
.cancel-btn {
  border-color: rgba(239, 83, 80, .45) !important;
  color: #ef9a9a !important;
  background: rgba(239, 83, 80, .08) !important;
}
.cancel-btn:hover:not(:disabled) {
  background: rgba(239, 83, 80, .18) !important;
  border-color: rgba(239, 83, 80, .55) !important;
  color: #fff !important;
}

/* Responsive */
@media (max-width: 1100px) {
  .comic-grid { grid-template-columns: 1fr; }
  .ws-grid { grid-template-columns: 1fr; }
}
</style>
