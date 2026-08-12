<script setup>
import { ref, reactive, computed, watch, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  updateAsset, uploadImage, listStagesWithPoses, generateCharacterCard,
  addStage, delStage, addPose, delPose,
  getAssetFaceVectors, getStageFaceVector, extractStageFaceVector,
} from '../api'
import { toast } from '../store'
import { splitVoice, joinVoice, stageNameFor } from '../assetMeta'
import { pruneRefs } from '../composables/useProject'
import { useAutoSave } from '../composables/useAutoSave'
import { ElMessageBox } from 'element-plus'
import {
  ASSET_STAGE_OPTIONS, ASSET_ROLE_TYPE_OPTIONS, ASSET_POSE_OPTIONS,
} from '../mock'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  role: { type: Object, default: null },
  // F1：所属项目 id（用于删除阶段后级联清理悬空 @引用）；未传则回退到路由参数
  projectId: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'saved', 'preview', 'stagesChanged'])

const router = useRouter()
const route = useRoute()
// F1：删除阶段后级联清理悬空 @引用；优先用显式 projectId prop，否则回退路由参数
const pid = computed(() => props.projectId || route.params.id)

const PLACEHOLDER = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0%25' stop-color='%2320242e'/><stop offset='100%25' stop-color='%233a3f4d'/></linearGradient></defs><rect width='200' height='200' fill='url(%23g)'/><text x='50%25' y='55%25' dominant-baseline='middle' text-anchor='middle' fill='%236b7283' font-size='70'>🖼️</text></svg>"

// 图片加载失败（如 URL 失效/跨域被拦）时回退到占位图，避免破图图标
const imgError = (e) => {
  const t = e && e.target
  if (t && t.src !== PLACEHOLDER) { t.onerror = null; t.src = PLACEHOLDER }
}

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const busy = ref(false)
const stages = ref([])
const dirty = ref(false)
let suppressDirty = true
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

// 手册 §5.1：真人资产身份特征向量维度固定 512；§5.3：资产调用前必须权限校验
const FACE_VECTOR_DIM = 512
const busyVectorStage = ref('') // 当前正在提取 512 维向量的阶段 ID
const busyCard = ref(false) // 当前正在经意图翻译引擎生成角色卡

const form = reactive({
  id: '',
  name: '',
  roleType: 'protagonist',
  description: '',
  voice: '',
  coverUrl: '',
})

// 阶段/姿势交互状态
const selectedStageId = ref('')
const previewUrl = ref('')
const stageForm = reactive({ open: false, stage_name: 'youth', age: 25, file: null, coverUrl: '' })
const poseForm = reactive({ open: false, stageId: '', pose_name: 'front', file: null })

// 音色试听状态
const voiceSample = ref('')
const isSpeaking = ref(false)
const currentAudio = ref(null)
const audioFileUrl = ref('')
const audioInputKey = ref(0)

const resetForm = () => {
  form.id = ''
  form.name = ''
  form.roleType = 'protagonist'
  form.description = ''
  form.voice = ''
  form.coverUrl = ''
  stages.value = []
  selectedStageId.value = ''
  resetVoicePreview()
}

const resetVoicePreview = () => {
  if (audioFileUrl.value) {
    URL.revokeObjectURL(audioFileUrl.value)
    audioFileUrl.value = ''
  }
  if (currentAudio.value) {
    currentAudio.value.pause()
    currentAudio.value = null
  }
  if (window.speechSynthesis) window.speechSynthesis.cancel()
  isSpeaking.value = false
  audioInputKey.value++
}

const loadStages = async (role) => {
  if (!role || !role.id) return
  try {
    stages.value = await listStagesWithPoses(role.id)
    // 加载每个阶段的 512 维身份特征向量状态（手册 §5.1 / §5.3）
    try {
      const vectors = await getAssetFaceVectors(role.id)
      stages.value = stages.value.map((s) => ({
        ...s,
        faceVector: vectors[s.stage_id] || { has_vector: false, dim: 0 },
      }))
    } catch (e) {
      console.warn('load face vectors failed', e)
    }
  } catch (e) {
    console.warn('load stages failed', e)
    stages.value = []
  }
}

// 阶段参考图数量：阶段主图 + 各姿势图（手册 §5.1 要求真人资产≥3张参考图）
const stageRefImageCount = (st) => {
  if (!st) return 0
  let n = st.reference_image_url ? 1 : 0
  n += (st.poses || []).filter((p) => p.reference_image_url).length
  return n
}

// 真人资产注入资格（§5.3 权限校验前置条件）：≥3张参考图 且 已提取 512 维向量
const injectEligible = (st) => {
  if (!st) return { ok: false, reason: '无阶段' }
  const cnt = stageRefImageCount(st)
  if (cnt < 3) return { ok: false, reason: '需≥3张参考图（当前' + cnt + '张）' }
  if (!(st.faceVector && st.faceVector.has_vector)) return { ok: false, reason: '未提取 512 维特征' }
  return { ok: true, reason: '可注入' }
}

