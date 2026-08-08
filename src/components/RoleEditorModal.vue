<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { updateAsset, uploadImage, listStagesWithPoses } from '../api'
import { toast } from '../store'
import { splitVoice, joinVoice } from '../assetMeta'
import {
  ASSET_STAGE_OPTIONS, ASSET_POSE_OPTIONS, ASSET_ROLE_TYPE_OPTIONS,
} from '../mock'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  role: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'saved', 'preview'])

const router = useRouter()

const PLACEHOLDER = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0%25' stop-color='%2320242e'/><stop offset='100%25' stop-color='%233a3f4d'/></linearGradient></defs><rect width='200' height='200' fill='url(%23g)'/><text x='50%25' y='55%25' dominant-baseline='middle' text-anchor='middle' fill='%236b7283' font-size='70'>🖼️</text></svg>"

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const busy = ref(false)
const stages = ref([])
const loadingStages = ref(false)

const form = reactive({
  id: '',
  name: '',
  roleType: 'protagonist',
  description: '',
  voice: '',
  coverUrl: '',
})

const resetForm = () => {
  form.id = ''
  form.name = ''
  form.roleType = 'protagonist'
  form.description = ''
  form.voice = ''
  form.coverUrl = ''
  stages.value = []
}

const stageLabel = (name) => ASSET_STAGE_OPTIONS.find((o) => o.value === name)?.label || name
const poseLabel = (name) => ASSET_POSE_OPTIONS.find((o) => o.value === name)?.label || name

const loadStages = async (role) => {
  if (!role || !role.id) return
  loadingStages.value = true
  try {
    stages.value = await listStagesWithPoses(role.id)
  } catch (e) {
    console.warn('load stages failed', e)
    stages.value = []
  } finally {
    loadingStages.value = false
  }
}

watch(() => props.role, (r) => {
  if (r) {
    form.id = r.id
    form.name = r.name || ''
    form.roleType = r.roleType || 'protagonist'
    // 设定与音色共生于 description：优先从完整 raw.description 拆出，否则用 prompt/description 兜底
    const fullDesc = r.raw?.description ?? r.description ?? r.prompt ?? ''
    const parsed = splitVoice(fullDesc)
    form.description = parsed.desc
    form.voice = r.voice ?? parsed.voice ?? ''
    form.coverUrl = r.url || r.raw?.cover_url || PLACEHOLDER
    loadStages(r)
  } else {
    resetForm()
  }
}, { immediate: true })

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

const onAudioUpload = () => {
  // 占位：当前后端无独立角色音频字段
  toast('角色音频/TTS 能力待接入（需新增 voice 资产类型）')
}

const onAIGenerate = () => {
  toast('AI 角色设定生成：将基于参考图与定位扩写（本地模型待接入）')
}

const goCanvas = () => {
  if (!form.id) return
  visible.value = false
  router.push('/canvas?asset_id=' + encodeURIComponent(form.id))
}

const save = async () => {
  if (!form.name.trim()) { toast('角色名称不能为空'); return }
  busy.value = true
  try {
    // 设定 + 音色合并写回 description（后端无独立音色列）
    const fullDesc = joinVoice(form.description, form.voice)
    const body = {
      name: form.name.trim(),
      description: fullDesc,
      role_type: form.roleType,
      cover_url: form.coverUrl,
    }
    const r = await updateAsset(form.id, body)
    emit('saved', { ...props.role, name: form.name.trim(), prompt: form.description, voice: form.voice, roleType: form.roleType, url: form.coverUrl, raw: r })
    visible.value = false
    toast('角色已保存')
  } catch (e) {
    toast('保存失败: ' + e.message)
  } finally {
    busy.value = false
  }
}

const roleTypeLabel = computed(() => ASSET_ROLE_TYPE_OPTIONS.find((o) => o.value === form.roleType)?.label || form.roleType)
</script>

