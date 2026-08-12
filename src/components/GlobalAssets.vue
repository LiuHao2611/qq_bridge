<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listAssets, createAsset, updateAsset, deleteAsset, uploadGlobalAsset, importScene, importProp, listProjects } from '../api'
import RoleEditorModal from './RoleEditorModal.vue'
import { splitVoice } from '../assetMeta'
import { pruneRefs } from '../composables/useProject'

// ---------- Tab ----------
const TAB_MAP = {
  characters: 'role',
  scenes: 'scene',
  props: 'prop',
  voices: 'voice',
}
const tabs = [
  { key: 'characters', label: '角色' },
  { key: 'scenes', label: '场景' },
  { key: 'props', label: '道具' },
  { key: 'voices', label: '音色' },
]
const tab = ref('characters')
const assets = reactive({ characters: [], scenes: [], props: [], voices: [] })
const loading = ref(false)

const adaptAsset = (a) => {
  // 设定与音色共生于 description，剥离音色块用于「类型」展示，并暂存 voice
  const { desc, voice } = splitVoice(a.description || '')
  return {
    id: a.asset_id,
    name: a.name,
    type: desc || a.role_type || a.type,
    role: a.type === 'role' ? (a.role_type || '主角') : '',
    voice,
    stage: '已定稿',
    from: '全局资产库',
    cover_url: a.cover_url || '',
    raw: a,
  }
}

const load = async () => {
  loading.value = true
  try {
    // 全集库只展示全局资产（is_global=1）；本集资产由项目「素材库」管理，互不影响
    const all = (await listAssets()).filter((a) => a.is_global !== 0)
    assets.characters = all.filter((a) => a.type === 'role').map(adaptAsset)
    assets.scenes = all.filter((a) => a.type === 'scene').map(adaptAsset)
    assets.props = all.filter((a) => a.type === 'prop').map(adaptAsset)
    assets.voices = all.filter((a) => a.type === 'voice').map(adaptAsset)
  } catch (e) {
    ElMessage.warning('后端资产库加载失败，显示为空')
    console.warn(e)
  }
  loading.value = false
}

onMounted(load)

const keyword = ref('')

const list = computed(() => {
  const arr = assets[tab.value] || []
  const k = keyword.value.trim().toLowerCase()
  if (!k) return arr
  return arr.filter((a) => a.name.toLowerCase().includes(k) || a.type.toLowerCase().includes(k))
})

const total = computed(() => (assets[tab.value] || []).length)

// ---------- 删除 ----------
const remove = async (item) => {
  try {
    await deleteAsset(item.id)
    // F1：全局资产删除后级联清理所有项目中指向它的悬空 @引用（无项目上下文 → 后端全局清理）
    await pruneRefs(null, { assetId: item.id })
    const arr = assets[tab.value]
    const i = arr.findIndex((a) => a.id === item.id)
    if (i > -1) arr.splice(i, 1)
    ElMessage.success(`已删除资产「${item.name}」`)
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.message || e))
  }
}

// ---------- 编辑 ----------
const editVisible = ref(false)
const editing = reactive({ id: '', name: '', type: '', from: '' })
const openEdit = (item) => {
  editing.id = item.id
  editing.name = item.name
  editing.type = item.type
  editing.from = item.from
  editVisible.value = true
}
const saveEdit = async () => {
  try {
    const arr = assets[tab.value]
    const t = arr.find((a) => a.id === editing.id)
    if (!t) return
    const backendType = TAB_MAP[tab.value]
    await updateAsset(editing.id, { name: editing.name.trim(), description: editing.type.trim(), type: backendType })
    t.name = editing.name.trim() || t.name
    t.type = editing.type.trim() || t.type
    editVisible.value = false
    ElMessage.success('资产信息已更新')
  } catch (e) {
    ElMessage.error('更新失败: ' + (e.message || e))
  }
}

// ---- 角色卡：小云雀式「编辑角色」弹窗（含角色音色） ----
const roleEditorVisible = ref(false)
const editingRole = ref(null)
const RELATION_TO_ROLE_TYPE = { '主角': 'protagonist', '配角': 'supporting', '反派': 'antagonist' }
const openRoleEditor = (item) => {
  // 把全局资产适配成 RoleEditorModal 需要的 role 形状（设定/音色已从 description 拆出）
  const { desc, voice } = splitVoice(item.raw?.description || '')
  editingRole.value = {
    id: item.id,
    name: item.name,
    roleType: RELATION_TO_ROLE_TYPE[item.role] || item.raw?.role_type || 'protagonist',
    prompt: desc,
    voice: voice || item.voice || '',
    url: item.cover_url || '',
  }
  roleEditorVisible.value = true
}
const onRoleSaved = async () => {
  // 重新拉取并重新适配，保证设定/音色与缩略图一致
  try { await load() } catch (e) { /* 忽略，弹窗已提示成功 */ }
}

