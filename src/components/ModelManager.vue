<template>
  <div class="model-manager">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="新模型零代码接入（P4 M1）"
      description="放模型到 ComfyUI 模型目录 → 点「扫描」自动发现节点族与能力维度 → 模板自动匹配 → 补声明后「接入」即可被导演链路选用，无需改代码。"
    />

    <div class="mm-actions">
      <el-button type="primary" :loading="scanning" @click="doScan">扫描未声明模型</el-button>
      <el-button :loading="loadingList" @click="refreshList">刷新已接入</el-button>
      <span class="mm-hint" v-if="!comfyHint">提示：需先启动 ComfyUI(:8188) 才能扫描到磁盘新模型。</span>
    </div>

    <!-- 未声明模型（待接入） -->
    <section class="mm-section">
      <h4>未声明模型（放模型后待接入）· {{ pending.length }}</h4>
      <el-empty v-if="!pending.length" description="暂无未声明的模型（或 ComfyUI 未启动）" />
      <el-card v-for="m in pending" :key="m.name" class="mm-card" shadow="hover">
        <div class="mm-card-head">
          <span class="mm-name">{{ m.name }}</span>
          <el-tag size="small" :type="typeTag(m.type)">{{ m.type }}</el-tag>
          <el-tag v-if="m.node_family" size="small" type="success">族:{{ m.node_family }}</el-tag>
        </div>
        <div class="mm-card-body">
          <span class="mm-label">自动标签：</span>
          <el-tag v-for="t in (m.auto_tags||[])" :key="t" size="small" effect="plain" class="mm-tag">{{ t }}</el-tag>
          <el-button size="small" type="primary" plain :loading="discovering===m.name" @click="doDiscover(m.name)">自动发现并接入</el-button>
        </div>
        <!-- 发现结果 -->
        <div v-if="discovered[m.name]" class="mm-discover">
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="节点族">{{ discovered[m.name].node_family || '—' }}</el-descriptions-item>
            <el-descriptions-item label="节点类">{{ discovered[m.name].node_class || '—' }}</el-descriptions-item>
            <el-descriptions-item label="首尾帧分级">
              {{ discovered[m.name].frame_anchor_grade ? discovered[m.name].frame_anchor_grade.mode : '不适用' }}
            </el-descriptions-item>
            <el-descriptions-item label="模板匹配">
              <template v-if="discovered[m.name].template.status==='matched'">
                <el-tag type="success" size="small">命中 {{ discovered[m.name].template.template_id }}</el-tag>
              </template>
              <template v-else>
                <el-alert
                  type="warning"
                  :closable="false"
                  show-icon
                  :title="'全新族，暂无内置模板'"
                  :description="discovered[m.name].template.guide"
                />
              </template>
            </el-descriptions-item>
          </el-descriptions>
          <div class="mm-form" v-if="discovered[m.name].template.status==='matched'">
            <el-input v-model="notes[m.name]" size="small" placeholder="补充备注（可选）" class="mm-note" />
            <el-select v-model="tags[m.name]" multiple filterable allow-create default-first-option
                       placeholder="补充语义标签（可选）" size="small" class="mm-tags">
              <el-option v-for="t in candidateTags(m.name)" :key="t" :label="t" :value="t" />
            </el-select>
            <el-button type="success" size="small" :loading="registering===m.name" @click="doRegister(m.name)">接入</el-button>
          </div>
        </div>
      </el-card>
    </section>

    <!-- 已接入模型 -->
    <section class="mm-section">
      <h4>已接入模型（热加载，导演链路可选用）· {{ registered.length }}</h4>
      <el-empty v-if="!registered.length" description="尚未接入任何模型" />
      <el-card v-for="m in registered" :key="m.model_id" class="mm-card" shadow="hover">
        <div class="mm-card-head">
          <span class="mm-name">{{ m.name }}</span>
          <el-tag size="small" :type="typeTag(m.type)">{{ m.type }}</el-tag>
          <el-tag v-if="m.node_family" size="small" type="success">族:{{ m.node_family }}</el-tag>
          <el-tag size="small" :type="statusType(m.status)">{{ m.status }}</el-tag>
        </div>
        <div class="mm-card-body">
          <span class="mm-label">语义标签：</span>
          <el-tag v-for="t in (m.semantic_tags||[])" :key="t" size="small" effect="plain" class="mm-tag">{{ t }}</el-tag>
          <span v-if="!m.semantic_tags || !m.semantic_tags.length" class="mm-muted">（无）</span>
        </div>
        <div class="mm-card-body" v-if="m.user_notes">
          <span class="mm-label">备注：</span><span class="mm-muted">{{ m.user_notes }}</span>
        </div>
      </el-card>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { scanModels, discoverModel, registerModel, listModels } from '../api.js'

