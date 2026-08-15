<script setup>
defineOptions({ name: 'FragmentEditorPhase' })
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProject } from '../composables/useProject'
import { useHotkeys } from '../composables/useHotkeys'
import { useAutoSave } from '../composables/useAutoSave'
import { toast } from '../store'
import { ElMessageBox } from 'element-plus'
import { aiPolish, estimatePoints, preAuthPoints, regenerateSegment, exportSegment, updateProject, batchCreateProjectAssets, synthesizeProject } from '../api'
import { saveDraft, loadDraft, clearDraft, draftAge, draftKey } from '../composables/useDraft'
import { pushHistory, genKey } from '../composables/useGenHistory'
import { pushGeneration } from '../composables/useDirectorOrb'
import { useVideoGate } from '../composables/useVideoGate'
import ProjectBookShell from './ProjectBookShell.vue'
import HistoryPanel from './HistoryPanel.vue'
import AssetRichInput from './AssetRichInput.vue'
import RoleEditorModal from './RoleEditorModal.vue'
import AssetStageManager from './AssetStageManager.vue'
import {
  OPTION_RESOLUTIONS, OPTION_RATIOS, OPTION_DURATIONS,
  SHOT_TYPE_OPTIONS, CAMERA_MOVEMENT_OPTIONS, CAMERA_ANGLE_OPTIONS, FOCAL_LENGTH_OPTIONS,
  normalizeShotEnums,
} from '../mock'

// spec §3.1 模型 / 预设枚举（平台约束值，直接存储为枚举 id）
const MODEL_OPTIONS = [
  { value: 'seedance-2.0-mini', label: 'Seedance 2.0 Mini' },
  { value: 'seedance-2.0-pro', label: 'Seedance 2.0 Pro' },
  { value: 'kling-1.6', label: '可灵 1.6' },
  { value: 'jimeng-video-3.0', label: '即梦视频 3.0' },
  { value: 'vidu-q3', label: 'Vidu Q3' },
]
// 预设套餐：把时长上限、速度档位、默认模型、能力权限、画质策略、积分系数打包
// 设计依据：降低用户决策成本；套餐与底层模型是「包含关系」，模型只是执行引擎之一
const PRESET_CONFIG = {
  pro: { value: 'pro', label: '创作旗舰 Pro', slogan: '画质优先 · 标准时长', speed: 'quality', speedLabel: '标准', durationMax: 10, defaultDuration: 8, defaultModel: 'seedance-2.0-pro', allowedModels: ['seedance-2.0-pro', 'seedance-2.0-mini'], features: ['人脸修复', '画面防抖', '色彩增强'], priceFactor: 1.0, desc: '均衡画质与速度，适合日常创作。', color: '#4d9fff', tags: ['画质优先', '标准队列'] },
  'flash-short': { value: 'flash-short', label: '创作旗舰 Flash·短', slogan: '极速出片 · 短片段', speed: 'fast', speedLabel: '极速', durationMax: 6, defaultDuration: 5, defaultModel: 'seedance-2.0-mini', allowedModels: ['seedance-2.0-mini', 'kling-1.6'], features: ['快速队列', '精简后处理'], priceFactor: 0.6, desc: '低价快速验证创意，适合测试脚本。', color: '#00d68f', tags: ['极速', '低价'] },
  'flash-long': { value: 'flash-long', label: '创作旗舰 Flash·长', slogan: '极速出片 · 长片段', speed: 'fast', speedLabel: '极速', durationMax: 12, defaultDuration: 12, defaultModel: 'seedance-2.0-pro', allowedModels: ['seedance-2.0-pro', 'seedance-2.0-mini', 'kling-1.6'], features: ['高速队列', '长片段支持'], priceFactor: 1.3, desc: '快速生成长片段，适合短视频快节奏创作。', color: '#ffb020', tags: ['极速', '长片段'] },
  max: { value: 'max', label: '创作旗舰 Max', slogan: '旗舰画质 · 全能力', speed: 'quality', speedLabel: '旗舰', durationMax: 15, defaultDuration: 12, defaultModel: 'seedance-2.0-pro', allowedModels: ['seedance-2.0-pro', 'vidu-q3'], features: ['人脸修复', '超分辨率', '画面防抖', '色彩增强', '高清分辨率', '真人资产化增强', '返回尾帧'], priceFactor: 2.0, desc: '最高画质与稳定性，专业出片首选。', color: '#ff4d8d', tags: ['旗舰', '全能力'] },
}
const PRESET_LIST = Object.values(PRESET_CONFIG)
const modelLabel = (v) => MODEL_OPTIONS.find((o) => o.value === v)?.label || v
const presetLabel = (v) => PRESET_CONFIG[v]?.label || v
const presetCfg = computed(() => PRESET_CONFIG[p.value?.preset_id] || PRESET_CONFIG['flash-long'])

const route = useRoute()
const router = useRouter()
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'
const PLACEHOLDER = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0%25' stop-color='%2320242e'/><stop offset='100%25' stop-color='%233a3f4d'/></linearGradient></defs><rect width='200' height='200' fill='url(%23g)'/><text x='50%25' y='55%25' dominant-baseline='middle' text-anchor='middle' fill='%236b7283' font-size='70'>🖼️</text></svg>"
const { project: p, load, save, generate, pollTask, switchSegment, addSegment, removeSegment, refreshAssets } = useProject(route.params.id, 'short')
// 视频生成前置闸门：本机显存不足时诚实提示，避免白等 30 分钟
const { ensureVideoReady } = useVideoGate()

// 模型输出自动草稿（P3）：AI 润色成功后保存 descriptions，导航离开/丢改动后可一键还原
const polishDraft = ref(null)
const editorHist = ref(null)
// 历史生成面板（P3 润色 / P4 重新生成 / P5 合成全集）：按 kind 覆盖使用
function onUseEditorHist(record) {
  const pid = p.value?.id || route.params.id
  if (record.kind === 'text') {
    // 润色结果：按 key 还原到当前 activeScenes 的 description
    const map = new Map((record.payload?.items || []).map((it) => [it.key, it.description]))
    let n = 0
    activeScenes.value.forEach((s, i) => {
      const key = SID(s, i)
      if (map.has(key)) { s.description = map.get(key); n++ }
    })
    dirty.value = true
    toast(`已覆盖使用历史润色（${n} 条）`)
  } else if (record.kind === 'video') {
    // 视频结果（重新生成 / 合成全集）：覆盖当前项目视频
    p.value.video_url = record.payload?.video_url || p.value.video_url
    p.value.thumb = record.payload?.thumb || p.value.thumb
    p.value.status = 'done'
    toast('已覆盖使用历史视频')
  }
  if (editorHist.value) editorHist.value.refresh()
}
const SID = (s, i) => s.id || s.scene_id || s.shot_id || `idx_${i}`
function restorePolishDraft() {
  const d = polishDraft.value
  if (!d || !d.data || !Array.isArray(d.data.items)) return
  const map = new Map(d.data.items.map((it) => [it.key, it.description]))
  let applied = 0
  activeScenes.value.forEach((s, i) => {
    const k = SID(s, i)
    if (map.has(k)) { s.description = map.get(k); applied++ }
  })
  dirty.value = true
  toast(`已还原 ${draftAge(d.ts)} 的 AI 润色草稿（应用 ${applied} 条）`)
  polishDraft.value = null
}
function discardPolishDraft() {
  if (polishDraft.value) clearDraft(draftKey.polish(route.params.id))
  polishDraft.value = null
}

onMounted(() => {
  load()
  // 检测 P3 AI 润色草稿
  const d = loadDraft(draftKey.polish(route.params.id))
  polishDraft.value = d && d.data && Array.isArray(d.data.items) && d.data.items.length ? d : null
})

// activeChapterId 与活动片段（_activeSegmentId）双向联动：
// 真实模式下切换片段前先保存当前未保存修改，再从后端拉取目标片段分镜；切换期间抑制 dirty 误标。
const activeChapterId = computed({
  get: () => p.value?._activeSegmentId || (p.value?.chapters[0]?.id || ''),
  set: async (v) => {
    if (!p.value || v === p.value._activeSegmentId) return
    if (USE_MOCK) { p.value._activeSegmentId = v; return }
    try {
      if (dirty.value) await onSave()
      await withSuppressDirty(() => switchSegment(v))
    } catch (e) { /* 错误已在内部 toast */ }
  },
})

// 字段归一化：分镜枚举（shot_type / camera_movement / camera_angle / focal_length）
// 对齐 KB 英文枚举，兼容旧中文历史值与旧 camera_move 字段名
const ready = ref(false)
let normalized = false
const normalizeProject = (val) => {
  if (!val || normalized) return
  normalized = true
  ;(val.scenes || []).forEach((s) => {
    normalizeShotEnums(s)
    if (s.voiceover === undefined) s.voiceover = ''
  })
  nextTick(() => { ready.value = true })
}
watch(() => p.value, normalizeProject, { immediate: true })

// ---- 顶部参数 ----
const activeChapter = computed(() => p.value?.chapters.find((c) => c.id === activeChapterId.value) || null)
const activeSeq = computed(() => (p.value ? p.value.chapters.findIndex((c) => c.id === activeChapterId.value) : -1) + 1)
const prevChapter = () => {
  const idx = p.value ? p.value.chapters.findIndex((c) => c.id === activeChapterId.value) : -1
  if (idx > 0) activeChapterId.value = p.value.chapters[idx - 1].id
}
const nextChapter = () => {
  const idx = p.value ? p.value.chapters.findIndex((c) => c.id === activeChapterId.value) : -1
  if (idx >= 0 && idx < p.value.chapters.length - 1) activeChapterId.value = p.value.chapters[idx + 1].id
}
const activeScenes = computed(() =>
  p.value ? p.value.scenes.filter((s) => s.chapter_id === activeChapterId.value) : []
)
const totalSeconds = computed(() => (p.value ? p.value.chapters.reduce((a, c) => a + (Number(c.duration) || 0), 0) : 0))
const doneCount = computed(() => (p.value ? p.value.scenes.filter((s) => s.thumb).length : 0))
const logCount = computed(() => (p.value?.gen_log ? p.value.gen_log.split('\n').filter(Boolean).length : 0))

// BR-003：当前片段分镜时长之和 == 片段时长（spec §5 / §6.2）
const shotSum = computed(() => activeScenes.value.reduce((a, s) => a + (Number(s.duration) || 0), 0))
const br003Mismatch = computed(() => shotSum.value !== (Number(p.value?.duration) || 0))
// BR-009：片段资产引用最多 9 个（spec §3.3 segment_asset_refs）
const MAX_REFS = 9
const segRefCount = computed(() => {
  const s = new Set()
  activeScenes.value.forEach((sh) => (sh.refs || []).forEach((r) => s.add(refKeyOf(r))))
  return s.size
})
const br009Exceed = computed(() => segRefCount.value > MAX_REFS)

// 引用对象工具：引用以 { asset_id, asset_type, stage_id, name } 表示；
// 唯一键 = asset_id + '::' + stage_id —— 同一角色的不同阶段算不同引用（阶段即最小引用单元）。
// F8 修复：stage_id 可能为 0（整数主键），`||` 会误判为空导致 key 冲突/计数错误，改用 `??` 仅对 null/undefined 兜底
const refKeyOf = (r) => (r && r.asset_id) ? (r.asset_id + '::' + (r.stage_id ?? '')) : ''
const sameRef = (a, b) => refKeyOf(a) === refKeyOf(b)
// 资产名（含阶段名）→ 引用对象。阶段名优先（如「周岩-青年」），其次资产名（「周岩」）。
// F3 修复（R2/R3）：仅匹配「阶段名」（含 stage_id）与「场景/道具资产名」（无阶段概念，stage_id 为空合法）；
// 角色名（非阶段名）不再生成 stage_id:null 的角色级引用，返回 null 由 UI 提示用户改用具体阶段名（如「周岩-青年」）。
const resolveRefByName = (name) => {
  if (!p.value || !name) return null
  // 阶段名优先（如「周岩-青年」）：匹配角色的任一阶段，返回含 stage_id 的引用（阶段即最小引用单元）
  for (const c of (p.value.characters || [])) {
    for (const st of (c.stages || [])) {
      if (st.stage_name === name) return { asset_id: c.id, asset_type: 'role', stage_id: st.stage_id, name: st.stage_name }
    }
  }
  // 场景 / 道具按资产名解析（它们无阶段概念，stage_id 为空是合法的资产级引用）
  for (const a of (p.value.assets?.scenes || [])) {
    if (a.name === name) return { asset_id: a.id, asset_type: 'scene', stage_id: null, name: a.name }
  }
  for (const a of (p.value.assets?.images || [])) {
    if (a.name === name) return { asset_id: a.id, asset_type: 'scene', stage_id: null, name: a.name }
  }
  for (const a of (p.value.assets?.props || [])) {
    if (a.name === name) return { asset_id: a.id, asset_type: 'prop', stage_id: null, name: a.name }
  }
  // 角色名（非阶段名）不生成引用：违反「阶段为引用单元 / 角色须≥1阶段才可引用」
  return null
}
// 由资产对象构造基础引用对象（资产级，无阶段）
const assetRefObj = (a, type) => ({ asset_id: a.id, asset_type: type, stage_id: null, name: a.name })

