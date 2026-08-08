<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ref, onMounted } from 'vue'
import { mockNotifications } from '../mock'
import { toast } from '../store'
import { useTheme } from '../composables/useTheme'
import { listNotifications, markAllNotificationsRead, markNotificationRead, deleteNotification } from '../api'

const route = useRoute()
const router = useRouter()

const PAGE_TITLE = {
  '/': '首页',
  '/projects': '项目中心',
  '/tasks': '推理监控',
  '/txt2img': '文生图',
  '/img2img': '图生图',
  '/creative': '创意工坊',
  '/memoir': '人生回忆录',
  '/global-assets': '全局资产库',
  '/img2prompt': '图片反推',
  '/history': '历史记录',
  '/membership': '会员中心',
  '/messages': '消息中心',
  '/help': '帮助与反馈',
  '/logs': '操作日志',
  '/settings': '设置',
}
const pageTitle = computed(() => {
  const p = route.path
  if (p.startsWith('/projects/')) {
    if (p.endsWith('/assets')) return '素材库'
    if (p.endsWith('/canvas')) return '工作流画布'
    if (p.endsWith('/production')) return '生成生产'
    return '项目中心 · 片段编辑'
  }
  if (p.startsWith('/memoir/')) return '人生回忆录 · 编辑页'
  return PAGE_TITLE[p] || '万能短剧'
})

// ---- 通知：优先真实后端（messages 表），失败回退演示数据 ----
const notifs = ref([])
const notifSource = ref('demo')
const notifOpen = ref(false)
const { isDark, toggle: toggleTheme } = useTheme()

