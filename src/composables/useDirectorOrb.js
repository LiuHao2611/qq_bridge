// ============================================================
// useDirectorOrb —— 导演 Agent 悬浮球共享状态（单例）
// 职责（对应执行手册 Layer3 前端交互 + KB §18 WS 网关）：
//   - 订阅 WS 频道 `director:<pid>`，复用现有 progress/completed/error/alert 事件
//   - WS 不可用时按 REDUN-01 回退轮询 directorRuns / directorRunStatus
//   - 维护 N01–N09 DAG 节点状态、虚拟日志、告警、0–1 成片结果
//   - 提供 pushGeneration(kind,title,status,detail) 供各阶段页上报「正在生成的文本/图/视频」
//   - DirectorOrb v2：订阅 orb_* 事件，聚合导演 Agent 已有数据，呈现「管家」人格化建议
//
// 设计（方案 §二/§四）：悬浮球没有独立大脑，只做翻译/聚合/代理；建议去重节流（5 分钟）、
// 用户「忽略」存前端 localStorage（不持久化到后端，与导演 Agent 状态始终一致）。
// 纯内存状态，不写文件、不进 C 盘；模块级 reactive 即单例。
// ============================================================
import { reactive, computed } from 'vue'
import { subscribe, wsConnected } from '../ws'
import { directorRuns, directorRunStatus, listProjectAssets, listSegments, getProduction, orbApi, resolveDecision as resolveDecisionApi, listDecisions as listDecisionsApi } from '../api'

// DAG 节点中文标签（与 ProductionStage.vue NODE_TXT 对齐）
export const NODE_TXT = {
  N01: '接收任务', N02: '剧本解析', N03: '视觉设定', N04: '任务分发',
  N05: '分镜生成', N06: '质量检查', N07: '音频合成', N08: '视频合成', N09: '成片输出',
}
// 节点 → 产出类型（用于小任务分类与小图标）
export const NODE_KIND = {
  N01: 'meta', N02: 'text', N03: 'image', N04: 'meta',
  N05: 'video', N06: 'video', N07: 'audio', N08: 'video', N09: 'video',
}
export const NODE_ORDER = ['N01', 'N02', 'N03', 'N04', 'N05', 'N06', 'N07', 'N08', 'N09']

