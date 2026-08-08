<script setup>
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { usePersistedState } from '../composables/usePersist'

defineOptions({ name: 'Img2Img' })

const resolutions = ['720p', '1080p', '4K']
const ratios = ['16:9', '9:16', '1:1', '4:3']

const grads = [
  ['#ff4d8d', '#8a5cff'], ['#4d9fff', '#3ddc84'], ['#ffb020', '#ff4d8d'],
  ['#8a5cff', '#4d9fff'], ['#3ddc84', '#8a5cff'], ['#ff7696', '#ffb020'],
  ['#5b6cff', '#ff4d8d'], ['#22d3ee', '#8a5cff'], ['#f472b6', '#a78bfa'],
]
const emojis = ['🖼', '🌆', '🌃', '🎭', '✨', '🌟', '🌈', '🪄', '🌉']

const state = usePersistedState('wn_i2i_state', {
  prompt: '',
  strength: 0.65,
  count: 4,
  resolution: '1080p',
  ratio: '16:9',
})

const uploaded = ref([]) // { name, url }
const results = ref([])
const generating = ref(false)
const progress = ref(0)

let seq = 0
function makeImage(base) {
  const i = seq++
  const g = base || grads[i % grads.length]
  const e = emojis[i % emojis.length]
  return {
    id: i,
    name: '变体 ' + (i + 1),
    grad: `linear-gradient(135deg, ${g[0]}, ${g[1]})`,
    emoji: e,
    res: state.resolution,
    ratio: state.ratio,
  }
}

// 上传参考图（本地预览，不真正上传）
function handleChange(file) {
  if (!file.raw || !file.raw.type.startsWith('image/')) return
  uploaded.value.push({ name: file.name, url: URL.createObjectURL(file.raw) })
}
function removeUpload(idx) {
  uploaded.value.splice(idx, 1)
}

const estCredits = computed(() => {
  const base = { '720p': 2, '1080p': 4, '4K': 8 }[state.resolution] || 4
  return base * state.count
})

function generate() {
  if (!uploaded.value.length) {
    ElMessage.warning('请先上传一张参考图（演示）')
    return
  }
  if (generating.value) return
  generating.value = true
  progress.value = 0
  const n = Number(state.count)

  const timer = setInterval(() => {
    progress.value = Math.min(100, progress.value + 9 + Math.random() * 11)
    if (progress.value >= 100) {
      clearInterval(timer)
      // 以参考图渐变为底，生成 N 个变体
      const base = grads[Math.floor(Math.random() * grads.length)]
      for (let k = 0; k < n; k++) results.value.push(makeImage())
      generating.value = false
      ElMessage.success(`已生成 ${n} 个变体（演示）`)
    }
  }, 220)
}