const pending = ref([])
const registered = ref([])
const scanning = ref(false)
const loadingList = ref(false)
const discovering = ref('')
const registering = ref('')
const discovered = ref({})
const notes = ref({})
const tags = ref({})

// 候选语义标签（自动发现 + 常用）
function candidateTags(name) {
  const base = ['写实', '动漫', '电影感', '插值', '外推', '视频', 'sdxl']
  const d = discovered.value[name]
  if (d && d.capabilities && d.capabilities.frame_anchor) {
    // 仅作候选，不强制
  }
  return base
}

function typeTag(t) {
  return { video: 'danger', lora: 'warning', ipadapter: 'info', controlnet: 'info', checkpoint: '' }[t] || ''
}
function statusType(s) {
  return { active: 'success', disabled: 'info', pending: 'warning' }[s] || 'info'
}

async function doScan() {
  scanning.value = true
  try {
    pending.value = await scanModels()
    if (!pending.value.length) ElMessage.info('未扫描到未声明模型（确认 ComfyUI 已启动且模型目录有新增文件）')
  } catch (e) {
    ElMessage.error('扫描失败：' + (e.message || e))
  } finally {
    scanning.value = false
  }
}

async function refreshList() {
  loadingList.value = true
  try {
    registered.value = await listModels()
  } catch (e) {
    ElMessage.error('加载已接入模型失败：' + (e.message || e))
  } finally {
    loadingList.value = false
  }
}

async function doDiscover(name) {
  discovering.value = name
  try {
    const r = await discoverModel(name)
    discovered.value = { ...discovered.value, [name]: r }
    if (!tags.value[name]) tags.value[name] = []
    if (r.template && r.template.status !== 'matched') {
      ElMessage.warning('该节点族暂无内置模板，接入后需到「模板」页导入工作流（P3 M2）')
    }
  } catch (e) {
    ElMessage.error('自动发现失败：' + (e.message || e))
  } finally {
    discovering.value = ''
  }
}

async function doRegister(name) {
  registering.value = name
  try {
    const d = discovered.value[name] || {}
    const body = {
      name,
      semantic_tags: tags.value[name] || [],
      user_notes: notes.value[name] || '',
      // node_family / type 由后端自动发现补全
    }
    if (d.node_family) body.node_family = d.node_family
    await registerModel(body)
    ElMessage.success('接入成功：' + name + '（热加载生效，无需重启）')
    // 清理该 pending 项并刷新
    pending.value = pending.value.filter((m) => m.name !== name)
    delete discovered.value[name]
    await refreshList()
  } catch (e) {
    ElMessage.error('接入失败：' + (e.message || e))
  } finally {
    registering.value = ''
  }
}

onMounted(refreshList)
</script>

<style scoped>
.model-manager { padding: 4px 2px; }
.mm-actions { margin: 12px 0; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.mm-hint { color: #909399; font-size: 12px; }
.mm-section { margin-top: 16px; }
.mm-section h4 { margin: 8px 0; font-weight: 600; }
.mm-card { margin-bottom: 10px; }
.mm-card-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mm-name { font-weight: 600; word-break: break-all; }
.mm-card-body { margin-top: 8px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.mm-label { color: #606266; font-size: 13px; }
.mm-tag { margin-right: 4px; }
.mm-muted { color: #909399; font-size: 13px; }
.mm-discover { margin-top: 10px; border-top: 1px dashed #ebeef5; padding-top: 10px; }
.mm-form { margin-top: 10px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.mm-note { max-width: 220px; }
.mm-tags { min-width: 220px; }
</style>
