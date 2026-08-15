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
  if (!opts) return opts
  // 允许调用方通过 opts.timeout 覆盖默认超时
  const targetMs = opts.timeout || ms
  if (!opts.signal && typeof AbortSignal !== 'undefined' && AbortSignal.timeout) {
    opts.signal = AbortSignal.timeout(targetMs)
  }
  delete opts.timeout
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
export const post = (path, body, opts = {}) => apiV1(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), ...opts })
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
// model_config 可选：{ provider:'local'|'openai-compatible', model, apiBase, apiKey }
export const splitStory = (pid, body, opts = {}) =>
  apiV1('/projects/' + pid + '/split-story', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...opts,
  })
export const getLocalModels = () => get('/models/local').then((r) => r.models || [])

// ---- P4 M1 新模型零代码接入 ----
export const scanModels = () => get('/models/scan').then((r) => r.models || [])
export const discoverModel = (name) => get('/models/' + encodeURIComponent(name) + '/discover')
export const registerModel = (body) => post('/models/register', body)
export const listModels = (status) =>
  get('/models' + (status ? ('?status=' + status) : '')).then((r) => r.models || [])
export const getOllamaStatus = () => get('/ollama/status').then((r) => r || { ok: false, models: [] })
export const getSegment = (pid, sid) => get('/projects/' + pid + '/segments/' + sid).then((r) => ({ segment: r.segment, shots: r.shots || [] }))
export const updateSegment = (pid, sid, body) => patch('/projects/' + pid + '/segments/' + sid, body).then((r) => r.segment)
export const putShots = (pid, sid, shots) => put('/projects/' + pid + '/segments/' + sid + '/shots', { shots }).then((r) => r.shots || [])
export const putAssetRefs = (pid, sid, refs) => put('/projects/' + pid + '/segments/' + sid + '/asset-refs', { asset_refs: refs }).then((r) => r.asset_refs || [])
// F1：删除资产/阶段后，级联清理所有片段（含未在内存加载的）中的悬空 @引用。
// 以 asset_id（可选 stage_id）为键「全局」清理 segment_asset_refs 与 shots.asset_refs。
export const pruneAssetRefs = (projectId, { assetId, stageId } = {}) =>
  post('/projects/' + projectId + '/prune-refs', { asset_id: assetId, stage_id: stageId || null })
export const deleteSegment = (pid, sid) => del('/projects/' + pid + '/segments/' + sid)
// 分镜生视频 / 重生成：ComfyUI 多帧渲染耗时远超默认 30s，必须放宽超时，
// 否则出现「signal timed out」假失败（前端已取消但后端仍在跑）。30 分钟上限。
export const generateSegment = (pid, sid, preAuthId, params) =>
  post('/projects/' + pid + '/segments/' + sid + '/generate', { pre_auth_id: preAuthId, params }, { timeout: 1800000 })
export const regenerateSegment = (pid, sid, preAuthId, params) =>
  post('/projects/' + pid + '/segments/' + sid + '/regenerate', { pre_auth_id: preAuthId, params }, { timeout: 1800000 })
// 片段导出（视频文件合成）放宽至 10 分钟
export const exportSegment = (pid, sid, fmt) => post('/projects/' + pid + '/segments/' + sid + '/export', { format: fmt }, { timeout: 600000 })
// Phase 5 合成全集：把项目下所有已生成成功的片段拼接为一个完整视频（长任务，30 分钟）
export const synthesizeProject = (pid, preAuthId, options = {}) =>
  post('/synthesize', { project_id: pid, pre_auth_id: preAuthId, options }, { timeout: 1800000 })

// ---- Director Agent Core 一站式生成（N01→N09 全链路编排）----
// POST 启动后台编排，返回 { run_id, status:'running', trace_id }；进度经 WS 频道 director:<pid> 推送
export const directorRun = (pid, body = {}) =>
  post('/projects/' + pid + '/director-run', body)
// GET 列出项目下的运行记录（内存态）
export const directorRuns = (pid) =>
  get('/projects/' + pid + '/director-run')
// GET 查询单次运行的进度/结果
export const directorRunStatus = (pid, runId) =>
  get('/projects/' + pid + '/director-run/' + runId)

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

// ---- Phase 1 一键从剧本提取 + 批量创建本集资产 ----
export const extractAssetsFromStory = (pid, story, modelConfig, opts = {}) =>
  post('/projects/' + pid + '/extract-assets', { story, model_config: modelConfig }, { timeout: 300000, ...opts }).then((r) => r || { roles: [], scenes: [], props: [], meta: {} })
export const batchCreateProjectAssets = (pid, assets) =>
  post('/projects/' + pid + '/assets/batch', { assets }).then((r) => r || { created: [], skipped: [], count: 0 })

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
// P4 M2 批量调度：队列视图 + 优先级调整
export const getTaskQueue = () => get('/tasks/queue').then((r) => r || { tasks: [], gpu_max_concurrent: 1 })
export const updateTaskPriority = (id, priority) => post('/tasks/' + id + '/priority', { priority }).then((r) => r || {})
// P4 M3 健壮性：手动清理 ComfyUI 显存（优雅 free memory，不杀进程）
export const clearComfyCache = () => post('/resource/clear-comfy-cache').then((r) => r || {})

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
export const testModelConnection = (body) => post('/models/test', body)
export const getDashboard = () => get('/home/dashboard').then((r) => r.dashboard || {})

