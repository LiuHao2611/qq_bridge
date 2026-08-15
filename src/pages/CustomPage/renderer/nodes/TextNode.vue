<script setup>
const props = defineProps({ node: Object, editable: Boolean })
</script>

<template>
  <component :is="node.type === 'title' ? 'h' + (node.props?.level || 1) : 'span'" :style="node.style">{{ node.props?.content }}</component>
</template>
