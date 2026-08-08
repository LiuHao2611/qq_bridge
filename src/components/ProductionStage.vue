<script setup>
// ============================================================
// Phase 4 生产工作台（KB §5.6 模块五 AI视频生成 + §18 WebSocket + §06 Webhook）
// 语义：将分镜「分发」到 AI 推理引擎并异步生成视频；实时监看任务进度/日志/结果。
// 不是「任务中心」式的全局浏览页 —— 任务在此是生产流程的跨切面关注点，内嵌于编辑器。
// 实时：优先 WebSocket（task:{id} 频道）；WS 不可用时回退轮询（REDUN-01）。
// ============================================================
import { ref, reactive, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getProduction, inferenceHealth, estimatePoints, preAuthPoints, getTask,
  generateSegment, regenerateSegment, cancelTask, retryTask, synthesizeProject,
} from '../api'
import { subscribeTask, wsConnected, connectWs } from '../ws'
import { toast } from '../store'
import { fmtTime } from '../api'
import ProjectFlowNav from './ProjectFlowNav.vue'
import VideoLogPanel from './VideoLogPanel.vue'

const route = useRoute()
const router = useRouter()
const pid = route.params.id

const project = ref({})
const segments = ref([])
const health = ref({ comfyui: {}, ollama: {}, queue_depth: 0, ws_connections: 0, mode: 'unknown', async_callback: '' })
const loading = ref(false)
const expandedId = ref('')
const synthTaskId = ref('')
const synthStatus = ref('')

const STATUS_TXT = { queued: '排队中', running: '生成中', retrying: '重试中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
const SEG_STATUS_TXT = { draft: '草稿', saved: '已保存', generating: '生成中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }

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
      task.status = event === 'completed' ? 'succeeded' : 'failed'
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

async function onSynthesize() {
  const segs = segments.value
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
        if (event === 'completed') { synthStatus.value = 'succeeded'; toast('合成全集完成'); loadProd() }
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
    }
  } catch (e) { toast('合成失败: ' + (e.message || e)) }
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
})
onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer)
  subs.forEach((fn) => { try { fn() } catch {} })
  polls.forEach((t) => clearInterval(t))
  subs.clear(); polls.clear()
})
</script>

<template>
  <div class="page prod">
    <ProjectFlowNav :pid="pid" :step="4" @synthesize="onSynthesize" />

    <div class="page-head">
      <div>
        <h1>生成生产</h1>
        <p class="muted">第四阶段 · 任务分发与异步 AI 推理（生产阶段）。将分镜分发给推理引擎，实时监看生成进度与结果。</p>
      </div>
      <div class="ops">
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
    </div>

    <!-- 合成全集进度横幅 -->
    <div v-if="synthTaskId" class="synth-banner" :class="synthStatus">
      <span>合成全集任务</span>
      <code>{{ synthTaskId.slice(0, 12) }}</code>
      <el-tag size="small" :type="synthStatus === 'succeeded' ? 'success' : (synthStatus === 'failed' ? 'danger' : 'warning')">
        {{ mapDisp(synthStatus).t }}
      </el-tag>
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
  </div>
</template>

<style scoped>
.prod { padding: 0 18px 24px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin: 14px 0 10px; flex-wrap: wrap; }
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
</style>
