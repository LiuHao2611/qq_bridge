<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, onActivated, onDeactivated } from 'vue'
import { ElMessage } from 'element-plus'
import { usePersistedState } from '../composables/usePersist'
import { useServiceStatus } from '../composables/useServiceStatus'
import { t2iGenerate, t2iTask, t2iCancel, t2iHistory, difyConfig, updateSettings, difyStart, difyStop } from '../api.js'

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
      } else if (t.status === 'failed') {
        finishPoll()
        console.error('[Txt2Img] task failed', JSON.parse(JSON.stringify(t)))
        progressMsg.value = '失败：' + (t.error_message || '未知错误')
        ElMessage.error('生成失败：' + (t.error_message || '未知错误'))
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
function openPreview(itemOrItems, startIndex = 0) {
  const list = Array.isArray(itemOrItems) ? itemOrItems : [itemOrItems]
  previewImages.value = list
  previewIndex.value = Math.max(0, Math.min(startIndex, list.length - 1))
  previewVisible.value = true
}
const previewItem = computed(() => {
  const list = previewImages.value
  return list.length ? list[previewIndex.value] : null
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

// 初始加载（服务状态由 useServiceStatus 自动在 activated 时拉取）
loadDifyConfig()
loadHistory()
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>🎨 文生图</h1>
        <p class="muted">输入文字描述，调用本地 ComfyUI 真实出图（Ollama 润色提示词 · Dify 可选编排）</p>
      </div>
      <el-tag type="success" effect="dark">本地生成 · 不计积分</el-tag>
    </div>

    <!-- 服务绑定状态条：Docker · Dify · Ollama · ComfyUI · 模型（Docker/Dify 可启停） -->
    <div class="svc-bar">
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

    <div class="ws-grid">
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

        <div style="display:flex;gap:10px;margin-top:14px">
          <button class="btn pink" style="flex:1;justify-content:center"
            :disabled="generating" @click="generate">
            {{ generating ? '生成中…' : '开始生成' }}
          </button>
          <button v-if="generating" class="btn sm ghost cancel-btn"
            :disabled="cancelling" @click="cancelGenerate"
            style="min-width:100px;justify-content:center">
            {{ cancelling ? '取消中…' : '⏸ 暂停' }}
          </button>
        </div>

        <div class="progress" v-if="generating || task?.status === 'failed' || task?.status === 'cancelled'">
          <div class="lab">
            <span>{{ progressMsg || statusText }}</span>
            <span>{{ doneCount }}/{{ totalCount }}</span>
          </div>
          <el-progress :percentage="totalCount ? Math.round(doneCount / totalCount * 100) : (task?.status==='succeeded'?100:0)"
            :stroke-width="8" :color="['#ff4d8d', '#8a5cff']"
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
            <div class="asset-card" v-for="(item, i) in results" :key="i" @click="openPreview(item)">
              <div class="ph">
                <img :src="item.url" alt="生成图" loading="lazy" @load="imgLoadLog(item, true, $event)" @error="imgLoadLog(item, false, $event)" />
                <div class="ph-acts" @click.stop>
                  <button class="btn sm ghost" @click.stop="download(item)">下载</button>
                  <button class="btn sm ghost" @click.stop="openPreview(item)">放大</button>
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
            <div class="asset-card" v-for="(h, i) in histories" :key="h.task_id" @click="openPreview(h.images, 0)">
              <div class="ph">
                <img :src="h.images[0].url" alt="历史图" loading="lazy" @error="imgLoadLog(h.images[0], false, $event)" />
                <div v-if="h.images.length > 1" class="img-count">{{ h.images.length }} 张</div>
                <div class="ph-acts" @click.stop>
                  <button class="btn sm ghost" @click.stop="useHistoryPrompt(h)">复用提示词</button>
                  <button class="btn sm ghost" @click.stop="openPreview(h.images, 0)">放大</button>
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
      <div v-if="previewImages.length > 1" class="preview-thumbs">
        <img v-for="(it, idx) in previewImages" :key="idx"
          :src="it.url" :class="{ active: idx === previewIndex }"
          @click="previewIndex = idx" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.svc-bar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 16px; }
.svc { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 10px;
  border: 1px solid var(--line); background: var(--card); font-size: 13px; font-weight: 600; }
.svc .dot { width: 8px; height: 8px; border-radius: 50%; background: #888; }
.svc.ok { border-color: #2ecc71; color: #2ecc71; }
.svc.ok .dot { background: #2ecc71; box-shadow: 0 0 6px #2ecc71; }
.svc.bad { border-color: #e74c3c; color: #e74c3c; }
.svc.bad .dot { background: #e74c3c; }
.svc.warn { border-color: #f5c542; color: #f5c542; }
.svc.warn .dot { background: #f5c542; box-shadow: 0 0 6px #f5c542; }
.svc small { color: var(--muted); font-weight: 400; }
.svc-refresh { margin-left: auto; }
.cancel-btn { border-color: #e74c3c !important; color: #e74c3c !important; }
.cancel-btn:hover:not(:disabled) { background: #e74c3c !important; color: #fff !important; }
.mini-btn { margin-left: 8px; padding: 2px 10px; border-radius: 8px; border: 1px solid var(--pur);
  background: var(--pur); color: #fff; font-size: 12px; font-weight: 700; cursor: pointer; transition: .15s; }
.mini-btn:hover:not(:disabled) { filter: brightness(1.1); }
.mini-btn:disabled { opacity: .5; cursor: not-allowed; }
.mini-btn.ghost { background: transparent; color: var(--sub); border-color: var(--line); }
.mini-btn.ghost:hover:not(:disabled) { color: var(--txt); border-color: var(--sub); }

.ws-grid { display: grid; grid-template-columns: 380px 1fr; gap: 18px; align-items: start; }
.panel { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }

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
</style>
