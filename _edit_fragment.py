from pathlib import Path
p = Path('src/components/ProjectFragmentEditor.vue')
t = p.read_text(encoding='utf-8')

def rep(old, new, count=1):
    global t
    n = t.count(old)
    if n != count:
        raise SystemExit(f'EXPECTED {count} occurrence(s) of:\n{old[:80]}\nfound {n}')
    t = t.replace(old, new)

# 0) 导入：增加 useAutoSave / ElMessageBox / onUnmounted
rep(
"import { useProject } from '../composables/useProject'\nimport { useHotkeys } from '../composables/useHotkeys'\nimport { toast } from '../store'",
"import { useProject } from '../composables/useProject'\nimport { useHotkeys } from '../composables/useHotkeys'\nimport { useAutoSave } from '../composables/useAutoSave'\nimport { toast } from '../store'\nimport { ElMessageBox } from 'element-plus'"
)
rep(
"import { ref, reactive, computed, onMounted, watch, nextTick } from 'vue'",
"import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'"
)

# 1) onSave 返回 boolean（供自动保存判定成败）
rep(
"""  if (br003Mismatch.value) { toast('分镜时长之和不等于片段时长，无法保存（BR-003）'); return }
  if (br009Exceed.value) { toast('片段资产引用最多 9 个（BR-009）'); return }
  await save({ title: p.value.title })
  dirty.value = false
}""",
"""  if (br003Mismatch.value) { toast('分镜时长之和不等于片段时长，无法保存（BR-003）'); return false }
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
onUnmounted(() => window.removeEventListener('beforeunload', _beforeUnload))"""
)

# 2) desc-modal 关闭逻辑 + before-close
rep(
"const closeDescModal = () => {\n  descModalVisible.value = false\n  descModalShot.value = null\n  descModalText.value = ''\n}",
"""const closeDescModal = () => {
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
}"""
)
rep(
'v-model="descModalVisible" title="编辑画面描述" width="90%" top="5vh" append-to-body class="desc-modal" destroy-on-close :close-on-click-modal="false">',
'v-model="descModalVisible" title="编辑画面描述" width="90%" top="5vh" append-to-body class="desc-modal" destroy-on-close :before-close="beforeDescModalClose">'
)

# 3) 资产阶段编辑弹窗：允许点遮罩关闭（RoleStageManager 改动即时落库，无未保存态）
rep(
'v-model="assetStagePanel.open" title="编辑阶段 / 变体" width="820px" top="8vh" append-to-body destroy-on-close :close-on-click-modal="false">',
'v-model="assetStagePanel.open" title="编辑阶段 / 变体" width="820px" top="8vh" append-to-body destroy-on-close>'
)

# 4) 新建资产弹窗：允许点遮罩关闭，但已填内容时二次确认
rep(
"const openCreateAsset = (type) => {\n  assetForm.name = ''\n  assetForm.type = type || 'scene'\n  assetForm.role_type = 'protagonist'\n  assetForm.description = ''\n  assetDialogVisible.value = true\n}",
"""const openCreateAsset = (type) => {
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
}"""
)
rep(
'v-model="assetDialogVisible" title="新建资产" width="440px" append-to-body>',
'v-model="assetDialogVisible" title="新建资产" width="440px" append-to-body :before-close="beforeAssetDialogClose">'
)

# 5) ws-actions 增加保存状态指示
rep(
"""          <div class="ws-actions">
            <el-button size="small" @click="onCancel">取消</el-button>
            <el-button size="small" type="primary" @click="onSave">保存</el-button>""",
"""          <div class="ws-actions">
            <span class="save-state" :class="saveStatus">
              <i v-if="saveStatus==='saving' || saveStatus==='pending'" class="dot spin" />
              <i v-else-if="saveStatus==='saved'" class="dot ok" />
              <i v-else-if="saveStatus==='error'" class="dot bad" />
              <i v-else class="dot" />
              <span>{{ { saving: '保存中…', pending: '待保存…', saved: '已自动保存', error: '保存失败' }[saveStatus] || '自动保存开启' }}</span>
            </span>
            <el-button size="small" @click="onCancel">取消</el-button>
            <el-button size="small" type="primary" @click="onSave">保存</el-button>"""
)

# 6) CSS：保存状态指示
rep(
".seg-scene-setting .el-input { flex: 1; }",
""".seg-scene-setting .el-input { flex: 1; }

/* 自动保存状态指示 */
.save-state { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--sub); margin-right: auto; }
.save-state .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--sub); opacity: .5; }
.save-state .dot.ok { background: var(--green); opacity: 1; }
.save-state .dot.bad { background: #ff7a7a; opacity: 1; }
.save-state.saved { color: var(--green); }
.save-state.error { color: #ff7a7a; }
.save-state.saving .dot, .save-state.pending .dot { background: var(--pur); opacity: 1; animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }"""
)

p.write_text(t, encoding='utf-8')
print('ProjectFragmentEditor.vue patched')
