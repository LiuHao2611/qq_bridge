<script setup>
import { inject, computed } from 'vue'
const props = defineProps({ node: Object, editable: Boolean })
const simulate = inject('simulate', () => {})
const simState = inject('simState', null)
const activePageId = inject('activePageId', null)

const active = computed(() => {
  const p = props.node.props || {}
  const at = p.actionType
  // 页面切换型：按当前激活页高亮
  if (at === 'switchPage') {
    const cur = activePageId && activePageId.value
    return cur === (p.actionTarget || p.content)
  }
  // 页签（子页面）切换型：按当前激活子页高亮
  if (at === 'switchSubpage') {
    const cur = simState && simState.activeSubpage
    return cur === (p.actionTarget || p.content)
  }
  // 普通菜单高亮（activeMenu）
  const m = simState && simState.activeMenu
  return !!m && (m === p.content || m === p.actionTarget)
})

function onClick() {
  if (props.editable) return
  const a = props.node.props || {}
  const at = a.actionType
  if (at === 'switchPage') simulate({ type: 'switchPage', target: a.actionTarget || a.content })
  else if (at === 'switchSubpage') simulate({ type: 'switchSubpage', target: a.actionTarget })
  else if (at === 'menu') {
    if (simState) simState.activeMenu = a.actionTarget || a.content
    simulate({ type: 'toast', message: '已切换：' + (a.actionTarget || a.content) + '（沙盒模拟）' })
  } else if (a.target) simulate({ type: 'toast', message: '跳转（模拟）：' + a.target })
}

// 选中态用内联样式覆盖（优先级高于节点自带 color）
const styleObj = computed(() =>
  active.value
    ? { ...(props.node.style || {}), background: 'var(--pur)', color: '#fff' }
    : (props.node.style || {})
)
</script>

<template>
  <div class="cp-menuitem" :class="{ on: active }" :style="styleObj" @click="onClick">{{ node.props?.content }}</div>
</template>