// 五阶段技能清单（管家面板按当前阶段只展示当前块）
export const PHASE_SKILLS = {
  P1: {
    key: 'P1', title: '资产初始化', subtitle: '冷启动资产层，建立 ID 一致性基础',
    agents: 'Asset Management',
    items: [
      { id: 'p1_face', name: '角色人脸特征提取', desc: '提取 512 维身份向量并入库', agent: 'FaceFeatures' },
      { id: 'p1_scene', name: '场景特征提取', desc: '环境 / 构图 / 光影特征结构化', agent: 'SceneFeatures' },
      { id: 'p1_pose', name: '角色阶段与姿势', desc: '阶段卡层级 + 姿势参考图绑定', agent: 'AssetStage' },
      { id: 'p1_index', name: '资产向量化索引', desc: '跨项目复用与版本控制', agent: 'FeatureVector' },
    ],
  },
  P2: {
    key: 'P2', title: '项目策划', subtitle: '剧本结构化拆解与资产预绑定',
    agents: 'ScriptParserAgent',
    items: [
      { id: 'p2_semantic', name: '语义拆解与结构化', desc: '景别 / 机位 / 焦段 / 光影 / 运镜补全', agent: 'ScriptParser', nodes: ['N02'] },
      { id: 'p2_granularity', name: '粒度切分适配', desc: '按套餐时长上限切 3–12s 分镜', agent: 'ScriptParser', nodes: ['N02'] },
      { id: 'p2_bind', name: '资产自动匹配与绑定', desc: '角色 / 场景 / 道具关键词 → 资产 ID', agent: 'ScriptParser', nodes: ['N02'] },
      { id: 'p2_blocking', name: '空间站位预规划', desc: '多人 / 群像站位蓝图生成', agent: 'ScriptParser', nodes: ['N02'] },
    ],
  },
  P3: {
    key: 'P3', title: '分镜编排', subtitle: '全局视觉统筹与资产一致性约束',
    agents: 'VisualControlAgent',
    items: [
      { id: 'p3_params', name: '全局参数统一下发', desc: '分辨率 / 画幅 / seed 策略 / 光影方向', agent: 'VisualControl', nodes: ['N03'] },
      { id: 'p3_face', name: '真人资产特征注入', desc: '512 维 ID → 扩散模型交叉注意力层', agent: 'VisualControl', nodes: ['N03'] },
      { id: 'p3_weight', name: '资产分层权重控制', desc: '基础 ID + 风格 LoRA 分层调权', agent: 'VisualControl', nodes: ['N03'] },
      { id: 'p3_style', name: '全局风格参考图', desc: '暖 / 冷色温决定色调统一条件', agent: 'VisualControl', nodes: ['N03'] },
    ],
  },
  P4: {
    key: 'P4', title: '生成生产', subtitle: '首尾帧锚定衔接与队列调度',
    agents: 'SequenceSchedulerAgent',
    items: [
      { id: 'p4_anchor', name: '首尾帧锚定衔接', desc: '上一分镜末帧 → 下一分镜首帧约束', agent: 'SequenceScheduler', nodes: ['N04', 'N05'] },
      { id: 'p4_queue', name: '分布式任务队列调度', desc: '并行 / 串行分镜 + VIP 优先队列', agent: 'SequenceScheduler', nodes: ['N04', 'N05'] },
      { id: 'p4_progress', name: '进度状态实时同步', desc: 'WS 频道 director:<pid> 推送', agent: 'SequenceScheduler', nodes: ['N04', 'N05'] },
      { id: 'p4_resume', name: '断点续跑', desc: '持久化中间产物与生产进度', agent: 'SequenceScheduler', nodes: ['N04', 'N05'] },
      { id: 'p4_retry', name: '失败重试 + 超时熔断', desc: '耗尽触发降级模型 / 降级队列', agent: 'SequenceScheduler', nodes: ['N04', 'N05'] },
    ],
  },
  P5: {
    key: 'P5', title: '合成导出', subtitle: '音画对齐、智能拼接与自动化品控',
    agents: 'PostProductionAgent',
    items: [
      { id: 'p5_av', name: '音画精准对齐', desc: 'TTS 时间轴 + 多音轨分层混音', agent: 'PostProduction', nodes: ['N07'] },
      { id: 'p5_splice', name: '多片段智能拼接', desc: '硬切 / 淡入淡出 + 首尾色彩过渡', agent: 'PostProduction', nodes: ['N08'] },
      { id: 'p5_post', name: '全局后处理优化', desc: '人脸修复 / 降噪 / 锐化 / 调色 / 帧率提升', agent: 'PostProduction', nodes: ['N08'] },
      { id: 'p5_qc', name: '自动化品控校验', desc: '人脸 ID 相似度 + 场景匹配度 + 时长', agent: 'PostProduction', nodes: ['N06'] },
    ],
  },
}

const FEED_CAP = 12