// 状态机：is_dirty + 自动保存（spec §2.1）
const dirty = ref(false)
const loaded = ref(false)
let suppressDirty = false
// 包裹程序化重载（切换/新增/删除片段），期间抑制 dirty 误标，重载完成后复位
const withSuppressDirty = async (fn) => {
  suppressDirty = true
  try { await fn() } finally { /* noop */ }
  await nextTick() // 让重载引起的所有响应式刷新先行 flush
  dirty.value = false
  suppressDirty = false
}
watch(() => p.value, () => { if (loaded.value && !suppressDirty) dirty.value = true }, { deep: true })
watch(() => ready.value, (v) => { if (v) loaded.value = true })
// STR-03：首帧模式切换后立即持久化（独立于通用 save，避免漏存）
watch(() => p.value?.first_frame_mode, (v) => {
  if (!ready.value || !v) return
  updateProject(route.params.id, { first_frame_mode: v })
    .catch((e) => toast('首帧模式保存失败: ' + (e.message || e)))
})

// 后端积分公式：base(180) × 时长 × 分辨率系数 × 音频(1.2) × 模型系数
const RES_COEF = { '480p': 0.6, '720p': 1.0, '1080p': 2.0, '2K': 3.0, '4K': 4.0 }
const estimatedCredits = computed(() => {
  if (!p.value) return 0
  const base = 180
  const dur = Number(p.value.duration) || 0
  const res = RES_COEF[p.value.resolution] ?? 1.0
  const aud = p.value.audio ? 1.2 : 1.0
  const factor = presetCfg.value.priceFactor ?? 1.0
  return Math.ceil(base * dur * res * aud * factor)
})

// 模型说明条：套餐 vs 模型的关系，spec §3.1
const modelHint = computed(() => {
  if (!p.value) return ''
  const cfg = presetCfg.value
  const model = modelLabel(p.value.ai_model)
  return `当前套餐「${cfg.label}」：${cfg.desc} 已启用 ${cfg.features.slice(0, 3).join('、')}。底层模型「${model}」仅为执行引擎，时长上限、速度档位、积分单价由套餐决定。`
})

// 切换预设时自动对齐：模型必须落在套餐可用列表内，时长不得超过套餐上限
watch(() => p.value?.preset_id, (id, oldId) => {
  if (!p.value || !id || id === oldId) return
  const cfg = PRESET_CONFIG[id]
  if (!cfg || oldId === undefined) return
  if (!cfg.allowedModels.includes(p.value.ai_model)) p.value.ai_model = cfg.defaultModel
  if ((p.value.duration || 0) > cfg.durationMax) p.value.duration = cfg.defaultDuration
}, { flush: 'post' })

// 视频状态（知识库配色：紫=已生成 / 橙=生成中 / 红=失败 / 灰=待生成）
const vstat = computed(() => {
  const s = p.value?.status
  if (s === 'done') return { text: '已生成', cls: 'ok' }
  if (s === 'rendering') return { text: '生成中', cls: 'rendering' }
  if (s === 'failed') return { text: '失败', cls: 'failed' }
  return { text: '待生成', cls: 'pending' }
})

// 资产胶囊选项（@ 联想）
const mentionOptions = computed(() => {
  if (!p.value) return []
  const out = []
  p.value.characters.forEach((c) => {
    out.push({ value: c.name, label: c.name, kind: 'character' })
    // 阶段名也纳入 @ 联想，使「周岩-青年」这类阶段可直接 @ 引用
    ;(c.stages || []).forEach((st) => out.push({ value: st.stage_name, label: st.stage_name, kind: 'character' }))
  })
  ;['scenes', 'images'].forEach((k) => (p.value.assets[k] || []).forEach((a) => out.push({ value: a.name, label: a.name, kind: 'scene' })))
  ;(p.value.assets.props || []).forEach((a) => out.push({ value: a.name, label: a.name, kind: 'prop' }))
  return out
})

// 接收引用对象（{asset_type}）返回标签类型用于着色
const assetKind = (ref) => {
  if (!ref) return 'scene'
  const t = ref.asset_type || 'role'
  return (t === 'character' || t === 'role') ? 'character' : (t === 'prop' ? 'prop' : 'scene')
}
// 接收引用对象返回展示名（优先 ref.name，否则按 asset_id 反查资产名兜底）
const assetName = (ref) => {
  if (!ref) return ''
  if (ref.name) return ref.name
  const all = [
    ...(p.value?.characters || []),
    ...(p.value?.assets.scenes || []),
    ...(p.value?.assets.images || []),
    ...(p.value?.assets.props || []),
  ]
  return all.find((x) => x.id === ref.asset_id)?.name || ref.asset_id
}
const escapeRegExp = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
// 点击 shot-refs 的 × 时：从 refs 移除，同时把对应 @[name]/[name] 标记从所有解析字段里清除
const removeRef = (shot, ref) => {
  const name = assetName(ref)
  if (name && name !== ref.asset_id) {
    const reMarker = new RegExp(`@\\[${escapeRegExp(name)}\\]`, 'g')
    const reLegacy = new RegExp(`\\[${escapeRegExp(name)}\\]`, 'g')
    const reLoose = new RegExp(`@${escapeRegExp(name)}(?![\\w\\u4e00-\u9fa5])`, 'g')
    const clean = (text) => {
      if (!text) return text
      return text.replace(reMarker, '').replace(reLegacy, '').replace(reLoose, '').replace(/\s+/g, ' ').trim()
    }
    shot.description = clean(shot.description)
    shot.prompt = clean(shot.prompt)
    shot.voiceover = clean(shot.voiceover)
    shot.dialogue = clean(shot.dialogue)
  }
  shot.refs = (shot.refs || []).filter((x) => refKeyOf(x) !== refKeyOf(ref))
  dirty.value = true
}

// 本集：当前项目被分镜引用的资产
const referencedAssets = computed(() => {
  if (!p.value) return []
  const ids = new Set()
  ;(p.value.scenes || []).forEach((s) => (s.refs || []).forEach((r) => ids.add(r.asset_id)))
  const all = [
    ...(p.value.assets.scenes || []),
    ...(p.value.assets.images || []),
    ...(p.value.assets.props || []),
    ...p.value.characters,
  ]
  return all.filter((a) => ids.has(a.id))
})

// 底部片段导航状态角标
const chapterStatus = (ch) => {
  const shots = p.value ? p.value.scenes.filter((s) => s.chapter_id === ch.id) : []
  const done = shots.some((s) => s.thumb)
  return done ? { cls: 'ok', icon: '✓' } : { cls: 'pending', icon: '⏳' }
}

const assetTab = ref('')
const assetTabs = ['角色', '场景', '道具']
// 资产库三按钮：选中其一→只显示该类；都不选→三类全显示
const toggleAssetTab = (t) => { assetTab.value = assetTab.value === t ? '' : t }
const activeAssetType = computed(() => assetTab.value || '角色')

// ---- 资产库接线：新建资产弹窗 ----
const assetDialogVisible = ref(false)
const assetForm = reactive({ name: '', type: 'scene', role_type: 'protagonist', description: '' })
const openCreateAsset = (type) => {
  assetForm.name = ''
  assetForm.type = type || 'scene'
  assetForm.role_type = 'protagonist'
  assetForm.description = ''
  assetDialogVisible.value = true
}
// 已填内容时点遮罩/× 关闭：二次确认防丢稿
const beforeAssetDialogClose = (done) => {
  if (assetForm.name || assetForm.description) {
    ElMessageBox.confirm('新建资产有未提交内容，关闭后将丢失。确定关闭？', '未保存内容', { confirmButtonText: '丢弃', cancelButtonText: '继续填写', type: 'warning' })
      .then(() => done()).catch(() => {})
  } else { done() }
}
const confirmCreateAsset = async () => {
  const name = (assetForm.name || '').trim()
  if (!name) { toast('请填写资产名称'); return }
  try {
    await batchCreateProjectAssets(route.params.id, [{
      name,
      type: assetForm.type,
      role_type: assetForm.type === 'role' ? assetForm.role_type : undefined,
      description: assetForm.description,
      tags: [],
    }])
    await refreshAssets() // 即时反映到左侧资产库与分镜引用候选
    assetDialogVisible.value = false
    toast('已创建资产：' + name)
  } catch (e) { toast('创建失败: ' + (e.message || e)) }
}

// ---- 小云雀式角色编辑弹窗（左侧资产库角色卡片 hover 三按钮：拖动/编辑/预览） ----
const editorVisible = ref(false)
const editingRole = ref(null)
const previewRoleImg = ref('')
// 角色卡片（p.characters）由 useProject 从 assets 表映射而来：id=asset_id, thumb=cover_url, desc=description, relation=中文定位
const RELATION_TO_ROLE_TYPE = { '主角': 'protagonist', '配角': 'supporting', '反派': 'antagonist' }
const openRoleEditor = (c) => {
  editingRole.value = {
    id: c.id,
    name: c.name,
    roleType: RELATION_TO_ROLE_TYPE[c.relation] || 'protagonist',
    prompt: c.desc,
    voice: c.voice || '',
    url: c.thumb,
  }
  editorVisible.value = true
}
// 角色统一存于后端 assets 表，保存后刷新左侧资产库与映射后的 p.characters
const onRoleSaved = async () => {
  try { await refreshAssets() } catch (e) { /* 忽略刷新异常，弹窗已提示成功 */ }
}

// 左侧资产卡展开/收起：角色、场景、道具都支持展开阶段/变体内联横条
const expandedAssets = reactive({})
const toggleAssetExpand = (a) => { expandedAssets[a.id] = !expandedAssets[a.id] }
// 阶段/姿势增删后刷新资产库，使左侧折叠态计数与列表保持最新
const onStageChanged = async () => {
  try { await refreshAssets() } catch (e) { /* 忽略刷新异常 */ }
}
// 场景/道具阶段编辑弹窗（角色仍走 RoleEditorModal）
const assetStagePanel = reactive({ open: false, asset: null })
const openAssetStagePanel = (a) => { assetStagePanel.open = true; assetStagePanel.asset = a }
const closeAssetStagePanel = () => { assetStagePanel.open = false; assetStagePanel.asset = null }

// ---- 资产库接线：分镜引用资产选择器（BR-009 上限 9） ----
const refDialogVisible = ref(false)
const refShot = ref(null)
const refDraft = ref([])
const assetOptions = computed(() => {
  if (!p.value) return []
  const out = []
  ;(p.value.characters || []).forEach((c) => out.push({ value: c.id, label: c.name, type: '角色' }))
  ;(p.value.assets?.scenes || []).forEach((a) => out.push({ value: a.id, label: a.name, type: '场景' }))
  ;(p.value.assets?.props || []).forEach((a) => out.push({ value: a.id, label: a.name, type: '道具' }))
  return out
})
// 弹窗选择器用的资产列表（带缩略图）。角色按「阶段」拆分——每个阶段是一个可引用单元（BR：角色须经由阶段引用）；
// 无阶段的角色不会出现在此，从而天然满足「无阶段角色不可使用」。
const refAssetOptions = computed(() => {
  if (!p.value) return []
  const out = []
  const pushRef = (refObj, typeLabel, thumb) => out.push({ ...refObj, type: typeLabel, thumb: thumb || '', _ref: refObj })
  ;(p.value.characters || []).forEach((c) => {
    ;(c.stages || []).forEach((st) => pushRef(
      { asset_id: c.id, asset_type: 'role', stage_id: st.stage_id, name: st.stage_name },
      '角色·阶段', st.reference_image_url || c.thumb,
    ))
  })
  ;(p.value.assets?.scenes || []).forEach((a) => pushRef(assetRefObj(a, 'scene'), '场景', a.thumb))
  ;(p.value.assets?.images || []).forEach((a) => pushRef(assetRefObj(a, 'scene'), '图片', a.thumb))
  ;(p.value.assets?.props || []).forEach((a) => pushRef(assetRefObj(a, 'prop'), '道具', a.thumb))
  return out
})
// 富文本提示词组件用的资产列表（带缩略图）；角色按阶段展开，使阶段名也能被 @ 联想 / 拖入气泡显示阶段缩略图
const richInputAssets = computed(() => {
  if (!p.value) return []
  const out = []
  ;(p.value.characters || []).forEach((c) => {
    out.push(c)
    ;(c.stages || []).forEach((st) => out.push({
      id: c.id, name: st.stage_name, type: c.relation || 'role',
      thumb: st.reference_image_url || c.thumb, asset_id: c.id, stage_id: st.stage_id,
    }))
  })
  ;(p.value.assets?.scenes || []).forEach((a) => out.push(a))
  ;(p.value.assets?.images || []).forEach((a) => out.push(a))
  ;(p.value.assets?.props || []).forEach((a) => out.push(a))
  return out
})
const openRefPicker = (shot) => {
  refShot.value = shot
  refDraft.value = (shot.refs || []).map((r) => ({ ...r }))
  refDialogVisible.value = true
}
const isRefSelected = (item) => refDraft.value.some((r) => refKeyOf(r) === refKeyOf(item._ref))
const toggleRefDraft = (item) => {
  const key = refKeyOf(item._ref)
  const idx = refDraft.value.findIndex((r) => refKeyOf(r) === key)
  if (idx >= 0) {
    refDraft.value.splice(idx, 1)
    return
  }
  const others = new Set()
  activeScenes.value.forEach((s) => { if (s !== refShot.value) (s.refs || []).forEach((r) => others.add(refKeyOf(r))) })
  if (!others.has(key) && others.size + refDraft.value.length >= MAX_REFS) {
    toast('片段资产引用最多 9 个（BR-009），无法再加入该引用')
    return
  }
  refDraft.value.push({ ...item._ref })
}
const confirmRefs = () => {
  const shot = refShot.value
  if (!shot) return
  // BR-009 守卫：去掉本镜后片段其余唯一引用（保留其对象）+ 候选去重，总数不得 > 9
  const others = []
  activeScenes.value.forEach((s) => { if (s !== shot) (s.refs || []).forEach((r) => { if (!others.some((x) => refKeyOf(x) === refKeyOf(r))) others.push(r) }) })
  const map = new Map()
  others.forEach((r) => map.set(refKeyOf(r), r))
  refDraft.value.forEach((r) => map.set(refKeyOf(r), r))
  if (map.size > MAX_REFS) {
    toast('片段资产引用最多 9 个（BR-009），当前将达 ' + map.size + ' 个')
    return
  }
  shot.refs = [...map.values()]
  refDialogVisible.value = false
  dirty.value = true
  toast('已更新引用资产')
}

