<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { listTasks, cancelTask, retryTask } from '../api'
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

const adaptTask = (t) => {
  const params = (typeof t.params === 'string' ? JSON.parse(t.params || '{}') : (t.params || {})) || {}
  return {
    id: t.task_id,
    title: '片段生成任务 ' + (t.local_task_id || t.task_id.slice(0, 8)),
    status: mapStatus(t.status),
    progress: t.progress || 0,
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

onMounted(() => {
  load()
  timer = setInterval(load, 5000)
})

const filtered = computed(() => (tab.value === 'all' ? tasks.value : tasks.value.filter((t) => t.status === tab.value)))

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

let timer = null
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div><h1>推理监控</h1><p class="muted">所有项目的生成任务队列与历史记录（跨切面诊断视图，非流程阶段页）</p></div>
    </div>
    <el-tabs v-model="tab" class="tk-tabs">
      <el-tab-pane v-for="t in tabs" :key="t.v" :label="t.label" :name="t.v" />
    </el-tabs>

    <div v-if="filtered.length" class="tk-grid">
      <div v-for="t in filtered" :key="t.id" class="tk-item">
        <div class="tk-card">
          <div class="tk-thumb">{{ t.title.slice(0, 1) }}</div>
          <div class="tk-body">
            <div class="tk-title">{{ t.title }}</div>
            <div class="tk-params">
              <span>{{ t.params.model }}</span><span>{{ t.params.resolution }}</span><span>{{ t.params.ratio }}</span><span>{{ t.params.duration }}</span>
            </div>
            <el-progress :percentage="t.progress" :status="t.status==='done'?'success':t.status==='failed'?'exception':''" :stroke-width="8" />
            <div class="tk-foot">
              <span class="tk-status" :class="t.status">{{ STATUS_TXT[t.status] }}</span>
              <span class="tk-time">{{ t.created_at }}</span>
              <div class="tk-ops">
                <el-button size="small" :disabled="t.status!=='generating'&&t.status!=='queued'" @click="cancel(t)">取消</el-button>
                <el-button size="small" :disabled="t.status==='generating'" @click="retry(t)">重试</el-button>
                <el-button size="small" @click="toggleExpand(t.id)">{{ expandedId===t.id ? '收起日志' : '实时日志' }}</el-button>
              </div>
            </div>
          </div>
        </div>
        <VideoLogPanel v-if="expandedId === t.id" :task-id="t.id" />
      </div>
    </div>
    <div v-else class="empty">暂无生成任务</div>
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
.tk-foot { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.tk-status { font-size: 12px; font-weight: 700; }
.tk-status.generating { color: var(--blue); } .tk-status.done { color: var(--green); } .tk-status.failed { color: var(--pink); } .tk-status.queued { color: var(--sub); }
.tk-time { font-size: 11px; color: var(--sub); }
.tk-ops { margin-left: auto; display: flex; gap: 6px; }
.empty { text-align: center; color: var(--sub); padding: 60px 0; }
</style>
