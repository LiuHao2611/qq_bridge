<script setup>
// ============================================================
// LoRA 训练模块前端（手册 §8 / §9 / 设计思路 §3）
// 五区：①类型切换 ②数据集 ③参数配置 ④训练进度(WS) ⑤模型管理
// 后端诚实降级：引擎(kohya_ss)/ComfyUI 缺失时一律显式提示，不静默失败。
// ------------------------------------------------------------
// P5 激进异形试点（2026-08-14）：仅在视图层叠加异形视觉（halftone/斜切面板/
// 多边形切割/胶带/星爆/超大编号），所有业务逻辑与 WS/模型管理保持不变（无创性铁律）。
// ============================================================
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import {
  loraPresets, createLoraTrain, listLoraTrain, cancelLoraTrain,
  loraCaption, listLoraModels, registerLoraModel, enableLoraModel,
  disableLoraModel, deleteLoraModel, validateLoraModel, uploadLoraDataset,
  listAssets,
} from '../api.js'
import { subscribe } from '../ws.js'

// ---- 引擎可用性 / 预设 ----
const engine = reactive({ available: true, path: '', reason: '', train_script: '', caption_script: '' })
const presets = ref([])
const defaultPreset = ref('6gb_quality')
const chosenPreset = ref('6gb_quality')

// ---- ① 类型切换 ----
const loraType = ref('character') // character | style

// ---- ② 数据集 ----
const assetList = ref([])      // 角色资产（character 导入用）
const sceneList = ref([])      // 场景/道具（style 导入备用）
const dsMode = ref('upload')   // 'upload' | 'asset'
const selectedAssetId = ref('')
const pendingFiles = ref([])   // el-upload 收集的原始 File
const datasetDir = ref('')
const uploadedCount = ref(0)
const building = ref(false)
const dsValidation = reactive({ ok: false, message: '' })
const captioning = ref(false)
const captionResult = reactive({ count: 0, caption_dir: '' })

// ---- ③ 参数配置 ----
const name = ref('')
const triggerWord = ref('')
const baseModel = ref('v1-5-pruned.safetensors')
const seed = ref(42)
const showAdvanced = ref(false)

// ---- ④ 训练进度 ----
const training = ref(false)
const trainStatus = ref('')
const curStep = ref(0)
const totSteps = ref(0)
const progress = ref(0)
const curLoss = ref(null)
const lossHistory = ref([])   // [{step, loss}]
const etaText = ref('')
const trainError = ref('')
const lastTaskId = ref('')
const _unsub = ref(null)
let _lastProgTs = 0
let _lastStep = 0

// ---- ⑤ 模型管理 / 任务记录 ----
const models = ref([])
const tasks = ref([])
const loadingModels = ref(false)
const vpair = reactive({ open: false, baseline: '', with_lora: '', loading: false, err: '' })

function parseTags(t) {
  if (!t) return []
  if (Array.isArray(t)) return t
  try { const a = JSON.parse(t); return Array.isArray(a) ? a : [String(t)] } catch { return [String(t)] }
}

// ---- 加载 ----
async function loadPresets() {
  try {
    const r = await loraPresets()
    presets.value = r.presets || []
    const def = presets.value.find((p) => p.is_default) || presets.value[0]
    if (def) { defaultPreset.value = def.key; chosenPreset.value = def.key }
    const e = r.engine || {}
    engine.available = !!e.available
    engine.path = e.path || ''
    engine.reason = e.reason || ''
    engine.train_script = e.train_script || ''
    engine.caption_script = e.caption_script || ''
  } catch (e) {
    ElMessage.error('加载预设失败：' + (e.message || e))
  }
}

async function loadAssets() {
  try {
    const roles = await listAssets('role')
    assetList.value = Array.isArray(roles) ? roles : []
    const scenes = await listAssets('scene')
    sceneList.value = Array.isArray(scenes) ? scenes : []
  } catch (e) { /* 资产库为空或非致命，忽略 */ }
}

async function loadModels() {
  loadingModels.value = true
  try {
    const r = await listLoraModels({ page: 1, size: 100 })
    models.value = (r.items || []).map((m) => ({ ...m, tags: parseTags(m.semantic_tags) }))
  } catch (e) {
    ElMessage.error('加载模型列表失败：' + (e.message || e))
  } finally {
    loadingModels.value = false
  }
}