const fmtTime = (ts) => {
  if (!ts) return ''
  const ms = (typeof ts === 'number' && ts < 1e12) ? ts * 1000 : ts
  const d = new Date(ms)
  if (isNaN(d.getTime())) return String(ts)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
const loadNotifs = async () => {
  try {
    const list = await listNotifications()
    notifs.value = (list || []).map((m) => ({
      id: m.message_id, type: m.type, title: m.title, time: fmtTime(m.created_at), read: !!m.is_read,
    }))
    notifSource.value = 'real'
  } catch (e) {
    notifs.value = (mockNotifications || []).map((n) => ({ ...n }))
    notifSource.value = 'demo'
  }
}
const unread = computed(() => notifs.value.filter((n) => !n.read).length)
const onNotifItem = async (n) => {
  if (n.read) return
  if (notifSource.value === 'real') {
    try { await markNotificationRead(n.id) } catch (e) {}
    await loadNotifs()
  } else {
    n.read = true
  }
}
const markAllRead = async () => {
  if (notifSource.value === 'real') {
    try { await markAllNotificationsRead() } catch (e) {}
    await loadNotifs()
  } else {
    notifs.value = notifs.value.map((n) => ({ ...n, read: true }))
  }
}
const removeNotif = async (n) => {
  if (notifSource.value === 'real') {
    try { await deleteNotification(n.id) } catch (e) {}
    await loadNotifs()
  } else {
    const i = notifs.value.findIndex((x) => x.id === n.id)
    if (i > -1) notifs.value.splice(i, 1)
  }
}
onMounted(loadNotifs)

const goMembership = () => { router.push('/membership'); toast('会员中心（演示）') }
const onUser = (c) => {
  if (c === 'profile') toast('个人中心（演示）')
  else if (c === 'switch') toast('切换账号（演示）')
  else if (c === 'logout') toast('退出登录（演示）')
}
</script>

<template>
  <header class="topbar">
    <div class="topbar-left">
      <span class="crumb-root">万能短剧</span>
      <span class="crumb-sep">/</span>
      <span class="crumb-cur">{{ pageTitle }}</span>
    </div>
    <div class="topbar-right">
      <button class="vip-btn" @click="goMembership">👑 个人版</button>
      <div class="points" title="我的积分（本地模拟）">
        <span class="coin">🪙</span><b>1,999,995,162</b><span class="pts-txt">积分</span>
      </div>
      <button class="icon-btn theme-btn" :title="isDark ? '切换浅色主题' : '切换深色主题'" @click="toggleTheme">
        {{ isDark ? '🌙' : '☀️' }}
      </button>
      <el-badge :value="unread" :hidden="unread === 0" class="bell">
        <el-popover :visible="notifOpen" placement="bottom-end" :width="320" trigger="manual">
          <template #reference>
            <button class="icon-btn" @click="notifOpen = !notifOpen">🔔</button>
          </template>
          <div class="notif-head">通知
            <span v-if="notifSource === 'demo'" class="nt-demo">演示数据</span>
          </div>
          <div class="notif-list">
            <div v-for="n in notifs" :key="n.id" class="notif-item" :class="'nt-' + n.type" @click="onNotifItem(n)">
              <div class="nt-title">{{ n.title }} <span v-if="!n.read" class="nt-dot" /></div>
              <div class="nt-time">{{ n.time }}</div>
              <button class="nt-del" title="删除通知" @click.stop="removeNotif(n)">×</button>
            </div>
            <div v-if="!notifs.length" class="notif-empty">暂无通知</div>
          </div>
          <div class="notif-foot" @click="markAllRead">全部已读</div>
        </el-popover>
      </el-badge>
      <el-dropdown trigger="click" @command="onUser">
        <div class="user">
          <span class="avatar">小</span>
          <span class="uname">小志</span>
          <span class="caret">▾</span>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="profile">个人中心</el-dropdown-item>
            <el-dropdown-item command="switch">切换账号</el-dropdown-item>
            <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<style scoped>
.topbar-left { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.crumb-root { color: var(--sub); }
.crumb-sep { color: var(--line); }
.crumb-cur { color: var(--txt); font-weight: 700; }
.topbar-right { display: flex; align-items: center; gap: 14px; }
.vip-btn {
  background: #111; color: #e8c87a; border: 1px solid #5a4a1e; border-radius: 20px;
  padding: 6px 14px; font-size: 13px; font-weight: 800; cursor: pointer; transition: .2s;
}
.vip-btn:hover { border-color: #e8c87a; box-shadow: 0 0 10px rgba(232,200,122,.3); }
.points { display: flex; align-items: center; gap: 4px; font-size: 13px; color: var(--sub); }
.points .coin { font-size: 14px; }
.points b { color: var(--txt); }
.pts-txt { font-size: 12px; }
.icon-btn {
  background: transparent; border: 0; font-size: 18px; cursor: pointer; color: var(--txt);
  width: 34px; height: 34px; border-radius: 8px;
}
.icon-btn:hover { background: var(--card-2); }
.bell :deep(.el-badge__content) { border: 0; }
.user { display: flex; align-items: center; gap: 6px; cursor: pointer; padding: 4px 6px; border-radius: 8px; }
.user:hover { background: var(--card-2); }
.avatar { width: 28px; height: 28px; border-radius: 50%; background: var(--grad); color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 13px; }
.uname { font-size: 13px; color: var(--txt); }
.caret { color: var(--sub); font-size: 11px; }
.notif-head { font-weight: 800; font-size: 14px; padding: 4px 2px 10px; border-bottom: 1px solid var(--el-border-color); }
.notif-list { max-height: 260px; overflow: auto; }
.notif-item { padding: 9px 4px; border-bottom: 1px solid var(--el-border-color-lighter); }
.notif-item:last-child { border-bottom: 0; }
.nt-title { font-size: 13px; color: var(--txt); }
.nt-time { font-size: 11px; color: var(--sub); margin-top: 3px; }
.nt-success .nt-title { color: var(--green); }
.nt-warn .nt-title { color: var(--warn); }
.notif-foot { text-align: center; margin-top: 8px; color: var(--blue); font-size: 13px; cursor: pointer; }
.nt-demo { float: right; font-size: 11px; color: var(--sub); background: var(--card-2); border-radius: 6px; padding: 1px 6px; font-weight: 400; }
.nt-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--pink); margin-left: 6px; vertical-align: middle; }
.nt-del { position: absolute; right: 6px; top: 8px; width: 18px; height: 18px; line-height: 16px; border: 0; border-radius: 50%; background: transparent; color: var(--sub); font-size: 16px; cursor: pointer; }
.nt-del:hover { background: var(--card-2); color: var(--pink); }
.notif-item { position: relative; padding-right: 26px; }
.notif-empty { text-align: center; color: var(--sub); font-size: 13px; padding: 16px 0; }
</style>
