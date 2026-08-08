<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const props = defineProps({
  pid: { type: String, required: true },
  step: { type: Number, default: 3 },
})

const router = useRouter()
const route = useRoute()

const steps = [
  { id: 1, label: '资产初始化', icon: '🌐', pathPrefix: '/projects/', suffix: '/assets', tip: '初始化角色 / 场景 / 道具资产（全集可一键复用至本集）' },
  { id: 2, label: '项目策划', icon: '📖', pathPrefix: '/projects/', suffix: '/planning', tip: '粘贴故事文本，AI 自动分场拆解' },
  { id: 3, label: '分镜编排', icon: '🎬', pathPrefix: '/projects/', suffix: '/editor', tip: '多模态分镜编辑与资产绑定' },
  { id: 4, label: '生成生产', icon: '🚀', pathPrefix: '/projects/', suffix: '/production', tip: '分发生成任务并实时监看异步 AI 推理进度与结果' },
  { id: 5, label: '合成全集', icon: '🎞️', action: 'synthesize', tip: '所有片段生成成功后拼接完整视频' },
]

const current = computed(() => props.step)

const onStep = (s) => {
  if (s.action === 'synthesize') {
    emit('synthesize')
    return
  }
  if (s.path) {
    router.push(s.path)
    return
  }
  const target = s.pathPrefix + props.pid + s.suffix
  if (route.path !== target) router.push(target)
}

const emit = defineEmits(['synthesize'])
</script>

<template>
  <div class="flow-nav">
    <div
      v-for="(s, i) in steps"
      :key="s.id"
      class="step"
      :class="{ active: current === s.id, done: current > s.id, last: i === steps.length - 1 }"
      @click="onStep(s)"
      :title="s.tip"
    >
      <div class="ring">{{ s.icon }}</div>
      <div class="label">{{ s.label }}</div>
      <div v-if="i < steps.length - 1" class="line"></div>
    </div>
  </div>
</template>

<style scoped>
.flow-nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0;
  padding: 10px 18px 6px;
  background: var(--bg-2);
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}
.step {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 0;
  color: var(--sub);
  transition: 0.15s;
  user-select: none;
}
.step:hover { color: var(--txt); }
.step.active { color: var(--pur); font-weight: 700; }
.step.active .ring { border-color: var(--pur); background: rgba(138, 92, 255, 0.15); box-shadow: 0 0 10px rgba(138, 92, 255, 0.35); }
.step.done { color: var(--green); }
.step.done .ring { border-color: var(--green); background: rgba(74, 222, 128, 0.12); }
.ring {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 2px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  background: var(--card);
}
.label { font-size: 13px; white-space: nowrap; }
.line {
  width: 28px;
  height: 2px;
  background: var(--line);
  margin: 0 8px;
  border-radius: 1px;
}
.step.done + .step .line,
.step.active + .step .line {
  background: linear-gradient(90deg, var(--pur) 0%, var(--line) 100%);
}
</style>
