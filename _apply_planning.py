#!/usr/bin/env python3
# 应用 P2（ProjectPlanning.vue）自动保存草稿 + 弹窗外部关闭
import io, sys

PATH = 'src/components/ProjectPlanning.vue'
with io.open(PATH, encoding='utf-8') as f:
    src = f.read()

reps = []  # (old, new, desc)

# 1) 导入 watch 与 ElMessageBox
reps.append((
    "import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'\n",
    "import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'\n"
    "import { ElMessageBox } from 'element-plus'\n",
    "import vue + ElMessageBox"
))

# 2) 在 pendingTimers 之后插入 USE_MOCK + 草稿逻辑
reps.append((
    "let pendingTimers = []\n",
    "let pendingTimers = []\n"
    "\n"
    "const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'\n"
    "\n"
    "// ---- 策划页草稿自动保存（localStorage 兜底）----\n"
    "// 策划页内容仅在 doImport 时落库（createSegment + putShots），导入前 story 文本仅存内存。\n"
    "// 为避免误关页面丢失原始剧本：防抖写 localStorage + 挂载时提示恢复（双保险）。\n"
    "// CLOSE-02：草稿写入失败显式提示，不静默。\n"
    "const DRAFT_KEY = 'manju_planning_draft_' + pid\n"
    "const draftAvailable = ref(false)\n"
    "const pendingDraft = ref(null)\n"
    "const draftSavedAt = ref(0)\n"
    "let draftTimer = null\n"
    "function persistDraft() {\n"
    "  if (USE_MOCK) return\n"
    "  try {\n"
    "    localStorage.setItem(DRAFT_KEY, JSON.stringify({ story: story.value, ts: Date.now() }))\n"
    "    draftSavedAt.value = Date.now()\n"
    "  } catch (e) {\n"
    "    toast('草稿保存失败：' + (e.message || '未知错误'))\n"
    "  }\n"
    "}\n"
    "function scheduleDraft() {\n"
    "  if (draftTimer) clearTimeout(draftTimer)\n"
    "  draftTimer = setTimeout(persistDraft, 1200)\n"
    "}\n"
    "function clearDraft() {\n"
    "  try { localStorage.removeItem(DRAFT_KEY) } catch { /* ignore */ }\n"
    "}\n"
    "function checkDraft() {\n"
    "  try {\n"
    "    const raw = localStorage.getItem(DRAFT_KEY)\n"
    "    if (!raw) return\n"
    "    const d = JSON.parse(raw)\n"
    "    if (d && typeof d.story === 'string' && d.story.trim() && d.story !== story.value) {\n"
    "      pendingDraft.value = d\n"
    "      draftAvailable.value = true\n"
    "    }\n"
    "  } catch { /* ignore */ }\n"
    "}\n"
    "function restoreDraft() {\n"
    "  if (pendingDraft.value) {\n"
    "    story.value = pendingDraft.value.story\n"
    "    draftSavedAt.value = Date.now()\n"
    "    draftAvailable.value = false\n"
    "    pendingDraft.value = null\n"
    "    toast('已恢复上次未导入的草稿')\n"
    "  }\n"
    "}\n"
    "function discardDraft() {\n"
    "  clearDraft()\n"
    "  draftAvailable.value = false\n"
    "  pendingDraft.value = null\n"
    "  toast('已丢弃草稿')\n"
    "}\n"
    "watch(story, () => { if (!USE_MOCK) scheduleDraft() }, { flush: 'post' })\n",
    "insert USE_MOCK + draft logic"
))

# 3) onMounted 加 checkDraft
reps.append((
    "onMounted(() => {\n"
    "  load()\n"
    "  refreshLocalModels()\n"
    "})\n",
    "onMounted(() => {\n"
    "  load()\n"
    "  refreshLocalModels()\n"
    "  checkDraft()\n"
    "})\n",
    "onMounted checkDraft"
))

# 4) doImport 成功清除草稿
reps.append((
    "    toast(`已导入 ${toImport.length} 个片段，进入分镜编排`)\n"
    "    router.push('/projects/' + pid + '/editor')\n",
    "    clearDraft()\n"
    "    toast(`已导入 ${toImport.length} 个片段，进入分镜编排`)\n"
    "    router.push('/projects/' + pid + '/editor')\n",
    "doImport clearDraft"
))

