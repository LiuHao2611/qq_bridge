<script setup>
// 回忆录导演创意指导板（AGENT_SPEC_15）
// 4-zone：topbar / chapter_switcher / main_3col / bottom_action_bar
// 数据来源：useMemoir（真实后端 /api/v1/memoirs...）。不硬编码任何实例数据。
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMemoir } from '../composables/useMemoir'
import { useHotkeys } from '../composables/useHotkeys'
import { toast } from '../store'
import {
  OPTION_STORYBOARD_COUNTS, OPTION_MEMOIR_RATIOS, OPTION_MEMOIR_RESOLUTIONS,
  SHOT_TYPE_OPTIONS, CAMERA_MOVEMENT_OPTIONS, CAMERA_PATH_OPTIONS, NARRATION_VOICE_OPTIONS,
} from '../mock'
import {
  listAnchors, createAnchor, patchAnchor, deleteAnchor,
  reorderChapters, createNarration, listNarrations, patchChapter,
} from '../api'

const route = useRoute()
const router = useRouter()
const {
  memoir, chapters, activeChapterId, activeChapter, chapterCount, activeIndex, target,
  totalDuration, subtitle, loading, busy, taskStatus, preCheckResult, pointsBalance,
  load, selectChapter, updateMemoir, addTimeline, removeTimeline, uploadPhoto, addChapter,
  plan, storyboard, video, saveChapterShots, exportPkg,
} = useMemoir(route.params.id)
onMounted(load)

// 分镜景别 / 运镜（统一复用 KB 规范枚举，与片段编辑页一致）
const SHOT_TYPES = SHOT_TYPE_OPTIONS
const CAMERA_MOVES = CAMERA_MOVEMENT_OPTIONS
const shotTypeLabel = (v) => SHOT_TYPES.find((o) => o.value === v)?.label || v
const camMoveLabel = (v) => CAMERA_MOVES.find((o) => o.value === v)?.label || v

const photoInput = ref(null)
const newTimelineAge = ref('')
const newTimelineDesc = ref('')
const newChapterTitle = ref('')
const editingShots = ref(false)

const personPerspective = computed({
  get: () => memoir.value?.person_perspective || '第一人称',
  set: (v) => updateMemoir({ person_perspective: v }),
})
const storyboardCount = computed({
  get: () => Number(memoir.value?.storyboard_count) || 4,
  set: (v) => {
    updateMemoir({ storyboard_count: v })
    if (chapterCount.value > 0) toast('已生成策划后修改故事板数量，需要重新生成故事策划（BR-019）')
  },
})
const targetRatio = computed({
  get: () => memoir.value?.target_ratio || '16:9',
  set: (v) => { updateMemoir({ target_ratio: v }); ifHasGenerated('比例变化会使视频和画面失效（BR-020）') },
})
const targetResolution = computed({
  get: () => memoir.value?.target_resolution || '720p',
  set: (v) => { updateMemoir({ target_resolution: v }); ifHasGenerated('分辨率变化会使视频和画面失效（BR-020）') },
})

function ifHasGenerated(msg) {
  const gen = chapters.value.some((c) => c.storyboard_image_url || c.video_url)
  if (gen) toast('⚠ ' + msg)
}

const boardMeta = computed(() => {
  const ch = activeChapter.value
  if (!ch) return ''
  return `第${ch.chapter_index ?? (activeIndex.value + 1)}章: ${ch.title || '未命名'} | 时间: ${ch.era || '—'} | 格式: ${memoir.value?.target_ratio} | 预计时长: ${ch.duration || 15}秒`
})

const currentShots = computed(() => activeChapter.value?.shot_data?.shots || [])
const shotSum = computed(() => currentShots.value.reduce((a, s) => a + (Number(s.duration) || 0), 0))
const shotMismatch = computed(() => shotSum.value !== (Number(activeChapter.value?.duration) || 15))

// 预检（BR-021）
const preChecks = computed(() => {
  const p = preCheckResult.value
  if (!p) return []
  return [
    { label: '人物照片', ok: !!p.photos, expect: p.photos ? '✓ 已上传' : '✗ 至少 1 张' },
    { label: '故事策划', ok: !!p.planning, expect: `${p.chapter_count || 0} 章 / ${p.target || 0} 镜头` },
    { label: '导演指导板', ok: !!p.storyboard, expect: p.storyboard ? '✓ 已生成' : '✗ 未生成' },
    { label: '视频片段', ok: !!p.video, expect: p.video ? '✓ 已生成' : '✗ 未生成' },
  ]
})
const allPreCheck = computed(() => preChecks.value.length === 4 && preChecks.value.every((c) => c.ok))

// 色彩参考
const palette = computed(() => activeChapter.value?.shot_data?.color_palette || [])
// 图片信息（meta）
const imageInfo = computed(() => {
  const ch = activeChapter.value
  const m = ch?.shot_data?.meta || {}
  return {
    title: m.title || ch?.title || '—',
    characters: m.characters || '',
    location: m.location || '',
    era: m.era || ch?.era || '—',
    format: m.format || (memoir.value?.target_ratio || '16:9'),
    duration: m.duration || ch?.duration || 15,
    shot_count: m.shot_count || currentShots.value.length,
    page: `${activeIndex.value + 1} / ${chapterCount.value}`,
  }
})

// ---- 交互 ----
const onUploadClick = () => photoInput.value?.click()
const onPhotoSelected = (e) => {
  const f = e.target.files?.[0]
  if (f) uploadPhoto(f)
  e.target.value = ''
}
const onAddTimeline = () => {
  const age = parseInt(newTimelineAge.value, 10)
  if (isNaN(age) || age < 0) { toast('年龄必须为 >=0 的整数'); return }
  if (!newTimelineDesc.value.trim()) { toast('请填写事件描述'); return }
  addTimeline(age, newTimelineDesc.value.trim())
  newTimelineAge.value = ''; newTimelineDesc.value = ''
}
const onAddChapter = () => {
  const t = newChapterTitle.value.trim() || undefined
  addChapter(t)
  newChapterTitle.value = ''
}
const onFullscreen = () => toast('全屏预览（演示）')
const onDownload = () => toast('下载指导板（演示）')

const onStoryboard = () => storyboard()
const onVideo = () => { if (!canGenerate.value && taskStatus.value !== 'running') { toast('生成前检查未全部通过（BR-021 / MEM-05）'); return } video() }

const startEditShots = () => { editingShots.value = true }
const saveShots = async () => {
  const ok = await saveChapterShots(currentShots.value.map((s) => ({ ...s })))
  if (ok) editingShots.value = false
}
const autoDistributeShots = () => {
  const ch = activeChapter.value
  const dur = Number(ch?.duration) || 15
  const arr = currentShots.value
  if (!arr.length) return
  const base = Math.floor(dur / arr.length)
  let rem = dur - base * arr.length
  arr.forEach((s, i) => { s.duration = base + (i < rem ? 1 : 0) })
  toast('已按章节时长自动均分')
}

useHotkeys({ save: () => { if (editingShots.value) saveShots() }, undo: () => toast('撤销（演示）'), fullscreen: onFullscreen })

