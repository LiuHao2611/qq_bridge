<script setup>
const props = defineProps({ node: Object, editable: Boolean })
</script>

<template>
  <input class="cp-input" :placeholder="node.props?.placeholder" :style="node.style" readonly />
</template>
