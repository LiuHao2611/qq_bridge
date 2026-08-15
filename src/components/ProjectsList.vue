<script setup>
import { ref, computed, onMounted, reactive, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  listProjects, createProject, createSegment, deleteProject, fmtTime, coverStyle,
  STATUS_TXT, COVER_PRESETS, recommendMemory, importMemory,
} from '../api'
import { toast } from '../store'
import { mockHome } from '../mock'

const projects = ref([])
const router = useRouter()

const viewMode = ref('grid')            // grid | list
const filter = ref('all')               // all | short | memoir | fav | draft
const search = ref('')
const sortBy = ref('recent')            // recent | name | progress
const favorites = ref(new Set(JSON.parse(localStorage.getItem('wn_fav') || '[]')))
const loading = ref(false)

const inferType = (p) => {
  return p.project_type === 'memoir' ? 'memoir' : 'short'
}

const adaptProject = (p) => ({
  id: p.project_id,
  title: p.name,
  type: inferType(p),
  status: p.is_draft ? 'draft' : 'done',
  scene_count: p.progress || 0,
  updated_at: (p.updated_at || 0) * 1000,
  raw: p,
})

const load = async () => {
  loading.value = true
  try {
    const data = await listProjects({ limit: 100 })
    if (Array.isArray(data) && data.length) {
      projects.value = data.map(adaptProject)
      loading.value = false
      return
    }
  } catch (e) {
    console.warn('project list backend failed, fallback to mock', e.message)
  }
  projects.value = (mockHome.recent || []).map((p) => ({ ...p, type: inferType(p) }))
  loading.value = false
}

// ---------- 新建项目弹窗 ----------
const createVisible = ref(false)
const createLoading = ref(false)
const createFormRef = ref(null)

const MODEL_OPTIONS = [
  { value: 'seedance-2.0-mini', label: 'Seedance 2.0 Mini' },
  { value: 'seedance-2.0-pro', label: 'Seedance 2.0 Pro' },
  { value: 'kling-1.6', label: '可灵 1.6' },
  { value: 'jimeng-video-3.0', label: '即梦视频 3.0' },
  { value: 'vidu-q3', label: 'Vidu Q3' },
]
const PRESET_OPTIONS = [
  { value: 'pro', label: '创作旗舰 Pro' },
  { value: 'flash-short', label: '创作旗舰 Flash·短' },
  { value: 'flash-long', label: '创作旗舰 Flash·长' },
  { value: 'max', label: '创作旗舰 Max' },
]

const form = reactive({
  name: '',
  project_type: 'short_drama',
  description: '',
  thumbnail_index: 0,
  preset_id: 'flash-long',
  ai_model: 'seedance-2.0-pro',
  resolution: '720p',
  ratio: '16:9',
  duration: 12,
  audio: true,
})

const formRules = {
  name: [{ required: true, message: '请输入项目名称', trigger: 'blur' }],
}

const resetForm = () => {
  form.name = ''
  form.project_type = 'short_drama'
  form.description = ''
  form.thumbnail_index = 0
  form.preset_id = 'flash-long'
  form.ai_model = 'seedance-2.0-pro'
  form.resolution = '720p'
  form.ratio = '16:9'
  form.duration = 12
  form.audio = true
}

const onOpenCreate = () => {
  resetForm()
  selectedMemory.clear()
  createVisible.value = true
}

// ---- P3 M3 长期记忆：新建项目时从历史复用角色卡 / 风格锚点 / LoRA ----
const recommendVisible = ref(false)
const recommendLoading = ref(false)
const recommendItems = ref([])
const selectedMemory = reactive(new Set())
const TYPE_LABEL = { character: '角色卡', style: '风格锚点', lora: 'LoRA' }

