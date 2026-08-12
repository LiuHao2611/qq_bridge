<script setup>
// ============================================================
// 终端用户数据中心（前台 / 8099）
// 严格边界：仅通过 ../api（7788 /api/v1 业务接口）读取本人业务数据，
// 绝不调用任何后台管理平台（8900/8910）的接口或页面。
// 只读：本页所有查询均为 SELECT 性质，无写入、无删除、无管理操作。
// ============================================================
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  listProjects, listAssets, t2iHistory, fetchProfile, estimatePoints, fmtTime,
} from '../api'

// ---- 四个独立查询（一个选项一个查询，单独呈现）----
const tabs = [
  { key: 'projects', label: '我的项目', icon: '🎬', api: 'GET /api/v1/projects', desc: '查询你创建的全部短剧 / 回忆录项目（仅本人业务数据）。' },
  { key: 'assets', label: '我的资产', icon: '🗂️', api: 'GET /api/v1/assets', desc: '查询你名下的角色 / 场景 / 道具等素材资产。' },
  { key: 'clips', label: '我的成片', icon: '🖼️', api: 'GET /api/v1/t2i/tasks', desc: '查询你生成的文生图成片任务与提示词记录。' },
  { key: 'points', label: '我的积分', icon: '🪙', api: 'GET /api/v1/user/profile · /user/points', desc: '查询你的积分余额与单次生成预估消耗。' },
]
const activeTab = ref('projects')

// ---- 数据 ----
const projects = ref([])
const assets = ref([])
const clips = ref([])
const profile = ref(null)      // /user/profile
const estimate = ref(null)     // /user/points 预估

const loading = ref({ projects: false, assets: false, clips: false, points: false })
const error = ref({ projects: '', assets: '', clips: '', points: '' })
const loaded = ref({ projects: false, assets: false, clips: false, points: false })

// ---- 筛选 ----
const searchProjects = ref('')
const searchAssets = ref('')
const assetType = ref('all')
const searchClips = ref('')
const clipStatus = ref('all')

// ---- 加载函数（每个都是独立查询）----
async function loadProjects() {
  loading.value.projects = true; error.value.projects = ''
  try {
    const data = await listProjects({ limit: 200 })
    projects.value = Array.isArray(data) ? data : (data.items || [])
    loaded.value.projects = true
  } catch (e) { error.value.projects = e.message || '加载失败' }
  finally { loading.value.projects = false }
}
async function loadAssets() {
  loading.value.assets = true; error.value.assets = ''
  try {
    const data = await listAssets()
    assets.value = Array.isArray(data) ? data : (data.items || [])
    loaded.value.assets = true
  } catch (e) { error.value.assets = e.message || '加载失败' }
  finally { loading.value.assets = false }
}
async function loadClips() {
  loading.value.clips = true; error.value.clips = ''
  try {
    const r = await t2iHistory(1, 200, '')
    clips.value = (r && r.items) || []
    loaded.value.clips = true
  } catch (e) { error.value.clips = e.message || '加载失败' }
  finally { loading.value.clips = false }
}
async function loadPoints() {
  loading.value.points = true; error.value.points = ''
  try {
    const [prof, est] = await Promise.all([
      fetchProfile().catch(() => null),
      estimatePoints({}).catch(() => null),
    ])
    profile.value = prof
    estimate.value = est
    loaded.value.points = true
  } catch (e) { error.value.points = e.message || '加载失败' }
  finally { loading.value.points = false }
}

const loaders = { projects: loadProjects, assets: loadAssets, clips: loadClips, points: loadPoints }
function ensure(tab) { if (!loaded.value[tab]) loaders[tab]() }
function refreshActive() { loaders[activeTab.value]() }

watch(activeTab, ensure)
onMounted(() => ensure(activeTab.value))

