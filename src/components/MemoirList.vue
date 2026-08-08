<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { listProjects, coverStyle, STATUS_TXT } from '../api'
import { toast } from '../store'

const router = useRouter()
const items = ref([])
const loading = ref(false)

const load = async () => {
  loading.value = true
  try {
    const data = await listProjects({ limit: 100 })
    items.value = Array.isArray(data) ? data : []
  } catch (e) {
    console.warn('memoir list failed', e.message)
    items.value = []
  } finally {
    loading.value = false
  }
}
onMounted(load)

const open = (id) => router.push('/memoir/' + id)
</script>

<template>
  <div class="page memoir-list">
    <div class="page-head">
      <div>
        <h1>人生回忆录</h1>
        <p class="muted">把人生经历 AI 策划成电影感短剧 · 六阶段工作流（资料 → 策划 → 指导板 → 审片 → 视频 → 合成）</p>
      </div>
    </div>

    <div v-if="loading" class="muted">加载中…</div>
    <div v-else-if="!items.length" class="empty">
      <div class="empty-ico">📖</div>
      <div>暂无回忆录项目</div>
      <div class="muted">在「项目中心」创建项目后，即可从此进入回忆录编辑。</div>
    </div>
    <div v-else class="ml-grid">
      <div v-for="p in items" :key="p.project_id || p.id" class="ml-card" @click="open(p.project_id || p.id)">
        <div class="ml-cover" :style="coverStyle(p.project_id || p.id)">
          <span class="ml-badge" :class="(p.is_draft ? 'draft' : 'done')">{{ STATUS_TXT[p.is_draft ? 'draft' : 'done'] || '草稿' }}</span>
        </div>
        <div class="ml-body">
          <div class="ml-title">{{ p.name || p.title }}</div>
          <div class="ml-meta muted">{{ p.updated_at ? new Date((p.updated_at || 0) * 1000).toLocaleDateString() : '' }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { padding: 20px 24px; }
.page-head { margin-bottom: 18px; }
.page-head h1 { margin: 0 0 6px; font-size: 22px; }
.muted { color: var(--sub); font-size: 13px; }
.ml-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 18px; }
.ml-card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; cursor: pointer; transition: .2s; }
.ml-card:hover { border-color: var(--pur); transform: translateY(-2px); }
.ml-cover { height: 130px; position: relative; display: flex; align-items: center; justify-content: center; }
.ml-badge { position: absolute; top: 10px; right: 10px; padding: 3px 9px; border-radius: 6px; font-size: 11px; font-weight: 700; background: rgba(0,0,0,.55); color: var(--sub); }
.ml-badge.done { color: var(--green); }
.ml-body { padding: 12px 14px; }
.ml-title { font-size: 15px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ml-meta { font-size: 12px; margin-top: 5px; }
.empty { text-align: center; color: var(--sub); padding: 60px 0; }
.empty-ico { font-size: 40px; margin-bottom: 10px; }
</style>
