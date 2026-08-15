<script setup>
import { computed, inject } from 'vue'
import { nodeTypes } from '../schema/nodeTypes'

const props = defineProps({
  node: { type: Object, required: true },
  path: { type: String, required: true },
  editable: { type: Boolean, default: false },
})
const select = inject('select', () => {})
const meta = computed(() => nodeTypes[props.node.type] || nodeTypes.unknown)

function onSelect(e) {
  if (!props.editable) return
  e.stopPropagation()
  select(props.path)
}
</script>

<template>
  <component
    :is="meta.component"
    :node="node"
    :path="path"
    :editable="editable"
    :data-path="path"
    :data-nid="node.id"
    :class="editable ? 'v-node v-node--editable' : 'v-node'"
    @click="onSelect"
  />
</template>