const state = reactive({
  pid: '',
  runId: '',
  runStatus: 'idle',   // idle | running | done | failed | error
  traceId: '',
  progress: 0,          // 0..100
  nodes: {},            // N01..N09 -> { status, progress }
  logs: [],             // 虚拟日志：{ ts, level, node, msg }
  alerts: [],           // 告警：{ ts, level, reason, ... }
  finalOutput: '',
  result: null,         // 成片快照（quality_report / global_style_ref ...）
  feed: [],             // 实时生成小任务：{ id, kind, title, status, progress, steps, log, ts }
  stageStats: null,     // 阶段统计：{ assets, segments, production, ts }
  phase: 1,             // 当前阶段 1..5，由 DirectorOrb.vue 按路由同步
  // ---- DirectorOrb v2 ----
  orbEnabled: true,     // 灰度开关（后端 ORB_DISABLED 时置 false，前端回退旧版）
  orbState: 'idle',     // 六态机：idle/watching/suggestion/warning/error/success
  readiness: null,      // 接口1：资产就绪度（P1）
  scriptAnalysis: null, // 接口2：叙事结构（P2）
  runSummary: null,     // 接口4：品控摘要（P4）
  synthReport: null,    // 接口6：成片质检（P5）
  suggestions: [],      // 管家建议（含 WS 推送 + 轮询派生，稳定 id）
  dismissed: [],        // 用户忽略的建议 id（localStorage 持久）
  // ---- M3 悬浮球前端化（手册 §4）：三卡信息流 ----
  translator: null,     // 最新选型可视化：{ task_type, checkpoint, node, loras, seed, ts }
  translatorHistory: [],// 最近若干次选型（最多 5）
  decisions: [],        // 待确认 / 已确认决策点（decision_request 推送 + 拉取合并）
  conversation: [],     // 对话气泡：{ id, kind:'notice'|'insight', text, ts }
})

let _unsub = null
let _unsubTr = null
let _pollTimer = null
let _orbPollTimer = null
let _feedSeq = 0
let _activePid = ''
let _suggestedSeq = 0
let _convSeq = 0

const LS_DISMISSED = 'orb_dismissed'

// ---- 内部工具 -------------------------------------------------------------
function _now() { return Date.now() }
function _pushLog(level, node, msg) {
  state.logs.push({ ts: _now(), level, node: node || '', msg })
  if (state.logs.length > 200) state.logs.splice(0, state.logs.length - 200)
}
function _loadDismissed() {
  try {
    const raw = localStorage.getItem(LS_DISMISSED)
    if (raw) return JSON.parse(raw) || []
  } catch (e) { /* ignore */ }
  return []
}
function _saveDismissed() {
  try { localStorage.setItem(LS_DISMISSED, JSON.stringify(state.dismissed)) } catch (e) { /* ignore */ }
}
function _resetRun() {
  state.runId = ''
  state.runStatus = 'idle'
  state.traceId = ''
  state.progress = 0
  state.nodes = {}
  state.finalOutput = ''
  state.result = null
  state.alerts = []
  state.logs = []
}
function _resetOrb() {
  state.readiness = null
  state.scriptAnalysis = null
  state.runSummary = null
  state.synthReport = null
  state.suggestions = []
  // M3 三卡状态重置
  state.translator = null
  state.translatorHistory = []
  state.decisions = []
  state.conversation = []
}

function _ensureNodes() {
  for (const n of NODE_ORDER) {
    if (!state.nodes[n]) state.nodes[n] = { status: 'pending', progress: 0 }
  }
}

