<script setup>
const props = defineProps({ node: Object, editable: Boolean })
</script>

<template>
  <span class="cp-tag" :class="'cp-tag--' + (node.props?.variant || 'default')" :style="node.style">{{ node.props?.content }}</span>
</template>
