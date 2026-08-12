<script setup>
import { useRoute } from 'vue-router'
import { computed } from 'vue'
import SideBar from './components/SideBar.vue'
import TopBar from './components/TopBar.vue'
import DirectorOrb from './components/DirectorOrb.vue'
import { toastState } from './store'

const route = useRoute()
const pid = computed(() => route.params.id || '')

// keep-alive 白名单（响应式 · 基于当前路由）：
// - 创作者三类工具页（始终缓存）：Txt2Img / Img2Img / Creative
// - 五阶段页（仅在 /projects/:id/* 时缓存，离开到项目中心主页 /projects 时清空缓存）：
//   AssetsPhase / PlanningPhase / FragmentEditorPhase / ProductionPhase / CanvasPhase
//
// 缓存机制说明（回答「缓存路径」）：
//   1. 数据载体：Vue 3 `<keep-alive>` 内存里的组件实例缓存（DOM+状态+已加载数据都在内存中）
//   2. 缓存键：组件 `defineOptions({ name })`（见各阶段组件首行）
//   3. 缓存作用域：当前项目内 `/projects/:id/*`；用户点侧栏「项目中心」回到 `/projects` 列表页，
//      `cachedViews` 立即把五个阶段名剔除 → keep-alive 检测到 name 不在 include 列表 → 销毁缓存实例 → 下次再进入该项目页面走全新挂载、重新拉取数据
//   4. 持久性：纯内存，刷新浏览器即清空（不写 localStorage/sessionStorage，符合侧栏 tab「刷新即重置」语义）
//   5. 跨项目保护：必须先回 /projects 才能切换项目；进入新项目即全新挂载，无 stale-data 风险
const TOOL_NAMES = ['Txt2Img', 'Img2Img', 'Creative']
const PHASE_NAMES = ['AssetsPhase', 'PlanningPhase', 'FragmentEditorPhase', 'ProductionPhase', 'CanvasPhase']

const cachedViews = computed(() => {
  const p = route.path
  // /projects/:id/*（含 ProjectEntry /projects/:id）→ 缓存五个阶段页
  if (p.startsWith('/projects/')) {
    return [...TOOL_NAMES, ...PHASE_NAMES]
  }
  // 其他（含 /projects 列表、首页 /、任务中心 /tasks、/settings 等）→ 不缓存阶段页
  return TOOL_NAMES
})
</script>

<template>
  <div id="app-shell">
    <SideBar :pid="pid" />
    <div class="app-right">
      <TopBar :pid="pid" />
      <main class="main">
        <router-view v-slot="{ Component }">
          <keep-alive :include="cachedViews" :max="5">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </main>
    </div>
    <div class="toast-wrap">
      <transition-group name="fade">
        <div v-if="toastState.msg" :key="toastState.msg" class="toast">{{ toastState.msg }}</div>
      </transition-group>
    </div>
    <!-- 导演 Agent 悬浮球（仅在五个阶段页显示，组件内按路由自判） -->
    <DirectorOrb />
  </div>
</template>