// ---- 筛选后的行 ----
const filteredProjects = computed(() => {
  const q = searchProjects.value.trim().toLowerCase()
  return projects.value.filter(p => !q || (p.name || '').toLowerCase().includes(q))
})
const assetTypes = computed(() => ['all', ...[...new Set(assets.value.map(a => a.type).filter(Boolean))]])
const filteredAssets = computed(() => {
  const q = searchAssets.value.trim().toLowerCase()
  return assets.value.filter(a =>
    (assetType.value === 'all' || a.type === assetType.value) &&
    (!q || (a.name || '').toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q))
  )
})
const clipStatuses = computed(() => ['all', ...[...new Set(clips.value.map(c => c.status).filter(Boolean))]])
const filteredClips = computed(() => {
  const q = searchClips.value.trim().toLowerCase()
  return clips.value.filter(c =>
    (clipStatus.value === 'all' || c.status === clipStatus.value) &&
    (!q || (c.prompt || '').toLowerCase().includes(q))
  )
})

// ---- 顶部概览统计（原始总量，非筛选后）----
const stats = computed(() => [
  { key: 'projects', label: '项目', value: projects.value.length, icon: '🎬' },
  { key: 'assets', label: '资产', value: assets.value.length, icon: '🗂️' },
  { key: 'clips', label: '成片', value: clips.value.length, icon: '🖼️' },
  { key: 'points', label: '积分', value: profile.value?.points_balance ?? '—', icon: '🪙' },
])

// ---- 表格列定义（通用渲染）----
const projectColumns = [
  { label: '项目名称', text: r => r.name || '-' },
  { label: '类型', text: r => r.project_type === 'memoir' ? '回忆录' : '短剧', tag: r => r.project_type === 'memoir' ? 'pur' : 'blue' },
  { label: '镜头数', text: r => (r.progress || 0) },
  { label: '分辨率', text: r => r.resolution || '-' },
  { label: '比例', text: r => r.ratio || '-' },
  { label: '状态', text: r => r.is_draft ? '草稿' : '已发布', tag: r => r.is_draft ? 'gray' : 'green' },
  { label: '更新时间', text: r => fmtTime((r.updated_at || 0) * 1000) },
]
const assetColumns = [
  { label: '名称', text: r => r.name || '-' },
  { label: '类型', text: r => r.type || '-', tag: r => r.type === 'role' ? 'pur' : r.type === 'scene' ? 'blue' : 'gray' },
  { label: '角色类型', text: r => r.role_type || '-' },
  { label: '范围', text: r => r.is_global ? '全局' : '项目内', tag: r => r.is_global ? 'green' : 'gray' },
  { label: '所属项目', text: r => r.project_id ? r.project_id.slice(0, 8) + '…' : '-' },
  { label: '创建时间', text: r => fmtTime((r.created_at || 0) * 1000) },
]
const clipColumns = [
  { label: '预览', img: r => (r.images && r.images[0] && r.images[0].url) || '' },
  { label: '提示词', text: r => r.prompt || '-' },
  { label: '状态', text: r => r.status || '-', tag: r => r.status === 'succeeded' ? 'green' : r.status === 'failed' ? 'red' : 'blue' },
  { label: '进度', text: r => Math.round((r.progress || 0) * 100) + '%' },
  { label: '引擎', text: r => r.orchestrator_used || '-' },
  { label: '生成时间', text: r => fmtTime((r.created_at || 0) * 1000) },
]

const activeColumns = computed(() => {
  if (activeTab.value === 'projects') return projectColumns
  if (activeTab.value === 'assets') return assetColumns
  if (activeTab.value === 'clips') return clipColumns
  return []
})
const activeRows = computed(() => {
  if (activeTab.value === 'projects') return filteredProjects.value
  if (activeTab.value === 'assets') return filteredAssets.value
  if (activeTab.value === 'clips') return filteredClips.value
  return []
})
const activeError = computed(() => error.value[activeTab.value] || '')
const activeLoading = computed(() => loading.value[activeTab.value])

