<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  getServices, controlService, listModules, snapshotModule, rollbackModule, setModuleEnabled, getGitLog,
} from '../api'

const route = useRoute()
const activeTab = ref('services')
const services = ref([])
const modules = ref([])
const loading = ref(false)
const disabledMsg = ref('')

// 提交记录（Git 历史，服务与版本管理器第三页签）
const gitlog = ref([])
const gitBranch = ref('')
const gitLoading = ref(false)
const gitError = ref('')
// 默认最近 7 天（含今天）：起始=今天-6，结束=今天
function _fmtYmd(d) {
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
const _today = new Date()
const _sinceDefault = new Date(_today)
_sinceDefault.setDate(_today.getDate() - 6)
const gitSince = ref(_fmtYmd(_sinceDefault))   // 默认起始：最近 7 天
const gitUntil = ref(_fmtYmd(_today))          // 默认结束：今天
const gitFilterActive = ref(false)

// 弹窗状态
const snapDialog = ref(false)
const snapTarget = ref(null)
const snapNote = ref('')
const rollDialog = ref(false)
const rollTarget = ref(null)
const rollVersion = ref('')

const serviceTip = computed(() => {
  const down = services.value.filter((s) => s.status !== 'up').map((s) => s.name)
  return down.length ? '离线：' + down.join('、') : '全部在线'
})

async function loadServices() {
  try {
    services.value = await getServices()
  } catch (e) {
    ElMessage.error('服务探针失败：' + (e.message || e))
  }
}

async function loadModules() {
  try {
    const r = await listModules()
    modules.value = r.modules || []
  } catch (e) {
    ElMessage.error('模块列表失败：' + (e.message || e))
  }
}

async function loadGitLog(opts = {}) {
  // 首次加载全部后缓存；仅当显式 force（筛选/重置）时才重新请求
  if (gitlog.value.length && !opts.force) return
  gitLoading.value = true
  gitError.value = ''
  try {
    // 默认按日期筛选：优先取 opts，否则复用当前选择器值（默认最近 7 天）
    const since = (opts.since !== undefined) ? opts.since : gitSince.value
    const until = (opts.until !== undefined) ? opts.until : gitUntil.value
    const params = {}
    if (since) params.since = since
    if (until) params.until = until
    const r = await getGitLog(params)   // 无 limit → 后端返回该范围内全部记录
    gitlog.value = (r && r.commits) || []
    gitBranch.value = (r && r.branch) || ''
    gitFilterActive.value = !!(since || until)
    if (!gitlog.value.length) gitError.value = gitFilterActive.value ? '该时间范围内暂无提交记录' : '暂无提交记录'
  } catch (e) {
    gitError.value = '提交记录加载失败：' + (e.message || e)
  } finally {
    gitLoading.value = false
  }
}

function applyGitFilter() {
  loadGitLog({ since: gitSince.value, until: gitUntil.value, force: true })
}

function resetGitFilter() {
  gitSince.value = ''
  gitUntil.value = ''
  loadGitLog({ since: '', until: '', force: true })
}

function onTabChange(name) {
  if (name === 'gitlog') loadGitLog()
}

function refresh() {
  loadServices()
  loadModules()
}

async function doControl(svc, action) {
  // 本应用弹窗为自研组件，ElMessageBox 不渲染；用原生 confirm（与 Assets.vue 删除一致）
  if (action === 'stop' || action === 'restart') {
    if (!confirm(`确定要${action === 'stop' ? '停止' : '重启'}受管服务「${svc.name}」吗？该操作会中断正在进行的生成链路。`)) return
  }
  try {
    await controlService(svc.name, action)
    ElMessage.success(`已向 ${svc.name} 发送 ${action} 指令`)
    setTimeout(loadServices, 2500)
  } catch (e) {
    ElMessage.error('操作失败：' + (e.message || e))
  }
}

function openSnap(m) {
  snapTarget.value = m
  snapNote.value = ''
  snapDialog.value = true
}
async function confirmSnap() {
  if (!snapTarget.value) return
  loading.value = true
  try {
    const r = await snapshotModule(snapTarget.value.key, snapNote.value)
    ElMessage.success(`已发布版本 ${r.version}`)
    snapDialog.value = false
    loadModules()
  } catch (e) {
    ElMessage.error('发布失败：' + (e.message || e))
  } finally {
    loading.value = false
  }
}

function openRoll(m) {
  if (!m.history.length) {
    ElMessage.warning('该模块暂无历史版本可回退')
    return
  }
  rollTarget.value = m
  rollVersion.value = m.current_version || (m.history[0] && m.history[0].version) || ''
  rollDialog.value = true
}
async function confirmRoll() {
  if (!rollTarget.value || !rollVersion.value) return
  if (!confirm(`确认将「${rollTarget.value.name}」回退到版本 ${rollVersion.value}？将恢复源文件并重建前端。`)) return
  loading.value = true
  try {
    const r = await rollbackModule(rollTarget.value.key, rollVersion.value)
    const buildOk = r.build && r.build.ok
    ElMessage.success(`回退完成（版本 ${r.version}）${buildOk ? '，前端已重建' : '，但前端重建失败，请手动构建'}`)
    rollDialog.value = false
    loadModules()
    setTimeout(loadServices, 2000)
  } catch (e) {
    ElMessage.error('回退失败：' + (e.message || e))
  } finally {
    loading.value = false
  }
}

async function toggleEnabled(m) {
  try {
    await setModuleEnabled(m.key, m.enabled)
    ElMessage.success(m.enabled ? `已启用 ${m.name}` : `已停用 ${m.name}`)
    loadModules()
  } catch (e) {
    ElMessage.error('开关失败：' + (e.message || e))
    m.enabled = !m.enabled
  }
}

onMounted(() => {
  loadServices()
  loadModules()
  if (route.query.disabled) {
    disabledMsg.value = route.query.disabled
    ElMessage.warning(`模块 ${route.query.disabled} 已停用，请从下方「模块版本」中重新启用。`)
    activeTab.value = 'modules'
  }
})
</script>

<template>
  <div class="sysmgr">
    <header class="sm-head">
      <div>
        <h2>🛠️ 服务与版本管理器</h2>
        <p class="sub">业务模块 / 页面功能的版本发布 · 回退 · 启停；后端服务健康与进程管理</p>
      </div>
      <el-button @click="refresh" :loading="loading">刷新</el-button>
    </header>

    <el-alert v-if="disabledMsg" type="warning" :closable="false"
      :title="`模块「${disabledMsg}」当前已停用，访问已被拦截。请在下方「模块版本」中将其启用。`" show-icon />

    <el-tabs v-model="activeTab" class="sm-tabs" @tab-change="onTabChange">
      <!-- 服务 -->
      <el-tab-pane label="服务状态" name="services">
        <el-alert :title="serviceTip" :type="serviceTip === '全部在线' ? 'success' : 'error'"
          :closable="false" show-icon />
        <el-table :data="services" class="sm-table" border>
          <el-table-column prop="name" label="服务" width="160">
            <template #default="{ row }">
              <strong>{{ row.name }}</strong>
              <el-tag v-if="row.managed" size="small" type="info" effect="plain">受管</el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">外部</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <span class="dot" :class="row.status === 'up' ? 'on' : 'off'"></span>
              {{ row.status === 'up' ? '运行中' : '离线' }}
            </template>
          </el-table-column>
          <el-table-column prop="latency_ms" label="延迟(ms)" width="110">
            <template #default="{ row }">{{ row.latency_ms == null ? '—' : row.latency_ms }}</template>
          </el-table-column>
          <el-table-column prop="url" label="探测地址" />
          <el-table-column label="操作" width="240">
            <template #default="{ row }">
              <template v-if="row.managed">
                <el-button size="small" @click="doControl(row, 'restart')">重启</el-button>
                <el-button size="small" type="danger" plain @click="doControl(row, 'stop')">停止</el-button>
                <el-button size="small" type="success" plain @click="doControl(row, 'start')">启动</el-button>
              </template>
              <span v-else class="ext-hint">外部服务，请在宿主机自行管理</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 模块版本 -->
      <el-tab-pane label="模块版本" name="modules">
        <el-table :data="modules" class="sm-table" border row-key="key">
          <el-table-column prop="name" label="业务模块" min-width="200" />
          <el-table-column label="当前版本" width="150">
            <template #default="{ row }">
              <el-tag v-if="row.current_version" type="success">{{ row.current_version }}</el-tag>
              <span v-else class="ext-hint">未发布</span>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="100">
            <template #default="{ row }">
              <el-switch v-model="row.enabled" @change="toggleEnabled(row)" />
            </template>
          </el-table-column>
          <el-table-column label="源文件" min-width="260">
            <template #default="{ row }">
              <div v-for="f in row.files" :key="f" class="fpath">{{ f }}</div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <el-button size="small" type="primary" @click="openSnap(row)">发布快照</el-button>
              <el-button size="small" @click="openRoll(row)">回退</el-button>
            </template>
          </el-table-column>
          <el-table-column type="expand" label="历史" width="70">
            <template #default="{ row }">
              <el-table :data="row.history" size="small" border>
                <el-table-column prop="version" label="版本" width="160" />
                <el-table-column prop="note" label="备注" />
                <el-table-column prop="created_at" label="时间" width="180">
                  <template #default="{ h }">{{ new Date(h.created_at * 1000).toLocaleString() }}</template>
                </el-table-column>
                <el-table-column label="当前" width="80">
                  <template #default="{ h }">
                    <el-tag v-if="h.is_current" size="small" type="success">当前</el-tag>
                  </template>
                </el-table-column>
              </el-table>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 提交记录 -->
      <el-tab-pane label="提交记录" name="gitlog">
        <div class="sm-table-wrap">
          <div class="git-filter">
            <el-date-picker v-model="gitSince" type="date" value-format="YYYY-MM-DD"
              placeholder="起始日期" size="small" :clearable="true" />
            <span class="git-filter-sep">~</span>
            <el-date-picker v-model="gitUntil" type="date" value-format="YYYY-MM-DD"
              placeholder="结束日期" size="small" :clearable="true" />
            <el-button size="small" type="primary" :loading="gitLoading" @click="applyGitFilter">查询</el-button>
            <el-button size="small" @click="resetGitFilter">全部</el-button>
          </div>
          <el-alert v-if="gitError" type="error" :closable="false" :title="gitError" show-icon />
          <div v-else-if="gitLoading" class="git-loading">加载提交记录中…</div>
          <template v-else>
            <div class="git-meta" v-if="gitlog.length">
              分支 <code>{{ gitBranch }}</code> · 共 {{ gitlog.length }} 条提交<span v-if="gitFilterActive">（已按日期筛选）</span>
            </div>
            <el-table :data="gitlog" class="sm-table" border stripe max-height="520">
              <el-table-column prop="short" label="短哈希" width="110">
                <template #default="{ row }"><code class="mono">{{ row.short }}</code></template>
              </el-table-column>
              <el-table-column prop="author" label="作者" width="150" />
              <el-table-column prop="date" label="提交时间" width="200" />
              <el-table-column label="提交说明" min-width="240">
                <template #default="{ row }">
                  <span :title="row.hash">{{ row.message }}</span>
                  <el-tag v-if="row.refs" size="small" type="info" class="ml-1">{{ row.refs }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </template>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 发布快照 -->
    <el-dialog v-model="snapDialog" title="发布模块版本快照" width="460px">
      <p class="dlg-sub">将备份当前源文件，并记录一个新版本（发布后默认启用）。</p>
      <el-input v-model="snapNote" placeholder="版本备注（可选，如：v1 拖拽引用上线）" />
      <template #footer>
        <el-button @click="snapDialog = false">取消</el-button>
        <el-button type="primary" :loading="loading" @click="confirmSnap">发布</el-button>
      </template>
    </el-dialog>

    <!-- 回退 -->
    <el-dialog v-model="rollDialog" title="版本回退" width="460px">
      <p class="dlg-sub">将恢复该版本的源文件并自动重建前端产物。</p>
      <el-select v-model="rollVersion" placeholder="选择目标版本" style="width:100%">
        <el-option v-for="h in (rollTarget && rollTarget.history)" :key="h.version"
          :label="h.version + (h.is_current ? '（当前）' : '') + (h.note ? ' · ' + h.note : '')"
          :value="h.version" />
      </el-select>
      <template #footer>
        <el-button @click="rollDialog = false">取消</el-button>
        <el-button type="warning" :loading="loading" @click="confirmRoll">回退</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.sysmgr { padding: 20px 24px; max-width: 1180px; margin: 0 auto; }
.sm-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.sm-head h2 { margin: 0 0 4px; font-size: 20px; }
.sub { margin: 0; color: var(--sub); font-size: 13px; }
.sm-tabs { margin-top: 12px; }
.sm-table { margin-top: 12px; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; }
.dot.on { background: var(--green); box-shadow: 0 0 8px var(--green); }
.dot.off { background: #e74c3c; }
.fpath { font-size: 12px; color: var(--sub); font-family: monospace; }
.ext-hint { font-size: 12px; color: var(--sub); }
.dlg-sub { margin: 0 0 12px; color: var(--sub); font-size: 13px; }
.git-filter { display: flex; align-items: center; gap: 8px; margin: 4px 0 10px; flex-wrap: wrap; }
.git-filter-sep { color: var(--sub); font-size: 13px; }
.git-meta { margin: 8px 0 10px; font-size: 13px; color: var(--sub); }
.git-meta code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.ml-1 { margin-left: 6px; }
.git-loading { padding: 24px; color: var(--sub); font-size: 13px; }
</style>