// ---- WS 事件处理 ----------------------------------------------------------
function _onEvent(event, data, msg) {
  const d = data || {}
  const channel = (msg && msg.channel) || ''
  // translator 频道：选型可视化（导演选了哪个底模/节点/seed）—— 与 director 频道分离
  if (channel.indexOf('translator:') === 0) {
    _onTranslator(event, d)
    return
  }
  // director 频道：M3 新增 decision_request / notice / insight + 既有事件
  if (event === 'decision_request') {
    const list = Array.isArray(d.decisions) ? d.decisions : []
    state.decisions = list.map(_normalizeDecision).filter(Boolean)
    _recomputeOrbState()
    return
  } else if (event === 'notice') {
    _pushConversation('notice', d.message || d.text || '')
    return
  } else if (event === 'insight') {
    _pushConversation('insight', d.message || d.summary || d.text || '')
    return
  }
  if (event === 'progress') {
    if (d.run_id && !state.runId) {
      state.runId = d.run_id
      state.runStatus = 'running'
      _ensureNodes()
    }
    if (d.run_id) state.runId = d.run_id
    if (d.progress != null) state.progress = Math.round(d.progress * 100)
    const node = d.node
    if (node && state.nodes[node]) {
      state.nodes[node].status = d.status || 'running'
      if (d.progress != null) state.nodes[node].progress = Math.round(d.progress * 100)
    }
    _pushLog('info', node, `节点 ${NODE_TXT[node] || node} → ${d.status || ''}`)
  } else if (event === 'orb_dag_state') {
    // DirectorOrb v2：导演运行态呼吸数据源
    if (d.run_id && !state.runId) state.runId = d.run_id
    if (d.run_id) state.runId = d.run_id
    if (d.status === 'running' && state.runStatus === 'idle') state.runStatus = 'running'
    const node = d.node
    if (node && state.nodes[node]) {
      state.nodes[node].status = d.status || 'running'
      if (d.progress != null) state.nodes[node].progress = Math.round(d.progress * 100)
    }
  } else if (event === 'orb_suggestion') {
    // 后端生产者推送的建议（已后端去重）；前端再按 dismissed 过滤
    const s = d.data || d
    if (!s || !s.id) return
    if (state.dismissed.includes(s.id)) return
    if (!state.suggestions.find((x) => x.id === s.id)) {
      state.suggestions.push({
        id: s.id, project_id: s.project_id || state.pid, phase: s.phase || '',
        severity: s.severity || 'suggestion', category: s.category || '',
        metric_name: s.metric_name, metric_value: s.metric_value, threshold: s.threshold,
        title: s.title || '', detail: s.detail || '', action_type: s.action_type || null,
        action_payload: s.action_payload || null, target_id: s.target_id || null,
        created_at: s.created_at || _now(),
      })
    }
  } else if (event === 'orb_quality_check') {
    state.synthReport = d.data || d || null
  } else if (event === 'completed') {
    state.runStatus = d.ok ? 'done' : 'failed'
    state.progress = 100
    state.finalOutput = d.final_output || ''
    if (d.node_status) {
      for (const [n, s] of Object.entries(d.node_status)) {
        if (state.nodes[n]) state.nodes[n].status = s
      }
    }
    _pushLog(d.ok ? 'success' : 'error', '', d.ok ? '导演全链路完成' : '导演全链路失败')
    if (state.runId) {
      directorRunStatus(state.pid, state.runId)
        .then((r) => { state.result = (r && r.snapshot) || null; if (r && r.final_output) state.finalOutput = r.final_output })
        .catch(() => {})
    }
  } else if (event === 'error') {
    state.runStatus = 'error'
    _pushLog('error', '', '导演运行异常: ' + (d.error || '未知错误'))
  } else if (event === 'alert') {
    const a = { ts: _now(), ...d }
    state.alerts.push(a)
    if (state.alerts.length > 50) state.alerts.splice(0, state.alerts.length - 50)
    _pushLog('warning', '', `告警: ${d.reason || ''}${d.refunded_count ? ' (退冻结金 ' + d.refunded_count + ')' : ''}`)
  }
  _recomputeOrbState()
}

// ---- 订阅 / 轮询 ----------------------------------------------------------
// M3：决策点规范化（options 可能为 JSON 字符串）
function _normalizeDecision(x) {
  if (!x) return null
  let opts = x.options
  if (typeof opts === 'string') {
    try { opts = JSON.parse(opts) } catch (e) { opts = [] }
  }
  return {
    decision_id: x.decision_id,
    phase: x.phase || '',
    category: x.category || 'A',
    title: x.title || '',
    description: x.description || '',
    options: Array.isArray(opts) ? opts : [],
    status: x.status || 'pending',
    answer: x.answer != null ? x.answer : null,
  }
}

// M3：对话气泡（notice/insight）
function _pushConversation(kind, text) {
  if (!text) return
  state.conversation.push({ id: 'conv-' + (++_convSeq), kind, text: String(text), ts: _now() })
  if (state.conversation.length > 30) state.conversation.splice(0, state.conversation.length - 30)
}