// ---- Phase 3：@提及 → 资产引用（refs）----
// 解析分镜画外音 / 对白里的 @角色 / @场景 / @阶段，把命中的引用对象写回 shot.refs，
// 供后端多模态注入（IP-Adapter / ControlNet，按 stage_id 注入对应阶段人脸向量）。去重并受 BR-009（≤9）约束。
// 引用以对象 { asset_id, asset_type, stage_id, name } 表示；resolveRefByName 负责按名解析（阶段名优先）。
const syncMentionRefs = (shot, override) => {
  if (!shot) return
  const existing = new Map() // refKey -> ref
  ;(shot.refs || []).forEach((rf) => { if (rf && rf.asset_id) existing.set(refKeyOf(rf), rf) })
  const vv = override && override.field === 'voiceover' ? override.value : (shot.voiceover || '')
  const dd = override && override.field === 'dialogue' ? override.value : (shot.dialogue || '')
  const pp = override && override.field === 'prompt' ? override.value : (shot.prompt || '')
  const desc = override && override.field === 'description' ? override.value : (shot.description || '')
  const text = [vv, dd, pp, desc].join('\n')
  // 同时匹配手动 @输入（@资产名）与拖拽插入（@[资产名]）——KB §04/§05 两种写法
  const re = /@\[([^\]]+)\]|@([\u4e00-\u9fa5A-Za-z0-9_·.\-]+)/g
  let mt
  while ((mt = re.exec(text))) {
    const ref = resolveRefByName(mt[1] || mt[2])
    if (ref) existing.set(refKeyOf(ref), ref)
  }
  // F7 修复：原 `[...].slice(0, MAX_REFS)` 在 @提及合并超过 9 个时「静默截断」丢弃引用，用户无感知。
  // 现改为：超上限时保留前 9 个并显式提示，避免无声数据丢失（BR-009 守卫）。
  let merged = [...existing.values()]
  if (merged.length > MAX_REFS) {
    toast('片段资产引用最多 9 个（BR-009），部分提及未纳入引用')
    merged = merged.slice(0, MAX_REFS)
  }
  const changed = merged.length !== (shot.refs || []).length || merged.some((rf, i) => !sameRef(rf, (shot.refs || [])[i]))
  if (changed) {
    shot.refs = merged
    dirty.value = true
  }
  return merged
}
// 编辑器内直接删除 asset-chip 时：syncMentionRefs 是「只增不删」（引用资产弹窗可脱离文本添加 refs），
// 故这里显式移除对应 refs——仅当该资产名在所有解析字段都不再出现时才删，避免误删仍在画外音/对白中引用的资产
const onChipRemoved = (shot, name) => {
  if (!name) return
  // 同时识别 @[name] / [name] / @name 三种遗留写法，只要任一字段仍提及就不删
  const re = new RegExp(`@\\[${escapeRegExp(name)}\\]|\\[${escapeRegExp(name)}\\]|@${escapeRegExp(name)}(?![\\w\\u4e00-\\u9fa5])`)
  const stillMentioned = ['description', 'prompt', 'voiceover', 'dialogue'].some((f) => re.test(shot[f] || ''))
  if (stillMentioned) return
  const ref = resolveRefByName(name)
  if (ref) {
    const key = refKeyOf(ref)
    shot.refs = (shot.refs || []).filter((x) => refKeyOf(x) !== key)
    dirty.value = true
  }
}

// ---- KB §5.3.4 资产拖拽引用：DragStart 记录引用对象 → Drop 到分镜文本 → 插入 @[资产名] → 合并 shot.refs ----
const DRAG_MIME = 'application/x-manju-asset'
// 拖拽源统一写入引用对象（含 stage_id）；角色卡的拖拽已由 AssetStageManager 的 stage-mini 接管，故此处接收对象即可。
const onAssetDragStart = (event, ref) => {
  const payload = JSON.stringify(ref)
  // 同时写自定义 MIME 与 text/plain 回退（部分浏览器要求 text/plain 才允许拖拽）
  event.dataTransfer.setData(DRAG_MIME, payload)
  event.dataTransfer.setData('text/plain', payload)
  event.dataTransfer.effectAllowed = 'copy'
}
// 段级别（跨本片段所有分镜，排除自身）唯一引用键集合，用于 BR-009 守卫
const segUniqueExcept = (shot) => {
  const s = new Set()
  activeScenes.value.forEach((sh) => { if (sh !== shot) (sh.refs || []).forEach((r) => s.add(refKeyOf(r))) })
  return s
}
// 尝试把引用对象并入 shot.refs，受 BR-009（片段唯一引用 ≤9）约束；返回是否成功并入
const tryMergeRef = (shot, ref) => {
  if (!ref || !ref.asset_id) return false
  const key = refKeyOf(ref)
  const others = segUniqueExcept(shot)
  // F9 修复：原守卫仅检查 `others.size`（排除当前分镜），当「当前分镜已有引用 + 其他分镜也有引用」
  // 时仍可加入，使【片段级（跨全分镜）唯一引用总数】突破 9，与 segRefCount（片段级口径）不一致。
  // 现以片段级总量 segRefCount 为准：仅当待加入的 key 为全新引用且片段已达上限时才拦截。
  const selfKeys = new Set((shot.refs || []).map(refKeyOf))
  if (!others.has(key) && !selfKeys.has(key) && segRefCount.value >= MAX_REFS) {
    toast('片段资产引用最多 9 个（BR-009），无法再加入该引用')
    return false
  }
  const map = new Map((shot.refs || []).map((rf) => [refKeyOf(rf), rf]))
  map.set(key, ref)
  shot.refs = [...map.values()].slice(0, MAX_REFS)
  return true
}
// 文本域接收 drag-drop：在光标处插入 @[资产名]，并同步 shot.refs（与手动 @ 输入共用解析逻辑）
const onFieldDrop = (event, shot, field) => {
  event.preventDefault()
  const raw = event.dataTransfer.getData(DRAG_MIME) || event.dataTransfer.getData('text/plain')
  if (!raw) return
  let ref
  try { ref = JSON.parse(raw) } catch { return }
  if (!ref || !ref.name || !ref.asset_id) return
  const marker = `@[${ref.name}]`
  const el = event.target // 实际发生 drop 的 textarea / input
  const cur = shot[field] || ''
  const start = (typeof el?.selectionStart === 'number') ? el.selectionStart : cur.length
  const end = (typeof el?.selectionEnd === 'number') ? el.selectionEnd : cur.length
  const next = cur.slice(0, start) + marker + cur.slice(end)
  shot[field] = next
  dirty.value = true
  if (field === 'voiceover') syncMentionRefs(shot, { field: 'voiceover', value: next })
  else if (field === 'dialogue') syncMentionRefs(shot, { field: 'dialogue', value: next })
  else tryMergeRef(shot, ref)
  toast('已插入资产引用 @[' + ref.name + ']')
}
const onFieldDragOver = (event) => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy' }
// 提示词富文本输入：拖入资产后由组件插入 @[name]，父级仅负责合并 shot.refs（收到完整引用对象）
const onPromptDrop = (shot, ref) => {
  if (!ref || !ref.asset_id) return
  tryMergeRef(shot, ref)
}
// 拖拽高亮反馈（标出可放置的文本域）
const dropActive = ref('')
const dropKey = (s, f) => s.scene_id + ':' + f
const onFieldDragEnter = (e, key) => { dropActive.value = key }
const onFieldDragLeave = (e, key) => { if (dropActive.value === key) dropActive.value = '' }

// ---- 画面描述弹窗编辑（含左侧资产库）----
const descModalVisible = ref(false)
const descModalShot = ref(null)
const descModalText = ref('')
const openDescModal = (s) => {
  descModalShot.value = s
  descModalText.value = s.description || ''
  descModalVisible.value = true
}
const closeDescModal = () => {
  descModalVisible.value = false
  descModalShot.value = null
  descModalText.value = ''
}
// 点击遮罩/ESC/× 关闭时：若画面描述有未保存修改，二次确认（防误关丢稿）
const beforeDescModalClose = (done) => {
  const s = descModalShot.value
  const orig = s ? (s.description || '') : ''
  if (descModalText.value !== orig) {
    ElMessageBox.confirm('画面描述有未保存的修改，关闭后将丢失。确定关闭？', '未保存修改', { confirmButtonText: '丢弃修改', cancelButtonText: '继续编辑', type: 'warning' })
      .then(() => done()).catch(() => {})
  } else { done() }
}
const saveDescModal = () => {
  const s = descModalShot.value
  if (!s) return
  const text = descModalText.value
  s.description = text
  s.prompt = text
  syncMentionRefs(s, { field: 'description', value: text })
  dirty.value = true
  closeDescModal()
}
// 弹窗内拖入资产：直接插入到弹窗编辑器的 modelValue，由 AssetRichInput 内部处理插入点
const onModalAssetDrop = (ref) => {
  // AssetRichInput 内部已经插入并 emit update:modelValue，这里只需同步 refs
  const s = descModalShot.value
  if (!s || !ref || !ref.asset_id) return
  tryMergeRef(s, ref)
}

