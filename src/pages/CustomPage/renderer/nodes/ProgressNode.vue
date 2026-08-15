<script setup>
const props = defineProps({ node: Object, editable: Boolean })
</script>

<template>
  <div class="cp-progress" :style="node.style">
    <div class="cp-progress-bar" :style="{ width: (node.props?.percent || 0) + '%', background: node.props?.color || 'var(--pur)' }"></div>
  </div>
</template>