// M3：translator 选型可视化事件
function _onTranslator(event, d) {
  if (event !== 'selection') return
  const sel = {
    task_type: d.task_type || '',
    trace_id: d.trace_id || '',
    checkpoint: (d.selected && d.selected.checkpoint) || '',
    node: (d.selected && d.selected.node) || '',
    ip_adapter_weight: (d.selected && d.selected.ip_adapter_weight) != null ? d.selected.ip_adapter_weight : null,
    loras: (d.selected && d.selected.loras) || [],
    seed: (d.selected && d.selected.seed) != null ? d.selected.seed : '',
    ts: d.timestamp || _now(),
  }
  state.translator = sel
  state.translatorHistory = [sel, ...(state.translatorHistory || [])].slice(0, 5)
}

function _subscribe(pid) {
  if (_unsub) { try { _unsub() } catch (e) { /* noop */ }; _unsub = null }
  if (_unsubTr) { try { _unsubTr() } catch (e) { /* noop */ }; _unsubTr = null }
  _unsub = subscribe('director:' + pid, _onEvent)
  // M3：订阅 translator:{pid} 选型可视化频道（导演选了哪个底模/节点/seed）
  _unsubTr = subscribe('translator:' + pid, _onEvent)
}

function _pollFallback() {
  // REDUN-01：WS 不可用时回退轮询
  if (wsConnected()) return
  if (!state.pid) return
  directorRuns(state.pid).then((res) => {
    const runs = (res && res.runs) || []
    if (!state.runId && runs.length) {
      const latest = runs[runs.length - 1]
      if (latest.status === 'running' || latest.status === 'done' || latest.status === 'failed') {
        state.runId = latest.run_id
        state.traceId = latest.trace_id || ''
        state.runStatus = latest.status === 'running' ? 'running' : (latest.status === 'done' ? 'done' : 'failed')
        _ensureNodes()
      }
    }
    if (state.runId && (state.runStatus === 'running' || state.runStatus === 'done' || state.runStatus === 'failed')) {
      directorRunStatus(state.pid, state.runId).then((r) => {
        if (r) {
          if (r.status === 'running' || r.status === 'done' || r.status === 'failed') state.runStatus = r.status
          if (r.snapshot && r.snapshot.node_status) {
            for (const [n, s] of Object.entries(r.snapshot.node_status)) {
              if (state.nodes[n]) state.nodes[n].status = s
            }
          }
          if (r.snapshot) state.result = r.snapshot
          if (r.final_output) state.finalOutput = r.final_output
          if (r.status === 'done') state.progress = 100
        }
      }).catch(() => {})
    }
  }).catch(() => {})
}

// ---- DirectorOrb v2：六态机 + 建议派生 ------------------------------------
function _severityRank(sev) {
  return { info: 0, suggestion: 1, warning: 2, error: 3 }[sev] || 0
}

function _recomputeOrbState() {
  const errorFromAlerts = state.alerts.some((a) => a.level === 'error')
  const vis = visibleSuggestions.value
  const hasError = errorFromAlerts || state.runStatus === 'error' || vis.some((s) => s.severity === 'error')
  if (hasError) { state.orbState = 'error'; return }
  if (vis.some((s) => s.severity === 'warning')) { state.orbState = 'warning'; return }
  if (vis.some((s) => s.severity === 'suggestion')) { state.orbState = 'suggestion'; return }
  if (state.runStatus === 'running') { state.orbState = 'watching'; return }
  if (state.runStatus === 'done') { state.orbState = 'success'; return }
  state.orbState = 'idle'
}