// ---- KB memoir 6-tab workflow（资料/策划/指导板/审片/视频/合成） ----
const activeTab = ref('board')  // 默认展示导演创意指导板
// G. 声音与旁白 波形图（演示）
const waveformBars = computed(() => {
  const arr = []
  for (let i = 0; i < 48; i++) {
    arr.push(Math.min(92, 22 + Math.abs(Math.sin(i * 0.5) * 42) + ((i * 7) % 13)))
  }
  return arr
})
// 叙事策划（memoir.story 子键）
const storySetting = computed({ get: () => memoir.value?.story?.setting || '', set: (v) => updateStory({ setting: v }) })
const storyVoice = computed({ get: () => memoir.value?.story?.voice_narration || '', set: (v) => updateStory({ voice_narration: v }) })
const storyNotes = computed({ get: () => memoir.value?.story?.notes || '', set: (v) => updateStory({ notes: v }) })
const updateStory = (fields) => {
  if (!memoir.value) return
  const story = { ...(memoir.value.story || {}), ...fields }
  updateMemoir({ story })
}
const reviewStatusText = (c) => {
  if (c.video_url) return '视频已完成'
  if (c.storyboard_image_url) return '指导板已完成'
  if (c.status === 'failed') return '失败'
  return '待生成'
}
const reviewStatusCls = (c) => {
  if (c.video_url) return 'ok'
  if (c.storyboard_image_url) return 'partial'
  if (c.status === 'failed') return 'fail'
  return 'pending'
}
const submitReview = () => {
  if (!allPreCheck.value) { toast('请先通过全部生成前检查（BR-021）'); return }
  toast('已提交审片（演示）')
}

// ============================================================
// §09 G3 补全：MEM-03 / 04 / 05 / 06 / 07 / 08 / 09 / 13 / 15
// ============================================================
function safeJson(s, d) { try { return JSON.parse(s) } catch { return d } }
function safeLen(v) { return Array.isArray(v) ? v.length : (v ? String(v).length : 0) }

// ---- MEM-05 生成前检查清单（6 项纯函数，不调后端） ----
const preGenChecks = computed(() => {
  const m = memoir.value
  const chs = chapters.value
  const board = activeChapter.value
  return [
    { itemId: 'anchors_locked', status: m?.character_anchors_locked ? 'pass' : 'fail', message: m?.character_anchors_locked ? '人物锚点已锁定' : '请先锁定人物锚点（MEM-04）' },
    { itemId: 'timeline_enough', status: (m?.timeline_events?.length ?? 0) >= 3 ? 'pass' : 'fail', message: `时间轴需 ≥3 个节点（当前 ${m?.timeline_events?.length ?? 0}）` },
    { itemId: 'photos_present', status: (m?.reference_photos?.length ?? 0) >= 1 ? 'pass' : 'fail', message: (m?.reference_photos?.length ?? 0) >= 1 ? '已上传人物参考照' : '至少上传 1 张人物参考照（BR-015）' },
    { itemId: 'chapters_complete', status: chs.length >= 1 && chs.every((c) => safeLen(c?.summary) >= 30) ? 'pass' : 'fail', message: chs.length >= 1 && chs.every((c) => safeLen(c?.summary) >= 30) ? '章节策划齐全' : '每章 summary 需 ≥30 字' },
    { itemId: 'board_ready', status: !!board && safeLen(board?.shot_data?.shots) >= 3 ? 'pass' : 'fail', message: !!board && safeLen(board?.shot_data?.shots) >= 3 ? '指导板镜头表就绪' : '指导板需 ≥3 个镜头（MEM-10）' },
    { itemId: 'output_valid', status: (m?.target_resolution === '720p' || m?.target_resolution === '1080p') ? 'pass' : 'warn', message: '成片规格 ' + (m?.target_resolution || '720p') + '（建议 720p/1080p）' },
  ]
})
const allPreGenOk = computed(() => preGenChecks.value.every((c) => c.status === 'pass'))
const checkLabel = (id) => ({
  anchors_locked: '人物锚点锁定', timeline_enough: '时间轴 ≥3 节点', photos_present: '参考照 ≥1 张',
  chapters_complete: '章节策划齐全', board_ready: '指导板镜头 ≥3', output_valid: '输出参数合规',
}[id] || id)
// 生成按钮同时受 BR-021 与 MEM-05 约束
const canGenerate = computed(() => allPreCheck.value && allPreGenOk.value)

// ---- MEM-03 事实锚点（用户对 AI 的硬约束） ----
const anchors = ref([])
const newAnchorType = ref('event')
const newAnchorContent = ref('')
const newAnchorLocked = ref(true)
const loadAnchors = async () => {
  if (!memoir.value) return
  try { anchors.value = await listAnchors(memoir.value.memoir_id) } catch (e) { anchors.value = [] }
}
const addAnchor = async () => {
  if (!memoir.value) return
  const content = newAnchorContent.value.trim()
  if (content.length < 2) { toast('事实锚点内容至少 2 字'); return }
  try {
    const a = await createAnchor(memoir.value.memoir_id, { anchor_type: newAnchorType.value, content, is_locked: newAnchorLocked.value })
    anchors.value.push(a); newAnchorContent.value = ''; toast('已添加事实锚点')
  } catch (e) { toast('添加失败: ' + (e.message || e)) }
}
const toggleAnchorLock = async (a) => {
  try { const u = await patchAnchor(memoir.value.memoir_id, a.id, { is_locked: !a.is_locked }); Object.assign(a, u) } catch (e) { toast('更新失败: ' + (e.message || e)) }
}
const removeAnchor = async (a) => {
  try { await deleteAnchor(memoir.value.memoir_id, a.id); anchors.value = anchors.value.filter((x) => x.id !== a.id); toast('已删除') } catch (e) { toast('删除失败: ' + (e.message || e)) }
}

// ---- MEM-04 人物锚点（6 维 + 锁定） ----
const characterAnchors = computed({
  get: () => {
    const a = memoir.value?.character_anchors
    const obj = (typeof a === 'string' ? safeJson(a, {}) : (a || {}))
    return {
      age_at_event: obj.age_at_event || '',
      appearance_keywords: obj.appearance_keywords || [],
      voice_keywords: obj.voice_keywords || [],
      personality_keywords: obj.personality_keywords || [],
      core_relationship: obj.core_relationship || '',
      signature_phrase: obj.signature_phrase || '',
    }
  },
  set: (v) => updateMemoir({ character_anchors: v }),
})
const caAppearance = ref('')
const caVoice = ref('')
const caPersonality = ref('')
const saveCharacterAnchors = () => {
  const cur = characterAnchors.value
  const next = {
    ...cur,
    appearance_keywords: caAppearance.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    voice_keywords: caVoice.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    personality_keywords: caPersonality.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
  }
  characterAnchors.value = next
  caAppearance.value = ''; caVoice.value = ''; caPersonality.value = ''
  toast('人物锚点已保存')
}
const lockCharacterAnchors = () => {
  const a = characterAnchors.value
  if (a.appearance_keywords.length < 3) { toast('外观关键词至少 3 项（MEM-04）'); return }
  if (a.personality_keywords.length < 2) { toast('性格关键词至少 2 项（MEM-04）'); return }
  if ((a.core_relationship || '').length < 5) { toast('核心关系描述至少 5 字（MEM-04）'); return }
  updateMemoir({ character_anchors_locked: true }); toast('人物锚点已锁定')
}
const unlockCharacterAnchors = () => { updateMemoir({ character_anchors_locked: false }); toast('已解锁人物锚点') }

