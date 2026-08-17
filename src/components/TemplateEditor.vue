<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { importTemplate, listUserTemplates, testUserTemplate } from '../api'

// 与 P0 production_intent.VALID_TASK_TYPES 严格对齐（同名覆盖注册进 P0 映射）
const VALID_TASK_TYPES = [
  { value: 'character_genesis', label: '角色生成 (character_genesis)' },
  { value: 'scene_genesis', label: '场景生成 (scene_genesis)' },
  { value: 'shot_txt2img', label: '文生图 (shot_txt2img)' },
  { value: 'shot_img2img', label: '图生图 (shot_img2img)' },
  { value: 'shot_video_text2video', label: '文生视频 (shot_video_text2video)' },
  { value: 'shot_video_with_anchor', label: '带锚点视频 (shot_video_with_anchor)' },
]

const SAMPLE = {
  '1': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: '{{checkpoint}}' } },
  '2': { class_type: 'CLIPTextEncode', inputs: { text: '{{positive_prompt}}', clip: ['1', 1] } },
  '3': { class_type: 'CLIPTextEncode', inputs: { text: 'lowres, bad anatomy', clip: ['1', 1] } },
  '4': { class_type: 'EmptyLatentImage', inputs: { width: '{{width}}', height: '{{height}}', batch_size: 1 } },
  '5': {
    class_type: 'KSampler',
    inputs: {
      model: ['1', 0], positive: ['2', 0], negative: ['3', 0], latent_image: ['4', 0],
      seed: '{{seed}}', steps: '{{steps}}', cfg: '{{cfg_scale}}',
      sampler_name: 'dpmpp_2m', scheduler: 'karras', denoise: 1.0,
    },
  },
  '6': { class_type: 'VAEDecode', inputs: { samples: ['5', 0], vae: ['1', 2] } },
  '7': { class_type: 'SaveImage', inputs: { images: ['6', 0], filename_prefix: 'manju' } },
}

const taskType = ref('shot_txt2img')
const jsonText = ref('')
const importing = ref(false)
const importResult = ref(null)
const templates = ref([])
const loadingTpl = ref(false)
const testingId = ref('')
const testResult = ref(null)

function loadSample() {
  jsonText.value = JSON.stringify(SAMPLE, null, 2)
  ElMessage.info('已载入示例工作流（ComfyUI API 格式）')
}

// 归一化：ComfyUI 导出有 UI 格式({nodes:[...]}) 与 API 格式({prompt:{id:{...}}}) 两种，
// 后端 import_workflow 期望 API 格式（{node_id:{class_type,inputs}}）。这里做兼容转换。
function normalizeComfy(raw) {
  if (raw && raw.prompt && typeof raw.prompt === 'object') return raw.prompt
  if (raw && Array.isArray(raw.nodes)) {
    const out = {}
    raw.nodes.forEach((n) => {
      out[String(n.id)] = { class_type: n.type, inputs: n.inputs || {} }
    })
    return out
  }
  return raw
}

async function onImport() {
  importing.value = true
  importResult.value = null
  testResult.value = null
  try {
    const raw = JSON.parse(jsonText.value)
    const comfyJson = normalizeComfy(raw)
    const r = await importTemplate(taskType.value, comfyJson)
    importResult.value = r
    ElMessage.success('模板已注册进 P0 映射：' + r.template_id + (r.usable_by_director ? '（director 可选用）' : ''))
    await loadTemplates()
  } catch (e) {
    ElMessage.error('导入失败: ' + (e.message || e))
  } finally {
    importing.value = false
  }
}

async function loadTemplates() {
  loadingTpl.value = true
  try {
    templates.value = await listUserTemplates()
  } catch (e) {
    templates.value = []
  } finally {
    loadingTpl.value = false
  }
}

async function onTest(tid) {
  testingId.value = tid
  testResult.value = null
  try {
    const r = await testUserTemplate(tid)
    testResult.value = r
    ElMessage.success('模板校验通过：' + r.template_id + ' · 节点数 ' + r.node_count)
  } catch (e) {
    ElMessage.error('校验失败: ' + (e.message || e))
  } finally {
    testingId.value = ''
  }
}

onMounted(() => {
  // 注意：不再在挂载时自动填入示例，避免「载入示例」按钮点击后看似无反应
  // （字段已被预填 → 内容不变）。改为由按钮显式载入，并给出 toast 反馈。
  loadTemplates()
})
</script>