// 从导演 Agent 已有数据派生「管家建议」（稳定 id：der-xxx，便于去重/消解）
function _deriveSuggestions() {
  const out = []
  const now = _now()
  // P1 资产就绪度
  const rd = state.readiness
  if (rd) {
    const primaries = (rd.characters && rd.characters.items) || []
    for (const c of primaries) {
      if (c.is_primary && !c.ready) {
        out.push({
          id: 'der-p1-primary-' + c.id, phase: 'P1', severity: 'warning', category: 'asset_quality',
          title: '主角未就绪', detail: `角色「${c.name}」存在问题：${(c.issues || []).join('、') || '未通过质量检查'}`,
          action_type: 'navigate', action_payload: { path: '/assets' }, target_id: c.id, created_at: now,
        })
      }
    }
    const miss = rd.missing_role_segments || []
    for (const m of miss.slice(0, 6)) {
      out.push({
        id: 'der-p1-missrole-' + m.segment_id, phase: 'P1', severity: 'suggestion', category: 'asset_quality',
        title: `场次 ${m.index} 缺角色绑定`, detail: '该场次没有绑定任何角色资产，进入 P4 前建议补绑',
        action_type: 'navigate', action_payload: { path: '/editor' }, target_id: m.segment_id, created_at: now,
      })
    }
  }
  // P2 叙事结构
  const sa = state.scriptAnalysis
  if (sa) {
    const cons = sa.consecutive_same_scene || []
    if (cons.length) {
      const seg = cons.map((c) => `${c.start}-${c.end}`).join('、')
      out.push({
        id: 'der-p2-consecutive', phase: 'P2', severity: 'suggestion', category: 'script_structure',
        title: `连续同场景 ${cons.length} 处`, detail: `场次 ${seg} 连续同场景，建议变化景别/运镜`,
        action_type: null, created_at: now,
      })
    }
    const empty = sa.empty_description_segments || []
    if (empty.length) {
      out.push({
        id: 'der-p2-empty', phase: 'P2', severity: 'warning', category: 'script_structure',
        title: `${empty.length} 场缺描述`, detail: `场次 ${empty.join('、')} 缺少场景描述，影响后续生成`,
        action_type: null, created_at: now,
      })
    }
  }
  // P4 品控摘要
  const rs = state.runSummary
  if (rs) {
    const failed = rs.failed_segments || []
    if (failed.length) {
      for (const f of failed.slice(0, 6)) {
        out.push({
          id: 'der-p4-fail-' + f.segment_id, phase: 'P4', severity: 'error', category: 'qc_face',
          title: `场次 ${f.index} 生成失败`, detail: '该分镜生成失败，可一键重生成（消耗积分）',
          action_type: 'regenerate', action_payload: { segment_ids: [f.segment_id] }, target_id: f.segment_id, created_at: now,
        })
      }
    }
    if (rs.face_similarity_pass_rate > 0 && rs.face_similarity_pass_rate < 100) {
      out.push({
        id: 'der-p4-face', phase: 'P4', severity: 'warning', category: 'qc_face',
        title: '人脸相似度未全通过', detail: `人脸相似度通过率 ${rs.face_similarity_pass_rate}%（标准≥80%）`,
        action_type: null, metric_name: 'face_similarity_pass_rate', metric_value: rs.face_similarity_pass_rate, threshold: 100, created_at: now,
      })
    }
  }
  // P5 成片质检
  const sr = state.synthReport
  if (sr && sr.degrade_summary && sr.degrade_summary !== '无降级') {
    out.push({
      id: 'der-p5-degrade', phase: 'P5', severity: 'warning', category: 'synth_quality',
      title: '成片存在降级', detail: sr.degrade_summary, action_type: null, created_at: now,
    })
  }
  return out
}

