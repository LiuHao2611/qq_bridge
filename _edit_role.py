from pathlib import Path
p = Path('src/components/RoleEditorModal.vue')
t = p.read_text(encoding='utf-8')

def rep(old, new, count=1):
    global t
    n = t.count(old)
    if n != count:
        raise SystemExit(f'EXPECTED {count} of:\n{old[:80]}\nfound {n}')
    t = t.replace(old, new)

# 1) imports
rep(
"import { ref, reactive, computed, watch, onMounted } from 'vue'\nimport { useRouter } from 'vue-router'\nimport { updateAsset, uploadImage, listStagesWithPoses } from '../api'\nimport { toast } from '../store'\nimport { splitVoice, joinVoice } from '../assetMeta'",
"import { ref, reactive, computed, watch, onMounted } from 'vue'\nimport { useRouter } from 'vue-router'\nimport { updateAsset, uploadImage, listStagesWithPoses } from '../api'\nimport { toast } from '../store'\nimport { splitVoice, joinVoice } from '../assetMeta'\nimport { useAutoSave } from '../composables/useAutoSave'\nimport { ElMessageBox } from 'element-plus'"
)

# 2) 加载表单抽成函数 + dirty 标记
rep(
"const busy = ref(false)\nconst stages = ref([])",
"const busy = ref(false)\nconst stages = ref([])\nconst dirty = ref(false)\nlet suppressDirty = true"
)
rep(
"const loadStages = async (role) => {\n  if (!role || !role.id) return\n  try {\n    stages.value = await listStagesWithPoses(role.id)\n  } catch (e) {\n    console.warn('load stages failed', e)\n    stages.value = []\n  }\n}\n\nwatch(() => props.role, (r) => {\n  if (r) {\n    form.id = r.id\n    form.name = r.name || ''\n    form.roleType = r.roleType || 'protagonist'\n    // 设定与音色共生于 description：优先从完整 raw.description 拆出，否则用 prompt/description 兜底\n    const fullDesc = r.raw?.description ?? r.description ?? r.prompt ?? ''\n    const parsed = splitVoice(fullDesc)\n    form.description = parsed.desc\n    form.voice = r.voice ?? parsed.voice ?? ''\n    form.coverUrl = r.url || r.raw?.cover_url || PLACEHOLDER\n    loadStages(r)\n  } else {\n    resetForm()\n  }\n}, { immediate: true })",
"""const loadStages = async (role) => {
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
watch(form, () => { if (!suppressDirty) dirty.value = true }, { deep: true })"""
)

# 3) save -> persist + save；自动保存用 persist（不关闭弹窗）
rep(
"""const save = async () => {
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
}""",
"""// 真实持久化（不关闭弹窗，供自动保存与手动保存共用）
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
}"""
)

# 4) 弹窗允许点遮罩关闭 + before-close 确认
rep(
'  <el-dialog v-model="visible" title="编辑角色" width="860px" top="8vh" class="role-editor-dialog" :close-on-click-modal="false" destroy-on-close>',
'  <el-dialog v-model="visible" title="编辑角色" width="860px" top="8vh" class="role-editor-dialog" destroy-on-close :before-close="beforeClose">'
)

# 5) 在 footer 增加自动保存状态
rep(
"""        <div class="re-footer-right">
          <el-button @click="visible = false">取消</el-button>
          <el-button type="primary" :loading="busy" @click="save">保存</el-button>
        </div>""",
"""        <div class="re-footer-right">
          <span class="re-autosave" :class="saveStatus">{{ { saving: '保存中…', saved: '已自动保存', error: '保存失败' }[saveStatus] || '自动保存开启' }}</span>
          <el-button @click="visible = false">取消</el-button>
          <el-button type="primary" :loading="busy" @click="save">保存</el-button>
        </div>"""
)
rep(
".re-footer-right { display: flex; gap: 10px; }",
".re-footer-right { display: flex; gap: 10px; align-items: center; }\n.re-autosave { font-size: 12px; color: var(--sub); }\n.re-autosave.saved { color: var(--green); }\n.re-autosave.error { color: #ff7a7a; }"
)

p.write_text(t, encoding='utf-8')
print('RoleEditorModal.vue patched')
