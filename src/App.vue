<script setup>
import { useRoute } from 'vue-router'
import { computed } from 'vue'
import SideBar from './components/SideBar.vue'
import TopBar from './components/TopBar.vue'
import DirectorOrb from './components/DirectorOrb.vue'
import P5Celebrate from './components/P5Celebrate.vue'
import { toastState } from './store'

// ============================================================================
// 页面切换过渡（修复「阶段翻页黑屏 / .main 空节点」功能性 bug）
// ----------------------------------------------------------------------------
// 根因：原代码 `<transition mode="out-in">` 包裹 `<keep-alive>` —— out-in 模式
// 要求「离场组件完全结束」后才会插入进场组件；当进出场都是 keep-alive 缓存的
// 阶段页时，这套「先离场、后进场」的串行链会在某些情况下断掉，导致进场组件
// 永远不插入，`.main` 残留 `<!---->` 占位 → 页面黑色未加载（body 透出）。
//
// 修复：
//   1. 移除 `mode="out-in"`，改回默认（同时）过渡 —— 进场组件立即插入，不再
//      依赖「离场完成」这一脆弱的前置条件。
//   2. 改用 JS 钩子 + `:css="false"`，用定时器兜底调用 done，不依赖
//      transitionend，离场/进场必然完成；P5 主题保留异形斜切 + 裁切进场，
//      非 P5 主题退化为纯淡入，视觉不破坏主题。
//   3. keep-alive 置于 transition 外层（缓存键按组件 name），transition 直接
//      包裹 `<component :is>`，确保能正确侦测组件切换并触发动画。
// ============================================================================
const DUR_ENTER = 420
const DUR_LEAVE = 250
const EASE = 'cubic-bezier(.22,1,.36,1)'
const isP5 = () => document.documentElement.classList.contains('t-p5')

function onLeave(el, done) {
  el.style.transition = 'none'
  void el.offsetWidth // 强制 reflow，固定起点
  el.style.transition = `opacity ${DUR_LEAVE}ms ${EASE}, transform ${DUR_LEAVE}ms ${EASE}`
  el.style.opacity = '0'
  el.style.transform = isP5() ? 'translateX(-24px) skewX(6deg)' : 'translateX(-12px)'
  // 不依赖 transitionend → 定时器兜底，离场必然完成
  setTimeout(done, DUR_LEAVE + 30)
}

function onEnter(el, done) {
  el.style.transition = 'none'
  void el.offsetWidth
  const p5 = isP5()
  if (p5) {
    el.style.opacity = '0'
    el.style.transform = 'translateX(24px) skewX(-6deg)'
    el.style.clipPath = 'inset(0 0 0 100%)'
  } else {
    el.style.opacity = '0'
  }
  void el.offsetWidth
  el.style.transition = `opacity ${DUR_ENTER}ms ${EASE}, transform ${DUR_ENTER}ms ${EASE}, clip-path ${DUR_ENTER}ms ${EASE}`
  el.style.opacity = '1'
  el.style.transform = 'none'
  el.style.clipPath = 'inset(0 0 0 0%)' // 可插值（inset→inset），配合 afterEnter 清除
  setTimeout(done, DUR_ENTER + 30)
}

function onAfterEnter(el) {
  // 兜底清理：确保任何残留内联样式都不会隐藏内容
  el.style.transition = ''
  el.style.opacity = ''
  el.style.transform = ''
  el.style.clipPath = ''
}

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
            <transition
              name="p5-page"
              :css="false"
              @leave="onLeave"
              @enter="onEnter"
              @after-enter="onAfterEnter"
            >
              <component :is="Component" />
            </transition>
          </keep-alive>
        </router-view>
      </main>
    </div>
    <div class="toast-wrap">
      <transition-group name="fade">
        <div v-if="toastState.msg" :key="toastState.msg" class="toast" :class="'toast-' + toastState.type">{{ toastState.msg }}</div>
      </transition-group>
    </div>
    <!-- 导演 Agent 悬浮球（仅在五个阶段页显示，组件内按路由自判） -->
    <DirectorOrb />
    <!-- P5 主题 · 任务完成予告状通知 + 星爆（仅 t-p5 激活时生效） -->
    <P5Celebrate />
  </div>
</template>