// 合并派生建议（更新稳定 id，移除已消解项；不触碰 WS 推送的 uuid 建议）
function _mergeDerived(derived) {
  const derivedIds = new Set(derived.map((d) => d.id))
  const byId = {}
  for (const d of derived) byId[d.id] = d
  for (const s of state.suggestions) {
    if (s.id && s.id.startsWith('der-') && derivedIds.has(s.id)) {
      const d = byId[s.id]
      s.title = d.title; s.detail = d.detail; s.severity = d.severity
      s.action_type = d.action_type; s.action_payload = d.action_payload
      s.target_id = d.target_id; s.phase = d.phase; s.category = d.category
      s.metric_name = d.metric_name; s.metric_value = d.metric_value; s.threshold = d.threshold
    }
  }
  // 移除已消解的派生项
  state.suggestions = state.suggestions.filter((s) => !(s.id && s.id.startsWith('der-') && !derivedIds.has(s.id)))
  // 新增派生项
  for (const d of derived) {
    if (!state.suggestions.find((x) => x.id === d.id)) state.suggestions.push(d)
  }
}

async function refreshOrb() {
  if (!state.pid || !state.orbEnabled) return
  const pid = state.pid
  const tasks = [
    orbApi.assetReadiness(pid).then((r) => { state.readiness = r }).catch((e) => { if (e && e.code === 'ORB_DISABLED') state.orbEnabled = false }),
    orbApi.scriptAnalysis(pid).then((r) => { state.scriptAnalysis = r }).catch((e) => { if (e && e.code === 'ORB_DISABLED') state.orbEnabled = false }),
    orbApi.runSummary(pid).then((r) => { state.runSummary = r }).catch((e) => { if (e && e.code === 'ORB_DISABLED') state.orbEnabled = false }),
    orbApi.synthesizeReport(pid).then((r) => { state.synthReport = r }).catch((e) => { if (e && e.code === 'ORB_DISABLED') state.orbEnabled = false }),
  ]
  await Promise.all(tasks)
  if (state.orbEnabled) {
    _mergeDerived(_deriveSuggestions())
    _recomputeOrbState()
  }
}

// ---- 对外 API -------------------------------------------------------------
export function useDirectorOrb() {
  return { state, NODE_TXT, NODE_KIND, NODE_ORDER, PHASE_SKILLS }
}

export function directorOrbInit(pid, phase) {
  if (!pid) return
  if (pid !== _activePid) {
    _activePid = pid
    state.pid = pid
    state.phase = phase || 1
    state.dismissed = _loadDismissed()
    _resetRun()
    _resetOrb()
    _subscribe(pid)
    refreshDecisions()  // M3：兜底拉取待确认决策点（与 WS 推送合并）
    fetchStageStats(pid)
  }
  if (!_pollTimer) {
    _pollTimer = setInterval(_pollFallback, 3000)
  }
  if (!_orbPollTimer) {
    _orbPollTimer = setInterval(refreshOrb, 5000)
    refreshOrb()
  }
}

export function directorOrbDestroy() {
  if (_unsub) { try { _unsub() } catch (e) { /* noop */ }; _unsub = null }
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null }
  if (_orbPollTimer) { clearInterval(_orbPollTimer); _orbPollTimer = null }
  _activePid = ''
}

export function directorOrbSetPhase(phase) {
  if (phase && phase >= 1 && phase <= 5) state.phase = phase
}

// 各阶段页上报实时生成小任务（文本/图/视频）
// kind: 'text' | 'image' | 'video' | 'audio'
// status: 'running' | 'done' | 'failed'
export function pushGeneration(kind, title, status, detail) {
  const ts = _now()
  const existing = state.feed.find((f) => f.kind === kind && f.title === title && f.status === 'running')
  if (existing) {
    existing.status = status
    existing.ts = ts
    existing.progress = status === 'running' ? (existing.progress || 0) : 100
    if (detail) existing.log.push({ ts, level: status === 'failed' ? 'error' : 'info', msg: detail })
    return existing.id
  }
  const id = 'feed-' + (++_feedSeq)
  const steps = [
    { name: '提交任务', status: 'done' },
    { name: '模型推理中', status: status === 'running' ? 'running' : (status === 'done' ? 'done' : 'failed') },
    { name: '产出结果', status: status === 'done' ? 'done' : (status === 'failed' ? 'failed' : 'pending') },
  ]
  const entry = {
    id, kind, title, status, progress: status === 'running' ? 12 : 100,
    steps, ts, log: [{ ts, level: 'info', msg: detail || (status === 'running' ? '开始生成…' : '生成结束') }],
  }
  state.feed.push(entry)
  if (state.feed.length > FEED_CAP) state.feed.splice(0, state.feed.length - FEED_CAP)
  return id
}

