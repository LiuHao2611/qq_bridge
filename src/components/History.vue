<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'

const now = Date.now()
const DAY = 86400000
const ago = (d) => now - d * DAY

// ---------- 类型 / 筛选 ----------
const typeTabs = [
  { key: '全部', label: '全部' },
  { key: '视频', label: '视频' },
  { key: '图片', label: '图片' },
  { key: '文本', label: '文本' },
]
const typeFilter = ref('全部')

const projectOptions = ['全部项目', '都市迷局', '岁月的余温', '星河彼岸', '迷雾剧场']
const projectFilter = ref('全部项目')

const timeOptions = [
  { key: '全部', label: '全部' },
  { key: '今天', label: '今天' },
  { key: '近7天', label: '近 7 天' },
  { key: '近30天', label: '近 30 天' },
]
const timeFilter = ref('全部')

// ---------- 本地 mock 历史（覆盖三种类型与多种状态） ----------
const records = ref([
  { id: 'r1', type: '视频', name: '都市迷局 EP03 正片', project: '都市迷局', cost: '4分12秒', ts: ago(0.1), status: '成功' },
  { id: 'r2', type: '图片', name: '雨夜天桥概念图', project: '都市迷局', cost: '38秒', ts: ago(0.4), status: '成功' },
  { id: 'r3', type: '文本', name: '星河彼岸 分镜脚本', project: '星河彼岸', cost: '12秒', ts: ago(0.9), status: '成功' },
  { id: 'r4', type: '视频', name: '岁月的余温 预告片', project: '岁月的余温', cost: '—', ts: ago(1.2), status: '生成中' },
  { id: 'r5', type: '图片', name: '老宅客厅氛围图', project: '岁月的余温', cost: '41秒', ts: ago(2.1), status: '成功' },
  { id: 'r6', type: '视频', name: '迷雾剧场 EP01 片段', project: '迷雾剧场', cost: '—', ts: ago(3.0), status: '失败' },
  { id: 'r7', type: '文本', name: '都市迷局 角色小传', project: '都市迷局', cost: '9秒', ts: ago(5.5), status: '成功' },
  { id: 'r8', type: '图片', name: '海边灯塔黄昏图', project: '星河彼岸', cost: '35秒', ts: ago(8.3), status: '成功' },
  { id: 'r9', type: '视频', name: '星河彼岸 MV', project: '星河彼岸', cost: '3分50秒', ts: ago(11.0), status: '成功' },
  { id: 'r10', type: '文本', name: '迷雾剧场 对白稿', project: '迷雾剧场', cost: '15秒', ts: ago(16.0), status: '成功' },
  { id: 'r11', type: '图片', name: '审讯室冷调图', project: '迷雾剧场', cost: '—', ts: ago(22.0), status: '失败' },
  { id: 'r12', type: '视频', name: '岁月的余温 EP02', project: '岁月的余温', cost: '5分03秒', ts: ago(28.0), status: '成功' },
])

const startOfToday = new Date().setHours(0, 0, 0, 0)

const filtered = computed(() => {
  return records.value.filter((r) => {
    if (typeFilter.value !== '全部' && r.type !== typeFilter.value) return false
    if (projectFilter.value !== '全部项目' && r.project !== projectFilter.value) return false
    if (timeFilter.value === '今天' && r.ts < startOfToday) return false
    if (timeFilter.value === '近7天' && r.ts < now - 7 * DAY) return false
    if (timeFilter.value === '近30天' && r.ts < now - 30 * DAY) return false
    return true
  })
})

const fmtTime = (ts) => {
  const d = new Date(ts)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${p(d.getHours())}:${p(d.getMinutes())}`
}

// ---------- 状态徽标样式 ----------
const statusType = (s) => (s === '成功' ? 'success' : s === '失败' ? 'danger' : 'warning')
const typeTagClass = (t) => (t === '视频' ? 'vt' : t === '图片' ? 'it' : 'tt')

// ---------- 操作（演示 toast） ----------
const view = (r) => ElMessage.info(`查看「${r.name}」（演示）`)
const regen = (r) => {
  r.status = '生成中'
  ElMessage.success(`已重新生成「${r.name}」（演示）`)
}

const grads = [
  'linear-gradient(135deg,#ff4d8d,#8a5cff)',
  'linear-gradient(135deg,#4d9fff,#5c7bff)',
  'linear-gradient(135deg,#3ddc84,#2bbf6a)',
  'linear-gradient(135deg,#ffb020,#ff7a45)',
  'linear-gradient(135deg,#8a5cff,#4d9fff)',
]
const gradOf = (id) => {
  let h = 0
  for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return grads[h % grads.length]
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>🕑 历史记录</h1>
        <p class="muted">全局生成历史 · 共 {{ records.length }} 条，当前显示 {{ filtered.length }} 条</p>
      </div>
    </div>

    <!-- 筛选条 -->
    <div class="filters">
      <div class="tabs">
        <div v-for="t in typeTabs" :key="t.key" class="tab" :class="{ active: typeFilter === t.key }" @click="typeFilter = t.key">
          {{ t.label }}
        </div>
      </div>
      <el-select v-model="projectFilter" style="width:160px">
        <el-option v-for="p in projectOptions" :key="p" :label="p" :value="p" />
      </el-select>
      <el-select v-model="timeFilter" style="width:140px">
        <el-option v-for="t in timeOptions" :key="t.key" :label="t.label" :value="t.key" />
      </el-select>
    </div>

    <!-- 列表 -->
    <div v-if="filtered.length">
      <div v-for="r in filtered" :key="r.id" class="list-row hist-row">
        <div class="thumb" :style="{ background: gradOf(r.id) }">
          <span class="thumb-ico">{{ r.type === '视频' ? '▶' : r.type === '图片' ? '🖼' : '📝' }}</span>
        </div>
        <div class="col grow">
          <div class="row-top">
            <span class="asset-tag" :class="typeTagClass(r.type)">{{ r.type }}</span>
            <span class="r-name">{{ r.name }}</span>
          </div>
          <div class="t">所属项目：{{ r.project }} · 耗时 {{ r.cost }} · {{ fmtTime(r.ts) }}</div>
        </div>
        <el-tag :type="statusType(r.status)" size="small" effect="dark">{{ r.status }}</el-tag>
        <div class="acts">
          <el-button size="small" @click="view(r)">查看</el-button>
          <el-button size="small" type="primary" :disabled="r.status === '生成中'" @click="regen(r)">重新生成</el-button>
        </div>
      </div>
    </div>
    <el-empty v-else description="没有符合条件的历史记录" style="margin-top:40px" />
  </div>
</template>

<style scoped>
.filters { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
.hist-row { align-items: center; }
.col.grow { flex: 1; min-width: 0; }
.row-top { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.r-name { font-weight: 700; font-size: 14px; color: var(--txt); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.thumb {
  width: 56px; height: 56px; flex: 0 0 56px; border-radius: 10px; display: flex; align-items: center; justify-content: center;
}
.thumb-ico { font-size: 20px; color: rgba(255,255,255,.9); }
.asset-tag.vt { background: rgba(255,77,141,.15); color: #ff9cc1; border-color: rgba(255,77,141,.4); }
.asset-tag.it { background: rgba(77,159,255,.15); color: #8fc0ff; border-color: rgba(77,159,255,.4); }
.asset-tag.tt { background: rgba(138,92,255,.15); color: #b79bff; border-color: rgba(138,92,255,.4); }
.acts { display: flex; gap: 8px; flex: 0 0 auto; }
</style>
