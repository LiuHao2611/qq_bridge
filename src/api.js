// ============================================================
// API 层：统一调用本地 FastAPI 后端 /api/v1
// 所有接口返回 {code, data, message, timestamp} 信封；本层自动解包。
// 当后端不可用时降级到 mock（页面各自处理）。
// ============================================================

const BASE = '/api/v1'

class ApiError extends Error {
  constructor(code, message) {
    super(message)
    this.code = code
    this.name = 'ApiError'
  }
}

function _withTimeout(opts, ms = 30000) {
  if (opts && !opts.signal && typeof AbortSignal !== 'undefined' && AbortSignal.timeout) {
    opts.signal = AbortSignal.timeout(ms)
  }
  return opts
}

async function _fetchJson(path, opts) {
  _withTimeout(opts, 30000)
  const r = await fetch(BASE + path, opts)
  const ct = r.headers.get('content-type') || ''
  let body = null
  if (ct.indexOf('application/json') >= 0) {
    try { body = await r.json() } catch { body = null }
  } else if (!r.ok) {
    const text = await r.text().catch(() => '')
    throw new ApiError('HTTP_' + r.status, text || r.statusText)
  }
  if (!r.ok) {
    const msg = body?.message || body?.detail || r.statusText
    throw new ApiError(body?.code || ('HTTP_' + r.status), msg)
  }
  if (body && typeof body === 'object' && 'code' in body) {
    if (body.code !== 0) {
      throw new ApiError(body.code, body.message || '未知错误')
    }
    return body.data
  }
  return body
}

export function apiV1(path, opts = {}) {
  return _fetchJson(path, opts)
}