// ---------- 新建 ----------
const newVisible = ref(false)
const creating = reactive({ name: '', type: '', role: '' })
const openNew = () => {
  creating.name = ''
  creating.type = ''
  creating.role = ''
  newVisible.value = true
}
const saveNew = async () => {
  const name = creating.name.trim()
  if (!name) { ElMessage.warning('请输入资产名称'); return }
  try {
    const backendType = TAB_MAP[tab.value]
    const body = { name, type: backendType, description: creating.type.trim() || name }
    if (backendType === 'role') body.role_type = creating.role.trim() || '主角'
    const a = await createAsset(body)
    assets[tab.value].push(adaptAsset(a))
    newVisible.value = false
    ElMessage.success('新建资产成功')
  } catch (e) {
    ElMessage.error('新建失败: ' + (e.message || e))
  }
}

// ---------- 批量导入 / 导出 ----------
// 真实文件上传：收图 → 后端落盘 + 缩略图 + 特征 → 全局唯一 ID（is_global=1）
const onUpload = async (ev) => {
  const files = Array.from(ev && ev.target && ev.target.files ? ev.target.files : [])
  if (!files.length) return
  const backendType = TAB_MAP[tab.value]
  let okN = 0
  for (const f of files) {
    try {
      const r = await uploadGlobalAsset(f, { name: f.name, type: backendType })
      if (r && r.asset) assets[tab.value].unshift(adaptAsset(r.asset))
      okN++
    } catch (e) {
      ElMessage.error('上传失败 ' + (f.name || '') + '：' + (e.message || e))
    }
  }
  if (okN) ElMessage.success(`已导入 ${okN} 个资产到全局库`)
  if (ev && ev.target) ev.target.value = ''
}
const onExport = () => ElMessage.success('已导出当前资产清单（演示）')

// ---------- 导入到项目（API-03：全局资产库 → 项目） ----------
const importVisible = ref(false)
const importing = reactive({ id: '', name: '', kind: '' })  // kind: scene | prop
const projects = ref([])
const targetProject = ref('')
const importingLoading = ref(false)
const openImport = async (item) => {
  importing.id = item.id
  importing.name = item.name
  importing.kind = tab.value === 'props' ? 'prop' : 'scene'
  targetProject.value = ''
  importVisible.value = true
  if (!projects.value.length) {
    try { projects.value = await listProjects() } catch (e) { projects.value = [] }
  }
}
const doImport = async () => {
  if (!targetProject.value) { ElMessage.warning('请选择目标项目'); return }
  importingLoading.value = true
  try {
    const fn = importing.kind === 'prop' ? importProp : importScene
    await fn(importing.id, targetProject.value)
    ElMessage.success(`已将「${importing.name}」导入到项目`)
    importVisible.value = false
  } catch (e) {
    ElMessage.error('导入失败：' + (e.message || e))
  } finally {
    importingLoading.value = false
  }
}

