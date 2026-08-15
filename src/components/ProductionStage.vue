<script setup>
// ============================================================
// Phase 4 生产工作台（KB §5.6 模块五 AI视频生成 + §18 WebSocket + §06 Webhook）
// 语义：将分镜「分发」到 AI 推理引擎并异步生成视频；实时监看任务进度/日志/结果。
// 不是「任务中心」式的全局浏览页 —— 任务在此是生产流程的跨切面关注点，内嵌于编辑器。
// 实时：优先 WebSocket（task:{id} 频道）；WS 不可用时回退轮询（REDUN-01）。
// ============================================================
defineOptions({ name: 'ProductionPhase' })
import { ref, reactive, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getProduction, inferenceHealth, estimatePoints, preAuthPoints, getTask,
  generateSegment, regenerateSegment, cancelTask, retryTask, pauseTask, resumeTask, synthesizeProject,
  directorRun, directorRunStatus,
} from '../api'
import { subscribeTask, subscribe, wsConnected, connectWs } from '../ws'
import { toast } from '../store'
import { fmtTime } from '../api'
import { pushGeneration } from '../composables/useDirectorOrb'
import { pushHistory, genKey } from '../composables/useGenHistory'
import { useVideoGate } from '../composables/useVideoGate'
import ProjectBookShell from './ProjectBookShell.vue'
import VideoLogPanel from './VideoLogPanel.vue'

const route = useRoute()
const router = useRouter()
const pid = route.params.id

// 视频生成前置闸门：本机显存不足时诚实提示，避免白等 30 分钟
const { ensureVideoReady, peekReadiness } = useVideoGate()
const videoReady = ref(null)  // 被动展示：{ feasible, reason, device } 或 null（探测失败）

const project = ref({})
const segments = ref([])
const health = ref({ comfyui: {}, ollama: {}, queue_depth: 0, ws_connections: 0, mode: 'unknown', async_callback: '' })
const loading = ref(false)
const expandedId = ref('')
const synthTaskId = ref('')
const synthStatus = ref('')

// Director Agent Core 一站式生成（N01→N09）
const NODE_TXT = { N01: '接收任务', N02: '剧本解析', N03: '视觉设定', N04: '任务分发', N05: '分镜生成', N06: '质量检查', N07: '音频合成', N08: '视频合成', N09: '成片输出' }
const directorRunId = ref('')
const directorRunning = ref(false)
const directorProgress = ref(0)
const directorNode = ref('')
const directorStage = ref('')
const directorOutput = ref('')
const directorError = ref('')
const _directorSubs = { unsub: null, timer: null }
// per-segment 跟踪（full 版 ②：Director 入队后由 worker 消费，经 WS director:<pid> 透出）
const directorSegments = ref({})  // seg_id -> {status, progress, video_url, task_id}

// Director 参数配置（手册 §3.2/§3.3/§3.4 输入）：经 directorRun 透传给各子 Agent
const directorParams = reactive({
  resolution: '720p',
  aspect_ratio: '16:9',
  model_id: '',
  seed_strategy: 'inherit',        // fixed | random | inherit（§3.2.5 跨镜继承+微扰）
  enable_real_asset: true,         // N03 真人资产 512 维特征注入（§3.2.2 / §5.1）
  enable_tail_frame: true,         // N04 首尾帧锚定（§3.3.5 默认开启）
  enable_tail_depth: true,         // N04 首尾帧深度复用（§3.3.4，缺 depth ControlNet 诚实降级）
  priority_level: 0,               // N04 算力队列优先级（§3.3.2）
  enable_quality_check: true,      // N06 自动化品控（§3.4.2）
  face_restore: 'GFPGAN',          // N08 人脸修复：'' | 'GFPGAN' | 'CodeFormer'（§3.4.4）
  tts: true, bgm: false, subtitle: true,
  transitions: true, post_process: true,
  target_fps: 25, target_resolution: '',
  // §3.4.4 多音轨分层音量控制（人声/背景乐/音效分别增益，0~2，默认 1.0）
  voice_volume: 1.0, bgm_volume: 0.6, sfx_volume: 1.0,
  bgm_path: '', sfx_path: '',
})
const showDirectorParams = ref(false)
// Director 运行结果快照（N03 风格参考图 / N06 品控报告，手册 §3.2.2 输出 / §3.4.5）
const directorResult = ref(null)   // { global_style_ref, quality_report, quality_alerts, failed_shot_list }

function onDirectorEvent(event, data) {
  if (event === 'progress') {
    directorNode.value = data.node || ''
    directorStage.value = data.status || ''
    if (data.progress != null) directorProgress.value = Math.round(data.progress * 100)
  } else if (event === 'segment') {
    // Director 入队 → worker 消费 的逐片段状态（REDUN-01：WS 透出，轮询兜底见下）
    const sid = data.segment_id
    if (sid) {
      const prev = directorSegments.value[sid] || {}
      directorSegments.value = {
        ...directorSegments.value,
        [sid]: {
          task_id: data.task_id || prev.task_id || null,
          status: data.status || prev.status || '',
          progress: data.progress != null ? Math.round(data.progress) : (prev.progress || 0),
          video_url: data.video_url || prev.video_url || ''
        }
      }
    }
  } else if (event === 'completed') {
    directorRunning.value = false
    directorOutput.value = data.final_output || ''
    directorProgress.value = 100
    toast('导演生成完成 🎬')
    stopDirectorWatch()
    loadDirectorResult(directorRunId.value)   // 拉取快照：N03 风格参考图 + N06 品控报告
    loadProd()
  } else if (event === 'error') {
    directorRunning.value = false
    directorError.value = data.error || '未知错误'
    toast('导演生成失败: ' + directorError.value)
    stopDirectorWatch()
  }
}

