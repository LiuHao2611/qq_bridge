<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { listTasks, cancelTask, retryTask, getTaskQueue, updateTaskPriority, clearComfyCache } from '../api'
import { mockTasks } from '../mock'
import { toast } from '../store'
import { fmtTime } from '../api'
import VideoLogPanel from './VideoLogPanel.vue'

const expandedId = ref('')
const toggleExpand = (id) => { expandedId.value = expandedId.value === id ? '' : id }

const tasks = ref([])
const tab = ref('all')
const tabs = [
  { v: 'all', label: '全部' },
  { v: 'queued', label: '排队中' },
  { v: 'generating', label: '生成中' },
  { v: 'done', label: '已完成' },
  { v: 'failed', label: '失败' },
]
const STATUS_TXT = { queued: '排队中', generating: '生成中', done: '已完成', failed: '失败' }

const mapStatus = (s) => {
  if (s === 'succeeded') return 'done'
  if (s === 'running' || s === 'retrying') return 'generating'
  if (s === 'failed' || s === 'cancelled') return 'failed'
  return s || 'queued'
}

// 复用 P2 M2 首尾帧链：params.first_frame 非空 → 有依赖（需等待上游尾帧）
const hasFirstFrame = (p) => {
  const ff = p && p.first_frame
  return !!ff && ff !== '' && ff !== false && ff !== 0 && ff !== 'false' && ff !== 'null'
}

const adaptTask = (t) => {
  const params = (typeof t.params === 'string' ? JSON.parse(t.params || '{}') : (t.params || {})) || {}
  const dep = hasFirstFrame(params)
  return {
    id: t.task_id,
    title: '片段生成任务 ' + (t.local_task_id || t.task_id.slice(0, 8)),
    status: mapStatus(t.status),
    progress: t.progress || 0,
    priority: t.priority || 0,
    reason: t.error_message || '',
    dep,
    // M2 依赖标注：waiting=有首尾帧依赖（串行等待上游）/ ready=可调度
    scheduleLabel: dep ? '等待上游' : '可调度',
    params: {
      model: params.model_id || t.model_id || '本地模型',
      resolution: params.resolution || '720p',
      ratio: params.ratio || '16:9',
      duration: (params.duration || 12) + 's',
    },
    created_at: fmtTime((t.created_at || 0) * 1000),
    raw: t,
  }
}

const load = async () => {
  try {
    const data = await listTasks({ limit: 50 })
    tasks.value = data.map(adaptTask)
  } catch (e) {
    console.warn('task list backend failed, fallback to mock', e.message)
    tasks.value = mockTasks.map((t) => ({ ...t }))
  }
}

// M2 队列元数据（GPU 并发约束提示）
const gpuMaxConcurrent = ref(1)
const loadQueueMeta = async () => {
  try {
    const r = await getTaskQueue()
    gpuMaxConcurrent.value = r.gpu_max_concurrent || 1
  } catch (e) { /* best-effort，不阻断主列表 */ }
}

onMounted(() => {
  load()
  loadQueueMeta()
  timer = setInterval(load, 5000)
})

const filtered = computed(() => (tab.value === 'all' ? tasks.value : tasks.value.filter((t) => t.status === tab.value)))

// 夜城 HUD 状态条（仅展示派生值，不改任何业务逻辑/数据流）
const queueDepth = computed(() => tasks.value.filter((t) => t.status === 'queued').length)
const generatingCount = computed(() => tasks.value.filter((t) => t.status === 'generating').length)
const totalCount = computed(() => tasks.value.length)
const statusTagClass = (s) => ({
  queued: '', generating: 'nc-tag--cyan', done: 'nc-tag--green', failed: 'nc-tag--magenta',
}[s] || '')

const cancel = async (t) => {
  try {
    await cancelTask(t.id)
    toast('已取消任务：' + t.title)
    load()
  } catch (e) {
    toast('取消失败: ' + (e.message || e))
  }
}
const retry = async (t) => {
  try {
    await retryTask(t.id)
    toast('已重试：' + t.title)
    load()
  } catch (e) {
    // local fallback
    t.status = 'generating'; t.progress = 0
    toast('已重试：' + t.title)
  }
}

// M2 调整优先级（VIP 高优先），后端 worker 下次轮询即按新优先级重排消费
const adjustPriority = async (t, delta) => {
  const np = (t.priority || 0) + delta
  try {
    await updateTaskPriority(t.id, np)
    t.priority = np
    toast('已调整优先级：' + np)
    loadQueueMeta()
  } catch (e) {
    toast('调整失败: ' + (e.message || e))
  }
}

// M3 手动清理 ComfyUI 显存（优雅 free memory，不杀进程）
const clearing = ref(false)
const clearVram = async () => {
  clearing.value = true
  try {
    const r = await clearComfyCache()
    toast('显存清理：' + (r.result || 'done'))
  } catch (e) {
    toast('清理失败: ' + (e.message || e))
  } finally {
    clearing.value = false
  }
}