async function loadTasks() {
  try {
    const r = await listLoraTrain({ page: 1, size: 50 })
    tasks.value = r.items || []
  } catch (e) { /* 忽略 */ }
}

// ---- ② 数据集：上传 / 资产导入 ----
function onPick(file, fileList) {
  if (!file.raw || !/^image\//.test(file.raw.type)) {
    ElMessage.warning('仅支持图片文件')
    return
  }
  pendingFiles.value = fileList.map((f) => f.raw).filter(Boolean)
}

async function buildDataset() {
  if (!pendingFiles.value.length) { ElMessage.warning('请先选择图片'); return }
  building.value = true
  try {
    const r = await uploadLoraDataset(pendingFiles.value, loraType.value)
    datasetDir.value = r.dataset_dir || ''
    uploadedCount.value = r.count || 0
    const v = r.validation || {}
    dsValidation.ok = !!v.ok
    dsValidation.message = v.message || ''
    dsMode.value = 'upload'
    ElMessage.success('数据集就绪：' + uploadedCount.value + ' 张')
  } catch (e) {
    ElMessage.error('构建失败：' + (e.message || e))
  } finally {
    building.value = false
  }
}

function onAssetMode(val) {
  dsMode.value = val
  if (val === 'asset') { datasetDir.value = ''; dsValidation.ok = false; captionResult.count = 0 }
}

async function runCaption() {
  if (dsMode.value === 'asset') {
    ElMessage.info('资产导入模式将在训练时自动打标，无需手动打标')
    return
  }
  if (!datasetDir.value) { ElMessage.warning('请先构建数据集'); return }
  captioning.value = true
  try {
    const r = await loraCaption({
      dataset_dir: datasetDir.value,
      lora_type: loraType.value,
      trigger_word: triggerWord.value.trim(),
    })
    captionResult.count = r.count || 0
    captionResult.caption_dir = r.caption_dir || ''
    ElMessage.success('打标完成：' + captionResult.count + ' 张')
  } catch (e) {
    ElMessage.error('打标失败：' + (e.message || e))
  } finally {
    captioning.value = false
  }
}

// ---- ④ 开始训练 / 进度订阅 ----
function canStart() {
  if (!name.value.trim()) return false
  if (loraType.value === 'character' && !triggerWord.value.trim()) return false
  if (dsMode.value === 'asset') return !!selectedAssetId.value
  return !!datasetDir.value
}

async function startTrain() {
  if (!canStart()) { ElMessage.warning('请完整填写：名称 / 触发词 / 数据集'); return }
  training.value = true
  trainStatus.value = 'queued'
  progress.value = 0
  curStep.value = 0
  totSteps.value = 0
  lossHistory.value = []
  curLoss.value = null
  trainError.value = ''
  etaText.value = ''
  _lastProgTs = 0
  _lastStep = 0

  const body = {
    name: name.value.trim(),
    lora_type: loraType.value,
    base_model: baseModel.value.trim(),
    trigger_word: triggerWord.value.trim(),
    preset: chosenPreset.value,
    seed: seed.value,
  }
  if (dsMode.value === 'asset') body.asset_id = selectedAssetId.value
  else body.dataset_dir = datasetDir.value

  try {
    const r = await createLoraTrain(body)
    const t = r.task
    lastTaskId.value = t.id
    ElMessage.success('训练任务已创建：' + t.id)
    subscribeProgress(t.id)
  } catch (e) {
    training.value = false
    ElMessage.error('创建失败：' + (e.message || e))
  }
}

