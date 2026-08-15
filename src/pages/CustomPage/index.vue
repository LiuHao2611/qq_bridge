<script setup>
import { reactive, computed, provide, ref, watch, unref, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import VNode from './renderer/VNode.vue'
import EditOverlay from './editor/EditOverlay.vue'
import PropertyPanel from './editor/PropertyPanel.vue'
import { useEditor } from './editor/useEditor'
import { useCustomPageStore } from './store'

const PAGE_LABELS = {
  home: '首页', projects: '项目中心', tasks: '推理监控', custom: '页面自定义',
  txt2img: '文生图', img2img: '图生图', creative: '创意工坊', memoir: '人生回忆录',
  'global-assets': '全局资产库', img2prompt: '图片反推', 'lora-train': 'LoRA 训练', history: '历史记录',
  membership: '会员中心', messages: '消息中心', help: '帮助与反馈',
  'my-data': '我的数据', logs: '操作日志', settings: '设置', system: '服务与版本',
}

const store = useCustomPageStore()
const { tree, mode, activePageId, setActivePage, pages, loadDefault, save, saveTheme, getSavedTheme } = store
const { select, deselect } = useEditor()

// 模拟状态（预览交互）：弹窗开关 / 子页面 / 提示 / 侧边菜单选中态
const simState = reactive({ activeSubpage: '', dialogs: {}, toasts: [], activeMenu: '' })
function simulate(action) {
  if (!action) return
  if (action.type === 'openDialog') simState.dialogs[action.target] = true
  else if (action.type === 'closeDialog') simState.dialogs[action.target] = false
  else if (action.type === 'switchSubpage') simState.activeSubpage = action.target || ''
  else if (action.type === 'switchPage') {
    const id = action.target
    if (id && pages[id]) { setActivePage(id); simState.toasts.push('已切换到：' + (PAGE_LABELS[id] || id) + '（沙盒模拟）') }
  }
  else if (action.type === 'toast') simState.toasts.push(action.message || '提示')
}
// 提供依赖注入给节点组件与编辑器
provide('tree', tree)
provide('simState', simState)
provide('simulate', simulate)
provide('select', select)
provide('activePageId', activePageId)

// 切页 / 恢复默认时，把激活子页（页签）重置为该页第一个 subpage，
// 否则跨页后 activeSubpage 指向别的页的 id，导致新页所有页签面板都不显示。
function firstSubpageOf(node) {
  if (!node) return ''
  if (node.type === 'subpage') return node.props?.subpageId || ''
  for (const c of (node.children || [])) {
    const r = firstSubpageOf(c)
    if (r) return r
  }
  return ''
}
watch(
  () => activePageId.value,
  () => { simState.activeSubpage = firstSubpageOf(unref(tree)) },
  { immediate: true }
)

const editable = computed(() => mode.value === 'edit')

function enterEdit() { mode.value = 'edit' }
function exitEdit() { mode.value = 'edit'; mode.value = 'preview'; deselect() }

// 提示自动消失
watch(() => simState.toasts.length, () => {
  if (simState.toasts.length) {
    const msg = simState.toasts[simState.toasts.length - 1]
    setTimeout(() => {
      const i = simState.toasts.indexOf(msg)
      if (i >= 0) simState.toasts.splice(i, 1)
    }, 2200)
  }
})

// 沙盒 → 主题包（手册 10.5）
function isConcreteColor(v) { return typeof v === 'string' && /^(#|rgb|hsl)/i.test(v.trim()) }
function extractColors(v) {
  if (typeof v !== 'string') return []
  return v.match(/#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)/g) || []
}
function buildDefaultMap(node, acc) {
  acc[node.id] = node
  ;(node.children || []).forEach((c) => buildDefaultMap(c, acc))
  return acc
}
function buildThemePackage() {
  const defMap = {}
  Object.values(pages).forEach((p) => buildDefaultMap(p, defMap))
  const rules = []
  const variables = {}
  const walk = (node) => {
    const def = defMap[node.id]
    if (node.targetSelector && node.style) {
      const style = {}
      for (const [k, v] of Object.entries(node.style)) {
        if (typeof v !== 'string') continue
        const colors = extractColors(v)
        if (colors.length) {
          // 保留完整值（boxShadow/border 等含色也一并进入规则），并映射到对应主题变量
          style[k] = v
          const defVal = def?.style?.[k]
          if (typeof defVal === 'string') {
            const m = defVal.match(/var\((--[\w-]+)\)/)
            if (m) variables[m[1]] = colors[0]
          }
        }
      }
      if (Object.keys(style).length) rules.push({ selector: node.targetSelector, style })
    }
    ;(node.children || []).forEach(walk)
  }
  Object.values(pages).forEach(walk)
  const firstColor = Object.values(variables)[0]
    || (rules[0] ? extractColors(Object.values(rules[0].style)[0] || '')[0] : '')
    || '#E4002B'
  return {
    version: 1,
    savedAt: new Date().toLocaleString(),
    name: '我的自定义主题',
    previewColor: firstColor,
    variables,
    rules,
  }
}

function onSave() {
  save()
  ElMessage.success('已保存沙盒页面（本地持久化）')
}
function onSaveTheme() {
  const pkg = buildThemePackage()
  saveTheme(pkg)
  save()
  ElMessage.success('已保存为「自定义主题」，可在设置页「配色主题」中启用')
}
function onReset() {
  loadDefault()
  ElMessage.info('已恢复默认首页复刻')
}

const savedTheme = ref(getSavedTheme())
</script>

<template>
  <div class="cp-page">
    <!-- 工具栏 -->
    <div class="cp-toolbar">
      <div class="cp-toolbar-left">
        <span class="cp-title">页面自定义</span>
        <el-select :model-value="activePageId" @update:model-value="setActivePage" size="small" style="width:132px">
          <el-option v-for="(p, id) in pages" :key="id" :label="PAGE_LABELS[id] || id" :value="id" />
        </el-select>
        <span class="cp-mode-tag">{{ editable ? '编辑模式' : '预览模式（沙盒）' }}</span>
      </div>
      <div class="cp-toolbar-right">
        <el-button v-if="!editable" type="primary" size="small" @click="enterEdit">✎ 编辑当前页面</el-button>
        <template v-else>
          <el-button size="small" @click="exitEdit">退出编辑</el-button>
          <el-button size="small" @click="onReset">恢复默认</el-button>
          <el-button size="small" type="success" @click="onSaveTheme">保存为自定义主题</el-button>
          <el-button size="small" type="primary" @click="onSave">保存</el-button>
        </template>
      </div>
    </div>

    <!-- 主区域：舞台 + 右侧属性抽屉（手册：右侧固定抽屉，宽 300px） -->
    <div class="cp-main">
      <!-- 沙盒舞台 -->
      <div class="cp-stage">
        <VNode :node="tree" path="" :editable="editable" />

        <!-- 提示（模拟 toast） -->
        <transition-group name="fade" tag="div" class="cp-toasts">
          <div v-for="(t, i) in simState.toasts" :key="i" class="cp-toast">{{ t }}</div>
        </transition-group>

        <!-- 编辑层 -->
        <EditOverlay v-if="editable" />
      </div>

      <!-- 属性面板（右侧抽屉） -->
      <PropertyPanel v-if="editable" />
    </div>
  </div>
</template>

<style scoped>
.cp-page { display: flex; flex-direction: column; height: 100%; width: 100%; background: var(--bg); }
.cp-toolbar { display: flex; align-items: center; justify-content: space-between; padding: 10px 18px; border-bottom: 1px solid var(--line); background: var(--panel); flex: 0 0 auto; position: relative; z-index: 3000; }
.cp-toolbar-left { display: flex; align-items: center; gap: 12px; }
.cp-title { font-size: 15px; font-weight: 800; color: var(--txt); }
.cp-mode-tag { font-size: 11px; padding: 2px 10px; border-radius: 10px; background: var(--card-2); color: var(--sub); }
.cp-toolbar-right { display: flex; gap: 8px; }
.cp-main { display: flex; flex: 1; min-height: 0; }
.cp-stage { flex: 1; overflow: auto; position: relative; padding: 0; min-width: 0; }
.cp-toasts { position: fixed; right: 24px; bottom: 24px; z-index: 9000; display: flex; flex-direction: column; gap: 8px; }
.cp-toast { background: var(--card); border: 1px solid var(--line); color: var(--txt); padding: 10px 16px; border-radius: 10px; font-size: 13px; box-shadow: 0 6px 24px rgba(0,0,0,.3); }
/* 编辑态：可点选元素 hover 高亮（全局，覆盖所有 v-node 根） */
:deep(.v-node--editable) { cursor: pointer; }
:deep(.v-node--editable:hover) { outline: 1px dashed var(--pur); outline-offset: 1px; }
/* 弹窗遮罩（仅预览可点关闭） */
:deep(.cp-dialog-mask) { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 1999; }
:deep(.cp-card-title) { font-size: 16px; font-weight: 700; color: var(--txt); margin-bottom: 14px; }
:deep(.cp-stat-label) { font-size: 14px; color: var(--sub); margin-bottom: 10px; }
:deep(.cp-stat-value) { font-size: 30px; font-weight: 800; color: var(--txt); margin-bottom: 6px; }
:deep(.cp-stat-sub) { font-size: 12px; color: var(--sub); }
:deep(.cp-tag) { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; }
:deep(.cp-tag--default) { background: var(--card-2); color: var(--sub); }
:deep(.cp-tag--cyan) { background: rgba(0,240,255,.15); color: #00f0ff; }
:deep(.cp-tag--magenta) { background: rgba(255,0,85,.15); color: #ff0055; }
:deep(.cp-tag--green) { background: rgba(61,220,132,.15); color: #00ff9c; }
:deep(.cp-tag--ghost) { background: transparent; color: var(--sub); border: 1px solid var(--line); }
:deep(.cp-progress) { width: 100%; height: 10px; background: var(--card-2); border-radius: 6px; overflow: hidden; }
:deep(.cp-progress-bar) { height: 100%; transition: width .3s; }
:deep(.cp-img) { display: flex; align-items: center; justify-content: center; min-height: 80px; background: var(--card-2); border: 1px dashed var(--line); border-radius: 10px; color: var(--sub); font-size: 12px; }
:deep(.cp-menuitem) { cursor: pointer; }
:deep(.cp-menuitem:hover) { opacity: .8; }
:deep(.cp-input) { color: var(--txt); outline: none; }
/* 左导航菜单项：整块可点、选中态高亮 */
:deep(.cp-sidebar) { display: flex; flex-direction: column; gap: 4px; }
:deep(.cp-sidebar > .cp-menuitem) { display: block; padding: 10px 12px; border-radius: 8px; color: var(--sub); font-size: 14px; }
:deep(.cp-sidebar > .cp-menuitem:hover) { background: var(--card-2); color: var(--txt); opacity: 1; }
:deep(.cp-sidebar > .cp-menuitem.on) { background: var(--pur); color: #fff; font-weight: 700; }
</style>