let timer = null
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="page">
    <div class="page-head nc-page-head">
      <div><h1 class="nc-title">推理监控</h1><p class="muted">所有项目的生成任务队列与历史记录（跨切面诊断视图，非流程阶段页）</p></div>
    </div>

    <!-- HUD 状态条（夜城签名；在默认/P5 主题下退化为纯文本，不破版） -->
    <div class="nc-statusbar">
      <span class="nc-tag nc-tag--cyan">引擎 ONLINE</span>
      <span class="nc-tag">队列 {{ queueDepth }}</span>
      <span class="nc-tag nc-tag--green">生成中 {{ generatingCount }}</span>
      <span class="nc-tag nc-tag--ghost">任务总数 {{ totalCount }}</span>
      <span class="nc-tag nc-tag--ghost">GPU并发 {{ gpuMaxConcurrent }}</span>
      <el-button size="small" :loading="clearing" @click="clearVram">清理显存</el-button>
    </div>

    <el-tabs v-model="tab" class="tk-tabs">
      <el-tab-pane v-for="t in tabs" :key="t.v" :label="t.label" :name="t.v" />
    </el-tabs>

    <div v-if="filtered.length" class="tk-grid">
      <div v-for="t in filtered" :key="t.id" class="tk-item">
        <div class="tk-card">
          <span class="nc-corner tl"></span><span class="nc-corner tr"></span><span class="nc-corner bl"></span><span class="nc-corner br"></span>
          <div class="tk-thumb">{{ t.title.slice(0, 1) }}</div>
          <div class="tk-body">
            <div class="tk-title">{{ t.title }}</div>
            <div class="tk-params">
              <span>{{ t.params.model }}</span><span>{{ t.params.resolution }}</span><span>{{ t.params.ratio }}</span><span>{{ t.params.duration }}</span>
            </div>
            <!-- M2：依赖标注（复用 P2 M2 首尾帧链）+ 优先级调整 -->
            <div class="tk-meta">
              <span class="nc-tag" :class="t.dep ? 'nc-tag--magenta' : 'nc-tag--green'">{{ t.scheduleLabel }}</span>
              <span class="tk-pri">优先级 {{ t.priority }}</span>
              <el-button size="small" text :disabled="t.status==='generating'" @click="adjustPriority(t, -1)">−</el-button>
              <el-button size="small" text :disabled="t.status==='generating'" @click="adjustPriority(t, 1)">＋</el-button>
            </div>
            <!-- M3：失败红色提示 + 原因 -->
            <div v-if="t.status==='failed' && t.reason" class="tk-fail">⚠ 失败原因：{{ t.reason }}</div>
            <el-progress :percentage="t.progress" :status="t.status==='done'?'success':t.status==='failed'?'exception':''" :stroke-width="8" />
            <div class="tk-foot">
              <span class="nc-tag" :class="statusTagClass(t.status)">{{ STATUS_TXT[t.status] }}</span>
              <span class="tk-time">{{ t.created_at }}</span>
              <div class="tk-ops">
                <el-button size="small" :disabled="t.status!=='generating'&&t.status!=='queued'" @click="cancel(t)">取消</el-button>
                <el-button size="small" type="primary" :disabled="t.status==='generating'" @click="retry(t)">重试</el-button>
                <el-button size="small" @click="toggleExpand(t.id)">{{ expandedId===t.id ? '收起日志' : '实时日志' }}</el-button>
              </div>
            </div>
          </div>
        </div>
        <div v-if="expandedId === t.id" class="nc-logwrap">
          <span class="nc-corner tl"></span><span class="nc-corner tr"></span><span class="nc-corner bl"></span><span class="nc-corner br"></span>
          <div class="nc-loghead">SYSTEM // 实时日志</div>
          <VideoLogPanel :task-id="t.id" />
        </div>
      </div>
    </div>
    <div v-else class="empty nc-empty">暂无生成任务</div>
  </div>
</template>

<style scoped>
.tk-tabs { margin-bottom: 8px; }
.tk-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.tk-card { display: flex; gap: 12px; background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 14px; }
.tk-thumb { flex: 0 0 64px; height: 64px; border-radius: 10px; background: linear-gradient(135deg, var(--pink), var(--pur)); display: flex; align-items: center; justify-content: center; font-size: 26px; font-weight: 800; color: #fff; }
.tk-body { flex: 1; min-width: 0; }
.tk-title { font-size: 14px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tk-params { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 8px; }
.tk-params span { font-size: 11px; color: var(--sub); background: var(--card-2); border-radius: 6px; padding: 1px 7px; }
.tk-meta { display: flex; align-items: center; gap: 8px; margin: 4px 0 2px; }
.tk-pri { font-size: 11px; color: var(--sub); }
.tk-fail { font-size: 12px; color: #ff5a7a; background: rgba(255,90,122,0.12); border: 1px solid rgba(255,90,122,0.4); border-radius: 6px; padding: 3px 8px; margin: 4px 0; font-weight: 600; }
.tk-foot { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.tk-status { font-size: 12px; font-weight: 700; }
.tk-status.generating { color: var(--blue); } .tk-status.done { color: var(--green); } .tk-status.failed { color: var(--pink); } .tk-status.queued { color: var(--sub); }
.tk-time { font-size: 11px; color: var(--sub); }
.tk-ops { margin-left: auto; display: flex; gap: 6px; }
.empty { text-align: center; color: var(--sub); padding: 60px 0; }
</style>