function subscribeProgress(taskId) {
  if (_unsub.value) _unsub.value()
  _unsub.value = subscribe('lora:' + taskId, (event, data) => {
    data = data || {}
    if (event === 'status') {
      trainStatus.value = data.status
      if (data.error_message) trainError.value = data.error_message
      if (data.status === 'failed') {
        training.value = false
        ElMessage.error('训练失败：' + (data.error_message || '未知错误'))
      } else if (data.status === 'cancelled') {
        training.value = false
        ElMessage.info('已取消训练')
      } else if (data.status === 'succeeded') {
        training.value = false
        ElMessage.success('训练成功并完成注册')
        loadModels(); loadTasks()
      }
    } else if (event === 'progress') {
      trainStatus.value = 'running'
      curStep.value = data.current_step || 0
      totSteps.value = data.total_steps || 0
      progress.value = data.progress || 0
      if (data.loss != null && !isNaN(data.loss)) {
        curLoss.value = data.loss
        lossHistory.value.push({ step: curStep.value, loss: data.loss })
        if (lossHistory.value.length > 600) lossHistory.value.shift()
      }
      computeEta()
    } else if (event === 'registered') {
      trainStatus.value = 'succeeded'
      training.value = false
      ElMessage.success('已注册模型：' + (data.name || data.model_id))
      loadModels(); loadTasks()
    }
  })
}

function computeEta() {
  const now = Date.now()
  if (_lastProgTs && curStep.value > _lastStep && totSteps.value) {
    const dStep = curStep.value - _lastStep
    const dMs = now - _lastProgTs
    if (dMs > 0) {
      const rate = dStep / (dMs / 1000) // steps/sec
      const remain = totSteps.value - curStep.value
      if (rate > 0) etaText.value = fmtEta(remain / rate)
    }
  }
  _lastProgTs = now
  _lastStep = curStep.value
}

function fmtEta(s) {
  s = Math.max(0, s | 0)
  if (s >= 3600) { const h = Math.floor(s / 3600); const m = Math.floor((s % 3600) / 60); return h + '小时' + m + '分' }
  if (s >= 60) { const m = Math.floor(s / 60); return m + '分' + (s % 60) + '秒' }
  return s + '秒'
}

const lossPoints = computed(() => {
  const hs = lossHistory.value
  if (hs.length < 2) return ''
  const W = 320, H = 84, pad = 8
  const losses = hs.map((p) => p.loss)
  const min = Math.min(...losses)
  const max = Math.max(...losses)
  const span = (max - min) || 1
  const n = hs.length
  return hs.map((p, i) => {
    const x = pad + (W - 2 * pad) * (i / (n - 1))
    const y = pad + (H - 2 * pad) * (1 - (p.loss - min) / span)
    return (i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1)
  }).join(' ')
})

const statusText = computed(() => {
  const map = { queued: '排队中', running: '训练中', succeeded: '成功', failed: '失败', cancelled: '已取消' }
  return map[trainStatus.value] || (trainStatus.value || '—')
})

function statusTextFor(s) {
  const map = { queued: '排队中', running: '训练中', succeeded: '成功', failed: '失败', cancelled: '已取消' }
  return map[s] || (s || '—')
}

const statusColor = computed(() => {
  const map = { queued: 'var(--sub)', running: 'var(--pur)', succeeded: 'var(--green)', failed: 'var(--warn)', cancelled: 'var(--sub)' }
  return map[trainStatus.value] || 'var(--sub)'
})

async function doCancel() {
  if (!lastTaskId.value) return
  try { await cancelLoraTrain(lastTaskId.value); ElMessage.info('已发送取消') }
  catch (e) { ElMessage.error(e.message || e) }
}

// ---- ⑤ 模型管理动作 ----
async function doRegister(m) { try { await registerLoraModel(m.id); ElMessage.success('已注册到导演目录'); loadModels() } catch (e) { ElMessage.error(e.message || e) } }
async function doEnable(m) { try { await enableLoraModel(m.id); ElMessage.success('已启用'); loadModels() } catch (e) { ElMessage.error(e.message || e) } }
async function doDisable(m) { try { await disableLoraModel(m.id); ElMessage.success('已停用'); loadModels() } catch (e) { ElMessage.error(e.message || e) } }
async function doDelete(m) {
  if (!confirm('确认删除模型「' + m.name + '」？磁盘文件保留，仅移除目录记录。')) return
  try { await deleteLoraModel(m.id); ElMessage.success('已删除'); loadModels() } catch (e) { ElMessage.error(e.message || e) }
}
async function doValidate(m) {
  vpair.open = true; vpair.loading = true; vpair.err = ''; vpair.baseline = ''; vpair.with_lora = ''
  try {
    const r = await validateLoraModel(m.id)
    vpair.baseline = r.baseline || ''
    vpair.with_lora = r.with_lora || ''
  } catch (e) {
    vpair.err = e.message || String(e)
  } finally {
    vpair.loading = false
  }
}

