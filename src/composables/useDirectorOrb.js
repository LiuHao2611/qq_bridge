// ============================================================
// useDirectorOrb —— 导演 Agent 悬浮球共享状态（单例）
// 职责（对应执行手册 Layer3 前端交互 + KB §18 WS 网关）：
//   - 订阅 WS 频道 `director:<pid>`，复用现有 progress/completed/error/alert 事件
//   - WS 不可用时按 REDUN-01 回退轮询 directorRuns / directorRunStatus
//   - 维护 N01–N09 DAG 节点状态、虚拟日志、告警、0–1 成片结果
//   - 提供 pushGeneration(kind,title,status,detail) 供各阶段页上报「正在生成的文本/图/视频」
//     实时小任务，满足悬浮窗「可折叠虚拟日志窗口显示小任务生命周期」
//   - 导出 P1–P5 阶段技能清单，供 DirectorOrb.vue 按当前阶段聚焦展示
// 纯内存状态，不写文件、不进 C 盘；模块级 reactive 即单例。
// ============================================================
import { reactive } from 'vue'
import { subscribe, wsConnected } from '../ws'
import { directorRuns, directorRunStatus, listProjectAssets, listSegments, getProduction } from '../api'

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
})

let _unsub = null
let _pollTimer = null
let _feedSeq = 0
let _activePid = ''

// ---- 内部工具 -------------------------------------------------------------
function _now() { return Date.now() }
function _pushLog(level, node, msg) {
  state.logs.push({ ts: _now(), level, node: node || '', msg })
  if (state.logs.length > 200) state.logs.splice(0, state.logs.length - 200)
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

function _ensureNodes() {
  for (const n of NODE_ORDER) {
    if (!state.nodes[n]) state.nodes[n] = { status: 'pending', progress: 0 }
  }
}

// ---- WS 事件处理 ----------------------------------------------------------
function _onEvent(event, data) {
  const d = data || {}
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
    // 拉取完整快照（quality_report / global_style_ref）
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
}

// ---- 订阅 / 轮询 ----------------------------------------------------------
function _subscribe(pid) {
  if (_unsub) { try { _unsub() } catch (e) { /* noop */ }; _unsub = null }
  _unsub = subscribe('director:' + pid, _onEvent)
}

function _pollFallback() {
  // REDUN-01：WS 不可用时回退轮询
  if (wsConnected()) return
  if (!state.pid) return
  // 1) 检测是否有进行中/新产生的运行
  directorRuns(state.pid).then((res) => {
    const runs = (res && res.runs) || []
    if (!state.runId && runs.length) {
      // 取最新一条作为当前跟踪对象
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
    _resetRun()
    _subscribe(pid)
    // 进入项目即拉阶段统计（异步、不阻塞）
    fetchStageStats(pid)
  }
  if (!_pollTimer) {
    _pollTimer = setInterval(_pollFallback, 3000)
  }
}

export function directorOrbDestroy() {
  if (_unsub) { try { _unsub() } catch (e) { /* noop */ }; _unsub = null }
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null }
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
  // P1 资产库（本集）
  try { out.assets = await listProjectAssets(pid) } catch (e) { /* ignore */ }
  // P2 / P3 分镜
  try { out.segments = await listSegments(pid) } catch (e) { /* ignore */ }
  // P4 生产视图（含任务状态分布）
  try { out.production = await getProduction(pid) } catch (e) { /* ignore */ }
  state.stageStats = out
  return out
}
