<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'

const cats = [
  { key: 'all', label: '全部' },
  { key: 'system', label: '系统通知' },
  { key: 'points', label: '积分变动' },
  { key: 'task', label: '任务完成' },
  { key: 'activity', label: '活动' }
]

const catMeta = {
  system: { icon: '⚙', color: '#4d9fff', tag: '系统' },
  points: { icon: '★', color: '#ffb020', tag: '积分' },
  task: { icon: '✔', color: '#3ddc84', tag: '任务' },
  activity: { icon: '🎁', color: '#ff4d8d', tag: '活动' }
}

const all = [
  { id: 1, cat: 'system', title: '账号安全提醒：检测到新设备登录', time: '2026-08-06 09:12', read: false },
  { id: 2, cat: 'points', title: '每日签到 +20 积分已到账', time: '2026-08-06 08:01', read: false },
  { id: 3, cat: 'task', title: '视频任务「都市逆袭·第3章」渲染完成', time: '2026-08-05 22:40', read: false },
  { id: 4, cat: 'activity', title: '限时活动：邀请好友得至尊SVIP体验卡', time: '2026-08-05 18:00', read: true },
  { id: 5, cat: 'system', title: '系统升级完成，新增1080p导出', time: '2026-08-05 12:30', read: false },
  { id: 6, cat: 'points', title: '文生图任务消耗 -15 积分', time: '2026-08-05 11:05', read: true },
  { id: 7, cat: 'task', title: '长剧「仙缘录」共12章已生成完毕', time: '2026-08-04 20:18', read: false },
  { id: 8, cat: 'activity', title: '会员日福利：今日生成任务双倍积分', time: '2026-08-04 09:00', read: true },
  { id: 9, cat: 'system', title: '您订阅的「每周创作灵感」已推送', time: '2026-08-03 10:00', read: true },
  { id: 10, cat: 'task', title: '角色资产化「林晚」处理完成', time: '2026-08-03 15:22', read: false }
]

const active = ref('all')
const list = ref(all.map(m => ({ ...m })))

const unreadCount = computed(() => list.value.filter(m => !m.read).length)

const filtered = computed(() =>
  active.value === 'all' ? list.value : list.value.filter(m => m.cat === active.value)
)

function markRead(m) {
  if (m.read) return
  m.read = true
  ElMessage({ message: '已标记为已读', type: 'success' })
}
function removeMsg(m) {
  list.value = list.value.filter(x => x.id !== m.id)
  ElMessage({ message: '消息已删除', type: 'info' })
}
function markAll() {
  if (!unreadCount.value) {
    ElMessage({ message: '当前没有未读消息', type: 'info' })
    return
  }
  list.value.forEach(m => (m.read = true))
  ElMessage({ message: `已将 ${unreadCount.value} 条消息标为已读`, type: 'success' })
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>消息中心</h1>
      <div style="display:flex;align-items:center;gap:12px">
        <span class="muted">未读 <b style="color:var(--pink)">{{ unreadCount }}</b></span>
        <button class="btn ghost sm" @click="markAll">一键已读</button>
      </div>
    </div>

    <div class="tabs" style="margin-bottom:18px">
      <div v-for="c in cats" :key="c.key" class="tab" :class="{ active: active === c.key }" @click="active = c.key">
        {{ c.label }}
        <span v-if="c.key !== 'all' && list.filter(m => m.cat === c.key && !m.read).length"
              class="tab-badge">{{ list.filter(m => m.cat === c.key && !m.read).length }}</span>
      </div>
    </div>

    <template v-if="filtered.length">
      <div v-for="m in filtered" :key="m.id" class="msg-row" :class="{ unread: !m.read }">
        <span class="msg-ico" :style="{ background: catMeta[m.cat].color + '22', color: catMeta[m.cat].color }">
          {{ catMeta[m.cat].icon }}
        </span>
        <div class="msg-main">
          <div class="msg-top">
            <span class="msg-title" :class="{ b: !m.read }">
              <span v-if="!m.read" class="dot" />
              {{ m.title }}
            </span>
            <el-tag size="small" effect="dark" :style="{ border: '0', background: catMeta[m.cat].color + '33', color: catMeta[m.cat].color }">
              {{ catMeta[m.cat].tag }}
            </el-tag>
          </div>
          <div class="muted msg-time">{{ m.time }}</div>
        </div>
        <div class="msg-ops">
          <el-button v-if="!m.read" size="small" class="btn ghost sm" @click="markRead(m)">标记已读</el-button>
          <el-button size="small" class="btn ghost sm" @click="removeMsg(m)">删除</el-button>
        </div>
      </div>
    </template>
    <div v-else class="empty">
      <div style="font-size:40px;opacity:.3;margin-bottom:8px">✉</div>
      {{ active === 'all' ? '全部消息已读，暂无新消息' : '该分类暂无消息' }}
    </div>
  </div>
</template>

<style scoped>
.tab-badge {
  display: inline-flex; min-width: 16px; height: 16px; padding: 0 4px; margin-left: 4px;
  align-items: center; justify-content: center; border-radius: 8px; font-size: 11px;
  background: var(--pink); color: #fff; font-weight: 700;
}
.msg-row {
  display: flex; align-items: center; gap: 14px;
  background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 16px; margin-bottom: 10px; transition: .15s;
}
.msg-row.unread { border-color: rgba(255,77,141,.5); background: #1d1822; }
.msg-ico { width: 40px; height: 40px; flex: 0 0 40px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 18px; }
.msg-main { flex: 1; min-width: 0; }
.msg-top { display: flex; align-items: center; gap: 10px; }
.msg-title { font-size: 14px; color: var(--txt); display: flex; align-items: center; gap: 8px; }
.msg-title.b { font-weight: 800; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--pink); flex: 0 0 8px; }
.msg-time { margin-top: 4px; font-size: 12px; }
.msg-ops { display: flex; gap: 8px; flex: 0 0 auto; }
</style>