export const get = (path, opts = {}) => apiV1(path, { method: 'GET', ...opts })
export const post = (path, body) => apiV1(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const put = (path, body) => apiV1(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const patch = (path, body) => apiV1(path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const del = (path) => apiV1(path, { method: 'DELETE' })

// ---- 用户 / 积分 ----
export const fetchProfile = () => get('/user/profile')
export const estimatePoints = (params) => {
  const q = new URLSearchParams(params).toString()
  return get('/user/points?' + q)
}
export const preAuthPoints = (amount, operationType, idempotencyKey) =>
  post('/points/pre-auth', { amount, operation_type: operationType, idempotency_key: idempotencyKey })

// ---- 项目 ----
export const listProjects = (params) => {
  const q = params ? '?' + new URLSearchParams(params).toString() : ''
  return get('/projects' + q).then((r) => {
    const items = r.items || r.projects || []
    items.pagination = r.pagination
    return items
  })
}
export const getProject = (id) => get('/projects/' + id).then((r) => r.project)
export const createProject = (body) => post('/projects', body).then((r) => r.project)
export const updateProject = (id, body) => patch('/projects/' + id, body).then((r) => r.project)
export const deleteProject = (id) => del('/projects/' + id)
export const duplicateProject = (id) => post('/projects/' + id + '/duplicate')
export const restoreProject = (id) => post('/projects/' + id + '/restore')

// ---- 片段 / 分镜 ----
// 后端信封 data 字段为 items（与 /projects、/memoirs 一致），此前误读 r.segments 导致永远返回 []
export const listSegments = (pid) => get('/projects/' + pid + '/segments').then((r) => r.items || r.segments || [])
export const createSegment = (pid, body) => post('/projects/' + pid + '/segments', body).then((r) => r.segment)
export const splitStory = (pid, body) => post('/projects/' + pid + '/split-story', body)
export const getSegment = (pid, sid) => get('/projects/' + pid + '/segments/' + sid).then((r) => ({ segment: r.segment, shots: r.shots || [] }))
export const updateSegment = (pid, sid, body) => patch('/projects/' + pid + '/segments/' + sid, body).then((r) => r.segment)
export const putShots = (pid, sid, shots) => put('/projects/' + pid + '/segments/' + sid + '/shots', { shots }).then((r) => r.shots || [])
export const putAssetRefs = (pid, sid, refs) => put('/projects/' + pid + '/segments/' + sid + '/asset-refs', { asset_refs: refs }).then((r) => r.asset_refs || [])
export const deleteSegment = (pid, sid) => del('/projects/' + pid + '/segments/' + sid)
export const generateSegment = (pid, sid, preAuthId, params) =>
  post('/projects/' + pid + '/segments/' + sid + '/generate', { pre_auth_id: preAuthId, params })
export const regenerateSegment = (pid, sid, preAuthId, params) =>
  post('/projects/' + pid + '/segments/' + sid + '/regenerate', { pre_auth_id: preAuthId, params })
export const exportSegment = (pid, sid, fmt) => post('/projects/' + pid + '/segments/' + sid + '/export', { format: fmt })
// Phase 5 合成全集：把项目下所有已生成成功的片段拼接为一个完整视频
export const synthesizeProject = (pid, preAuthId, options = {}) =>
  post('/synthesize', { project_id: pid, pre_auth_id: preAuthId, options })

// ---- Phase 4 生产视图（KB §5.6 模块五 AI视频生成 / §18 WebSocket / §06 Webhook）----
// 生产视图：片段列表 + 各自最新生成任务（分发与实时监看的数据源）
export const getProduction = (pid) =>
  get('/projects/' + pid + '/production').then((r) => r || { project: {}, segments: [] })
// 推理引擎健康与队列概览（供生产页展示引擎状态）
export const inferenceHealth = () =>
  get('/inference/health').then((r) => r || { comfyui: {}, ollama: {}, queue_depth: 0, ws_connections: 0, mode: 'unknown', async_callback: '' })

// ---- 资产 ----
export const listAssets = (type) => {
  const q = type ? '?type=' + encodeURIComponent(type) : ''
  return get('/assets' + q).then((r) => {
    const items = r.items || r.assets || []
    items.pagination = r.pagination
    return items
  })
}
export const createAsset = (body) => post('/assets', body).then((r) => r.asset)
export const updateAsset = (id, body) => patch('/assets/' + id, body).then((r) => r.asset)
export const deleteAsset = (id) => del('/assets/' + id)

// ---- Phase 1 真实文件上传（全局资产 / 本集资产）----
// uploadGlobalAsset: POST /assets/upload → 全局资产（is_global=1），跨项目复用
// uploadProjectAsset: POST /projects/{id}/assets → 本集资产（is_global=0，绑定 project_id）
export const uploadGlobalAsset = (file, opts = {}) => {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('name', (opts.name || (file && file.name) || '素材').toString().replace(/\.[^.]+$/, ''))
  fd.append('type', opts.type || 'scene')
  if (opts.roleType) fd.append('role_type', opts.roleType)
  if (opts.description) fd.append('description', opts.description)
  if (opts.tags) fd.append('tags', Array.isArray(opts.tags) ? JSON.stringify(opts.tags) : opts.tags)
  return _fetchJson('/assets/upload', { method: 'POST', body: fd }).then((r) => r || {})
}
export const uploadProjectAsset = (pid, file, opts = {}) => {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('name', (opts.name || (file && file.name) || '素材').toString().replace(/\.[^.]+$/, ''))
  fd.append('type', opts.type || 'scene')
  if (opts.roleType) fd.append('role_type', opts.roleType)
  if (opts.description) fd.append('description', opts.description)
  if (opts.tags) fd.append('tags', Array.isArray(opts.tags) ? JSON.stringify(opts.tags) : opts.tags)
  return _fetchJson('/projects/' + pid + '/assets', { method: 'POST', body: fd }).then((r) => r || {})
}
// 本集(项目)资产列表（is_global=0 AND project_id=?）
export const listProjectAssets = (pid, params = {}) => {
  const q = new URLSearchParams(params).toString()
  return get('/projects/' + pid + '/assets' + (q ? '?' + q : '')).then((r) => r.items || [])
}

// ---- 任务 ----
export const listTasks = (params) => {
  const q = params ? '?' + new URLSearchParams(params).toString() : ''
  return get('/tasks' + q).then((r) => {
    const items = r.items || r.tasks || []
    items.pagination = r.pagination
    return items
  })
}
export const getTask = (id) => get('/tasks/' + id).then((r) => r.task)
// STR-01：任务 + 生成日志（实时轮询用，无 WebSocket）
export const getTaskLogs = (id) => get('/tasks/' + id).then((r) => ({ task: r.task, logs: r.logs || [] }))
export const cancelTask = (id) => post('/tasks/' + id + '/cancel')
export const retryTask = (id) => post('/tasks/' + id + '/retry')
export const pauseTask = (id) => post('/tasks/' + id + '/pause')
export const resumeTask = (id) => post('/tasks/' + id + '/resume')

// ---- 通知（KB notifications，本地用 messages 表） ----
export const listNotifications = (params) => {
  const q = params ? '?' + new URLSearchParams(params).toString() : ''
  return get('/notifications' + q).then((r) => {
    const items = r.items || []
    items.pagination = r.pagination
    return items
  })
}
export const markAllNotificationsRead = () => post('/notifications/read-all')
export const markNotificationRead = (id) => post('/notifications/' + id + '/read')

// ---- 设置 / 首页 ----
export const getSettings = () => get('/settings').then((r) => r.settings || {})
export const updateSettings = (body) => patch('/settings', body).then((r) => r.settings || {})
export const getDashboard = () => get('/home/dashboard').then((r) => r.dashboard || {})

// ---- 健康检查 ----
export const healthCheck = () => get('/health')

// ---- 回忆录导演板（AGENT_SPEC_15 真实后端） ----
export const STORYBOARD_COST = 800 // spec §5 storyboard_credits
export const getOrCreateMemoir = (projectId) => get('/projects/' + projectId + '/memoir').then((r) => ({ memoir: r.memoir, chapters: r.chapters || [] }))
export const getMemoir = (memoirId) => get('/memoirs/' + memoirId).then((r) => ({ memoir: r.memoir, chapters: r.chapters || [] }))
export const patchMemoir = (memoirId, body) => patch('/memoirs/' + memoirId, body).then((r) => r.memoir)
export const addTimelineEvent = (memoirId, age, description) =>
  post('/memoirs/' + memoirId + '/timeline-events', { age, description }).then((r) => r.timeline_events || [])
export const deleteTimelineEvent = (memoirId, index) =>
  del('/memoirs/' + memoirId + '/timeline-events/' + index).then((r) => r.timeline_events || [])
export const uploadReferencePhoto = (memoirId, file) => {
  const fd = new FormData()
  fd.append('file', file)
  return _fetchJson('/memoirs/' + memoirId + '/reference-photos', { method: 'POST', body: fd })
    .then((r) => ({ photo: r.photo, reference_photos: r.reference_photos || [] }))
}
export const listChapters = (memoirId) => get('/memoirs/' + memoirId + '/chapters').then((r) => r.chapters || [])
export const createChapter = (memoirId, title) =>
  post('/memoirs/' + memoirId + '/chapters', { title }).then((r) => r.chapter)
export const getChapter = (memoirId, chapterId) => get('/memoirs/' + memoirId + '/chapters/' + chapterId).then((r) => r.chapter)
export const patchChapter = (memoirId, chapterId, body) =>
  patch('/memoirs/' + memoirId + '/chapters/' + chapterId, body).then((r) => r.chapter)
export const planMemoir = (memoirId) => post('/memoirs/' + memoirId + '/planning').then((r) => ({ chapters: r.chapters || [], status: r.status }))
export const generateStoryboard = (memoirId, chapterId, preAuthId) =>
  post('/memoirs/' + memoirId + '/chapters/' + chapterId + '/storyboard', { pre_auth_id: preAuthId })
export const generateChapterVideo = (memoirId, chapterId, preAuthId) =>
  post('/memoirs/' + memoirId + '/chapters/' + chapterId + '/video', { pre_auth_id: preAuthId })
export const getChapterTask = (memoirId, chapterId) => get('/memoirs/' + memoirId + '/chapters/' + chapterId + '/task').then((r) => r.task)
export const preCheck = (memoirId) => get('/memoirs/' + memoirId + '/pre-check')
export const exportPackage = (memoirId) => post('/memoirs/' + memoirId + '/export-package')

// ---- §09 MEM-03 事实锚点（用户对 AI 的硬约束） ----
export const listAnchors = (memoirId) => get('/memoirs/' + memoirId + '/anchors').then((r) => r.anchors || [])
export const createAnchor = (memoirId, body) =>
  post('/memoirs/' + memoirId + '/anchors', body).then((r) => r.anchor)
export const patchAnchor = (memoirId, anchorId, body) =>
  patch('/memoirs/' + memoirId + '/anchors/' + anchorId, body).then((r) => r.anchor)
export const deleteAnchor = (memoirId, anchorId) => del('/memoirs/' + memoirId + '/anchors/' + anchorId)

// ---- §09 MEM-13 章节拖拽重排 ----
export const reorderChapters = (memoirId, chapterIds) =>
  post('/memoirs/' + memoirId + '/chapters/reorder', { chapter_ids: chapterIds }).then((r) => r.chapters || [])

// ---- §09 MEM-09 声音旁白 TTS ----
export const createNarration = (memoirId, chapterId, body) =>
  post('/memoirs/' + memoirId + '/chapters/' + chapterId + '/narrations', body).then((r) => r.narration)
export const listNarrations = (memoirId, chapterId) =>
  get('/memoirs/' + memoirId + '/chapters/' + chapterId + '/narrations').then((r) => r.narrations || [])

// ---- §09 API-06 反馈 ----
export const submitFeedback = (body) => post('/feedback', body).then((r) => r.feedback)
export const listFeedback = (params = {}) => {
  const q = new URLSearchParams(params).toString()
  return get('/feedback' + (q ? '?' + q : '')).then((r) => ({ items: r.items || [], pagination: r.pagination }))
}

// ---- §09 API-04 模板 ----
export const listTemplates = (params = {}) => {
  const q = new URLSearchParams(params).toString()
  return get('/templates' + (q ? '?' + q : '')).then((r) => ({ items: r.items || [], pagination: r.pagination }))
}
export const getTemplate = (id) => get('/templates/' + id).then((r) => r.template)
export const patchTemplate = (id, body) => patch('/templates/' + id, body).then((r) => r.template)
export const deleteTemplate = (id) => del('/templates/' + id)
export const applyTemplate = (id, body) => post('/templates/' + id + '/apply', body).then((r) => r)

// ---- §09 API-03 全局资产库导入 ----
export const importScene = (assetId, projectId) =>
  post('/asset-hub/scenes/' + assetId + '/import', { project_id: projectId }).then((r) => r.asset)
export const importProp = (assetId, projectId) =>
  post('/asset-hub/props/' + assetId + '/import', { project_id: projectId }).then((r) => r.asset)
export const importRole = (assetId, projectId) =>
  post('/asset-hub/roles/' + assetId + '/import', { project_id: projectId }).then((r) => r.asset)

// ---- Phase 1 角色多阶段 + 多姿势（KB §02 character_stages / character_poses）----
export const uploadImage = (file) => {
  const fd = new FormData()
  fd.append('file', file)
  return _fetchJson('/upload-image', { method: 'POST', body: fd }).then((r) => r || {})
}
export const listStagesWithPoses = (assetId) =>
  get('/assets/' + assetId + '/stages-with-poses').then((r) => r.items || [])
export const addStage = (assetId, body) =>
  post('/assets/' + assetId + '/stages', body).then((r) => r.stage)
export const delStage = (assetId, stageId) =>
  del('/assets/' + assetId + '/stages/' + stageId)
export const addPose = (stageId, body) =>
  post('/asset-stages/' + stageId + '/poses', body).then((r) => r.pose)
export const delPose = (stageId, poseId) =>
  del('/asset-stages/' + stageId + '/poses/' + poseId)

// ---- §09 API-05 通知删除 ----
export const deleteNotification = (id) => del('/notifications/' + id)

// ---- AI：润色 / 文生图 / 图生图（本地 Ollama / ComfyUI） ----
export const aiPolish = (content, style = 'cinematic') =>
  post('/ai/polish', { content, style }).then((r) => r || {})
export const t2iGenerate = (body) => post('/t2i/generate', body).then((r) => r || {})
export const t2iTask = (taskId) => get('/t2i/tasks/' + taskId).then((r) => r.task)
export const t2iCancel = (taskId) => post('/t2i/tasks/' + taskId + '/cancel').then((r) => r || {})
export const t2iHistory = (page = 1, size = 60, status = '') => {
  const q = new URLSearchParams({ page: String(page), size: String(size) })
  if (status) q.set('status', status)
  return get('/t2i/tasks?' + q.toString()).then((r) => r || { items: [], pagination: {} })
}
export const servicesStatus = (opts = {}) => get('/services/status', opts).then((r) => r || {})
export const difyConfig = () => get('/services/dify-config').then((r) => r || {})
export const difyStart = () => post('/services/dify/start').then((r) => r || {})
export const difyStop = () => post('/services/dify/stop').then((r) => r || {})
export const i2iGenerate = (body) => post('/i2i/generate', body).then((r) => r || {})
export const aiReverse = (imageUrl) => post('/ai/reverse', { image_url: imageUrl }).then((r) => r || {})
export const creativeGenerate = (body) => post('/creative/generate', body).then((r) => r || {})

// ---- 业务模块 / 服务管理器（服务管理器前端）----
export const getServices = () => get('/system/services').then((r) => r.services || [])
export const controlService = (name, action) => post('/system/service/control', { name, action }).then((r) => r || {})
export const listModules = () => get('/modules').then((r) => r || { modules: [] })
export const snapshotModule = (key, note) => post('/modules/snapshot', { key, note }).then((r) => r || {})
export const rollbackModule = (key, version) => post('/modules/rollback', { key, version }).then((r) => r || {})
export const setModuleEnabled = (key, enabled) => post('/modules/enable', { key, enabled }).then((r) => r || {})

// ---- 日志查看 / 搜索 / 封装（排查用） ----
export const logList = () => get('/logs').then((r) => r || { logs: [] })
export const logContent = (name, opts = {}) => {
  const q = new URLSearchParams()
  if (opts.tail) q.set('tail', String(opts.tail))
  if (opts.grep) q.set('grep', opts.grep)
  if (opts.max_lines) q.set('max_lines', String(opts.max_lines))
  const qs = q.toString()
  return get('/logs/' + encodeURIComponent(name) + (qs ? '?' + qs : '')).then((r) => r || {})
}
export async function logPack(names = []) {
  const q = names && names.length ? '?names=' + encodeURIComponent(names.join(',')) : ''
  const r = await fetch(BASE + '/logs/pack' + q, { method: 'GET' })
  if (!r.ok) throw new Error('打包失败: HTTP ' + r.status)
  const blob = await r.blob()
  if (blob.size === 0) throw new Error('打包失败：下载内容为空，请检查后端服务是否正常')
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'ai_logs_' + Date.now() + '.zip'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// ============================================================
// 旧版兼容：保留原始低层 api()、pollStatus()、工具函数
// ============================================================
export async function api(path, opts) {
  const r = await fetch(path, opts)
  if (!r.ok) {
    let e
    try { e = await r.json() } catch { e = { error: r.statusText } }
    throw new Error(e.error || 'HTTP ' + r.status)
  }
  const ct = r.headers.get('content-type') || ''
  return ct.indexOf('application/json') >= 0 ? r.json() : r.text()
}

export function pollStatus(tid, onProgress, timeoutMs) {
  timeoutMs = timeoutMs || 1800000
  const start = Date.now()
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const s = await api('/api/status?task_id=' + tid)
        if (onProgress) onProgress(s)
        if (s.done) return resolve(s)
        if (s.error) return reject(new Error(s.error))
        if (Date.now() - start > timeoutMs) return reject(new Error('轮询超时'))
        setTimeout(tick, 2500)
      } catch (e) {
        reject(e)
      }
    }
    tick()
  })
}