const previewVisible = ref(false)
const previewItem = ref(null)
function openPreview(item) { previewItem.value = item; previewVisible.value = true }
function download(item) { ElMessage.success(`已开始下载「${item.name}」（演示）`) }
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>🪄 图生图</h1>
        <p class="muted">上传参考图并调整强度，生成风格一致的多个变体（演示）</p>
      </div>
      <el-tag type="warning" effect="dark">预计消耗 {{ estCredits }} 积分</el-tag>
    </div>

    <div class="ws-grid">
      <div class="panel">
        <div class="param-group">
          <div class="g-title">参考图</div>
          <el-upload drag :auto-upload="false" :show-file-list="false" :on-change="handleChange"
            accept="image/*" class="up">
            <el-icon style="font-size:34px;color:var(--sub)"><Picture /></el-icon>
            <div class="el-upload__text">拖拽图片到此处，或 <em>点击上传</em></div>
          </el-upload>
          <div class="thumbs" v-if="uploaded.length">
            <div class="thumb" v-for="(u, i) in uploaded" :key="i">
              <img :src="u.url" :alt="u.name" />
              <button class="btn sm ghost del" @click="removeUpload(i)">×</button>
              <span class="tn">{{ u.name }}</span>
            </div>
          </div>
        </div>

        <div class="param-group">
          <div class="g-title">提示词</div>
          <el-input v-model="state.prompt" type="textarea" :rows="3"
            placeholder="在参考图基础上描述想要的改动，如：更暖的色调、增加雾气" />
          <div class="param-row" style="margin-top:10px;align-items:flex-start">
            <span class="lab" style="margin-top:6px">重绘强度</span>
            <div style="width:200px">
              <el-slider v-model="state.strength" :min="0" :max="1" :step="0.01" />
              <div class="muted" style="text-align:right">{{ state.strength.toFixed(2) }}</div>
            </div>
          </div>
        </div>

        <div class="param-group">
          <div class="g-title">变体与参数</div>
          <div class="param-row">
            <span class="lab">变体数量</span>
            <el-radio-group v-model="state.count">
              <el-radio-button :value="2">2</el-radio-button>
              <el-radio-button :value="4">4</el-radio-button>
              <el-radio-button :value="6">6</el-radio-button>
            </el-radio-group>
          </div>
          <div class="param-row">
            <span class="lab">分辨率</span>
            <el-select v-model="state.resolution" style="width:200px">
              <el-option v-for="r in resolutions" :key="r" :label="r" :value="r" />
            </el-select>
          </div>
          <div class="param-row">
            <span class="lab">比例</span>
            <el-select v-model="state.ratio" style="width:200px">
              <el-option v-for="r in ratios" :key="r" :label="r" :value="r" />
            </el-select>
          </div>
        </div>

        <button class="btn pink" style="width:100%;justify-content:center;margin-top:14px"
          :disabled="generating" @click="generate">
          {{ generating ? '生成中…' : '生成变体' }}
        </button>

        <div class="progress" v-if="generating || progress >= 100">
          <div class="lab"><span>图生图推理中…（演示）</span><span>{{ Math.round(progress) }}%</span></div>
          <el-progress :percentage="Math.round(progress)" :stroke-width="8" :color="['#ff4d8d', '#8a5cff']" />
        </div>
      </div>

      <div class="panel">
        <div class="g-title" style="margin-bottom:4px">变体结果 <span class="muted">（{{ results.length }} 张）</span></div>
        <div v-if="!results.length && !generating" class="empty">还没有变体，上传参考图后点击「生成变体」。</div>
        <div class="asset-grid">
          <div class="asset-card" v-for="item in results" :key="item.id" @click="openPreview(item)">
            <div class="ph" :style="{ background: item.grad }">
              <span class="ph-emoji">{{ item.emoji }}</span>
              <div class="ph-acts" @click.stop>
                <button class="btn sm ghost" @click.stop="download(item)">下载</button>
                <button class="btn sm ghost" @click.stop="openPreview(item)">放大</button>
              </div>
            </div>
            <div class="info">
              <div class="name">{{ item.name }}</div>
              <div class="type">{{ item.res }} · {{ item.ratio }} · 强度 {{ state.strength.toFixed(2) }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="previewVisible" title="预览（演示）" width="60%" align-center>
      <div v-if="previewItem" class="big-ph" :style="{ background: previewItem.grad }">
        <span>{{ previewItem.emoji }}</span>
      </div>
      <div class="muted" style="margin-top:10px">{{ previewItem?.name }} · {{ previewItem?.res }} · {{ previewItem?.ratio }}</div>
    </el-dialog>
  </div>
</template>

<style scoped>
.ws-grid { display: grid; grid-template-columns: 380px 1fr; gap: 18px; align-items: start; }
.panel { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
.up :deep(.el-upload-dragger) { background: #0f1117; border-color: var(--line); padding: 22px; }
.thumbs { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }
.thumb { position: relative; width: 84px; }
.thumb img { width: 84px; height: 84px; object-fit: cover; border-radius: 10px; border: 1px solid var(--line); display: block; }
.thumb .del { position: absolute; top: 4px; right: 4px; padding: 0 7px; line-height: 20px; }
.thumb .tn { display: block; font-size: 11px; color: var(--sub); margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ph { position: relative; aspect-ratio: 1; display: flex; align-items: center; justify-content: center; }
.ph-emoji { font-size: 42px; filter: drop-shadow(0 2px 6px rgba(0,0,0,.4)); }
.ph-acts { position: absolute; inset: auto 0 0 0; display: flex; gap: 6px; justify-content: center; padding: 8px;
  opacity: 0; transition: .2s; background: linear-gradient(0deg, rgba(0,0,0,.55), transparent); }
.asset-card:hover .ph-acts { opacity: 1; }
.big-ph { width: 100%; aspect-ratio: 16/9; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 80px; }
</style>
