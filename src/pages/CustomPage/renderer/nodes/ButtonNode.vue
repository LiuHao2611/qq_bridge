<script setup>
import { inject } from 'vue'
const props = defineProps({ node: Object, path: String, editable: Boolean })
const simulate = inject('simulate', () => {})
function onClick() {
  if (props.editable) return
  const a = props.node.props || {}
  simulate({ type: a.actionType, target: a.actionTarget, message: a.actionMessage })
}
</script>

<template>
  <button :style="node.style" @click="onClick"><span>{{ node.props?.content }}</span></button>
</template>
