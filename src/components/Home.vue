<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getDashboard, listProjects, createProject, coverStyle, STATUS_TXT } from '../api'
import { mockHome } from '../mock'
import { toast } from '../store'

const router = useRouter()
const dashboard = ref({})
const recent = ref(mockHome.recent)
const loading = ref(true)

const adaptProject = (p) => ({
  id: p.project_id,
  title: p.name,
  type: p.project_type === 'memoir' ? '回忆录' : '短剧',
  status: p.is_draft ? 'draft' : 'done',
  scene_count: p.progress || 0,
})

const load = async () => {
  loading.value = true
  try {
    const [dash, projects] = await Promise.all([
      getDashboard(),
      listProjects({ limit: 6 }),
    ])
    dashboard.value = dash || {}
    if (projects.length) recent.value = projects.map(adaptProject)
  } catch (e) {
    console.warn('dashboard backend failed, fallback to mock', e.message)
    dashboard.value = {
      stats: [
        { label: '总项目数', value: 4, sub: '剧本/回忆录总数' },
        { label: '今日 AI 调用', value: 0, sub: 'provider_calls' },
        { label: '今日成功任务', value: 0, sub: 'cloud_jobs_succeeded' },
        { label: '本月积分消耗', value: 0, sub: 'credit_ledger' },
        { label: '本月厂商成本', value: '¥0.00', sub: 'provider_calls_cost' },
        { label: '7日活跃设备', value: 1, sub: '实时设备心跳' },
      ],
      trend: [],
      credit_trend: [],
      package_distribution: [{ name: '个人版', value: 1 }],
      recent_failed_tasks: [],
      provider_health: [],
    }
  } finally {
    loading.value = false
  }
}
onMounted(load)

const stats = computed(() => dashboard.value.stats || [])
const trend = computed(() => dashboard.value.trend || [])
const creditTrend = computed(() => dashboard.value.credit_trend || [])
const packages = computed(() => dashboard.value.package_distribution || [])
const failedTasks = computed(() => dashboard.value.recent_failed_tasks || [])
const providers = computed(() => dashboard.value.provider_health || [])

const idea = ref('')
const ideaType = ref('短剧')
const startCreate = async () => {
  if (!idea.value.trim()) { toast('请输入创意描述'); return }
  try {
    const p = await createProject({
      name: idea.value.trim().slice(0, 40),
      project_type: ideaType.value === '回忆录' ? 'memoir' : 'short_drama',
      ratio: '16:9',
      resolution: '720p',
    })
    router.push('/projects/' + p.project_id)
  } catch (e) {
    toast('创建失败: ' + (e.message || e))
  }
}
const quickNew = async () => {
  try {
    const p = await createProject({ name: '未命名项目', project_type: 'short_drama', ratio: '16:9', resolution: '720p' })
    router.push('/projects/' + p.project_id)
  } catch (e) {
    toast('创建失败: ' + (e.message || e))
  }
}
const open = (id, type) => router.push(type === '回忆录' ? '/memoir/' + id : '/projects/' + id)

// SVG chart helpers
const linePath = (data, key, width, height) => {
  if (!data.length) return ''
  const vals = data.map((d) => d[key] || 0)
  const max = Math.max(...vals, 1)
  const stepX = width / (data.length - 1)
  return data
    .map((d, i) => {
      const x = i * stepX
      const y = height - (d[key] || 0) / max * height
      return `${i === 0 ? 'M' : 'L'} ${x} ${y}`
    })
    .join(' ')
}
const areaPath = (data, key, width, height) => {
  const p = linePath(data, key, width, height)
  if (!p) return ''
  return `${p} L ${width} ${height} L 0 ${height} Z`
}
const pieSlices = (data, radius) => {
  const total = data.reduce((s, d) => s + d.value, 0) || 1
  let acc = 0
  return data.map((d) => {
    const start = acc / total * Math.PI * 2 - Math.PI / 2
    acc += d.value
    const end = acc / total * Math.PI * 2 - Math.PI / 2
    const x1 = radius + radius * Math.cos(start)
    const y1 = radius + radius * Math.sin(start)
    const x2 = radius + radius * Math.cos(end)
    const y2 = radius + radius * Math.sin(end)
    const large = end - start > Math.PI ? 1 : 0
    return {
      ...d,
      d: `M ${radius} ${radius} L ${x1} ${y1} A ${radius} ${radius} 0 ${large} 1 ${x2} ${y2} Z`,
    }
  })
}

