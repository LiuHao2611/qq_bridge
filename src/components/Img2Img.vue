<script setup>
import { ref, reactive, computed, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { usePersistedState } from '../composables/usePersist'

defineOptions({ name: 'Img2Img' })

// ============================================================
// 模式定义（社区高频 ComfyUI 图片编辑工作流）
// ============================================================
const modes = [
  { key: 'img2img', label: '图生图', tip: '以原图为锚点重新生成，强度越高改动越大' },
  { key: 'inpaint', label: '局部重绘', tip: '涂抹需要修改的区域，仅重绘遮罩部分（denoise 0.6–0.8）' },
  { key: 'outpaint', label: '扩图', tip: '向四向扩展画布并由 AI 智能填充新区域' },
  { key: 'upscale', label: '高清放大', tip: 'ESRGAN / 4x-UltraSharp 模型超分，可选细节增强' },
  { key: 'facefix', label: '面部修复', tip: 'GFPGAN / CodeFormer 重建人脸细节' },
  { key: 'style', label: '风格迁移', tip: '用风格参考图经 IP-Adapter 统一色调/质感' },
  { key: 'controlnet', label: 'ControlNet', tip: '用原图提取边缘/深度/姿态等结构约束生成' },
  { key: 'reverse', label: '反推提示词', tip: '上传参考图，反推结构化提示词（需本地 Ollama）' },
]
const genModes = ['img2img', 'inpaint', 'outpaint', 'style', 'controlnet']
const mode = ref('img2img')
const modeTip = computed(() => modes.find(m => m.key === mode.value)?.tip || '')

function setMode(k) {
  mode.value = k
  // 按模式给出社区常用默认重绘幅度
  const def = { img2img: 0.65, inpaint: 0.75, outpaint: 0.8, style: 0.6, controlnet: 0.6 }
  if (k in def) strength.value = def[k]
  if (k === 'inpaint') nextTick(() => { clearMask() })
}

// 持久化部分参数（跨刷新保留习惯）
const persist = usePersistedState('wn_editor_state', {
  prompt: '', negative: '', strength: 0.65, steps: 20, cfg: 7.0,
  sampler: 'euler', scheduler: 'normal', seedRandom: true, seed: -1, count: 1,
  scale: 4, denoise: 0.15, feathering: 24, left: 128, top: 128, right: 128, bottom: 128,
  controlType: 'canny', controlWeight: 1.0, ipWeight: 0.8, faceMethod: 'GFPGAN',
})
const prompt = computed({ get: () => persist.prompt, set: v => persist.prompt = v })
const negative = computed({ get: () => persist.negative, set: v => persist.negative = v })
const strength = computed({ get: () => persist.strength, set: v => persist.strength = v })
const steps = computed({ get: () => persist.steps, set: v => persist.steps = v })
const cfg = computed({ get: () => persist.cfg, set: v => persist.cfg = v })
const sampler = computed({ get: () => persist.sampler, set: v => persist.sampler = v })
const scheduler = computed({ get: () => persist.scheduler, set: v => persist.scheduler = v })
const seedRandom = computed({ get: () => persist.seedRandom, set: v => persist.seedRandom = v })
const seed = computed({ get: () => persist.seed, set: v => persist.seed = v })
const count = computed({ get: () => persist.count, set: v => persist.count = v })
const scale = computed({ get: () => persist.scale, set: v => persist.scale = v })
const denoise = computed({ get: () => persist.denoise, set: v => persist.denoise = v })
const feathering = computed({ get: () => persist.feathering, set: v => persist.feathering = v })
const left = computed({ get: () => persist.left, set: v => persist.left = v })
const top = computed({ get: () => persist.top, set: v => persist.top = v })
const right = computed({ get: () => persist.right, set: v => persist.right = v })
const bottom = computed({ get: () => persist.bottom, set: v => persist.bottom = v })
const controlType = computed({ get: () => persist.controlType, set: v => persist.controlType = v })
const controlWeight = computed({ get: () => persist.controlWeight, set: v => persist.controlWeight = v })
const ipWeight = computed({ get: () => persist.ipWeight, set: v => persist.ipWeight = v })
const faceMethod = computed({ get: () => persist.faceMethod, set: v => persist.faceMethod = v })

const samplers = ['euler', 'euler_ancestral', 'dpmpp_2m', 'dpmpp_2m_sde', 'dpmpp_sde', 'ddim', 'uni_pc']
const schedulers = ['normal', 'karras', 'exponential', 'simple', 'ddim_uniform']

// ============================================================
// 源图 / 风格图 / 蒙版
// ============================================================
const sourceFile = ref(null)
const sourceUrl = ref('')
const styleFile = ref(null)
const styleUrl = ref('')
const imgEl = ref(null)
const maskEl = ref(null)
const brushSize = ref(48)
const eraser = ref(false)
const maskPainted = ref(false)
const drawing = ref(false)

function onSource(file) {
  if (!file.raw || !file.raw.type.startsWith('image/')) return
  if (sourceUrl.value) URL.revokeObjectURL(sourceUrl.value)
  sourceFile.value = file.raw
  sourceUrl.value = URL.createObjectURL(file.raw)
  compareBefore.value = sourceUrl.value
}
function onStyle(file) {
  if (!file.raw || !file.raw.type.startsWith('image/')) return
  if (styleUrl.value) URL.revokeObjectURL(styleUrl.value)
  styleFile.value = file.raw
  styleUrl.value = URL.createObjectURL(file.raw)
}
function onImgLoad() {
  const c = maskEl.value
  if (!c || !imgEl.value) return
  c.width = imgEl.value.naturalWidth
  c.height = imgEl.value.naturalHeight
  clearMask()
}
function maskPos(e) {
  const c = maskEl.value
  const r = c.getBoundingClientRect()
  const sx = c.width / r.width, sy = c.height / r.height
  return { x: (e.clientX - r.left) * sx, y: (e.clientY - r.top) * sy, sx }
}
function paintAt(x, y, sx) {
  const c = maskEl.value
  const ctx = c.getContext('2d')
  ctx.lineCap = 'round'; ctx.lineJoin = 'round'
  if (eraser.value) { ctx.globalCompositeOperation = 'destination-out'; ctx.fillStyle = 'rgba(0,0,0,1)' }
  else { ctx.globalCompositeOperation = 'source-over'; ctx.fillStyle = 'rgba(255,255,255,1)' }
  ctx.beginPath(); ctx.arc(x, y, Math.max(2, brushSize.value * sx), 0, Math.PI * 2); ctx.fill()
  maskPainted.value = true
}
function maskDown(e) { if (mode.value !== 'inpaint') return; drawing.value = true; const p = maskPos(e); paintAt(p.x, p.y, p.sx) }
function maskMove(e) { if (!drawing.value) return; const p = maskPos(e); paintAt(p.x, p.y, p.sx) }
function maskUp() { drawing.value = false }
function clearMask() {
  const c = maskEl.value; if (!c) return
  c.getContext('2d').clearRect(0, 0, c.width, c.height)
  maskPainted.value = false
}
function invertMask() {
  const c = maskEl.value; if (!c) return
  const ctx = c.getContext('2d')
  const d = ctx.getImageData(0, 0, c.width, c.height)
  for (let i = 3; i < d.data.length; i += 4) d.data[i] = 255 - d.data[i]
  ctx.putImageData(d, 0, 0); maskPainted.value = true
}
function maskBlob() {
  return new Promise(res => { if (maskEl.value) maskEl.value.toBlob(b => res(b), 'image/png'); else res(null) })
}

// ============================================================
// 生成 / 反推 / 历史
// ============================================================
const generating = ref(false)
const results = ref([])
const compareBefore = ref('')
const compareAfter = ref('')
const slider = ref(50)
const afterClip = computed(() => ({ clipPath: `inset(0 ${100 - slider.value}% 0 0)` }))
const historyVisible = ref(false)
const history = ref([])
const previewVisible = ref(false)
const previewItem = ref(null)

const estCredits = computed(() => {
  if (mode.value === 'upscale') return Math.ceil(scale.value / 2)
  if (mode.value === 'facefix') return 2
  return 2 * (Number(count.value) || 1)
})

async function postEdit(fd) {
  const r = await fetch('/api/v1/t2i/edit', { method: 'POST', body: fd })
  const j = await r.json()
  if (j.code !== 0) throw new Error(j.message || '编辑失败')
  return j.data
}

async function generate() {
  if (mode.value === 'reverse') { reversePrompt(); return }
  if (!sourceFile.value) { ElMessage.warning('请先上传源图'); return }
  if (mode.value === 'inpaint' && !maskPainted.value) { ElMessage.warning('请在图上涂抹需要重绘的区域'); return }
  if (mode.value === 'style' && !styleFile.value) { ElMessage.warning('风格迁移请先上传风格参考图'); return }
  if (generating.value) return
  generating.value = true
  try {
    const n = Math.min(4, Math.max(1, Number(count.value) || 1))
    for (let i = 0; i < n; i++) {
      const fd = new FormData()
      fd.append('image', sourceFile.value)
      fd.append('mode', mode.value)
      fd.append('prompt', prompt.value || '')
      fd.append('negative', negative.value || '')
      fd.append('strength', String(strength.value))
      fd.append('steps', String(steps.value))
      fd.append('cfg', String(cfg.value))
      fd.append('sampler', sampler.value)
      fd.append('scheduler', scheduler.value)
      fd.append('seed', seedRandom.value ? '-1' : String(seed.value))
      if (mode.value === 'inpaint') {
        const mb = await maskBlob(); if (mb) fd.append('mask', mb, 'mask.png')
      }
      if (mode.value === 'style' && styleFile.value) fd.append('ref', styleFile.value)
      if (mode.value === 'outpaint') {
        fd.append('left', String(left.value)); fd.append('top', String(top.value))
        fd.append('right', String(right.value)); fd.append('bottom', String(bottom.value))
        fd.append('feathering', String(feathering.value))
      }
      if (mode.value === 'upscale') {
        fd.append('scale', String(scale.value)); fd.append('denoise', String(denoise.value))
      }
      if (mode.value === 'facefix') fd.append('method', faceMethod.value)
      if (mode.value === 'controlnet') {
        fd.append('control_type', controlType.value); fd.append('control_weight', String(controlWeight.value))
      }
      const data = await postEdit(fd)
      results.value.unshift({ url: data.url, mode: data.mode, ts: Date.now() })
      compareBefore.value = sourceUrl.value
      compareAfter.value = data.url
    }
    ElMessage.success('编辑完成（' + n + ' 张）')
  } catch (e) {
    ElMessage.error(e.message || '编辑失败')
  } finally {
    generating.value = false
  }
}

async function reversePrompt() {
  if (!sourceFile.value) { ElMessage.warning('请先上传参考图'); return }
  try {
    const fd = new FormData(); fd.append('image', sourceFile.value)
    const r = await fetch('/api/v1/t2i/reverse', { method: 'POST', body: fd })
    const j = await r.json()
    if (j.code !== 0) throw new Error(j.message || '反推失败')
    prompt.value = j.data.prompt || ''
    negative.value = j.data.negative_prompt || negative.value
    ElMessage.success('已反推提示词，可在此基础上修改')
  } catch (e) {
    ElMessage.error(e.message || '反推失败')
  }
}

async function loadHistory() {
  historyVisible.value = true
  try {
    const r = await fetch('/api/v1/t2i/tasks?page=1&size=60')
    const j = await r.json()
    if (j.code !== 0) throw new Error(j.message || '加载失败')
    const items = (j.data.items || []).filter(it => (it.params && it.params.kind) === 'edit')
    history.value = items.map(it => ({
      url: (it.result && it.result.url) || '',
      mode: (it.params && it.params.mode) || '',
      ts: it.created_at,
    })).filter(h => h.url)
    if (!history.value.length) ElMessage.info('暂无编辑历史')
  } catch (e) {
    ElMessage.error(e.message || '加载历史失败')
  }
}

function openPreview(item) { previewItem.value = item; previewVisible.value = true }
function download(item) {
  if (!item.url) return
  const a = document.createElement('a'); a.href = item.url; a.download = 'edit.png'; a.click()
  ElMessage.success('已开始下载')
}
function useAsSource(item) {
  if (!item.url) return
  fetch(item.url).then(r => r.blob()).then(b => {
    const f = new File([b], 'history.png', { type: 'image/png' })
    sourceFile.value = f; sourceUrl.value = URL.createObjectURL(f)
    compareBefore.value = sourceUrl.value
    ElMessage.success('已载入为源图')
  })
}
</script>

<template>
  <div class="page editor">
    <div class="page-head">
      <div>
        <h1>🖼 专业图片编辑</h1>
        <p class="muted">基于 ComfyUI 工作流：局部重绘 · 扩图 · 高清放大 · 面部修复 · 风格迁移 · ControlNet · 提示词反推</p>
      </div>
      <el-tag type="warning">预计消耗 {{ estCredits }} 积分</el-tag>
    </div>

    <!-- 模式切换 -->
    <div class="segment-tabs mode-tabs">
      <button v-for="m in modes" :key="m.key" :class="{ active: mode === m.key }" @click="setMode(m.key)">
        {{ m.label }}
      </button>
    </div>
    <div class="mode-tip muted">{{ modeTip }}</div>

    <div class="ws-grid">
      <!-- 左侧参数面板 -->
      <div class="panel">
        <!-- 源图 -->
        <div class="param-group">
          <div class="g-title">源图（必填）</div>
          <el-upload drag :auto-upload="false" :show-file-list="false" :on-change="onSource" accept="image/*" class="up">
            <el-icon style="font-size:34px;color:var(--sub)"><Picture /></el-icon>
            <div class="el-upload__text">拖拽图片到此处，或 <em>点击上传</em></div>
          </el-upload>
          <div v-if="sourceUrl" class="src-preview">
            <div class="canvas-wrap">
              <img ref="imgEl" :src="sourceUrl" @load="onImgLoad" alt="源图" />
              <canvas v-show="mode === 'inpaint'" ref="maskEl" class="mask-canvas"
                @mousedown.prevent="maskDown" @mousemove="maskMove" @mouseup="maskUp" @mouseleave="maskUp"></canvas>
            </div>
            <div v-if="mode === 'inpaint'" class="mask-tools">
              <span class="lab">笔刷 {{ brushSize }}</span>
              <el-slider v-model="brushSize" :min="5" :max="160" style="width:130px" />
              <button class="tool" :class="{ on: !eraser }" @click="eraser = false">画笔</button>
              <button class="tool" :class="{ on: eraser }" @click="eraser = true">橡皮</button>
              <button class="tool" @click="clearMask">清除</button>
              <button class="tool" @click="invertMask">反相</button>
            </div>
            <div v-if="mode === 'inpaint'" class="muted small">在图上涂抹需要重绘的区域（白色），遮罩外区域保持不变</div>
          </div>
        </div>

        <transition name="modefade" mode="out-in">
        <div class="dyn" :key="mode">
        <!-- 风格参考图（风格迁移） -->
        <div class="param-group" v-if="mode === 'style'">
          <div class="g-title">风格参考图（必填）</div>
          <el-upload drag :auto-upload="false" :show-file-list="false" :on-change="onStyle" accept="image/*" class="up">
            <el-icon style="font-size:30px;color:var(--sub)"><Picture /></el-icon>
            <div class="el-upload__text">上传风格参考图</div>
          </el-upload>
          <div v-if="styleUrl" class="thumbs"><div class="thumb"><img :src="styleUrl" alt="风格" /></div></div>
          <div class="param-row" style="margin-top:10px">
            <span class="lab">风格强度</span>
            <el-slider v-model="ipWeight" :min="0.2" :max="1.2" :step="0.05" style="width:200px" />
            <span class="val">{{ ipWeight.toFixed(2) }}</span>
          </div>
        </div>

        <!-- 扩图边距 -->
        <div class="param-group" v-if="mode === 'outpaint'">
          <div class="g-title">扩展边距（像素）</div>
          <div class="param-row"><span class="lab">左</span><el-slider v-model="left" :min="0" :max="512" style="width:200px" /><span class="val">{{ left }}</span></div>
          <div class="param-row"><span class="lab">上</span><el-slider v-model="top" :min="0" :max="512" style="width:200px" /><span class="val">{{ top }}</span></div>
          <div class="param-row"><span class="lab">右</span><el-slider v-model="right" :min="0" :max="512" style="width:200px" /><span class="val">{{ right }}</span></div>
          <div class="param-row"><span class="lab">下</span><el-slider v-model="bottom" :min="0" :max="512" style="width:200px" /><span class="val">{{ bottom }}</span></div>
          <div class="param-row"><span class="lab">羽化</span><el-slider v-model="feathering" :min="0" :max="64" style="width:200px" /><span class="val">{{ feathering }}</span></div>
          <div class="muted small">羽化越大，原图与新增区域过渡越平滑</div>
        </div>

        <!-- 高清放大 -->
        <div class="param-group" v-if="mode === 'upscale'">
          <div class="g-title">放大设置</div>
          <div class="param-row"><span class="lab">倍数</span>
            <el-radio-group v-model="scale">
              <el-radio-button :value="2">2x</el-radio-button>
              <el-radio-button :value="4">4x</el-radio-button>
            </el-radio-group>
          </div>
          <div class="param-row"><span class="lab">细节增强</span>
            <el-slider v-model="denoise" :min="0" :max="0.4" :step="0.01" style="width:200px" />
            <span class="val">{{ denoise.toFixed(2) }}</span>
          </div>
          <div class="muted small">放大模型自动优选（4x-UltraSharp / RealESRGAN 等）；细节增强会在放大后追加一次轻量重绘</div>
        </div>

        <!-- 面部修复 -->
        <div class="param-group" v-if="mode === 'facefix'">
          <div class="g-title">修复方法</div>
          <el-radio-group v-model="faceMethod">
            <el-radio-button value="GFPGAN">GFPGAN</el-radio-button>
            <el-radio-button value="CodeFormer">CodeFormer</el-radio-button>
          </el-radio-group>
          <div class="muted small">需在 ComfyUI 安装对应节点并放入权重</div>
        </div>

        <!-- ControlNet -->
        <div class="param-group" v-if="mode === 'controlnet'">
          <div class="g-title">结构控制</div>
          <div class="param-row"><span class="lab">类型</span>
            <el-select v-model="controlType" style="width:200px">
              <el-option label="边缘 Canny" value="canny" />
              <el-option label="深度 Depth" value="depth" />
              <el-option label="姿态 OpenPose" value="openpose" />
              <el-option label="线稿 LineArt" value="lineart" />
              <el-option label="涂鸦 Scribble" value="scribble" />
            </el-select>
          </div>
          <div class="param-row"><span class="lab">控制强度</span>
            <el-slider v-model="controlWeight" :min="0.2" :max="2" :step="0.05" style="width:200px" />
            <span class="val">{{ controlWeight.toFixed(2) }}</span>
          </div>
        </div>

        <!-- 生成类通用参数 -->
        <div class="param-group" v-if="genModes.includes(mode)">
          <div class="g-title">提示词</div>
          <el-input v-model="prompt" type="textarea" :rows="3" placeholder="描述想要的改动，如：更暖的色调、增加雾气、换成夜晚" />
          <div class="param-row" style="margin-top:10px;align-items:flex-start">
            <span class="lab" style="margin-top:6px">反向</span>
            <el-input v-model="negative" type="textarea" :rows="2" placeholder="不想要的元素" />
          </div>
          <div class="param-row"><span class="lab">重绘幅度</span>
            <el-slider v-model="strength" :min="0" :max="1" :step="0.01" style="width:200px" />
            <span class="val">{{ strength.toFixed(2) }}</span>
          </div>
          <div class="param-row"><span class="lab">步数</span><el-input-number v-model="steps" :min="1" :max="60" :controls="false" style="width:90px" /></div>
          <div class="param-row"><span class="lab">CFG</span><el-input-number v-model="cfg" :min="1" :max="20" :step="0.5" :controls="false" style="width:90px" /></div>
          <div class="param-row"><span class="lab">采样器</span>
            <el-select v-model="sampler" style="width:200px"><el-option v-for="s in samplers" :key="s" :label="s" :value="s" /></el-select>
          </div>
          <div class="param-row"><span class="lab">调度器</span>
            <el-select v-model="scheduler" style="width:200px"><el-option v-for="s in schedulers" :key="s" :label="s" :value="s" /></el-select>
          </div>
          <div class="param-row"><span class="lab">种子</span>
            <el-switch v-model="seedRandom" active-text="随机" inactive-text="固定" />
            <el-input-number v-if="!seedRandom" v-model="seed" :controls="false" style="width:120px" />
          </div>
          <div class="param-row" v-if="mode !== 'facefix'"><span class="lab">批次数</span>
            <el-radio-group v-model="count">
              <el-radio-button :value="1">1</el-radio-button>
              <el-radio-button :value="2">2</el-radio-button>
              <el-radio-button :value="4">4</el-radio-button>
            </el-radio-group>
          </div>
        </div>
        </div>
        </transition>

        <!-- 操作 -->
        <div class="param-group">
          <button class="generate-btn" style="width:100%;justify-content:center" :disabled="generating" @click="generate">
            {{ generating ? '处理中…' : (mode === 'reverse' ? '反推提示词' : '开始编辑') }}
          </button>
          <div class="act-row" v-if="genModes.includes(mode)">
            <button class="btn ghost" :disabled="!sourceFile" @click="reversePrompt">反推提示词</button>
            <button class="btn ghost" @click="loadHistory">编辑历史</button>
          </div>
          <el-progress v-if="generating" :indeterminate="true" :duration="1.5" :stroke-width="8"
            style="margin-top:12px" :color="'var(--pur)'" />
        </div>
      </div>

      <!-- 右侧对比 + 结果 + 历史 -->
      <div class="panel">
        <div class="g-title">前后对比 <span class="muted">（拖动滑块查看原图 / 结果）</span></div>
        <div class="compare" v-if="compareAfter || sourceUrl">
          <img class="c-before" :src="compareBefore || sourceUrl" alt="原图" />
          <img v-if="compareAfter" class="c-after" :src="compareAfter" :style="afterClip" alt="结果" />
          <input class="c-slider" type="range" min="0" max="100" v-model="slider" />
          <div class="c-tag b">原图</div>
          <div class="c-tag a">结果</div>
        </div>
        <div v-else class="empty">上传源图并生成后，这里显示前后对比。</div>

        <div class="g-title" style="margin-top:16px">结果 <span class="muted">（{{ results.length }} 张）</span></div>
        <div v-if="!results.length" class="empty small">暂无结果</div>
        <transition-group name="cardpop" tag="div" class="asset-grid">
          <div class="asset-card" v-for="(it, i) in results" :key="i" @click="openPreview(it)">
            <div class="ph"><img :src="it.url" alt="结果" />
              <div class="ph-acts" @click.stop>
                <button class="btn sm ghost" @click.stop="download(it)">下载</button>
                <button class="btn sm ghost" @click.stop="openPreview(it)">放大</button>
              </div>
            </div>
            <div class="info"><div class="name">{{ it.mode }}</div><div class="type">{{ new Date(it.ts).toLocaleTimeString() }}</div></div>
          </div>
        </transition-group>

        <template v-if="historyVisible">
          <div class="g-title" style="margin-top:16px">编辑历史 <span class="muted">（{{ history.length }} 条）</span></div>
          <div v-if="!history.length" class="empty small">暂无历史</div>
          <div class="asset-grid">
            <div class="asset-card" v-for="(it, i) in history" :key="i">
              <div class="ph"><img :src="it.url" alt="历史" />
                <div class="ph-acts" @click.stop>
                  <button class="btn sm ghost" @click.stop="useAsSource(it)">载入源图</button>
                  <button class="btn sm ghost" @click.stop="openPreview(it)">放大</button>
                </div>
              </div>
              <div class="info"><div class="name">{{ it.mode }}</div><div class="type">{{ it.ts }}</div></div>
            </div>
          </div>
        </template>

        <transition name="fade">
          <div v-if="generating" class="gen-cover">
            <div class="spin"></div>
            <div class="txt">正在处理，请稍候…</div>
          </div>
        </transition>
      </div>
    </div>

    <el-dialog v-model="previewVisible" title="预览" width="60%" align-center>
      <div v-if="previewItem" class="big-ph">
        <img :src="previewItem.url" alt="预览" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ===== 固定窗口高度：根容器填满 .page，内部面板自行滚动，切换页签/内容增多不再撑高整窗 ===== */
.page.editor { padding: 0; overflow: hidden; }
.editor { height: 100%; box-sizing: border-box; padding: 20px 24px; display: flex; flex-direction: column; }
.editor .page-head { flex: 0 0 auto; }
.editor .mode-tabs { flex: 0 0 auto; margin: 4px 0 2px; }
.mode-tip { flex: 0 0 auto; min-height: 19px; margin: 2px 0 14px; font-size: 13px; }

.ws-grid { display: grid; grid-template-columns: 400px 1fr; grid-template-rows: minmax(0, 1fr); gap: 18px; flex: 1 1 auto; min-height: 0; }
.panel { position: relative; background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; overflow-y: auto; min-height: 0; }
.dyn { display: block; }

.up :deep(.el-upload-dragger) { background: var(--bg); border-color: var(--line); padding: 18px; }
.up :deep(.el-upload-dragger .el-upload__text) { color: var(--sub); font-size: 13px; }

.src-preview { margin-top: 12px; }
.canvas-wrap { position: relative; display: inline-block; max-width: 100%; border-radius: 10px; overflow: hidden; border: 1px solid var(--line); background: var(--bg); }
.canvas-wrap img { display: block; max-width: 100%; max-height: 460px; }
.mask-canvas { position: absolute; inset: 0; width: 100%; height: 100%; cursor: crosshair; }
.mask-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.mask-tools .tool { padding: 4px 10px; border-radius: 8px; border: 1px solid var(--line); background: transparent; color: var(--sub); cursor: pointer; font-size: 12px; transition: .15s; }
.mask-tools .tool:hover { border-color: var(--pur); color: var(--txt); }
.mask-tools .tool.on { border-color: var(--pur); color: var(--txt); background: color-mix(in srgb, var(--pur) 16%, transparent); }
.thumbs { display: flex; gap: 10px; margin-top: 10px; }
.thumb { width: 92px; }
.thumb img { width: 92px; height: 92px; object-fit: cover; border-radius: 10px; border: 1px solid var(--line); }

.param-group { padding: 12px 0; border-bottom: 1px solid var(--line); }
.param-group:last-child { border-bottom: none; }
.g-title { font-size: 13px; font-weight: 600; color: var(--txt); margin-bottom: 10px; }
.param-row { display: flex; align-items: center; gap: 10px; margin: 10px 0; }
.param-row .lab { width: 64px; font-size: 13px; color: var(--sub); flex: none; }
.param-row .val { width: 46px; text-align: right; font-size: 12px; color: var(--sub); }
.muted { color: var(--sub); font-size: 13px; }
.small { font-size: 12px; }

.generate-btn { display: inline-flex; align-items: center; gap: 8px; padding: 11px 18px; border-radius: 12px; border: none; cursor: pointer;
  color: #fff; font-weight: 600; background: var(--grad);
  box-shadow: 0 6px 20px color-mix(in srgb, var(--pur) 30%, transparent), inset 0 1px 0 rgba(255,255,255,.25); transition: transform .18s ease, filter .18s ease, box-shadow .25s ease; }
.generate-btn:hover:not(:disabled) { transform: translateY(-1px); filter: brightness(1.06); }
.generate-btn:disabled { opacity: .55; cursor: not-allowed; }
.act-row { display: flex; gap: 10px; margin-top: 10px; }
.btn.ghost { padding: 8px 12px; border-radius: 10px; border: 1px solid var(--line); background: var(--card-2); color: var(--txt); cursor: pointer; font-size: 13px; transition: .18s; }
.btn.ghost:hover:not(:disabled) { border-color: var(--pur); color: var(--txt); background: color-mix(in srgb, var(--pur) 14%, var(--card-2)); }

.compare { position: relative; border-radius: 12px; overflow: hidden; border: 1px solid var(--line); background: var(--bg); user-select: none; }
.compare img { display: block; width: 100%; }
.compare .c-after { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.compare .c-slider { position: absolute; left: 0; right: 0; bottom: 8px; width: 90%; margin: 0 auto; }
.c-tag { position: absolute; top: 8px; padding: 2px 8px; border-radius: 6px; font-size: 11px; background: rgba(0,0,0,.5); color: #fff; }
.c-tag.b { left: 8px; } .c-tag.a { right: 8px; }

.asset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 12px; }
.asset-card { border-radius: 12px; overflow: hidden; border: 1px solid var(--line); background: var(--bg); cursor: pointer; transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease; }
.asset-card:hover { transform: translateY(-2px); border-color: var(--pur); box-shadow: 0 8px 22px color-mix(in srgb, var(--pur) 22%, transparent); }
.asset-card .ph { position: relative; aspect-ratio: 1; background: var(--bg-2); }
.asset-card .ph img { width: 100%; height: 100%; object-fit: cover; }
.ph-acts { position: absolute; inset: auto 0 0 0; display: flex; gap: 6px; justify-content: center; padding: 8px; opacity: 0; transition: .2s; background: linear-gradient(0deg, rgba(0,0,0,.55), transparent); }
.asset-card:hover .ph-acts { opacity: 1; }
.btn.sm { padding: 3px 8px; font-size: 12px; border-radius: 7px; border: 1px solid var(--line); background: color-mix(in srgb, var(--card) 78%, transparent); color: var(--txt); cursor: pointer; backdrop-filter: blur(4px); transition: .15s; }
.btn.sm:hover { border-color: var(--pur); }
.info { padding: 7px 9px; }
.info .name { font-size: 12px; color: var(--txt); }
.info .type { font-size: 11px; color: var(--sub); margin-top: 2px; }
.empty { padding: 24px; text-align: center; color: var(--sub); border: 1px dashed var(--line); border-radius: 12px; }
.big-ph img { width: 100%; border-radius: 12px; display: block; }

/* ===== 生成中磨砂遮罩 ===== */
.gen-cover { position: absolute; inset: 0; display: flex; flex-direction: column; gap: 12px; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--card) 55%, transparent); backdrop-filter: blur(3px); border-radius: 14px; z-index: 5; }
.gen-cover .spin { width: 34px; height: 34px; border-radius: 50%; border: 3px solid var(--line); border-top-color: var(--pur); animation: edspin .8s linear infinite; }
.gen-cover .txt { font-size: 13px; color: var(--txt); }
@keyframes edspin { to { transform: rotate(360deg); } }

/* ===== 模式切换淡入淡出（out-in） ===== */
.modefade-enter-active, .modefade-leave-active { transition: opacity .22s ease, transform .22s ease; }
.modefade-enter-from { opacity: 0; transform: translateY(6px); }
.modefade-leave-to { opacity: 0; transform: translateY(-6px); }

/* ===== 结果卡片弹入 ===== */
.cardpop-enter-active { transition: opacity .3s ease, transform .3s cubic-bezier(.34,1.56,.64,1); }
.cardpop-enter-from { opacity: 0; transform: scale(.86); }
.cardpop-leave-active { transition: opacity .2s ease; }
.cardpop-leave-to { opacity: 0; }
.cardpop-move { transition: transform .3s ease; }
</style>
