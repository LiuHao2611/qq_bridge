<script setup>
import { ref, watch, onMounted, onBeforeUnmount, inject, nextTick } from 'vue'
import { useEditor } from './useEditor'

const { selectedPath } = useEditor()
const simState = inject('simState', null)
const box = ref(null)

function cssEscape(s) {
  return window.CSS && CSS.escape ? CSS.escape(s) : s
}
function refresh() {
  if (!selectedPath.value) { box.value = null; return }
  const el = document.querySelector(`[data-path="${cssEscape(selectedPath.value)}"]`)
  if (!el) { box.value = null; return }
  const r = el.getBoundingClientRect()
  box.value = { left: r.left, top: r.top, width: r.width, height: r.height, label: selectedPath.value }
}

let raf = null
function schedule() {
  if (raf) cancelAnimationFrame(raf)
  raf = requestAnimationFrame(refresh)
}

onMounted(() => {
  window.addEventListener('scroll', schedule, true)
  window.addEventListener('resize', schedule)
  if (simState) watch(() => [simState.dialogs, simState.activeSubpage], schedule, { deep: true })
})
onBeforeUnmount(() => {
  window.removeEventListener('scroll', schedule, true)
  window.removeEventListener('resize', schedule)
  if (raf) cancelAnimationFrame(raf)
})
watch(selectedPath, () => nextTick(schedule))
</script>

<template>
  <div
    v-if="box"
    class="edit-box"
    :style="{ left: box.left + 'px', top: box.top + 'px', width: box.width + 'px', height: box.height + 'px' }"
  >
    <span class="edit-box__label">{{ box.label }}</span>
  </div>
</template>

<style scoped>
.edit-box {
  position: fixed;
  z-index: 9999;
  pointer-events: none;
  border: 2px dashed var(--p5-red, #E4002B);
  box-shadow: 0 0 0 2px rgba(228, 0, 43, 0.25);
}
.edit-box__label {
  position: absolute;
  top: -22px;
  left: -2px;
  background: var(--p5-red, #E4002B);
  color: #fff;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  white-space: nowrap;
  font-family: var(--font-mono, monospace);
}
</style>