const onSave = async () => {
  // Phase 3：保存前把画外音 / 对白里的 @提及 同步进 refs（驱动多模态资产注入）
  ;(p.value?.scenes || []).forEach((s) => syncMentionRefs(s))
  if (br003Mismatch.value) { toast('分镜时长之和不等于片段时长，无法保存（BR-003）'); return false }
  if (br009Exceed.value) { toast('片段资产引用最多 9 个（BR-009）'); return false }
  const ok = await save({ title: p.value.title })
  if (ok) dirty.value = false
  return ok
}
// 自动保存（spec §2.1 状态机延伸）：内容变更后防抖保存，状态可见、失败显式提示（CLOSE-02）
const autoSaveEnabled = ref(!USE_MOCK)
const { status: saveStatus } = useAutoSave({ dirty, save: onSave, enabled: autoSaveEnabled, delay: 1500 })
// 兜底：离开页面时若仍有未保存修改则提示（自动保存已覆盖绝大多数场景）
const _beforeUnload = (e) => {
  if (autoSaveEnabled.value && dirty.value && saveStatus.value !== 'saved') {
    e.preventDefault(); e.returnValue = ''
  }
}
window.addEventListener('beforeunload', _beforeUnload)
onUnmounted(() => window.removeEventListener('beforeunload', _beforeUnload))
const onSyncAll = async () => {
  if (!p.value) return
  const pid = p.value.id
  try {
    // 先估算整片积分并预授权（BR-004：合成全集同样需 pre-auth，禁止占位）
    const segs = p.value.chapters || []
    const totalDur = segs.reduce((a, c) => a + (Number(c.duration) || 0), 0)
    const est = await estimatePoints({ operation: 'estimate', duration: totalDur, resolution: p.value.resolution, audio: true })
    const idem = 'idem_syn_' + Date.now() + '_' + Math.random().toString(36).slice(2)
    const pre = await preAuthPoints(est.estimated_credits, 'synthesize', idem)
    const res = await synthesizeProject(pid, pre.pre_auth_id, {})
    toast('已提交「合成全集」任务')
    pollTask(res.task_id, (t) => {
      if (t.status === 'succeeded') {
        p.value.video_url = t.result_url || ''
        p.value.thumb = t.thumbnail_url || ''
        p.value.status = 'done'
        toast('合成全集完成')
        // 记录到左侧历史生成面板
        const h = pushHistory(genKey.editor(pid), {
          kind: 'video',
          title: '合成全集',
          preview: t.thumbnail_url || '',
          previewText: '视频',
          payload: { video_url: t.result_url || '', thumb: t.thumbnail_url || '' },
        })
        if (h && editorHist.value) editorHist.value.refresh()
      } else if (t.status === 'failed') {
        p.value.status = 'failed'
        toast('合成失败: ' + (t.error_message || '未知'))
      }
    })
    router.push('/projects/' + pid + '/production')
  } catch (e) { /* toast 已在内部 */ }
}
// 重新生成（spec §2.1 状态机 GENERATING / BR-004 预授权）：预授权 → 重新提交生成 → 轮询任务
const onRegen = async () => {
  if (!p.value) return
  // 视频生成前置闸门：本机显存不足时诚实提示，用户取消即停止（避免白等 30 分钟）
  if (!(await ensureVideoReady())) { toast('已取消：当前设备不满足视频生成条件'); return }
  const pid = p.value.id
  const sid = p.value._activeSegmentId
  const params = {
    duration: p.value.duration,
    resolution: p.value.resolution,
    ratio: p.value.ratio,
    audio: p.value.audio ? 1 : 0,
    model_id: p.value.ai_model,
    asset_humanize: p.value.real_person ? 1 : 0,
    return_last_frame: p.value.return_tail ? 1 : 0,
    seed: p.value.seed,
  }
  try {
    const est = await estimatePoints({ operation: 'estimate', ...params })
    const idem = 'idem_regen_' + Date.now() + '_' + Math.random().toString(36).slice(2)
    const pre = await preAuthPoints(est.estimated_credits, 'segment_regenerate', idem)
    const res = await regenerateSegment(pid, sid, pre.pre_auth_id, params)
    p.value.status = 'rendering'
    toast('已提交重新生成任务')
    pollTask(res.task_id, (t) => {
      if (t.status === 'succeeded') {
        p.value.video_url = t.result_url || ''
        p.value.thumb = t.thumbnail_url || ''
        p.value.status = 'done'
        toast('视频重新生成完成')
        // 记录到左侧历史生成面板
        const h = pushHistory(genKey.editor(pid), {
          kind: 'video',
          title: '重新生成 · 片段',
          preview: t.thumbnail_url || '',
          previewText: '视频',
          payload: { video_url: t.result_url || '', thumb: t.thumbnail_url || '' },
        })
        if (h && editorHist.value) editorHist.value.refresh()
      } else if (t.status === 'failed') {
        p.value.status = 'failed'
        toast('重新生成失败: ' + (t.error_message || '未知'))
      }
    })
  } catch (e) { toast('重新生成失败: ' + (e.message || e)) }
}
const onPolish = async () => {
  const texts = activeScenes.value.map((s) => (s.description || s.caption || '')).filter(Boolean)
  if (!texts.length) { toast('没有可润色的分镜描述'); return }
  pushGeneration('text', 'AI 润色', 'running')
  try {
    const res = await aiPolish(texts.join('\n'), 'cinematic')
    const polished = (res.polished || '').split('\n').map((t) => t.trim()).filter(Boolean)
    if (polished.length) {
      activeScenes.value.forEach((s, i) => { if (polished[i]) s.description = polished[i] })
      // 模型输出成功后立即写草稿（P3）：保存 {key,description} 列表，可按当前 activeScenes key 还原
      const items = []
      activeScenes.value.forEach((s, i) => { if (polished[i]) items.push({ key: SID(s, i), description: polished[i] }) })
      const saved = saveDraft(draftKey.polish(pid || route.params.id), { items })
      if (saved) toast('AI 润色完成（已应用，🗂 草稿已自动保存；未保存就离开可点「↶ 还原润色」）')
      // 记录到左侧历史生成面板
      const h = pushHistory(genKey.editor(pid || route.params.id), {
        kind: 'text',
        title: 'AI 润色 · ' + activeScenes.value.length + ' 条',
        preview: (polished[0] || '').slice(0, 60),
        payload: { items },
      })
      if (h && editorHist.value) editorHist.value.refresh()
      dirty.value = true
      pushGeneration('text', 'AI 润色', 'done')
    } else { toast('润色返回为空，未改动'); pushGeneration('text', 'AI 润色', 'done', '返回为空') }
  } catch (e) { toast('润色失败: ' + (e.message || e)); pushGeneration('text', 'AI 润色', 'failed', e.message) }
}
const autoDistribute = () => {
  const arr = activeScenes.value
  const dur = Number(p.value?.duration) || 0
  if (!arr.length) return
  const base = Math.floor(dur / arr.length)
  let rem = dur - base * arr.length
  arr.forEach((s, i) => { s.duration = base + (i < rem ? 1 : 0) })
  dirty.value = true
  toast('已按片段时长自动均分分镜时长')
}
const onCancel = () => { dirty.value = false; toast('已放弃未保存修改') }
const onExport = async (cmd) => {
  if (!p.value) return
  const pid = p.value.id
  const sid = p.value._activeSegmentId
  if (cmd === 'mp4') {
    if (!p.value.video_url) { toast('视频尚未生成'); return }
    try {
      const r = await exportSegment(pid, sid, 'mp4')
      toast('导出链接：' + (r.download_url || '（见视频预览）'))
    } catch (e) { toast('导出失败: ' + (e.message || e)) }
    return
  }
  const map = { capcut: '导出为剪映草稿', script: '导出分镜脚本', image: '导出为图片' }
  toast('导出：' + (map[cmd] || cmd) + '（演示）')
}
const removeChapterRef = (r) => { activeChapter.value._refs = (activeChapter.value._refs || []).filter((x) => x !== r) }
const onAddChapter = async () => {
  if (!p.value) return
  if (USE_MOCK) {
    const id = 'ch' + (p.value.chapters.length + 1)
    p.value.chapters.push({ id, title: '新片段 ' + (p.value.chapters.length + 1), duration: p.value.duration, count: 1, thumb: '' })
    p.value.scenes.push({ scene_id: 'sc' + Date.now(), chapter_id: id, no: 1, caption: '新分镜', description: '', prompt: '', negative_prompt: '', duration: Math.min(p.value.duration, 4) || 4, shot_type: 'medium', camera_angle: 'eye_level', camera_movement: 'static', action: '', focal_length: '35mm', thumb: '', voiceover: '', dialogue: '', refs: [], setting: '' })
    p.value._activeSegmentId = id
    toast('已新增片段（演示）')
    return
  }
  try {
    await withSuppressDirty(() => addSegment('新片段 ' + (p.value.chapters.length + 1), p.value.duration))
    toast('已新增片段')
  } catch (e) { /* toast 已在内部 */ }
}
const onDeleteChapter = async (ch) => {
  if (!p.value) return
  if (p.value.chapters.length <= 1) { toast('至少保留一个片段'); return }
  try {
    await withSuppressDirty(() => removeSegment(ch.id))
    toast('已删除片段')
  } catch (e) { /* toast 已在内部 */ }
}
const onAddScene = () => {
  if (!p.value || !activeChapterId.value) return
  const id = 'sc' + (p.value.scenes.length + 1)
  p.value.scenes.push({ scene_id: id, chapter_id: activeChapterId.value, no: activeScenes.value.length + 1, caption: '新分镜', description: '', prompt: '', negative_prompt: '', duration: 4, shot_type: 'medium', camera_angle: 'eye_level', camera_movement: 'static', action: '', focal_length: '35mm', thumb: '', voiceover: '', dialogue: '', refs: [], setting: '' })
}

// 分镜高级参数折叠：默认收起，只保留核心文案/引用外露
const advancedOpen = reactive({})
const toggleAdvanced = (id) => { advancedOpen[id] = !advancedOpen[id] }
const shotParamSummary = (s) => {
  const st = SHOT_TYPE_OPTIONS.find((o) => o.value === s.shot_type)
  const ca = CAMERA_ANGLE_OPTIONS.find((o) => o.value === s.camera_angle)
  const cm = CAMERA_MOVEMENT_OPTIONS.find((o) => o.value === s.camera_movement)
  const fl = FOCAL_LENGTH_OPTIONS.find((o) => o.value === s.focal_length)
  const parts = [st?.label, ca?.label, cm?.label, fl?.label].filter(Boolean)
  return parts.join(' · ') || '默认镜头参数'
}
// 对当前片段的分镜按数组顺序重排序号（no = 索引+1），保证顺序与索引一致
const reindexActive = () => {
  if (!p.value) return
  let i = 1
  p.value.scenes.forEach((s) => {
    if (s.chapter_id === activeChapterId.value) { s.no = i; i++ }
  })
}
const onDeleteScene = (s) => {
  if (!p.value) return
  p.value.scenes = p.value.scenes.filter((x) => x.scene_id !== s.scene_id)
  reindexActive()
  dirty.value = true
  toast('已删除分镜')
}
const onCopyScene = (s) => {
  if (!p.value) return
  const clone = { ...s, scene_id: 'sc' + Date.now(), thumb: '', no: 0 }
  const idx = p.value.scenes.findIndex((x) => x.scene_id === s.scene_id)
  p.value.scenes.splice(idx + 1, 0, clone)
  reindexActive()
  dirty.value = true
  toast('已复制分镜')
}
const onMoveScene = (s, dir) => {
  if (!p.value) return
  const arr = p.value.scenes
  const idx = arr.findIndex((x) => x.scene_id === s.scene_id)
  const target = idx + dir
  if (target < 0 || target >= arr.length) return
  // 仅当相邻项同属当前片段时才交换（跨片段移动无意义）
  if (arr[target].chapter_id !== activeChapterId.value) return
  const tmp = arr[idx]; arr[idx] = arr[target]; arr[target] = tmp
  reindexActive()
  dirty.value = true
}

useHotkeys({ save: onSave, undo: () => toast('撤销（演示）'), fullscreen: () => toast('全屏（演示）') })
</script>