const typeLabel = (t) => (t === 'character' ? '角色' : (t === 'style' ? '风格' : t))
const statusLabel = (s) => (s === 'active' ? '启用' : (s === 'disabled' ? '停用' : s))

onMounted(() => { loadPresets(); loadAssets(); loadModels(); loadTasks() })
onBeforeUnmount(() => { if (_unsub.value) _unsub.value() })
</script>

<template>
  <div class="page p5-halftone-strong lora-train">
    <!-- ① 页头：斜切大标题 + 予告状副标 + 星爆 NEW + 六边形奖牌 -->
    <div class="page-head lora-head">
      <div class="lora-head__main">
        <h1 class="p5-title lora-title">🎯 LoRA 训练</h1>
        <div class="lora-titlebar" aria-hidden="true"></div>
        <p class="muted lora-sub">LOCAL FINE-TUNING · 上传/导入数据集 → 自动打标 → 异步训练 → 注册导演资源目录</p>
      </div>
      <div class="lora-head__badge">
        <div class="p5-medallion lora-medal">LoRA</div>
        <div class="p5-badge-star lora-star"><span>NEW</span></div>
        <div class="engine-chip" :class="{ bad: !engine.available }">
          <span class="dot"></span>
          {{ engine.available ? '训练引擎就绪 (kohya_ss)' : '训练引擎缺失' }}
        </div>
      </div>
    </div>

    <!-- ② 状态横幅：撕裂边 + 胶带 + 超大编号 01（始终可见，诚实标注引擎状态） -->
    <!-- 胶带/编号作为撕裂面板的兄弟节点，避免被 clip-path 裁掉（clip-path 裁切所有后代） -->
    <div class="lora-banner-wrap">
      <div class="p5-tear-hard lora-banner">
        <div class="lora-banner__in">
          <span class="p5-tag" :class="engine.available ? 'ok' : 'alert'">{{ engine.available ? 'ENGINE READY' : 'COMING SOON' }}</span>
          <div class="b-body">
            <div class="b-title">
              <template v-if="engine.available">✅ kohya_ss 训练引擎就绪，可创建真实训练任务（手册 §1.3 闭环）</template>
              <template v-else>⚠️ kohya_ss 训练引擎未安装，训练任务将诚实失败</template>
            </div>
            <template v-if="!engine.available">
              <div>检测路径：<code>{{ engine.path || 'D:/AI/kohya_ss' }}</code></div>
              <div class="b-reason">{{ engine.reason }}</div>
              <div class="b-tip">安装后设置环境变量 <code>KOHYA_SS_PATH</code> 指向其根目录，重启后端即可启用真实训练。</div>
            </template>
          </div>
        </div>
      </div>
      <span class="p5-tape tr"></span>
      <span class="p5-big-num lora-bignum">01</span>
    </div>

    <div class="ws-grid">
      <!-- 左：配置 + 训练 -->
      <div class="col">
        <div class="p5-deco-strip slant lora-coltag">配置 · 训练</div>

        <!-- ① 类型切换 -->
        <section class="p5-panel p5-panel--lift">
          <div class="p5-panel__in">
            <div class="panel">
              <div class="g-title">① LoRA 类型</div>
              <el-radio-group v-model="loraType" class="seg">
                <el-radio-button label="character">角色 LoRA</el-radio-button>
                <el-radio-button label="style">风格 LoRA</el-radio-button>
              </el-radio-group>
              <div class="hint">角色 LoRA 强制携带触发词（激活开关）；风格 LoRA 学习画风，无需触发词。</div>
            </div>
          </div>
        </section>

        <!-- ② 数据集 -->
        <section class="p5-panel p5-panel--lift">
          <div class="p5-panel__in">
            <div class="panel">
              <div class="g-title">② 数据集</div>
              <el-radio-group :model-value="dsMode" @update:model-value="onAssetMode" class="seg sm">
                <el-radio-button label="upload">上传图片</el-radio-button>
                <el-radio-button label="asset">从资产库导入</el-radio-button>
              </el-radio-group>

              <div v-if="dsMode === 'upload'" class="ds-block">
                <el-upload drag :auto-upload="false" :show-file-list="false" :on-change="onPick"
                  accept="image/*" class="up">
                  <el-icon style="font-size:34px;color:var(--sub)"><Picture /></el-icon>
                  <div class="el-upload__text">拖拽图片到此处，或 <em>点击选择</em>（多张）</div>
                </el-upload>
                <div class="ds-row">
                  <span class="t">已选 {{ pendingFiles.length }} 张</span>
                  <button class="btn p5-btn--corner" :disabled="!pendingFiles.length || building" @click="buildDataset">
                    {{ building ? '构建中…' : '构建数据集' }}
                  </button>
                </div>
                <div v-if="datasetDir" class="ds-ok">
                  数据集就绪：{{ uploadedCount }} 张
                  <span :class="dsValidation.ok ? 'ok' : 'warn'">
                    {{ dsValidation.ok ? '· 数量达标' : ('· ' + (dsValidation.message || '数量不足')) }}
                  </span>
                </div>
                <button class="btn ghost p5-btn--corner" style="margin-top:10px" :disabled="!datasetDir || captioning"
                  @click="runCaption">
                  {{ captioning ? '打标中…' : '自动打标 (WD14)' }}
                </button>
                <div v-if="captionResult.count" class="ds-ok">已打标 {{ captionResult.count }} 张</div>
              </div>

              <div v-else class="ds-block">
                <div class="g-sub">选择资产（{{ loraType === 'character' ? '角色' : '场景/道具' }}）</div>
                <el-select v-model="selectedAssetId" placeholder="选择要训练的资产" style="width:100%">
                  <el-option v-for="a in (loraType === 'character' ? assetList : sceneList)" :key="a.asset_id"
                    :label="a.name + (a.role_type ? ' (' + a.role_type + ')' : '')" :value="a.asset_id" />
                </el-select>
                <div class="hint">资产导入模式将在训练时自动从参考图构建数据集并打标。</div>
              </div>
            </div>
          </div>
        </section>

        <!-- ③ 参数配置 -->
        <section class="p5-panel p5-panel--lift">
          <div class="p5-panel__in">
            <div class="panel">
              <div class="g-title">③ 参数配置</div>
              <div class="field">
                <label>名称 *</label>
                <el-input v-model="name" placeholder="如：林浩 / 民国胶片风" />
              </div>
              <div class="field" v-if="loraType === 'character'">
                <label>触发词 *（角色激活开关）</label>
                <el-input v-model="triggerWord" placeholder="如：linhao_char" />
              </div>
              <div class="field">
                <label>训练预设</label>
                <el-select v-model="chosenPreset" style="width:100%">
                  <el-option v-for="p in presets" :key="p.key" :label="p.label + ' · ' + p.resolution + ' · ' + p.max_train_steps + '步'" :value="p.key" />
                </el-select>
                <div class="hint" v-if="presets.length">
                  默认强制 xformers + 梯度检查点 + fp16（6GB 显存必需）。
                  {{ (presets.find(p => p.key === chosenPreset) || {}).note }}
                </div>
              </div>
              <div class="field">
                <label>底模 (base_model)</label>
                <el-input v-model="baseModel" placeholder="v1-5-pruned.safetensors" />
              </div>
              <div class="adv-toggle" @click="showAdvanced = !showAdvanced">
                {{ showAdvanced ? '收起高级参数 ▲' : '展开高级参数 ▼' }}
              </div>
              <div v-if="showAdvanced" class="field">
                <label>随机种子 seed</label>
                <el-input v-model.number="seed" type="number" />
              </div>
            </div>
          </div>
        </section>

        <!-- ④ 训练进度 -->
        <section class="p5-panel p5-panel--lift">
          <div class="p5-panel__in">
            <div class="panel">
              <div class="g-title">④ 训练
                <span class="st" :style="{ color: statusColor }">{{ statusText }}</span>
                <span class="p5-deco-strip corner lora-mini">LIVE</span>
              </div>
              <button class="btn pink p5-btn p5-btn--primary p5-btn--corner" style="width:100%;justify-content:center" :disabled="!canStart() || training"
                @click="startTrain">
                {{ training ? '训练中…' : '开始训练' }}
              </button>
              <div v-if="trainError" class="err-box">{{ trainError }}</div>

              <div v-if="training || lossHistory.length || trainStatus === 'succeeded' || trainStatus === 'failed'" class="prog">
                <div class="prog-bar">
                  <div class="fill" :class="{ running: trainStatus === 'running' }" :style="{ width: (progress * 100).toFixed(1) + '%', background: statusColor }"></div>
                </div>
                <div class="prog-meta">
                  <span>步数 {{ curStep }} / {{ totSteps }}</span>
                  <span>进度 {{ (progress * 100).toFixed(1) }}%</span>
                  <span v-if="curLoss != null">loss {{ curLoss.toFixed(4) }}</span>
                  <span v-if="etaText && trainStatus === 'running'">预计剩余 {{ etaText }}</span>
                </div>
                <svg v-if="lossPoints" class="loss-chart" viewBox="0 0 320 84" preserveAspectRatio="none">
                  <path :d="lossPoints" fill="none" stroke="var(--pur)" stroke-width="1.5" />
                </svg>
                <div v-if="trainStatus === 'running'" class="ds-row">
                  <span class="t">实时进度经 WebSocket 频道 lora:{{ lastTaskId }} 推送</span>
                  <button class="btn ghost sm p5-btn--corner" @click="doCancel">取消训练</button>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <!-- 右：模型管理 + 任务记录 -->
      <div class="col">
        <div class="p5-deco-strip slant-rev ink lora-coltag">模型 · 任务</div>

        <section class="p5-panel p5-panel--lift">
          <div class="p5-panel__in">
            <div class="panel">
              <span class="p5-big-num paper p5-big-num--bleed">02</span>
              <div class="g-title">⑤ 已注册 LoRA 模型
                <button class="btn ghost sm p5-btn--corner" style="float:right" :disabled="loadingModels" @click="loadModels">刷新</button>
              </div>
              <div v-if="!models.length" class="empty">尚无模型。完成一次训练后将在此出现并自动注册到导演资源目录。</div>
              <div v-for="m in models" :key="m.id" class="model-card">
                <div class="mc-head">
                  <div class="mc-name">{{ m.name }}</div>
                  <span class="mc-tag p5-tag" :class="m.lora_type === 'character' ? 'char' : 'style'">{{ typeLabel(m.lora_type) }}</span>
                  <span class="mc-status" :class="m.status">{{ statusLabel(m.status) }}</span>
                </div>
                <div class="mc-line">触发词：<code>{{ m.trigger_word || '—' }}</code></div>
                <div class="mc-line">底模：{{ m.base_model || '—' }}</div>
                <div class="mc-tags" v-if="m.tags && m.tags.length">
                  <span v-for="t in m.tags" :key="t" class="chip">{{ t }}</span>
                </div>
                <div class="mc-actions">
                  <button class="btn ghost sm p5-btn--corner" @click="doValidate(m)">验证对比</button>
                  <button v-if="m.status !== 'active'" class="btn ghost sm p5-btn--corner" @click="doEnable(m)">启用</button>
                  <button v-else class="btn ghost sm p5-btn--corner" @click="doDisable(m)">停用</button>
                  <button class="btn ghost sm p5-btn--corner" @click="doRegister(m)">重注册</button>
                  <button class="btn ghost sm danger p5-btn--corner" @click="doDelete(m)">删除</button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section class="p5-panel p5-panel--lift">
          <div class="p5-panel__in">
            <div class="panel">
              <div class="g-title">训练任务记录
                <button class="btn ghost sm p5-btn--corner" style="float:right" @click="loadTasks">刷新</button>
              </div>
              <div v-if="!tasks.length" class="empty">暂无训练任务记录。</div>
              <div v-for="t in tasks" :key="t.id" class="task-row">
                <div class="tr-main">
                  <div class="tr-name">{{ t.name }}</div>
                  <div class="tr-sub">{{ typeLabel(t.lora_type) }} · 步 {{ t.current_step }}/{{ t.total_steps }} · {{ (t.progress * 100 || 0).toFixed(0) }}%</div>
                </div>
                <span class="tr-status" :class="t.status">{{ statusTextFor(t.status) }}</span>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>

    <!-- 验证对比图弹窗（功能保持不变） -->
    <div v-if="vpair.open" class="mask" @click.self="vpair.open = false">
      <div class="modal">
        <div class="m-head">
          <span>LoRA 验证对比（左：无 LoRA 基线 / 右：含 LoRA）</span>
          <button class="btn ghost sm p5-btn--corner" @click="vpair.open = false">关闭</button>
        </div>
        <div v-if="vpair.loading" class="empty">正在生成对比图（依赖本地 ComfyUI）…</div>
        <div v-else-if="vpair.err" class="err-box">{{ vpair.err }}</div>
        <div v-else class="compare">
          <div class="cmp">
            <div class="cmp-label">基线（无 LoRA）</div>
            <img v-if="vpair.baseline" :src="vpair.baseline" alt="baseline" />
            <div v-else class="empty">无</div>
          </div>
          <div class="cmp">
            <div class="cmp-label">含 LoRA</div>
            <img v-if="vpair.with_lora" :src="vpair.with_lora" alt="with_lora" />
            <div v-else class="empty">无</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ============================================================================
   通用结构 / 布局（所有主题共享，全部使用主题变量，不影响其他主题外观）
   ============================================================================ */