const openRecommend = async () => {
  recommendVisible.value = true
  recommendLoading.value = true
  try {
    const items = await recommendMemory()
    recommendItems.value = Array.isArray(items) ? items : []
    if (!recommendItems.value.length) {
      toast('暂无历史资产可复用（请先在其他项目沉淀：设置/资产页「记忆本项目」）')
    }
  } catch (e) {
    recommendItems.value = []
    toast('历史资产加载失败: ' + (e.message || e))
  } finally {
    recommendLoading.value = false
  }
}
const toggleMemory = (id) => {
  if (selectedMemory.has(id)) selectedMemory.delete(id)
  else selectedMemory.add(id)
}
const memoryName = (it) => (it.payload && it.payload.name) || it.memory_id
const confirmRecommend = () => { recommendVisible.value = false }

const estimatedCredits = computed(() => {
  const RES_COEF = { '480p': 0.6, '720p': 1.0, '1080p': 2.0, '2K': 3.0, '4K': 4.0 }
  const base = 180
  const res = RES_COEF[form.resolution] ?? 1.0
  const aud = form.audio ? 1.2 : 1.0
  return Math.ceil(base * form.duration * res * aud)
})

const onCreateProject = async () => {
  const valid = await createFormRef.value.validate().catch(() => false)
  if (!valid) return
  createLoading.value = true
  try {
    const p = await createProject({
      name: form.name.trim() || '未命名项目',
      description: form.description,
      project_type: form.project_type,
      ratio: form.ratio,
      resolution: form.resolution,
      preset_id: form.preset_id,
      is_draft: 1,
    })
    // 持久化封面选择
    localStorage.setItem('wn_thumb_' + p.project_id, String(form.thumbnail_index))

    // 短剧：同步创建首个片段，将项目中心核心参数提前落库
    if (form.project_type === 'short_drama') {
      try {
        await createSegment(p.project_id, {
          title: '片段一',
          duration: form.duration,
          resolution: form.resolution,
          ratio: form.ratio,
          audio_enabled: form.audio ? 1 : 0,
          asset_humanize: 1,
          return_last_frame: 0,
          shot_count: 3,
        })
      } catch (segErr) {
        // 项目已创建，片段失败不阻塞进入编辑器（进入后可重新初始化）
        console.warn('auto create segment failed', segErr)
      }
    }

    createVisible.value = false
    toast('项目创建成功')
    router.push('/projects/' + p.project_id)

    // P3 M3 历史资产复用：创建后立即把勾选的记忆项导入新项目（角色卡深拷贝 / 风格锚点 / LoRA 复制）
    const mids = [...selectedMemory]
    if (mids.length) {
      importMemory(p.project_id, mids)
        .then((r) => toast('已导入 ' + (r?.imported ?? mids.length) + ' 项历史资产到新项目'))
        .catch((err) => toast('历史资产导入失败: ' + (err.message || err)))
    }
  } catch (e) {
    toast('创建失败: ' + (e.message || e))
  } finally {
    createLoading.value = false
  }
}

const open = (id) => router.push('/projects/' + id)
const onDelete = async (p) => {
  if (!confirm('确定删除项目「' + p.title + '」？')) return
  try {
    await deleteProject(p.id)
    toast('已删除')
    load()
  } catch (e) {
    toast('删除失败: ' + (e.message || e))
  }
}
const toggleFav = (p) => {
  const s = favorites.value
  if (s.has(p.id)) s.delete(p.id); else s.add(p.id)
  favorites.value = new Set(s)
  localStorage.setItem('wn_fav', JSON.stringify([...s]))
  toast(s.has(p.id) ? '已收藏' : '已取消收藏')
}