function startDirectorWatch(runId) {
  const channel = 'director:' + pid
  if (wsConnected()) {
    _directorSubs.unsub = subscribe(channel, onDirectorEvent)
  } else {
    // REDUN-01 轮询兜底：WS 不可用时轮询运行态
    _directorSubs.timer = setInterval(async () => {
      try {
        const r = await directorRunStatus(pid, runId)
        if (r.status !== 'running') {
          if (r.status === 'done') { directorRunning.value = false; directorOutput.value = r.final_output || ''; directorProgress.value = 100; directorResult.value = r.snapshot || null; toast('导演生成完成 🎬') }
          else { directorRunning.value = false; directorError.value = r.error || (r.snapshot && r.snapshot.errors && JSON.stringify(r.snapshot.errors)) || '失败'; toast('导演生成失败') }
          stopDirectorWatch(); loadProd()
        } else if (r.snapshot && r.snapshot.node_status) {
          const ns = r.snapshot.node_status
          const last = Object.keys(ns).reverse().find((k) => ns[k] === 'running' || ns[k] === 'done')
          if (last) { directorNode.value = last; directorStage.value = ns[last] }
          const doneN = Object.values(ns).filter((v) => v === 'done').length
          directorProgress.value = Math.round((doneN / 9) * 100)
        }
      } catch { /* ignore */ }
    }, 3000)
  }
}

function stopDirectorWatch() {
  if (_directorSubs.unsub) { try { _directorSubs.unsub() } catch { /* noop */ }; _directorSubs.unsub = null }
  if (_directorSubs.timer) { clearInterval(_directorSubs.timer); _directorSubs.timer = null }
}

async function onDirectorRun() {
  if (directorRunning.value) { toast('导演生成进行中，请稍候'); return }
  directorRunning.value = true
  directorProgress.value = 0
  directorNode.value = ''
  directorStage.value = ''
  directorOutput.value = ''
  directorError.value = ''
  directorSegments.value = {}
  directorResult.value = null
  try {
    const r = await directorRun(pid, { ...directorParams })
    directorRunId.value = r.run_id
    toast('已启动导演一站式生成')
    startDirectorWatch(r.run_id)
  } catch (e) {
    directorRunning.value = false
    toast('启动失败: ' + (e.message || e))
  }
}

// 拉取一次导演运行快照：N03 风格参考图 + N06 品控报告（手册 §3.2.2/§3.4.5，Layer 3 状态推送）
async function loadDirectorResult(runId) {
  if (!runId) return
  try {
    const r = await directorRunStatus(pid, runId)
    directorResult.value = r.snapshot || null
    if (r.final_output) directorOutput.value = r.final_output
  } catch { /* ignore */ }
}