// 渐变占位缩略图（按 id 取色，保持视觉稳定）
const grads = [
  'linear-gradient(135deg,#ff4d8d,#8a5cff)',
  'linear-gradient(135deg,#4d9fff,#5c7bff)',
  'linear-gradient(135deg,#3ddc84,#2bbf6a)',
  'linear-gradient(135deg,#ffb020,#ff7a45)',
  'linear-gradient(135deg,#8a5cff,#4d9fff)',
  'linear-gradient(135deg,#ff7ac0,#b79bff)',
]
const gradOf = (id) => {
  let h = 0
  for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return grads[h % grads.length]
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>🌐 全局资产库</h1>
        <p class="muted">跨项目共享的角色 / 场景 / 道具，可在任意短剧中复用</p>
      </div>
      <div style="display:flex;gap:10px;align-items:center">
        <div class="tabs">
          <div v-for="t in tabs" :key="t.key" class="tab" :class="{ active: tab === t.key }" @click="tab = t.key">
            {{ t.label }}
          </div>
        </div>
      </div>
    </div>

    <!-- 工具条 -->
    <div class="toolbar">
      <el-input v-model="keyword" placeholder="搜索资产名称 / 类型…" clearable style="width:260px" />
      <label class="upload-btn">批量导入<input type="file" accept="image/*" multiple style="display:none" @change="onUpload" /></label>
      <el-button @click="onExport">导出</el-button>
      <el-button type="primary" @click="openNew">+ 新建资产</el-button>
      <span class="muted" style="margin-left:auto">本类共 {{ total }} 项 · 显示 {{ list.length }} 项</span>
    </div>

    <!-- 资产网格 -->
    <div class="asset-grid">
      <div v-for="a in list" :key="a.id" class="asset-card">
        <div class="thumb" :style="{ background: a.cover_url ? '#0f1117' : gradOf(a.id) }">
          <img v-if="a.cover_url" :src="a.cover_url" class="thumb-img" />
          <span class="src-badge">{{ a.from }}</span>
          <div class="acts">
            <button v-if="tab==='characters'" class="btn sm ghost" @click="openRoleEditor(a)">编辑</button>
            <button v-else class="btn sm ghost" @click="openEdit(a)">编辑</button>
            <button v-if="tab==='scenes' || tab==='props'" class="btn sm ghost" @click="openImport(a)">导入到项目</button>
            <button class="btn sm ghost" @click="remove(a)">删除</button>
          </div>
        </div>
        <div class="info">
          <div class="name">{{ a.name }}</div>
          <div class="type">{{ a.type }}</div>
          <div class="meta">
            <el-tag v-if="a.role" size="small" :type="a.role === '主角' ? 'danger' : 'info'">{{ a.role }}</el-tag>
            <span class="stage" :class="{ done: a.stage === '已定稿' }">{{ a.stage }}</span>
          </div>
        </div>
      </div>
      <div v-if="!list.length" class="empty" style="grid-column:1/-1">
        没有匹配「{{ keyword }}」的资产，换个关键词试试。
      </div>
    </div>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editVisible" title="编辑资产" width="420px">
      <div class="field"><label>名称</label><el-input v-model="editing.name" /></div>
      <div class="field"><label>类型描述</label><el-input v-model="editing.type" /></div>
      <div class="field"><label>来源项目</label><el-input v-model="editing.from" disabled /></div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 角色卡：小云雀式「编辑角色」弹窗（含角色音色） -->
    <RoleEditorModal v-model="roleEditorVisible" :role="editingRole" @saved="onRoleSaved" />

    <!-- 新建弹窗 -->
    <el-dialog v-model="newVisible" title="新建资产" width="420px">
      <div class="field"><label>名称</label><el-input v-model="creating.name" /></div>
      <div class="field"><label>类型描述</label><el-input v-model="creating.type" /></div>
      <div v-if="tab === 'characters'" class="field"><label>角色定位</label><el-input v-model="creating.role" placeholder="主角 / 配角" /></div>
      <template #footer>
        <el-button @click="newVisible = false">取消</el-button>
        <el-button type="primary" @click="saveNew">保存</el-button>
      </template>
    </el-dialog>

    <!-- 导入到项目弹窗（API-03） -->
    <el-dialog v-model="importVisible" title="导入到项目" width="420px">
      <p style="margin:0 0 14px;color:var(--sub)">将「{{ importing.name }}」复制到目标项目，作为可复用资产。</p>
      <div class="field"><label>目标项目</label>
        <el-select v-model="targetProject" placeholder="请选择项目" filterable style="width:100%">
          <el-option v-for="pr in projects" :key="pr.id || pr.project_id" :label="pr.title || pr.name" :value="pr.id || pr.project_id" />
        </el-select>
      </div>
      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button type="primary" :loading="importingLoading" @click="doImport">确认导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.upload-btn {
  display: inline-flex; align-items: center; justify-content: center; cursor: pointer;
  background: var(--card); color: var(--sub); border: 1px solid var(--line);
  border-radius: 8px; padding: 8px 14px; font-size: 13px; font-weight: 600; transition: .15s;
}
.upload-btn:hover { color: var(--txt); border-color: var(--pur); background: var(--card-2); }
.asset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 16px; }
.asset-card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.asset-card .thumb {
  position: relative; aspect-ratio: 1; display: flex; align-items: center; justify-content: center;
  overflow: hidden;
}
.asset-card .thumb-img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.src-badge {
  position: absolute; top: 8px; left: 8px; font-size: 11px; padding: 2px 8px; border-radius: 7px;
  background: rgba(0,0,0,.45); color: #fff; backdrop-filter: blur(2px);
}
.asset-card .acts {
  position: absolute; inset: 0; display: none; align-items: center; justify-content: center; gap: 8px;
  background: rgba(8,9,14,.55);
}
.asset-card:hover .acts { display: flex; }
.asset-card .info { padding: 10px; }
.asset-card .name { font-weight: 700; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.asset-card .type { font-size: 12px; color: var(--sub); margin-top: 4px; }
.asset-card .meta { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.stage { font-size: 11px; color: var(--sub); }
.stage.done { color: var(--green); }
.empty { text-align: center; color: var(--sub); padding: 40px 0; }
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12px; color: var(--sub); margin-bottom: 6px; }
</style>