const filtered = computed(() => {
  let list = projects.value
  if (filter.value === 'short') list = list.filter((p) => p.type === 'short')
  else if (filter.value === 'memoir') list = list.filter((p) => p.type === 'memoir')
  else if (filter.value === 'fav') list = list.filter((p) => favorites.value.has(p.id))
  else if (filter.value === 'draft') list = list.filter((p) => (p.status || 'draft') === 'draft')
  if (search.value.trim()) {
    const q = search.value.trim().toLowerCase()
    list = list.filter((p) => (p.title || '').toLowerCase().includes(q))
  }
  list = [...list]
  if (sortBy.value === 'name') list.sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh'))
  else if (sortBy.value === 'progress') list.sort((a, b) => (b.scene_count || 0) - (a.scene_count || 0))
  else list.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))
  return list
})

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="p5-title">我的项目</h1>
        <p class="muted">本地离线 · 免费 AI 漫剧生成工作台</p>
      </div>
      <button class="btn" @click="onOpenCreate">＋ 新建项目</button>
    </div>

    <!-- 工具栏：筛选 / 搜索 / 排序 / 视图切换 -->
    <div class="pl-toolbar">
      <div class="tabs">
        <span class="tab" :class="{ active: filter === 'all' }" @click="filter = 'all'">全部</span>
        <span class="tab" :class="{ active: filter === 'short' }" @click="filter = 'short'">短剧</span>
        <span class="tab" :class="{ active: filter === 'memoir' }" @click="filter = 'memoir'">回忆录</span>
        <span class="tab" :class="{ active: filter === 'fav' }" @click="filter = 'fav'">收藏</span>
        <span class="tab" :class="{ active: filter === 'draft' }" @click="filter = 'draft'">草稿</span>
      </div>
      <div class="pl-right">
        <input class="pl-search" v-model="search" placeholder="搜索项目名…" />
        <select class="pl-select" v-model="sortBy">
          <option value="recent">最近</option>
          <option value="name">名称</option>
          <option value="progress">进度</option>
        </select>
        <div class="tabs view-toggle">
          <span class="tab" :class="{ active: viewMode === 'grid' }" @click="viewMode = 'grid'">▦ 网格</span>
          <span class="tab" :class="{ active: viewMode === 'list' }" @click="viewMode = 'list'">☰ 列表</span>
        </div>
      </div>
    </div>

    <!-- 加载动画：首次打开项目中心 / 拉取列表时 -->
    <div v-if="loading" class="pl-loading">
      <div class="spinner" />
      <p>正在加载项目…</p>
    </div>

    <!-- 网格视图 -->
    <div v-else-if="viewMode === 'grid' && filtered.length" class="pl-grid">
      <div v-for="p in filtered" :key="p.id" class="pl-card" @click="open(p.id)">
        <div class="pl-cover" :style="coverStyle(p.id)">
          <span class="pl-badge" :class="p.status">{{ STATUS_TXT[p.status] || '草稿' }}</span>
          <span class="pl-type">{{ p.type === 'memoir' ? '回忆录' : '短剧' }}</span>
          <button class="pl-fav" :class="{ on: favorites.has(p.id) }" @click.stop="toggleFav(p)">★</button>
        </div>
        <div class="pl-body">
          <div class="pl-title">{{ p.title }}</div>
          <div class="pl-meta">
            <span>{{ p.scene_count }} 镜头</span>
            <span>{{ fmtTime(p.updated_at) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 列表视图 -->
    <div v-else-if="viewMode === 'list' && filtered.length" class="pl-list">
      <div v-for="p in filtered" :key="p.id" class="pl-row" @click="open(p.id)">
        <div class="pl-thumb" :style="coverStyle(p.id)">{{ p.title.slice(0, 1) }}</div>
        <div class="pl-info">
          <div class="pl-title">{{ p.title }}</div>
          <div class="pl-meta">{{ p.type === 'memoir' ? '回忆录' : '短剧' }} · {{ p.scene_count }} 镜头 · {{ fmtTime(p.updated_at) }}</div>
        </div>
        <div class="pl-status" :class="p.status">{{ STATUS_TXT[p.status] || '草稿' }}</div>
        <button class="pl-del" @click.stop="onDelete(p)">删除</button>
      </div>
    </div>

    <div v-else class="empty">暂无项目</div>

    <!-- 新建项目弹窗 -->
    <el-dialog
      v-model="createVisible"
      title="新建项目"
      width="720"
      :close-on-click-modal="false"
      destroy-on-close
      class="create-dialog"
      align-center
    >
      <el-form ref="createFormRef" :model="form" :rules="formRules" label-position="top" class="create-form">
        <!-- 项目类型 -->
        <div class="form-section">
          <div class="section-label">项目类型</div>
          <div class="type-tabs">
            <div
              class="type-tab"
              :class="{ active: form.project_type === 'short_drama' }"
              @click="form.project_type = 'short_drama'"
            >
              <div class="type-icon">🎬</div>
              <div class="type-name">短剧</div>
              <div class="type-desc">多片段 / 分镜式视频生成</div>
            </div>
            <div
              class="type-tab"
              :class="{ active: form.project_type === 'memoir' }"
              @click="form.project_type = 'memoir'"
            >
              <div class="type-icon">📖</div>
              <div class="type-name">回忆录</div>
              <div class="type-desc">人生故事 / 时光相册</div>
            </div>
          </div>
        </div>

        <!-- 基础信息 -->
        <div class="form-section">
          <div class="section-label">基础信息</div>
          <div class="form-row">
            <el-form-item label="项目名称" prop="name" class="flex-2">
              <el-input v-model="form.name" placeholder="给项目起个名字" clearable maxlength="40" show-word-limit />
            </el-form-item>
            <el-form-item label="预设套餐" class="flex-1">
              <el-select v-model="form.preset_id" style="width: 100%">
                <el-option v-for="o in PRESET_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="项目描述（选填）">
            <el-input v-model="form.description" type="textarea" :rows="2" placeholder="一句话描述这个项目的内容或用途" />
          </el-form-item>
        </div>

        <!-- 封面缩略图 -->
        <div class="form-section">
          <div class="section-label">封面缩略图</div>
          <div class="thumb-grid">
            <div
              v-for="(t, i) in COVER_PRESETS"
              :key="i"
              class="thumb-item"
              :class="{ active: form.thumbnail_index === i }"
              :style="{ background: t.bg }"
              @click="form.thumbnail_index = i"
            >
              <span class="thumb-name">{{ t.name }}</span>
              <span v-if="form.thumbnail_index === i" class="thumb-check">✓</span>
            </div>
          </div>
        </div>

        <!-- 核心参数 -->
        <div class="form-section">
          <div class="section-label">核心参数</div>
          <div class="form-row">
            <el-form-item label="画面比例" class="flex-1">
              <el-select v-model="form.ratio" style="width: 100%">
                <el-option label="16:9 横屏" value="16:9" />
                <el-option label="9:16 竖屏" value="9:16" />
                <el-option label="1:1 方形" value="1:1" />
                <el-option label="4:3 标准" value="4:3" />
                <el-option label="21:9 电影" value="21:9" />
              </el-select>
            </el-form-item>
            <el-form-item label="分辨率" class="flex-1">
              <el-select v-model="form.resolution" style="width: 100%">
                <el-option label="480p" value="480p" />
                <el-option label="720p" value="720p" />
                <el-option label="1080p" value="1080p" />
                <el-option label="2K" value="2K" />
                <el-option label="4K" value="4K" />
              </el-select>
            </el-form-item>
          </div>

          <template v-if="form.project_type === 'short_drama'">
            <div class="form-row">
              <el-form-item label="AI 视频模型" class="flex-2">
                <el-select v-model="form.ai_model" style="width: 100%">
                  <el-option v-for="o in MODEL_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
              </el-form-item>
              <el-form-item label="片段时长" class="flex-1">
                <el-select v-model="form.duration" style="width: 100%">
                  <el-option label="5 秒" :value="5" />
                  <el-option label="8 秒" :value="8" />
                  <el-option label="10 秒" :value="10" />
                  <el-option label="12 秒" :value="12" />
                  <el-option label="15 秒" :value="15" />
                </el-select>
              </el-form-item>
            </div>
            <div class="form-row">
              <el-form-item class="flex-1">
                <template #label>
                  <span>生成音频</span>
                  <el-switch v-model="form.audio" inline-prompt active-text="开" inactive-text="关" style="margin-left: 10px" />
                </template>
                <div class="hint">开启后片段将携带 AI 配音与背景音乐</div>
              </el-form-item>
              <el-form-item label="预计消耗积分" class="flex-1">
                <div class="credit-preview">
                  <b>{{ estimatedCredits }}</b>
                  <span>积分 / 片段</span>
                </div>
              </el-form-item>
            </div>
          </template>

          <template v-else>
            <div class="form-row">
              <el-form-item label="预计故事板数" class="flex-1">
                <el-select v-model="form.duration" style="width: 100%">
                  <el-option label="4 板" :value="4" />
                  <el-option label="6 板" :value="6" />
                  <el-option label="8 板" :value="8" />
                  <el-option label="10 板" :value="10" />
                </el-select>
              </el-form-item>
              <el-form-item label="预计消耗积分" class="flex-1">
                <div class="credit-preview">
                  <b>{{ estimatedCredits }}</b>
                  <span>积分 / 章节</span>
                </div>
              </el-form-item>
            </div>
          </template>
        </div>

        <!-- P3 M3 历史资产复用：新建项目时从其他项目引入角色卡 / 风格锚点 / LoRA -->
        <div class="form-section">
          <div class="section-label">历史资产复用（可选）</div>
          <div class="mem-reuse">
            <span class="muted">从已有项目沉淀的长期记忆中，带入角色卡 / 风格锚点 / LoRA，新项目开局即有积累。</span>
            <div class="mem-reuse-row">
              <button class="btn ghost" type="button" @click="openRecommend">📦 浏览历史资产</button>
              <span v-if="selectedMemory.size" class="mem-count">已选 {{ selectedMemory.size }} 项</span>
            </div>
            <div v-if="selectedMemory.size" class="mem-chips">
              <span v-for="id in [...selectedMemory]" :key="id" class="mem-chip">
                {{ (recommendItems.find((x) => x.memory_id === id) && memoryName(recommendItems.find((x) => x.memory_id === id))) || id }}
                <b @click="toggleMemory(id)">×</b>
              </span>
            </div>
          </div>
        </div>
      </el-form>

      <template #footer>
        <div class="dialog-footer">
          <el-button @click="createVisible = false">取消</el-button>
          <el-button type="primary" :loading="createLoading" @click="onCreateProject">立即创建</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- P3 M3 历史资产浏览弹窗（跨项目复用）-->
    <el-dialog v-model="recommendVisible" title="浏览历史资产" width="640" align-center>
      <div v-loading="recommendLoading">
        <div v-if="!recommendItems.length" class="empty muted">暂无历史资产（请先在其它项目的设置/资产页「沉淀本项目记忆」）</div>
        <div v-else class="mem-list">
          <label v-for="it in recommendItems" :key="it.memory_id" class="mem-item" :class="{ on: selectedMemory.has(it.memory_id) }">
            <input type="checkbox" :checked="selectedMemory.has(it.memory_id)" @change="toggleMemory(it.memory_id)" />
            <span class="mem-type" :class="it.type">{{ TYPE_LABEL[it.type] || it.type }}</span>
            <span class="mem-name">{{ memoryName(it) }}</span>
            <span class="mem-meta">来自项目 {{ it.source_project_id?.slice(0, 8) }} · 复用 {{ it.usage_count || 0 }} 次</span>
          </label>
        </div>
      </div>
      <template #footer>
        <div class="dialog-footer">
          <span class="muted">已选 {{ selectedMemory.size }} 项</span>
          <el-button type="primary" @click="confirmRecommend">确认选择</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page { padding: 20px 24px; }
.page-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }
.page-head h1 { margin: 0; font-size: 22px; }
.muted { color: var(--sub); font-size: 13px; }
.btn { background: var(--grad); color: #fff; border: 0; border-radius: 10px; padding: 8px 16px; font-size: 13px; font-weight: 700; cursor: pointer; }
.btn:hover { opacity: .9; }

.pl-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }
.tabs { display: flex; gap: 6px; background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 4px; }
.tab { padding: 6px 12px; border-radius: 8px; font-size: 13px; color: var(--sub); cursor: pointer; transition: .15s; }
.tab:hover { color: var(--txt); }
.tab.active { background: var(--card-2); color: var(--txt); }
.pl-right { display: flex; gap: 10px; align-items: center; }
.pl-search { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px; color: var(--txt); font-size: 13px; outline: none; width: 180px; }
.pl-search:focus { border-color: var(--pur); }
.pl-select { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 7px 8px; color: var(--txt); font-size: 13px; outline: none; }

.pl-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 18px; }
.pl-card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; cursor: pointer; transition: .2s; }
.pl-card:hover { border-color: var(--pur); transform: translateY(-2px); }
.pl-cover { height: 130px; position: relative; display: flex; align-items: center; justify-content: center; }
.pl-badge { position: absolute; top: 10px; right: 10px; padding: 3px 9px; border-radius: 6px; font-size: 11px; font-weight: 700; background: rgba(0,0,0,.55); color: var(--sub); }
.pl-badge.done { color: var(--green); }
.pl-badge.rendering { color: var(--blue); }
.pl-type { position: absolute; top: 10px; left: 10px; padding: 2px 8px; border-radius: 6px; font-size: 11px; background: rgba(0,0,0,.5); color: var(--txt); }
.pl-fav { position: absolute; bottom: 10px; right: 10px; background: transparent; border: 0; color: var(--sub); font-size: 18px; cursor: pointer; }
.pl-fav.on { color: var(--warn); }
.pl-body { padding: 12px 14px; }
.pl-title { font-size: 15px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pl-meta { display: flex; justify-content: space-between; font-size: 12px; color: var(--sub); margin-top: 5px; }

.pl-list { display: flex; flex-direction: column; gap: 10px; }
.pl-row { display: flex; align-items: center; gap: 14px; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; cursor: pointer; transition: .15s; }
.pl-row:hover { border-color: var(--pur); }
.pl-thumb { width: 48px; height: 48px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-weight: 800; color: #fff; }
.pl-info { flex: 1; min-width: 0; }
.pl-status { font-size: 12px; font-weight: 700; color: var(--sub); }
.pl-status.done { color: var(--green); }
.pl-del { background: transparent; border: 1px solid var(--line); color: var(--sub); border-radius: 8px; padding: 5px 10px; font-size: 12px; cursor: pointer; }
.pl-del:hover { border-color: var(--pink); color: var(--pink); }
.empty { text-align: center; color: var(--sub); padding: 60px 0; }

/* ---------- 加载动画（首次打开项目中心） ---------- */
.pl-loading { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; padding: 90px 0; }
.pl-loading p { color: var(--sub); font-size: 13px; margin: 0; }
.spinner { width: 38px; height: 38px; border: 4px solid var(--line); border-top-color: var(--pur, #8a5cff); border-radius: 50%; animation: pl-spin .8s linear infinite; }
@keyframes pl-spin { to { transform: rotate(360deg); } }

/* ---------- 新建项目弹窗 ---------- */
:deep(.create-dialog) { --el-dialog-bg-color: var(--card); --el-dialog-border-radius: 16px; }
:deep(.create-dialog .el-dialog__header) { margin-right: 0; padding: 20px 24px 12px; border-bottom: 1px solid var(--line); }
:deep(.create-dialog .el-dialog__title) { color: var(--txt); font-weight: 700; font-size: 16px; }
:deep(.create-dialog .el-dialog__body) { padding: 20px 24px; color: var(--txt); }
:deep(.create-dialog .el-dialog__footer) { padding: 14px 24px 20px; border-top: 1px solid var(--line); }
:deep(.create-dialog .el-form-item__label) { color: var(--sub); font-size: 12px; padding-bottom: 4px; }
:deep(.create-dialog .el-input__wrapper),
:deep(.create-dialog .el-textarea__inner) { background: var(--bg-2); box-shadow: 0 0 0 1px var(--line) inset; border-radius: 8px; }
:deep(.create-dialog .el-input__inner),
:deep(.create-dialog .el-textarea__inner) { color: var(--txt); }
:deep(.create-dialog .el-input__wrapper.is-focus) { box-shadow: 0 0 0 1px var(--pur) inset; }

.form-section + .form-section { margin-top: 20px; }
.section-label { font-size: 13px; font-weight: 700; color: var(--txt); margin-bottom: 12px; }

.type-tabs { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.type-tab {
  background: var(--bg-2); border: 1px solid var(--line); border-radius: 12px;
  padding: 16px; cursor: pointer; transition: .15s; position: relative;
}
.type-tab:hover { border-color: var(--pur); }
.type-tab.active { border-color: var(--pur); background: rgba(138,92,255,.08); }
.type-tab.active::after {
  content: '✓'; position: absolute; top: 10px; right: 10px;
  width: 20px; height: 20px; border-radius: 50%; background: var(--pur);
  color: #fff; font-size: 12px; display: flex; align-items: center; justify-content: center;
}
.type-icon { font-size: 28px; margin-bottom: 8px; }
.type-name { font-size: 15px; font-weight: 700; color: var(--txt); }
.type-desc { font-size: 12px; color: var(--sub); margin-top: 4px; }

.form-row { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }
.flex-1 { grid-column: span 1; }
.flex-2 { grid-column: span 2; }

.thumb-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.thumb-item {
  height: 76px; border-radius: 10px; cursor: pointer; position: relative;
  display: flex; align-items: center; justify-content: center;
  border: 2px solid transparent; transition: .15s; overflow: hidden;
}
.thumb-item:hover { transform: translateY(-2px); }
.thumb-item.active { border-color: var(--pur); box-shadow: 0 0 0 2px rgba(138,92,255,.25); }
.thumb-name { color: #fff; font-size: 13px; font-weight: 700; text-shadow: 0 1px 3px rgba(0,0,0,.4); }
.thumb-check {
  position: absolute; top: 6px; right: 6px; width: 18px; height: 18px;
  border-radius: 50%; background: var(--pur); color: #fff; font-size: 10px;
  display: flex; align-items: center; justify-content: center;
}

.hint { font-size: 12px; color: var(--sub); margin-top: 4px; }
.credit-preview {
  height: 40px; display: flex; align-items: baseline; gap: 8px;
  background: var(--bg-2); border: 1px solid var(--line); border-radius: 8px;
  padding: 0 12px; color: var(--sub); font-size: 13px;
}
.credit-preview b { color: var(--pink); font-size: 18px; }

.dialog-footer { display: flex; justify-content: flex-end; gap: 10px; }
:deep(.create-dialog .el-button) { border-radius: 8px; }
:deep(.create-dialog .el-button--primary) { background: var(--grad); border: 0; }

/* P3 M3 历史资产复用 */
.mem-reuse { display: flex; flex-direction: column; gap: 10px; }
.mem-reuse-row { display: flex; align-items: center; gap: 12px; }
.btn.ghost { background: var(--card-2); color: var(--txt); border: 1px solid var(--line); }
.mem-count { font-size: 12px; color: var(--pur); font-weight: 700; }
.mem-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.mem-chip { font-size: 11px; padding: 3px 8px; border-radius: 6px; background: var(--bg-2); border: 1px solid var(--line); color: var(--txt); display: inline-flex; align-items: center; gap: 6px; }
.mem-chip b { cursor: pointer; color: var(--pink); font-size: 13px; line-height: 1; }
.mem-list { display: flex; flex-direction: column; gap: 8px; max-height: 60vh; overflow: auto; }
.mem-item { display: flex; align-items: center; gap: 10px; background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; cursor: pointer; }
.mem-item.on { border-color: var(--pur); background: rgba(138,92,255,.08); }
.mem-type { font-size: 11px; padding: 2px 8px; border-radius: 6px; color: #fff; flex: 0 0 auto; }
.mem-type.character { background: #6d28d9; }
.mem-type.style { background: #0ea5e9; }
.mem-type.lora { background: #d94f3a; }
.mem-name { font-size: 13px; font-weight: 700; color: var(--txt); }
.mem-meta { font-size: 11px; color: var(--sub); margin-left: auto; }
.muted { color: var(--sub); font-size: 12px; }

@media (max-width: 700px) {
  .form-row { grid-template-columns: 1fr; }
  .flex-2 { grid-column: span 1; }
  .thumb-grid { grid-template-columns: repeat(2, 1fr); }
  .type-tabs { grid-template-columns: 1fr; }
}
</style>