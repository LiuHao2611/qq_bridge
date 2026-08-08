<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { healthCheck } from '../api'

const props = defineProps({ pid: { type: String, default: '' } })
const route = useRoute()
const router = useRouter()

const groups = computed(() => {
  const base = [
    {
      name: '主导航', items: [
        { icon: '🏠', label: '首页', path: '/' },
        { icon: '🎬', label: '项目中心', path: '/projects' },
        { icon: '📝', label: '推理监控', path: '/tasks' },
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
      name: '系统', items: [
        { icon: '📋', label: '操作日志', path: '/logs' },
        { icon: '⚙️', label: '设置', path: '/settings' },
        { icon: '🛠️', label: '服务与版本', path: '/system' },
      ],
    },
  ]
  if (props.pid) {
    base.push({
      name: '项目内', items: [
        { icon: '📖', label: '项目策划', path: '/projects/' + props.pid + '/planning' },
        { icon: '🎞️', label: '项目编辑页', path: '/projects/' + props.pid + '/editor' },
        { icon: '🗂️', label: '素材库', path: '/projects/' + props.pid + '/assets' },
        { icon: '🧩', label: '工作流画布', path: '/projects/' + props.pid + '/canvas' },
        { icon: '📽️', label: '人生回忆录编辑页', path: '/memoir/' + props.pid },
      ],
    })
  }
  return base
})

const isActive = (path) => {
  const p = route.path
  if (path === '/') return p === '/'
  if (path === '/projects') return p === '/projects' || p === '/'
  if (path === '/memoir') return p === '/memoir' || p.startsWith('/memoir/')
  if (path.startsWith('/projects/')) return p === path
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
    <div class="logo">
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
        @click="router.push(it.path)"
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
      <div class="user-card" @click="router.push('/membership')">
        <span class="uavatar">小</span>
        <div class="uinfo">
          <div class="uname">小志 <span class="vip-badge">个人版</span></div>
          <div class="usub">🪙 1,999,995,162 积分</div>
        </div>
        <span class="ucaret">›</span>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar { width: 232px; flex: 0 0 232px; background: var(--panel); border-right: 1px solid var(--line); display: flex; flex-direction: column; overflow-y: auto; }
.logo { display: flex; align-items: center; gap: 10px; padding: 18px 18px 14px; position: sticky; top: 0; background-color: var(--panel); z-index: 2; border-bottom: 1px solid var(--line); }
.logo .diamond { color: var(--pink); font-size: 22px; text-shadow: 0 0 12px rgba(255,77,141,.6); }
.logo-txt { font-size: 16px; font-weight: 800; line-height: 1.15; background: var(--grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
.logo-txt small { font-size: 10px; font-weight: 600; opacity: .8; }
.nav-group { font-size: 11px; color: var(--sub); padding: 14px 18px 6px; letter-spacing: 1px; }
.nav-item { display: flex; align-items: center; gap: 10px; padding: 10px 18px; color: var(--sub); font-size: 14px; cursor: pointer; transition: .15s; border-left: 3px solid transparent; }
.nav-item:hover { color: var(--txt); background: #1c1f27; }
.nav-item.active { color: var(--txt); background: #1f2330; border-left-color: var(--pur); }
.nav-item .icon { width: 18px; text-align: center; }
.sidebar-foot-wrap { margin-top: auto; padding: 10px 0 14px; }
.sidebar-foot { display: flex; align-items: center; gap: 8px; padding: 8px 18px; font-size: 12px; color: var(--sub); }
.sidebar-foot .dot { width: 8px; height: 8px; border-radius: 50%; background: #555; }
.sidebar-foot .dot.on { background: var(--green); box-shadow: 0 0 8px var(--green); }
.user-card { display: flex; align-items: center; gap: 10px; margin: 4px 12px 0; padding: 8px 10px; border-radius: 10px; cursor: pointer; transition: .15s; border: 1px solid var(--line); }
.user-card:hover { background: #1c1f27; border-color: var(--pur); }
.uavatar { width: 30px; height: 30px; border-radius: 50%; background: var(--grad); color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 13px; flex: 0 0 30px; }
.uinfo { flex: 1; line-height: 1.25; }
.uname { font-size: 13px; font-weight: 700; color: var(--txt); }
.vip-badge { font-size: 9px; background: #e8c87a; color: #111; padding: 0 4px; border-radius: 4px; font-weight: 800; margin-left: 4px; }
.usub { font-size: 11px; color: var(--sub); }
.ucaret { color: var(--sub); }
</style>