// 提取阶段 512 维身份特征向量（诚实错误：底层引擎/权重缺失时显式提示，绝不静默降级）
const extractFaceVector = async (st) => {
  if (!form.id || !st.stage_id) return
  const cnt = stageRefImageCount(st)
  if (cnt < 3) {
    toast('需至少 3 张参考图（当前 ' + cnt + ' 张）才能提取 512 维特征')
    return
  }
  busyVectorStage.value = st.stage_id
  try {
    const r = await extractStageFaceVector(form.id, st.stage_id)
    toast('512 维特征提取成功（维度 ' + (r.dim || FACE_VECTOR_DIM) + '）')
    await loadStages({ id: form.id })
    emit('stagesChanged')
  } catch (e) {
    // 诚实透传后端错误（如 FACE_RUNTIME_MISSING / FACE_MODEL_MISSING / FACE_NOT_DETECTED）
    toast('提取失败: ' + (e.message || e))
  } finally {
    busyVectorStage.value = ''
  }
}

// 把 role 映射进表单（打开/切换角色时调用）
const loadRoleIntoForm = (r) => {
  if (!r) return
  form.id = r.id
  form.name = r.name || ''
  form.roleType = r.roleType || 'protagonist'
  const fullDesc = r.raw?.description ?? r.description ?? r.prompt ?? ''
  const parsed = splitVoice(fullDesc)
  form.description = parsed.desc
  form.voice = r.voice ?? parsed.voice ?? ''
  form.coverUrl = r.url || r.raw?.cover_url || PLACEHOLDER
  voiceSample.value = `你好，我是${form.name || '该角色'}，这是我的音色参考。`
  loadStages(r)
}

watch(() => props.role, (r) => {
  suppressDirty = true
  if (r) loadRoleIntoForm(r)
  else resetForm()
  nextTick(() => { suppressDirty = false; dirty.value = false })
}, { immediate: true })

// 打开弹窗时始终用最新 role 刷新，避免残留上次编辑
watch(visible, (v) => {
  if (v) {
    suppressDirty = true
    selectedStageId.value = ''
    if (props.role) loadRoleIntoForm(props.role)
    nextTick(() => { suppressDirty = false })
    dirty.value = false
  } else {
    dirty.value = false
    resetVoicePreview()
  }
})

// 表单变更 → 标记 dirty（自动保存据此触发）
watch(form, () => { if (!suppressDirty) dirty.value = true }, { deep: true })

const onCoverUpload = async (ev) => {
  const file = ev.target.files?.[0]
  if (!file) return
  if (!file.type.startsWith('image/')) { toast('请选择图片文件'); return }
  busy.value = true
  try {
    const r = await uploadImage(file)
    form.coverUrl = r.url
    toast('角色图已更新，保存后生效')
  } catch (e) {
    toast('上传失败: ' + e.message)
  } finally {
    busy.value = false
  }
}

// 上传音色参考音频（本地预览；后端暂无独立音频字段）
const onAudioUpload = (ev) => {
  const file = ev.target.files?.[0]
  if (!file) return
  if (!file.type.startsWith('audio/')) { toast('请选择音频文件'); return }
  if (audioFileUrl.value) URL.revokeObjectURL(audioFileUrl.value)
  audioFileUrl.value = URL.createObjectURL(file)
  toast('已加载音色参考音频，点击试听可播放')
}

const clearAudio = () => {
  if (audioFileUrl.value) {
    URL.revokeObjectURL(audioFileUrl.value)
    audioFileUrl.value = ''
  }
  audioInputKey.value++
}

// 音色试听：优先播放上传的参考音频，否则用浏览器 TTS 朗读试听文本
const playVoicePreview = () => {
  if (audioFileUrl.value) {
    if (currentAudio.value) { currentAudio.value.pause(); currentAudio.value = null }
    const audio = new Audio(audioFileUrl.value)
    currentAudio.value = audio
    audio.onended = () => { isSpeaking.value = false; currentAudio.value = null }
    audio.onerror = () => { isSpeaking.value = false; currentAudio.value = null; toast('音频播放失败') }
    isSpeaking.value = true
    audio.play().catch(() => { isSpeaking.value = false; currentAudio.value = null })
    return
  }
  if (!window.speechSynthesis) { toast('当前浏览器不支持语音试听'); return }
  const text = (voiceSample.value || `你好，我是${form.name || '该角色'}。`).trim()
  if (!text) { toast('请先输入试听文本'); return }
  const u = new SpeechSynthesisUtterance(text)
  u.lang = 'zh-CN'
  u.onend = () => { isSpeaking.value = false }
  u.onerror = () => { isSpeaking.value = false }
  isSpeaking.value = true
  window.speechSynthesis.speak(u)
}
const stopVoicePreview = () => {
  if (currentAudio.value) {
    currentAudio.value.pause()
    currentAudio.value.currentTime = 0
    currentAudio.value = null
  }
  if (window.speechSynthesis) window.speechSynthesis.cancel()
  isSpeaking.value = false
}

