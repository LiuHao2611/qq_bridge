<script setup>
import { ref, reactive, computed, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { updateAsset, uploadImage, listStagesWithPoses } from '../api'
import { toast } from '../store'
import { splitVoice, joinVoice } from '../assetMeta'
import { useAutoSave } from '../composables/useAutoSave'
import { ElMessageBox } from 'element-plus'
import {
  ASSET_STAGE_OPTIONS, ASSET_ROLE_TYPE_OPTIONS,
} from '../mock'
import AssetStageManager from './AssetStageManager.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  role: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'saved', 'preview', 'stagesChanged'])

const onStagesChanged = () => emit('stagesChanged')

const router = useRouter()

const PLACEHOLDER = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0%25' stop-color='%2320242e'/><stop offset='100%25' stop-color='%233a3f4d'/></linearGradient></defs><rect width='200' height='200' fill='url(%23g)'/><text x='50%25' y='55%25' dominant-baseline='middle' text-anchor='middle' fill='%236b7283' font-size='70'>🖼️</text></svg>"

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const busy = ref(false)
const stages = ref([])
const dirty = ref(false)
let suppressDirty = true
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

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

const loadStages = async (role) => {
  if (!role || !role.id) return
  try {
    stages.value = await listStagesWithPoses(role.id)
  } catch (e) {
    console.warn('load stages failed', e)
    stages.value = []
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
    if (props.role) loadRoleIntoForm(props.role)
    nextTick(() => { suppressDirty = false })
    dirty.value = false
  } else {
    dirty.value = false
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
</script>

<template>
  <el-dialog v-model="visible" title="编辑角色" width="860px" top="8vh" class="role-editor-dialog" destroy-on-close :before-close="beforeClose">
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
          <AssetStageManager
            :asset-id="form.id"
            :initial-stages="stages"
            type="role"
            mode="panel"
            title="角色全部年龄阶段（含姿势）"
            @changed="onStagesChanged"
          />
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
          <span class="re-autosave" :class="saveStatus">{{ { saving: '保存中…', saved: '已自动保存', error: '保存失败' }[saveStatus] || '自动保存开启' }}</span>
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

.re-muted { color: var(--sub); font-size: 12px; }

.re-actions-inline { display: flex; gap: 10px; margin-top: auto; }
.re-footer { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.re-footer-right { display: flex; gap: 10px; align-items: center; }
.re-autosave { font-size: 12px; color: var(--sub); }
.re-autosave.saved { color: var(--green); }
.re-autosave.error { color: #ff7a7a; }
</style>

<style>
.role-editor-dialog .el-dialog__body { padding-top: 6px; }
</style>
