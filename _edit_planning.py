from pathlib import Path
p = Path('src/components/ProjectPlanning.vue')
t = p.read_text(encoding='utf-8')

def rep(old, new, count=1):
    global t
    n = t.count(old)
    if n != count:
        raise SystemExit(f'EXPECTED {count} of:\n{old[:80]}\nfound {n}')
    t = t.replace(old, new)

# 1) imports: USE_MOCK + ElMessageBox
rep(
"const pid = route.params.id\nconst { project: p, load } = useProject(pid, 'short')",
"const pid = route.params.id\nconst USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'\nconst { project: p, load } = useProject(pid, 'short')"
)
rep(
"import { toast } from '../store'",
"import { toast } from '../store'\nimport { ElMessageBox } from 'element-plus'"
)

# 2) 插入草稿自动保存逻辑 + 修改 onMounted
rep(
"""onMounted(() => {
  load()
  refreshLocalModels()
})""",
"""// ---- 草稿自动保存（P2 策划页防丢稿）----
// 策划页内容（故事文本 + 拆解结果 + 选择/裁剪）在「导入」前仅存内存，
// 用 localStorage 做草稿兜底：刷新/误关后可恢复（CLOSE-02「不丢工作」+ Web 端保存最佳实践）。
const DRAFT_KEY = () => 'manju_planning_draft_' + pid
const draftAvailable = ref(false)
const pendingDraft = ref(null)
let draftTimer = null
const snapshotDraft = () => ({
  story: story.value,
  segments: segments.value,
  selected: selected.value,
  keyword: keyword.value,
  viewMode: viewMode.value,
  trimMap: trimMap.value,
  expanded: expanded.value,
  ts: Date.now(),
})
const persistDraft = () => {
  if (USE_MOCK) return
  try { localStorage.setItem(DRAFT_KEY(), JSON.stringify(snapshotDraft())) } catch { /* 配额溢出忽略 */ }
}
const scheduleDraft = () => {
  if (draftTimer) clearTimeout(draftTimer)
  draftTimer = setTimeout(persistDraft, 1200)
}
const clearDraft = () => {
  try { localStorage.removeItem(DRAFT_KEY()) } catch { /* ignore */ }
  draftAvailable.value = false
  pendingDraft.value = null
}
const checkDraft = () => {
  try {
    const raw = localStorage.getItem(DRAFT_KEY())
    if (!raw) return
    const d = JSON.parse(raw)
    if (d && (d.story || (d.segments && d.segments.length))) {
      pendingDraft.value = d
      draftAvailable.value = true
    }
  } catch { /* ignore */ }
}
const restoreDraft = () => {
  const d = pendingDraft.value
  if (!d) return
  story.value = d.story || ''
  segments.value = d.segments || []
  selected.value = d.selected || []
  keyword.value = d.keyword || ''
  viewMode.value = d.viewMode || 'card'
  trimMap.value = d.trimMap || {}
  expanded.value = d.expanded || []
  draftAvailable.value = false
  pendingDraft.value = null
  toast('已恢复上次编辑草稿')
}
const discardDraft = () => { clearDraft() }
// 内容变更 → 防抖写草稿
watch([story, segments, selected, keyword, viewMode, trimMap, expanded], scheduleDraft, { deep: true })

onMounted(() => {
  load()
  refreshLocalModels()
  checkDraft()
})"""
)

# 3) doImport 成功后清掉草稿
rep(
"""    toast(`已导入 ${toImport.length} 个片段，进入分镜编排`)
    router.push('/projects/' + pid + '/editor')""",
"""    toast(`已导入 ${toImport.length} 个片段，进入分镜编排`)
    clearDraft()
    router.push('/projects/' + pid + '/editor')"""
)

# 4) 在线模型弹窗：允许点遮罩关闭 + 已填内容时二次确认
rep(
"const onlineDialogVisible = ref(false)",
"const onlineDialogVisible = ref(false)\n// 点遮罩/ESC/× 关闭：已填配置则二次确认防丢稿\nconst beforeOnlineClose = (done) => {\n  const c = modelConfig.value\n  if (c.apiBase || c.apiKey || c.model) {\n    ElMessageBox.confirm('在线模型配置有未保存内容，关闭后将丢失。确定关闭？', '未保存内容', { confirmButtonText: '丢弃', cancelButtonText: '继续填写', type: 'warning' })\n      .then(() => done()).catch(() => {})\n  } else { done() }\n}"
)
rep(
'    <el-dialog v-model="onlineDialogVisible" title="配置在线模型" width="480px" align-center>',
'    <el-dialog v-model="onlineDialogVisible" title="配置在线模型" width="480px" align-center :before-close="beforeOnlineClose">'
)

# 5) 恢复草稿提示条（plan-body 顶部）
rep(
"""    <div class="plan-body">
      <div
        class="story-col\"""",
"""    <div class="plan-body">
      <div v-if="draftAvailable" class="draft-banner">
        <span class="draft-tip">检测到上次未保存的草稿（{{ (pendingDraft?.segments || []).length }} 场 · {{ (pendingDraft?.story || '').length }} 字），是否恢复？</span>
        <div class="draft-actions">
          <el-button size="small" type="primary" @click="restoreDraft">恢复草稿</el-button>
          <el-button size="small" @click="discardDraft">忽略</el-button>
        </div>
      </div>
      <div
        class="story-col\""""
)

# 6) CSS：草稿提示条
rep(
"    .story-tools {\n      display: flex;\n      align-items: center;\n      justify-content: space-between;\n      margin-top: 8px;\n    }",
"""    .story-tools {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 8px;
    }
    .draft-banner {
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
      background: rgba(138,92,255,.12); border: 1px solid rgba(138,92,255,.4);
      color: var(--txt); border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; font-size: 13px;
    }
    .draft-tip { color: var(--pur); }
    .draft-actions { display: flex; gap: 8px; flex: 0 0 auto; }"""
)

p.write_text(t, encoding='utf-8')
print('ProjectPlanning.vue patched')
