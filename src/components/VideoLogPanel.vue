<script setup>
import { ref, watch, onUnmounted, nextTick } from 'vue'
import { getTaskLogs } from '../api'

// STR-01：实时生成日志流（轮询，禁用 WebSocket）
// 给定一个 task_id，每 3s 拉取一次「任务状态 + generation_logs」，渲染滚动日志。
const props = defineProps({
  taskId: { type: String, required: true },
})

const logs = ref([])
const status = ref('')
const errorMessage = ref('')
const autoScroll = ref(true)
const logBox = ref(null)
let timer = null

const TERMINAL = ['succeeded', 'failed', 'cancelled']
const STATUS_LABEL = {
  queued: '排队中', running: '生成中', retrying: '重试中',
  succeeded: '已完成', failed: '失败', cancelled: '已取消',
}

const fetchOnce = async () => {
  try {
    const { task, logs: ls } = await getTaskLogs(props.taskId)
    status.value = task?.status || ''
    errorMessage.value = task?.error_message || ''
    logs.value = (ls || []).map((l) => ({
      level: l.level || 'info',
      message: l.message,
      time: l.created_at,
    }))
    if (autoScroll.value) {
      await nextTick(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight })
    }
  } catch (e) {
    // 网络抖动时保留上次日志，避免面板闪烁空白
  }
}

const stop = () => { if (timer) { clearInterval(timer); timer = null } }
const start = () => {
  stop()
  fetchOnce()
  timer = setInterval(() => {
    fetchOnce()
    if (TERMINAL.includes(status.value)) stop()
  }, 3000)
}

watch(() => props.taskId, () => { logs.value = []; start() }, { immediate: true })
onUnmounted(stop)

const fmt = (ts) => {
  if (!ts) return ''
  const ms = (typeof ts === 'number' && ts < 1e12) ? ts * 1000 : ts
  const d = new Date(ms)
  if (isNaN(d.getTime())) return ''
  const p = (x) => (x < 10 ? '0' + x : '' + x)
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds())
}
const onScroll = () => {
  if (!logBox.value) return
  const nearBottom = logBox.value.scrollHeight - logBox.value.scrollTop - logBox.value.clientHeight < 40
  autoScroll.value = nearBottom
}
</script>

<template>
  <div class="vlp">
    <div class="vlp-head">
      <span class="vlp-status" :class="status">{{ STATUS_LABEL[status] || status || '未知' }}</span>
      <span v-if="errorMessage" class="vlp-err">⚠ {{ errorMessage }}</span>
      <span class="vlp-count">{{ logs.length }} 条日志</span>
      <label class="vlp-auto"><el-checkbox v-model="autoScroll" size="small">自动滚动</el-checkbox></label>
      <button class="vlp-refresh" @click="fetchOnce">刷新</button>
    </div>
    <div class="vlp-body" ref="logBox" @scroll="onScroll">
      <div v-for="(l, i) in logs" :key="i" class="vlp-line" :class="l.level">
        <span class="vlp-time">{{ fmt(l.time) }}</span>
        <span class="vlp-msg">{{ l.message }}</span>
      </div>
      <div v-if="!logs.length" class="vlp-empty">暂无日志，生成开始后自动刷新…</div>
    </div>
  </div>
</template>

<style scoped>
.vlp { margin-top: 10px; background: var(--card); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.vlp-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 8px 12px; border-bottom: 1px solid var(--line); font-size: 12px; }
.vlp-status { font-weight: 700; padding: 1px 8px; border-radius: 6px; background: var(--card-2); }
.vlp-status.queued { color: var(--sub); }
.vlp-status.running, .vlp-status.retrying { color: var(--blue); }
.vlp-status.succeeded { color: var(--green); }
.vlp-status.failed { color: var(--pink); }
.vlp-status.cancelled { color: var(--sub); }
.vlp-err { color: var(--pink); }
.vlp-count { color: var(--sub); }
.vlp-auto { margin-left: auto; }
.vlp-refresh { background: transparent; border: 1px solid var(--line); color: var(--txt); border-radius: 6px; padding: 2px 10px; font-size: 12px; cursor: pointer; }
.vlp-refresh:hover { border-color: var(--pur); }
.vlp-body { background: #0e1015; color: #d8e0ea; border-radius: 0 0 12px 12px; padding: 8px 0; font-size: 12px; line-height: 1.55; max-height: 240px; overflow: auto; font-family: ui-monospace, Menlo, Consolas, monospace; }
.vlp-line { display: flex; gap: 12px; padding: 0 12px; }
.vlp-time { color: #5a6573; text-align: right; min-width: 64px; flex-shrink: 0; user-select: none; }
.vlp-msg { flex: 1; white-space: pre-wrap; word-break: break-word; }
.vlp-line.warning .vlp-msg { color: #f5c542; }
.vlp-line.error .vlp-msg { color: #ff6b6b; }
.vlp-empty { padding: 18px; text-align: center; color: var(--muted); }
</style>