// ---- 健康检查 ----
export const healthCheck = () => get('/health')

// ---- M2 资源注册表（能力维度声明 / 模型无关）----
export const getResourceCatalog = () => get('/resource/catalog')

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

// ---- 设备算力探测与能力分级（多设备兼容：诚实告知显存与视频生成可行性）----
// GET /device/capabilities → 完整探测（GPU 名称 / 总显存 / 图像·视频生成可行性分级）
export const deviceCapabilities = () =>
  get('/device/capabilities').then((r) => r || {})
// GET /video/readiness → 视频生成前置闸门（前端在 generateSegment 前读此接口，
// 不可行时诚实提示，避免用户等待 30 分钟后才 OOM 假失败）
export const videoReadiness = () =>
  get('/video/readiness').then((r) => r || { feasible: true, reason: '', device: {} })

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
// P1 M4 复用阶段：角色卡跨项目深拷贝导入（POST /projects/{pid}/assets/import）
// 深拷贝含 512 维向量 + 身份卡 + 参考图集；同名幂等。
export const importRole = (assetId, projectId) =>
  post('/projects/' + projectId + '/assets/import', { asset_id: assetId }).then((r) => r.asset)
// 列出可导入的历史角色卡（其他项目 / 全局库中的 role 资产）
export const listImportableRoles = (projectId) =>
  get('/projects/' + projectId + '/assets/importable-roles').then((r) => r.items || [])
// P1 M3/M5 权重前置诚实探测：四杠杆所需权重就绪状态
export const getConsistencyReadiness = () =>
  get('/consistency/readiness').then((r) => r || { weights: {}, all_ready: false, missing: [] })

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

// ---- 真人资产 512 维身份特征向量（手册 §5.1 / §5.3，Layer 3 资产调用接口）----
export const getAssetFaceVectors = (assetId) =>
  get('/assets/' + assetId + '/face-vectors').then((r) => r.vectors || {})
export const getStageFaceVector = (assetId, stageId) =>
  get('/assets/' + assetId + '/stages/' + stageId + '/face-vector').then((r) => r)
export const extractStageFaceVector = (assetId, stageId) =>
  post('/assets/' + assetId + '/stages/' + stageId + '/face-vector/extract').then((r) => r)
// 角色卡 AI 生成（导演agent优化草稿 #1）：经意图翻译引擎产出首张定妆照，确定性 seed 不随机
export const generateCharacterCard = (assetId, body = {}) =>
  post('/assets/' + assetId + '/generate-card', body).then((r) => r || {})

// ---- P1 M1 角色身份系统（身份卡）----
export const getIdentityCard = (assetId) =>
  get('/assets/' + assetId + '/identity-card').then((r) => r || {})
export const patchIdentityCard = (assetId, body = {}) =>
  patch('/assets/' + assetId + '/identity-card', body).then((r) => r || {})

// ---- P1 M2 Lock 锁定阶段（标准肖像 + 参考图集）----
// generate：一键生成标准肖像 + 4 张参考图（光线一致），每张提取 512 维向量；
// 依赖（ComfyUI / IP-Adapter / insightface）缺失时后端诚实抛错。
export const generateReferenceSet = (assetId) =>
  post('/assets/' + assetId + '/reference-set/generate', {}, { timeout: 1200000 }).then((r) => r || {})
export const getReferenceSet = (assetId) =>
  get('/assets/' + assetId + '/reference-set').then((r) => r || { items: [] })
export const regenerateReference = (assetId, refKind) =>
  post('/assets/' + assetId + '/reference-set/' + refKind + '/regenerate', {}, { timeout: 300000 }).then((r) => r || {})

// M3 悬浮球前端化 · 导演决策点 + 角色卡增强管线（手册 §4.2）
// ① 决策点：列出项目待确认/已确认清单；提交用户答案回传悬浮球决策卡
export const listDecisions = (projectId, status) =>
  get('/projects/' + projectId + '/decisions' + (status ? ('?status=' + status) : ''))
    .then((r) => r || { decisions: [], count: 0 })
export const resolveDecision = (projectId, decisionId, answer) =>
  post('/projects/' + projectId + '/decisions/' + decisionId + '/resolve', { answer }).then((r) => r || {})
// ② 从剧本生成角色（六步确定性管线 v2）：projectId 作用域，触发 translator/insight 推送
export const generateCharacterCardV2 = (projectId, assetId, body = {}) =>
  post('/projects/' + projectId + '/assets/' + assetId + '/generate-card-v2', body).then((r) => r || {})

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
// 一键生成 4 格漫画（复用导演 Agent 生图约束：N03 全局风格参考 + §5.2 角色一致性）；
// 单次请求完整生成全部 4 格，长任务放宽超时至 10 分钟。
export const t2iComic = (body) =>
  post('/t2i/comic', body, { timeout: 600000 }).then((r) => r || {})
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

