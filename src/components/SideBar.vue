<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { healthCheck } from '../api'

const props = defineProps({ pid: { type: String, default: '' } })
const route = useRoute()
const router = useRouter()

// 「项目中心」tab 路径：会话级（内存 ref，不写 localStorage）记忆「最后一次项目中心上下文路径」。
// 任意时刻位于 /projects 或 /projects/* 都记录当前完整路径；点击 tab 时恢复到该路径。
// 语义覆盖两层需求：
//   ① 项目内（含子页 /projects/:id/assets 等）切到首页再切回 → 停留原项目（用户原始功能，保留）
//   ② 列表页（/projects）切到首页再切回 → 停留列表，不再自动打开历史项目（旧 BUG，已修）
// 关键：仅内存 ref，刷新页面即重置为 /projects，避免旧 BUG（持久化记忆导致刷新/列表访问后自动跳历史项目）。
const lastProjectCenterPath = ref('/projects')
watch(
  () => route.path,
  (p) => {
    if (p === '/projects' || p.startsWith('/projects/')) {
      lastProjectCenterPath.value = p
    }
  },
  { immediate: true }
)

// 点击「项目中心」时【实时】读取内存记忆路径（不把路径固化进 computed 数组，避免竞态）：
//   - 已打开项目（route 在 /projects/:id*）→ 切走再切回仍回到该项目的精确位置
//   - 没打开项目（route 仅为 /projects 列表）→ 回到项目中心主页面
// 仅内存 ref，刷新页面即重置为 /projects（根除旧 BUG：持久化导致刷新/列表访问后自动跳历史项目）。
const goProjectCenter = () => {
  router.push(lastProjectCenterPath.value)
}
const goHome = () => router.push('/')

const groups = computed(() => {
  const base = [
    {
      name: '主导航', items: [
        { icon: '🏠', label: '首页', path: '/' },
        { icon: '🎬', label: '项目中心', path: '/projects', action: goProjectCenter },
        { icon: '📝', label: '推理监控', path: '/tasks' },
      ],
    },
    {
      name: '页面自定义', items: [
        { icon: '🎨', label: '页面自定义', path: '/custom-page' },
      ],
    },
    {
      name: 'AI 创作', items: [
        { icon: '✨', label: '文生图', path: '/txt2img' },
        { icon: '🖼️', label: '图生图', path: '/img2img' },
        { icon: '🎨', label: '创意工坊', path: '/creative' },
        { icon: '📖', label: '人生回忆录', path: '/memoir' },
      ],
    },
    {
      name: '资源管理', items: [
        { icon: '🌐', label: '全局资产库', path: '/global-assets' },
        { icon: '🔍', label: '图片反推', path: '/img2prompt' },
        { icon: '🎯', label: 'LoRA 训练', path: '/lora-train' },
        { icon: '🕒', label: '历史记录', path: '/history' },
      ],
    },
    {
      name: '账户服务', items: [
        { icon: '👑', label: '会员中心', path: '/membership' },
        { icon: '💬', label: '消息中心', path: '/messages' },
        { icon: '❓', label: '帮助与反馈', path: '/help' },
      ],
    },
    {
      name: '数据查询', items: [
        { icon: '📊', label: '我的数据', path: '/my-data' },
      ],
    },
    {
      name: '系统', items: [
        { icon: '📋', label: '操作日志', path: '/logs' },
        { icon: '⚙️', label: '设置', path: '/settings' },
        { icon: '🛠️', label: '服务与版本', path: '/system' },
      ],
    },
  ]
  // 项目内导航已由 ProjectBookShell 翻页按钮承载，侧栏不再重复展开「项目内」分组
  return base
})

const isActive = (path) => {
  const p = route.path
  if (path === '/') return p === '/'
  if (path === '/custom-page') return p === '/custom-page'
  if (path === '/projects') return p === '/projects' || p.startsWith('/projects/')
  if (path === '/memoir') return p === '/memoir' || p.startsWith('/memoir/')
  if (path === '/my-data') return p === '/my-data'
  if (path.startsWith('/projects/')) {
    if (p === path) return true
    // 项目入口 /projects/:id 在其任意子路由下都高亮
    if (/^\/projects\/[^/]+$/.test(path) && p.startsWith(path + '/')) return true
    return false
  }
  return p.startsWith(path)
}

const online = ref(true)
onMounted(async () => {
  try { await healthCheck(); online.value = true }
  catch { online.value = false }
})
</script>

<template>
  <aside class="sidebar">
    <div class="logo" @click="goHome" title="返回首页">
      <span class="diamond">◆</span>
      <span class="logo-txt">万能短剧<br /><small>AI 创作平台</small></span>
    </div>

    <template v-for="g in groups" :key="g.name">
      <div class="nav-group">{{ g.name }}</div>
      <div
        v-for="it in g.items"
        :key="it.path"
        class="nav-item"
        :class="{ active: isActive(it.path) }"
        @click="it.action ? it.action() : router.push(it.path)"
      >
        <span class="icon">{{ it.icon }}</span>
        <span class="label">{{ it.label }}</span>
      </div>
    </template>

    <div class="sidebar-foot-wrap">
      <div class="sidebar-foot">
        <span class="dot" :class="{ on: online }"></span>
        <span>{{ online ? '🟢 系统正常' : '🔴 后端离线' }} · v1.3.0</span>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar { width: 232px; flex: 0 0 232px; background: var(--panel); border-right: 1px solid var(--line); display: flex; flex-direction: column; overflow-y: auto; }
.logo { display: flex; align-items: center; gap: 10px; padding: 18px 18px 14px; position: sticky; top: 0; background-color: var(--panel); z-index: 2; border-bottom: 1px solid var(--line); cursor: pointer; transition: background-color .15s; }
.logo:hover { background-color: var(--card-2); }
.logo .diamond { color: var(--pink); font-size: 22px; text-shadow: 0 0 12px rgba(255,77,141,.6); }
.logo-txt { font-size: 16px; font-weight: 800; line-height: 1.15; background: var(--grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
.logo-txt small { font-size: 10px; font-weight: 600; opacity: .8; }
.nav-group { font-size: 11px; color: var(--sub); padding: 16px 18px 8px; letter-spacing: 1px; }
.nav-item { display: flex; align-items: center; gap: 10px; padding: 10px 14px; margin: 0 10px 3px; color: var(--sub); font-size: 14px; cursor: pointer; transition: .15s; border-left: 3px solid transparent; border-radius: 8px; }
.nav-item:hover { color: var(--txt); background: rgba(255,255,255,.06); }
.nav-item.active { color: var(--txt); background: rgba(138,92,255,.12); border-left-color: var(--pur); }
.nav-item .icon { width: 18px; text-align: center; }
.sidebar-foot-wrap { margin-top: auto; padding: 10px 0 14px; }
.sidebar-foot { display: flex; align-items: center; gap: 8px; padding: 8px 18px; font-size: 12px; color: var(--sub); }
.sidebar-foot .dot { width: 8px; height: 8px; border-radius: 50%; background: #555; }
.sidebar-foot .dot.on { background: var(--green); box-shadow: 0 0 8px var(--green); }
</style>