// 角色卡 AI 生成（导演agent优化草稿 #1）：经意图翻译引擎产出首张定妆照。
// 铁律 1.1：由结构化角色属性驱动（非自由 Prompt）；确定性 seed 保证跨次一致（修复「每次随机」）。
// 经后端 /assets/{id}/generate-card → intent_engine/templates/character_card_gen.json，绝不手搓节点。
const onAIGenerate = async () => {
  if (!form.id) { toast('请先保存角色后再生成定妆照'); return }
  busyCard.value = true
  try {
    const sel = stages.value.find((s) => s.stage_id === selectedStageId.value) || stages.value[0]
    const attrs = {
      name: form.name || '',
      age: sel ? Number(sel.age) || 25 : 25,
      gender: '',
      appearance: [],
    }
    const r = await generateCharacterCard(form.id, { attrs, style_tags: ['写实', '电影感'] })
    if (r && r.cover_url) {
      form.coverUrl = r.cover_url
      await loadStages({ id: form.id })
      emit('stagesChanged')
      toast('定妆照已生成（确定性种子，跨次一致）')
    } else {
      toast('生成未返回图像')
    }
  } catch (e) {
    toast('生成失败: ' + (e.message || e))
  } finally {
    busyCard.value = false
  }
}

const goCanvas = () => {
  if (!form.id) return
  visible.value = false
  router.push('/canvas?asset_id=' + encodeURIComponent(form.id))
}

// 真实持久化（不关闭弹窗，供自动保存与手动保存共用）
const persist = async () => {
  if (!form.name.trim()) { toast('角色名称不能为空'); return false }
  busy.value = true
  try {
    const fullDesc = joinVoice(form.description, form.voice)
    const body = {
      name: form.name.trim(),
      description: fullDesc,
      role_type: form.roleType,
      cover_url: form.coverUrl,
    }
    const r = await updateAsset(form.id, body)
    emit('saved', { ...props.role, name: form.name.trim(), prompt: form.description, voice: form.voice, roleType: form.roleType, url: form.coverUrl, raw: r })
    return true
  } catch (e) {
    toast('保存失败: ' + e.message)
    return false
  } finally {
    busy.value = false
  }
}
const save = async () => {
  const ok = await persist()
  if (ok) { visible.value = false; toast('角色已保存') }
}
// 自动保存：内容变更防抖落库，状态可见、失败显式提示
const { status: saveStatus } = useAutoSave({ dirty, save: persist, enabled: ref(!USE_MOCK), delay: 1500 })
// 点遮罩/ESC/× 关闭：有未保存修改则二次确认（防误关丢稿）
const beforeClose = (done) => {
  if (dirty.value) {
    ElMessageBox.confirm('角色有未保存的修改，关闭后将丢失。确定关闭？', '未保存修改', { confirmButtonText: '丢弃修改', cancelButtonText: '继续编辑', type: 'warning' })
      .then(() => done()).catch(() => {})
  } else { done() }
}

const roleTypeLabel = computed(() => ASSET_ROLE_TYPE_OPTIONS.find((o) => o.value === form.roleType)?.label || form.roleType)

// 阶段相关
const selectedStage = computed(() => stages.value.find((s) => s.stage_id === selectedStageId.value) || null)
const stageLabel = (v) => ASSET_STAGE_OPTIONS.find((o) => o.value === v)?.label || v
const poseLabel = (v) => ASSET_POSE_OPTIONS.find((o) => o.value === v)?.label || v
const selectStage = (st) => {
  if (selectedStageId.value === st.stage_id) {
    selectedStageId.value = ''
  } else {
    selectedStageId.value = st.stage_id
  }
}

