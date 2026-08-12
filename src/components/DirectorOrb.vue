<script setup>
import { computed, ref, watch, reactive, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  useDirectorOrb,
  directorOrbInit,
  directorOrbSetPhase,
  fetchStageStats,
  NODE_TXT,
  NODE_ORDER,
  PHASE_SKILLS,
  visibleSuggestions,
  decisionItems,
  unreadCount,
  dismissSuggestion,
  runOrbBatch,
  resolveDecision,
} from '../composables/useDirectorOrb'

const router = useRouter()

const route = useRoute()
const { state } = useDirectorOrb()

const pid = computed(() => route.params.id || '')
const open = ref(false)

// 五阶段路由映射
const STAGE_MAP = {
  '/assets': { step: 1, label: '资产初始化' },
  '/planning': { step: 2, label: '项目策划' },
  '/editor': { step: 3, label: '分镜编排' },
  '/production': { step: 4, label: '生成生产' },
}

const currentStage = computed(() => {
  for (const suf of Object.keys(STAGE_MAP)) {
    if (route.path.endsWith(suf)) return STAGE_MAP[suf]
  }
  return null
})
const visible = computed(() => !!pid.value && !!currentStage.value)

// 有效阶段：路由阶段 + 导演运行时动态感知（P4 页面内进入 N06–N09 时自动切换 P5）
const effectiveStep = computed(() => {
  const base = currentStage.value?.step || 1
  if (base === 4 && state.runStatus !== 'idle') {
    const late = ['N06', 'N07', 'N08', 'N09']
    const anyLate = late.some((n) => state.nodes[n]?.status === 'running' || state.nodes[n]?.status === 'done')
    if (anyLate) return 5
  }
  return base
})

// 同步当前阶段到状态机
watch(
  () => [pid.value, route.path, effectiveStep.value],
  () => {
    if (visible.value) {
      directorOrbInit(pid.value, effectiveStep.value)
      directorOrbSetPhase(effectiveStep.value)
    }
  },
  { immediate: true }
)

// 当前阶段技能
const phaseKey = computed(() => (effectiveStep.value ? `P${effectiveStep.value}` : null))
const phaseInfo = computed(() => (phaseKey.value ? PHASE_SKILLS[phaseKey.value] : null))

// 技能状态判定（DAG 节点优先，其次阶段数据就绪度）
function logicStatusOf(item) {
  if (item.nodes && item.nodes.length && state.runStatus !== 'idle') {
    let anyRun = false, anyDone = false, anyFail = false
    for (const n of item.nodes) {
      const ns = state.nodes[n] && state.nodes[n].status
      if (ns === 'running') anyRun = true
      else if (ns === 'done') anyDone = true
      else if (ns === 'failed') anyFail = true
    }
    if (anyRun) return 'running'
    if (anyFail && !anyDone) return 'failed'
    if (anyDone) return 'done'
  }
  const ss = state.stageStats
  const step = effectiveStep.value
  if (ss) {
    if (step === 1 && ss.assets && ss.assets.length) return 'done'
    if ((step === 2 || step === 3) && ss.segments && ss.segments.length) return 'done'
    if (step === 4 && ss.production && ss.production.segments && ss.production.segments.length) return 'done'
    if (step === 5 && state.result && state.result.quality_report) return 'done'
  }
  return 'idle'
}
const STATUS_LIT_TXT = { idle: '待触发', running: '进行中', done: '就绪', failed: '异常' }

// 阶段进度指标
const stageMetrics = computed(() => {
  const step = effectiveStep.value
  const ss = state.stageStats || {}
  const segs = ss.segments || []
  const assets = ss.assets || []
  const prod = ss.production || {}
  const prodSegs = prod.segments || []
  const r = state.result || {}
  const cfg = r.asset_inject_config || {}
  const reports = (r.quality_report && r.quality_report.reports) || []
  const pass = reports.filter((x) => x.pass).length
  if (step === 1) {
    return [
      { label: '资产总数', value: assets.length, tone: assets.length ? 'primary' : 'muted' },
      { label: '角色', value: assets.filter((a) => a.type === 'role').length, tone: 'info' },
      { label: '场景', value: assets.filter((a) => a.type === 'scene').length, tone: 'info' },
      { label: '道具', value: assets.filter((a) => a.type === 'prop').length, tone: 'info' },
    ]
  }
  if (step === 2) {
    const bound = segs.filter((s) => (s.asset_refs || []).length > 0).length
    return [
      { label: '分镜数', value: segs.length, tone: segs.length ? 'primary' : 'muted' },
      { label: '已绑定资产', value: bound, tone: bound ? 'ok' : 'muted' },
      { label: '含分镜头', value: segs.filter((s) => (s.shots || []).length > 0).length, tone: 'info' },
    ]
  }
  if (step === 3) {
    return [
      { label: '风格参考图', value: r.global_style_ref ? '已生成' : '未生成', tone: r.global_style_ref ? 'ok' : 'muted' },
      { label: '真人资产化', value: cfg.enable_real_asset ? '已启用' : '未启用', tone: cfg.enable_real_asset ? 'ok' : 'muted' },
      { label: '角色注入数', value: cfg.roles ? Object.keys(cfg.roles).length : 0, tone: 'info' },
    ]
  }
  if (step === 4) {
    let q = 0, r0 = 0, d = 0, f = 0
    for (const s of prodSegs) {
      const t = s.latest_task || s.task || {}
      const st = t.status || 'pending'
      if (st === 'queued' || st === 'pending') q++
      else if (st === 'running') r0++
      else if (st === 'succeeded' || st === 'done') d++
      else if (st === 'failed' || st === 'error') f++
    }
    return [
      { label: '排队', value: q, tone: 'muted' },
      { label: '生成中', value: r0, tone: r0 ? 'running' : 'muted' },
      { label: '已完成', value: d, tone: d ? 'ok' : 'muted' },
      { label: '失败', value: f, tone: f ? 'bad' : 'muted' },
    ]
  }
  if (step === 5) {
    return [
      { label: '品控合格', value: pass, tone: 'ok' },
      { label: '品控不合格', value: reports.length - pass, tone: reports.length - pass ? 'bad' : 'muted' },
      { label: '失败分镜', value: (r.failed_shot_list || []).length, tone: (r.failed_shot_list || []).length ? 'bad' : 'muted' },
    ]
  }
  return []
})

// 实时小任务 + 折叠区
const expanded = reactive({ live: true })
function toggleFeed(id) { expanded[id] = !expanded[id] }
function toggleSection(key) { expanded[key] = !expanded[key] }
const activeFeedCount = computed(() => state.feed.filter((f) => f.status === 'running').length)

// M3：待确认决策点（排除已解决/已跳过）
const pendingDecisions = computed(() =>
  state.decisions.filter((d) => d.status !== 'resolved' && d.status !== 'skipped'))

// 折叠区
const showDag = ref(false)
const showLogs = ref(false)
const showAlerts = ref(false)
function kindIcon(kind) {
  const map = {
    text: '文',
    image: '图',
    video: '视',
    audio: '音',
    meta: '控',
  }
  return map[kind] || '·'
}
function statusText(status) {
  return { running: '生成中', done: '完成', failed: '失败' }[status] || status
}