export function fmtTime(t) {
  if (!t) return ''
  const d = new Date(t)
  const p = (n) => (n < 10 ? '0' + n : '' + n)
  return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes())
}

export function hashHue(s) {
  let h = 0
  for (let i = 0; i < (s || '').length; i++) h = (h * 31 + s.charCodeAt(i)) % 360
  return h
}

// 项目封面缩略图预设（新建项目时可手动选择，未选择时按 id 哈希回退）
export const COVER_PRESETS = [
  { name: '紫韵', bg: 'linear-gradient(135deg, #6d28d9 0%, #be185d 100%)' },
  { name: '深海', bg: 'linear-gradient(135deg, #0f4c81 0%, #06b6d4 100%)' },
  { name: '墨绿', bg: 'linear-gradient(135deg, #064e3b 0%, #10b981 100%)' },
  { name: '暖橙', bg: 'linear-gradient(135deg, #9a3412 0%, #f59e0b 100%)' },
  { name: '玫红', bg: 'linear-gradient(135deg, #9f1239 0%, #ec4899 100%)' },
  { name: '靛青', bg: 'linear-gradient(135deg, #1e3a8a 0%, #6366f1 100%)' },
]

export function coverStyle(id, presetIndex) {
  const idx = presetIndex ?? Number(localStorage.getItem('wn_thumb_' + id))
  if (Number.isFinite(idx) && idx >= 0 && idx < COVER_PRESETS.length) {
    return { background: COVER_PRESETS[idx].bg }
  }
  const h = hashHue(id)
  return {
    background:
      'linear-gradient(135deg, hsl(' + h + ',55%,32%), hsl(' + ((h + 60) % 360) + ',55%,22%))',
  }
}

export const STATUS_TXT = {
  planning: '规划中',
  rendering: '生成中',
  done: '已完成',
  error: '失败',
  draft: '草稿',
}