const formatNumber = (n) => {
  if (typeof n === 'number') return n.toLocaleString()
  return n
}
</script>

<template>
  <div class="page home">
    <!-- top bar -->
    <div class="dash-header">
      <div class="dash-title">
        <h1>创作驾驶舱</h1>
        <div class="muted">数据实时更新于 {{ dashboard.updated_at || '-' }}</div>
      </div>
      <div class="dash-actions">
        <el-input v-model="idea" placeholder="一句话开启 AI 短剧…" class="dash-search" clearable @keyup.enter="startCreate">
          <template #append>
            <el-button type="primary" @click="startCreate">创作</el-button>
          </template>
        </el-input>
        <el-radio-group v-model="ideaType" size="small">
          <el-radio-button value="短剧">短剧</el-radio-button>
          <el-radio-button value="回忆录">人生回忆录</el-radio-button>
        </el-radio-group>
        <el-button type="primary" size="small" @click="quickNew">＋ 新建项目</el-button>
      </div>
    </div>

    <!-- stats cards -->
    <div class="stat-row">
      <div v-for="s in stats" :key="s.key" class="stat-card">
        <div class="stat-label">{{ s.label }}</div>
        <div class="stat-value">{{ formatNumber(s.value) }}</div>
        <div class="stat-sub muted">{{ s.sub }}</div>
      </div>
      <div v-if="!stats.length" v-for="i in 6" :key="i" class="stat-card skeleton">
        <div class="stat-label">—</div>
        <div class="stat-value">—</div>
        <div class="stat-sub muted">—</div>
      </div>
    </div>

    <!-- middle charts -->
    <div class="chart-row">
      <div class="chart-card wide">
        <div class="chart-head">
          <h3>关键指标趋势</h3>
          <div class="legend">
            <span><i class="dot blue" /> 项目创建</span>
            <span><i class="dot pink" /> AI 调用</span>
            <span><i class="dot green" /> 成功任务</span>
            <span><i class="dot warn" /> 积分消耗</span>
          </div>
        </div>
        <div class="chart-body">
          <svg v-if="trend.length" viewBox="0 0 640 220" preserveAspectRatio="none" class="line-chart">
            <defs>
              <linearGradient id="gradBlue" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--blue)" stop-opacity="0.35"/><stop offset="100%" stop-color="var(--blue)" stop-opacity="0"/></linearGradient>
              <linearGradient id="gradPink" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--pink)" stop-opacity="0.35"/><stop offset="100%" stop-color="var(--pink)" stop-opacity="0"/></linearGradient>
              <linearGradient id="gradGreen" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--green)" stop-opacity="0.35"/><stop offset="100%" stop-color="var(--green)" stop-opacity="0"/></linearGradient>
              <linearGradient id="gradWarn" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--warn)" stop-opacity="0.35"/><stop offset="100%" stop-color="var(--warn)" stop-opacity="0"/></linearGradient>
            </defs>
            <path :d="areaPath(trend, 'projects', 640, 200)" fill="url(#gradBlue)" />
            <path :d="linePath(trend, 'projects', 640, 200)" fill="none" stroke="var(--blue)" stroke-width="2" />
            <path :d="areaPath(trend, 'ai_calls', 640, 200)" fill="url(#gradPink)" />
            <path :d="linePath(trend, 'ai_calls', 640, 200)" fill="none" stroke="var(--pink)" stroke-width="2" />
            <path :d="areaPath(trend, 'success', 640, 200)" fill="url(#gradGreen)" />
            <path :d="linePath(trend, 'success', 640, 200)" fill="none" stroke="var(--green)" stroke-width="2" />
            <path :d="areaPath(trend, 'credits', 640, 200)" fill="url(#gradWarn)" />
            <path :d="linePath(trend, 'credits', 640, 200)" fill="none" stroke="var(--warn)" stroke-width="2" />
            <g v-for="(d,i) in trend" :key="i">
              <text :x="i * (640/(trend.length-1))" y="215" text-anchor="middle" fill="var(--sub)" font-size="10">{{ d.date }}</text>
            </g>
          </svg>
          <div v-else class="empty-chart">暂无趋势数据</div>
        </div>
      </div>

      <div class="chart-card">
        <div class="chart-head"><h3>套餐分布</h3></div>
        <div class="chart-body center">
          <svg v-if="packages.length" viewBox="0 0 180 180" class="donut-chart">
            <g v-for="(slice,i) in pieSlices(packages, 90)" :key="i">
              <path :d="slice.d" :fill="['var(--pur)','var(--blue)','var(--green)','var(--warn)','var(--pink)'][i % 5]" stroke="var(--card)" stroke-width="2" />
            </g>
            <circle cx="90" cy="90" r="54" fill="var(--card)" />
            <text x="90" y="85" text-anchor="middle" fill="var(--txt)" font-size="22" font-weight="700">{{ packages.reduce((s,d)=>s+d.value,0) }}</text>
            <text x="90" y="105" text-anchor="middle" fill="var(--sub)" font-size="11">总数</text>
          </svg>
          <div v-else class="empty-chart">暂无套餐数据</div>
          <div v-if="packages.length" class="donut-legend">
            <div v-for="(p,i) in packages" :key="i" class="dl-item">
              <i class="dot" :style="{background:['var(--pur)','var(--blue)','var(--green)','var(--warn)','var(--pink)'][i%5]}" />
              <span>{{ p.name }}</span>
              <b>{{ p.value }}</b>
            </div>
          </div>
        </div>
      </div>

      <div class="chart-card">
        <div class="chart-head"><h3>积分消耗趋势</h3></div>
        <div class="chart-body">
          <svg v-if="creditTrend.length" viewBox="0 0 300 220" preserveAspectRatio="none" class="line-chart">
            <defs>
              <linearGradient id="gradCredit" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--pur)" stop-opacity="0.4"/><stop offset="100%" stop-color="var(--pur)" stop-opacity="0"/></linearGradient>
            </defs>
            <path :d="areaPath(creditTrend, 'credits', 300, 200)" fill="url(#gradCredit)" />
            <path :d="linePath(creditTrend, 'credits', 300, 200)" fill="none" stroke="var(--pur)" stroke-width="2" />
            <g v-for="(d,i) in creditTrend" :key="i">
              <text :x="i * (300/(creditTrend.length-1))" y="215" text-anchor="middle" fill="var(--sub)" font-size="10">{{ d.date }}</text>
            </g>
          </svg>
          <div v-else class="empty-chart">暂无积分数据</div>
        </div>
      </div>
    </div>

    <!-- bottom lists -->
    <div class="list-row">
      <div class="list-card">
        <div class="list-head"><h3>最近项目</h3><el-button size="small" text @click="quickNew">＋ 新建</el-button></div>
        <div class="list-body">
          <div v-for="p in recent" :key="p.id" class="list-item" @click="open(p.id, p.type)">
            <div class="list-thumb" :style="coverStyle(p.id)"><span class="mini-type">{{ p.type }}</span></div>
            <div class="list-info">
              <div class="list-title">{{ p.title }}</div>
              <div class="list-meta muted">{{ p.scene_count }} 镜头 · {{ STATUS_TXT[p.status] || '草稿' }}</div>
            </div>
          </div>
          <div v-if="!recent.length" class="empty-mini">暂无项目</div>
        </div>
      </div>

      <div class="list-card">
        <div class="list-head"><h3>最近失败任务</h3></div>
        <div class="list-body">
          <div v-for="t in failedTasks" :key="t.task_id" class="list-item fail">
            <div class="list-dot fail" />
            <div class="list-info">
              <div class="list-title">{{ t.project_name || '未命名项目' }}</div>
              <div class="list-meta muted">{{ t.error_code || 'GENERATION_FAILED' }} · {{ t.error_message || '生成失败' }}</div>
            </div>
            <div class="list-time muted">{{ t.finished_at ? new Date(t.finished_at*1000).toLocaleString() : '-' }}</div>
          </div>
          <div v-if="!failedTasks.length" class="empty-mini">暂无失败任务</div>
        </div>
      </div>

      <div class="list-card">
        <div class="list-head"><h3>厂商健康状态</h3></div>
        <div class="list-body">
          <div v-for="p in providers" :key="p.name" class="list-item provider">
            <div class="list-dot" :class="p.status" />
            <div class="list-info">
              <div class="list-title">{{ p.name }}</div>
              <div class="list-meta muted">{{ p.latency_ms }}ms · 可用性 {{ (p.availability*100).toFixed(0) }}%</div>
            </div>
            <span class="status-tag" :class="p.status">{{ p.status === 'healthy' ? '健康' : '未配置' }}</span>
          </div>
          <div v-if="!providers.length" class="empty-mini">暂无厂商数据</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home { width: 100%; max-width: none; margin: 0 auto; padding-bottom: 40px; }