<template>
  <ProjectBookShell v-if="p && ready" :step="3" @synthesize="onSyncAll">
    <!-- 左侧：历史生成记录（页面级，固定在内容区左侧，默认折叠，点击展开可选择覆盖使用） -->
    <HistoryPanel
      ref="editorHist"
      :storage-key="genKey.editor(route.params.id)"
      title="生成历史"
      kind-filter="all"
      :floating="true"
      @use="onUseEditorHist"
    />

    <!-- ① 顶部窗口栏 -->
    <div class="frag-top">
      <div class="ftitle">
        <input v-model="p.title" class="ti" @dblclick="toast('重命名（演示）')" />
        <span class="tsub">{{ p.subtitle }}</span>
      </div>
      <div class="fparams">
        <div class="fgrp preset-grp"><label>生成套餐</label>
          <el-select v-model="p.preset_id" size="small" style="width:200px" popper-class="preset-dropdown">
            <template #prefix><span class="preset-dot" :style="{ background: presetCfg.color }"></span></template>
            <el-option v-for="cfg in PRESET_LIST" :key="cfg.value" :label="cfg.label" :value="cfg.value">
              <div class="preset-option">
                <div class="preset-option-head">
                  <span class="preset-dot" :style="{ background: cfg.color }"></span>
                  <span class="preset-option-title">{{ cfg.label }}</span>
                  <span class="preset-tags"><el-tag v-for="t in cfg.tags" :key="t" size="small" effect="plain">{{ t }}</el-tag></span>
                </div>
                <div class="preset-option-meta">{{ cfg.slogan }} · 单段≤{{ cfg.durationMax }}s · 积分系数 {{ cfg.priceFactor }}x</div>
              </div>
            </el-option>
          </el-select>
          <el-popover placement="bottom" :width="320" trigger="hover">
            <template #reference>
              <span class="preset-info-icon">ⓘ</span>
            </template>
            <div class="preset-detail">
              <div class="preset-detail-title" :style="{ color: presetCfg.color }">{{ presetCfg.label }}</div>
              <div class="preset-detail-slogan">{{ presetCfg.slogan }}</div>
              <div class="preset-detail-line"><b>速度档位：</b>{{ presetCfg.speedLabel }}</div>
              <div class="preset-detail-line"><b>单段时长上限：</b>{{ presetCfg.durationMax }} 秒</div>
              <div class="preset-detail-line"><b>默认模型：</b>{{ modelLabel(presetCfg.defaultModel) }}</div>
              <div class="preset-detail-line"><b>可用模型：</b>{{ presetCfg.allowedModels.map(modelLabel).join('、') }}</div>
              <div class="preset-detail-line"><b>积分系数：</b>{{ presetCfg.priceFactor }}x</div>
              <div class="preset-detail-line"><b>画质策略：</b>{{ presetCfg.features.join('、') }}</div>
              <div class="preset-detail-desc">{{ presetCfg.desc }}</div>
            </div>
          </el-popover>
        </div>
        <div class="fgrp"><label>底层模型</label>
          <el-select v-model="p.ai_model" size="small" style="width:170px">
            <el-option v-for="o in MODEL_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </div>
        <div class="fgrp"><label>分辨率</label>
          <el-select v-model="p.resolution" size="small" style="width:96px">
            <el-option v-for="o in OPTION_RESOLUTIONS" :key="o" :label="o" :value="o" />
          </el-select>
        </div>
        <div class="fgrp"><label>比例</label>
          <el-select v-model="p.ratio" size="small" style="width:84px">
            <el-option v-for="o in OPTION_RATIOS" :key="o" :label="o" :value="o" />
          </el-select>
        </div>
        <div class="fgrp"><label>时长</label>
          <el-select v-model="p.duration" size="small" style="width:96px">
            <el-option v-for="o in OPTION_DURATIONS" :key="o" :label="o + '秒'" :value="o" />
          </el-select>
        </div>
        <div class="fgrp"><label>首帧</label>
          <el-select v-model="p.first_frame_mode" size="small" style="width:120px" title="STR-03 首帧机制：是否在视频首帧输出参考图">
            <el-option label="首帧不出图" value="no_output" />
            <el-option label="首帧出图" value="output" />
          </el-select>
        </div>
        <div class="fgrp"><label>🔊 音频</label><el-switch v-model="p.audio" size="small" /></div>
        <el-dropdown trigger="click" @command="onExport">
          <el-button size="small">导出 ▾</el-button>
          <template #dropdown><el-dropdown-menu>
            <el-dropdown-item command="mp4">导出为 MP4</el-dropdown-item>
            <el-dropdown-item command="capcut">导出为剪映草稿</el-dropdown-item>
            <el-dropdown-item command="script">导出分镜脚本</el-dropdown-item>
            <el-dropdown-item command="image">导出为图片</el-dropdown-item>
          </el-dropdown-menu></template>
        </el-dropdown>
        <el-button size="small" @click="onPolish">✨ AI 润色</el-button>
        <template v-if="polishDraft">
          <el-button size="small" type="success" plain @click="restorePolishDraft">↶ 还原润色（{{ draftAge(polishDraft.ts) }}）</el-button>
          <el-button size="small" text @click="discardPolishDraft">丢弃草稿</el-button>
        </template>
        <el-button type="primary" size="small" class="p5-btn p5-btn--primary" @click="onSyncAll">🎞️ 合成全集</el-button>
        <span class="credits-tag">预估 {{ estimatedCredits }} 积分</span>
      </div>
    </div>

    <!-- ② 模型说明条 -->
    <div class="hint-bar">
      <span class="hint-text">{{ modelHint }}</span>
      <label><el-checkbox v-model="p.real_person" /> 真人资产化</label>
      <label><el-checkbox v-model="p.return_tail" /> 返回尾帧</label>
      <label class="seed">seed
        <el-input-number v-model="p.seed" :min="-1" size="small" style="width:110px" />
      </label>
    </div>

    <!-- ④ 主内容区（三栏） -->
    <div class="frag-body">
      <!-- 左：资产库 -->
      <div class="col col-left">
        <h3>资产库 <span class="add-asset" title="新建资产" @click="openCreateAsset(activeAssetType === '角色' ? 'role' : activeAssetType === '场景' ? 'scene' : 'prop')">＋</span></h3>
        <div class="asset-tabs">
          <div v-for="t in assetTabs" :key="t" class="t p5-menu-item" :class="{ on: assetTab === t }" @click="toggleAssetTab(t)">{{ t }}</div>
        </div>
        <div v-if="br009Exceed" class="br-bar bad">⚠ BR-009：片段资产引用 {{ segRefCount }} 个，超过上限 9 个</div>
        <div v-else class="br-bar ok">资产引用 {{ segRefCount }}/9（BR-009）</div>
        <template v-if="assetTab === '' || assetTab === '角色'">
          <div v-for="c in p.characters" :key="c.id" class="char-card" :class="{ expanded: expandedAssets[c.id], disabled: !(c.stages && c.stages.length) }">
            <div class="char-row">
              <img class="char-thumb" :src="c.thumb || PLACEHOLDER" />
              <div class="char-info">
                <div class="char-title">{{ c.name }} <span class="role-badge" :class="c.relation === '配角' ? 'supporting' : 'lead'">{{ c.relation }}</span></div>
                <div class="char-meta">{{ c.desc }}</div>
                <div class="stage-toggle" @click.stop="toggleAssetExpand(c)">
                  <span class="chev" :class="{ open: expandedAssets[c.id] }">▸</span>
                  {{ c.relation }} · {{ (c.stages?.length || 0) }}个阶段
                </div>
              </div>
            </div>
            <div v-if="expandedAssets[c.id]" class="stage-strip-host">
              <AssetStageManager :asset-id="c.id" :asset-name="c.name" :initial-stages="c.stages" type="role" mode="strip" @changed="onStageChanged" />
            </div>
            <div class="drag-hint">
              <template v-if="c.stages && c.stages.length">展开后拖入「阶段卡」到右侧提示词</template>
              <template v-else>⚠ 请先添加阶段才能引用</template>
            </div>
            <!-- 角色卡本身不再作为提示词素材；编辑 / 预览按钮保留以便添加阶段 -->
            <div class="thumb-overlay role-overlay">
              <button class="role-btn" title="编辑角色" @click.stop="openRoleEditor(c)"><el-icon><EditPen /></el-icon></button>
              <button class="role-btn" title="预览角色图" @click.stop="previewRoleImg = c.thumb || ''"><el-icon><ZoomIn /></el-icon></button>
            </div>
          </div>
          <button class="add-line" @click="openCreateAsset('role')">＋ 新建角色</button>
        </template>
        <template v-if="assetTab === '' || assetTab === '场景'">
          <div v-for="a in p.assets.scenes" :key="a.id" class="asset-card" :class="{ expanded: expandedAssets[a.id] }" draggable="true" :title="'拖拽到右侧分镜文本可插入 @[' + a.name + '] 引用'" @dragstart="onAssetDragStart($event, assetRefObj(a, 'scene'))">
            <div class="char-row">
              <img class="char-thumb" :src="a.thumb || PLACEHOLDER" />
              <div class="char-info">
                <div class="char-title">{{ a.name }} <span class="type-badge scene">场景</span></div>
                <div class="stage-toggle" @click.stop="toggleAssetExpand(a)">
                  <span class="chev" :class="{ open: expandedAssets[a.id] }">▸</span>
                  {{ (a.stages?.length || 0) }}个阶段
                </div>
              </div>
            </div>
            <div v-if="expandedAssets[a.id]" class="stage-strip-host">
              <AssetStageManager :asset-id="a.id" :initial-stages="a.stages" type="scene" mode="strip" @changed="onStageChanged" />
            </div>
            <div class="drag-hint">⠿ 拖入提示词</div>
            <div class="thumb-overlay role-overlay">
              <button class="role-btn" title="拖动场景卡" @mousedown.stop><el-icon><Grid /></el-icon></button>
              <button class="role-btn" title="编辑阶段" @click.stop="openAssetStagePanel(a)"><el-icon><EditPen /></el-icon></button>
              <button class="role-btn" title="预览场景图" @click.stop="previewRoleImg = a.thumb || ''"><el-icon><ZoomIn /></el-icon></button>
            </div>
          </div>
          <button class="add-line" @click="openCreateAsset('scene')">＋ 新建场景</button>
        </template>
        <template v-if="assetTab === '' || assetTab === '道具'">
          <div v-for="a in p.assets.props" :key="a.id" class="asset-card" :class="{ expanded: expandedAssets[a.id] }" draggable="true" :title="'拖拽到右侧分镜文本可插入 @[' + a.name + '] 引用'" @dragstart="onAssetDragStart($event, assetRefObj(a, 'prop'))">
            <div class="char-row">
              <img class="char-thumb" :src="a.thumb || PLACEHOLDER" />
              <div class="char-info">
                <div class="char-title">{{ a.name }} <span class="type-badge prop">道具</span></div>
                <div class="stage-toggle" @click.stop="toggleAssetExpand(a)">
                  <span class="chev" :class="{ open: expandedAssets[a.id] }">▸</span>
                  {{ (a.stages?.length || 0) }}个阶段
                </div>
              </div>
            </div>
            <div v-if="expandedAssets[a.id]" class="stage-strip-host">
              <AssetStageManager :asset-id="a.id" :initial-stages="a.stages" type="prop" mode="strip" @changed="onStageChanged" />
            </div>
            <div class="drag-hint">⠿ 拖入提示词</div>
            <div class="thumb-overlay role-overlay">
              <button class="role-btn" title="拖动道具卡" @mousedown.stop><el-icon><Grid /></el-icon></button>
              <button class="role-btn" title="编辑阶段" @click.stop="openAssetStagePanel(a)"><el-icon><EditPen /></el-icon></button>
              <button class="role-btn" title="预览道具图" @click.stop="previewRoleImg = a.thumb || ''"><el-icon><ZoomIn /></el-icon></button>
            </div>
          </div>
          <button class="add-line" @click="openCreateAsset('prop')">＋ 新建道具</button>
        </template>
      </div>

      <!-- 中：片段编辑 -->
      <div class="col col-mid">
        <div class="segment-header">
          <div class="seg-nav-arrows">
            <el-button size="small" text :disabled="activeSeq <= 1" @click="prevChapter">←</el-button>
            <el-dropdown trigger="click" @command="(id) => activeChapterId = id">
              <span class="seg-current">片段 {{ activeSeq }} / {{ p.chapters.length }} · {{ activeChapter?.title || '未命名' }}</span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item
                    v-for="(c, ci) in p.chapters"
                    :key="c.id"
                    :command="c.id"
                    :class="{ active: activeChapterId === c.id }"
                  >片段 {{ ci + 1 }} · {{ c.title }}</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-button size="small" text :disabled="activeSeq >= p.chapters.length" @click="nextChapter">→</el-button>
          </div>
          <div class="seg-actions">
            <el-popconfirm v-if="activeChapter" title="确认删除当前片段？" confirm-button-text="删除" cancel-button-text="取消" @confirm="onDeleteChapter(activeChapter)">
              <template #reference>
                <el-button size="small" text type="danger">删除</el-button>
              </template>
            </el-popconfirm>
            <el-button size="small" type="primary" @click="onAddChapter">＋ 新增片段</el-button>
          </div>
        </div>

        <div v-if="activeChapter" class="editor">
          <div class="ed-head">
            <span class="ed-idx">片段 {{ activeSeq }}</span>
            <input v-model="activeChapter.title" class="ed-title" />
            <span class="ed-meta">{{ activeScenes.length }} 分镜 · {{ activeChapter.duration }}s</span>
          </div>

          <!-- BR-003：分镜时长之和必须等于片段时长（spec §5 / §6.2） -->
          <div v-if="br003Mismatch" class="br-bar bad">⚠ BR-003：分镜时长之和 {{ shotSum }}s ≠ 片段时长 {{ p.duration }}s</div>
          <div v-else class="br-bar ok">分镜时长之和 {{ shotSum }}s = 片段时长 {{ p.duration }}s（BR-003）</div>

          <!-- 场景设定（spec §3.3 segment.scene_description）：用于生成提示词，保留但弱化 -->
          <div class="seg-scene-setting">
            <span class="seg-scene-label">场景设定</span>
            <el-input v-model="p.scene_description" size="small" placeholder="本片段整体时空/环境/基调，将注入生成提示词…" maxlength="240" show-word-limit />
          </div>

          <!-- 分镜块 -->
          <div v-for="s in activeScenes" :key="s.scene_id" class="shot-block">
            <div class="shot-head">
              <span class="idx">{{ s.no }}</span>
              <input v-model="s.caption" class="shot-cap" />
              <el-tag size="small" effect="plain">{{ s.duration }}s</el-tag>
              <div class="shot-ops">
                <el-button size="small" text :disabled="s.no === 1" title="上移" @click="onMoveScene(s, -1)">↑</el-button>
                <el-button size="small" text :disabled="s.no === activeScenes.length" title="下移" @click="onMoveScene(s, 1)">↓</el-button>
                <el-button size="small" text title="复制分镜" @click="onCopyScene(s)">⧉</el-button>
                <el-popconfirm title="确认删除该分镜？" confirm-button-text="删除" cancel-button-text="取消" @confirm="onDeleteScene(s)">
                  <template #reference>
                    <el-button size="small" text type="danger" title="删除分镜">🗑</el-button>
                  </template>
                </el-popconfirm>
              </div>
            </div>
            <div class="sg full drop-field description-field" :class="{ 'drop-active': dropActive === dropKey(s, 'description') }" @dragover.prevent="onFieldDragOver" @dragenter="onFieldDragEnter($event, dropKey(s, 'description'))" @dragleave="onFieldDragLeave($event, dropKey(s, 'description'))">
              <label>画面描述 <span class="drag-tip">可拖入左侧资产</span><span class="desc-expand" title="展开编辑" @click.stop="openDescModal(s)">⛶ 展开编辑</span></label>
              <AssetRichInput v-model="s.description" :assets="richInputAssets" :min-rows="3" :fixed-height="true" placeholder="输入画面描述，拖入左侧资产自动插入 @[资产名] 引用标记" @drop-asset="(a) => onPromptDrop(s, a)" @change="(text) => { syncMentionRefs(s, { field: 'description', value: text }); s.prompt = text }" @remove-asset="(e) => onChipRemoved(s, e && e.name)" />
            </div>
            <div class="shot-voice-row">
              <div class="sg drop-field" :class="{ 'drop-active': dropActive === dropKey(s, 'voiceover') }" @dragover.prevent="onFieldDragOver" @dragenter="onFieldDragEnter($event, dropKey(s, 'voiceover'))" @dragleave="onFieldDragLeave($event, dropKey(s, 'voiceover'))" @drop="onFieldDrop($event, s, 'voiceover')"><label>画外音</label>
                <el-mention v-model="s.voiceover" :options="mentionOptions" :prefix="['@']" placeholder="输入 @ 引用角色 / 场景，或拖入左侧资产…" type="textarea" :autosize="{ minRows: 1, maxRows: 2 }" size="small" @update:modelValue="(v) => syncMentionRefs(s, { field: 'voiceover', value: v })" @change="syncMentionRefs(s)" />
              </div>
              <div class="sg drop-field" :class="{ 'drop-active': dropActive === dropKey(s, 'dialogue') }" @dragover.prevent="onFieldDragOver" @dragenter="onFieldDragEnter($event, dropKey(s, 'dialogue'))" @dragleave="onFieldDragLeave($event, dropKey(s, 'dialogue'))" @drop="onFieldDrop($event, s, 'dialogue')"><label>台词 / 对白</label>
                <el-mention v-model="s.dialogue" :options="mentionOptions" :prefix="['@']" placeholder="输入 @ 引用角色 / 场景，或拖入左侧资产…" type="textarea" :autosize="{ minRows: 1, maxRows: 2 }" size="small" @update:modelValue="(v) => syncMentionRefs(s, { field: 'dialogue', value: v })" @change="syncMentionRefs(s)" />
              </div>
            </div>
            <div class="shot-refs">
              <span class="refs-label">引用资产：</span>
              <span v-for="r in s.refs" :key="refKeyOf(r)" class="asset-tag" :class="assetKind(r)">{{ assetName(r) }} <span class="x" @click="removeRef(s, r)">×</span></span>
              <span v-if="!s.refs.length" class="muted">无</span>
              <span class="ref-add" @click="openRefPicker(s)">＋</span>
              <span class="ref-count">{{ s.refs.length }}/9</span>
            </div>
            <div class="shot-adv-toggle" @click="toggleAdvanced(s.scene_id)">
              <span>{{ advancedOpen[s.scene_id] ? '收起' : '展开' }} 镜头与生成参数</span>
              <span class="shot-adv-summary">{{ shotParamSummary(s) }}</span>
              <span class="shot-adv-chev">{{ advancedOpen[s.scene_id] ? '▲' : '▼' }}</span>
            </div>
            <div v-if="advancedOpen[s.scene_id]" class="shot-advanced">
              <div class="shot-grid">
                <div class="sg"><label>景别</label>
                  <el-select v-model="s.shot_type" size="small" style="width:100%">
                    <el-option v-for="o in SHOT_TYPE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                  </el-select>
                </div>
                <div class="sg"><label>机位</label>
                  <el-select v-model="s.camera_angle" size="small" style="width:100%">
                    <el-option v-for="o in CAMERA_ANGLE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                  </el-select>
                </div>
                <div class="sg"><label>运镜</label>
                  <el-select v-model="s.camera_movement" size="small" style="width:100%">
                    <el-option v-for="o in CAMERA_MOVEMENT_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                  </el-select>
                </div>
                <div class="sg"><label>动作</label>
                  <el-input v-model="s.action" size="small" placeholder="可选：主体动作短语，如『主角推门而入』；留空则由 AI 自动扩写" />
                </div>
                <div class="sg"><label>焦段</label>
                  <el-select v-model="s.focal_length" size="small" style="width:100%">
                    <el-option v-for="o in FOCAL_LENGTH_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                  </el-select>
                </div>
                <div class="sg"><label>时长(s)</label><el-input-number v-model="s.duration" :min="1" :max="30" size="small" style="width:100%" /></div>
              </div>
              <div class="sg full camera-action-preview">
                <label>运镜执行预览（P2·意图与控制）</label>
                <div class="cap-preview-text">
                  运镜：{{ (CAMERA_MOVEMENT_OPTIONS.find(o => o.value === s.camera_movement) || {}).label }}
                  ｜ 动作：{{ s.action || '（留空将由 AI 自动扩写）' }}
                </div>
              </div>
              <div class="sg full"><label>反向提示词</label><el-input v-model="s.negative_prompt" size="small" placeholder="不希望出现的元素，如 blurry, extra hands, watermark…" /></div>
              <div class="sg full drop-field" :class="{ 'drop-active': dropActive === dropKey(s, 'setting') }" @dragover.prevent="onFieldDragOver" @dragenter="onFieldDragEnter($event, dropKey(s, 'setting'))" @dragleave="onFieldDragLeave($event, dropKey(s, 'setting'))" @drop="onFieldDrop($event, s, 'setting')"><label>镜头设定 <span class="drag-tip">可拖入左侧资产</span></label><el-input v-model="s.setting" type="textarea" :autosize="{ minRows: 1, maxRows: 2 }" size="small" placeholder="本分镜的时空、环境、基调等设定…" /></div>
            </div>
          </div>
          <button class="add-line" @click="onAddScene">＋ 新增分镜</button>

          <div class="ws-actions">
            <span class="save-state" :class="saveStatus">
              <i v-if="saveStatus==='saving' || saveStatus==='pending'" class="dot spin" />
              <i v-else-if="saveStatus==='saved'" class="dot ok" />
              <i v-else-if="saveStatus==='error'" class="dot bad" />
              <i v-else class="dot" />
              <span>{{ { saving: '保存中…', pending: '待保存…', saved: '已自动保存', error: '保存失败' }[saveStatus] || '自动保存开启' }}</span>
            </span>
            <el-button size="small" @click="onCancel">取消</el-button>
            <el-button size="small" type="primary" @click="onSave">保存</el-button>
            <el-button size="small" @click="onPolish">AI 润色</el-button>
            <el-button size="small" type="warning" @click="onRegen">重新生成</el-button>
          </div>
        </div>
        <div v-else class="empty">请选择左侧片段</div>
      </div>

      <!-- 右：视频预览 -->
      <div class="col col-right">
        <h3>视频预览 <span class="vstatus" :class="vstat.cls">{{ vstat.text }}</span></h3>
        <div class="preview-box">
          <video v-if="p.video_url" :src="p.video_url" controls class="pv" />
          <div v-else class="empty">🎞️ 暂无视频<br />点击「生成视频」开始合成</div>
        </div>
        <el-collapse :model-value="['params', 'log']" class="mt">
          <el-collapse-item title="模型参数" name="params">
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="模型">{{ p.ai_model }}</el-descriptions-item>
              <el-descriptions-item label="分辨率">{{ p.resolution }}</el-descriptions-item>
              <el-descriptions-item label="比例">{{ p.ratio }}</el-descriptions-item>
              <el-descriptions-item label="时长">{{ p.duration }}s</el-descriptions-item>
              <el-descriptions-item label="音频">{{ p.audio ? '是' : '否' }}</el-descriptions-item>
              <el-descriptions-item label="真人资产">{{ p.real_person ? '是' : '否' }}</el-descriptions-item>
              <el-descriptions-item label="尾帧">{{ p.return_tail ? '是' : '否' }}</el-descriptions-item>
              <el-descriptions-item label="seed">{{ p.seed }}</el-descriptions-item>
              <el-descriptions-item label="taskId">{{ p.remote_task_id || '—' }}</el-descriptions-item>
              <el-descriptions-item label="本机任务">{{ p.local_task_id || '—' }}</el-descriptions-item>
            </el-descriptions>
          </el-collapse-item>
          <el-collapse-item :title="`视频生成日志 (${logCount})`" name="log">
            <pre class="log">{{ p.gen_log }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
    </div>

    <!-- ⑤ 底部片段导航条 -->
    <div class="frag-nav">
      <div class="nav-stats">
        <span>共 {{ p.chapters.length }} 个片段</span>
        <span>约 {{ totalSeconds }} 秒</span>
        <span>成功 {{ doneCount }}</span>
      </div>
        <div class="nav-thumbs">
          <div v-for="(c, ci) in p.chapters" :key="c.id" class="nav-th" :class="{ on: activeChapterId === c.id }" @click="activeChapterId = c.id">
            <div class="nth-ph">
              <span v-if="!c.thumb" class="nth-ph-txt">{{ c.title.slice(0, 2) }}</span>
              <span class="nth-badge" :class="chapterStatus(c).cls">{{ chapterStatus(c).icon }}</span>
              <el-popconfirm title="确认删除该片段？" confirm-button-text="删除" cancel-button-text="取消" @confirm="onDeleteChapter(c)">
                <template #reference>
                  <span class="nth-del" @click.stop title="删除片段">×</span>
                </template>
              </el-popconfirm>
            </div>
            <div class="nth-name">{{ c.title }}</div>
            <div class="nth-dur">{{ c.duration }}s</div>
          </div>
          <div class="nav-th add" @click="onAddChapter">＋</div>
        </div>
    </div>
  </ProjectBookShell>
  <div v-else class="page"><div class="empty">加载中…</div></div>

    <!-- 新建资产弹窗 -->
    <el-dialog v-model="assetDialogVisible" title="新建资产" width="440px" append-to-body :before-close="beforeAssetDialogClose">
      <div class="form-row">
        <label>类型</label>
        <el-radio-group v-model="assetForm.type">
          <el-radio label="role">角色</el-radio>
          <el-radio label="scene">场景</el-radio>
          <el-radio label="prop">道具</el-radio>
        </el-radio-group>
      </div>
      <div v-if="assetForm.type === 'role'" class="form-row">
        <label>角色定位</label>
        <el-select v-model="assetForm.role_type" size="small" style="flex:1">
          <el-option label="主角" value="protagonist" />
          <el-option label="配角" value="supporting" />
          <el-option label="反派" value="antagonist" />
        </el-select>
      </div>
      <div class="form-row">
        <label>名称</label>
        <el-input v-model="assetForm.name" size="small" placeholder="资产名称" style="flex:1" @keyup.enter="confirmCreateAsset" />
      </div>
      <div class="form-row">
        <label>描述</label>
        <el-input v-model="assetForm.description" type="textarea" :autosize="{ minRows: 2 }" size="small" placeholder="描述 / 提示词…" style="flex:1" />
      </div>
      <template #footer>
        <el-button size="small" @click="assetDialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" @click="confirmCreateAsset">创建</el-button>
      </template>
    </el-dialog>

    <!-- 分镜引用资产选择器 -->
    <el-dialog v-model="refDialogVisible" title="引用资产到分镜" width="860px" append-to-body class="ref-picker-dialog" destroy-on-close>
      <p class="muted small">选择资产加入该分镜引用。片段内唯一引用上限 9 个（BR-009）。</p>
      <div class="ref-picker-grid">
        <div v-for="a in refAssetOptions" :key="refKeyOf(a._ref)" class="ref-picker-card" :class="{ selected: isRefSelected(a) }" @click="toggleRefDraft(a)">
          <div class="ref-picker-thumb" @click.stop>
            <el-image v-if="a.thumb" :src="a.thumb" :preview-src-list="[a.thumb]" fit="cover" style="width:100%;height:100%;border-radius:8px;" hide-on-click-modal />
            <div v-else class="ref-picker-no-thumb">@</div>
          </div>
          <div class="ref-picker-info">
            <span class="ref-picker-name" :title="a.name">{{ a.name }}</span>
            <span class="ref-picker-type">{{ a.type }}</span>
          </div>
          <div class="ref-picker-check" @click.stop>
            <el-checkbox :model-value="isRefSelected(a)" size="small" @change="toggleRefDraft(a)" />
          </div>
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="refDialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" @click="confirmRefs">确认引用（{{ refDraft.length }}）</el-button>
      </template>
    </el-dialog>

    <!-- 画面描述大弹窗编辑（带左侧资产库） -->
    <el-dialog v-model="descModalVisible" title="编辑画面描述" width="90%" top="5vh" append-to-body class="desc-modal" destroy-on-close :before-close="beforeDescModalClose">
      <div class="desc-modal-body">
        <div class="desc-modal-assets">
          <h3>资产库 <span class="add-asset" title="新建资产" @click="openCreateAsset(activeAssetType === '角色' ? 'role' : activeAssetType === '场景' ? 'scene' : 'prop')">＋</span></h3>
          <div class="asset-tabs">
            <div v-for="t in assetTabs" :key="t" class="t p5-menu-item" :class="{ on: assetTab === t }" @click="toggleAssetTab(t)">{{ t }}</div>
          </div>
          <div v-if="br009Exceed" class="br-bar bad">⚠ BR-009：片段资产引用 {{ segRefCount }} 个，超过上限 9 个</div>
          <div v-else class="br-bar ok">资产引用 {{ segRefCount }}/9（BR-009）</div>
          <template v-if="assetTab === '' || assetTab === '角色'">
            <div v-for="c in p.characters" :key="c.id" class="char-card" :class="{ expanded: expandedAssets[c.id], disabled: !(c.stages && c.stages.length) }">
              <div class="char-row">
                <img class="char-thumb" :src="c.thumb || PLACEHOLDER" />
                <div class="char-info">
                  <div class="char-title">{{ c.name }} <span class="role-badge" :class="c.relation === '配角' ? 'supporting' : 'lead'">{{ c.relation }}</span></div>
                  <div class="char-meta">{{ c.desc }}</div>
                  <div class="stage-toggle" @click.stop="toggleAssetExpand(c)">
                    <span class="chev" :class="{ open: expandedAssets[c.id] }">▸</span>
                    {{ c.relation }} · {{ (c.stages?.length || 0) }}个阶段
                  </div>
                </div>
              </div>
              <div v-if="expandedAssets[c.id]" class="stage-strip-host">
                <AssetStageManager :asset-id="c.id" :asset-name="c.name" :initial-stages="c.stages" type="role" mode="strip" @changed="onStageChanged" />
              </div>
              <div class="drag-hint">
                <template v-if="c.stages && c.stages.length">展开后拖入「阶段卡」到右侧编辑器</template>
                <template v-else>⚠ 请先添加阶段才能引用</template>
              </div>
              <div class="thumb-overlay role-overlay">
                <button class="role-btn" title="编辑角色" @click.stop="openRoleEditor(c)"><el-icon><EditPen /></el-icon></button>
                <button class="role-btn" title="预览角色图" @click.stop="previewRoleImg = c.thumb || ''"><el-icon><ZoomIn /></el-icon></button>
              </div>
            </div>
            <button class="add-line" @click="openCreateAsset('role')">＋ 新建角色</button>
          </template>
          <template v-if="assetTab === '' || assetTab === '场景'">
            <div v-for="a in p.assets.scenes" :key="a.id" class="asset-card" :class="{ expanded: expandedAssets[a.id] }" draggable="true" :title="'拖拽到右侧编辑器可插入 @[' + a.name + '] 引用'" @dragstart="onAssetDragStart($event, assetRefObj(a, 'scene'))">
              <div class="char-row">
                <img class="char-thumb" :src="a.thumb || PLACEHOLDER" />
                <div class="char-info">
                  <div class="char-title">{{ a.name }} <span class="type-badge scene">场景</span></div>
                  <div class="stage-toggle" @click.stop="toggleAssetExpand(a)">
                    <span class="chev" :class="{ open: expandedAssets[a.id] }">▸</span>
                    {{ (a.stages?.length || 0) }}个阶段
                  </div>
                </div>
              </div>
              <div v-if="expandedAssets[a.id]" class="stage-strip-host">
                <AssetStageManager :asset-id="a.id" :initial-stages="a.stages" type="scene" mode="strip" @changed="onStageChanged" />
              </div>
              <div class="drag-hint">⠿ 拖入编辑器</div>
              <div class="thumb-overlay role-overlay">
                <button class="role-btn" title="拖动场景卡" @mousedown.stop><el-icon><Grid /></el-icon></button>
                <button class="role-btn" title="编辑阶段" @click.stop="openAssetStagePanel(a)"><el-icon><EditPen /></el-icon></button>
                <button class="role-btn" title="预览场景图" @click.stop="previewRoleImg = a.thumb || ''"><el-icon><ZoomIn /></el-icon></button>
              </div>
            </div>
            <button class="add-line" @click="openCreateAsset('scene')">＋ 新建场景</button>
          </template>
          <template v-if="assetTab === '' || assetTab === '道具'">
            <div v-for="a in p.assets.props" :key="a.id" class="asset-card" :class="{ expanded: expandedAssets[a.id] }" draggable="true" :title="'拖拽到右侧编辑器可插入 @[' + a.name + '] 引用'" @dragstart="onAssetDragStart($event, assetRefObj(a, 'prop'))">
              <div class="char-row">
                <img class="char-thumb" :src="a.thumb || PLACEHOLDER" />
                <div class="char-info">
                  <div class="char-title">{{ a.name }} <span class="type-badge prop">道具</span></div>
                  <div class="stage-toggle" @click.stop="toggleAssetExpand(a)">
                    <span class="chev" :class="{ open: expandedAssets[a.id] }">▸</span>
                    {{ (a.stages?.length || 0) }}个阶段
                  </div>
                </div>
              </div>
              <div v-if="expandedAssets[a.id]" class="stage-strip-host">
                <AssetStageManager :asset-id="a.id" :initial-stages="a.stages" type="prop" mode="strip" @changed="onStageChanged" />
              </div>
              <div class="drag-hint">⠿ 拖入编辑器</div>
              <div class="thumb-overlay role-overlay">
                <button class="role-btn" title="拖动道具卡" @mousedown.stop><el-icon><Grid /></el-icon></button>
                <button class="role-btn" title="编辑阶段" @click.stop="openAssetStagePanel(a)"><el-icon><EditPen /></el-icon></button>
                <button class="role-btn" title="预览道具图" @click.stop="previewRoleImg = a.thumb || ''"><el-icon><ZoomIn /></el-icon></button>
              </div>
            </div>
            <button class="add-line" @click="openCreateAsset('prop')">＋ 新建道具</button>
          </template>
        </div>
        <div class="desc-modal-editor">
          <AssetRichInput v-model="descModalText" :assets="richInputAssets" :min-rows="14" :auto-grow="false" placeholder="输入画面描述，从左侧拖入资产自动插入 @[资产名] 引用标记" @drop-asset="onModalAssetDrop" @change="(text) => { descModalText = text }" />
          <div class="desc-modal-hint">提示：从左侧资产库拖拽资产到本框，可快速插入引用。按 Shift+Enter 换行。</div>
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="closeDescModal">取消</el-button>
        <el-button size="small" type="primary" @click="saveDescModal">确定</el-button>
      </template>
    </el-dialog>

    <!-- 角色图预览遮罩 -->
    <div class="modal-mask" v-if="previewRoleImg" @click="previewRoleImg = ''"><div class="modal"><img :src="previewRoleImg" @click.stop /></div></div>

    <!-- 小云雀式角色编辑弹窗 -->
    <RoleEditorModal v-model="editorVisible" :role="editingRole" :project-id="route.params.id" @saved="onRoleSaved" @stagesChanged="onStageChanged" @preview="previewRoleImg = $event" />

    <!-- 场景/道具阶段编辑弹窗（角色走 RoleEditorModal） -->
    <el-dialog v-model="assetStagePanel.open" title="编辑阶段 / 变体" width="820px" top="8vh" append-to-body destroy-on-close>
      <AssetStageManager
        v-if="assetStagePanel.asset"
        :asset-id="assetStagePanel.asset.id"
        :type="assetStagePanel.asset.type"
        mode="panel"
        @changed="onStageChanged"
      />
    </el-dialog>
</template>

<style scoped>
.frag { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.frag-top { flex: 0 0 auto; display: flex; align-items: center; gap: 14px; padding: 6px 18px; background: var(--bg-2); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.ftitle { display: flex; flex-direction: column; }
.ti { background: transparent; border: 0; border-bottom: 1px solid transparent; color: var(--txt); font-size: 16px; font-weight: 800; outline: none; width: 200px; }
.ti:focus { border-bottom-color: var(--pur); }
.tsub { font-size: 11px; color: var(--sub); }
.fparams { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-left: auto; }
.fgrp { display: flex; align-items: center; gap: 6px; }
.fgrp label { font-size: 12px; color: var(--sub); }
.credits-tag { font-size: 12px; font-weight: 700; color: var(--pur); background: rgba(138,92,255,.15); border: 1px solid rgba(138,92,255,.4); border-radius: 10px; padding: 5px 12px; }

/* 套餐选择器：降低决策成本，把速度/时长/模型/价格/画质策略打包展示 */
.preset-grp { align-items: center; }
.preset-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
.preset-info-icon { font-size: 14px; color: var(--sub); cursor: pointer; margin-left: 2px; user-select: none; }
.preset-info-icon:hover { color: var(--pur); }
.preset-option { padding: 6px 0; }
.preset-option-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.preset-option-title { font-weight: 700; color: var(--txt); }
.preset-tags { display: flex; gap: 4px; margin-left: auto; }
.preset-option-meta { font-size: 12px; color: var(--sub); margin-top: 4px; }
.preset-detail { font-size: 13px; color: var(--txt); }
.preset-detail-title { font-size: 15px; font-weight: 800; margin-bottom: 6px; }
.preset-detail-slogan { color: var(--sub); margin-bottom: 10px; }
.preset-detail-line { margin-bottom: 6px; }
.preset-detail-line b { color: var(--sub); font-weight: 600; }
.preset-detail-desc { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); color: var(--sub); line-height: 1.5; }
:global(.preset-dropdown .el-select-dropdown__item) { height: auto; padding: 8px 16px; }

.hint-bar { flex: 0 0 auto; display: flex; align-items: center; gap: 18px; padding: 8px 18px; background: var(--panel); border-bottom: 1px solid var(--line); font-size: 13px; color: var(--sub); }
.hint-text { flex: 1; color: var(--pur); }
.hint-bar label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.hint-bar .seed { display: flex; align-items: center; gap: 6px; }

.frag-body { flex: 1; display: grid; grid-template-columns: 280px 1fr 360px; min-height: 0; overflow: hidden; }
.col { overflow-y: auto; padding: 16px; border-right: 1px solid var(--line); }
.col-left { background: var(--panel); }
.col-mid { background: var(--bg-2); }
.col-right { border-right: 0; }
.col h3 { font-size: 13px; color: var(--sub); text-transform: uppercase; letter-spacing: 1px; margin: 0 0 12px; display: flex; align-items: center; gap: 8px; }
.add-asset { margin-left: auto; cursor: pointer; color: var(--sub); font-size: 15px; width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center; border-radius: 6px; }
.add-asset:hover { color: var(--txt); background: var(--card-2); }

.char-card, .asset-card { position: relative; background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; margin-bottom: 6px; cursor: grab; transition: box-shadow .15s, border-color .15s, transform .05s; }
.char-card:active, .asset-card:active { cursor: grabbing; }
.char-card:hover, .asset-card:hover { border-color: var(--pur); box-shadow: 0 2px 8px rgba(0,0,0,.12); }
.char-row { display: flex; gap: 8px; align-items: flex-start; }
.char-thumb { width: 42px; height: 56px; object-fit: cover; border-radius: 7px; background: var(--bg-2); border: 1px solid var(--line); flex: 0 0 auto; }
.asset-card .char-thumb { width: 56px; height: 42px; }
.char-info { flex: 1; min-width: 0; }
.char-title { font-weight: 700; font-size: 13px; display: flex; justify-content: space-between; align-items: center; gap: 6px; }
.char-meta { font-size: 12px; color: var(--sub); margin-top: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.role-badge { font-size: 10px; padding: 1px 7px; border-radius: 8px; font-weight: 600; }
.role-badge.lead { background: rgba(255,77,141,.18); color: #ff9cc1; }
.role-badge.supporting { background: rgba(139,147,167,.18); color: #b3bacb; }
.type-badge { font-size: 10px; padding: 1px 7px; border-radius: 8px; font-weight: 600; }
.type-badge.scene { background: rgba(77,159,255,.18); color: #8fc5ff; }
.type-badge.prop { background: rgba(255,176,32,.18); color: #ffd47a; }
/* 资产卡可展开阶段区：点击展开内联阶段横条 */
.stage-toggle { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--sub); margin-top: 6px; cursor: pointer; user-select: none; transition: color .15s; }
.stage-toggle:hover { color: var(--pur); }
.stage-toggle .chev { display: inline-block; transition: transform .15s ease; font-size: 10px; }
.stage-toggle .chev.open { transform: rotate(90deg); }
.stage-strip-host { margin-top: 6px; }
/* 小云雀式角色卡片 hover 按钮：覆盖层默认透明且不拦截拖拽，hover 时显示圆角按钮 */
.thumb-overlay { position: absolute; inset: 0; display: flex; align-items: flex-start; justify-content: flex-end; gap: 6px; padding: 7px; opacity: 0; transition: .15s; z-index: 2; pointer-events: none; }
.char-card:hover .thumb-overlay { opacity: 1; }
.role-btn { width: 28px; height: 28px; border-radius: 8px; background: rgba(0,0,0,.55); color: #fff; border: 0; display: flex; align-items: center; justify-content: center; cursor: pointer; backdrop-filter: blur(4px); transition: .15s; pointer-events: auto; }
.role-btn:hover { background: rgba(138,92,255,.9); transform: scale(1.05); }
.add-line { width: 100%; margin-top: 8px; padding: 8px; border: 1px dashed var(--line); border-radius: 10px; background: transparent; color: var(--sub); cursor: pointer; font-size: 13px; font-weight: 600; transition: .15s; }
.add-line:hover { border-color: var(--pur); color: var(--txt); background: rgba(138,92,255,.06); }

/* 角色图预览遮罩 */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.72); display: flex; align-items: center; justify-content: center; z-index: 4000; cursor: zoom-out; }
.modal-mask .modal { max-width: 90vw; max-height: 90vh; }
.modal-mask .modal img { max-width: 90vw; max-height: 90vh; border-radius: 12px; box-shadow: 0 12px 40px rgba(0,0,0,.5); }

.editor { padding-bottom: 20px; }
.ed-head { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.ed-idx { font-size: 13px; font-weight: 800; color: var(--pur); white-space: nowrap; }
.ed-title { background: transparent; border: 0; border-bottom: 1px solid var(--line); color: var(--txt); font-size: 18px; font-weight: 800; outline: none; flex: 1; }
.ed-title:focus { border-bottom-color: var(--pur); }
.ed-meta { font-size: 12px; color: var(--sub); white-space: nowrap; }

.field-row { display: flex; gap: 10px; margin-bottom: 14px; align-items: flex-start; }
.flabel { font-size: 13px; color: var(--sub); flex: 0 0 56px; padding-top: 4px; }
.caps { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.lead { font-size: 12px; color: var(--sub); }
.call-asset { background: var(--card); border: 1px solid var(--line); color: var(--sub); border-radius: 10px; padding: 5px 12px; cursor: pointer; font-size: 12px; font-weight: 600; transition: .15s; }
.call-asset:hover { color: var(--txt); border-color: var(--pur); background: var(--card-2); }

.shot-block { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px; margin-bottom: 10px; }
.shot-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.idx { display: inline-flex; width: 24px; height: 24px; align-items: center; justify-content: center; background: var(--pink); color: #fff; border-radius: 6px; font-weight: 700; font-size: 12px; }
.shot-cap { flex: 1; background: transparent; border: 0; border-bottom: 1px solid var(--line); color: var(--txt); font-size: 14px; font-weight: 700; outline: none; }
.shot-cap:focus { border-bottom-color: var(--pur); }
.shot-ops { display: flex; align-items: center; gap: 2px; margin-left: auto; }
.shot-ops .el-button { padding: 2px 6px; font-size: 13px; }
.shot-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 8px; }
.sg { display: flex; flex-direction: column; gap: 4px; }
.sg label { font-size: 11px; color: var(--sub); }
.sg.full { margin-bottom: 8px; grid-column: 1 / -1; }
.shot-refs { font-size: 12px; color: var(--sub); display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }
.refs-label { color: var(--sub); }
.ref-add { cursor: pointer; color: var(--accent, #8a5cff); border: 1px dashed var(--line); border-radius: 6px; padding: 1px 7px; font-size: 11px; }
.ref-add:hover { background: var(--card-2); }
.ref-count { margin-left: auto; color: var(--sub); }
.form-row { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.form-row > label { width: 56px; flex: none; color: var(--sub); font-size: 13px; }
.small { font-size: 12px; }

/* 场景设定：保留业务字段，但弱化为紧凑单行 */
.seg-scene-setting { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; padding: 8px 10px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }
.seg-scene-label { font-size: 12px; color: var(--sub); flex: 0 0 auto; }
.seg-scene-setting .el-input { flex: 1; }

/* 自动保存状态指示 */
.save-state { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--sub); margin-right: auto; }
.save-state .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--sub); opacity: .5; }
.save-state .dot.ok { background: var(--green); opacity: 1; }
.save-state .dot.bad { background: #ff7a7a; opacity: 1; }
.save-state.saved { color: var(--green); }
.save-state.error { color: #ff7a7a; }
.save-state.saving .dot, .save-state.pending .dot { background: var(--pur); opacity: 1; animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* 分镜核心文案并排，减少纵向占用 */
.shot-voice-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 8px; }

/* 高级参数折叠条 */
.shot-adv-toggle { display: flex; align-items: center; gap: 10px; margin-top: 8px; padding: 6px 10px; border-radius: 8px; background: var(--bg-2); border: 1px dashed var(--line); font-size: 12px; color: var(--sub); cursor: pointer; transition: .15s; }
.shot-adv-toggle:hover { border-color: var(--pur); color: var(--txt); background: var(--panel); }
.shot-adv-summary { flex: 1; font-size: 11px; color: var(--sub); opacity: .85; text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.shot-adv-chev { font-size: 10px; }
.shot-advanced { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--line); }

.preview-box { background: var(--bg); border-radius: 12px; overflow: hidden; aspect-ratio: 16/9; display: flex; align-items: center; justify-content: center; }
.preview-box .pv { width: 100%; height: 100%; }
.preview-box .empty { color: var(--sub); font-size: 13px; text-align: center; padding: 20px; line-height: 1.8; }
.vstatus { font-size: 11px; padding: 2px 9px; border-radius: 8px; font-weight: 700; }
.vstatus.ok { background: rgba(138,92,255,.18); color: #b79bff; }
.vstatus.rendering { background: rgba(255,176,32,.18); color: var(--warn); }
.vstatus.failed { background: rgba(255,92,92,.18); color: #ff7a7a; }
.vstatus.pending { background: rgba(139,147,167,.18); color: var(--sub); }
.mt { margin-top: 12px; }
.log { margin: 0; max-height: 160px; overflow: auto; white-space: pre-wrap; word-break: break-all; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; line-height: 1.6; color: var(--txt); }

.frag-nav { flex: 0 0 auto; border-top: 1px solid var(--line); background: var(--panel); padding: 8px 14px; display: flex; align-items: center; gap: 12px; }
.nav-stats { display: flex; gap: 10px; font-size: 11px; color: var(--sub); flex: 0 0 auto; }
.nav-thumbs { display: flex; gap: 8px; overflow-x: auto; flex: 1; }
.nav-th { flex: 0 0 auto; width: 72px; cursor: pointer; border: 2px solid transparent; border-radius: 8px; padding: 3px; }
.nav-th.on { border-color: var(--pur); }
.nth-ph { position: relative; height: 36px; border-radius: 6px; background: linear-gradient(135deg, var(--card-2), var(--card)); display: flex; align-items: center; justify-content: center; color: var(--sub); font-size: 12px; font-weight: 700; }
.nth-badge { position: absolute; right: 3px; bottom: 3px; width: 14px; height: 14px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 9px; font-weight: 700; }
.nth-badge.ok { background: var(--green); color: #06210f; }
.nth-badge.pending { background: var(--card-2); color: var(--sub); }
.nth-name { font-size: 10px; color: var(--sub); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nth-dur { font-size: 9px; color: var(--sub); }
.nav-th.add .nth-ph { border: 1px dashed var(--line); background: transparent; color: var(--sub); font-size: 16px; }

/* 片段顶部导航：当前片段 + 左右切换 + 下拉跳转 + 操作 */
.segment-header { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; background: var(--card); border-bottom: 1px solid var(--line); gap: 12px; }
.seg-nav-arrows { display: flex; align-items: center; gap: 6px; }
.seg-current { display: inline-flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 700; color: var(--txt); cursor: pointer; padding: 5px 12px; border-radius: 8px; background: var(--bg-2); border: 1px solid var(--line); transition: .15s; }
.seg-current:hover { border-color: var(--pur); background: var(--card-2); }
.seg-actions { display: flex; align-items: center; gap: 8px; }
/* 底栏缩略图删除按钮（hover 显现，避免误删） */
.nth-del { position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 700; color: #fff; background: rgba(0,0,0,.45); cursor: pointer; opacity: 0; transition: .15s; }
.nth-ph:hover .nth-del { opacity: 1; }
.nth-del:hover { background: rgba(255,90,90,.9); }

/* 道具胶囊按知识库配黄（角色紫 / 场景蓝 / 道具黄） */
.frag .asset-tag.prop { background: rgba(254,243,199,.15); color: #f5d97a; border-color: rgba(254,243,199,.4); }

/* 资产卡片可拖拽 + 拖入提示 */
.drag-hint { font-size: 10px; color: var(--sub); margin-top: 4px; opacity: .7; }
/* 无阶段角色：灰显禁用态，提示先添加阶段 */
.char-card { cursor: default; }
.char-card.disabled { opacity: .55; filter: grayscale(.3); }
.char-card.disabled .char-thumb { opacity: .6; }
/* 可放置的文本域：默认虚线描边提示，拖拽悬停高亮 */
.drop-field { border: 1px dashed transparent; border-radius: 10px; padding: 6px; margin: 0 -6px 8px; transition: box-shadow .15s, border-color .15s, background .15s; }
.drop-field .drag-tip { font-size: 10px; color: var(--sub); font-weight: 400; margin-left: 6px; opacity: .7; }
.drop-field.drop-active { border-color: var(--pur); box-shadow: 0 0 0 2px rgba(138,92,255,.35) inset; background: rgba(138,92,255,.06); }

/* 业务规则校验条（BR-003 / BR-009） */
.br-bar { font-size: 12px; font-weight: 600; border-radius: 10px; padding: 7px 12px; margin-bottom: 12px; }
.br-bar.ok { color: #6fe0a8; background: rgba(111,224,168,.12); border: 1px solid rgba(111,224,168,.35); }
.br-bar.bad { color: #ff7a7a; background: rgba(255,122,122,.14); border: 1px solid rgba(255,122,122,.4); }

/* 引用资产弹窗：大图卡片网格 + 点击预览 */
.ref-picker-dialog .el-dialog__body { padding-top: 10px; }
.ref-picker-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 14px; max-height: 56vh; overflow-y: auto; padding: 4px; }
.ref-picker-card { position: relative; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 10px; cursor: pointer; transition: border-color .15s, box-shadow .15s, transform .05s; display: flex; flex-direction: column; gap: 8px; }
.ref-picker-card:hover { border-color: var(--pur); box-shadow: 0 2px 8px rgba(0,0,0,.12); }
.ref-picker-card.selected { border-color: var(--pur); box-shadow: 0 0 0 2px rgba(138,92,255,.45) inset; background: rgba(138,92,255,.08); }
.ref-picker-thumb { width: 100%; aspect-ratio: 1; border-radius: 8px; overflow: hidden; background: var(--card-2); display: flex; align-items: center; justify-content: center; color: var(--sub); font-size: 24px; }
.ref-picker-thumb .el-image { width: 100%; height: 100%; }
.ref-picker-no-thumb { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; background: rgba(64,158,255,.12); color: #409eff; font-weight: 700; border-radius: 8px; }
.ref-picker-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.ref-picker-name { font-size: 12px; font-weight: 600; color: var(--txt); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ref-picker-type { font-size: 10px; color: var(--sub); }
.ref-picker-check { position: absolute; top: 8px; right: 8px; }
.ref-picker-check .el-checkbox { height: auto; }

/* 画面描述：固定高度 + 展开编辑按钮 */
.description-field label { display: flex; align-items: center; gap: 8px; }
.description-field .desc-expand { margin-left: auto; font-size: 11px; color: var(--pur); cursor: pointer; padding: 2px 8px; border-radius: 6px; transition: .15s; }
.description-field .desc-expand:hover { background: rgba(138,92,255,.15); }
.description-field .asset-rich-input.is-fixed { min-height: 84px !important; height: 84px !important; }

/* 画面描述大弹窗：左侧资产库 + 右侧编辑器 */
.desc-modal .el-dialog__body { padding: 0; }
.desc-modal-body { display: grid; grid-template-columns: 280px 1fr; height: 70vh; min-height: 480px; }
.desc-modal-assets { background: var(--panel); border-right: 1px solid var(--line); padding: 16px; overflow-y: auto; }
.desc-modal-assets h3 { font-size: 13px; color: var(--sub); text-transform: uppercase; letter-spacing: 1px; margin: 0 0 12px; display: flex; align-items: center; gap: 8px; }
.desc-modal-assets .asset-tabs { display: flex; gap: 6px; margin-bottom: 12px; }
.desc-modal-assets .asset-tabs .t { flex: 1; text-align: center; font-size: 12px; padding: 6px; border-radius: 8px; background: var(--card); border: 1px solid var(--line); color: var(--sub); cursor: pointer; }
.desc-modal-assets .asset-tabs .t.on { background: rgba(138,92,255,.18); border-color: var(--pur); color: var(--txt); }
.desc-modal-editor { padding: 20px; display: flex; flex-direction: column; gap: 10px; overflow: hidden; }
.desc-modal-editor .asset-rich-input { flex: 1; min-height: 0; overflow-y: auto; }
.desc-modal-hint { font-size: 11px; color: var(--sub); }
</style>
