<script setup>
const props = defineProps({ node: Object, editable: Boolean })
</script>

<template>
  <div class="cp-img" :style="node.style">
    <span class="cp-img-label">{{ node.props?.src ? '🖼 ' + node.props.src : '图片占位' }}</span>
  </div>
</template>
