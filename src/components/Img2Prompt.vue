<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'

const promptPool = [
  'cinematic, rainy night, neon city, highly detailed, 8k, bokeh, reflections on wet street',
  'portrait of a young woman, soft studio light, film grain, shallow depth of field, kodak tone',
  'ancient temple in mist, volumetric light, fantasy concept art, intricate details, epic scale',
  'cyberpunk alley, holographic signs, smoke, teal and magenta palette, ultra sharp',
  'cozy cafe interior, warm sunlight, wooden textures, lifestyle photography, 35mm',
]
const negativeSugg = 'blurry, lowres, deformed, extra fingers, watermark, text, jpeg artifacts, oversaturated'

const state = reactive({ file: null }) // { name, url }
const positive = ref('')
const negative = ref('')
const reversing = ref(false)
const done = ref(false)

const history = ref([
  { id: 1, name: 'scene_0421.png', prompt: 'cinematic, mountain lake at dawn, golden hour, mist…', time: '08-04 21:12' },
  { id: 2, name: 'char_a.png', prompt: 'anime girl, silver hair, school uniform, soft pastel…', time: '08-03 19:40' },
  { id: 3, name: 'poster_v3.jpg', prompt: 'retro film poster, bold typography, grain, vintage…', time: '08-02 10:05' },
])

function handleChange(file) {
  if (!file.raw || !file.raw.type.startsWith('image/')) return
  if (state.file && state.file.url) URL.revokeObjectURL(state.file.url)
  state.file = { name: file.name, url: URL.createObjectURL(file.raw) }
  done.value = false
  positive.value = ''
  negative.value = ''
}

function reverse() {
  if (!state.file) {
    ElMessage.warning('请先上传一张图片（演示）')
    return
  }
  if (reversing.value) return
  reversing.value = true
  const t = setTimeout(() => {
    positive.value = promptPool[Math.floor(Math.random() * promptPool.length)]
    negative.value = negativeSugg
    done.value = true
    reversing.value = false
    history.value.unshift({
      id: Date.now(),
      name: state.file.name,
      prompt: positive.value.length > 40 ? positive.value.slice(0, 40) + '…' : positive.value,
      time: new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }),
    })
    if (history.value.length > 8) history.value.pop()
    ElMessage.success('反推完成（演示）')
    clearTimeout(t)
  }, 900)
}

async function copyToTxt2img() {
  if (!positive.value) {
    ElMessage.warning('暂无可复制的提示词（演示）')
    return
  }
  try {
    await navigator.clipboard.writeText(positive.value)
  } catch (e) {
    // 忽略，仍写入 localStorage
  }
  localStorage.setItem('wn_img2prompt_last', positive.value)
  ElMessage.success('已复制，去文生图粘贴（演示）')
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>🔁 图片反推</h1>
        <p class="muted">上传图片，反推出可用于文生图的提示词（演示，不调用真实模型）</p>
      </div>
    </div>

    <div class="ws-grid">
      <div class="panel">
        <div class="param-group">
          <div class="g-title">上传图片</div>
          <el-upload drag :auto-upload="false" :show-file-list="false" :on-change="handleChange"
            accept="image/*" class="up">
            <el-icon style="font-size:34px;color:var(--sub)"><Picture /></el-icon>
            <div class="el-upload__text">拖拽图片到此处，或 <em>点击上传</em></div>
          </el-upload>
          <div class="preview-box" style="margin-top:12px" v-if="state.file">
            <img :src="state.file.url" :alt="state.file.name" />
          </div>
          <div class="preview-box" style="margin-top:12px" v-else>
            <span class="empty">尚未上传图片</span>
          </div>
        </div>

        <button class="btn pink" style="width:100%;justify-content:center;margin-top:14px"
          :disabled="!state.file || reversing" @click="reverse">
          {{ reversing ? '反推中…' : '反推提示词' }}
        </button>
        <el-button class="btn-copy" type="primary" plain :disabled="!done"
          style="width:100%;margin:10px 0 0;justify-content:center" @click="copyToTxt2img">
          一键复制到文生图
        </el-button>
      </div>

      <div class="panel">
        <div class="g-title">反推结果</div>
        <div v-if="!done" class="empty">上传图片并点击「反推提示词」后，结果会显示在这里。</div>
        <template v-else>
          <div class="g-title" style="margin:6px 0">正向提示词（英文）</div>
          <el-input v-model="positive" type="textarea" :rows="5" readonly />
          <div class="g-title" style="margin:14px 0 6px">反向提示词建议</div>
          <el-input v-model="negative" type="textarea" :rows="3" readonly />
        </template>

        <div class="g-title" style="margin:18px 0 6px">反推历史</div>
        <div class="list-row" v-for="h in history" :key="h.id">
          <div style="flex:1;min-width:0">
            <div class="name">{{ h.name }}</div>
            <div class="t">{{ h.prompt }}</div>
          </div>
          <span class="t">{{ h.time }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ws-grid { display: grid; grid-template-columns: 360px 1fr; gap: 18px; align-items: start; }
.panel { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
.up :deep(.el-upload-dragger) { background: #0f1117; border-color: var(--line); padding: 22px; }
.btn-copy :deep(.el-icon) { margin-right: 4px; }
</style>