// 追加一条虚拟日志到某个进行中的小任务（用于阶段页逐步回报）
export function appendGenerationLog(kind, title, level, msg) {
  const f = state.feed.find((x) => x.kind === kind && x.title === title && x.status === 'running')
  if (f) f.log.push({ ts: _now(), level: level || 'info', msg })
}

// ---- 阶段统计（轻量：用于悬浮窗「当前进度」可视化） ----------------------
// 按当前路由阶段一次拉齐 P1–P4 所需数据；P5 复用 state.result（来自快照）
export async function fetchStageStats(pid) {
  if (!pid) return
  const out = { ts: Date.now(), assets: null, segments: null, production: null }
  try { out.assets = await listProjectAssets(pid) } catch (e) { /* ignore */ }
  try { out.segments = await listSegments(pid) } catch (e) { /* ignore */ }
  try { out.production = await getProduction(pid) } catch (e) { /* ignore */ }
  state.stageStats = out
  return out
}

// ---- DirectorOrb v2 对外状态/操作 ----------------------------------------
const visibleSuggestions = computed(() =>
  state.suggestions.filter((s) => !state.dismissed.includes(s.id))
)
const decisionItems = computed(() =>
  visibleSuggestions.value.filter((s) => s.severity === 'error')
)
const unreadCount = computed(() => visibleSuggestions.value.length)

export function dismissSuggestion(id) {
  if (!state.dismissed.includes(id)) {
    state.dismissed.push(id)
    _saveDismissed()
  }
  _recomputeOrbState()
}

// 批量操作代理（P4 重生成/跳过/降档）：CLOSE-04 由后端 pre_auth 保证
export async function runOrbBatch(action, segmentIds, params) {
  if (!state.pid) return null
  try {
    const r = await orbApi.batchAction(state.pid, { action, segment_ids: segmentIds, params: params || {} })
    _pushLog('success', '', `管家操作 ${action} 已提交（${r.queued || 0} 个分镜）`)
    await refreshOrb()
    return r
  } catch (e) {
    _pushLog('error', '', `管家操作 ${action} 失败：${e && e.message ? e.message : e}`)
    return null
  }
}

// M3：拉取项目待确认决策点（与 WS 推送合并，避免错过已推送的清单）
export async function refreshDecisions() {
  if (!state.pid) return
  try {
    const r = await listDecisionsApi(state.pid, 'pending')
    const list = (r && r.decisions) || []
    const norm = list.map(_normalizeDecision).filter(Boolean)
    const byId = {}
    for (const x of state.decisions) byId[x.decision_id] = x
    for (const x of norm) if (!byId[x.decision_id]) byId[x.decision_id] = x
    state.decisions = Object.values(byId)
  } catch (e) { /* 忽略：WS 推送为主，拉取为兜底 */ }
}

// M3：用户提交决策答案（决策卡点选回传 → POST /decisions/{id}/resolve）
export async function resolveDecision(decisionId, answer) {
  if (!state.pid) return null
  try {
    const r = await resolveDecisionApi(state.pid, decisionId, answer)
    state.decisions = state.decisions.map((x) =>
      (x.decision_id === decisionId ? { ...x, status: 'resolved', answer: answer } : x))
    _pushLog('success', '', `已提交决策「${decisionId || ''}」：${answer || ''}`)
    return r
  } catch (e) {
    _pushLog('error', '', `决策提交失败：${e && e.message ? e.message : e}`)
    return null
  }
}

export { visibleSuggestions, decisionItems, unreadCount }
