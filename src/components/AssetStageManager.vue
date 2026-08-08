<script setup>
import { ref, reactive, onMounted, watch, computed } from 'vue'
import {
  listStagesWithPoses, addStage, delStage, addPose, delPose, uploadImage,
} from '../api'
import { toast } from '../store'
import { ASSET_STAGE_OPTIONS, ASSET_POSE_OPTIONS } from '../mock'

const props = defineProps({
  // 资产的 asset_id；传 null/空时不加载
  assetId: { type: [String, Number], default: '' },
  // 初始阶段数据（父组件已预加载时传入可减少一次请求）；为空则用 API 拉取
  initialStages: { type: Array, default: null },
  // 资产类型：决定文案与是否显示年龄输入
  type: { type: String, default: 'role', validator: (v) => ['role', 'scene', 'prop'].includes(v) },
  // 展示模式：panel（完整网格，含姿势编辑） | strip（左侧卡片内联横条）
  mode: { type: String, default: 'panel', validator: (v) => ['panel', 'strip'].includes(v) },
  // 紧凑模式（panel 模式下使用更小卡片）
  compact: { type: Boolean, default: false },
  // 是否显示「添加阶段」按钮
  showAddButton: { type: Boolean, default: true },
  // 覆盖默认标题
  title: { type: String, default: '' },
})
const emit = defineEmits(['changed', 'select'])

const STAGE_OPTS = ASSET_STAGE_OPTIONS
const POSE_OPTS = ASSET_POSE_OPTIONS
const PLACEHOLDER = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0%25' stop-color='%2320242e'/><stop offset='100%25' stop-color='%233a3f4d'/></linearGradient></defs><rect width='200' height='200' fill='url(%23g)'/><text x='50%25' y='55%25' dominant-baseline='middle' text-anchor='middle' fill='%236b7283' font-size='70'>🖼️</text></svg>"

const suggestionId = 'stage-suggestions-' + Math.random().toString(36).slice(2, 8)

const i18n = computed(() => {
  const map = {
    role: {
      title: '阶段与姿势',
      addStage: '添加阶段',
      empty: '暂无阶段',
      emptyHint: '点「添加阶段」上传参考照',
      stageLabel: '阶段',
      ageLabel: '年龄',
      unit: '岁',
      addPose: '姿势',
      delStageConfirm: '删除该阶段及其姿势？',
      stageNameLabel: '阶段名称',
      noAsset: '未选择角色',
      saveFirst: '请先保存角色主卡片',
      selectRef: '请选择阶段参考照',
    },
    scene: {
      title: '场景阶段与变体',
      addStage: '添加场景阶段',
      empty: '暂无场景阶段',
      emptyHint: '点「添加场景阶段」上传参考照',
      stageLabel: '阶段',
      ageLabel: '序号',
      unit: '',
      addPose: '变体',
      delStageConfirm: '删除该场景阶段及其变体？',
      stageNameLabel: '阶段名称',
      noAsset: '未选择场景',
      saveFirst: '请先保存场景主卡片',
      selectRef: '请选择阶段参考照',
    },
    prop: {
      title: '物品阶段与变体',
      addStage: '添加物品阶段',
      empty: '暂无物品阶段',
      emptyHint: '点「添加物品阶段」上传参考照',
      stageLabel: '阶段',
      ageLabel: '序号',
      unit: '',
      addPose: '变体',
      delStageConfirm: '删除该物品阶段及其变体？',
      stageNameLabel: '阶段名称',
      noAsset: '未选择物品',
      saveFirst: '请先保存物品主卡片',
      selectRef: '请选择阶段参考照',
    },
  }
  return map[props.type] || map.role
})

const stages = ref(Array.isArray(props.initialStages) ? props.initialStages : [])
const loading = ref(false)
const busy = ref(false)
const preview = ref('')

const hasId = computed(() => !!props.assetId)
const showAge = computed(() => props.type === 'role')
const displayTitle = computed(() => props.title || i18n.value.title)

const stageLabel = (v) => (STAGE_OPTS.find((o) => o.value === v) || { label: v }).label
const poseLabel = (v) => (POSE_OPTS.find((o) => o.value === v) || { label: v }).label