<template>
  <div class="tmpl-editor">
    <div class="te-grid">
      <!-- 左：导入区 -->
      <div class="te-col">
        <div class="te-sec-h">① 导入 ComfyUI 工作流</div>
        <div class="field">
          <label>注册为 task_type（同名覆盖 P0 系统模板）</label>
          <el-select v-model="taskType" style="width:100%">
            <el-option v-for="t in VALID_TASK_TYPES" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </div>
        <div class="field">
          <div class="te-row-between">
            <label>工作流 JSON（API 格式）</label>
            <button class="sp-btn" @click="loadSample">载入示例</button>
          </div>
          <textarea v-model="jsonText" rows="14" spellcheck="false" class="te-json"></textarea>
        </div>
        <el-button type="primary" :loading="importing" @click="onImport">解析并注册为语义模板</el-button>

        <div v-if="importResult" class="te-result">
          <div class="te-r-head">✓ 注册成功（usable_by_director: {{ importResult.usable_by_director }}）</div>
          <div class="te-r-line">template_id：<code>{{ importResult.template_id }}</code></div>
          <div class="te-r-line">path：<code>{{ importResult.path }}</code></div>
          <div class="te-r-sub">自动识别占位符（{{ (importResult.detected_placeholders || []).length }}）：</div>
          <div class="te-chips">
            <span v-for="p in (importResult.detected_placeholders || [])" :key="p" class="te-chip">{{ p }}</span>
            <span v-if="!(importResult.detected_placeholders || []).length" class="muted">无（未识别到标准字段）</span>
          </div>
        </div>
      </div>

      <!-- 右：已注册模板列表 -->
      <div class="te-col">
        <div class="te-sec-h">② 已注册的用户模板（user_custom）</div>
        <div v-loading="loadingTpl">
          <div v-if="!templates.length" class="te-empty muted">暂无已注册模板。在左侧导入一个吧。</div>
          <div v-for="t in templates" :key="t.template_id" class="te-tpl-card">
            <div class="te-tpl-h">
              <code>{{ t.template_id }}</code>
              <span class="te-badge">{{ t.node_count }} 节点</span>
            </div>
            <div class="te-chips">
              <span v-for="p in (t.placeholders || [])" :key="p" class="te-chip ghost">{{ p }}</span>
              <span v-if="!(t.placeholders || []).length" class="muted">无占位符</span>
            </div>
            <button class="sp-btn" :disabled="testingId === t.template_id" @click="onTest(t.template_id)">
              {{ testingId === t.template_id ? '校验中…' : '验证可用' }}
            </button>
          </div>
        </div>

        <div v-if="testResult" class="te-result">
          <div class="te-r-head">✓ 校验通过：{{ testResult.template_id }}</div>
          <div class="te-r-line">node_count：<code>{{ testResult.node_count }}</code></div>
          <pre class="te-test-json">{{ JSON.stringify(testResult.schema, null, 2) }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tmpl-editor { color: var(--txt); }
.te-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.te-col { display: flex; flex-direction: column; gap: 12px; }
.te-sec-h { font-size: 13px; font-weight: 700; color: var(--txt); margin-bottom: 4px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 12px; color: var(--sub); }
.te-row-between { display: flex; align-items: center; justify-content: space-between; }
.te-json { background: var(--bg-2); color: var(--txt); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; line-height: 1.5; resize: vertical; box-sizing: border-box; width: 100%; }
.sp-btn { border: 1px solid var(--line); background: var(--card); color: var(--txt); border-radius: 8px; padding: 4px 12px; cursor: pointer; font-size: 12px; }
.sp-btn:disabled { opacity: .5; cursor: not-allowed; }
.te-result { margin-top: 10px; border: 1px solid rgba(74,222,128,.4); background: rgba(74,222,128,.07); border-radius: 10px; padding: 12px; }
.te-r-head { font-size: 12.5px; color: #4ade80; font-weight: 700; margin-bottom: 8px; }
.te-r-line { font-size: 12px; margin-bottom: 4px; }
.te-r-sub { font-size: 12px; color: var(--sub); margin: 8px 0 6px; }
.te-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.te-chip { font-size: 11px; padding: 2px 8px; border-radius: 6px; background: var(--pur); color: #fff; }
.te-chip.ghost { background: var(--bg-2); color: var(--sub); border: 1px solid var(--line); }
.te-empty { padding: 20px 0; }
.te-tpl-card { background: var(--card-2); border: 1px solid var(--line); border-radius: 10px; padding: 12px; margin-bottom: 10px; }
.te-tpl-h { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.te-tpl-h code { font-size: 12px; color: var(--txt); font-weight: 700; }
.te-badge { font-size: 11px; padding: 1px 8px; border-radius: 6px; background: var(--sub); color: #fff; }
.te-test-json { max-height: 200px; overflow: auto; white-space: pre-wrap; word-break: break-all; background: var(--bg-2); border-radius: 8px; padding: 10px; font-size: 11px; color: var(--sub); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; margin-top: 8px; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.muted { color: var(--sub); font-size: 12px; }
@media (max-width: 860px) { .te-grid { grid-template-columns: 1fr; } }
</style>