const openStageForm = () => {
  if (!form.id) { toast('请先保存角色主卡片'); return }
  stageForm.open = true
  stageForm.stage_name = ''
  stageForm.age = 25
  stageForm.file = null
  stageForm.coverUrl = ''
}
const onStageFile = (ev) => {
  const f = ev.target.files?.[0] || null
  stageForm.file = f
  if (stageForm.coverUrl && stageForm.coverUrl.startsWith('blob:')) {
    URL.revokeObjectURL(stageForm.coverUrl)
  }
  stageForm.coverUrl = f ? URL.createObjectURL(f) : ''
}
const submitStage = async () => {
  if (!stageForm.file) { toast('请选择阶段参考照'); return }
  const suffix = stageForm.stage_name.trim()
  if (!suffix) { toast('请输入阶段名称后缀'); return }
  const parentName = (form.name || '').trim()
  if (!parentName) { toast('当前角色名称为空，无法生成层级阶段名'); return }
  // F4：角色阶段统一为层级命名（roleName-suffix），与 AssetStageManager / Assets.vue 一致
  const fullName = stageNameFor(parentName, 'role', suffix)
  busy.value = true
  try {
    const up = await uploadImage(stageForm.file)
    await addStage(form.id, {
      stage_name: fullName,
      age: Number(stageForm.age) || 25,
      reference_image_url: up.url || '',
    })
    toast('已添加阶段')
    stageForm.open = false
    if (stageForm.coverUrl && stageForm.coverUrl.startsWith('blob:')) {
      URL.revokeObjectURL(stageForm.coverUrl)
      stageForm.coverUrl = ''
    }
    await loadStages({ id: form.id })
    emit('stagesChanged')
  } catch (e) {
    toast('添加失败: ' + e.message)
  } finally {
    busy.value = false
  }
}
const removeStage = async (st) => {
  try {
    await ElMessageBox.confirm('删除该阶段及其姿势？', '删除确认', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
    await delStage(form.id, st.stage_id)
    // F1：级联清理该角色阶段在所有片段中的悬空 @引用
    await pruneRefs(pid.value, { assetId: form.id, stageId: st.stage_id })
    if (selectedStageId.value === st.stage_id) selectedStageId.value = ''
    await loadStages({ id: form.id })
    emit('stagesChanged')
  } catch (e) {
    if (e !== 'cancel') toast('删除失败: ' + (e.message || e))
  }
}

const openPoseForm = (st) => {
  poseForm.open = true
  poseForm.stageId = st.stage_id
  poseForm.pose_name = 'front'
  poseForm.file = null
}
const onPoseFile = (ev) => { poseForm.file = ev.target.files?.[0] || null }
const submitPose = async () => {
  if (!poseForm.file) { toast('请选择姿势参考照'); return }
  if (!poseForm.pose_name.trim()) { toast('请输入姿势名称'); return }
  busy.value = true
  try {
    const up = await uploadImage(poseForm.file)
    await addPose(poseForm.stageId, { pose_name: poseForm.pose_name.trim(), reference_image_url: up.url || '' })
    toast('已添加姿势')
    poseForm.open = false
    await loadStages({ id: form.id })
    emit('stagesChanged')
  } catch (e) {
    toast('添加失败: ' + e.message)
  } finally {
    busy.value = false
  }
}
const removePose = async (st, p) => {
  try {
    await ElMessageBox.confirm('删除该姿势？', '删除确认', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
    await delPose(st.stage_id, p.pose_id)
    await loadStages({ id: form.id })
    emit('stagesChanged')
  } catch (e) {
    if (e !== 'cancel') toast('删除失败: ' + (e.message || e))
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="编辑角色" width="960px" top="5vh" class="role-editor-dialog" destroy-on-close :before-close="beforeClose">
    <div class="role-editor-body" v-loading="busy">
      <!-- 上半部分：角色主信息 + 阶段详情 -->
      <div class="re-top">
        <!-- 左侧：角色大图 -->
        <div class="re-left">
          <div class="re-cover-wrap">
            <img :src="form.coverUrl || PLACEHOLDER" class="re-cover" @error="imgError" />
            <div class="re-cover-actions">
              <label class="re-cover-btn" title="替换角色图">
                <el-icon><Upload /></el-icon>
                <input type="file" accept="image/*" style="display:none" @change="onCoverUpload" />
              </label>
              <button class="re-cover-btn" title="预览大图" @click="emit('preview', form.coverUrl || PLACEHOLDER)">
                <el-icon><ZoomIn /></el-icon>
              </button>
            </div>
          </div>
        </div>

        <!-- 右侧：表单 + 阶段详情 -->
        <div class="re-right">
          <!-- 角色基础信息（紧凑） -->
          <div class="re-base">
            <div class="re-row re-row-inline">
              <div class="re-field" style="flex:1">
                <label class="re-label">角色名称</label>
                <el-input v-model="form.name" placeholder="输入角色名称" clearable />
              </div>
              <div class="re-field" style="width:150px">
                <label class="re-label">角色定位</label>
                <el-select v-model="form.roleType" placeholder="定位" style="width:100%">
                  <el-option v-for="o in ASSET_ROLE_TYPE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
              </div>
              <span class="re-role-badge" :class="form.roleType">{{ roleTypeLabel }}</span>
            </div>

            <div class="re-row">
              <label class="re-label">角色设定</label>
              <el-input v-model="form.description" type="textarea" :rows="3" placeholder="描述角色外貌、性格、语气、背景等，AI生成与分镜引用时作为提示词基础" />
            </div>

            <div class="re-row">
              <div class="re-voice-head">
                <label class="re-label">角色音色</label>
                <div class="re-voice-actions">
                  <label class="re-btn-text" :key="audioInputKey">
                    <el-icon><Microphone /></el-icon> 上传音频
                    <input type="file" accept="audio/*" style="display:none" @change="onAudioUpload" />
                  </label>
                  <button v-if="audioFileUrl" class="re-btn-text re-btn-text-danger" @click="clearAudio">
                    <el-icon><Close /></el-icon> 清除音频
                  </button>
                  <button v-if="!isSpeaking" class="re-btn-text" @click="playVoicePreview">
                    <el-icon><VideoPlay /></el-icon> 试听
                  </button>
                  <button v-else class="re-btn-text re-btn-text-active" @click="stopVoicePreview">
                    <el-icon><VideoPause /></el-icon> 停止
                  </button>
                </div>
              </div>
              <el-input v-model="form.voice" type="textarea" :rows="2" placeholder="描述角色配音音色：音色定位（如温柔青年男声）、语速、语调、情绪表达、口头禅等，供 AI 配音/口播参考" />
              <div class="re-voice-sample">
                <span class="re-voice-sample-label">试听文本</span>
                <input v-model="voiceSample" class="re-voice-sample-input" placeholder="输入用于试听的样本文本" />
                <span v-if="audioFileUrl" class="re-voice-hint">已启用上传的参考音频</span>
                <span v-else class="re-voice-hint">未上传音频时将使用浏览器语音合成试听</span>
              </div>
            </div>
          </div>

          <!-- 阶段详情（选中阶段时展开；默认折叠） -->
          <div v-if="selectedStage" class="re-stage-detail">
            <div class="re-stage-detail-head">
              <div>
                <span class="re-stage-detail-title">{{ stageLabel(selectedStage.stage_name) }}</span>
                <span v-if="selectedStage.age" class="re-stage-detail-age">{{ selectedStage.age }}岁</span>
              </div>
              <div class="re-stage-detail-actions">
                <button class="re-btn-icon" title="添加姿势" @click="openPoseForm(selectedStage)">
                  <el-icon><Plus /></el-icon>
                </button>
                <button class="re-btn-icon re-btn-icon-danger" title="删除阶段" @click="removeStage(selectedStage)">
                  <el-icon><Delete /></el-icon>
                </button>
              </div>
            </div>

            <!-- 真人资产 512 维身份特征向量（手册 §5.1 / §5.3） -->
            <div class="re-facevec">
              <span class="re-facevec-badge" :class="selectedStage.faceVector && selectedStage.faceVector.has_vector ? 'ok' : 'missing'">
                <el-icon><Key /></el-icon> 512维 {{ selectedStage.faceVector && selectedStage.faceVector.has_vector ? '已提取' : '缺失' }}
              </span>
              <span class="re-facevec-count" :class="{ warn: stageRefImageCount(selectedStage) < 3 }">参考图 {{ stageRefImageCount(selectedStage) }}/3</span>
              <span class="re-facevec-permission" :class="injectEligible(selectedStage).ok ? 'ok' : 'no'">
                <el-icon><Connection /></el-icon> {{ injectEligible(selectedStage).ok ? '可注入真人资产' : injectEligible(selectedStage).reason }}
              </span>
              <button class="re-btn-text re-facevec-extract" :disabled="busyVectorStage === selectedStage.stage_id" @click="extractFaceVector(selectedStage)">
                <el-icon v-if="busyVectorStage === selectedStage.stage_id"><Loading /></el-icon>
                <el-icon v-else><Refresh /></el-icon>
                {{ busyVectorStage === selectedStage.stage_id ? '提取中…' : '提取512维' }}
              </button>
            </div>

            <div class="re-stage-detail-body">
              <div class="re-stage-img-wrap" @click="previewUrl = selectedStage.reference_image_url">
                <img :src="selectedStage.reference_image_url || PLACEHOLDER" @error="imgError" />
                <div class="re-stage-img-hint"><el-icon><ZoomIn /></el-icon> 预览</div>
              </div>
              <div class="re-stage-poses">
                <div v-if="!(selectedStage.poses||[]).length" class="re-poses-empty">该阶段暂无姿势，点击 + 添加</div>
                <div v-else class="re-pose-list">
                  <div class="re-pose-chip" v-for="p in selectedStage.poses" :key="p.pose_id">
                    <img :src="p.reference_image_url || PLACEHOLDER" @error="imgError" @click="previewUrl = p.reference_image_url" />
                    <span class="re-pose-label">{{ poseLabel(p.pose_name) }}</span>
                    <button class="re-pose-del" @click="removePose(selectedStage, p)"><el-icon><Close /></el-icon></button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div v-else class="re-stage-empty">
            <div class="re-stage-empty-inner">
              <el-icon :size="28"><Picture /></el-icon>
              <p>点击下方阶段查看详情、编辑姿势</p>
            </div>
          </div>
        </div>
      </div>

      <!-- 下半部分：阶段菜单条（类似片段导航） -->
      <div class="re-bottom">
        <div class="re-stage-strip">
          <div
            v-for="st in stages"
            :key="st.stage_id"
            class="re-stage-mini"
            :class="{ active: selectedStageId === st.stage_id }"
            @click="selectStage(st)"
          >
            <div class="re-stage-mini-img">
              <img :src="st.reference_image_url || PLACEHOLDER" @error="imgError" />
              <span v-if="st.age" class="re-stage-mini-age">{{ st.age }}岁</span>
              <span class="re-stage-mini-vec" :class="st.faceVector && st.faceVector.has_vector ? 'ok' : 'missing'" :title="st.faceVector && st.faceVector.has_vector ? '512维已提取' : '512维缺失'"></span>
            </div>
            <div class="re-stage-mini-name">{{ stageLabel(st.stage_name) }}</div>
          </div>
          <button class="re-stage-mini re-stage-mini-add" @click="openStageForm">
            <span class="add-icon">＋</span>
            <span class="add-label">添加阶段</span>
          </button>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="re-footer">
        <el-button link @click="goCanvas">
          <el-icon><EditPen /></el-icon> 去画布编辑
        </el-button>
        <div class="re-footer-right">
          <el-button type="primary" plain :loading="busyCard" @click="onAIGenerate">
            <el-icon><MagicStick /></el-icon> AI 生成设定
          </el-button>
          <span class="re-autosave" :class="saveStatus">{{ { saving: '保存中…', saved: '已自动保存', error: '保存失败' }[saveStatus] || '自动保存开启' }}</span>
          <el-button @click="visible = false">取消</el-button>
          <el-button type="primary" :loading="busy" @click="save">保存</el-button>
        </div>
      </div>
    </template>
  </el-dialog>

  <Teleport to="body">
  <!-- 添加阶段弹窗：与编辑角色主面板保持一致的两栏布局，强制层级命名 -->
  <div class="re-modal-mask" v-if="stageForm.open" @click="stageForm.open=false">
    <div class="re-modal-box re-modal-wide" @click.stop>
      <h3>添加阶段</h3>
      <p class="re-modal-sub">为「{{ form.name || '当前角色' }}」新增一个阶段卡片。阶段角色卡与底层角色卡保持层级关系，阶段名强制为「{{ form.name || '角色' }}-后缀」。</p>
      <div class="re-modal-body">
        <!-- 左侧：参考图预览 -->
        <div class="re-modal-left">
          <div class="re-cover-wrap re-stage-cover-wrap">
            <img :src="stageForm.coverUrl || PLACEHOLDER" class="re-cover" @error="imgError" />
            <div class="re-cover-actions">
              <label class="re-cover-btn" title="上传阶段参考图">
                <el-icon><Upload /></el-icon>
                <input type="file" accept="image/*" style="display:none" @change="onStageFile" />
              </label>
            </div>
          </div>
        </div>
        <!-- 右侧：表单 -->
        <div class="re-modal-right">
          <div class="re-modal-field">
            <label class="re-modal-lbl">阶段名称 <span class="re-modal-hint">（层级命名，必填）</span></label>
            <div class="re-stage-name-row">
              <span class="re-stage-name-prefix">{{ form.name || '角色' }}-</span>
              <input class="re-modal-input re-stage-name-input" v-model="stageForm.stage_name" list="stage-name-suggestions" placeholder="如：青年 / 红衣造型" />
            </div>
            <datalist id="stage-name-suggestions">
              <option v-for="o in ASSET_STAGE_OPTIONS" :key="o.value" :value="o.label">{{ o.label }}</option>
            </datalist>
          </div>
          <div class="re-modal-field">
            <label class="re-modal-lbl">年龄</label>
            <input class="re-modal-input" type="number" v-model="stageForm.age" min="0" max="120" />
          </div>
        </div>
      </div>
      <div class="re-modal-acts">
        <button class="re-btn ghost" @click="stageForm.open=false">取消</button>
        <button class="re-btn" :disabled="busy" @click="submitStage">保存</button>
      </div>
    </div>
  </div>

  <!-- 添加姿势弹窗 -->
  <div class="re-modal-mask" v-if="poseForm.open" @click="poseForm.open=false">
    <div class="re-modal-box" @click.stop>
      <h3>添加姿势</h3>
      <label class="re-modal-lbl">姿势名称</label>
      <input class="re-modal-input" v-model="poseForm.pose_name" list="pose-name-suggestions" placeholder="如：正面、侧面、微笑" />
      <datalist id="pose-name-suggestions">
        <option v-for="o in ASSET_POSE_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
      </datalist>
      <label class="re-modal-lbl">参考照</label>
      <input type="file" accept="image/*" @change="onPoseFile" />
      <div class="re-modal-acts">
        <button class="re-btn ghost" @click="poseForm.open=false">取消</button>
        <button class="re-btn" :disabled="busy" @click="submitPose">保存</button>
      </div>
    </div>
  </div>

  <!-- 图片预览 -->
  <div class="re-modal-mask" v-if="previewUrl" @click="previewUrl=''">
    <div class="re-preview-box"><img :src="previewUrl" @click.stop /></div>
  </div>
</Teleport>
</template>

<style scoped>
/* ===== 整体布局：75% 上 / 25% 下 ===== */
.role-editor-body { display: flex; flex-direction: column; height: 78vh; min-height: 520px; max-height: 860px; }
.re-top { flex: 3; display: flex; gap: 20px; min-height: 0; overflow: hidden; }
.re-bottom { flex: 1; min-height: 0; border: 1px solid var(--line); background: var(--bg-2); border-radius: 12px; margin-top: 12px; padding: 10px 12px 6px; display: flex; flex-direction: column; }

/* ===== 左侧封面 ===== */
.re-left { flex: 0 0 260px; display: flex; flex-direction: column; }
.re-cover-wrap { position: relative; width: 100%; flex: 1; min-height: 0; background: var(--bg-2); border-radius: 12px; overflow: hidden; border: 1px solid var(--line); }
.re-cover { width: 100%; height: 100%; object-fit: contain; background: var(--bg-2); }
.re-cover-actions { position: absolute; top: 10px; right: 10px; display: flex; gap: 8px; opacity: 0; transition: .15s; }
.re-cover-wrap:hover .re-cover-actions { opacity: 1; }
.re-cover-btn { width: 32px; height: 32px; border-radius: 8px; background: rgba(0,0,0,.55); color: #fff; border: 0; display: flex; align-items: center; justify-content: center; cursor: pointer; backdrop-filter: blur(4px); }
.re-cover-btn:hover { background: rgba(138,92,255,.9); }

/* ===== 右侧内容区（滚动） ===== */
.re-right { flex: 1; display: flex; flex-direction: column; min-width: 0; overflow-y: auto; padding-right: 4px; }
.re-base { flex-shrink: 0; }
.re-row { margin-bottom: 12px; }
.re-row-inline { display: flex; align-items: flex-end; gap: 12px; }
.re-field { display: flex; flex-direction: column; }
.re-label { display: block; font-size: 12px; color: var(--sub); margin-bottom: 6px; }
.re-role-badge { font-size: 12px; padding: 2px 10px; border-radius: 12px; font-weight: 700; margin-bottom: 6px; }
.re-role-badge.protagonist { background: rgba(255,77,141,.18); color: #ff4d8d; }
.re-role-badge.supporting { background: rgba(77,159,255,.18); color: #4d9fff; }
.re-role-badge.antagonist { background: rgba(255,176,32,.18); color: #ffb020; }

/* ===== 音色试听 ===== */
.re-voice-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.re-voice-actions { display: flex; align-items: center; gap: 8px; }
.re-btn-text { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--sub); background: transparent; border: 0; cursor: pointer; padding: 2px 4px; border-radius: 4px; }
.re-btn-text:hover { color: var(--pur); background: rgba(138,92,255,.1); }
.re-btn-text-active { color: #ff7a7a; }
.re-btn-text-active:hover { color: #ff4d4d; background: rgba(255,80,80,.1); }
.re-btn-text-danger:hover { color: #ff4d4d; background: rgba(255,80,80,.1); }
.re-voice-sample { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.re-voice-sample-label { font-size: 12px; color: var(--sub); white-space: nowrap; }
.re-voice-sample-input { flex: 1; background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 5px 10px; color: var(--text); font-size: 13px; }
.re-voice-sample-input:focus { outline: none; border-color: var(--pur); }
.re-voice-hint { font-size: 11px; color: var(--sub); white-space: nowrap; }

/* ===== 阶段详情（默认折叠，选中展开） ===== */
.re-stage-detail { flex: 1; min-height: 0; display: flex; flex-direction: column; margin-top: 4px; border-top: 1px dashed var(--line); padding-top: 12px; }
.re-stage-detail-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.re-stage-detail-title { font-size: 14px; font-weight: 700; }
.re-stage-detail-age { margin-left: 8px; font-size: 12px; color: var(--sub); background: var(--bg-2); padding: 1px 8px; border-radius: 10px; }
.re-stage-detail-actions { display: flex; gap: 6px; }

/* ===== 真人资产 512 维身份特征向量（§5.1 / §5.3） ===== */
.re-facevec { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; padding: 6px 8px; border-radius: 8px; background: var(--bg-2); border: 1px solid var(--line); }
.re-facevec-badge { display: inline-flex; align-items: center; gap: 3px; font-size: 12px; font-weight: 700; padding: 2px 9px; border-radius: 10px; }
.re-facevec-badge.ok { background: rgba(34,197,94,.18); color: #22c55e; }
.re-facevec-badge.missing { background: rgba(255,176,32,.18); color: #ffb020; }
.re-facevec-count { font-size: 12px; color: var(--sub); padding: 2px 8px; border-radius: 10px; background: var(--bg); }
.re-facevec-count.warn { color: #ffb020; }
.re-facevec-permission { display: inline-flex; align-items: center; gap: 3px; font-size: 12px; padding: 2px 9px; border-radius: 10px; }
.re-facevec-permission.ok { background: rgba(77,159,255,.16); color: #4d9fff; }
.re-facevec-permission.no { background: rgba(255,80,80,.14); color: #ff7a7a; }
.re-facevec-extract { margin-left: auto; }
.re-stage-mini-vec { position: absolute; top: 4px; right: 4px; width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--bg-2); }
.re-stage-mini-vec.ok { background: #22c55e; }
.re-stage-mini-vec.missing { background: #ffb020; }
.re-btn-icon { width: 28px; height: 28px; border-radius: 6px; border: 1px solid var(--line); background: var(--bg-2); color: var(--text); cursor: pointer; display: flex; align-items: center; justify-content: center; }
.re-btn-icon:hover { border-color: var(--pur); color: var(--pur); }
.re-btn-icon-danger:hover { border-color: #ff4d4d; color: #ff4d4d; }
.re-stage-detail-body { flex: 1; display: flex; gap: 14px; min-height: 0; }
.re-stage-img-wrap { flex: 0 0 180px; align-self: flex-start; aspect-ratio: 3 / 4; position: relative; border-radius: 10px; overflow: hidden; background: var(--bg-2); border: 1px solid var(--line); cursor: zoom-in; }
.re-stage-img-wrap img { display: block; width: 100%; height: 100%; object-fit: contain; background: var(--bg-2); transition: transform .2s; }
.re-stage-img-wrap:hover img { transform: scale(1.04); }
.re-stage-img-hint { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; gap: 4px; background: rgba(0,0,0,.45); color: #fff; font-size: 12px; opacity: 0; transition: .15s; }
.re-stage-img-wrap:hover .re-stage-img-hint { opacity: 1; }
.re-stage-poses { flex: 1; min-width: 0; overflow-y: auto; }
.re-poses-empty { color: var(--sub); font-size: 12px; padding: 20px; text-align: center; }
.re-pose-list { display: flex; flex-wrap: wrap; gap: 10px; }
.re-pose-chip { position: relative; width: 72px; }
.re-pose-chip img { width: 72px; height: 72px; object-fit: cover; border-radius: 8px; border: 1px solid var(--line); cursor: zoom-in; }
.re-pose-label { position: absolute; bottom: 0; left: 0; right: 0; font-size: 10px; text-align: center; background: rgba(0,0,0,.6); color: #fff; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; }
.re-pose-del { position: absolute; top: -6px; right: -6px; width: 18px; height: 18px; border-radius: 50%; border: 0; background: rgba(255,80,80,.9); color: #fff; cursor: pointer; font-size: 11px; display: flex; align-items: center; justify-content: center; }

.re-stage-empty { flex: 1; display: flex; align-items: center; justify-content: center; min-height: 0; }
.re-stage-empty-inner { text-align: center; color: var(--sub); }
.re-stage-empty-inner p { margin: 8px 0 0; font-size: 13px; }

/* ===== 底部阶段菜单条（类似片段导航） ===== */
.re-stage-strip { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 4px; align-items: center; flex: 1; }
.re-stage-mini { flex: 0 0 72px; display: flex; flex-direction: column; align-items: center; gap: 5px; cursor: pointer; padding: 6px 4px; border-radius: 12px; border: 2px solid transparent; transition: .15s; }
.re-stage-mini:hover { background: rgba(255,255,255,.04); transform: translateY(-2px); }
.re-stage-mini.active { background: rgba(138,92,255,.14); border-color: var(--pur); box-shadow: 0 4px 14px rgba(138,92,255,.25); }
.re-stage-mini-img { position: relative; width: 60px; height: 60px; border-radius: 10px; overflow: hidden; background: var(--bg); border: 1px solid var(--line); box-shadow: 0 2px 6px rgba(0,0,0,.25); }
.re-stage-mini-img img { display: block; width: 100%; height: 100%; object-fit: cover; transition: transform .2s; }
.re-stage-mini:hover .re-stage-mini-img img { transform: scale(1.06); }
.re-stage-mini-img::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 42%; background: linear-gradient(to top, rgba(0,0,0,.55), rgba(0,0,0,0)); pointer-events: none; }
.re-stage-mini-name { width: 100%; font-size: 11px; color: var(--text); text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.re-stage-mini-age { position: absolute; top: 3px; left: 3px; font-size: 10px; line-height: 1; padding: 2px 5px; border-radius: 8px; background: rgba(0,0,0,.55); color: #fff; }
.re-stage-mini-add { border: 1px dashed var(--line); background: transparent; color: var(--sub); justify-content: center; }
.re-stage-mini-add:hover { border-color: var(--pur); color: var(--pur); background: rgba(138,92,255,.08); transform: translateY(-2px); }
.re-stage-mini-add .add-icon { font-size: 22px; line-height: 1; }
.re-stage-mini-add .add-label { font-size: 10px; line-height: 1.2; text-align: center; }

/* ===== 弹窗 ===== */
.re-modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.7); display: flex; align-items: center; justify-content: center; z-index: 3000; }
.re-modal-box { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 20px; width: 360px; max-height: 85vh; overflow-y: auto; }
.re-modal-box h3 { margin: 0 0 8px; font-size: 15px; }
.re-modal-sub { font-size: 12px; color: var(--sub); margin: 0 0 14px; line-height: 1.5; }
.re-modal-lbl { display: block; font-size: 12px; color: var(--sub); margin: 10px 0 4px; }
.re-modal-input { width: 100%; box-sizing: border-box; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line); background: var(--bg); color: var(--text); }
.re-modal-input:focus { outline: none; border-color: var(--pur); }
.re-modal-acts { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.re-btn { padding: 8px 16px; border-radius: 8px; border: 1px solid var(--pur); background: var(--pur); color: #fff; cursor: pointer; font-size: 13px; }
.re-btn.ghost { background: transparent; color: var(--sub); border-color: var(--line); }
.re-btn.ghost:hover { border-color: var(--pink); color: var(--pink); }
.re-btn:disabled { opacity: .6; cursor: not-allowed; }
.re-preview-box { display: flex; align-items: center; justify-content: center; max-width: 95vw; max-height: 95vh; }
.re-preview-box img { display: block; width: auto; height: auto; max-width: 95vw; max-height: 95vh; object-fit: contain; border-radius: 12px; }

/* ===== 添加阶段弹窗：与角色编辑主面板一致的两栏布局 ===== */
.re-modal-wide { width: 720px; max-width: 92vw; }
.re-modal-body { display: flex; gap: 18px; margin-top: 12px; }
.re-modal-left { flex: 0 0 220px; }
.re-modal-right { flex: 1; min-width: 0; }
.re-stage-cover-wrap { flex: none; height: auto; max-height: 320px; aspect-ratio: 3 / 4; }
.re-modal-field { margin-bottom: 14px; }
.re-modal-hint { font-weight: normal; color: var(--sub); }
.re-stage-name-row { display: flex; align-items: center; }
.re-stage-name-prefix { flex-shrink: 0; font-size: 13px; color: var(--text); background: var(--bg-2); padding: 7px 10px; border-radius: 8px 0 0 8px; border: 1px solid var(--line); border-right: 0; white-space: nowrap; }
.re-stage-name-input { flex: 1; width: auto; min-width: 0; border-radius: 0 8px 8px 0; }

/* ===== 底部操作栏 ===== */
.re-footer { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.re-footer-right { display: flex; gap: 10px; align-items: center; }
.re-autosave { font-size: 12px; color: var(--sub); }
.re-autosave.saved { color: var(--green); }
.re-autosave.error { color: #ff7a7a; }
</style>

<style>
.role-editor-dialog .el-dialog__body { padding-top: 6px; }
</style>
