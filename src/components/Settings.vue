<script setup>
import { reactive, ref, watch, onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useTheme } from '../composables/useTheme'
import { getSettings, updateSettings } from '../api'

const router = useRouter()
const LS_KEY = 'wn_settings'
const APP_PREFIX = 'wn_'

const defaults = () => ({
  // 外观
  theme: 'dark',            // dark / light / auto
  fontSize: 14,             // 12-20px
  language: 'zh-CN',
  accent: 'purple',
  density: 'standard',      // compact / standard / loose
  // AI 模型
  createModel: '创作旗舰 Pro',
  videoModel: 'Seedance 2.0 Pro',
  imgModel: 'SDXL 本地',
  planningModel: 'Gemini 3.5 Flash',
  defaultResolution: '720p',
  defaultRatio: '16:9',
  defaultDuration: 12,
  safetyLevel: 'standard', // strict / standard / loose
  quality: 'standard',     // draft / standard / hd
  // 存储
  storagePath: '%APPDATA%/wanneng-drama/',
  autoCleanupDays: 30,
  maxSizeGb: 50,
  mediaQuality: 'keep',     // keep / compress / ondemand
  // 网络
  proxyOn: false,
  proxyAddr: '',
  apiHost: '127.0.0.1',
  apiPort: 7788,
  timeoutS: 120,
  concurrency: 2,
  // 通知
  desktopNotify: true,
  soundNotify: true,
  quietStart: '22:00',
  quietEnd: '08:00',
  notifyTask: true,
  notifyCredits: true,
  notifySystem: true,
  notifyActivity: true,
  // 快捷键 / 备份 / 隐私
  autoBackup: true,
  backupKeepDays: 30,
  dataLocal: 'force',       // default / force
  uploadMask: false,
  logRetentionDays: 90,
})

const load = () => {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return { ...defaults(), ...JSON.parse(raw) }
  } catch (e) { /* 忽略损坏数据 */ }
  return defaults()
}

const settings = reactive(load())
const { set: setTheme } = useTheme()

const persist = () => {
  try { localStorage.setItem(LS_KEY, JSON.stringify({ ...settings })) } catch (e) {}
}
watch(settings, persist, { deep: true })
watch(() => settings.theme, setTheme)

const saveAll = async () => {
  persist()
  try {
    await updateSettings({ ...settings })
    ElMessage.success('设置已保存')
  } catch (e) {
    ElMessage.warning('后端保存失败，已保留本地设置')
  }
}

const clearCache = () => {
  const keys = []
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (k && k.startsWith(APP_PREFIX)) keys.push(k)
  }
  keys.forEach(k => localStorage.removeItem(k))
  Object.assign(settings, defaults())
  ElMessage.success('已清理本地缓存（演示）')
}

// 网络测速模拟
const speedProgress = ref(0)
const speedIng = ref(false)
let timer = null
const runSpeedTest = () => {
  if (speedIng.value) return
  speedIng.value = true
  speedProgress.value = 0
  timer = setInterval(() => {
    speedProgress.value += Math.floor(Math.random() * 9) + 5
    if (speedProgress.value >= 100) {
      speedProgress.value = 100
      clearInterval(timer); timer = null
      speedIng.value = false
      ElMessage.success('延迟 23ms / 下行 120Mbps（演示）')
    }
  }, 220)
}
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

onMounted(async () => {
  try {
    const backend = await getSettings()
    if (backend && typeof backend === 'object') {
      // 保留本地主题选择，其余字段后端优先
      const theme = settings.theme
      Object.assign(settings, backend)
      settings.theme = theme
    }
  } catch (e) {
    console.warn('settings backend failed, use local', e.message)
  }
})

const checkUpdate = () => ElMessage('已是最新版本 v1.3.0（演示）')
const goHelp = () => router.push('/help')
const resetSettings = () => { Object.assign(settings, defaults()); ElMessage.success('已恢复默认设置（演示）') }

