<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { logList, logContent, logPack } from '../api.js'

const logs = ref([])
const loadingList = ref(false)
const selected = ref('')
const content = ref('')
const loadingContent = ref(false)
const totalLines = ref(0)
const returnedLines = ref(0)

// 搜索条件（前端即时过滤 + 高亮，单一搜索框）
const tailN = ref(300)
const grep = ref('')

async function loadList() {
  loadingList.value = true
  try {
    const r = await logList()
    logs.value = r.logs || []
    if (!selected.value && logs.value.length) selected.value = logs.value[0].name
  } catch (e) {
    logs.value = []
    ElMessage.error('日志列表加载失败：' + (e.message || e))
  } finally {
    loadingList.value = false
  }
}

async function loadContent() {
  if (!selected.value) { content.value = ''; return }
  loadingContent.value = true
  try {
    // 仅按末尾行数拉取，搜索/过滤在前端实时完成（响应快、单搜索框）
    const r = await logContent(selected.value, { tail: tailN.value })
    content.value = r.text || ''
    totalLines.value = r.total_lines || 0
    returnedLines.value = r.returned_lines || 0
  } catch (e) {
    content.value = ''
    ElMessage.error('读取失败：' + (e.message || e))
  } finally {
    loadingContent.value = false
  }
}

// ---- 日志行解析：行号 + 级别着色 + 关键词高亮 ----
function detectLevel(text) {
  const t = text.toLowerCase()
  if (/(error|err|traceback|exception|failed|失败|fatal|cannot|unable)/.test(t)) return 'error'
  if (/(warn|warning|警告)/.test(t)) return 'warn'
  if (/(^|\s)info(:|\s)/.test(t) || /\binfo\b/.test(t)) return 'info'
  if (/(^|\s)debug(:|\s)/.test(t) || /\bdebug\b/.test(t)) return 'debug'
  return 'normal'
}
function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
function highlight(text, kw) {
  const safe = escapeHtml(text)
  if (!kw) return safe
  const lower = text.toLowerCase()
  const k = kw.toLowerCase()
  let idx = lower.indexOf(k)
  if (idx === -1) return safe
  let out = ''
  let i = 0
  while (idx !== -1) {
    out += escapeHtml(text.slice(i, idx)) + '<mark>' + escapeHtml(text.slice(idx, idx + k.length)) + '</mark>'
    i = idx + k.length
    idx = lower.indexOf(k, i)
  }
  out += escapeHtml(text.slice(i))
  return out
}
const lines = computed(() => {
  let raw = (content.value || '').split('\n')
  // 去掉末尾因换行产生的空行，避免多出一个空行号
  if (raw.length && raw[raw.length - 1] === '') raw = raw.slice(0, -1)
  // 真实行号：纯 tail 时首行 = 总行数 - 返回行数 + 1（grep 过滤时近似）
  const start = Math.max(1, totalLines.value - returnedLines.value + 1)
  return raw.map((text, i) => ({ no: start + i, text, level: detectLevel(text) }))
})
const displayLines = computed(() => {
  const kw = grep.value.trim().toLowerCase()
  if (!kw) return lines.value
  return lines.value.filter((l) => l.text.toLowerCase().includes(kw))
})
const matchCount = computed(() => displayLines.value.length)

watch(selected, () => { grep.value = ''; loadContent() })

// 搜索框输入即过滤；回车/点按刷新尾部
function onSearchInput() { /* displayLines 已响应式，无需额外操作 */ }


function fmtSize(n) {
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(2) + ' MB'
}
function fmtTime(t) {
  if (!t) return ''
  const d = new Date(t * 1000)
  const p = (x) => (x < 10 ? '0' + x : '' + x)
  return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes())
}

async function packAll() {
  try {
    await logPack([])
    ElMessage.success('已打包全部日志开始下载')
  } catch (e) {
    ElMessage.error('打包失败：' + (e.message || e))
  }
}