.dash-header { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 24px; flex-wrap: wrap; }
.dash-title h1 { font-size: 24px; margin: 0 0 6px; }
.dash-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.dash-search { width: 420px; }
:deep(.dash-search .el-input__wrapper) { background: var(--card); box-shadow: 0 0 0 1px var(--line) inset; }
:deep(.dash-search .el-input__inner) { color: var(--txt); }
:deep(.dash-search .el-input-group__append) { background: var(--pur); border-color: var(--pur); color: #fff; padding: 0 18px; }

.stat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 18px; margin-bottom: 24px; }
.stat-card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 20px; transition: .2s; }
.stat-card:hover { border-color: var(--pur); transform: translateY(-2px); }
.stat-label { font-size: 14px; color: var(--sub); margin-bottom: 10px; }
.stat-value { font-size: 30px; font-weight: 800; color: var(--txt); margin-bottom: 6px; }
.stat-sub { font-size: 12px; }
.stat-card.skeleton { opacity: .5; }

.chart-row { display: grid; grid-template-columns: 2.5fr 1fr 1fr; gap: 18px; margin-bottom: 24px; }
.chart-card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 20px; display: flex; flex-direction: column; }
.chart-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.chart-head h3 { margin: 0; font-size: 16px; }
.chart-head .legend { display: flex; gap: 14px; font-size: 12px; color: var(--sub); }
.chart-head .legend span { display: flex; align-items: center; gap: 5px; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; }
.dot.blue { background: var(--blue); }
.dot.pink { background: var(--pink); }
.dot.green { background: var(--green); }
.dot.warn { background: var(--warn); }
.chart-body { flex: 1; min-height: 260px; position: relative; }
.chart-body.center { display: flex; align-items: center; justify-content: center; gap: 24px; flex-wrap: wrap; }
.line-chart { width: 100%; height: 100%; }
.donut-chart { width: 160px; height: 160px; }
.donut-legend { display: flex; flex-direction: column; gap: 10px; }
.dl-item { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--sub); min-width: 100px; }
.dl-item b { margin-left: auto; color: var(--txt); }
.empty-chart { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--sub); font-size: 13px; }