// 服务与版本管理器 ·「提交记录」页签：读取仓库 Git 提交历史（只读）
export const getGitLog = (limit = 50) => get(`/system/git-log?limit=${limit}`).then((r) => r || {})

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

// ---- DirectorOrb v2（悬浮球化身）接口 ----
// 全部挂在 /api/v1/orb 下，受 module_flags.director_orb_v2 灰度开关保护（默认关闭）。
// 任一接口返回 ApiError(code:'ORB_DISABLED') 时，前端回退到旧版悬浮球。
export const orbApi = {
  assetReadiness: (pid) => get('/orb/projects/' + pid + '/asset-readiness'),
  scriptAnalysis: (pid) => get('/orb/projects/' + pid + '/script-analysis'),
  intentPreview: (shotId) => get('/orb/shots/' + shotId + '/intent-preview'),
  runSummary: (pid) => get('/orb/projects/' + pid + '/run-summary'),
  synthesizeReport: (pid) => get('/orb/projects/' + pid + '/synthesize-report'),
  batchAction: (pid, body) => post('/orb/projects/' + pid + '/batch-action', body),
}

// ---- LoRA 训练模块（手册 §8 / 设计思路 §8）----
export const loraPresets = () => get('/lora/presets')
export const createLoraTrain = (body) => post('/lora/train', body).then((r) => r.task)
export const listLoraTrain = (params) => {
  const q = params ? '?' + new URLSearchParams(params).toString() : ''
  return get('/lora/train' + q).then((r) => r)
}
export const getLoraTrain = (id) => get('/lora/train/' + id).then((r) => r)
export const cancelLoraTrain = (id) => post('/lora/train/' + id + '/cancel').then((r) => r)
export const loraCaption = (body) => post('/lora/caption', body).then((r) => r)
export const listLoraModels = (params) => {
  const q = params ? '?' + new URLSearchParams(params).toString() : ''
  return get('/lora/models' + q).then((r) => r)
}
export const registerLoraModel = (id) => post('/lora/models/' + id + '/register').then((r) => r)
export const enableLoraModel = (id) => post('/lora/models/' + id + '/enable').then((r) => r)
export const disableLoraModel = (id) => post('/lora/models/' + id + '/disable').then((r) => r)
export const deleteLoraModel = (id) => del('/lora/models/' + id).then((r) => r)
export const validateLoraModel = (id) => post('/lora/models/' + id + '/validate').then((r) => r)

// ---- LoRA 数据集上传（multipart）----
export async function uploadLoraDataset(files, loraType) {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  form.append('lora_type', loraType || 'style')
  const r = await fetch(BASE + '/lora/dataset/upload', {
    method: 'POST',
    body: form,
  })
  const ct = r.headers.get('content-type') || ''
  let body = null
  if (ct.indexOf('application/json') >= 0) body = await r.json()
  else if (!r.ok) throw new ApiError('HTTP_' + r.status, (await r.text().catch(() => '')) || r.statusText)
  if (!r.ok) {
    const msg = body?.message || body?.detail || r.statusText
    throw new ApiError(body?.code || ('HTTP_' + r.status), msg)
  }
  if (body && typeof body === 'object' && 'code' in body) {
    if (body.code !== 0) throw new ApiError(body.code, body.message || '未知错误')
    return body.data
  }
  return body
}

// ============================================================
// P3 编排层可插拔：Skills 技能库 / 自定义模板 / 长期记忆
// ============================================================

// ---- P3 M1 Skills 技能库 ----
export const listSkills = () => get('/skills').then((r) => r.skills || [])
export const createSkill = (body) => post('/skills', body).then((r) => r.skill_id)
export const getSkill = (id) => get('/skills/' + id).then((r) => r.skill)
export const updateSkill = (id, body) => put('/skills/' + id, body)
export const deleteSkill = (id) => del('/skills/' + id)
// 拖拽执行：填 user_input + context → 后端真调 P0 translator.translate
export const runSkill = (id, body) => post('/skills/' + id + '/run', body).then((r) => r.workflow)

// ---- P3 M2 用户自定义模板（ComfyUI workflow → 语义模板 → 注册进 P0 映射）----
export const importTemplate = (taskType, comfyJson) =>
  post('/templates/import', { task_type: taskType, comfy_json: comfyJson }).then((r) => r)
export const listUserTemplates = () => get('/user-templates').then((r) => r.templates || [])
export const testUserTemplate = (id) => post('/templates/' + id + '/test').then((r) => r)

// ---- P3 M3 长期记忆 / 资产沉淀（跨项目复用）----
export const rememberProject = (pid) => post('/projects/' + pid + '/remember').then((r) => r.remembered)
export const recommendMemory = () => get('/memory/recommend').then((r) => r.items || [])
export const importMemory = (pid, memoryIds) =>
  post('/projects/' + pid + '/import-memory', { memory_ids: memoryIds }).then((r) => r)