// ---- MEM-06 摄影机路径（每镜，label 工具） ----
const cameraPathLabel = (v) => CAMERA_PATH_OPTIONS.find((o) => o.value === v)?.label || v || 'static'

// ---- MEM-07 灯光方案 / MEM-08 情绪色卡（按章节持久化） ----
const patchActiveChapter = async (fields) => {
  if (!memoir.value || !activeChapter.value) return
  try { await patchChapter(memoir.value.memoir_id, activeChapter.value.chapter_id, fields); await reloadChapter(activeChapter.value.chapter_id); toast('已保存') } catch (e) { toast('保存失败: ' + (e.message || e)) }
}
const lightingScheme = computed({
  get: () => {
    const raw = activeChapter.value?.lighting_scheme
    const obj = (typeof raw === 'string' ? safeJson(raw, null) : raw) || {}
    return {
      key_light: obj.key_light || 'soft',
      fill_ratio: obj.fill_ratio ?? 0.5,
      back_light: obj.back_light || false,
      color_temp: obj.color_temp ?? 5600,
      description: obj.description || '',
    }
  },
  set: (v) => patchActiveChapter({ lighting_scheme: v }),
})
const emotionPalette = computed({
  get: () => {
    const raw = activeChapter.value?.emotion_palette
    return (typeof raw === 'string' ? safeJson(raw, []) : (raw || []))
  },
  set: (v) => patchActiveChapter({ emotion_palette: v }),
})
const newSwatchName = ref('')
const newSwatchHex = ref('#88aaff')
const addSwatch = () => {
  if (!/^#[0-9A-Fa-f]{6}$/.test(newSwatchHex.value)) { toast('颜色格式需为 #RRGGBB'); return }
  const arr = [...emotionPalette.value]
  if (arr.length >= 6) { toast('情绪色卡最多 6 个'); return }
  arr.push({ name: newSwatchName.value.trim() || '色卡', hex: newSwatchHex.value })
  emotionPalette.value = arr; newSwatchName.value = ''; toast('已添加色卡')
}
const removeSwatch = (i) => { const arr = [...emotionPalette.value]; arr.splice(i, 1); emotionPalette.value = arr }

// ---- MEM-09 声音旁白 TTS ----
const narrations = ref([])
const narrationText = ref('')
const narrationVoice = ref('zh-CN-XiaoxiaoNeural')
const loadNarrations = async () => {
  if (!memoir.value || !activeChapter.value) return
  try { narrations.value = await listNarrations(memoir.value.memoir_id, activeChapter.value.chapter_id) } catch (e) { narrations.value = [] }
}
const genNarration = async () => {
  if (!memoir.value || !activeChapter.value) return
  const text = narrationText.value.trim()
  if (text.length < 2) { toast('旁白文本至少 2 字'); return }
  busy.value = true
  try {
    const n = await createNarration(memoir.value.memoir_id, activeChapter.value.chapter_id, { text, voice_id: narrationVoice.value })
    narrations.value.push(n); narrationText.value = ''; toast(n.local_fallback ? '本地降级：仅保存文本（TTS 不可用）' : '旁白已生成')
  } catch (e) { toast('生成失败: ' + (e.message || e)) }
  busy.value = false
}

// ---- MEM-13 章节拖拽重排（原生 HTML5 DnD，等价 @dnd-kit 排序，事务原子） ----
const dragIndex = ref(-1)
const onChapterDragStart = (i) => { dragIndex.value = i }
const onChapterDragOver = (i) => { /* 允许放置 */ }
const onChapterDrop = async (i) => {
  if (dragIndex.value < 0 || dragIndex.value === i) { dragIndex.value = -1; return }
  const arr = [...chapters.value]
  const [moved] = arr.splice(dragIndex.value, 1)
  arr.splice(i, 0, moved)
  chapters.value = arr
  dragIndex.value = -1
  if (!memoir.value) return
  try {
    const ids = arr.map((c) => c.chapter_id)
    const updated = await reorderChapters(memoir.value.memoir_id, ids)
    if (Array.isArray(updated) && updated.length) chapters.value = updated
    toast('章节顺序已保存')
  } catch (e) { toast('重排失败: ' + (e.message || e)); await reloadChapter(activeChapterId.value) }
}

// ---- MEM-15 四模型独立配置（本地等价替代） ----
const MODEL_OPTIONS = {
  planning_model: ['本地 Ollama · qwen3.5:9b', '本地 Ollama · qwen2.5:7b'],
  board_image_model: ['秋叶 ComfyUI · SD1.5', '秋叶 ComfyUI · SDXL'],
  video_model: ['本地 FFmpeg 合成', 'Seedance 2.0 Mini（云端等价）'],
  audio_model: ['本地 TTS · edge-tts', 'ElevenLabs（云端等价）'],
}
const modelConfig = computed({
  get: () => {
    const raw = memoir.value?.model_config
    const obj = (typeof raw === 'string' ? safeJson(raw, {}) : (raw || {}))
    return {
      planning_model: obj.planning_model || MODEL_OPTIONS.planning_model[0],
      board_image_model: obj.board_image_model || MODEL_OPTIONS.board_image_model[0],
      video_model: obj.video_model || MODEL_OPTIONS.video_model[0],
      audio_model: obj.audio_model || MODEL_OPTIONS.audio_model[0],
    }
  },
  set: (v) => updateMemoir({ model_config: v }),
})
const saveModelConfig = () => { modelConfig.value = { ...modelConfig.value }; toast('模型配置已保存') }

// 回忆录加载 / 章节切换时同步加载事实锚点与旁白
watch(() => memoir.value?.memoir_id, (id) => { if (id) loadAnchors() })
watch(activeChapterId, () => { loadNarrations() })

// 更新 onVideo 生成按钮约束（BR-021 + MEM-05）
</script>

<template>
  <div v-if="memoir && !loading" class="memoir">
    <!-- ① 顶栏 -->
    <div class="m-top">
      <button class="back" @click="router.push('/projects')">← 返回</button>
      <span class="m-tag">回忆录</span>
      <div class="mtitle">
        <input v-model="memoir.subject_name" class="mi" placeholder="主人公姓名/称谓" @blur="updateMemoir({ subject_name: memoir.subject_name })" />
        <span class="msub">{{ subtitle }}</span>
      </div>
      <div class="m-right">
        <span class="credits">💰 积分余额 <b>{{ pointsBalance }}</b></span>
        <label class="person-switch">人称
          <el-select v-model="personPerspective" size="small" style="width:110px">
            <el-option label="第一人称" value="第一人称" />
            <el-option label="第三人称" value="第三人称" />
          </el-select>
        </label>
        <el-button size="small" type="primary" @click="exportPkg">导出交付包</el-button>
      </div>
    </div>

    <!-- ② 章节切换 -->
    <div class="m-chapters">
      <div v-for="(c, i) in chapters" :key="c.chapter_id" class="ch-tab"
           :class="{ on: c.chapter_id === activeChapterId, failed: c.status === 'failed', dragging: dragIndex === i }"
           draggable="true"
           @click="selectChapter(c.chapter_id)"
           @dragstart="onChapterDragStart(i)"
           @dragover.prevent="onChapterDragOver(i)"
           @drop.prevent="onChapterDrop(i)">
        <span class="ch-idx">{{ String(i + 1).padStart(2, '0') }}</span>
        <span class="ch-name">{{ c.title || ('第' + String(i + 1).padStart(2, '0') + '章') }}</span>
        <span v-if="c.status === 'video_ready' || c.video_url" class="ch-dot ok" />
        <span v-else-if="c.storyboard_image_url" class="ch-dot" />
        <span v-else-if="c.status === 'failed'" class="ch-dot fail" />
      </div>
      <button class="ch-add" @click="onAddChapter" :disabled="chapterCount >= target">＋ 新增章节</button>
      <input v-model="newChapterTitle" class="ch-input" placeholder="新章节标题（可选）" @keyup.enter="onAddChapter" />
    </div>

    <div class="m-body">
      <el-tabs v-model="activeTab" class="memoir-tabs">

        <!-- 资料 -->
        <el-tab-pane label="资料" name="material">
          <div class="tab-scroll">
            <h3>人物档案（资料）</h3>
            <div class="field"><label>姓名</label><el-input v-model="memoir.subject_name" size="small" @blur="updateMemoir({ subject_name: memoir.subject_name })" /></div>
            <div class="block">
              <div class="bl-title">人生自述 / 背景</div>
              <el-input v-model="memoir.background_text" type="textarea" :autosize="{minRows:4}" size="small"
                        placeholder="例如：六十岁以后，我开始记得从前…" @blur="updateMemoir({ background_text: memoir.background_text })" />
            </div>
            <div class="block">
              <div class="bl-title">时间轴关键事件（age ≥ 0）</div>
              <div v-for="(t, i) in (memoir.timeline_events || [])" :key="i" class="tl-row">
                <span class="tl-age">{{ t.age }}岁</span>
                <el-input v-model="t.description" size="small" @blur="updateMemoir({ timeline_events: memoir.timeline_events })" />
                <span class="x" @click="removeTimeline(i)">×</span>
              </div>
              <div class="tl-add">
                <el-input v-model="newTimelineAge" size="small" placeholder="年龄" style="width:64px" />
                <el-input v-model="newTimelineDesc" size="small" placeholder="事件描述…" @keyup.enter="onAddTimeline" />
                <el-button size="small" @click="onAddTimeline">＋</el-button>
              </div>
            </div>
            <div class="block">
              <div class="bl-title">人物参考照（BR-015：至少 1 张）</div>
              <div class="ref-grid">
                <div v-for="r in (memoir.reference_photos || [])" :key="r.photo_id" class="ref-ph">
                  <img v-if="r.url" :src="r.url" class="ref-img" />
                  <span class="ref-name">{{ r.name }}</span>
                </div>
                <div class="ref-ph add" @click="onUploadClick">＋ 上传</div>
              </div>
              <input ref="photoInput" type="file" accept="image/png,image/jpeg,image/webp" hidden @change="onPhotoSelected" />
            </div>

            <!-- §09 MEM-04 人物锚点（6 维 + 锁定） -->
            <div class="block">
              <div class="bl-title">人物锚点（生成人脸一致性硬约束 · MEM-04）</div>
              <div class="field"><label>事件年龄</label><el-input v-model="characterAnchors.age_at_event" size="small" placeholder="如：出生时 / 25 岁" @blur="characterAnchors = { ...characterAnchors }" /></div>
              <div class="field"><label>外观词</label><el-input v-model="caAppearance" size="small" placeholder="逗号分隔，≥3 项：圆脸,黑发,戴眼镜" /></div>
              <div class="field"><label>声音词</label><el-input v-model="caVoice" size="small" placeholder="逗号分隔，1-3 项：低沉稳重,带乡音" /></div>
              <div class="field"><label>性格词</label><el-input v-model="caPersonality" size="small" placeholder="逗号分隔，≥2 项：内敛,倔强" /></div>
              <div class="field"><label>核心关系</label><el-input v-model="characterAnchors.core_relationship" size="small" placeholder="≤50 字：与母亲相依为命" @blur="characterAnchors = { ...characterAnchors }" /></div>
              <div class="field"><label>口头禅</label><el-input v-model="characterAnchors.signature_phrase" size="small" placeholder="≤30 字（可选）" @blur="characterAnchors = { ...characterAnchors }" /></div>
              <div class="shot-actions">
                <el-button size="small" @click="saveCharacterAnchors">保存锚点</el-button>
                <el-button v-if="!memoir.character_anchors_locked" size="small" type="primary" @click="lockCharacterAnchors">🔒 锁定锚点</el-button>
                <el-button v-else size="small" @click="unlockCharacterAnchors">解锁（二次确认）</el-button>
                <span class="ma-hint" v-if="memoir.character_anchors_locked">已锁定：AI 生成图像必须包含上述外观关键词</span>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- 策划 -->
        <el-tab-pane label="策划" name="plan">
          <div class="tab-scroll">
            <h3>故事策划配置</h3>
            <div class="field"><label>故事板</label>
              <el-select v-model="storyboardCount" size="small" style="width:100%">
                <el-option v-for="o in OPTION_STORYBOARD_COUNTS" :key="o" :label="o + ' 张'" :value="o" />
              </el-select>
            </div>
            <div class="field"><label>比例</label>
              <el-select v-model="targetRatio" size="small" style="width:100%">
                <el-option v-for="o in OPTION_MEMOIR_RATIOS" :key="o" :label="o" :value="o" />
              </el-select>
            </div>
            <div class="field"><label>分辨率</label>
              <el-select v-model="targetResolution" size="small" style="width:100%">
                <el-option v-for="o in OPTION_MEMOIR_RESOLUTIONS" :key="o" :label="o" :value="o" />
              </el-select>
            </div>
            <div class="block">
              <div class="bl-title">强化情绪（逗号分隔）</div>
              <el-input v-model="memoir.enhance_emotion" size="small" placeholder="如：克制、温暖" @blur="updateMemoir({ enhance_emotion: memoir.enhance_emotion })" />
            </div>
            <div class="block">
              <div class="bl-title">不希望出现的内容</div>
              <el-input v-model="memoir.exclude_content" type="textarea" :autosize="{minRows:2}" size="small"
                        placeholder="如：不出现未提及的疾病、冲突或现代物品" @blur="updateMemoir({ exclude_content: memoir.exclude_content })" />
            </div>
            <div class="block">
              <div class="bl-title">叙事设定（story.setting）</div>
              <el-input v-model="storySetting" type="textarea" :autosize="{minRows:2}" size="small"
                        placeholder="故事发生的时空与基调" />
            </div>
            <div class="block">
              <div class="bl-title">旁白底稿（story.voice_narration）</div>
              <el-input v-model="storyVoice" type="textarea" :autosize="{minRows:2}" size="small"
                        placeholder="贯穿全片的旁白文案" />
            </div>
            <div class="block">
              <div class="bl-title">策划备注（story.notes）</div>
              <el-input v-model="storyNotes" type="textarea" :autosize="{minRows:2}" size="small"
                        placeholder="给导演板的补充提示" />
            </div>
          </div>
        </el-tab-pane>

        <!-- 指导板 -->
        <el-tab-pane label="指导板" name="board">
          <div class="tab-scroll">
            <div class="gb-head">
              <div>
                <div class="gb-title">导演创意指导板 · {{ activeChapter ? ('第' + String(activeIndex + 1).padStart(2,'0') + '章') : '' }}</div>
                <div class="gb-meta">{{ boardMeta }}</div>
              </div>
              <div class="gb-tools">
                <el-button size="small" @click="onFullscreen">⛶ 全屏预览</el-button>
                <el-button size="small" @click="onDownload">⬇ 下载</el-button>
              </div>
            </div>

            <div class="board-img-wrap">
              <img v-if="activeChapter?.storyboard_image_url" :src="activeChapter.storyboard_image_url" class="board-img" />
              <div v-else class="board-empty">
                <div v-if="busy && taskStatus === 'running'" class="spinner">指导板生成中…</div>
                <div v-else>尚未生成指导板<br />点击下方「重新生成指导板」</div>
              </div>
            </div>

            <el-collapse :model-value="['a','b','c','d','e','shots','g','h','palette']" class="mt">
              <el-collapse-item title="A. 角色参考" name="a">
                <div class="chips">
                  <span v-for="(c, i) in (activeChapter?.shot_data?.characters || [])" :key="i" class="asset-tag character">{{ c }}</span>
                  <span v-if="!(activeChapter?.shot_data?.characters || []).length" class="muted">（AI 策划生成）</span>
                </div>
              </el-collapse-item>
              <el-collapse-item title="B. 环境与场景设计" name="b">
                <div class="chips">
                  <span v-for="(s, i) in (activeChapter?.shot_data?.scenes || [])" :key="i" class="asset-tag scene">{{ s }}</span>
                  <span v-if="!(activeChapter?.shot_data?.scenes || []).length" class="muted">（AI 策划生成）</span>
                </div>
              </el-collapse-item>
              <el-collapse-item title="C. 摄影机路径" name="c">
                <div class="txt">{{ activeChapter?.shot_data?.camera_plan || '（AI 策划生成）' }}</div>
              </el-collapse-item>
              <el-collapse-item title="D. 灯光方案（MEM-07）" name="d">
                <div class="block">
                  <div class="field"><label>主光质感</label>
                    <el-select v-model="lightingScheme.key_light" size="small" style="width:100%" @change="lightingScheme = { ...lightingScheme }">
                      <el-option label="硬光 hard" value="hard" /><el-option label="柔光 soft" value="soft" /><el-option label="自然 natural" value="natural" />
                    </el-select>
                  </div>
                  <div class="field"><label>逆光</label>
                    <el-switch v-model="lightingScheme.back_light" @change="lightingScheme = { ...lightingScheme }" />
                  </div>
                  <div class="field"><label>辅光比</label>
                    <el-slider v-model="lightingScheme.fill_ratio" :min="0" :max="1" :step="0.05" @change="lightingScheme = { ...lightingScheme }" />
                    <span class="muted">{{ lightingScheme.fill_ratio }}</span>
                  </div>
                  <div class="field"><label>色温(K)</label>
                    <el-input-number v-model="lightingScheme.color_temp" :min="2700" :max="10000" :step="100" size="small" @change="lightingScheme = { ...lightingScheme }" />
                  </div>
                  <div class="field"><label>描述</label>
                    <el-input v-model="lightingScheme.description" type="textarea" :autosize="{minRows:2}" size="small" placeholder="≤80 字自然语言描述" @blur="lightingScheme = { ...lightingScheme }" />
                  </div>
                </div>
              </el-collapse-item>
              <el-collapse-item title="E. 情绪关键词 / 情绪色卡（MEM-08）" name="e">
                <div class="chips">
                  <span v-for="(k, i) in (activeChapter?.shot_data?.mood_keywords || [])" :key="i" class="asset-tag character">{{ k }}</span>
                  <span v-if="!(activeChapter?.shot_data?.mood_keywords || []).length" class="muted">（AI 策划生成）</span>
                </div>
                <div class="bl-title" style="margin-top:12px">情绪色卡（3-6 色）</div>
                <div class="palette">
                  <div v-for="(c, i) in emotionPalette" :key="i" class="color-swatch" :style="{ background: c.hex }">
                    {{ c.name }} <span class="sw-x" @click="removeSwatch(i)">×</span>
                  </div>
                  <span v-if="!emotionPalette.length" class="muted">（AI 策划生成）</span>
                </div>
                <div class="tl-add" style="margin-top:8px">
                  <el-input v-model="newSwatchName" size="small" placeholder="色卡名" style="width:110px" />
                  <el-color-picker v-model="newSwatchHex" size="small" />
                  <el-button size="small" @click="addSwatch">＋ 添加色卡</el-button>
                </div>
              </el-collapse-item>
              <el-collapse-item :title="`F. 镜头表格（BR-016 时长之和=${shotSum}s / 章节${activeChapter?.duration||15}s）`" name="shots">
                <div v-if="shotMismatch" class="warn-bar">⚠ 分镜时长之和不等于章节时长（BR-016）</div>
                <el-table :data="currentShots" size="small" border style="width:100%">
                  <el-table-column label="镜号" width="54">
                    <template #default="{row, $index}"><span class="sno">{{ String($index + 1).padStart(2,'0') }}</span></template>
                  </el-table-column>
                  <el-table-column label="景别" width="92">
                    <template #default="{row}">
                      <template v-if="editingShots">
                        <el-select v-model="row.shot_type" size="small" style="width:100%">
                          <el-option v-for="o in SHOT_TYPES" :key="o.value" :label="o.label" :value="o.value" />
                        </el-select>
                      </template>
                      <template v-else>{{ shotTypeLabel(row.shot_type) }}</template>
                    </template>
                  </el-table-column>
                  <el-table-column label="运镜" width="84">
                    <template #default="{row}">
                      <template v-if="editingShots">
                        <el-select v-model="row.camera_movement" size="small" style="width:100%">
                          <el-option v-for="o in CAMERA_MOVES" :key="o.value" :label="o.label" :value="o.value" />
                        </el-select>
                      </template>
                      <template v-else>{{ camMoveLabel(row.camera_movement) }}</template>
                    </template>
                  </el-table-column>
                  <el-table-column label="摄影机路径" width="150">
                    <template #default="{row}">
                      <el-select v-model="row.camera_path" size="small" style="width:100%">
                        <el-option v-for="o in CAMERA_PATH_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                      </el-select>
                    </template>
                  </el-table-column>
                  <el-table-column label="时长" width="86">
                    <template #default="{row}">
                      <el-input-number v-if="editingShots" v-model="row.duration" :min="1" :max="30" size="small" style="width:100%" />
                      <template v-else>{{ row.duration }}s</template>
                    </template>
                  </el-table-column>
                  <el-table-column label="画面描述" prop="description" show-overflow-tooltip />
                  <el-table-column label="旁白 / 台词" prop="voiceover" show-overflow-tooltip />
                  <el-table-column label="缩略图" width="78">
                    <template #default="{row}">
                      <img v-if="row.thumbnail_url" :src="row.thumbnail_url" class="tthumb" />
                      <div v-else class="tthumb empty">图</div>
                    </template>
                  </el-table-column>
                </el-table>
                <div class="shot-actions">
                  <el-button v-if="!editingShots" size="small" @click="startEditShots">编辑分镜</el-button>
                  <template v-else>
                    <el-button size="small" @click="autoDistributeShots">自动均分时长</el-button>
                    <el-button size="small" type="primary" @click="saveShots">保存分镜</el-button>
                    <el-button size="small" @click="editingShots = false">取消</el-button>
                  </template>
                </div>
              </el-collapse-item>
              <el-collapse-item title="G. 声音与旁白（波形图）" name="g">
                <div class="txt">{{ activeChapter?.shot_data?.audio_description || '（AI 策划生成）' }}</div>
                <div class="waveform">
                  <span v-for="(h, i) in waveformBars" :key="i" class="wbar" :style="{ height: h + '%' }"></span>
                </div>
                <div class="wave-cap">旁白波形预览（演示）</div>
                <!-- §09 MEM-09 声音旁白 TTS -->
                <div class="bl-title" style="margin-top:14px">声音旁白生成（MEM-09）</div>
                <div class="tl-add">
                  <el-select v-model="narrationVoice" size="small" style="width:200px">
                    <el-option v-for="o in NARRATION_VOICE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                  </el-select>
                </div>
                <el-input v-model="narrationText" type="textarea" :autosize="{minRows:2}" size="small" style="margin-top:8px"
                          placeholder="输入旁白文本（≤500 字/段），本地 edge-tts 优先，不可用时降级仅保存文本" />
                <div class="shot-actions" style="margin-top:8px">
                  <el-button size="small" type="primary" :loading="busy" @click="genNarration">🎙 生成旁白</el-button>
                </div>
                <ul class="bullets" style="margin-top:8px">
                  <li v-for="(n, i) in narrations" :key="i">
                    {{ n.text }} <span class="muted" v-if="n.local_fallback">（本地降级·仅文本）</span>
                    <span v-else-if="n.audio_url">🔊</span>
                  </li>
                  <li v-if="!narrations.length" class="muted">暂无旁白</li>
                </ul>
              </el-collapse-item>
              <el-collapse-item title="H. 旁白备注" name="h">
                <ul class="bullets">
                  <li v-for="(n, i) in (activeChapter?.shot_data?.voiceover_notes || [])" :key="i">· {{ n }}</li>
                  <li v-if="!(activeChapter?.shot_data?.voiceover_notes || []).length" class="muted">（AI 策划生成）</li>
                </ul>
              </el-collapse-item>
              <el-collapse-item title="I. 色彩参考（五色色卡）" name="palette">
                <div class="palette">
                  <div v-for="(c, i) in palette" :key="i" class="color-swatch" :style="{ background: c.hex || c.color }">{{ c.color_name || c.hex }}</div>
                  <span v-if="!palette.length" class="muted">（AI 策划生成）</span>
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>
        </el-tab-pane>

        <!-- 审片 -->
        <el-tab-pane label="审片" name="review">
          <div class="tab-scroll">
            <h3>生成前检查（BR-021）</h3>
            <div class="check-list">
              <div v-for="(c, i) in preGenChecks" :key="i" class="check-item" :class="c.status">
                <span class="ck">{{ c.status === 'pass' ? '✓' : (c.status === 'warn' ? '⚠' : '✗') }}</span>{{ checkLabel(c.itemId) }} <span class="expect">{{ c.message }}</span>
              </div>
            </div>
            <h3 style="margin-top:18px">章节审片</h3>
            <div v-for="c in chapters" :key="c.chapter_id" class="review-ch">
              <span class="rc-idx">{{ String(chapters.indexOf(c) + 1).padStart(2,'0') }}</span>
              <span class="rc-name">{{ c.title || '未命名' }}</span>
              <span class="rc-status" :class="reviewStatusCls(c)">{{ reviewStatusText(c) }}</span>
              <span v-if="c.storyboard_image_url" class="rc-thumb">指导板✓</span>
              <span v-if="c.video_url" class="rc-thumb ok">视频✓</span>
            </div>
            <div class="block" style="margin-top:16px">
              <el-button size="small" type="primary" :disabled="!canGenerate" @click="submitReview">提交审片</el-button>
              <span class="ma-hint" v-if="!canGenerate">请先通过全部生成前检查（BR-021 / MEM-05）</span>
            </div>
          </div>
        </el-tab-pane>

        <!-- 视频 -->
        <el-tab-pane label="视频" name="video">
          <div class="tab-scroll two-col">
            <div>
              <h3>输出参数</h3>
              <el-descriptions :column="1" size="small" border>
                <el-descriptions-item label="成片比例">{{ memoir.target_ratio }}</el-descriptions-item>
                <el-descriptions-item label="成片规格">{{ memoir.target_resolution }} · 30fps</el-descriptions-item>
                <el-descriptions-item label="指导板">2048×1152 / {{ memoir.target_ratio }}</el-descriptions-item>
                <el-descriptions-item label="总时长">{{ totalDuration }}s</el-descriptions-item>
                <el-descriptions-item label="字幕">不生成 · 后期自行添加</el-descriptions-item>
              </el-descriptions>
              <div class="block">
                <div class="bl-title">模型配置（MEM-15 · 四模型独立配置，本地等价替代）</div>
                <div class="field"><label>策划模型</label>
                  <el-select v-model="modelConfig.planning_model" size="small" style="width:100%" @change="saveModelConfig">
                    <el-option v-for="o in MODEL_OPTIONS.planning_model" :key="o" :label="o" :value="o" />
                  </el-select>
                </div>
                <div class="field"><label>指导板</label>
                  <el-select v-model="modelConfig.board_image_model" size="small" style="width:100%" @change="saveModelConfig">
                    <el-option v-for="o in MODEL_OPTIONS.board_image_model" :key="o" :label="o" :value="o" />
                  </el-select>
                </div>
                <div class="field"><label>视频模型</label>
                  <el-select v-model="modelConfig.video_model" size="small" style="width:100%" @change="saveModelConfig">
                    <el-option v-for="o in MODEL_OPTIONS.video_model" :key="o" :label="o" :value="o" />
                  </el-select>
                </div>
                <div class="field"><label>音频模型</label>
                  <el-select v-model="modelConfig.audio_model" size="small" style="width:100%" @change="saveModelConfig">
                    <el-option v-for="o in MODEL_OPTIONS.audio_model" :key="o" :label="o" :value="o" />
                  </el-select>
                </div>
              </div>
              <div class="block">
                <div class="bl-title">事实与人物锚点</div>
                <div class="anchors">
                  <div class="anchor">以用户上传的人物参考照为唯一视觉依据。</div>
                  <div class="anchor">策划模型未返回单独事实条目，仍以原始经历为唯一依据。</div>
                </div>
              </div>
            </div>
            <div>
              <h3>本章视频预览</h3>
              <div class="preview-box">
                <video v-if="activeChapter?.video_url" :src="activeChapter.video_url" controls class="pv" />
                <div v-else class="empty">🎞️ 暂无本章视频<br />点击下方生成</div>
              </div>
              <el-button size="small" type="warning" :disabled="busy" @click="onVideo" style="margin-top:12px">▶ 重生成本章视频</el-button>
            </div>
          </div>
        </el-tab-pane>

        <!-- 合成 -->
        <el-tab-pane label="合成" name="compose">
          <div class="tab-scroll">
            <h3>合成与交付</h3>
            <div class="block">
              <div class="bl-title">当前章节</div>
              <el-descriptions :column="1" size="small" border>
                <el-descriptions-item label="情绪">{{ activeChapter?.emotion || '—' }}</el-descriptions-item>
              </el-descriptions>
              <div class="bl-title" style="margin-top:10px">模型提示词</div>
              <el-input :model-value="activeChapter?.storyboard_prompt || '（生成后显示）'" type="textarea" :rows="6" readonly size="small" class="code-block" />
            </div>
            <div class="block">
              <div class="bl-title">图片信息</div>
              <el-descriptions :column="1" size="small" border>
                <el-descriptions-item label="片名">{{ imageInfo.title }}</el-descriptions-item>
                <el-descriptions-item label="人物">{{ imageInfo.characters }}</el-descriptions-item>
                <el-descriptions-item label="地点">{{ imageInfo.location }}</el-descriptions-item>
                <el-descriptions-item label="时间">{{ imageInfo.era }}</el-descriptions-item>
                <el-descriptions-item label="画面标准">{{ imageInfo.format }}</el-descriptions-item>
                <el-descriptions-item label="预估时长">{{ imageInfo.duration }}秒</el-descriptions-item>
                <el-descriptions-item label="镜头总数">{{ imageInfo.shot_count }}</el-descriptions-item>
                <el-descriptions-item label="页码">{{ imageInfo.page }}</el-descriptions-item>
              </el-descriptions>
            </div>
            <div class="block">
              <div class="bl-title">合成（FFmpeg 等价）</div>
              <div class="txt">按章节顺序拼接指导板 / 视频，叠加本地 TTS 旁白与字幕，输出最终成片（演示）。</div>
              <div class="shot-actions" style="margin-top:10px">
                <el-button size="small" type="primary" @click="exportPkg">导出交付包</el-button>
              </div>
            </div>
          </div>
        </el-tab-pane>

      </el-tabs>
    </div>

    <!-- ③-2 事实锚点面板（§09 MEM-03 · 对 AI 的硬约束） -->
    <div class="m-anchors">
      <div class="ma-head">
        <h3>事实锚点（MEM-03）</h3>
        <span class="muted">锁定项将注入所有策划 / 指导板 / 生成 prompt 末尾 ## FACT_ANCHORS 节</span>
      </div>
      <div class="anchor-form">
        <el-select v-model="newAnchorType" size="small" style="width:120px">
          <el-option label="事件" value="event" /><el-option label="关系" value="relation" />
          <el-option label="日期" value="date" /><el-option label="地点" value="location" /><el-option label="其他" value="other" />
        </el-select>
        <el-input v-model="newAnchorContent" size="small" placeholder="如：2008 年妈妈确诊癌症这件事必须出现" style="flex:1" @keyup.enter="addAnchor" />
        <el-checkbox v-model="newAnchorLocked" size="small">硬约束</el-checkbox>
        <el-button size="small" type="primary" @click="addAnchor">＋ 添加</el-button>
      </div>
      <div class="anchor-list">
        <div v-for="(a, i) in anchors" :key="a.id || i" class="anchor-row" :class="{ locked: a.is_locked }">
          <span class="a-type">{{ a.anchor_type }}</span>
          <span class="a-content">{{ a.content }}</span>
          <el-switch v-model="a.is_locked" size="small" @change="toggleAnchorLock(a)" />
          <span class="x" @click="removeAnchor(a)">×</span>
        </div>
        <div v-if="!anchors.length" class="muted">暂无事实锚点</div>
      </div>
    </div>

    <!-- ④ 底部操作栏 -->
    <div class="m-action">
      <div class="ma-left">
        <el-button size="small" :loading="busy && taskStatus==='running'" @click="plan">🧠 AI 故事策划</el-button>
        <span class="ma-hint" v-if="busy">生成中：{{ taskStatus }}</span>
      </div>
      <div class="ma-right">
        <el-button size="small" :disabled="busy" @click="onStoryboard">🔄 重新生成指导板</el-button>
      </div>
    </div>
  </div>
  <div v-else class="page"><div class="empty">加载中…</div></div>
</template>

<style scoped>
.memoir { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.m-top { flex: 0 0 auto; display: flex; align-items: center; gap: 12px; padding: 10px 18px; background: var(--bg-2); border-bottom: 1px solid var(--line); }
.back { background: var(--card); border: 1px solid var(--line); color: var(--sub); border-radius: 8px; padding: 6px 12px; cursor: pointer; font-size: 13px; }
.back:hover { color: var(--txt); border-color: var(--pur); }
.m-tag { background: var(--grad); color: #fff; font-size: 11px; font-weight: 800; padding: 3px 9px; border-radius: 6px; }
.mtitle { display: flex; flex-direction: column; }
.mi { background: transparent; border: 0; border-bottom: 1px solid transparent; color: var(--txt); font-size: 16px; font-weight: 800; outline: none; width: 220px; }
.mi:focus { border-bottom-color: var(--pur); }
.msub { font-size: 11px; color: var(--sub); }
.m-right { margin-left: auto; display: flex; align-items: center; gap: 14px; font-size: 13px; color: var(--sub); }
.m-right b { color: var(--txt); }
.person-switch { display: flex; align-items: center; gap: 6px; }

.m-chapters { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; padding: 8px 18px; background: var(--panel); border-bottom: 1px solid var(--line); overflow-x: auto; }
.ch-tab { display: flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 10px; background: var(--card); border: 1px solid var(--line); cursor: pointer; white-space: nowrap; font-size: 13px; }
.ch-tab.on { border-color: var(--pur); background: rgba(138,92,255,.12); }
.ch-tab.failed { border-color: #ff7a7a; }
.ch-idx { font-weight: 800; color: var(--pur); }
.ch-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--warn); }
.ch-dot.ok { background: var(--green); }
.ch-dot.fail { background: #ff7a7a; }
.ch-add { padding: 6px 10px; border: 1px dashed var(--line); border-radius: 10px; background: transparent; color: var(--sub); cursor: pointer; font-size: 13px; }
.ch-add:hover:not(:disabled) { border-color: var(--pur); color: var(--txt); }
.ch-add:disabled { opacity: .4; cursor: not-allowed; }
.ch-input { background: var(--card); border: 1px solid var(--line); color: var(--txt); border-radius: 8px; padding: 5px 10px; font-size: 12px; width: 160px; outline: none; }

.m-body { flex: 1; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.memoir-tabs { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.memoir-tabs :deep(.el-tabs__header) { margin: 0; padding: 0 14px; background: var(--panel); }
.memoir-tabs :deep(.el-tabs__content) { flex: 1; min-height: 0; overflow: hidden; }
.memoir-tabs :deep(.el-tab-pane) { height: 100%; }
.tab-scroll { height: 100%; overflow-y: auto; padding: 16px; box-sizing: border-box; }
.tab-scroll.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
.waveform { display: flex; align-items: center; gap: 2px; height: 56px; margin: 10px 0 4px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; box-sizing: border-box; }
.wbar { flex: 1; min-width: 2px; background: linear-gradient(180deg, var(--pur), var(--pink)); border-radius: 2px; }
.wave-cap { font-size: 11px; color: var(--sub); }
.review-ch { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; margin-bottom: 6px; font-size: 13px; }
.rc-idx { font-weight: 800; color: var(--pur); }
.rc-name { flex: 1; }
.rc-status { font-size: 12px; color: var(--sub); }
.rc-status.ok { color: var(--green); } .rc-status.partial { color: var(--warn); } .rc-status.fail { color: #ff7a7a; }
.rc-thumb { font-size: 11px; color: var(--sub); }
.rc-thumb.ok { color: var(--green); }
.preview-box { background: #0c0e13; border: 1px solid var(--line); border-radius: 12px; aspect-ratio: 16/9; display: flex; align-items: center; justify-content: center; color: var(--sub); font-size: 13px; text-align: center; padding: 20px; box-sizing: border-box; }
.pv { width: 100%; height: 100%; object-fit: contain; }
.col { overflow-y: auto; padding: 16px; border-right: 1px solid var(--line); }
.col-left { background: var(--panel); }
.col-mid { background: var(--bg-2); }
.col-right { border-right: 0; }
.col h3 { font-size: 13px; color: var(--sub); text-transform: uppercase; letter-spacing: 1px; margin: 0 0 12px; }
.field { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.field label { width: 48px; color: var(--sub); font-size: 13px; flex: 0 0 48px; }
.block { margin-bottom: 16px; }
.bl-title { font-size: 13px; font-weight: 700; color: var(--txt); margin-bottom: 8px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.txt { font-size: 13px; color: var(--txt); line-height: 1.7; background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
.tl-row { display: flex; gap: 6px; margin-bottom: 6px; align-items: center; }
.tl-age { font-size: 12px; color: var(--pur); font-weight: 700; flex: 0 0 38px; }
.tl-row .x { cursor: pointer; color: var(--sub); }
.tl-row .x:hover { color: var(--pink); }
.tl-add { display: flex; gap: 6px; }
.ref-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.ref-ph { aspect-ratio: 3/4; background: linear-gradient(135deg, #2a2030, #14161c); border: 1px solid var(--line); border-radius: 10px; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; padding: 5px; font-size: 11px; color: var(--sub); overflow: hidden; }
.ref-img { width: 100%; height: 100%; object-fit: cover; border-radius: 8px; }
.ref-name { margin-top: 3px; }
.ref-ph.add { align-items: center; justify-content: center; border: 1px dashed var(--line); cursor: pointer; color: var(--sub); font-size: 20px; }
.ref-ph.add:hover { border-color: var(--pur); color: var(--txt); }

.gb-head { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 14px; }
.gb-title { font-size: 16px; font-weight: 800; }
.gb-meta { font-size: 12px; color: var(--sub); margin-top: 4px; }
.gb-tools { margin-left: auto; display: flex; gap: 8px; }
.board-img-wrap { background: #0c0e13; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; aspect-ratio: 16/9; display: flex; align-items: center; justify-content: center; margin-bottom: 14px; }
.board-img { width: 100%; height: 100%; object-fit: contain; }
.board-empty { color: var(--sub); font-size: 13px; text-align: center; padding: 20px; line-height: 1.8; }
.spinner { color: var(--warn); }
.mt { margin-top: 4px; }
.sno { display: inline-flex; width: 22px; height: 22px; align-items: center; justify-content: center; background: var(--pink); color: #fff; border-radius: 6px; font-weight: 700; font-size: 12px; }
.tthumb { width: 100%; aspect-ratio: 16/9; background: var(--card-2); border-radius: 6px; display: flex; align-items: center; justify-content: center; color: var(--sub); font-size: 11px; }
.tthumb.empty { opacity: .5; }
.shot-actions { display: flex; gap: 8px; margin-top: 10px; }
.warn-bar { margin-bottom: 8px; background: rgba(255,176,32,.12); border: 1px solid rgba(255,176,32,.4); color: var(--warn); font-size: 12px; padding: 6px 8px; border-radius: 8px; }
.voice-text { font-size: 13px; color: var(--txt); padding: 8px 10px; background: var(--card); border-radius: 8px; border-left: 3px solid var(--pink); }
.bullets { margin: 8px 0 0; padding-left: 16px; color: var(--sub); font-size: 12px; line-height: 1.9; }
.palette { display: flex; gap: 8px; flex-wrap: wrap; }
.color-swatch { width: 80px; height: 52px; border-radius: 10px; border: 1px solid var(--line); display: flex; align-items: flex-end; padding: 5px 7px; font-size: 11px; color: #fff; text-shadow: 0 1px 2px rgba(0,0,0,.6); }

.model-cfg { display: flex; flex-direction: column; gap: 6px; }
.mc { display: flex; justify-content: space-between; font-size: 13px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px; }
.mc span { color: var(--sub); } .mc b { color: var(--txt); font-weight: 600; }
.check-list { display: flex; flex-direction: column; gap: 8px; }
.check-item { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--sub); }
.check-item .ck { width: 18px; height: 18px; border-radius: 50%; border: 1px solid var(--line); display: inline-flex; align-items: center; justify-content: center; font-size: 11px; flex: 0 0 18px; }
.check-item.ok { color: var(--txt); }
.check-item.ok .ck { background: var(--green); border-color: var(--green); color: #06210f; }
.expect { color: var(--sub); font-size: 11px; margin-left: auto; }
.anchors { display: flex; flex-direction: column; gap: 6px; }
.anchor { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; font-size: 12px; color: var(--txt); }
.code-block :deep(textarea) { font-family: ui-monospace, Menlo, monospace; color: var(--txt); background: #0f1117; }

.m-action { flex: 0 0 auto; border-top: 1px solid var(--line); background: var(--panel); padding: 10px 18px; display: flex; align-items: center; gap: 16px; }
.ma-left { display: flex; align-items: center; gap: 12px; }
.ma-right { margin-left: auto; display: flex; gap: 8px; }
.ma-hint { font-size: 12px; color: var(--warn); }
.muted { color: var(--sub); font-size: 12px; }
.credits { background: rgba(138,92,255,.15); border: 1px solid rgba(138,92,255,.4); color: var(--pur); border-radius: 10px; padding: 5px 12px; font-weight: 700; }
/* §09 G3 MEM-03 / 04 / 13 / 05 新增样式 */
.m-anchors { flex: 0 0 auto; border-top: 1px solid var(--line); background: var(--panel); padding: 12px 18px; }
.ma-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.ma-head h3 { font-size: 14px; margin: 0; }
.anchor-form { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
.anchor-list { display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto; }
.anchor-row { display: flex; align-items: center; gap: 10px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; font-size: 13px; }
.anchor-row.locked { border-color: rgba(56,189,248,.5); }
.a-type { font-size: 11px; color: var(--pur); font-weight: 700; flex: 0 0 auto; text-transform: uppercase; }
.a-content { flex: 1; color: var(--txt); }
.anchor-row .x { cursor: pointer; color: var(--sub); }
.anchor-row .x:hover { color: var(--pink); }
.sw-x { cursor: pointer; margin-left: 4px; opacity: .6; }
.sw-x:hover { opacity: 1; color: #fff; }
.ch-tab { cursor: grab; }
.ch-tab.dragging { opacity: .45; border-style: dashed; }
.check-item.warn { color: var(--warn); }
.check-item.warn .ck { background: var(--warn); border-color: var(--warn); color: #2a1d00; }
.check-item.fail { color: #ff9a9a; }
.check-item.fail .ck { background: #ff7a7a; border-color: #ff7a7a; color: #2a0000; }

/* 资产胶囊配色：角色紫 / 场景蓝 / 道具黄 */
.asset-tag { display: inline-block; padding: 2px 9px; border-radius: 8px; font-size: 12px; border: 1px solid; }
.asset-tag.character { background: rgba(138,92,255,.15); color: #b79bff; border-color: rgba(138,92,255,.4); }
.asset-tag.scene { background: rgba(56,189,248,.15); color: #7dd3fc; border-color: rgba(56,189,248,.4); }
</style>
