<script setup>
// 项目统一入口：根据后端派生的 current_phase 自动跳转到「进行中阶段」。
// - 新建项目（无资产）→ current_phase=1 → 资产初始化
// - 旧项目 → 跳到第一个未完成的阶段，避免每次都从第一步重新开始
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getProject } from '../api'
import { toast } from '../store'

const route = useRoute()
const router = useRouter()
const pid = route.params.id
const loading = ref(true)

// current_phase(1-5) → 项目内路由（一律留在项目壳内，禁止指向全局页）
const PHASE_ROUTE = {
  1: '/projects/' + pid + '/assets',      // 资产初始化
  2: '/projects/' + pid + '/planning',    // 分场拆解
  3: '/projects/' + pid + '/editor',      // 分镜编排
  4: '/projects/' + pid + '/editor',      // 异步生成（在编辑器内触发）
  5: '/projects/' + pid + '/editor',      // 合成全集（在编辑器内触发）
}

onMounted(async () => {
  try {
    const p = await getProject(pid)
    const phase = Number(p.current_phase) || 1
    const target = PHASE_ROUTE[phase] || PHASE_ROUTE[1]
    if (route.path !== target) {
      router.replace(target)
    }
  } catch (e) {
    toast('项目打开失败：' + (e.message || e))
    router.replace('/projects/' + pid + '/editor')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="page entry-loading">
    <div class="spinner" />
    <p class="muted">正在打开项目…</p>
  </div>
</template>

<style scoped>
.entry-loading { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 60vh; gap: 14px; }
.spinner { width: 28px; height: 28px; border: 3px solid var(--line); border-top-color: var(--pur); border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.muted { color: var(--sub); font-size: 13px; }
</style>