function fmtTime(ts) {
  const d = new Date(ts)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/* ===================== DirectorOrb v2 —— 管家化身层 ===================== */
// 悬浮球按钮悬浮提示（随六态机变化）
const orbTitle = computed(() => {
  const map = {
    idle: '待命', watching: '监督中', suggestion: '有管家建议',
    warning: '需注意', error: '需决策', success: '已完成',
  }
  return (map[state.orbState] || '待命') + ' · 拖动可移动 · 点击查看导演管家'
})

function sevTxt(s) { return { suggestion: '建议', warning: '提醒', error: '错误' }[s] || '建议' }
function goPath(p) { if (p) router.push(p) }

// 阶段核心视图派生（严格对齐 orb/routes.py 返回字段）
const p1Core = computed(() => {
  const r = state.readiness
  if (!r) return null
  return {
    score: r.readiness_score,
    groups: [
      { label: '角色', ready: r.characters.ready, total: r.characters.total },
      { label: '场景', ready: r.scenes.ready, total: r.scenes.total },
      { label: '道具', ready: r.props.ready, total: r.props.total },
    ],
    missing: r.missing_role_segments || [],
  }
})
const p2Core = computed(() => {
  const s = state.scriptAnalysis
  if (!s) return null
  return {
    segment_count: s.segment_count,
    consecutive: s.consecutive_same_scene || [],
    empty: s.empty_description_segments || [],
    appearances: s.character_appearances || {},
  }
})
const p4Core = computed(() => state.runSummary || null)
const p5Core = computed(() => state.synthReport || null)

// 0-1 成片
const qrReports = computed(() => (state.result && state.result.quality_report && state.result.quality_report.reports) || [])
const qrPass = computed(() => qrReports.value.filter((r) => r.pass).length)
const styleRef = computed(() => (state.result && state.result.global_style_ref) || '')
const failedShots = computed(() => (state.result && state.result.failed_shot_list) || [])

/* ===================== 悬浮球拖动 + 磁吸边框 ===================== */
const BTN = 56
const MARGIN = 12
const SNAP_DIST = 56
const DRAG_THRESHOLD = 5
const LS_KEY = 'manju_director_orb_pos'

const pos = reactive(loadPos())
const dragging = ref(false)
const magnetEdge = ref(null)
let startPX = 0, startPY = 0, startX = 0, startY = 0, moved = false

function defaultPos() {
  const W = window.innerWidth, H = window.innerHeight
  return { x: W - BTN - MARGIN, y: H - BTN - MARGIN }
}
function loadPos() {
  let p = defaultPos()
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const o = JSON.parse(raw)
      if (typeof o.x === 'number' && typeof o.y === 'number') p = o
    }
  } catch (e) { /* ignore */ }
  return clampPos(p)
}
function clampPos(p) {
  const o = p || pos
  const W = window.innerWidth, H = window.innerHeight
  o.x = Math.max(0, Math.min(o.x, W - BTN))
  o.y = Math.max(0, Math.min(o.y, H - BTN))
  return o
}
function savePos() {
  try { localStorage.setItem(LS_KEY, JSON.stringify({ x: Math.round(pos.x), y: Math.round(pos.y) })) } catch (e) { /* ignore */ }
}
function nearestEdge() {
  const W = window.innerWidth, H = window.innerHeight
  const dL = pos.x, dR = (W - BTN) - pos.x, dT = pos.y, dB = (H - BTN) - pos.y
  const edges = [['left', dL], ['right', dR], ['top', dT], ['bottom', dB]]
  edges.sort((a, b) => a[1] - b[1])
  return edges
}
function onPointerDown(e) {
  if (e.button !== undefined && e.button !== 0) return
  startPX = e.clientX; startPY = e.clientY
  startX = pos.x; startY = pos.y
  moved = false
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
  e.preventDefault()
}
function onPointerMove(e) {
  const dx = e.clientX - startPX, dy = e.clientY - startPY
  if (!moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) { moved = true; dragging.value = true }
  if (!dragging.value) return
  pos.x = startX + dx
  pos.y = startY + dy
  clampPos()
  const edges = nearestEdge()
  magnetEdge.value = edges[0][1] < SNAP_DIST ? edges[0][0] : null
}
function onPointerUp() {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  if (dragging.value) {
    const W = window.innerWidth, H = window.innerHeight
    const dL = pos.x, dR = (W - BTN) - pos.x, dT = pos.y, dB = (H - BTN) - pos.y
    if (dL < SNAP_DIST) pos.x = MARGIN
    else if (dR < SNAP_DIST) pos.x = W - BTN - MARGIN
    if (dT < SNAP_DIST) pos.y = MARGIN
    else if (dB < SNAP_DIST) pos.y = H - BTN - MARGIN
    savePos()
  } else {
    open.value = !open.value
  }
  dragging.value = false
  magnetEdge.value = null
}
function onResize() { clampPos() }

const panelStyle = computed(() => {
  const W = window.innerWidth, H = window.innerHeight
  const cx = pos.x + BTN / 2, cy = pos.y + BTN / 2
  const s = {}
  if (cx > W / 2) { s.right = '0px'; s.left = 'auto' } else { s.left = '0px'; s.right = 'auto' }
  if (cy > H / 2) { s.bottom = (BTN + 12) + 'px'; s.top = 'auto' } else { s.top = (BTN + 12) + 'px'; s.bottom = 'auto' }
  return s
})

function onDocPointerDown(e) {
  if (open.value && e.target && !(e.target.closest && e.target.closest('.dir-orb-root'))) {
    open.value = false
  }
}
function onKeydown(e) {
  if (e.key === 'Escape' && open.value) open.value = false
}
watch(open, (v) => {
  if (v) {
    document.addEventListener('pointerdown', onDocPointerDown)
    document.addEventListener('keydown', onKeydown)
  } else {
    document.removeEventListener('pointerdown', onDocPointerDown)
    document.removeEventListener('keydown', onKeydown)
  }
})

onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  window.removeEventListener('resize', onResize)
  document.removeEventListener('pointerdown', onDocPointerDown)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div v-if="visible" class="dir-orb-root" :class="{ dragging }" :style="{ left: pos.x + 'px', top: pos.y + 'px' }">
    <div v-if="magnetEdge" class="orb-magnet" :class="magnetEdge"></div>

    <transition name="orb-pop">
      <div v-if="open" class="orb-panel" :style="panelStyle" @click.stop>
        <!-- Header -->
        <div class="orb-head">
          <div class="orb-title">
            <span class="orb-icon">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="7" width="20" height="10" rx="2" />
                <path d="M7 7v10M17 7v10M2 12h20" />
                <circle cx="7" cy="12" r="1.5" fill="currentColor" stroke="none" />
                <circle cx="17" cy="12" r="1.5" fill="currentColor" stroke="none" />
              </svg>
            </span>
            <span>导演 Agent</span>
          </div>
          <div class="orb-head-right">
            <span v-if="currentStage" class="orb-stage-tag">阶段 {{ effectiveStep }}</span>
            <button class="orb-x" @click="open = false" title="收起">×</button>
          </div>
        </div>

        <!-- 五阶段导航条 -->
        <div class="phase-track">
          <div
            v-for="i in 5"
            :key="i"
            class="phase-node"
            :class="{
              active: effectiveStep === i,
              done: effectiveStep > i,
              future: effectiveStep < i,
            }"
            :title="['资产初始化','项目策划','分镜编排','生成生产','合成导出'][i-1]"
          >
            <span class="pn-dot">
              <svg v-if="effectiveStep > i" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="4 12 9 17 20 6" />
              </svg>
              <span v-else>{{ i }}</span>
            </span>
            <span class="pn-label">{{ ['P1','P2','P3','P4','P5'][i-1] }}</span>
          </div>
        </div>

        <!-- 当前阶段主视图 -->
        <div v-if="phaseInfo" class="phase-hero">
          <div class="ph-title-row">
            <span class="ph-step">{{ phaseInfo.key }}</span>
            <span class="ph-title">{{ phaseInfo.title }}</span>
          </div>
          <div class="ph-subtitle">{{ phaseInfo.subtitle }}</div>

          <!-- 导演运行总进度 -->
          <div v-if="state.runStatus !== 'idle'" class="orb-progress">
            <div class="ob-pbar"><div class="ob-pfill" :class="state.runStatus" :style="{ width: state.progress + '%' }"></div></div>
            <span class="ob-pnum">{{ state.progress }}%</span>
          </div>

          <!-- 当前阶段技能清单 -->
          <div class="skills-card">
            <div class="skills-head">
              <span class="sh-label">当前阶段能力</span>
              <span class="sh-agent">{{ phaseInfo.agents }}</span>
            </div>
            <div class="skills-list">
              <div
                v-for="it in phaseInfo.items"
                :key="it.id"
                class="skill-item"
                :class="logicStatusOf(it)"
              >
                <span class="si-dot"></span>
                <div class="si-body">
                  <div class="si-name">{{ it.name }}</div>
                  <div class="si-desc">{{ it.desc }}</div>
                </div>
                <span class="si-status">{{ STATUS_LIT_TXT[logicStatusOf(it)] }}</span>
              </div>
            </div>
          </div>

          <!-- 阶段指标 -->
          <div v-if="stageMetrics.length" class="metrics-row">
            <div v-for="(m, i) in stageMetrics" :key="i" class="metric-cell" :class="m.tone">
              <div class="metric-value">{{ m.value }}</div>
              <div class="metric-label">{{ m.label }}</div>
            </div>
          </div>
        </div>

        <!-- ===== DirectorOrb v2 管家视图（化身层 · 信息架构优先级最高） ===== -->
        <div v-if="state.orbEnabled" class="orb-persona">

          <!-- ===== M3 悬浮球前端化：三卡（决策 / 选型 / 对话） ===== -->

          <!-- 决策卡：A 类决策请示（director.decision_request），点选回传 resolve -->
          <div class="persona-decision m3-decision">
            <div class="pd-title">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="12" y1="8" x2="12" y2="13"/><line x1="12" y1="16.5" x2="12.01" y2="16.5"/></svg>
              待确认决策（{{ pendingDecisions.length }}）
            </div>
            <div v-if="!pendingDecisions.length" class="orb-empty">暂无待确认决策</div>
            <div v-for="d in pendingDecisions" :key="d.decision_id" class="pd-item">
              <div class="pd-name">{{ d.title }} <span class="pd-phase">{{ d.phase }}</span></div>
              <div class="pd-detail" v-if="d.description">{{ d.description }}</div>
              <div class="pd-options">
                <button
                  v-for="(opt, i) in (d.options && d.options.length ? d.options : ['确认'])"
                  :key="i" class="pd-btn primary" @click="resolveDecision(d.decision_id, opt)">{{ opt }}</button>
              </div>
            </div>
          </div>

          <!-- 选型卡：translator 频道（导演选了哪个底模/节点/seed，决策可视化） -->
          <div class="m3-translator">
            <div class="mt-title">🎬 导演选型</div>
            <div v-if="!state.translator" class="orb-empty">暂无导演选型（进入 P4 生成后将自动展示）</div>
            <template v-else>
              <div class="mt-row"><span class="mt-k">底模</span><span class="mt-v">{{ state.translator.checkpoint || '—' }}</span></div>
              <div class="mt-row"><span class="mt-k">IP节点</span><span class="mt-v">{{ state.translator.node || '未使用 IP 适配器' }}</span></div>
              <div class="mt-row" v-if="state.translator.ip_adapter_weight != null"><span class="mt-k">IP权重</span><span class="mt-v">{{ state.translator.ip_adapter_weight }}</span></div>
              <div class="mt-row" v-if="state.translator.loras && state.translator.loras.length">
                <span class="mt-k">LoRA</span><span class="mt-v">{{ state.translator.loras.join('、') }}</span>
              </div>
              <div class="mt-row"><span class="mt-k">Seed</span><span class="mt-v">{{ (state.translator.seed != null && state.translator.seed !== '') ? state.translator.seed : '—' }}</span></div>
              <div class="mt-sub" v-if="state.translator.task_type">任务类型：{{ state.translator.task_type }}</div>
            </template>
          </div>

          <!-- 对话气泡：notice / insight（导演主动发言 + 品控结论） -->
          <div class="m3-conv">
            <div v-if="!state.conversation.length" class="conv-bubble empty">
              <span class="conv-tag">通知</span>
              <span class="conv-text">暂无通知或品控消息</span>
            </div>
            <div v-for="c in state.conversation.slice().reverse()" :key="c.id" class="conv-bubble" :class="c.kind">
              <span class="conv-tag">{{ c.kind === 'notice' ? '通知' : '品控' }}</span>
              <span class="conv-text">{{ c.text }}</span>
              <span class="conv-t">{{ fmtTime(c.ts) }}</span>
            </div>
          </div>

          <!-- 需决策（error 级建议，带操作按钮，L3 主动提醒） -->
          <div v-if="decisionItems.length" class="persona-decision">
            <div class="pd-title">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="12" y1="8" x2="12" y2="13"/><line x1="12" y1="16.5" x2="12.01" y2="16.5"/></svg>
              需决策（{{ decisionItems.length }}）
            </div>
            <div v-for="s in decisionItems" :key="s.id" class="pd-item">
              <div class="pd-name">{{ s.title }}</div>
              <div class="pd-detail">{{ s.detail }}</div>
              <div class="pd-actions" v-if="s.action_type">
                <button v-if="s.action_type === 'regenerate'" class="pd-btn primary" @click="runOrbBatch('regenerate', s.action_payload.segment_ids, {})">一键重生成</button>
                <button v-if="s.action_type === 'skip'" class="pd-btn ghost" @click="runOrbBatch('skip', s.action_payload.segment_ids, {})">跳过</button>
                <button class="pd-btn ghost" @click="dismissSuggestion(s.id)">暂不处理</button>
              </div>
              <div class="pd-actions" v-else>
                <button class="pd-btn ghost" @click="dismissSuggestion(s.id)">忽略</button>
              </div>
            </div>
          </div>

          <!-- 管家建议（suggestion/warning，可忽略，L2 状态暗示） -->
          <div v-if="visibleSuggestions.length" class="persona-advice">
            <div class="pa-title">
              管家建议 <span class="pa-sub">（可忽略）</span>
              <span class="pa-clear" @click="visibleSuggestions.forEach((s) => dismissSuggestion(s.id))">全部忽略</span>
            </div>
            <div v-for="s in visibleSuggestions" :key="s.id" class="advice-item">
              <span class="ai-sev" :class="s.severity">{{ sevTxt(s.severity) }}</span>
              <div class="ai-body">
                <div class="ai-name">{{ s.title }}</div>
                <div class="ai-detail">{{ s.detail }}</div>
                <button v-if="s.action_type === 'navigate'" class="ai-btn" @click="goPath((s.action_payload || {}).path)">去处理</button>
                <button v-else-if="s.action_type === 'regenerate'" class="ai-btn" @click="runOrbBatch('regenerate', s.action_payload.segment_ids, {})">重生成</button>
                <button v-else-if="s.action_type === 'skip'" class="ai-btn" @click="runOrbBatch('skip', s.action_payload.segment_ids, {})">跳过</button>
              </div>
              <button class="ai-dismiss" @click="dismissSuggestion(s.id)" title="忽略">×</button>
            </div>
          </div>

          <!-- 阶段核心视图（按 effectiveStep 动态展示导演 Agent 已有数据） -->
          <div class="persona-core">
            <div v-if="effectiveStep === 1 && p1Core" class="core-block">
              <div class="core-title">资产就绪度 <span class="core-score" :class="{ low: p1Core.score < 50 }">{{ p1Core.score }} 分</span></div>
              <div class="core-metric-row">
                <div v-for="g in p1Core.groups" :key="g.label" class="cm-cell">
                  <div class="cm-val">{{ g.ready }}/{{ g.total }}</div>
                  <div class="cm-lab">{{ g.label }}</div>
                </div>
              </div>
              <div v-if="p1Core.missing.length" class="core-note warn">缺角色绑定场次：{{ p1Core.missing.map((m) => m.index).join('、') }}</div>
            </div>

            <div v-else-if="effectiveStep === 2 && p2Core" class="core-block">
              <div class="core-title">叙事结构（共 {{ p2Core.segment_count }} 场）</div>
              <div v-if="p2Core.consecutive.length" class="core-note">连续同场景 {{ p2Core.consecutive.length }} 处：{{ p2Core.consecutive.map((c) => c.start + '-' + c.end).join('、') }}</div>
              <div v-if="p2Core.empty.length" class="core-note warn">{{ p2Core.empty.length }} 场缺描述：{{ p2Core.empty.join('、') }}</div>
              <div v-if="!p2Core.consecutive.length && !p2Core.empty.length" class="core-note ok">结构健康，无连续同场景 / 空描述</div>
            </div>

            <div v-else-if="effectiveStep === 3" class="core-block">
              <div class="core-title">配方透明度</div>
              <div class="core-note">分镜级配方（风格 / 资产权重 / 运镜）可在「分镜编排」页选中分镜后由导演 Agent 实时解析呈现。</div>
            </div>

            <div v-else-if="effectiveStep === 4 && p4Core" class="core-block">
              <div class="core-title">品控摘要 <span class="core-score" :class="{ low: p4Core.failed > 0 }">失败 {{ p4Core.failed }}</span></div>
              <div class="core-metric-row">
                <div class="cm-cell"><div class="cm-val">{{ p4Core.completed }}</div><div class="cm-lab">已完成</div></div>
                <div class="cm-cell"><div class="cm-val">{{ p4Core.running }}</div><div class="cm-lab">生成中</div></div>
                <div class="cm-cell"><div class="cm-val">{{ p4Core.failed }}</div><div class="cm-lab">失败</div></div>
              </div>
              <div v-if="p4Core.face_similarity_pass_rate > 0" class="core-note" :class="{ warn: p4Core.face_similarity_pass_rate < 100 }">人脸相似度通过率 {{ p4Core.face_similarity_pass_rate }}%</div>
              <div v-for="f in (p4Core.failed_segments || [])" :key="f.segment_id" class="core-fail">
                <span>场次 {{ f.index }} 失败</span>
                <button class="ai-btn" @click="runOrbBatch('regenerate', [f.segment_id], {})">重生成</button>
              </div>
            </div>

            <div v-else-if="effectiveStep === 5 && p5Core" class="core-block">
              <div class="core-title">成片质检（{{ p5Core.generated }} 段）</div>
              <div class="core-metric-row">
                <div class="cm-cell"><div class="cm-val">{{ p5Core.face_similarity_pass_rate }}%</div><div class="cm-lab">人脸通过率</div></div>
                <div class="cm-cell"><div class="cm-val">{{ p5Core.first_frame_match_avg }}</div><div class="cm-lab">首帧匹配</div></div>
              </div>
              <div class="core-note" :class="{ warn: p5Core.degrade_summary && p5Core.degrade_summary !== '无降级' }">
                降级：{{ p5Core.degrade_summary || '无' }}
              </div>
            </div>

            <div v-else class="core-block">
              <div class="core-note">管家视图聚合导演 Agent 数据 · 进入各阶段后展示核心指标</div>
            </div>
          </div>
        </div>

        <!-- 实时小任务 -->
        <div class="live-section">
          <div class="live-head" @click="toggleSection('live')">
            <span class="live-title">实时生成</span>
            <span v-if="activeFeedCount" class="live-count">{{ activeFeedCount }} 进行中</span>
            <span class="live-caret">{{ expanded.live ? '−' : '+' }}</span>
          </div>
          <div v-if="expanded.live !== false" class="live-body">
            <div v-if="!state.feed.length" class="orb-empty">暂无进行中的生成任务</div>
            <div v-for="f in state.feed" :key="f.id" class="feed-card" :class="f.status">
              <div class="fc-head" @click="toggleFeed(f.id)">
                <span class="fc-kind">{{ kindIcon(f.kind) }}</span>
                <span class="fc-title">{{ f.title }}</span>
                <span class="fc-status" :class="f.status">{{ statusText(f.status) }}</span>
                <span class="fc-caret">{{ expanded[f.id] ? '−' : '+' }}</span>
              </div>
              <div v-if="f.status === 'running'" class="fc-bar"><div class="fc-fill" :style="{ width: (f.progress || 0) + '%' }"></div></div>
              <div v-if="expanded[f.id]" class="fc-body">
                <div class="fc-log">
                  <div v-for="(l, i) in f.log" :key="i" class="fc-log-line" :class="l.level">
                    <span class="fl-t">{{ fmtTime(l.ts) }}</span>{{ l.msg }}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 链路详情折叠区 -->
        <div class="detail-stack">
          <div class="detail-item" :class="{ open: showDag }">
            <button class="di-toggle" @click="showDag = !showDag">
              <span class="di-icon">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" />
                  <rect x="14" y="14" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" />
                  <path d="M10 6h4M6 10v4M18 10v4M10 18h4" />
                </svg>
              </span>
              <span class="di-label">调度链路 N01–N09</span>
              <span class="di-caret">{{ showDag ? '−' : '+' }}</span>
            </button>
            <div v-if="showDag" class="di-body">
              <div class="orb-dag">
                <div v-for="n in NODE_ORDER" :key="n" class="orb-node" :class="(state.nodes[n] && state.nodes[n].status) || 'pending'">
                  <span class="on-id">{{ n }}</span>
                  <span class="on-label">{{ NODE_TXT[n] }}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="detail-item" :class="{ open: showLogs }">
            <button class="di-toggle" @click="showLogs = !showLogs">
              <span class="di-icon">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                </svg>
              </span>
              <span class="di-label">导演链路日志</span>
              <span class="di-caret">{{ showLogs ? '−' : '+' }}</span>
            </button>
            <div v-if="showLogs" class="di-body">
              <div v-if="!state.logs.length" class="orb-empty">尚未开始监督</div>
              <div class="orb-log">
                <div v-for="(l, i) in state.logs.slice().reverse()" :key="i" class="orb-log-line" :class="l.level">
                  <span class="ol-t">{{ fmtTime(l.ts) }}</span>{{ l.msg }}
                </div>
              </div>
            </div>
          </div>

          <div class="detail-item" :class="{ open: showAlerts, hasAlert: state.alerts.length }">
            <button class="di-toggle" @click="showAlerts = !showAlerts">
              <span class="di-icon">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                  <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
                </svg>
              </span>
              <span class="di-label">告警 / 资金</span>
              <span v-if="state.alerts.length" class="di-badge">{{ state.alerts.length }}</span>
              <span class="di-caret">{{ showAlerts ? '−' : '+' }}</span>
            </button>
            <div v-if="showAlerts" class="di-body">
              <div v-if="!state.alerts.length" class="orb-empty">暂无告警</div>
              <div v-for="(a, i) in state.alerts.slice().reverse()" :key="i" class="orb-alert">
                <span class="oa-level" :class="a.level">{{ a.level === 'warning' ? '!' : '×' }}</span>
                <span>{{ a.reason || '' }}</span>
                <span v-if="a.refunded_count" class="oa-ref">退冻结金 {{ a.refunded_count }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 成片摘要 -->
        <div v-if="state.runStatus === 'done' || styleRef || state.finalOutput || qrReports.length" class="output-card">
          <div class="output-head">成片输出</div>
          <div v-if="styleRef" class="orb-style">
            <span class="os-label">全局风格参考图</span>
            <img class="os-img" :src="'/file?name=' + encodeURIComponent(styleRef)" @error="(e) => (e.target.style.display = 'none')" />
          </div>
          <div v-if="qrReports.length" class="orb-qr">
            <span class="qr-pill ok">合格 {{ qrPass }}</span>
            <span class="qr-pill bad">不合格 {{ qrReports.length - qrPass }}</span>
            <span v-if="failedShots.length" class="qr-pill bad">失败分镜 {{ failedShots.length }}</span>
          </div>
          <a v-if="state.finalOutput" :href="state.finalOutput" target="_blank" class="orb-out">查看成片</a>
        </div>
      </div>
    </transition>

    <!-- 悬浮球按钮（六态机：idle/watching/suggestion/warning/error/success） -->
    <button
      class="orb-btn"
      :class="'orb-' + state.orbState"
      @pointerdown="onPointerDown"
      :title="orbTitle"
    >
      <span class="orb-core">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <rect x="2" y="7" width="20" height="10" rx="2" />
          <path d="M7 7v10M17 7v10M2 12h20" />
          <circle cx="7" cy="12" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="17" cy="12" r="1.5" fill="currentColor" stroke="none" />
        </svg>
      </span>
      <!-- 状态环 -->
      <span v-if="state.orbState === 'watching' || state.runStatus === 'running'" class="orb-pulse"></span>
      <span v-else-if="state.orbState === 'warning'" class="orb-ring ring-warning"></span>
      <span v-else-if="state.orbState === 'error'" class="orb-ring ring-error"></span>
      <span v-else-if="state.orbState === 'success'" class="orb-ring ring-success"></span>
      <!-- 琥珀/橙角标点（suggestion/warning） -->
      <span v-if="state.orbState === 'suggestion' || state.orbState === 'warning'" class="orb-corner"></span>
      <!-- 状态徽标（优先级：error > warning/suggestion > 实时生成数） -->
      <span v-if="state.orbState === 'error'" class="orb-badge orb-badge-err">{{ decisionItems.length }}</span>
      <span v-else-if="state.orbState === 'warning' || state.orbState === 'suggestion'" class="orb-badge orb-badge-adv">{{ unreadCount }}</span>
      <span v-else-if="activeFeedCount" class="orb-badge">{{ activeFeedCount }}</span>
    </button>
  </div>
</template>

<style scoped>
.dir-orb-root { position: fixed; z-index: 2700; user-select: none; transition: left .28s cubic-bezier(.2,.85,.25,1), top .28s cubic-bezier(.2,.85,.25,1); }
.dir-orb-root.dragging { transition: none; }

/* 悬浮球 */
.orb-btn {
  position: relative; width: 56px; height: 56px; border-radius: 50%;
  border: none; cursor: grab; color: #fff;
  background: radial-gradient(circle at 30% 30%, rgba(255,255,255,.18), transparent 50%),
              linear-gradient(135deg, #7c6af0 0%, #4f8bf7 100%);
  box-shadow: 0 8px 24px rgba(79, 139, 247, .38), inset 0 1px 1px rgba(255,255,255,.2);
  display: flex; align-items: center; justify-content: center;
  transition: transform .18s, box-shadow .18s; touch-action: none;
}
.dir-orb-root.dragging .orb-btn { cursor: grabbing; }
.orb-btn:hover { transform: scale(1.06); box-shadow: 0 10px 30px rgba(79, 139, 247, .48); }

/* 六态机配色 */
.orb-btn.orb-watching { background: radial-gradient(circle at 30% 30%, rgba(255,255,255,.18), transparent 50%), linear-gradient(135deg, #8a93a8 0%, #5b6478 100%); box-shadow: 0 8px 24px rgba(120,130,150,.4); animation: orbBreath 2.6s ease-in-out infinite; }
.orb-btn.orb-warning { background: radial-gradient(circle at 30% 30%, rgba(255,255,255,.18), transparent 50%), linear-gradient(135deg, #f0a85a 0%, #e07b3a 100%); box-shadow: 0 8px 24px rgba(224,123,58,.42); }
.orb-btn.orb-error { background: radial-gradient(circle at 30% 30%, rgba(255,255,255,.18), transparent 50%), linear-gradient(135deg, #ff6b7d 0%, #e23b54 100%); box-shadow: 0 8px 26px rgba(226,59,84,.5); }
.orb-btn.orb-success { background: radial-gradient(circle at 30% 30%, rgba(255,255,255,.18), transparent 50%), linear-gradient(135deg, #4ade80 0%, #22b35e 100%); box-shadow: 0 8px 24px rgba(34,179,94,.42); }
/* idle (默认蓝) 与 suggestion (蓝+琥珀角标) 沿用基础 .orb-btn 渐变 */

.orb-core { display: flex; align-items: center; justify-content: center; }
.orb-core svg { filter: drop-shadow(0 1px 2px rgba(0,0,0,.25)); }

/* 状态环 */
.orb-ring { position: absolute; inset: -5px; border-radius: 50%; pointer-events: none; opacity: 0; }
.orb-ring.ring-warning { border: 2px solid rgba(240,168,90,.75); animation: orbRing 1.8s ease-out infinite; }
.orb-ring.ring-error { border: 2px solid rgba(255,94,125,.85); animation: orbRing 1.2s ease-out infinite; }
.orb-ring.ring-success { border: 2px solid rgba(74,222,128,.7); animation: orbDiffuse 2s ease-out infinite; }
@keyframes orbRing { 0% { transform: scale(1); opacity: .6 } 100% { transform: scale(1.5); opacity: 0 } }
@keyframes orbDiffuse { 0% { transform: scale(1); opacity: .5 } 60% { opacity: 0 } 100% { transform: scale(1.65); opacity: 0 } }
@keyframes orbBreath { 0%,100% { box-shadow: 0 8px 24px rgba(120,130,150,.4) } 50% { box-shadow: 0 8px 34px rgba(120,130,150,.72) } }

.orb-pulse {
  position: absolute; inset: -5px; border-radius: 50%;
  border: 2px solid rgba(124, 106, 240, .7);
  animation: orbPulse 2s ease-out infinite; opacity: 0; pointer-events: none;
}
/* 琥珀/橙角标点（置于右下角，避让右上角徽标） */
.orb-corner { position: absolute; bottom: -3px; right: -3px; width: 13px; height: 13px; border-radius: 50%; background: #ffb454; border: 2px solid #1b1b26; box-shadow: 0 0 8px rgba(255,180,84,.85); }
.orb-badge {
  position: absolute; top: -4px; right: -4px; min-width: 20px; height: 20px; padding: 0 5px;
  border-radius: 10px; background: #ff5e7d; color: #fff; font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(255,94,125,.45);
}
.orb-badge-err { background: #ff3b54; box-shadow: 0 2px 8px rgba(255,59,84,.5); }
.orb-badge-adv { background: #ffb454; color: #3a2a10; box-shadow: 0 2px 8px rgba(255,180,84,.5); }

/* ===== 管家视图（DirectorOrb v2 化身层） ===== */
.orb-persona { margin-bottom: 12px; }

/* 需决策（error） */
.persona-decision { border: 1px solid rgba(255,94,125,.42); background: rgba(255,94,125,.08); border-radius: 12px; padding: 10px 12px; margin-bottom: 8px; }
.pd-title { display: flex; align-items: center; gap: 6px; font-size: 11.5px; font-weight: 800; color: #ff8da0; margin-bottom: 8px; }
.pd-item { padding: 7px 0; border-bottom: 1px dashed rgba(255,94,125,.25); }
.pd-item:last-child { border-bottom: none; }
.pd-name { font-size: 12px; font-weight: 600; color: var(--txt, #f0f0f5); }
.pd-detail { font-size: 10.5px; color: var(--sub, #b9bccb); line-height: 1.45; margin-top: 2px; }
.pd-actions { display: flex; gap: 6px; margin-top: 7px; }
.pd-btn { font-size: 11px; padding: 4px 11px; border-radius: 8px; border: none; cursor: pointer; font-weight: 600; transition: filter .15s; }
.pd-btn:hover { filter: brightness(1.08); }
.pd-btn.primary { background: linear-gradient(135deg, #7c6af0, #4f8bf7); color: #fff; }
.pd-btn.ghost { background: rgba(255,255,255,.08); color: var(--txt, #eee); }
/* M3 决策卡：A 类决策请示 */
.m3-decision { border-color: rgba(124,106,240,.45); background: rgba(124,106,240,.1); }
.m3-decision .pd-title { color: #a6aaff; }
.m3-decision .pd-item { border-bottom-color: rgba(124,106,240,.28); }
.pd-phase { font-size: 9.5px; font-weight: 700; color: var(--sub, #7a8094); padding: 1px 6px; border-radius: 6px; background: rgba(255,255,255,.07); margin-left: 4px; }
.pd-options { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }

/* M3 选型卡：translator 决策可视化 */
.m3-translator { border: 1px solid rgba(79,139,247,.42); background: rgba(79,139,247,.08); border-radius: 12px; padding: 10px 12px; margin-bottom: 8px; }
.mt-title { font-size: 11.5px; font-weight: 800; color: #6f9bff; margin-bottom: 7px; }
.mt-row { display: flex; align-items: baseline; gap: 8px; font-size: 11px; padding: 2px 0; }
.mt-k { flex-shrink: 0; width: 38px; color: var(--sub, #7a8094); }
.mt-v { color: var(--txt, #e8e8ef); font-weight: 600; word-break: break-all; }
.mt-sub { font-size: 10px; color: var(--sub, #6d7286); margin-top: 5px; }

/* M3 对话气泡：notice / insight */
.m3-conv { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.conv-bubble { display: flex; align-items: flex-start; gap: 7px; padding: 7px 10px; border-radius: 10px; background: color-mix(in srgb, var(--bg-2, #20202c) 60%, transparent); border: 1px solid var(--line, #2a2a38); }
.conv-bubble.notice { border-left: 3px solid #6f9bff; }
.conv-bubble.insight { border-left: 3px solid #4ade80; }
.conv-bubble.empty { opacity: .65; }
.conv-bubble.empty .conv-text { color: var(--sub, #7a8094); font-style: italic; }
.conv-tag { flex-shrink: 0; font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 6px; background: rgba(255,255,255,.08); color: var(--sub, #b9bccb); }
.conv-text { flex: 1; min-width: 0; font-size: 11px; color: var(--txt, #e8e8ef); line-height: 1.45; }
.conv-t { flex-shrink: 0; font-size: 9px; color: var(--sub, #6d7286); align-self: center; }

/* 管家建议（可忽略） */
.persona-advice { border: 1px solid var(--line, #2a2a38); background: color-mix(in srgb, var(--bg-2, #20202c) 60%, transparent); border-radius: 12px; padding: 10px 12px; margin-bottom: 8px; }
.pa-title { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700; color: #a6aaff; margin-bottom: 6px; }
.pa-sub { font-size: 10px; color: var(--sub, #7a8094); font-weight: 500; }
.pa-clear { margin-left: auto; font-size: 10px; color: var(--sub, #7a8094); cursor: pointer; padding: 1px 6px; border-radius: 6px; }
.pa-clear:hover { background: rgba(255,255,255,.06); color: var(--txt, #eee); }
.advice-item { display: flex; align-items: flex-start; gap: 8px; padding: 7px 0; border-bottom: 1px dashed var(--line, #2f2f3d); }
.advice-item:last-child { border-bottom: none; }
.ai-sev { flex-shrink: 0; font-size: 9px; padding: 2px 6px; border-radius: 6px; font-weight: 700; margin-top: 1px; }
.ai-sev.suggestion { background: rgba(124,106,240,.16); color: #a6aaff; }
.ai-sev.warning { background: rgba(255,180,84,.16); color: #ffb454; }
.ai-sev.error { background: rgba(255,94,125,.16); color: #ff8da0; }
.ai-body { flex: 1; min-width: 0; }
.ai-name { font-size: 12px; color: var(--txt, #e8e8ef); font-weight: 600; }
.ai-detail { font-size: 10.5px; color: var(--sub, #9095a8); line-height: 1.45; margin-top: 2px; }
.ai-btn { margin-top: 5px; font-size: 10.5px; padding: 3px 10px; border-radius: 7px; border: none; cursor: pointer; font-weight: 600; background: rgba(124,106,240,.18); color: #a6aaff; transition: filter .15s; }
.ai-btn:hover { filter: brightness(1.12); }
.ai-dismiss { flex-shrink: 0; background: transparent; border: none; color: var(--sub, #6d7286); cursor: pointer; font-size: 16px; line-height: 1; padding: 0 2px; border-radius: 6px; }
.ai-dismiss:hover { color: var(--txt, #fff); background: rgba(255,255,255,.06); }

/* 阶段核心视图 */
.persona-core { display: flex; flex-direction: column; gap: 8px; }
.core-block { border: 1px solid var(--line, #2a2a38); border-radius: 12px; padding: 10px 12px; background: color-mix(in srgb, var(--bg-2, #20202c) 55%, transparent); }
.core-title { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 700; color: var(--txt, #e8e8ef); margin-bottom: 8px; }
.core-score { font-size: 11px; padding: 2px 8px; border-radius: 8px; background: rgba(74,222,128,.14); color: #4ade80; font-weight: 700; }
.core-score.low { background: rgba(255,180,84,.16); color: #ffb454; }
.core-metric-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.cm-cell { padding: 7px 4px; border-radius: 9px; background: color-mix(in srgb, var(--bg-2, #252532) 55%, transparent); text-align: center; }
.cm-val { font-size: 14px; font-weight: 700; color: var(--txt, #e8e8ef); line-height: 1.2; }
.cm-lab { font-size: 9.5px; color: var(--sub, #7a8094); margin-top: 2px; }
.core-note { font-size: 11px; color: var(--sub, #9095a8); line-height: 1.5; margin-top: 8px; padding-top: 7px; border-top: 1px dashed var(--line, #2f2f3d); }
.core-note.ok { color: #4ade80; border-top: none; padding-top: 0; }
.core-note.warn { color: #ffb454; }
.core-fail { display: flex; align-items: center; justify-content: space-between; font-size: 11px; color: #ff8da0; margin-top: 6px; padding: 4px 0; border-top: 1px dashed rgba(255,94,125,.25); }
@keyframes orbPulse { 0% { transform: scale(1); opacity: .55 } 100% { transform: scale(1.55); opacity: 0 } }
@keyframes orbGlow { 0%,100% { box-shadow: 0 8px 24px rgba(79,139,247,.38) } 50% { box-shadow: 0 8px 34px rgba(124,106,240,.65) } }

/* 磁吸边框 */
.orb-magnet { position: fixed; z-index: 2699; pointer-events: none; animation: magnetIn .12s ease-out; }
.orb-magnet.left { left: 0; top: 0; bottom: 0; width: 3px; background: linear-gradient(to bottom, transparent, #6f9bff 50%, transparent); box-shadow: 0 0 10px #6f9bff; }
.orb-magnet.right { right: 0; top: 0; bottom: 0; width: 3px; background: linear-gradient(to bottom, transparent, #6f9bff 50%, transparent); box-shadow: 0 0 10px #6f9bff; }
.orb-magnet.top { top: 0; left: 0; right: 0; height: 3px; background: linear-gradient(to right, transparent, #6f9bff 50%, transparent); box-shadow: 0 0 10px #6f9bff; }
.orb-magnet.bottom { bottom: 0; left: 0; right: 0; height: 3px; background: linear-gradient(to right, transparent, #6f9bff 50%, transparent); box-shadow: 0 0 10px #6f9bff; }
@keyframes magnetIn { from { opacity: 0 } to { opacity: 1 } }

/* 展开面板 */
.orb-panel {
  position: absolute; width: 360px; max-height: 78vh; overflow-y: auto;
  background: color-mix(in srgb, var(--card, #1b1b26) 96%, transparent);
  border: 1px solid color-mix(in srgb, var(--line, #2f2f3d) 80%, transparent);
  border-radius: 20px;
  box-shadow: 0 20px 50px rgba(0,0,0,.45), 0 0 0 1px rgba(255,255,255,.03) inset;
  padding: 16px; color: var(--txt, #eee);
  backdrop-filter: blur(14px) saturate(1.2);
}
.orb-panel::-webkit-scrollbar { width: 6px; }
.orb-panel::-webkit-scrollbar-thumb { background: var(--line, #3a3a4a); border-radius: 3px; }

/* Header */
.orb-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 12px; }
.orb-title { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 15px; color: var(--txt, #f0f0f5); }
.orb-icon { display: flex; color: #8f94ff; }
.orb-head-right { display: flex; align-items: center; gap: 8px; }
.orb-stage-tag { font-size: 11px; color: var(--sub, #a0a6b8); background: var(--bg-2, #232330); padding: 3px 9px; border-radius: 10px; font-weight: 600; }
.orb-x { background: transparent; border: none; color: var(--sub, #8b90a4); cursor: pointer; font-size: 18px; line-height: 1; padding: 2px 4px; border-radius: 6px; transition: color .15s, background .15s; }
.orb-x:hover { color: var(--txt, #fff); background: var(--bg-2, #262633); }

/* 阶段导航条 */
.phase-track { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 14px; padding: 6px 8px; background: var(--bg-2, #20202c); border-radius: 12px; border: 1px solid var(--line, #2c2c3a); }
.phase-node { display: flex; flex-direction: column; align-items: center; gap: 4px; cursor: default; flex: 1; }
.pn-dot {
  width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  background: var(--bg-2, #2a2a38); border: 1.5px solid var(--line, #3c3c4d);
  color: var(--sub, #6d7286); font-size: 10px; font-weight: 700; transition: all .25s;
}
.pn-dot svg { stroke: currentColor; }
.pn-label { font-size: 10px; color: var(--sub, #6d7286); font-weight: 600; transition: color .25s; }
.phase-node.active .pn-dot { background: linear-gradient(135deg, #7c6af0, #4f8bf7); border-color: transparent; color: #fff; box-shadow: 0 0 10px rgba(124,106,240,.45); }
.phase-node.active .pn-label { color: #8f94ff; }
.phase-node.done .pn-dot { background: rgba(74,222,128,.12); border-color: rgba(74,222,128,.45); color: #4ade80; }
.phase-node.done .pn-label { color: #4ade80; }

/* 当前阶段主视图 */
.phase-hero { margin-bottom: 14px; }
.ph-title-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 2px; }
.ph-step { font-size: 12px; font-weight: 800; color: #8f94ff; letter-spacing: .5px; }
.ph-title { font-size: 18px; font-weight: 700; color: var(--txt, #fff); }
.ph-subtitle { font-size: 12px; color: var(--sub, #9ca2b3); margin-bottom: 12px; line-height: 1.5; }

/* 总进度 */
.orb-progress { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.ob-pbar { flex: 1; height: 6px; background: var(--bg-2, #2a2a38); border-radius: 3px; overflow: hidden; }
.ob-pfill { height: 100%; border-radius: 3px; transition: width .4s; background: linear-gradient(90deg, #7c6af0, #4f8bf7); }
.ob-pfill.done { background: #4ade80; }
.ob-pfill.failed, .ob-pfill.error { background: #ff5e7d; }
.ob-pnum { font-size: 11px; color: var(--sub, #8b90a4); min-width: 34px; text-align: right; font-weight: 600; }

/* 技能卡片 */
.skills-card { background: color-mix(in srgb, var(--bg-2, #20202c) 70%, transparent); border: 1px solid var(--line, #2a2a38); border-radius: 14px; padding: 12px; margin-bottom: 10px; }
.skills-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.sh-label { font-size: 12px; font-weight: 700; color: var(--txt, #e8e8ef); }
.sh-agent { font-size: 10px; color: var(--sub, #7a8094); background: var(--bg-2, #252532); padding: 2px 8px; border-radius: 8px; }
.skills-list { display: flex; flex-direction: column; gap: 6px; }
.skill-item { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px; border-radius: 10px; background: color-mix(in srgb, var(--bg-2, #252532) 50%, transparent); border: 1px solid transparent; transition: all .2s; }
.skill-item:hover { background: color-mix(in srgb, var(--bg-2, #2a2a38) 80%, transparent); }
.skill-item.running { border-color: rgba(124,106,240,.45); background: color-mix(in srgb, rgba(124,106,240,.08), var(--bg-2, #252532)); }
.skill-item.done { border-color: rgba(74,222,128,.35); }
.skill-item.failed { border-color: rgba(255,94,125,.4); }
.si-dot { flex-shrink: 0; width: 8px; height: 8px; border-radius: 50%; margin-top: 5px; background: var(--line, #4a4a5a); transition: background .2s; }
.skill-item.running .si-dot { background: #8f94ff; animation: pulseDot 1.4s ease-in-out infinite; }
.skill-item.done .si-dot { background: #4ade80; }
.skill-item.failed .si-dot { background: #ff5e7d; }
@keyframes pulseDot { 0%,100% { opacity: 1 } 50% { opacity: .35 } }
.si-body { flex: 1; min-width: 0; }
.si-name { font-size: 12px; font-weight: 600; color: var(--txt, #e8e8ef); margin-bottom: 1px; }
.si-desc { font-size: 10.5px; color: var(--sub, #9095a8); line-height: 1.4; }
.si-status { flex-shrink: 0; font-size: 10px; padding: 2px 7px; border-radius: 8px; background: rgba(150,150,170,.12); color: var(--sub, #7a8094); font-weight: 600; }
.skill-item.running .si-status { background: rgba(124,106,240,.18); color: #a6aaff; }
.skill-item.done .si-status { background: rgba(74,222,128,.14); color: #4ade80; }
.skill-item.failed .si-status { background: rgba(255,94,125,.14); color: #ff5e7d; }

/* 指标 */
.metrics-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-bottom: 10px; }
.metric-cell { padding: 8px 4px; border-radius: 10px; background: color-mix(in srgb, var(--bg-2, #20202c) 60%, transparent); border: 1px solid var(--line, #2a2a38); text-align: center; }
.metric-value { font-size: 15px; font-weight: 700; line-height: 1.2; color: var(--txt, #e8e8ef); }
.metric-label { font-size: 9.5px; color: var(--sub, #7a8094); margin-top: 3px; font-weight: 500; }
.metric-cell.primary .metric-value { color: #8f94ff; }
.metric-cell.ok .metric-value { color: #4ade80; }
.metric-cell.bad .metric-value { color: #ff5e7d; }
.metric-cell.running .metric-value { color: #8f94ff; }
.metric-cell.muted .metric-value { color: #5c6070; }

/* 实时生成区 */
.live-section { background: color-mix(in srgb, var(--bg-2, #20202c) 55%, transparent); border: 1px solid var(--line, #2a2a38); border-radius: 14px; margin-bottom: 10px; overflow: hidden; }
.live-head { display: flex; align-items: center; gap: 8px; padding: 10px 12px; cursor: pointer; font-size: 12px; font-weight: 700; color: var(--txt, #e8e8ef); }
.live-head:hover { background: color-mix(in srgb, var(--bg-2, #252532) 50%, transparent); }
.live-title { flex: 1; }
.live-count { font-size: 10px; color: #8f94ff; background: rgba(124,106,240,.14); padding: 2px 7px; border-radius: 8px; }
.live-caret { color: var(--sub, #7a8094); font-size: 14px; font-weight: 400; }
.live-body { padding: 0 12px 12px; }

/* feed 卡片 */
.feed-card { border: 1px solid var(--line, #2f2f3d); border-radius: 10px; margin-bottom: 6px; overflow: hidden; background: color-mix(in srgb, var(--bg-2, #252532) 40%, transparent); }
.feed-card.running { border-color: rgba(124,106,240,.35); }
.feed-card.done { border-color: rgba(74,222,128,.25); }
.feed-card.failed { border-color: rgba(255,94,125,.35); }
.fc-head { display: flex; align-items: center; gap: 7px; padding: 8px 10px; cursor: pointer; }
.fc-kind { width: 20px; height: 20px; border-radius: 5px; background: var(--line, #333342); color: var(--sub, #9ca2b3); font-size: 10px; display: flex; align-items: center; justify-content: center; font-weight: 700; }
.fc-title { flex: 1; font-size: 12px; color: var(--txt, #e8e8ef); }
.fc-status { font-size: 10px; padding: 2px 7px; border-radius: 8px; font-weight: 600; }
.fc-status.running { background: rgba(124,106,240,.16); color: #a6aaff; }
.fc-status.done { background: rgba(74,222,128,.14); color: #4ade80; }
.fc-status.failed { background: rgba(255,94,125,.14); color: #ff5e7d; }
.fc-caret { color: var(--sub, #7a8094); font-size: 11px; }
.fc-bar { height: 3px; background: var(--bg-2, #2a2a38); margin: 0 10px 6px; border-radius: 2px; overflow: hidden; }
.fc-fill { height: 100%; background: linear-gradient(90deg, #7c6af0, #4f8bf7); transition: width .4s; }
.fc-body { padding: 4px 10px 10px; border-top: 1px solid var(--line, #2f2f3d); }
.fc-log { max-height: 120px; overflow-y: auto; background: var(--bg-2, #181822); border-radius: 6px; padding: 6px 8px; }
.fc-log-line { font-size: 10.5px; color: var(--sub, #9ca2b3); line-height: 1.5; word-break: break-all; }
.fc-log-line.error { color: #ff8da0; }
.fl-t { color: #5c6070; margin-right: 6px; font-variant-numeric: tabular-nums; }

/* 折叠详情区 */
.detail-stack { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.detail-item { background: color-mix(in srgb, var(--bg-2, #20202c) 55%, transparent); border: 1px solid var(--line, #2a2a38); border-radius: 12px; overflow: hidden; }
.detail-item.hasAlert { border-color: rgba(255,180,84,.35); }
.di-toggle { width: 100%; display: flex; align-items: center; gap: 8px; padding: 10px 12px; background: transparent; border: none; color: var(--txt, #e8e8ef); cursor: pointer; font-size: 12px; font-weight: 600; }
.di-toggle:hover { background: color-mix(in srgb, var(--bg-2, #252532) 50%, transparent); }
.di-icon { display: flex; color: #8f94ff; }
.di-label { flex: 1; text-align: left; }
.di-badge { font-size: 10px; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; background: #ff5e7d; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 700; }
.di-caret { color: var(--sub, #7a8094); font-size: 14px; }
.di-body { padding: 0 12px 12px; }

/* DAG */
.orb-dag { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.orb-node { display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 7px 4px; border-radius: 9px; background: color-mix(in srgb, var(--bg-2, #252532) 50%, transparent); border: 1px solid var(--line, #2f2f3d); }
.orb-node .on-id { font-size: 10px; color: var(--sub, #6d7286); font-weight: 700; }
.orb-node .on-label { font-size: 11px; color: var(--txt, #c8c8d5); }
.orb-node.running { border-color: #8f94ff; box-shadow: 0 0 8px rgba(124,106,240,.35); }
.orb-node.running .on-label { color: #a6aaff; }
.orb-node.done { border-color: #4ade80; }
.orb-node.done .on-label { color: #4ade80; }
.orb-node.failed { border-color: #ff5e7d; }
.orb-node.failed .on-label { color: #ff5e7d; }
.orb-node.pending { opacity: .65; }

/* 日志 */
.orb-log { max-height: 130px; overflow-y: auto; background: var(--bg-2, #181822); border-radius: 8px; padding: 8px; }
.orb-log-line { font-size: 10.5px; color: var(--sub, #9ca2b3); line-height: 1.55; word-break: break-all; }
.orb-log-line.info { color: #b9c0d0; }
.orb-log-line.success { color: #4ade80; }
.orb-log-line.warning { color: #ffb454; }
.orb-log-line.error { color: #ff8da0; }
.ol-t { color: #5c6070; margin-right: 6px; font-variant-numeric: tabular-nums; }

/* 告警 */
.orb-alert { display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: #ffb454; padding: 5px 0; line-height: 1.4; }
.oa-level { width: 18px; height: 18px; border-radius: 50%; display: flex; align-items: center; justify-content: center; background: rgba(255,180,84,.15); font-weight: 700; }
.oa-level.error { background: rgba(255,94,125,.15); color: #ff5e7d; }
.oa-ref { margin-left: auto; font-size: 10px; color: var(--sub, #7a8094); }

/* 成片输出 */
.output-card { background: linear-gradient(135deg, rgba(124,106,240,.12), rgba(79,139,247,.08)); border: 1px solid rgba(124,106,240,.25); border-radius: 14px; padding: 12px; }
.output-head { font-size: 12px; font-weight: 700; color: #a6aaff; margin-bottom: 8px; }
.orb-style { margin-bottom: 8px; }
.os-label { font-size: 11px; color: var(--sub, #9ca2b3); display: block; margin-bottom: 5px; }
.os-img { max-width: 100%; max-height: 120px; border-radius: 8px; border: 1px solid var(--line, #2f2f3d); }
.orb-qr { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.qr-pill { font-size: 11px; padding: 3px 9px; border-radius: 8px; }
.qr-pill.ok { background: rgba(74,222,128,.12); color: #4ade80; }
.qr-pill.bad { background: rgba(255,94,125,.12); color: #ff5e7d; }
.orb-out { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: #fff; text-decoration: none; background: linear-gradient(135deg, #7c6af0, #4f8bf7); padding: 6px 12px; border-radius: 8px; }
.orb-out:hover { filter: brightness(1.1); }

.orb-empty { font-size: 11.5px; color: var(--sub, #6d7286); padding: 6px 0; }

/* 动画 */
.orb-pop-enter-active, .orb-pop-leave-active { transition: opacity .22s, transform .22s; }
.orb-pop-enter-from, .orb-pop-leave-to { opacity: 0; transform: translateY(10px) scale(.96); }
</style>
