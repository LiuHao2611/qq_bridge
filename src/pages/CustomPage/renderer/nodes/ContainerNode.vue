<script setup>
import { computed, inject } from 'vue'
import VNode from '../VNode.vue'

const props = defineProps({
  node: { type: Object, required: true },
  path: { type: String, default: '' },
  editable: { type: Boolean, default: false },
})
const simState = inject('simState', null)

const isDialog = computed(() => props.node.type === 'dialog')
const isSubpage = computed(() => props.node.type === 'subpage')
const isDivider = computed(() => props.node.type === 'divider')

const visible = computed(() => {
  if (isDialog.value) {
    const id = props.node.props?.dialogId
    return !!(simState && simState.dialogs[id])
  }
  if (isSubpage.value) {
    const id = props.node.props?.subpageId
    return !!(simState && simState.activeSubpage === id)
  }
  return true
})

// 弹窗：外层包裹（遮罩 + 居中盒），node.style 应用到盒本身
const wrapStyle = computed(() => {
  if (isDialog.value) {
    return { position: 'fixed', inset: '0', zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center' }
  }
  return null
})
const selfStyle = computed(() => {
  const s = { ...(props.node.style || {}) }
  if (isDivider.value) {
    s.width = s.width || '100%'
    s.height = s.height || '1px'
    s.background = s.background || 'var(--line)'
    s.border = '0'
    return s
  }
  if (isDialog.value) return {} // 弹窗定位由 wrapStyle 负责，盒样式用 node.style
  return s
})

function onMaskClick(e) {
  if (props.editable) return
  const t = e.target
  const onMask = t === e.currentTarget || (t.classList && t.classList.contains('cp-dialog-mask'))
  if (onMask && simState && isDialog.value) simState.dialogs[props.node.props.dialogId] = false
}
function onBoxClick(e) {
  // 点击盒内空白（非子节点）时不关闭弹窗（预览态）
  if (!props.editable) e.stopPropagation()
}
</script>

<template>
  <!-- 弹窗：遮罩 + 居中盒 -->
  <div v-if="isDialog" v-show="visible" class="cp-container cp-dialog" :style="wrapStyle" :data-path="path" @click="onMaskClick">
    <div class="cp-dialog-mask"></div>
    <div class="cp-dialog-box" :style="selfStyle" @click="onBoxClick">
      <template v-if="node.children?.length">
        <VNode
          v-for="(child, i) in node.children"
          :key="i"
          :node="child"
          :path="path ? path + '.' + i : '' + i"
          :editable="editable"
        />
      </template>
    </div>
  </div>

  <!-- 其余容器 -->
  <div v-else v-show="visible" class="cp-container" :class="'cp-' + node.type" :style="selfStyle" :data-path="path">
    <div v-if="node.type === 'card' && node.props?.title" class="cp-card-title">{{ node.props.title }}</div>
    <template v-if="node.children?.length">
      <VNode
        v-for="(child, i) in node.children"
        :key="i"
        :node="child"
        :path="path ? path + '.' + i : '' + i"
        :editable="editable"
      />
    </template>
  </div>
</template>

<style scoped>
.cp-dialog-mask { position: absolute; inset: 0; background: rgba(0, 0, 0, 0.5); pointer-events: none; }
.cp-dialog-box { position: relative; z-index: 1; pointer-events: auto; }
</style>