<template>
  <el-dialog v-model="visible" title="编辑角色" width="860px" top="8vh" class="role-editor-dialog" :close-on-click-modal="false" destroy-on-close>
    <div class="role-editor-body" v-loading="busy">
      <!-- 左侧：角色大图 -->
      <div class="re-left">
        <div class="re-cover-wrap">
          <img :src="form.coverUrl || PLACEHOLDER" class="re-cover" />
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

      <!-- 右侧：表单 -->
      <div class="re-right">
        <div class="re-row">
          <label class="re-label">角色名称</label>
          <el-input v-model="form.name" placeholder="输入角色名称" clearable />
        </div>

        <div class="re-row">
          <label class="re-label">角色定位</label>
          <el-select v-model="form.roleType" placeholder="选择角色定位" style="width:160px">
            <el-option v-for="o in ASSET_ROLE_TYPE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <span class="re-role-badge" :class="form.roleType">{{ roleTypeLabel }}</span>
        </div>

        <div class="re-row re-grow">
          <label class="re-label">角色设定</label>
          <el-input v-model="form.description" type="textarea" :rows="6" placeholder="描述角色外貌、性格、语气、背景等，AI生成与分镜引用时作为提示词基础" />
        </div>

        <div class="re-row re-grow">
          <label class="re-label">角色音色</label>
          <el-input v-model="form.voice" type="textarea" :rows="5" placeholder="描述角色配音音色：音色定位（如温柔青年男声）、语速、语调、情绪表达、口头禅等，供 AI 配音/口播参考" />
        </div>

        <div class="re-row">
          <label class="re-label">参考图 / 年龄阶段</label>
          <div v-if="loadingStages" class="re-muted">加载中…</div>
          <div v-else-if="!stages.length" class="re-muted">暂无阶段参考照，可在卡片上点击「阶段 / 姿势」添加。</div>
          <div v-else class="re-stage-list">
            <div v-for="st in stages" :key="st.stage_id" class="re-stage-chip">
              <img :src="st.reference_image_url || PLACEHOLDER" @click="emit('preview', st.reference_image_url || PLACEHOLDER)" />
              <span class="re-stage-name">{{ stageLabel(st.stage_name) }} · {{ st.age }}岁</span>
              <div v-if="st.poses?.length" class="re-pose-row">
                <img v-for="p in st.poses" :key="p.pose_id" :src="p.reference_image_url || PLACEHOLDER" :title="poseLabel(p.pose_name)" @click="emit('preview', p.reference_image_url || PLACEHOLDER)" />
              </div>
            </div>
          </div>
        </div>

        <div class="re-row re-actions-inline">
          <el-button type="primary" plain @click="onAudioUpload">
            <el-icon><Microphone /></el-icon> 上传音频
          </el-button>
          <el-button type="primary" plain @click="onAIGenerate">
            <el-icon><MagicStick /></el-icon> AI 生成
          </el-button>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="re-footer">
        <el-button link @click="goCanvas">
          <el-icon><EditPen /></el-icon> 去画布编辑
        </el-button>
        <div class="re-footer-right">
          <el-button @click="visible = false">取消</el-button>
          <el-button type="primary" :loading="busy" @click="save">保存</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.role-editor-body { display: flex; gap: 22px; min-height: 480px; }
.re-left { flex: 0 0 320px; }
.re-cover-wrap { position: relative; width: 100%; aspect-ratio: 3/4; background: var(--bg-2); border-radius: 12px; overflow: hidden; border: 1px solid var(--line); }
.re-cover { width: 100%; height: 100%; object-fit: cover; }
.re-cover-actions { position: absolute; top: 10px; right: 10px; display: flex; gap: 8px; opacity: 0; transition: .15s; }
.re-cover-wrap:hover .re-cover-actions { opacity: 1; }
.re-cover-btn { width: 32px; height: 32px; border-radius: 8px; background: rgba(0,0,0,.55); color: #fff; border: 0; display: flex; align-items: center; justify-content: center; cursor: pointer; backdrop-filter: blur(4px); }
.re-cover-btn:hover { background: rgba(138,92,255,.9); }

.re-right { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.re-row { margin-bottom: 14px; }
.re-row.re-grow { flex: 1; display: flex; flex-direction: column; }
.re-row.re-grow :deep(.el-textarea) { flex: 1; }
.re-row.re-grow :deep(.el-textarea__inner) { min-height: 160px !important; }
.re-label { display: block; font-size: 12px; color: var(--sub); margin-bottom: 6px; }
.re-role-badge { margin-left: 10px; font-size: 12px; padding: 2px 10px; border-radius: 12px; font-weight: 700; }
.re-role-badge.protagonist { background: rgba(255,77,141,.18); color: #ff4d8d; }
.re-role-badge.supporting { background: rgba(77,159,255,.18); color: #4d9fff; }
.re-role-badge.antagonist { background: rgba(255,176,32,.18); color: #ffb020; }

.re-stage-list { display: flex; gap: 10px; flex-wrap: wrap; }
.re-stage-chip { width: 96px; }
.re-stage-chip > img { width: 96px; height: 96px; object-fit: cover; border-radius: 8px; border: 1px solid var(--line); cursor: zoom-in; }
.re-stage-name { display: block; font-size: 11px; color: var(--sub); margin-top: 4px; }
.re-pose-row { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
.re-pose-row img { width: 28px; height: 28px; object-fit: cover; border-radius: 4px; border: 1px solid var(--line); cursor: zoom-in; }
.re-muted { color: var(--sub); font-size: 12px; }

.re-actions-inline { display: flex; gap: 10px; margin-top: auto; }
.re-footer { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.re-footer-right { display: flex; gap: 10px; }
</style>

<style>
.role-editor-dialog .el-dialog__body { padding-top: 6px; }
</style>