onMounted(loadList)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>系统日志</h1>
        <p class="muted">本地服务日志统一存放在 D:\AI\logs，可在线查看、搜索并打包发助手排查。所有日志均在 D 盘，绝不上 C 盘。</p>
      </div>
      <button class="btn" :disabled="!logs.length" @click="packAll">📦 打包全部日志</button>
    </div>

    <div class="log-layout">
      <!-- 左：文件列表 -->
      <div class="log-list">
        <div class="ll-head">
          <span>日志文件</span>
          <button class="btn ghost sm" :disabled="loadingList" @click="loadList">刷新</button>
        </div>
        <div v-if="loadingList" class="empty sm">加载中…</div>
        <div v-else-if="!logs.length" class="empty sm">暂无日志</div>
        <div
          v-for="f in logs" :key="f.name"
          class="ll-item" :class="{ active: f.name === selected }"
          @click="selected = f.name"
        >
          <div class="ll-name">{{ f.name }}</div>
          <div class="ll-meta">{{ fmtSize(f.size) }} · {{ fmtTime(f.modified_at) }}</div>
        </div>
      </div>

      <!-- 右：内容查看 -->
      <div class="log-view">
        <div class="lv-bar">
          <span class="lv-name">{{ selected || '未选择' }}</span>
          <span class="muted">共 {{ totalLines }} 行 / 展示 {{ returnedLines }} 行<span v-if="grep.trim()"> · 匹配 {{ matchCount }} 行</span></span>
          <div class="lv-tools">
            <el-input v-model="grep" size="small" placeholder="搜索关键字（即时高亮/过滤）" clearable style="width:240px"
              @input="onSearchInput" @keyup.enter="loadContent" />
            <label class="muted">末尾</label>
            <el-input-number v-model="tailN" :min="10" :max="5000" :step="50" size="small" controls-position="right" style="width:120px" />
            <label class="muted">行</label>
            <button class="btn sm" @click="loadContent" :disabled="loadingContent">查看</button>
          </div>
        </div>
        <div v-if="content" class="lv-body">
          <div class="line" :class="l.level" v-for="l in displayLines" :key="l.no">
            <span class="ln">{{ l.no }}</span>
            <span class="txt" v-html="highlight(l.text, grep)"></span>
          </div>
        </div>
        <div v-else-if="loadingContent" class="empty">读取中…</div>
        <div v-else-if="grep.trim() && !displayLines.length" class="empty">当前内容无匹配「{{ grep }}」</div>
        <div v-else class="empty">选择左侧日志文件查看内容</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.page-head p { margin: 4px 0 0; font-size: 12px; max-width: 720px; }
.log-layout { display: grid; grid-template-columns: 280px 1fr; gap: 16px; align-items: start; }
.log-list { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 12px; max-height: 70vh; overflow: auto; }
.ll-head { display: flex; align-items: center; justify-content: space-between; font-weight: 700; margin-bottom: 8px; font-size: 13px; }
.ll-item { padding: 9px 10px; border-radius: 9px; cursor: pointer; border: 1px solid transparent; margin-bottom: 4px; }
.ll-item:hover { background: var(--card-2); }
.ll-item.active { background: var(--card-2); border-color: var(--pur); }
.ll-name { font-size: 13px; font-weight: 700; word-break: break-all; }
.ll-meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
.log-view { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 12px; }
.lv-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.lv-name { font-weight: 800; font-size: 14px; }
.lv-tools { margin-left: auto; display: flex; align-items: center; gap: 6px; }
.lv-body { background: #0e1015; color: #d8e0ea; border-radius: 10px; padding: 8px 0; font-size: 12px;
  line-height: 1.55; max-height: 64vh; overflow: auto; font-family: ui-monospace, Menlo, Consolas, monospace; }
.line { display: flex; gap: 12px; padding: 0 12px; border-radius: 4px; }
.line:hover { background: rgba(255,255,255,.04); }
.ln { color: #5a6573; text-align: right; min-width: 52px; flex-shrink: 0; user-select: none; }
.txt { flex: 1; white-space: pre-wrap; word-break: break-word; }
.line.error { background: rgba(255,107,107,.08); }
.line.error .txt { color: #ff6b6b; }
.line.warn { background: rgba(245,197,66,.07); }
.line.warn .txt { color: #f5c542; }
.line.info .txt { color: #9fd3ff; }
.line.debug .txt { color: #8a93a3; }
.lv-body mark { background: #ffd24d; color: #1a1a1a; border-radius: 2px; padding: 0 1px; }
.empty { padding: 30px; text-align: center; color: var(--muted); font-size: 13px; }
.empty.sm { padding: 14px; }
</style>
