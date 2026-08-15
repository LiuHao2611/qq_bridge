<script setup>
// 项目阶段电子书翻页外壳
// - 顶部与底部阶段导航已全部移除，统一由 TopBar 面包屑显示当前阶段标题
// - 中间 slot 渲染当前阶段内容
// - 左上角 / 右上角水波纹折角翻页按钮：贴角、内凹、hover 水波扩散
//   - P5 主题：硬角硬阴影小按钮（红底黑边）
//   - 其他主题：通用方块卡片（避免 P5 风格误入默认主题）
// - 键盘 ← → 快速翻页
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useTheme } from '../composables/useTheme'

const props = defineProps({
  step: { type: Number, required: true }, // 1-5
})
const emit = defineEmits(['synthesize'])

const route = useRoute()
const router = useRouter()
const { preset } = useTheme()
// 仅 P5 主题才挂 p5-pager：CSS 中 :not(.p5-pager) 分支负责非 P5 降级
const isP5 = computed(() => preset.value === 'p5')

const pid = route.params.id

const steps = [
  { id: 1, label: '资产初始化', icon: '🔧', path: '/projects/' + pid + '/assets', tip: '角色 / 场景 / 道具资产初始化' },
  { id: 2, label: '项目策划', icon: '📖', path: '/projects/' + pid + '/planning', tip: '故事文本 AI 自动分场拆解' },
  { id: 3, label: '分镜编排', icon: '🎬', path: '/projects/' + pid + '/editor', tip: '多模态分镜编辑与资产绑定' },
  { id: 4, label: '生成生产', icon: '🚀', path: '/projects/' + pid + '/production', tip: 'AI 推理生成视频并实时监看' },
  { id: 5, label: '合成画布', icon: '🎞️', path: '/projects/' + pid + '/canvas', tip: '节点工作流画布 · 合成全集' },
]

const currentIndex = computed(() => steps.findIndex((s) => s.id === props.step))
const current = computed(() => steps[currentIndex.value] || steps[0])
const hasPrev = computed(() => currentIndex.value > 0)
const hasNext = computed(() => currentIndex.value < steps.length - 1)
const isLast = computed(() => currentIndex.value === steps.length - 1)

const prevStep = () => {
  if (!hasPrev.value) {
    router.push('/projects')
    return
  }
  const target = steps[currentIndex.value - 1].path
  if (target && route.path !== target) router.push(target)
}

const nextStep = () => {
  if (isLast.value) {
    emit('synthesize')
    return
  }
  const target = steps[currentIndex.value + 1].path
  if (target && route.path !== target) router.push(target)
}

const goStep = (s) => {
  if (s.action === 'synthesize') {
    emit('synthesize')
    return
  }
  if (s.path && route.path !== s.path) router.push(s.path)
}

let keyHandler = null
onMounted(() => {
  keyHandler = (e) => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); prevStep() }
    if (e.key === 'ArrowRight') { e.preventDefault(); nextStep() }
  }
  window.addEventListener('keydown', keyHandler)
})
onUnmounted(() => {
  if (keyHandler) window.removeEventListener('keydown', keyHandler)
})
</script>

<template>
  <div class="book-shell">
    <!-- 上一步：P5 主题下显示 P5 硬角翻页按钮，其他主题降级为通用方块 -->
    <button
      :class="['wave-fold', 'fold-prev', { 'p5-pager': isP5 }]"
      :title="hasPrev ? steps[currentIndex - 1].tip : '返回项目中心'"
      @click="prevStep"
    >
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="square" stroke-linejoin="miter">
        <polyline points="15 18 9 12 15 6" />
      </svg>
    </button>

    <!-- 下一步：P5 主题下显示 P5 硬角翻页按钮，其他主题降级为通用方块 -->
    <button
      :class="['wave-fold', 'fold-next', { 'p5-pager': isP5 }]"
      :title="isLast ? '合成全集' : steps[currentIndex + 1].tip"
      @click="nextStep"
    >
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="square" stroke-linejoin="miter">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </button>

    <!-- 中间内容页 -->
    <div class="book-page">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.book-shell {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--bg-2);
}

/* 中间书页：把顶部 46px 留给贴角翻页按钮 */
.book-page {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  padding-top: 46px;
  box-sizing: border-box;
  overflow: hidden;
}

/* ---------- 翻页按钮：P5 硬角硬阴影小按钮（替代 v1 的 120px 水波气泡） ---------- */
.wave-fold {
  position: absolute;
  top: 8px;
  width: 48px;
  height: 48px;
  z-index: 60;
  border: 2px solid var(--p5-ink, #141414);
  outline: none;
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--p5-red, #E4002B);
  color: var(--p5-white, #fff);
  box-shadow: var(--shadow-hard, 4px 4px 0 #141414);
  transition: transform .15s var(--ease-pop, cubic-bezier(.34,1.56,.64,1)), box-shadow .15s var(--ease-out, ease-out);
}
.fold-prev {
  left: 8px;
  clip-path: polygon(0 0, 100% 0, 100% 60%, 60% 100%, 0 100%);
}
.fold-prev:hover { transform: translate(-2px, -2px); box-shadow: var(--shadow-hard-lg, 6px 6px 0 #141414); }
.fold-prev:active { transform: translate(1px, 1px); box-shadow: var(--shadow-hard-sm, 2px 2px 0 #141414); }
.fold-next {
  right: 8px;
  clip-path: polygon(0 0, 100% 0, 100% 100%, 40% 100%, 0 60%);
}
.fold-next:hover { transform: translate(2px, -2px); box-shadow: var(--shadow-hard-lg, 6px 6px 0 #141414); }
.fold-next:active { transform: translate(-1px, 1px); box-shadow: var(--shadow-hard-sm, 2px 2px 0 #141414); }

/* 非 P5 主题下，按钮降级为标准方块（不让透明 / 渐变样式漏出） */
.wave-fold:not(.p5-pager) {
  background: var(--card);
  color: var(--txt);
  border-color: var(--line);
  box-shadow: none;
  clip-path: none;
  border-radius: 8px;
}
.wave-fold:not(.p5-pager):hover { transform: none; box-shadow: 0 4px 12px rgba(0,0,0,.18); }

/* 小屏适配 */
@media (max-width: 768px) {
  .wave-fold { width: 40px; height: 40px; top: 4px; }
  .wave-fold svg { width: 18px; height: 18px; }
  .fold-prev { left: 4px; }
  .fold-next { right: 4px; }
  .book-page { padding-top: 48px; }
}
</style>