const load = async () => {
  if (!hasId.value) return
  loading.value = true
  try {
    const items = await listStagesWithPoses(props.assetId)
    stages.value = Array.isArray(items) ? items : []
  } catch (e) {
    toast('阶段加载失败: ' + e.message)
    stages.value = []
  } finally {
    loading.value = false
  }
}

// 表单状态（阶段名称自由输入，下拉仅作建议）
const stageForm = reactive({ open: false, stage_name: '', age: 25, file: null })
const poseForm = reactive({ open: false, stageId: '', pose_name: '', file: null })

const openStageForm = () => {
  if (!hasId.value) { toast(i18n.value.saveFirst); return }
  stageForm.open = true
  stageForm.stage_name = showAge.value ? 'youth' : ''
  stageForm.age = showAge.value ? 25 : 0
  stageForm.file = null
}
const onStageFile = (ev) => { stageForm.file = ev.target.files?.[0] || null }
const submitStage = async () => {
  if (!stageForm.file) { toast(i18n.value.selectRef); return }
  if (!stageForm.stage_name.trim()) { toast('请输入阶段名称'); return }
  busy.value = true
  try {
    const up = await uploadImage(stageForm.file)
    await addStage(props.assetId, {
      stage_name: stageForm.stage_name.trim(),
      age: showAge.value ? (Number(stageForm.age) || 25) : 0,
      reference_image_url: up.url || '',
    })
    toast('已添加' + i18n.value.stageLabel)
    stageForm.open = false
    await load()
    emit('changed')
  } catch (e) {
    toast('添加失败: ' + e.message)
  } finally {
    busy.value = false
  }
}
const removeStage = async (st) => {
  if (!confirm(i18n.value.delStageConfirm)) return
  try {
    await delStage(props.assetId, st.stage_id)
    await load()
    emit('changed')
  } catch (e) {
    toast('删除失败: ' + e.message)
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
  if (!poseForm.file) { toast('请选择' + i18n.value.addPose + '参考照'); return }
  if (!poseForm.pose_name.trim()) { toast('请输入' + i18n.value.addPose + '名称'); return }
  busy.value = true
  try {
    const up = await uploadImage(poseForm.file)
    await addPose(poseForm.stageId, { pose_name: poseForm.pose_name.trim(), reference_image_url: up.url || '' })
    toast('已添加' + i18n.value.addPose)
    poseForm.open = false
    await load()
    emit('changed')
  } catch (e) {
    toast('添加失败: ' + e.message)
  } finally {
    busy.value = false
  }
}
const removePose = async (st, p) => {
  try {
    await delPose(st.stage_id, p.pose_id)
    await load()
    emit('changed')
  } catch (e) {
    toast('删除失败: ' + e.message)
  }
}

onMounted(() => { if (!Array.isArray(props.initialStages)) load() })
watch(() => props.assetId, (v) => { if (v && !Array.isArray(props.initialStages)) load() })
watch(() => props.initialStages, (v) => { if (Array.isArray(v)) stages.value = v })
</script>

<template>
  <div class="asm-root" :class="[props.type, props.mode, { compact }]" v-if="mode === 'panel'">
    <!-- ===== panel 模式：完整网格，含姿势编辑（用于 RoleEditorModal 底部） ===== -->
    <div class="rp-head">
      <div class="rp-title">{{ displayTitle }}</div>
      <button v-if="showAddButton" class="btn sm" @click="openStageForm">＋ {{ i18n.addStage }}</button>
    </div>

    <div v-if="!hasId" class="empty">{{ i18n.noAsset }}</div>
    <div v-else-if="loading" class="muted" style="padding:12px">加载阶段中…</div>
    <div v-else-if="!stages.length" class="empty">
      {{ i18n.empty }}，{{ i18n.emptyHint }}。
    </div>
    <div v-else class="stage-list">
      <div class="stage-card" v-for="st in stages" :key="st.stage_id">
        <div class="stage-head">
          <span class="stage-name">{{ stageLabel(st.stage_name) }}<template v-if="showAge && st.age">（{{ st.age }}{{ i18n.unit }}）</template></span>
          <button class="btn sm ghost" @click="removeStage(st)">删除{{ i18n.stageLabel }}</button>
        </div>
        <div class="stage-img"><img :src="st.reference_image_url || PLACEHOLDER" @click="preview = st.reference_image_url" /></div>
        <div class="pose-row">
          <div class="pose-chip" v-for="p in (st.poses||[])" :key="p.pose_id">
            <img :src="p.reference_image_url || PLACEHOLDER" @click="preview = p.reference_image_url" />
            <span class="pose-label">{{ poseLabel(p.pose_name) }}</span>
            <button class="pose-del" @click="removePose(st, p)">×</button>
          </div>
          <button class="pose-add" @click="openPoseForm(st)">＋ {{ i18n.addPose }}</button>
        </div>
      </div>
    </div>
  </div>

  <div class="asm-root strip" :class="[props.type]" v-else>
    <!-- ===== strip 模式：左侧卡片内联横条 ===== -->
    <div v-if="hasId && !loading" class="stage-strip" @click.stop>
      <div
        class="stage-mini"
        :class="{ 'stage-mini-add': false }"
        v-for="st in stages"
        :key="st.stage_id"
        @click="emit('select', st)"
      >
        <div class="stage-mini-img"><img :src="st.reference_image_url || PLACEHOLDER" /></div>
        <div class="stage-mini-name">{{ stageLabel(st.stage_name) }}</div>
        <div v-if="showAge && st.age" class="stage-mini-age">{{ st.age }}{{ i18n.unit }}</div>
      </div>
      <button v-if="showAddButton" class="stage-mini stage-mini-add" @click.stop="openStageForm">
        <span class="add-icon">＋</span>
      </button>
    </div>
    <div v-else-if="loading" class="strip-loading">加载阶段…</div>
  </div>

  <!-- 阶段表单 -->
  <div class="modal-mask" v-if="stageForm.open" @click="stageForm.open=false">
    <div class="modal-box" @click.stop>
      <h3>{{ i18n.addStage }}</h3>
      <label class="lbl">{{ i18n.stageNameLabel }}</label>
      <input class="input" v-model="stageForm.stage_name" :list="suggestionId" :placeholder="'如：' + (showAge ? '童年期、少年期' : '白天、夜晚、破损')" />
      <datalist :id="suggestionId">
        <option v-for="o in STAGE_OPTS" :key="o.value" :value="o.value">{{ o.label }}</option>
      </datalist>
      <template v-if="showAge">
        <label class="lbl">{{ i18n.ageLabel }}</label>
        <input class="input" type="number" v-model="stageForm.age" min="0" max="120" />
      </template>
      <label class="lbl">参考照</label>
      <input type="file" accept="image/*" @change="onStageFile" />
      <div class="modal-acts">
        <button class="btn sm ghost" @click="stageForm.open=false">取消</button>
        <button class="btn sm" :disabled="busy" @click="submitStage">保存</button>
      </div>
    </div>
  </div>

  <!-- 姿势表单 -->
  <div class="modal-mask" v-if="poseForm.open" @click="poseForm.open=false">
    <div class="modal-box" @click.stop>
      <h3>添加{{ i18n.addPose }}</h3>
      <label class="lbl">{{ i18n.addPose }}名称</label>
      <input class="input" v-model="poseForm.pose_name" list="pose-suggestions" placeholder="如：正面、侧面、自定义…" />
      <datalist id="pose-suggestions">
        <option v-for="o in POSE_OPTS" :key="o.value" :value="o.value">{{ o.label }}</option>
      </datalist>
      <label class="lbl">参考照</label>
      <input type="file" accept="image/*" @change="onPoseFile" />
      <div class="modal-acts">
        <button class="btn sm ghost" @click="poseForm.open=false">取消</button>
        <button class="btn sm" :disabled="busy" @click="submitPose">保存</button>
      </div>
    </div>
  </div>

  <div class="modal-mask" v-if="preview" @click="preview = ''"><div class="modal"><img :src="preview" @click.stop /></div></div>
</template>

<style scoped>
/* ===== 通用 ===== */
.asm-root { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
.rp-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.rp-title { font-size: 15px; font-weight: 700; }
.compact { padding: 12px; border-radius: 10px; }
.compact .rp-title { font-size: 13px; }
.empty { text-align: center; color: var(--sub); padding: 24px 0; }
.muted { color: var(--sub); }

/* ===== panel 网格 ===== */
.stage-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
.compact .stage-list { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.stage-card { background: var(--bg-2); border: 1px solid var(--line); border-radius: 12px; padding: 12px; }
.compact .stage-card { padding: 8px; border-radius: 10px; }
.stage-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; gap: 6px; }
.stage-name { font-weight: 700; font-size: 13px; }
.compact .stage-name { font-size: 12px; }
.stage-img { aspect-ratio: 16/9; background: var(--bg); border-radius: 8px; overflow: hidden; }
.stage-img img { width: 100%; height: 100%; object-fit: cover; cursor: zoom-in; }
.pose-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; align-items: center; }
.compact .pose-row { gap: 6px; margin-top: 8px; }
.pose-chip { position: relative; width: 64px; }
.compact .pose-chip { width: 48px; }
.pose-chip img { width: 64px; height: 64px; object-fit: cover; border-radius: 8px; cursor: zoom-in; border: 1px solid var(--line); }
.compact .pose-chip img { width: 48px; height: 48px; }
.pose-label { position: absolute; bottom: 0; left: 0; right: 0; font-size: 10px; text-align: center; background: rgba(0,0,0,.6); color: #fff; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; }
.pose-del { position: absolute; top: -6px; right: -6px; width: 18px; height: 18px; border-radius: 50%; border: 0; background: rgba(255,80,80,.9); color: #fff; cursor: pointer; font-size: 11px; line-height: 1; }
.pose-add { width: 64px; height: 64px; border-radius: 8px; border: 1px dashed var(--line); background: transparent; color: var(--sub); cursor: pointer; font-size: 12px; }
.compact .pose-add { width: 48px; height: 48px; font-size: 11px; }
.pose-add:hover { border-color: var(--pur); color: var(--pur); }

/* ===== strip 内联横条 ===== */
.asm-root.strip { background: transparent; border: 0; border-radius: 0; padding: 0; }
.stage-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.stage-mini { flex: 0 0 64px; display: flex; flex-direction: column; align-items: center; gap: 4px; cursor: pointer; }
.stage-mini-img { width: 64px; height: 64px; border-radius: 8px; overflow: hidden; background: var(--bg); border: 1px solid var(--line); }
.stage-mini-img img { width: 100%; height: 100%; object-fit: cover; }
.stage-mini-name { width: 100%; font-size: 11px; color: var(--text); text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stage-mini-age { width: 100%; font-size: 10px; color: var(--sub); text-align: center; }
.stage-mini-add { width: 64px; height: 64px; border-radius: 8px; border: 1px dashed var(--line); background: transparent; color: var(--sub); cursor: pointer; display: flex; align-items: center; justify-content: center; }
.stage-mini-add .add-icon { font-size: 20px; line-height: 1; }
.stage-mini-add:hover { border-color: var(--pur); color: var(--pur); }
.strip-loading { font-size: 11px; color: var(--sub); }

/* ===== 弹窗 ===== */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.7); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { max-width: 90vw; max-height: 90vh; }
.modal img { max-width: 90vw; max-height: 90vh; border-radius: 12px; }
.modal-box { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 20px; width: 320px; max-height: 85vh; overflow-y: auto; }
.modal-box h3 { margin: 0 0 14px; }
.lbl { display: block; font-size: 12px; color: var(--sub); margin: 10px 0 4px; }
.input { width: 100%; box-sizing: border-box; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line); background: var(--bg); color: var(--text); }
.modal-acts { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.btn { padding: 8px 14px; border-radius: 8px; border: 1px solid var(--pur); background: var(--pur); color: #fff; cursor: pointer; font-size: 13px; }
.btn.sm { padding: 6px 10px; font-size: 12px; }
.btn.ghost { background: transparent; color: var(--sub); border-color: var(--line); }
.btn.ghost:hover { border-color: var(--pink); color: var(--pink); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
</style>