# 5) beforeOnlineClose（在线模型弹窗外部关闭二次确认）
reps.append((
    "function applyOnlineConfig() {\n",
    "function beforeOnlineClose(done) {\n"
    "  const c = modelConfig.value\n"
    "  const dirty = c.apiBase || c.apiKey || c.model\n"
    "  if (!dirty) { done(); return }\n"
    "  ElMessageBox.confirm('在线模型配置尚未保存，确定关闭？', '提示', {\n"
    "    confirmButtonText: '放弃并关闭',\n"
    "    cancelButtonText: '继续编辑',\n"
    "    type: 'warning',\n"
    "  }).then(() => done()).catch(() => {})\n"
    "}\n"
    "function applyOnlineConfig() {\n",
    "beforeOnlineClose"
))

# 6) 模板 draft-banner（ProjectFlowNav 之后）
reps.append((
    "    <ProjectFlowNav :pid=\"pid\" :step=\"2\" />\n",
    "    <ProjectFlowNav :pid=\"pid\" :step=\"2\" />\n"
    "\n"
    "    <div v-if=\"draftAvailable\" class=\"draft-banner\">\n"
    "      <span class=\"draft-tip\">💡 检测到上次未导入的剧本草稿，是否恢复？</span>\n"
    "      <div class=\"draft-actions\">\n"
    "        <el-button size=\"small\" type=\"primary\" @click=\"restoreDraft\">恢复草稿</el-button>\n"
    "        <el-button size=\"small\" text @click=\"discardDraft\">丢弃</el-button>\n"
    "      </div>\n"
    "    </div>\n",
    "draft-banner template"
))

# 7) online dialog before-close
reps.append((
    "    <el-dialog v-model=\"onlineDialogVisible\" title=\"配置在线模型\" width=\"480px\" align-center>\n",
    "    <el-dialog v-model=\"onlineDialogVisible\" title=\"配置在线模型\" width=\"480px\" align-center :before-close=\"beforeOnlineClose\">\n",
    "online dialog before-close"
))

# 8) story-foot 草稿已保存提示
reps.append((
    "          <span class=\"muted\">已输入 {{ story.length }} 字</span>\n",
    "          <span class=\"muted\">已输入 {{ story.length }} 字<span v-if=\"draftSavedAt\"> · 草稿已自动保存</span></span>\n",
    "story-foot draft hint"
))

# 9) CSS draft-banner（推理进度面板注释之前）
reps.append((
    "\n/* 推理进度日志面板（底部实时打印 AI 分场过程） */\n",
    "\n.draft-banner {\n"
    "  display: flex; align-items: center; justify-content: space-between; gap: 12px;\n"
    "  background: rgba(138, 92, 255, .12); border: 1px solid rgba(138, 92, 255, .4);\n"
    "  color: var(--txt); border-radius: 10px; padding: 10px 14px; margin: 0 16px 14px; font-size: 13px;\n"
    "}\n"
    ".draft-tip { color: var(--pur); }\n"
    ".draft-actions { display: flex; gap: 8px; flex: 0 0 auto; }\n"
    "\n/* 推理进度日志面板（底部实时打印 AI 分场过程） */\n",
    "draft-banner CSS"
))

# ---- apply ----
for old, new, desc in reps:
    cnt = src.count(old)
    if cnt != 1:
        print(f"[FAIL] {desc}: expected 1 match, found {cnt}")
        sys.exit(1)
    src = src.replace(old, new, 1)
    print(f"[OK]   {desc}")

with io.open(PATH, 'w', encoding='utf-8') as f:
    f.write(src)

# ---- verify ----
markers = ['ElMessageBox', 'USE_MOCK', 'draftAvailable', 'checkDraft', 'clearDraft',
           'beforeOnlineClose', 'draft-banner', 'draft-tip', 'draft-actions', 'draftSavedAt']
miss = [m for m in markers if m not in src]
print("MISSING:", miss if miss else "none")
print("DONE")