.page { position: relative; }
.page > * { position: relative; z-index: 1; }
.lora-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; flex-wrap: wrap; }
.lora-head__main { position: relative; padding-left: 4px; }
.lora-sub { margin: 14px 0 0; font-family: var(--font-cond); letter-spacing: .04em; }
.lora-head__badge { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

/* 状态横幅结构层（撕裂/胶带/大编号外观由全局 .p5-tear-hard/.p5-tape/.p5-big-num 在 P5 主题下提供） */
.lora-banner-wrap { position: relative; margin-bottom: 18px; }
.lora-banner { position: relative; overflow: visible; }
.lora-bignum { right: 18px; top: -16px; }
.lora-banner__in { position: relative; z-index: 1; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.lora-banner__in .p5-tag { flex: 0 0 auto; }
.b-body { font-size: 13px; color: var(--txt); line-height: 1.7; }
.b-title { color: var(--warn); font-weight: 600; margin-bottom: 4px; }
.b-reason { color: var(--sub); font-size: 12px; margin: 4px 0; }
.b-tip { color: var(--warn); font-size: 12px; }
code { background: rgba(138, 92, 255, 0.12); color: var(--pur); padding: 1px 6px; border-radius: 5px; font-size: 12px; }

/* 列装饰条定位（装饰外观由全局 .p5-deco-strip 在 P5 主题下提供） */
.lora-coltag { margin-bottom: 6px; }

.engine-chip {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 6px 12px; border-radius: 20px; font-size: 13px;
  background: rgba(61, 220, 132, 0.12); color: var(--green);
  border: 1px solid rgba(61, 220, 132, 0.35);
}
.engine-chip.bad { background: rgba(255, 176, 32, 0.12); color: var(--warn); border-color: rgba(255, 176, 32, 0.4); }
.engine-chip .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }

.ws-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; align-items: start; }
.col { display: flex; flex-direction: column; gap: 16px; padding: 6px; }
.panel { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
.g-title { font-size: 14px; font-weight: 600; color: var(--txt); margin-bottom: 12px; display: flex; align-items: center; gap: 10px; }
.g-title .lora-mini { margin-left: auto; height: 22px; font-size: 11px; padding: 0 12px; }
.g-sub { font-size: 12px; color: var(--sub); margin: 8px 0 6px; }
.seg { margin-bottom: 10px; }
.seg.sm :deep(.el-radio-button__inner) { font-size: 12px; padding: 6px 12px; }
.hint { font-size: 12px; color: var(--sub); margin-top: 8px; line-height: 1.6; }
.up :deep(.el-upload-dragger) { background: #0f1117; border-color: var(--line); padding: 22px; }
.ds-block { margin-top: 12px; }
.ds-row { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; gap: 10px; }
.ds-ok { font-size: 12px; color: var(--green); margin-top: 8px; }
.ds-ok .ok { color: var(--green); }
.ds-ok .warn { color: var(--warn); }
.field { margin-bottom: 12px; padding-left: 10px; padding-top: 6px; padding-bottom: 6px; }
.field label { display: block; font-size: 12px; color: var(--sub); margin-bottom: 6px; }
.adv-toggle { cursor: pointer; color: var(--pur); font-size: 13px; margin-top: 4px; }

/* 按钮：所有主题通用外观（P5 斜切在下方按主题叠加） */
.btn {
  border: 1px solid var(--line); background: var(--bg); color: var(--txt);
  border-radius: 9px; padding: 8px 14px; font-size: 13px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 6px; transition: .15s;
}
.btn:hover:not(:disabled) { border-color: var(--pur); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn.ghost { background: transparent; }
.btn.pink { background: var(--pur); border-color: var(--pur); color: #fff; font-weight: 600; }
.btn.sm { padding: 5px 10px; font-size: 12px; }
.btn.danger { color: var(--warn); border-color: rgba(255, 176, 32, 0.4); }
.st { font-size: 13px; font-weight: 600; margin-left: 8px; }
.err-box { background: rgba(255, 176, 32, 0.1); border: 1px solid rgba(255, 176, 32, 0.35);
  color: var(--warn); border-radius: 9px; padding: 8px 10px; font-size: 12px; margin-top: 10px; }
.prog { margin-top: 14px; }
.prog-bar { height: 8px; border-radius: 6px; background: var(--bg); overflow: hidden; border: 1px solid var(--line); }
.prog-bar .fill { height: 100%; transition: width .4s; }
.prog-bar .fill.running { animation: lora-pulse 1.1s ease-in-out infinite; }
.prog-meta { display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; color: var(--sub); margin-top: 8px; }
.loss-chart { width: 100%; height: 84px; margin-top: 8px; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; }
.empty { color: var(--sub); font-size: 13px; padding: 10px 0; }
.model-card { border: 1px solid var(--line); border-radius: 11px; padding: 12px; margin-bottom: 10px; background: var(--bg); position: relative; }
.mc-head { display: flex; align-items: center; gap: 8px; }
.mc-name { font-weight: 600; color: var(--txt); flex: 1; }
.mc-tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; }
.mc-tag.char { background: rgba(138, 92, 255, 0.18); color: var(--pur); }
.mc-tag.style { background: rgba(77, 159, 255, 0.18); color: #4d9fff; }
.mc-status { font-size: 11px; padding: 2px 8px; border-radius: 10px; }
.mc-status.active { background: rgba(61, 220, 132, 0.18); color: var(--green); }
.mc-status.disabled { background: rgba(139, 147, 167, 0.18); color: var(--sub); }
.mc-line { font-size: 12px; color: var(--sub); margin-top: 6px; }
.mc-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: rgba(138, 92, 255, 0.12); color: var(--pur); }
.mc-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.task-row { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--line); }
.tr-main { flex: 1; min-width: 0; }
.tr-name { font-size: 13px; color: var(--txt); }
.tr-sub { font-size: 12px; color: var(--sub); margin-top: 2px; }
.tr-status { font-size: 11px; padding: 2px 8px; border-radius: 10px; }
.tr-status.succeeded, .tr-status.running { color: var(--green); }
.tr-status.failed { color: var(--warn); }
.tr-status.queued, .tr-status.cancelled { color: var(--sub); }
.mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex; align-items: center; justify-content: center; z-index: 2800; }
.modal { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; width: min(860px, 92vw); }
.m-head { display: flex; justify-content: space-between; align-items: center; font-weight: 600; color: var(--txt); margin-bottom: 12px; }
.compare { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.cmp-label { font-size: 12px; color: var(--sub); margin-bottom: 6px; }
.cmp img { width: 100%; border-radius: 10px; border: 1px solid var(--line); display: block; }

/* ============================================================================
   P5 怪盗风 · 组件级异形视觉已移至全局 style.css（挂在 .lora-train 根 + html.t-p5），
   因为 scoped 下 `:global(html.t-p5) .x` 会被编译器丢弃后代选择器，无法可靠生效。
   下方仅保留与 P5 无关的通用动画关键帧。
   ============================================================================ */
@keyframes lora-pulse { 0%,100% { opacity: 1; } 50% { opacity: .5; } }
</style>
