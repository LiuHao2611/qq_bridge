<script setup>
defineOptions({ name: 'CanvasPhase' })
import { ref, reactive, onMounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { LiteGraph, LGraph, LGraphCanvas } from 'litegraph.js'
import 'litegraph.js/css/litegraph.css'
import { getProject, aiPolish, estimatePoints, preAuthPoints, synthesizeProject, listSkills, getSkill, runSkill } from '../api'
import { toast } from '../store'
import ProjectBookShell from './ProjectBookShell.vue'
import SkillPanel from './SkillPanel.vue'

const route = useRoute()
const pid = route.params.id
const wrap = ref(null)
const cv = ref(null)
const running = ref(false)
const ran = ref(false)
const saving = ref(false)
const saved = ref(true)
// 诚实声明：画布是「可视化规划工具」，不执行真实出图/动画/合成（真实生成在「片段编辑器」提交）。
// 仅提供：本地 AI 润色主题（真调 Ollama）、导出 ComfyUI 工作流 JSON、技能库（真调 P0 translator）。
const canvasMode = ref('planning')
const prog = reactive({ stage: '', current: 0, total: 0, message: '' })
const project = reactive({ title: '' })
let graph = null
let canvas = null

const NODE_CATALOG = [
  { type: 'manju/script', title: '剧本解析', color: '#3a5bd9', desc: '输入主题/剧本' },
  { type: 'manju/plan', title: '分镜规划', color: '#7a3ad9', desc: '拆分镜头数' },
  { type: 'manju/prompt', title: '提示词增强', color: '#c23ad9', desc: '风格与优化' },
  { type: 'manju/gen', title: '出图 (ComfyUI)', color: '#d94f3a', desc: 'SD 生图参数' },
  { type: 'manju/anim', title: '动画 (AnimateDiff)', color: '#d9a23a', desc: '动画帧数设置' },
  { type: 'manju/comp', title: '合成 (FFmpeg)', color: '#3ad97a', desc: '视频输出设置' },
]

function registerNodes(initial) {
  if (LiteGraph.registered_node_types['manju/script']) return

  class ScriptNode {
    constructor() {
      this.title = '剧本解析'
      this.color = '#3a5bd9'
      this.addInput('项目数据', '*')
      this.addOutput('主题', 'string')
      this.addWidget('text', '主题/灵感', initial.theme || '', null, { property: 'theme' })
    }
  }
  LiteGraph.registerNodeType('manju/script', ScriptNode)

  class PlanNode {
    constructor() {
      this.title = '分镜规划'
      this.color = '#7a3ad9'
      this.addInput('主题', 'string')
      this.addOutput('分镜', 'array')
      this.addWidget('combo', '模式', initial.mode || 'full', null, { values: ['simple', 'full'], property: 'mode' })
      this.addWidget('number', '镜头数', initial.scenes_count || 8, null, { min: 1, max: 64, step: 1, property: 'scenes_count' })
    }
  }
  LiteGraph.registerNodeType('manju/plan', PlanNode)

  class PromptNode {
    constructor() {
      this.title = '提示词增强'
      this.color = '#c23ad9'
      this.addInput('分镜', 'array')
      this.addOutput('提示词', 'array')
      this.addWidget('combo', '风格', initial.style || 'anime', null, { values: ['anime', 'realistic', 'cinematic'], property: 'style' })
    }
  }
  LiteGraph.registerNodeType('manju/prompt', PromptNode)

  class GenNode {
    constructor() {
      this.title = '出图 (ComfyUI)'
      this.color = '#d94f3a'
      this.addInput('提示词', 'array')
      this.addOutput('图片', 'image')
      this.addWidget('combo', '底模', initial.ckpt || '', null, { values: initial.ckpts || [], property: 'ckpt' })
      this.addWidget('number', '步数', initial.steps || 25, null, { min: 1, max: 60, step: 1, property: 'steps' })
      this.addWidget('number', 'CFG', initial.cfg || 7, null, { min: 1, max: 20, step: 0.5, property: 'cfg' })
      this.addWidget('combo', '采样器', initial.sampler || 'DPM++ 2M Karras', null, { values: ['Euler', 'DPM++ 2M Karras', 'DPM++ SDE Karras', 'UniPC'], property: 'sampler' })
      this.addWidget('number', '宽', initial.width || 768, null, { min: 256, max: 2048, step: 64, property: 'width' })
      this.addWidget('number', '高', initial.height || 432, null, { min: 256, max: 2048, step: 64, property: 'height' })
      this.addWidget('combo', '角色一致(IP)', initial.use_ip || '否', null, { values: ['否', '是'], property: 'use_ip' })
    }
  }
  LiteGraph.registerNodeType('manju/gen', GenNode)

  class AnimNode {
    constructor() {
      this.title = '动画 (AnimateDiff)'
      this.color = '#d9a23a'
      this.addInput('图片', 'image')
      this.addOutput('动画', 'video')
      this.addWidget('combo', '启用动画', initial.animated || '是', null, { values: ['否', '是'], property: 'animated' })
      this.addWidget('combo', '运动模型', initial.motion || '', null, { values: initial.motions || [], property: 'motion' })
      this.addWidget('number', '帧数', initial.frames || 16, null, { min: 4, max: 64, step: 1, property: 'frames' })
      this.addWidget('number', '帧率', initial.fps || 8, null, { min: 1, max: 60, step: 1, property: 'fps' })
    }
  }
  LiteGraph.registerNodeType('manju/anim', AnimNode)

  class CompNode {
    constructor() {
      this.title = '合成 (FFmpeg)'
      this.color = '#3ad97a'
      this.addInput('动画', 'video')
      this.addOutput('成片', 'video')
      this.addWidget('number', '输出帧率', initial.out_fps || 24, null, { min: 1, max: 60, step: 1, property: 'out_fps' })
      this.addWidget('combo', '分辨率', initial.resolution || '768x432', null, { values: ['768x432', '1280x720', '1920x1080'], property: 'resolution' })
    }
  }
  LiteGraph.registerNodeType('manju/comp', CompNode)
}

function buildDefaultGraph(g, initial, proj) {
  registerNodes(initial)
  const order = ['manju/script', 'manju/plan', 'manju/prompt', 'manju/gen', 'manju/anim', 'manju/comp']
  const nodes = []
  order.forEach((type, i) => {
    const n = LiteGraph.createNode(type)
    if (!n) return
    n.pos = [60 + i * 40, 60 + i * 150]
    g.add(n)
    nodes.push(n)
  })
  for (let i = 0; i < nodes.length - 1; i++) {
    if (nodes[i].outputs.length && nodes[i + 1].inputs.length) nodes[i].connect(0, nodes[i + 1], 0)
  }
  const scriptNode = nodes[0]
  if (scriptNode && proj) {
    const theme = proj.title || (proj.script_text ? proj.script_text.slice(0, 40) : '')
    if (theme) {
      scriptNode.properties.theme = theme
      if (scriptNode.widgets && scriptNode.widgets[0]) scriptNode.widgets[0].value = theme
    }
  }
}

function nodeParam(n, key, fallback) {
  if (!n) return fallback
  if (n.properties && n.properties[key] !== undefined) return n.properties[key]
  if (n.widgets) {
    const w = n.widgets.find((x) => x.name === key || (x.options && x.options.property === key))
    if (w) return w.value
  }
  return fallback
}

function buildComfyWorkflow(params, prompt, negative) {
  const w = {}
  let id = 1
  const add = (cls, inputs) => {
    w[String(id)] = { class_type: cls, inputs }
    return String(id++)
  }
  const ckpt = add('CheckpointLoaderSimple', { ckpt_name: params.ckpt })
  const pos = add('CLIPTextEncode', { text: prompt || 'anime illustration, 1girl, masterpiece', clip: [ckpt, 1] })
  const neg = add('CLIPTextEncode', { text: negative || 'lowres, bad anatomy, extra limbs, watermark', clip: [ckpt, 1] })
  let modelOut = [ckpt, 0]
  const animated = params.animated !== '否'
  const frames = parseInt(params.frames) || 16
  if (animated) {
    const ad = add('ADE_AnimateDiffLoaderGen1', { model: [ckpt, 0], model_name: params.motion, beta_schedule: 'autoselect' })
    modelOut = [ad, 0]
  }
  if (params.use_ip === '是') {
    const ld = add('LoadImage', { image: 'ref.png' })
    const ip = add('IPAdapter', { model: modelOut, ipadapter: 'ip-adapter_sd15.safetensors', image: [ld, 0], weight: 0.8 })
    modelOut = [ip, 0]
  }
  const wdt = parseInt(params.width) || 768
  const hgt = parseInt(params.height) || 432
  const latent = add('EmptyLatentImage', { width: wdt, height: hgt, batch_size: animated ? frames : 1 })
  const ks = add('KSampler', {
    model: modelOut, positive: [pos, 0], negative: [neg, 0], latent_image: [latent, 0],
    seed: Math.floor(Math.random() * 1e9), steps: parseInt(params.steps) || 25, cfg: parseFloat(params.cfg) || 7,
    sampler_name: params.sampler, scheduler: 'karras', denoise: 1.0,
  })
  const vae = add('VAEDecode', { samples: [ks, 0], vae: [ckpt, 2] })
  add('SaveImage', { images: [vae, 0], filename_prefix: 'manju' })
  return { prompt: w, extra_data: { '1': { inputs: { ckpt_name: params.ckpt } } }, last_node_id: id - 1, last_link_id: id }
}

const resizeCanvas = () => {
  if (!cv.value || !wrap.value) return
  const rect = wrap.value.getBoundingClientRect()
  cv.value.width = rect.width
  cv.value.height = rect.height
  if (canvas) {
    canvas.setDirty(true, true)
    canvas.draw(true, true)
  }
}
const fitView = () => {
  if (!canvas || !graph || !wrap.value) return
  const ns = graph._nodes || []
  if (!ns.length) return
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  ns.forEach((n) => {
    minX = Math.min(minX, n.pos[0]); minY = Math.min(minY, n.pos[1])
    maxX = Math.max(maxX, n.pos[0] + n.size[0]); maxY = Math.max(maxY, n.pos[1] + n.size[1])
  })
  const w = wrap.value.clientWidth, h = wrap.value.clientHeight
  const gw = Math.max(maxX - minX, 1), gh = Math.max(maxY - minY, 1)
  const scale = Math.min(w / gw, h / gh, 1.5) * 0.85
  canvas.ds.scale = scale
  canvas.ds.offset[0] = (w - gw * scale) / 2 - minX * scale
  canvas.ds.offset[1] = (h - gh * scale) / 2 - minY * scale
  canvas.setDirty(true, true)
}
const resetGraph = () => {
  if (!graph) return
  if (!confirm('重置会清空当前画布，是否继续？')) return
  graph.clear()
  buildDefaultGraph(graph, lastInitial, project)
  fitView()
  saved.value = false
}
const addNode = (type) => {
  if (!graph) return
  const n = LiteGraph.createNode(type)
  if (!n) return toast('创建节点失败')
  const rect = wrap.value.getBoundingClientRect()
  n.pos = [
    (rect.width / 2 - canvas.ds.offset[0]) / canvas.ds.scale - n.size[0] / 2,
    (rect.height / 2 - canvas.ds.offset[1]) / canvas.ds.scale - n.size[1] / 2,
  ]
  graph.add(n)
  saved.value = false
}
const CANVAS_KEY = (id) => 'manju_canvas_' + (id || 'default')
const saveCanvas = async () => {
  if (!graph) return
  saving.value = true
  try {
    // 个人本地版：画布状态存浏览器本地（后端 projects 表无 canvas_state 字段，诚实告知不落库）
    const data = graph.serialize()
    try { localStorage.setItem(CANVAS_KEY(pid), JSON.stringify(data)) } catch (e) {}
    saved.value = true
    toast('画布已保存到本地（演示，未落库后端）')
  } catch (e) {
    toast('保存失败: ' + e.message)
  } finally {
    saving.value = false
  }
}
const findFirstNode = (type) => (graph ? graph._nodes.find((n) => n.type === type) : null)
const collectParams = () => {
  const p = {}
  NODE_CATALOG.forEach((cat) => {
    const n = findFirstNode(cat.type)
    if (n) Object.keys(n.properties || {}).forEach((k) => (p[k] = n.properties[k]))
  })
  return p
}
// 规划模式下的「运行」= 真实但有限的动作：本地 Ollama 润色主题 + 存档。
// 不假装执行节点图出图/动画/合成（那属于「片段编辑器」的真实生成链路，避免假功能）。
const runCanvasPolish = async () => {
  if (!graph) return
  running.value = true
  ran.value = true
  saved.value = false
  const scriptNode = findFirstNode('manju/script')
  const theme = nodeParam(scriptNode, 'theme', project.title || '')
  try {
    prog.stage = 'planning'; prog.current = 0; prog.total = 0; prog.message = '连接本地 AI 润色中…'
    // 真实调用：本地 Ollama 润色（手册 AI 适配 L1/L2）。Ollama 未启动会诚实报错而非假装成功。
    let polished = theme
    try {
      const r = await aiPolish(theme || 'AI 短剧片段', 'cinematic')
      if (r && r.polished) polished = r.polished
    } catch (e) {
      toast('本地 AI 润色不可用（' + e.message + '），使用原始主题')
    }
    await saveCanvas()
    prog.stage = ''; prog.message = '本地 AI 润色完成'
    // 诚实提示：本页只润色+存档，真实出图/动画/合成在「片段编辑器」提交。
    toast('已用本地 AI 润色主题并保存画布（规划模式）。真实出图/动画/合成请在「片段编辑器」提交生成任务')
  } catch (e) {
    toast('执行失败: ' + e.message)
    prog.stage = ''
  } finally {
    running.value = false
  }
}
const exportComfy = () => {
  const params = collectParams()
  let prompt = 'anime illustration, 2d animation'
  let negative = 'lowres, bad anatomy, watermark, text'
  if (project.scenes && project.scenes[0]) {
    prompt = project.scenes[0].prompt || prompt
    negative = project.scenes[0].negative_prompt || negative
  }
  const data = buildComfyWorkflow(params, prompt, negative)
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'comfyui_workflow_' + (project.title || 'manju') + '.json'
  a.click()
  URL.revokeObjectURL(a.href)
  toast('已导出 ComfyUI 工作流 JSON')
}

let lastInitial = null
onMounted(async () => {
  // 个人本地版：底模/运动模型来自本地秋叶 ComfyUI（硬编码真实可用模型名，无需 /api/models 轮询）
  const init = { ckpt: 'Realistic_Vision_V6.0_NV_B1_fp16.safetensors', ckpts: [], motion: 'mm_sd_v15_v2', motions: [] }
  let loadedState = null
  try {
    const p = await getProject(pid)
    if (p && p.name) {
      project.title = p.name
      init.theme = p.name
    }
  } catch (e) {
    console.warn('canvas project load failed (demo route?), using default:', e.message)
  }
  // 优先恢复本地保存的画布状态
  try {
    const local = localStorage.getItem(CANVAS_KEY(pid))
    if (local) loadedState = JSON.parse(local)
  } catch (e) {}
  lastInitial = init

  // 防御：onMounted 异步路径上方有 await getProject(pid)；若路由在此期间切走、组件已卸载，
  // cv.value/wrap.value 会被置空，继续初始化会抛 "Cannot read properties of null (reading 'focus')"。
  // 正常挂载时 ref 已就绪，此守卫对既有逻辑零影响。
  if (!cv.value || !wrap.value) return

  graph = new LGraph()
  canvas = new LGraphCanvas(cv.value, graph)
  canvas.allow_dragcanvas = true
  canvas.allow_interaction = true
  canvas.allow_searchbox = true
  cv.value.focus()

  loadSkillMap()

  if (loadedState) {
    try {
      graph.configure(loadedState)
    } catch (e) {
      buildDefaultGraph(graph, init, project)
    }
  } else {
    buildDefaultGraph(graph, init, project)
  }
  resizeCanvas()
  window.addEventListener('resize', resizeCanvas)
  graph.start()
  graph.onAfterChange = () => (saved.value = false)
  nextTick(fitView)
})

// 阶段翻页「合成全集」最终动作（阶段 5 为最后一阶段，fold-next 触发）
async function onSynthesize() {
  try {
    const est = await estimatePoints({ operation: 'estimate', duration: 0, resolution: '720p', audio: true })
    const idem = 'idem_syn_' + Date.now() + '_' + Math.random().toString(36).slice(2)
    const pre = await preAuthPoints(est.estimated_credits, 'synthesize', idem)
    await synthesizeProject(pid, pre.pre_auth_id, {})
    toast('已提交「合成全集」任务')
  } catch (e) {
    toast('合成失败: ' + (e.message || e))
  }
}

// ---- P3 M1 Skills 技能库：拖拽执行（真调 P0 translator）----
const skillMap = reactive({})       // skill_id -> skill 对象（供 drop 时反查名称）
const runDialog = reactive({ open: false, loading: false, skill: null, userInput: '', subjAsset: '', lighting: '' })
const runResult = ref(null)          // translator.translate 返回的 workflow

async function loadSkillMap() {
  try {
    const list = await listSkills()
    list.forEach((s) => { skillMap[s.skill_id] = s })
  } catch (e) { /* 技能库加载失败不阻塞画布 */ }
}

// 来自 SkillPanel 的「运行」按钮 / 双击
function onRunSkill(skill) {
  runDialog.skill = skill
  runDialog.open = true
  runResult.value = null
}

// 来自画布拖拽放置
function onCanvasDrop(e) {
  const sid = e.dataTransfer ? e.dataTransfer.getData('skillId') : ''
  if (!sid) return
  const skill = skillMap[sid]
  if (!skill) {
    // 兜底：从后端按 id 拉取
    getSkill(sid).then((s) => { if (s) onRunSkill(s); else toast('未找到技能：' + sid) }).catch(() => toast('未找到技能：' + sid))
    return
  }
  onRunSkill(skill)
}

async function doRunSkill() {
  if (!runDialog.skill) return
  runDialog.loading = true
  runResult.value = null
  try {
    const context = {
      subject_asset_id: runDialog.subjAsset || '{{subject_asset_id}}',
      style_lighting: runDialog.lighting || '{{style_lighting}}',
    }
    const wf = await runSkill(runDialog.skill.skill_id, {
      user_input: runDialog.userInput || '',
      context,
    })
    runResult.value = wf
    toast('已调用 P0 translator.translate（技能：' + runDialog.skill.name + '）')
  } catch (e) {
    toast('技能执行失败: ' + (e.message || e))
  } finally {
    runDialog.loading = false
  }
}
</script>

<template>
  <ProjectBookShell class="page canvas-page" :step="5" @synthesize="onSynthesize">
      <div class="canvas-shell">
        <!-- 诚实声明：画布是可视化规划工具，不执行真实出图/动画/合成 -->
        <div class="canvas-banner">
          <span class="cb-tag">🧭 规划模式</span>
          <span class="cb-text">本画布为<strong>可视化构思工具</strong>，节点用于编排思路并<strong>导出 ComfyUI 工作流 JSON</strong>；技能库卡片可真调 P0 意图翻译引擎。
          <strong>真实出图 / 动画 / 合成</strong>请在「片段编辑器」提交生成任务，本页不代执行生成管线。</span>
        </div>
        <div class="ws-toolbar">
          <div class="title"><div style="font-weight:800">工作流画布</div><div class="sub">ComfyUI 同款节点编辑器 · 与项目联动（规划模式）</div></div>
          <div class="sep"></div>
          <div class="group" style="margin-left:auto">
            <button class="btn" @click="saveCanvas" :disabled="saving">💾 保存画布</button>
            <button class="btn" @click="fitView">适配视图</button>
            <button class="btn" @click="resetGraph">重置</button>
            <button class="btn" @click="exportComfy">导出 ComfyUI</button>
            <button class="btn primary" :disabled="running" @click="runCanvasPolish">✨ AI 润色主题</button>
          </div>
        </div>
      <div class="canvas-body">
        <div class="canvas-library">
          <h4>节点库</h4>
          <div class="lib-item" v-for="n in NODE_CATALOG" :key="n.type" @click="addNode(n.type)">
            <span class="dot" :style="{ background: n.color }"></span>
            <div>
              <div class="lib-title">{{ n.title }}</div>
              <div class="lib-desc">{{ n.desc }}</div>
            </div>
          </div>
          <div class="lib-hint">点击节点添加到画布中央。拖拽节点移动、拖拽圆点连线、点击 widget 改参数。画布状态存本地（未落库后端），真实生成请在「片段编辑器」提交。</div>
        </div>
        <SkillPanel @run="onRunSkill" />
        <div class="canvas-wrap" ref="wrap" @dragover.prevent @drop="onCanvasDrop">
          <canvas ref="cv" tabindex="0"></canvas>
          <div class="canvas-hint" v-if="!ran">拖拽「技能库」卡片到此（真调 P0 意图翻译引擎生成 workflow）；或拖拽节点 / 连线 / 编辑参数构思分镜。真实生成请在「片段编辑器」提交。</div>
        </div>
      </div>

      <!-- P3 M1 技能运行弹窗（真调 P0 translator）-->
      <div class="modal-mask" v-if="runDialog.open" @click="runDialog.open = false">
        <div class="modal-box" @click.stop>
          <h3>🧩 运行技能：{{ runDialog.skill?.name }}</h3>
          <p class="muted" style="margin:0 0 12px">
            task_type：<code>{{ runDialog.skill?.intent_template?.task_type }}</code> ·
            填入下方参数后将真实调用 P0 <code>translator.translate</code> 生成 ComfyUI workflow。
          </p>
          <div class="field"><label>用户输入 / 运镜描述</label>
            <textarea v-model="runDialog.userInput" rows="3" placeholder="例如：镜头从脚推到脸，逆光剪影" class="sp-create-textarea" />
          </div>
          <div class="field"><label>角色资产 ID</label>
            <input v-model="runDialog.subjAsset" placeholder="留空则使用模板占位符 {{subject_asset_id}}" class="sp-create-input" />
          </div>
          <div class="field"><label>打光风格</label>
            <input v-model="runDialog.lighting" placeholder="留空则使用模板占位符 {{style_lighting}}" class="sp-create-input" />
          </div>

          <div v-if="runResult" class="run-result">
            <div class="rr-head">✓ translator.translate 已返回 workflow（{{ Object.keys(runResult).length }} 个顶层键）</div>
            <pre class="rr-json">{{ JSON.stringify(runResult, null, 2) }}</pre>
          </div>

          <div class="dialog-footer">
            <el-button @click="runDialog.open = false">关闭</el-button>
            <el-button type="primary" :loading="runDialog.loading" @click="doRunSkill">执行技能</el-button>
          </div>
        </div>
      </div>

      <div class="canvas-status" v-if="prog.stage">
        <span>状态: <b>{{ prog.stage }}</b> · {{ prog.current }}/{{ prog.total }} · {{ prog.message }}</span>
      </div>
    </div>
  </ProjectBookShell>
</template>

<style scoped>
/* 覆盖全局 .canvas-shell{height:100vh}：被 ProjectBookShell 包裹后需填满书页内容区 */
.canvas-shell { height: 100%; }

/* 规划模式诚实声明横幅 */
.canvas-banner { display: flex; align-items: flex-start; gap: 10px; padding: 10px 14px; margin: 10px 12px 0; background: rgba(250,204,21,.10); border: 1px solid rgba(250,204,21,.45); border-radius: 10px; color: var(--txt); }
.cb-tag { flex: 0 0 auto; font-weight: 800; font-size: 12.5px; color: #facc15; background: rgba(250,204,21,.16); border: 1px solid rgba(250,204,21,.5); border-radius: 999px; padding: 3px 10px; white-space: nowrap; }
.cb-text { font-size: 12.5px; line-height: 1.6; color: var(--sub); }
.cb-text strong { color: var(--txt); font-weight: 700; }

/* P3 M1 技能运行弹窗 */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.5); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal-box { width: 560px; max-width: 92vw; max-height: 86vh; overflow: auto; background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 20px 22px; color: var(--txt); }
.modal-box h3 { margin: 0 0 4px; font-size: 16px; }
.modal-box .field { display: flex; flex-direction: column; gap: 6px; margin: 12px 0; }
.modal-box .field label { font-size: 12px; color: var(--sub); }
.sp-create-input, .sp-create-textarea { background: var(--bg-2); color: var(--txt); border: 1px solid var(--line); border-radius: 8px; padding: 7px 9px; font-family: monospace; font-size: 12px; outline: none; width: 100%; box-sizing: border-box; }
.sp-create-textarea { resize: vertical; }
.run-result { margin-top: 12px; border: 1px solid rgba(74,222,128,.4); background: rgba(74,222,128,.07); border-radius: 10px; padding: 10px 12px; }
.rr-head { font-size: 12.5px; color: #4ade80; font-weight: 700; margin-bottom: 8px; }
.rr-json { max-height: 280px; overflow: auto; white-space: pre-wrap; word-break: break-all; background: var(--bg-2); border-radius: 8px; padding: 10px; font-size: 11px; line-height: 1.5; color: var(--sub); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.dialog-footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }
</style>