const createModels = ['创作旗舰 Pro', 'Flash·短', 'Flash·长', 'Max']
const videoModels = ['Seedance 2.0 Mini', 'Seedance 2.0 Pro', '可灵1.6', '即梦3.0', 'Vidu Q3']
const imgModels = ['SDXL 本地', 'GPT-Image-2 2K']
const planningModels = ['Gemini 3.5 Flash', 'Gemini 3.5 Pro', 'GPT-4o']
const resolutions = ['480p', '720p', '1080p', '2K', '4K']
const ratios = ['16:9', '9:16', '1:1', '4:3', '21:9']
const durations = [5, 8, 10, 12, 15]
const accents = [
  { key: 'purple', color: '#8a5cff' }, { key: 'pink', color: '#ff4d8d' },
  { key: 'blue', color: '#4d9fff' }, { key: 'green', color: '#3ddc84' }, { key: 'orange', color: '#ff7a45' },
]
const hotkeys = [
  { k: 'Ctrl + S', d: '保存当前编辑' }, { k: 'Ctrl + Z', d: '撤销' },
  { k: 'F11', d: '全屏' }, { k: 'Ctrl + 1~9', d: '切换主导航' },
]
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>设置</h1>
        <span class="muted">偏好会保存在本机浏览器，刷新后仍生效（个人版·本地）</span>
      </div>
      <button class="btn" @click="saveAll">保存设置</button>
    </div>

    <el-tabs type="border-card" class="set-tabs">
      <!-- 外观 -->
      <el-tab-pane label="外观">
        <div class="field"><label>主题</label>
          <el-radio-group v-model="settings.theme">
            <el-radio value="light">浅色</el-radio>
            <el-radio value="dark">深色</el-radio>
            <el-radio value="auto">跟随系统</el-radio>
          </el-radio-group>
        </div>
        <div class="field"><label>字体大小</label>
          <el-slider v-model="settings.fontSize" :min="12" :max="20" show-input style="max-width:320px" />
        </div>
        <div class="field"><label>语言</label>
          <el-select v-model="settings.language" style="max-width:220px">
            <el-option label="简体中文" value="zh-CN" /><el-option label="English" value="en-US" /><el-option label="日本語" value="ja-JP" />
          </el-select>
        </div>
        <div class="field"><label>主题色</label>
          <div class="accents">
            <span v-for="a in accents" :key="a.key" class="ac" :class="{ on: settings.accent === a.key }"
                  :style="{ background: a.color }" @click="settings.accent = a.key" />
          </div>
        </div>
        <div class="field"><label>界面密度</label>
          <el-radio-group v-model="settings.density">
            <el-radio value="compact">紧凑</el-radio><el-radio value="standard">标准</el-radio><el-radio value="loose">宽松</el-radio>
          </el-radio-group>
        </div>
      </el-tab-pane>

      <!-- AI 模型 -->
      <el-tab-pane label="AI 模型">
        <div class="field"><label>默认创作模型</label>
          <el-select v-model="settings.createModel" style="max-width:320px">
            <el-option v-for="m in createModels" :key="m" :label="m" :value="m" />
          </el-select>
        </div>
        <div class="field"><label>默认视频模型</label>
          <el-select v-model="settings.videoModel" style="max-width:320px">
            <el-option v-for="m in videoModels" :key="m" :label="m" :value="m" />
          </el-select>
        </div>
        <div class="field"><label>默认文生图</label>
          <el-select v-model="settings.imgModel" style="max-width:320px">
            <el-option v-for="m in imgModels" :key="m" :label="m" :value="m" />
          </el-select>
        </div>
        <div class="field"><label>默认策划模型</label>
          <el-select v-model="settings.planningModel" style="max-width:320px">
            <el-option v-for="m in planningModels" :key="m" :label="m" :value="m" />
          </el-select>
        </div>
        <div class="field"><label>默认参数</label>
          <el-select v-model="settings.defaultResolution" style="max-width:120px"><el-option v-for="r in resolutions" :key="r" :label="r" :value="r" /></el-select>
          <el-select v-model="settings.defaultRatio" style="max-width:110px;margin-left:8px"><el-option v-for="r in ratios" :key="r" :label="r" :value="r" /></el-select>
          <el-select v-model="settings.defaultDuration" style="max-width:110px;margin-left:8px"><el-option v-for="d in durations" :key="d" :label="d + '秒'" :value="d" /></el-select>
        </div>
        <div class="field"><label>安全等级</label>
          <el-radio-group v-model="settings.safetyLevel"><el-radio value="strict">严格</el-radio><el-radio value="standard">标准</el-radio><el-radio value="loose">宽松</el-radio></el-radio-group>
        </div>
        <div class="field"><label>生成质量</label>
          <el-radio-group v-model="settings.quality"><el-radio value="draft">草稿</el-radio><el-radio value="standard">标准</el-radio><el-radio value="hd">HD</el-radio></el-radio-group>
        </div>
      </el-tab-pane>

      <!-- 存储 -->
      <el-tab-pane label="存储">
        <div class="field"><label>存储路径</label>
          <el-input :model-value="settings.storagePath" disabled style="max-width:360px" />
        </div>
        <div class="field"><label>缓存清理</label>
          <el-popconfirm title="确认清空本应用本地缓存？" confirm-button-text="清空" cancel-button-text="取消" @confirm="clearCache">
            <template #reference><el-button type="danger" plain>清除本地缓存</el-button></template>
          </el-popconfirm>
        </div>
        <div class="field"><label>自动清理</label>
          <el-input-number v-model="settings.autoCleanupDays" :min="0" :max="365" /><span class="muted" style="margin-left:8px">天前任务自动清理</span>
        </div>
        <div class="field"><label>最大占用</label>
          <el-input-number v-model="settings.maxSizeGb" :min="1" :max="500" /><span class="muted" style="margin-left:8px">GB 警告</span>
        </div>
        <div class="field"><label>媒体质量</label>
          <el-radio-group v-model="settings.mediaQuality"><el-radio value="keep">保留</el-radio><el-radio value="compress">压缩</el-radio><el-radio value="ondemand">按需</el-radio></el-radio-group>
        </div>
      </el-tab-pane>

      <!-- 网络 -->
      <el-tab-pane label="网络">
        <div class="field"><label>启用代理</label><el-switch v-model="settings.proxyOn" /></div>
        <div class="field"><label>代理地址</label>
          <el-input v-model="settings.proxyAddr" placeholder="如 http://127.0.0.1:7890" :disabled="!settings.proxyOn" style="max-width:360px" />
        </div>
        <div class="field"><label>API 服务器</label>
          <el-input v-model="settings.apiHost" style="max-width:200px" /><span class="muted" style="margin:0 6px">:</span>
          <el-input-number v-model="settings.apiPort" :min="1" :max="65535" controls-position="right" style="width:140px" />
        </div>
        <div class="field"><label>请求超时</label>
          <el-input-number v-model="settings.timeoutS" :min="10" :max="300" /><span class="muted" style="margin-left:8px">秒</span>
        </div>
        <div class="field"><label>并发数</label>
          <el-input-number v-model="settings.concurrency" :min="1" :max="10" /><span class="muted" style="margin-left:8px">同时进行的生成任务（个人版单任务）</span>
        </div>
        <div class="field" style="align-items:flex-start"><label>网络测速</label>
          <div style="flex:1">
            <el-button type="primary" :loading="speedIng" @click="runSpeedTest">{{ speedIng ? '测速中…' : '开始测速' }}</el-button>
            <el-progress :percentage="speedProgress" :stroke-width="10" style="max-width:360px;margin-top:12px" :status="speedProgress >= 100 ? 'success' : ''" />
          </div>
        </div>
      </el-tab-pane>

      <!-- 通知 -->
      <el-tab-pane label="通知">
        <div class="field"><label>桌面通知</label><el-switch v-model="settings.desktopNotify" /></div>
        <div class="field"><label>声音</label><el-switch v-model="settings.soundNotify" /></div>
        <div class="field"><label>静默时段</label>
          <el-time-picker v-model="settings.quietStart" format="HH:mm" value-format="HH:mm" style="width:130px" />
          <span class="muted" style="margin:0 6px">至</span>
          <el-time-picker v-model="settings.quietEnd" format="HH:mm" value-format="HH:mm" style="width:130px" />
        </div>
        <div class="field"><label>接收类型</label>
          <el-checkbox v-model="settings.notifyTask">任务</el-checkbox>
          <el-checkbox v-model="settings.notifyCredits">积分</el-checkbox>
          <el-checkbox v-model="settings.notifySystem">系统</el-checkbox>
          <el-checkbox v-model="settings.notifyActivity">活动</el-checkbox>
        </div>
      </el-tab-pane>

      <!-- 快捷键 -->
      <el-tab-pane label="快捷键">
        <div class="field" style="align-items:flex-start"><label>快捷键</label>
          <div class="hk-list">
            <div v-for="h in hotkeys" :key="h.k" class="hk"><kbd>{{ h.k }}</kbd><span>{{ h.d }}</span></div>
          </div>
        </div>
        <div class="field"><label>恢复默认</label><el-button class="btn ghost" @click="resetSettings">恢复默认设置</el-button></div>
      </el-tab-pane>

      <!-- 备份 -->
      <el-tab-pane label="备份">
        <div class="field"><label>自动备份</label><el-switch v-model="settings.autoBackup" /></div>
        <div class="field"><label>保留时长</label>
          <el-select v-model="settings.backupKeepDays" style="max-width:160px"><el-option :value="7" label="7 天" /><el-option :value="30" label="30 天" /><el-option :value="90" label="90 天" /></el-select>
        </div>
        <div class="field"><label>一键备份</label><el-button @click="ElMessage.success('已创建本地备份（演示）')">立即备份</el-button></div>
      </el-tab-pane>

      <!-- 隐私 -->
      <el-tab-pane label="隐私">
        <div class="field"><label>数据本地化</label>
          <el-radio-group v-model="settings.dataLocal"><el-radio value="default">默认</el-radio><el-radio value="force">强制本机</el-radio></el-radio-group>
        </div>
        <div class="field"><label>上传脱敏</label><el-switch v-model="settings.uploadMask" /></div>
        <div class="field"><label>日志保留</label>
          <el-select v-model="settings.logRetentionDays" style="max-width:160px"><el-option :value="30" label="30 天" /><el-option :value="90" label="90 天" /><el-option :value="365" label="365 天" /></el-select>
        </div>
        <div class="field"><label>导出数据</label><el-button @click="ElMessage.success('已导出我的数据（演示）')">导出我的数据</el-button></div>
        <div class="field"><label>清除全部</label>
          <el-popconfirm title="确认清除所有本地数据？此操作不可恢复" confirm-button-text="清除" cancel-button-text="取消" @confirm="clearCache">
            <template #reference><el-button type="danger" plain>一键清除所有数据</el-button></template>
          </el-popconfirm>
        </div>
      </el-tab-pane>

      <!-- 关于 -->
      <el-tab-pane label="关于">
        <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
          <div style="width:56px;height:56px;border-radius:14px;background:var(--grad);display:flex;align-items:center;justify-content:center;font-size:28px;color:#fff">◆</div>
          <div>
            <div style="font-size:18px;font-weight:800">万能短剧 · AI 创作平台</div>
            <div class="muted">版本 v1.3.0 · 个人版（永久免费）</div>
          </div>
        </div>
        <p style="line-height:1.8;color:var(--txt)">本平台为本地演示版，所有生成能力均为模拟，不涉及真实后端调用。你可以自由体验创意工坊、导演工作台与各类素材管理功能。</p>
        <p class="muted" style="line-height:1.8">开源协议：基于 Apache-2.0 授权，仅供学习与非商业用途。图标与示例素材版权归各自权利人所有。</p>
        <div style="display:flex;gap:12px;margin-top:16px">
          <el-button @click="checkUpdate">检查更新</el-button>
          <el-button type="primary" plain @click="goHelp">反馈入口</el-button>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.field label { width: 90px; color: var(--sub); font-size: 13px; flex: 0 0 90px; }
.field { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.accents { display: flex; gap: 8px; }
.ac { width: 22px; height: 22px; border-radius: 6px; cursor: pointer; border: 2px solid transparent; }
.ac.on { border-color: #fff; box-shadow: 0 0 0 2px var(--pur); }
.hk-list { display: flex; flex-direction: column; gap: 8px; }
.hk { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--txt); }
.hk kbd { background: var(--card-2); border: 1px solid var(--line); border-radius: 6px; padding: 2px 8px; font-family: monospace; }
</style>