const STATUS_TXT = { queued: '排队中', running: '生成中', retrying: '重试中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
const SEG_STATUS_TXT = { draft: '草稿', saved: '已保存', generating: '生成中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }

// N06 品控报告（逐片段 face_sim/duration/file/pass）+ N03 风格参考图（§3.4.5 / §3.2.2 输出）
const qrReports = computed(() =>
  (directorResult.value && directorResult.value.quality_report && directorResult.value.quality_report.reports) || [])
const qrFailed = computed(() => (directorResult.value && directorResult.value.failed_shot_list) || [])
const qrAlerts = computed(() => (directorResult.value && directorResult.value.quality_alerts) || [])
const styleRefName = computed(() => {
  const p = directorResult.value && directorResult.value.global_style_ref
  return p ? p.split(/[\\/]/).pop() : ''
})
const FACE_SIM_THRESHOLD = 0.8   // 手册 §3.4.5

const mapDisp = (s) => {
  if (!s) return { k: 'queued', t: '排队中' }
  if (s === 'succeeded') return { k: 'succeeded', t: '已完成' }
  if (s === 'running' || s === 'retrying' || s === 'generating') return { k: 'running', t: '生成中' }
  if (s === 'failed') return { k: 'failed', t: '失败' }
  if (s === 'cancelled') return { k: 'cancelled', t: '已取消' }
  if (s === 'queued') return { k: 'queued', t: '排队中' }
  return { k: s, t: STATUS_TXT[s] || s }
}

// 每个运行中任务：WS 取消订阅函数 + 轮询兜底定时器
const subs = new Map()
const polls = new Map()
const logsByTask = reactive({})

function ensureWatch(seg) {
  const task = seg.latest_task
  if (!task || !task.task_id) return
  const tid = task.task_id
  const disp = mapDisp(task.status)
  // 终态无需监听
  if (disp.k === 'succeeded' || disp.k === 'failed' || disp.k === 'cancelled') return
  // 已监听则跳过
  if (subs.has(tid) || polls.has(tid)) return
  const onEvent = (event, data) => {
    if (event === 'status_update' || event === 'completed' || event === 'failed') {
      if (data.status) task.status = data.status
      if (data.progress != null) task.progress = data.progress
      if (data.result_url) { task.result_url = data.result_url; seg.video_url = data.result_url }
      if (data.error_message) task.error_message = data.error_message
    } else if (event === 'progress') {
      if (data.progress != null) task.progress = data.progress
    } else if (event === 'log') {
      pushLog(tid, data)
    }
    if (event === 'completed' || event === 'failed') {
      // 诚实判定：收到 completed 但无产物链接 → 不能谎报成功（手册铁律：全链路可观测/不静默降级）
      if (event === 'completed' && !data.result_url) {
        task.status = 'failed'
        task.error_message = data.error_message || '视频产物缺失（result_url 为空）'
        pushGeneration('video', '视频生成·' + (seg.title || seg.segment_id), 'failed', task.error_message)
      } else {
        task.status = event === 'completed' ? 'succeeded' : 'failed'
        pushGeneration('video', '视频生成·' + (seg.title || seg.segment_id), event === 'completed' ? 'done' : 'failed', event === 'failed' ? (task.error_message || '生成失败') : undefined)
      }
      if (event === 'completed' && seg.video_url) {
        try {
          pushHistory(genKey.prod(pid), {
            kind: 'video',
            title: (seg.title || seg.segment_id || '片段') + ' · 视频',
            preview: seg.thumb || seg.cover_url || '',
            previewText: '视频',
            payload: { video_url: seg.video_url, task_id: tid, title: seg.title || seg.segment_id },
          })
        } catch { /* 历史写入失败不阻塞生成主流程 */ }
      }
      stopWatch(tid)
      refreshHealth()
    }
  }
  // WebSocket 优先；不可用时启动轮询兜底（REDUN-01）
  if (wsConnected()) {
    subs.set(tid, subscribeTask(tid, onEvent))
  } else {
    startPoll(tid, seg, onEvent)
  }
}

function startPoll(tid, seg, onEvent) {
  const timer = setInterval(async () => {
    try {
      const t = await getTask(tid)
      onEvent('status_update', { status: t.status, progress: t.progress, result_url: t.result_url, error_message: t.error_message })
      if (wsConnected()) { // WS 恢复后切回 WS
        clearInterval(timer); polls.delete(tid)
        subs.set(tid, subscribeTask(tid, onEvent))
      }
    } catch (e) { /* ignore */ }
  }, 3000)
  polls.set(tid, timer)
}

function stopWatch(tid) {
  if (subs.has(tid)) { try { subs.get(tid)() } catch {} subs.delete(tid) }
  if (polls.has(tid)) { clearInterval(polls.get(tid)); polls.delete(tid) }
}

function pushLog(tid, data) {
  if (!logsByTask[tid]) logsByTask[tid] = []
  logsByTask[tid].push({ level: data.level || 'info', message: data.message || '', ts: Date.now() })
  if (logsByTask[tid].length > 200) logsByTask[tid].splice(0, logsByTask[tid].length - 200)
}

function toggleExpand(seg) {
  const tid = seg.latest_task?.task_id
  if (!tid) { toast('该片段尚无生成任务'); return }
  expandedId.value = expandedId.value === tid ? '' : tid
  if (expandedId.value && !logsByTask[tid]) logsByTask[tid] = []
}

async function loadProd() {
  loading.value = true
  try {
    const r = await getProduction(pid)
    project.value = r.project || {}
    segments.value = (r.segments || []).map((s) => ({
      ...s,
      // 归一化 latest_task，便于模板渲染
      latest_task: s.latest_task ? { ...s.latest_task } : null,
    }))
    // 为每个进行中的任务挂上实时监听
    segments.value.forEach((s) => ensureWatch(s))
  } catch (e) {
    toast('加载生产视图失败: ' + (e.message || e))
  } finally {
    loading.value = false
  }
}

async function refreshHealth() {
  try { health.value = await inferenceHealth() } catch { /* ignore */ }
}

function buildParams(p) {
  return {
    duration: p.duration || 12,
    resolution: p.resolution || '720p',
    ratio: p.ratio || '16:9',
    audio: p.audio ? 1 : 0,
    model_id: p.ai_model || 'seedance-2.0-mini',
    asset_humanize: p.real_person ? 1 : 0,
    return_last_frame: p.return_tail ? 1 : 0,
    seed: p.seed || 0,
  }
}

async function dispatchSegment(seg) {
  const tid = seg.latest_task?.task_id
  const hasPrior = !!tid && !['succeeded', 'failed', 'cancelled'].includes(mapDisp(seg.latest_task.status).k)
  // 既有运行中任务则忽略，避免重复分发
  if (hasPrior) { toast('该片段已有进行中的任务'); return }
  // 视频生成前置闸门：本机显存不足时诚实提示，用户取消即停止分发（避免白等 30 分钟）
  if (!(await ensureVideoReady())) {
    toast('已取消视频分发：当前设备不满足视频生成条件')
    return
  }
  const params = buildParams(project.value)
  const op = (seg.latest_task && ['succeeded', 'failed', 'cancelled'].includes(mapDisp(seg.latest_task.status).k)) ? 'regenerate' : 'generate'
  try {
    const est = await estimatePoints({ operation: 'estimate', ...params })
    const idem = 'idem_p4_' + seg.segment_id + '_' + Date.now() + '_' + Math.random().toString(36).slice(2)
    const pre = await preAuthPoints(est.estimated_credits, op === 'regenerate' ? 'segment_regenerate' : 'segment_generate', idem)
    const res = op === 'regenerate'
      ? await regenerateSegment(pid, seg.segment_id, pre.pre_auth_id, params)
      : await generateSegment(pid, seg.segment_id, pre.pre_auth_id, params)
    // 分发立即返回 task_id（异步推理，dispatch 不阻塞）
    seg.latest_task = { task_id: res.task_id, status: res.status || 'queued', progress: 0, result_url: '', error_message: '' }
    toast('已分发给推理引擎：' + (seg.title || seg.segment_id))
    pushGeneration('video', '视频生成·' + (seg.title || seg.segment_id), 'running')
    ensureWatch(seg)
    refreshHealth()
  } catch (e) {
    toast('分发失败: ' + (e.message || e))
  }
}

async function dispatchAll() {
  const pending = segments.value.filter((s) => {
    const k = s.latest_task ? mapDisp(s.latest_task.status).k : 'none'
    return k === 'none' || k === 'failed' || k === 'cancelled'
  })
  if (!pending.length) { toast('没有待分发的片段'); return }
  for (const s of pending) await dispatchSegment(s)
}

async function cancel(seg) {
  const tid = seg.latest_task?.task_id
  if (!tid) return
  try { await cancelTask(tid); toast('已取消分发任务'); seg.latest_task.status = 'cancelled'; stopWatch(tid) } catch (e) { toast('取消失败: ' + (e.message || e)) }
}

async function retry(seg) {
  const tid = seg.latest_task?.task_id
  if (!tid) return
  try { await retryTask(tid); toast('已重试'); seg.latest_task.status = 'queued'; seg.latest_task.progress = 0; ensureWatch(seg) } catch (e) { toast('重试失败: ' + (e.message || e)) }
}

// 断点续跑：导演流水线已内置 resume（scheduler 默认跳过已成功片段），再次发起即从上次断点继续。
// 适用于中途失败/取消后，仅补齐未完成的片段与合成，不重复已生成内容。
async function resumeDirector() {
  if (directorRunning.value) { toast('导演生成进行中，请稍候'); return }
  toast('已从断点续跑（跳过已成功片段）')
  await onDirectorRun()
}

// 导演一站式生成的逐片段任务控制（复用后端 /tasks/:id/{pause,resume,cancel}，worker 级断点控制）
async function pauseSeg(s) {
  const tid = s.task_id
  if (!tid) return
  try { await pauseTask(tid); toast('已暂停片段任务'); s.status = 'paused' } catch (e) { toast('暂停失败: ' + (e.message || e)) }
}
async function resumeSeg(s) {
  const tid = s.task_id
  if (!tid) return
  try { await resumeTask(tid); toast('已恢复片段任务'); if (s.status === 'paused') s.status = 'running' } catch (e) { toast('恢复失败: ' + (e.message || e)) }
}
async function cancelSeg(s) {
  const tid = s.task_id
  if (!tid) return
  try { await cancelTask(tid); toast('已取消片段任务'); s.status = 'cancelled' } catch (e) { toast('取消失败: ' + (e.message || e)) }
}

async function onSynthesize() {
  const segs = segments.value
  // 守卫1：空项目不能合成（与 dispatchSegment 一致）
  if (!segs.length) { toast('当前项目还没有分镜，无法合成全集'); return }
  // 守卫2：视频生成前置算力闸门（诚实提示，取消即停止，避免白等 30 分钟）
  if (!(await ensureVideoReady())) { toast('已取消：当前设备不满足视频生成条件'); return }
  const totalDur = segs.reduce((a, c) => a + (Number(c.duration) || 0), 0)
  const params = buildParams(project.value)
  try {
    const est = await estimatePoints({ operation: 'estimate', duration: totalDur, resolution: project.value.resolution, audio: true })
    const idem = 'idem_syn_' + Date.now() + '_' + Math.random().toString(36).slice(2)
    const pre = await preAuthPoints(est.estimated_credits, 'synthesize', idem)
    const res = await synthesizeProject(pid, pre.pre_auth_id, {})
    synthTaskId.value = res.task_id
    synthStatus.value = 'queued'
    toast('已提交「合成全集」任务')
    // 合成任务同样走实时通道（task:{id}）
    const onEvent = (event, data) => {
      if (event === 'status_update' || event === 'completed' || event === 'failed') {
        if (data.status) synthStatus.value = data.status
        if (event === 'completed') {
          // 合成完成但无产物链接 → 不能谎报成功（与 dispatchSegment 同源判定）
          if (!data.result_url) { synthStatus.value = 'failed'; toast.error('合成失败：产物缺失（result_url 为空）') }
          else { synthStatus.value = 'succeeded'; toast('合成全集完成'); loadProd() }
        }
        if (event === 'failed') { synthStatus.value = 'failed'; toast('合成失败: ' + (data.error_message || '未知')) }
      }
    }
    if (wsConnected()) subscribeTask(res.task_id, onEvent)
    else {
      const timer = setInterval(async () => {
        try {
          const t = await getTask(res.task_id)
          onEvent('status_update', { status: t.status })
          if (wsConnected()) { clearInterval(timer); subscribeTask(res.task_id, onEvent) }
        } catch {}
      }, 3000)
      polls.set(res.task_id, timer)
    }
  } catch (e) { toast('合成失败: ' + (e.message || e)) }
}

// 生产阶段历史面板「覆盖使用」：视频生成结果无法直接回填到某片段，故复制视频链接到剪贴板供粘贴使用
function onUseProdHist(it) {
  const url = it && it.payload && it.payload.video_url
  if (!url) { toast('该记录无可用视频链接'); return }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url)
  } catch { /* 剪贴板不可用时忽略 */ }
  toast('已复制视频链接：' + (it.title || '视频'))
}