const pointsBalance = computed(() => profile.value?.points_balance ?? null)
const tierLabel = computed(() => {
  const t = profile.value?.membership_tier
  if (t === 'personal') return '个人版'
  return t || '个人版'
})
</script>

<template>
  <div class="page">
    <!-- 页头 -->
    <div class="page-head">
      <div>
        <h1>📊 我的数据</h1>
        <p class="muted">终端用户数据中心 · 仅查询本人业务数据</p>
      </div>
      <button class="btn ghost" :disabled="activeLoading" @click="refreshActive">
        <span v-if="activeLoading">刷新中…</span>
        <span v-else>↻ 刷新当前查询</span>
      </button>
    </div>

    <!-- 只读隔离横幅 -->
    <div class="ro-banner">
      <span class="ro-lock">🔒</span>
      <div class="ro-text">
        <b>只读 · 与后台管理严格隔离</b>
        <span>本页仅通过业务接口（/api/v1）读取你的个人数据，不参与任何后台管理操作，数据不可修改。</span>
      </div>
    </div>

    <!-- 概览统计 -->
    <div class="stat-row">
      <div v-for="s in stats" :key="s.key" class="stat-card" :class="{ on: activeTab === s.key }" @click="activeTab = s.key">
        <span class="stat-icon">{{ s.icon }}</span>
        <div class="stat-meta">
          <div class="stat-value">{{ typeof s.value === 'number' ? s.value.toLocaleString() : s.value }}</div>
          <div class="stat-label">{{ s.label }}</div>
        </div>
      </div>
    </div>

    <!-- 查询切换（一个选项一个查询）-->
    <div class="tabs query-tabs">
      <span
        v-for="t in tabs"
        :key="t.key"
        class="tab"
        :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key"
      >{{ t.icon }} {{ t.label }}</span>
    </div>

    <!-- 当前查询信息卡 -->
    <div class="query-card">
      <div class="qc-left">
        <div class="qc-title">{{ tabs.find(t => t.key === activeTab).label }}查询</div>
        <div class="qc-desc">{{ tabs.find(t => t.key === activeTab).desc }}</div>
      </div>
      <div class="qc-api">
        <span class="qc-api-label">数据源</span>
        <code>{{ tabs.find(t => t.key === activeTab).api }}</code>
        <span class="qc-ro">只读</span>
      </div>
    </div>

    <!-- ============ 积分面板（独立呈现）============ -->
    <div v-if="activeTab === 'points'" class="points-panel">
      <div v-if="activeError" class="err-box">⚠ 查询失败：{{ activeError }}</div>
      <div v-else-if="activeLoading" class="loading-box">正在读取积分数据…</div>
      <template v-else>
        <div class="point-grid">
          <div class="point-main">
            <div class="pm-label">当前积分余额</div>
            <div class="pm-value">{{ pointsBalance !== null ? pointsBalance.toLocaleString() : '—' }}</div>
            <div class="pm-sub">会员版本：{{ tierLabel }} · 永久免费</div>
          </div>
          <div class="point-est">
            <div class="pe-label">单次生成预估消耗</div>
            <div class="pe-value" v-if="estimate">{{ estimate.estimated_credits?.toLocaleString() }} <small>积分</small></div>
            <div class="pe-value" v-else>—</div>
            <div class="pe-formula" v-if="estimate">{{ estimate.formula_explanation }}</div>
          </div>
        </div>
        <p class="muted note">说明：积分由本地模拟维护，仅用于展示与预估，不可在本页修改。</p>
      </template>
    </div>

    <!-- ============ 表格型查询（项目 / 资产 / 成片）============ -->
    <div v-else class="table-wrap">
      <!-- 工具栏：搜索 + 筛选 -->
      <div class="toolbar">
        <input
          v-if="activeTab === 'projects'"
          class="search" v-model="searchProjects" placeholder="搜索项目名称…"
        />
        <input
          v-if="activeTab === 'assets'"
          class="search" v-model="searchAssets" placeholder="搜索资产名称 / 描述…"
        />
        <input
          v-if="activeTab === 'clips'"
          class="search" v-model="searchClips" placeholder="搜索提示词…"
        />
        <select v-if="activeTab === 'assets'" class="select" v-model="assetType">
          <option v-for="t in assetTypes" :key="t" :value="t">{{ t === 'all' ? '全部类型' : t }}</option>
        </select>
        <select v-if="activeTab === 'clips'" class="select" v-model="clipStatus">
          <option v-for="s in clipStatuses" :key="s" :value="s">{{ s === 'all' ? '全部状态' : s }}</option>
        </select>
        <span class="count">共 {{ activeRows.length }} 条</span>
      </div>

      <!-- 错误 / 加载 / 空 / 表格 -->
      <div v-if="activeError" class="err-box">⚠ 查询失败：{{ activeError }}</div>
      <div v-else-if="activeLoading" class="loading-box">正在读取数据…</div>
      <div v-else-if="!activeRows.length" class="empty">暂无数据</div>
      <table v-else class="data-table">
        <thead>
          <tr><th v-for="c in activeColumns" :key="c.label">{{ c.label }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in activeRows" :key="i">
            <td v-for="c in activeColumns" :key="c.label">
              <img v-if="c.img" :src="c.img(row)" class="cell-img" :alt="row.prompt || ''" />
              <span v-else-if="c.tag" class="cell-tag" :class="'t-' + c.tag">{{ c.text ? c.text(row) : (row[c.key] ?? '-') }}</span>
              <span v-else>{{ c.text ? c.text(row) : (row[c.key] ?? '-') }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.page { padding: 24px; }
.page-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }
.page-head h1 { margin: 0; font-size: 22px; }
.muted { color: var(--sub); font-size: 13px; }
.btn { background: var(--grad); color: #fff; border: 0; border-radius: 10px; padding: 9px 16px; font-size: 13px; font-weight: 700; cursor: pointer; }
.btn.ghost { background: var(--card); border: 1px solid var(--line); color: var(--txt); }
.btn:disabled { opacity: .5; cursor: not-allowed; }

/* 只读横幅 */
.ro-banner {
  display: flex; align-items: center; gap: 12px;
  background: rgba(77,159,255,.10); border: 1px solid rgba(77,159,255,.35);
  border-radius: 12px; padding: 12px 16px; margin-bottom: 18px;
}
.ro-lock { font-size: 22px; }
.ro-text { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.ro-text b { color: var(--txt); }
.ro-text span { color: var(--sub); }

/* 概览统计 */
.stat-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 18px; }
.stat-card {
  display: flex; align-items: center; gap: 12px; cursor: pointer;
  background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 16px; transition: .15s;
}
.stat-card:hover { border-color: var(--pur); transform: translateY(-2px); }
.stat-card.on { border-color: var(--pur); box-shadow: 0 0 0 3px rgba(138,92,255,.16); }
.stat-icon { font-size: 24px; }
.stat-meta { line-height: 1.2; }
.stat-value { font-size: 22px; font-weight: 800; color: var(--txt); }
.stat-label { font-size: 12px; color: var(--sub); margin-top: 2px; }

/* 查询切换 */
.tabs { display: flex; gap: 6px; background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 4px; display: inline-flex; }
.tab { padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 14px; color: var(--sub); transition: .15s; }
.tab:hover:not(.active) { color: var(--txt); background: var(--card-2); }
.tab.active { background: var(--grad); color: #fff; font-weight: 700; }
.query-tabs { margin-bottom: 14px; }

/* 查询信息卡 */
.query-card {
  display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
  background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 18px; margin-bottom: 16px;
}
.qc-title { font-size: 15px; font-weight: 700; color: var(--txt); }
.qc-desc { font-size: 12px; color: var(--sub); margin-top: 3px; }
.qc-api { display: flex; align-items: center; gap: 10px; }
.qc-api-label { font-size: 12px; color: var(--sub); }
.qc-api code {
  background: var(--bg-2); border: 1px solid var(--line); border-radius: 8px;
  padding: 5px 10px; font-size: 12px; color: var(--blue); font-family: ui-monospace, Menlo, Consolas, monospace;
}
.qc-ro { font-size: 11px; font-weight: 700; color: var(--green); background: rgba(61,220,132,.15); border: 1px solid rgba(61,220,132,.4); border-radius: 6px; padding: 2px 8px; }

/* 工具栏 */
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.search {
  background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 12px; color: var(--txt); font-size: 13px; outline: none; width: 240px;
}
.search:focus { border-color: var(--pur); }
.select {
  background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 10px; color: var(--txt); font-size: 13px; outline: none;
}
.count { font-size: 12px; color: var(--sub); margin-left: auto; }

/* 表格 */
.table-wrap { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 6px; overflow: auto; }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th {
  text-align: left; color: var(--sub); font-weight: 600; font-size: 12px;
  padding: 12px 14px; border-bottom: 1px solid var(--line); white-space: nowrap;
}
.data-table td { padding: 11px 14px; border-bottom: 1px solid var(--line); color: var(--txt); vertical-align: middle; }
.data-table tbody tr:hover { background: var(--card-2); }
.data-table tbody tr:last-child td { border-bottom: 0; }
.cell-img { width: 88px; height: 50px; object-fit: cover; border-radius: 8px; background: var(--card-2); display: block; }
.cell-tag {
  display: inline-flex; align-items: center; padding: 2px 10px; border-radius: 8px;
  font-size: 12px; border: 1px solid transparent;
}
.cell-tag.t-pur { background: rgba(138,92,255,.15); color: #b79bff; border-color: rgba(138,92,255,.4); }
.cell-tag.t-blue { background: rgba(77,159,255,.15); color: #8fc0ff; border-color: rgba(77,159,255,.4); }
.cell-tag.t-green { background: rgba(61,220,132,.15); color: #7fe3ad; border-color: rgba(61,220,132,.4); }
.cell-tag.t-gray { background: rgba(139,147,167,.15); color: #b3bacb; border-color: rgba(139,147,167,.4); }
.cell-tag.t-red { background: rgba(255,92,92,.15); color: #ff8f8f; border-color: rgba(255,92,92,.4); }

/* 状态盒子 */
.err-box { background: rgba(255,92,92,.10); border: 1px solid rgba(255,92,92,.35); color: #ff8f8f; border-radius: 12px; padding: 16px 18px; font-size: 13px; }
.loading-box { text-align: center; padding: 50px 0; color: var(--sub); font-size: 13px; }
.empty { text-align: center; padding: 50px 0; color: var(--sub); }

/* 积分面板 */
.points-panel { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 20px; }
.point-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.point-main, .point-est {
  background: var(--bg-2); border: 1px solid var(--line); border-radius: 12px; padding: 18px 20px;
}
.pm-label, .pe-label { font-size: 12px; color: var(--sub); }
.pm-value { font-size: 34px; font-weight: 800; background: var(--grad); -webkit-background-clip: text; background-clip: text; color: transparent; margin: 6px 0; }
.pm-sub { font-size: 12px; color: var(--sub); }
.pe-value { font-size: 28px; font-weight: 800; color: var(--txt); margin: 6px 0; }
.pe-value small { font-size: 13px; color: var(--sub); font-weight: 600; }
.pe-formula { font-size: 12px; color: var(--sub); font-family: ui-monospace, Menlo, Consolas, monospace; line-height: 1.6; word-break: break-all; }
.note { margin-top: 16px; line-height: 1.6; }

@media (max-width: 760px) {
  .stat-row { grid-template-columns: repeat(2, 1fr); }
  .point-grid { grid-template-columns: 1fr; }
}
</style>
