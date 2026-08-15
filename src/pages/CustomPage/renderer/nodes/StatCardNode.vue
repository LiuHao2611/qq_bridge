<script setup>
const props = defineProps({ node: Object, editable: Boolean })
</script>

<template>
  <div class="cp-statcard" :style="node.style">
    <div class="cp-stat-label">{{ node.props?.title }}</div>
    <div class="cp-stat-value">{{ node.props?.value }}<span v-if="node.props?.unit" class="cp-stat-unit">{{ node.props.unit }}</span></div>
    <div class="cp-stat-sub">{{ node.props?.sub }}</div>
  </div>
</template>