const engineChips = computed(() => [
  { name: 'ComfyUI', role: '视频/图像推理', ok: !!health.value.comfyui?.reachable },
  { name: 'Ollama', role: 'LLM 策划/润色', ok: !!health.value.ollama?.reachable },
])

let healthTimer = null
onMounted(() => {
  connectWs()
  loadProd()
  refreshHealth()
  healthTimer = setInterval(refreshHealth, 10000)
  peekReadiness().then((r) => { videoReady.value = r }).catch(() => { videoReady.value = null })
})
onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer)
  subs.forEach((fn) => { try { fn() } catch {} })
  polls.forEach((t) => clearInterval(t))
  subs.clear(); polls.clear()
  stopDirectorWatch()
})
</script>

<template>
  <ProjectBookShell class="page prod" :step="4" @synthesize="onSynthesize">
    <!-- 左侧：历史生成记录（页面级，固定在内容区左侧，默认折叠，点击展开可选择覆盖使用） -->
    <HistoryPanel
      ref="prodHist"
      :storage-key="genKey.prod(pid)"
      title="生成历史"
      kind-filter="video"
      :floating="true"
      @use="onUseProdHist"
    />

    <div class="page-head p5-tear">
      <div>
        <h1 class="p5-title">生成生产</h1>
        <p class="muted">第四阶段 · 任务分发与异步 AI 推理（生产阶段）。将分镜分发给推理引擎，实时监看生成进度与结果。</p>
      </div>
      <div class="ops">
        <el-button type="success" @click="onDirectorRun" :loading="directorRunning">🎬 导演一站式生成</el-button>
        <el-button v-if="directorRunId || directorOutput" :disabled="directorRunning" @click="resumeDirector">↻ 断点续跑</el-button>
        <el-button text @click="showDirectorParams = !showDirectorParams">⚙ 参数</el-button>
        <el-button type="primary" @click="dispatchAll">一键分发全部</el-button>
        <el-button @click="onSynthesize">合成全集</el-button>
        <el-button text @click="loadProd">刷新</el-button>
      </div>
    </div>

    <!-- 推理引擎状态（§06 / §18 概览） -->
    <div class="engine-bar">
      <div class="chip" :class="c.ok ? 'ok' : 'bad'" v-for="c in engineChips" :key="c.name">
        <span class="dot" /> {{ c.name }} · {{ c.ok ? '在线' : '离线' }}
        <span class="role">{{ c.role }}</span>
      </div>
      <div class="chip">
        队列深度 <b>{{ health.queue_depth }}</b>
      </div>
      <div class="chip">
        WS 连接 <b>{{ health.ws_connections }}</b>
        <span class="role">{{ wsConnected() ? '实时' : '轮询兜底' }}</span>
      </div>
      <!-- 视频生成算力前置闸门（被动展示）：本机显存不足时红色警示，分发前即知 -->
      <div class="chip" v-if="videoReady === null">
        视频算力 <b>探测中…</b>
      </div>
      <div class="chip" v-else-if="videoReady.feasible" :class="'ok'">
        <span class="dot" /> 视频算力 · 充足
        <span class="role" v-if="videoReady.device && videoReady.device.gpu_name">{{ videoReady.device.gpu_name }} · {{ videoReady.device.vram_total_gb }}GB</span>
      </div>
      <div class="chip" v-else :class="'bad'">
        <span class="dot" /> 视频算力 · 不足
        <span class="role" v-if="videoReady.device && videoReady.device.gpu_name">{{ videoReady.device.gpu_name }} · {{ videoReady.device.vram_total_gb }}GB</span>
      </div>
    </div>

    <!-- Director 参数配置面板（手册 §3.2/§3.3/§3.4 输入） -->
    <el-collapse-transition>
      <div v-if="showDirectorParams" class="dir-params">
        <div class="dp-head">导演生成参数 <span class="muted">（下发至 N03 视觉统筹 / N04 时序 / N06 品控 / N08 合成）</span></div>
        <div class="dp-grid">
          <label>分辨率
            <select v-model="directorParams.resolution">
              <option>480p</option><option>720p</option><option>1080p</option>
            </select>
          </label>
          <label>画幅比例
            <select v-model="directorParams.aspect_ratio">
              <option>16:9</option><option>9:16</option><option>1:1</option><option>4:3</option>
            </select>
          </label>
          <label>seed 策略
            <select v-model="directorParams.seed_strategy">
              <option value="fixed">fixed 固定</option>
              <option value="random">random 随机</option>
              <option value="inherit">inherit 跨镜继承+微扰</option>
            </select>
          </label>
          <label>首尾帧锚定(N04)
            <input type="checkbox" v-model="directorParams.enable_tail_frame" />
          </label>
          <label>首尾帧深度复用(N04)
            <input type="checkbox" v-model="directorParams.enable_tail_depth" />
          </label>
          <label>队列优先级
            <input type="number" v-model.number="directorParams.priority_level" min="-10" max="10" />
          </label>
          <label>真人资产 512维(N03)
            <input type="checkbox" v-model="directorParams.enable_real_asset" />
          </label>
          <label>品控校验(N06)
            <input type="checkbox" v-model="directorParams.enable_quality_check" />
          </label>
          <label>人脸修复(N08)
            <select v-model="directorParams.face_restore">
              <option value="">关闭</option>
              <option value="GFPGAN">GFPGAN</option>
              <option value="CodeFormer">CodeFormer</option>
            </select>
          </label>
          <label>TTS 配音
            <input type="checkbox" v-model="directorParams.tts" />
          </label>
          <label>字幕
            <input type="checkbox" v-model="directorParams.subtitle" />
          </label>
          <label>转场
            <input type="checkbox" v-model="directorParams.transitions" />
          </label>
          <label>全局后处理
            <input type="checkbox" v-model="directorParams.post_process" />
          </label>
          <label>背景音乐(BGM)
            <input type="checkbox" v-model="directorParams.bgm" />
          </label>
          <label class="dp-wide">背景乐路径
            <input type="text" v-model="directorParams.bgm_path"
                   placeholder="本地音乐文件绝对路径，如 D:\AI\bgm.mp3（开启 BGM 后生效）" />
          </label>
          <label class="dp-slider">人声音量（§3.4.4）
            <el-slider v-model="directorParams.voice_volume" :min="0" :max="2" :step="0.05" show-input />
          </label>
          <label class="dp-slider">背景乐音量（§3.4.4）
            <el-slider v-model="directorParams.bgm_volume" :min="0" :max="2" :step="0.05" show-input />
          </label>
          <label class="dp-slider">音效音量（§3.4.4）
            <el-slider v-model="directorParams.sfx_volume" :min="0" :max="2" :step="0.05" show-input />
          </label>
          <label class="dp-wide">音效路径(SFX，可选)
            <input type="text" v-model="directorParams.sfx_path"
                   placeholder="可选：本地音效文件绝对路径，如 D:\AI\sfx.wav（留空则不叠加音效轨）" />
          </label>
        </div>
      </div>
    </el-collapse-transition>

    <!-- 合成全集进度横幅 -->
    <div v-if="synthTaskId" class="synth-banner" :class="synthStatus">
      <span>合成全集任务</span>
      <code>{{ synthTaskId.slice(0, 12) }}</code>
      <el-tag size="small" :type="synthStatus === 'succeeded' ? 'success' : (synthStatus === 'failed' ? 'danger' : 'warning')">
        {{ mapDisp(synthStatus).t }}
      </el-tag>
    </div>

    <!-- Director Agent Core 一站式生成进度横幅 -->
    <div v-if="directorRunId" class="director-banner" :class="directorRunning ? 'running' : (directorOutput ? 'done' : 'failed')">
      <span class="lab">🎬 导演一站式生成</span>
      <code>{{ directorRunId.slice(0, 12) }}</code>
      <span v-if="directorRunning" class="dn">{{ NODE_TXT[directorNode] || directorNode }} · {{ directorStage }}</span>
      <el-progress :percentage="directorProgress" :stroke-width="8" class="dn-prog" />
      <div v-if="Object.keys(directorSegments).length" class="seg-progress">
        <div v-for="(s, sid) in directorSegments" :key="sid" class="seg-row" :class="s.status">
          <span class="sid">{{ sid.slice(0, 8) }}</span>
          <span class="sst">{{ STATUS_TXT[s.status] || s.status }}</span>
          <el-progress :percentage="s.progress || 0" :stroke-width="6" class="sp" />
          <span class="seg-ops">
            <el-button size="small" v-if="s.status === 'running'" @click="pauseSeg(s)">暂停</el-button>
            <el-button size="small" v-if="s.status === 'paused'" @click="resumeSeg(s)">恢复</el-button>
            <el-button size="small" type="danger" plain v-if="s.status === 'running' || s.status === 'paused'" @click="cancelSeg(s)">取消</el-button>
            <a v-if="s.video_url" :href="s.video_url" target="_blank" class="sout">视频</a>
          </span>
        </div>
      </div>
      <a v-if="directorOutput" :href="directorOutput" target="_blank" class="out">查看成片</a>
      <span v-if="directorError" class="err">{{ directorError }}</span>
    </div>

    <!-- Director 品控报告 + 风格参考图（N06 §3.4.5 / N03 §3.2.2 输出，Layer 3 状态推送闭环） -->
    <div v-if="directorResult" class="director-quality">
      <h3>导演品控报告</h3>
      <div v-if="styleRefName" class="style-ref">
        <span class="lbl">N03 全局风格参考图：</span>
        <img :src="'/file?name=' + encodeURIComponent(styleRefName)" alt="style ref" />
      </div>
      <div v-if="qrAlerts.length" class="qc-alerts">
        <div v-for="(a, i) in qrAlerts" :key="i" class="qc-alert">⚠ {{ a.reason || a.message || JSON.stringify(a) }}</div>
      </div>
      <table v-if="qrReports.length" class="qc-table">
        <thead><tr><th>片段</th><th>文件</th><th>时长一致</th><th>人脸相似度(≥{{ FACE_SIM_THRESHOLD }})</th><th>结论</th></tr></thead>
        <tbody>
          <tr v-for="rep in qrReports" :key="rep.segment_id" :class="rep.pass ? 'ok' : 'bad'">
            <td>{{ rep.title || (rep.segment_id || '').slice(0, 8) }}</td>
            <td>{{ rep.file_ok ? '✓' : '✗' }}</td>
            <td>{{ rep.duration_ok ? '✓' : ('✗ +' + rep.duration_error + 's') }}</td>
            <td>{{ rep.face_sim == null ? '—' : rep.face_sim.toFixed(3) }}<span v-if="rep.engine_missing" class="muted"> (引擎缺)</span></td>
            <td>{{ rep.pass ? '通过' : '不合格' }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="qrFailed.length" class="qc-failed">跳过并告警的失败片段：{{ qrFailed.length }} 个（N08 合成时自动跳过）</div>
      <div v-if="!qrReports.length && !styleRefName && !qrAlerts.length" class="muted">本次运行未产出可展示的品控数据。</div>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="!segments.length" class="empty">该项目暂无片段，请先在「分镜编排」创建分镜。</div>

    <div v-else class="seg-grid">
      <div v-for="seg in segments" :key="seg.segment_id" class="seg-card">
        <div class="seg-thumb">
          <img v-if="seg.thumbnail_url || seg.video_url" :src="seg.thumbnail_url || seg.video_url" alt="" />
          <span v-else class="ph">({{ seg.index }})</span>
        </div>
        <div class="seg-body">
          <div class="seg-title">{{ seg.title || ('片段 ' + (seg.index ?? '')) }}</div>
          <div class="seg-meta">
            <span class="tag" :class="'st-'+mapDisp(seg.status).k">{{ SEG_STATUS_TXT[seg.status] || seg.status }}</span>
            <span class="dur">{{ Number(seg.duration) || 0 }}s</span>
          </div>

          <template v-if="seg.latest_task">
            <el-progress
              :percentage="Math.round(seg.latest_task.progress || 0)"
              :status="mapDisp(seg.latest_task.status).k === 'succeeded' ? 'success' : (mapDisp(seg.latest_task.status).k === 'failed' ? 'exception' : '')"
              :stroke-width="8" />
            <div class="seg-task">
              <span class="tk" :class="'tk-'+mapDisp(seg.latest_task.status).k">{{ mapDisp(seg.latest_task.status).t }}</span>
              <span class="tid">{{ (seg.latest_task.task_id || '').slice(0, 10) }}</span>
              <span v-if="seg.latest_task.error_message" class="err">{{ seg.latest_task.error_message }}</span>
            </div>
          </template>
          <div v-else class="seg-task muted">尚未分发</div>

          <div class="seg-ops">
            <el-button size="small" type="primary" @click="dispatchSegment(seg)">分发生成</el-button>
            <el-button size="small" :disabled="!seg.latest_task" @click="toggleExpand(seg)">
              {{ expandedId === seg.latest_task?.task_id ? '收起日志' : '实时日志' }}
            </el-button>
            <el-button size="small" :disabled="!seg.latest_task || mapDisp(seg.latest_task.status).k === 'succeeded'" @click="cancel(seg)">取消</el-button>
            <el-button size="small" :disabled="!seg.latest_task" @click="retry(seg)">重试</el-button>
          </div>
        </div>
        <VideoLogPanel v-if="expandedId === seg.latest_task?.task_id" :task-id="seg.latest_task.task_id" />
      </div>
    </div>
  </ProjectBookShell>
</template>

<style scoped>
.prod { padding: 0 18px 24px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin: 4px 0 10px; flex-wrap: wrap; }
.page-head h1 { font-size: 20px; margin: 0; }
.muted { color: var(--sub); font-size: 12px; margin: 4px 0 0; }
.ops { display: flex; gap: 8px; flex-wrap: wrap; }
.engine-bar { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.chip { display: flex; align-items: center; gap: 6px; font-size: 12px; background: var(--card); border: 1px solid var(--line); border-radius: 999px; padding: 4px 12px; color: var(--sub); }
.chip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--sub); }
.chip.ok { color: var(--green); border-color: rgba(74,222,128,0.4); }
.chip.ok .dot { background: var(--green); }
.chip.bad { color: var(--pink); border-color: rgba(244,114,182,0.4); }
.chip.bad .dot { background: var(--pink); }
.chip .role { color: var(--sub); font-size: 11px; }
.chip b { color: var(--txt); }
.synth-banner { display: flex; align-items: center; gap: 10px; background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--pur); border-radius: 10px; padding: 8px 14px; margin-bottom: 12px; font-size: 13px; }
.synth-banner.failed { border-left-color: var(--pink); }
.synth-banner.succeeded { border-left-color: var(--green); }
.synth-banner code { background: var(--card-2); padding: 1px 6px; border-radius: 5px; }
.director-banner { display: flex; align-items: center; gap: 10px; background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--pur); border-radius: 10px; padding: 8px 14px; margin-bottom: 12px; font-size: 13px; flex-wrap: wrap; }
.director-banner.running { border-left-color: var(--blue); }
.director-banner.done { border-left-color: var(--green); }
.director-banner.failed { border-left-color: var(--pink); }
.director-banner .lab { font-weight: 700; }
.director-banner code { background: var(--card-2); padding: 1px 6px; border-radius: 5px; }
.director-banner .dn { color: var(--sub); font-size: 12px; }
.director-banner .dn-prog { flex: 1; min-width: 160px; max-width: 360px; }
.director-banner .out { color: var(--blue); text-decoration: none; font-weight: 700; }
.director-banner .err { color: var(--pink); }
.seg-progress { flex-basis: 100%; display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
.seg-row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.seg-row .sid { font-family: monospace; color: var(--sub); }
.seg-row .sst { width: 52px; color: var(--sub); }
.seg-row.running .sst { color: var(--blue); }
.seg-row.succeeded .sst { color: var(--green); }
.seg-row.failed .sst { color: var(--pink); }
.seg-row .sp { flex: 1; min-width: 120px; max-width: 280px; }
.seg-row .sout { color: var(--blue); text-decoration: none; font-weight: 700; }
.seg-row .seg-ops { margin: 0 0 0 auto; gap: 4px; }
.seg-row .seg-ops .el-button { margin-left: 0; }
.seg-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 16px; }
.seg-card { display: flex; gap: 12px; background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 14px; flex-wrap: wrap; }
.seg-thumb { flex: 0 0 84px; height: 84px; border-radius: 10px; background: linear-gradient(135deg, var(--pink), var(--pur)); display: flex; align-items: center; justify-content: center; color: #fff; font-size: 24px; font-weight: 800; overflow: hidden; }
.seg-thumb img { width: 100%; height: 100%; object-fit: cover; }
.seg-body { flex: 1; min-width: 220px; }
.seg-title { font-size: 14px; font-weight: 700; }
.seg-meta { display: flex; align-items: center; gap: 8px; margin: 6px 0 8px; }
.tag { font-size: 11px; border-radius: 6px; padding: 1px 8px; background: var(--card-2); color: var(--sub); }
.tag.st-succeeded { color: var(--green); } .tag.st-generating { color: var(--blue); } .tag.st-failed { color: var(--pink); }
.dur { font-size: 12px; color: var(--sub); }
.seg-task { display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: 12px; }
.seg-task .tk { font-weight: 700; } .seg-task .tk-tk-succeeded { color: var(--green); } .seg-task .tk-running { color: var(--blue); } .seg-task .tk-failed { color: var(--pink); } .seg-task .tk-queued { color: var(--sub); }
.seg-task .tid { color: var(--sub); font-family: monospace; }
.seg-task .err { color: var(--pink); }
.seg-ops { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; }
.empty { padding: 40px; text-align: center; color: var(--sub); }
.dir-params { background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--pur); border-radius: 10px; padding: 10px 14px; margin-bottom: 12px; }
.dir-params .dp-head { font-weight: 700; margin-bottom: 8px; }
.dir-params .dp-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 8px 16px; }
.dir-params label { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--sub); }
.dir-params label select, .dir-params label input[type=number] { width: 96px; background: var(--card-2); color: var(--txt); border: 1px solid var(--line); border-radius: 6px; padding: 2px 4px; }
.dir-params label input[type=text] { width: 100%; background: var(--card-2); color: var(--txt); border: 1px solid var(--line); border-radius: 6px; padding: 4px 6px; font-size: 12px; }
.dir-params label.dp-wide { grid-column: 1 / -1; }
.dir-params label.dp-slider { grid-column: 1 / -1; flex-direction: column; align-items: stretch; gap: 4px; }
.director-quality { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; margin-bottom: 12px; }
.director-quality h3 { margin: 0 0 8px; font-size: 14px; }
.director-quality .style-ref { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; font-size: 12px; }
.director-quality .style-ref img { max-height: 120px; border-radius: 8px; border: 1px solid var(--line); }
.director-quality .qc-alert { color: var(--pink); font-size: 12px; margin: 2px 0; }
.director-quality .qc-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 4px; }
.director-quality .qc-table th, .director-quality .qc-table td { border: 1px solid var(--line); padding: 4px 8px; text-align: left; }
.director-quality .qc-table tr.ok td:last-child { color: var(--green); }
.director-quality .qc-table tr.bad td:last-child { color: var(--pink); font-weight: 700; }
.director-quality .qc-failed { color: var(--pink); font-size: 12px; margin-top: 6px; }
</style>