.list-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
.list-card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 20px; display: flex; flex-direction: column; min-height: 340px; }
.list-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.list-head h3 { margin: 0; font-size: 16px; }
.list-body { flex: 1; overflow-y: auto; }
.list-item { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--line); cursor: pointer; transition: .15s; }
.list-item:last-child { border-bottom: 0; }
.list-item:hover { opacity: .85; }
.list-thumb { width: 48px; height: 48px; border-radius: 12px; position: relative; overflow: hidden; flex: 0 0 48px; }
.mini-type { position: absolute; top: 3px; left: 3px; font-size: 9px; padding: 1px 5px; border-radius: 4px; background: rgba(0,0,0,.55); color: #fff; }
.list-info { flex: 1; min-width: 0; }
.list-title { font-size: 15px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.list-meta { font-size: 13px; margin-top: 3px; }
.list-dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }
.list-dot.fail { background: var(--pink); box-shadow: 0 0 6px var(--pink); }
.list-dot.healthy { background: var(--green); box-shadow: 0 0 6px var(--green); }
.list-dot.unconfigured { background: var(--warn); }
.list-time { font-size: 12px; white-space: nowrap; }
.status-tag { font-size: 12px; padding: 3px 10px; border-radius: 10px; }
.status-tag.healthy { background: rgba(61,220,132,.15); color: var(--green); }
.status-tag.unconfigured { background: rgba(255,176,32,.15); color: var(--warn); }
.empty-mini { text-align: center; padding: 50px 0; color: var(--sub); font-size: 13px; }

@media (max-width: 1500px) {
  .stat-row { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
  .chart-row { grid-template-columns: 1.8fr 1fr 1fr; }
}
@media (max-width: 1200px) {
  .chart-row { grid-template-columns: 1fr 1fr; }
  .chart-card.wide { grid-column: 1 / -1; }
  .list-row { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 900px) {
  .stat-row { grid-template-columns: repeat(2, 1fr); }
  .chart-row { grid-template-columns: 1fr; }
  .list-row { grid-template-columns: 1fr; }
  .dash-search { width: 100%; }
}
</style>
