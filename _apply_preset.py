import io, sys
PATH='src/components/ProjectFragmentEditor.vue'
s=io.open(PATH,encoding='utf-8').read()

reps=[]

# 1) 替换 PRESET_OPTIONS 为 PRESET_CONFIG
a1="""const PRESET_OPTIONS = [
  { value: 'pro', label: '创作旗舰 Pro' },
  { value: 'flash-short', label: '创作旗舰 Flash·短' },
  { value: 'flash-long', label: '创作旗舰 Flash·长' },
  { value: 'max', label: '创作旗舰 Max' },
]
const modelLabel = (v) => MODEL_OPTIONS.find((o) => o.value === v)?.label || v
const presetLabel = (v) => PRESET_OPTIONS.find((o) => o.value === v)?.label || v"""
n1="""// 预设套餐：把时长上限、速度档位、默认模型、能力权限、画质策略、积分系数打包
// 设计依据：降低用户决策成本；套餐与底层模型是「包含关系」，模型只是执行引擎之一
const PRESET_CONFIG = {
  pro: { value: 'pro', label: '创作旗舰 Pro', slogan: '画质优先 · 标准时长', speed: 'quality', speedLabel: '标准', durationMax: 10, defaultDuration: 8, defaultModel: 'seedance-2.0-pro', allowedModels: ['seedance-2.0-pro', 'seedance-2.0-mini'], features: ['人脸修复', '画面防抖', '色彩增强'], priceFactor: 1.0, desc: '均衡画质与速度，适合日常创作。', color: '#4d9fff', tags: ['画质优先', '标准队列'] },
  'flash-short': { value: 'flash-short', label: '创作旗舰 Flash·短', slogan: '极速出片 · 短片段', speed: 'fast', speedLabel: '极速', durationMax: 6, defaultDuration: 5, defaultModel: 'seedance-2.0-mini', allowedModels: ['seedance-2.0-mini', 'kling-1.6'], features: ['快速队列', '精简后处理'], priceFactor: 0.6, desc: '低价快速验证创意，适合测试脚本。', color: '#00d68f', tags: ['极速', '低价'] },
  'flash-long': { value: 'flash-long', label: '创作旗舰 Flash·长', slogan: '极速出片 · 长片段', speed: 'fast', speedLabel: '极速', durationMax: 12, defaultDuration: 12, defaultModel: 'seedance-2.0-pro', allowedModels: ['seedance-2.0-pro', 'seedance-2.0-mini', 'kling-1.6'], features: ['高速队列', '长片段支持'], priceFactor: 1.3, desc: '快速生成长片段，适合短视频快节奏创作。', color: '#ffb020', tags: ['极速', '长片段'] },
  max: { value: 'max', label: '创作旗舰 Max', slogan: '旗舰画质 · 全能力', speed: 'quality', speedLabel: '旗舰', durationMax: 15, defaultDuration: 12, defaultModel: 'seedance-2.0-pro', allowedModels: ['seedance-2.0-pro', 'vidu-q3'], features: ['人脸修复', '超分辨率', '画面防抖', '色彩增强', '高清分辨率', '真人资产化增强', '返回尾帧'], priceFactor: 2.0, desc: '最高画质与稳定性，专业出片首选。', color: '#ff4d8d', tags: ['旗舰', '全能力'] },
}
const PRESET_LIST = Object.values(PRESET_CONFIG)
const modelLabel = (v) => MODEL_OPTIONS.find((o) => o.value === v)?.label || v
const presetLabel = (v) => PRESET_CONFIG[v]?.label || v
const presetCfg = computed(() => PRESET_CONFIG[p.value?.preset_id] || PRESET_CONFIG['flash-long'])"""
reps.append((a1,n1,'PRESET_CONFIG'))

# 2) 更新积分估算，加入套餐系数
a2="""const estimatedCredits = computed(() => {
  if (!p.value) return 0
  const base = 180
  const dur = Number(p.value.duration) || 0
  const res = RES_COEF[p.value.resolution] ?? 1.0
  const aud = p.value.audio ? 1.2 : 1.0
  return Math.ceil(base * dur * res * aud)
})

// 模型说明条（随 AI 模型切换的动态文案，spec §3.1）
const modelHint = computed(() => {
  if (!p.value) return ''
  return `${modelLabel(p.value.ai_model)}：标准视频生成模型，按分镜参数与资产引用生成视频。`
})"""
n2="""const estimatedCredits = computed(() => {
  if (!p.value) return 0
  const base = 180
  const dur = Number(p.value.duration) || 0
  const res = RES_COEF[p.value.resolution] ?? 1.0
  const aud = p.value.audio ? 1.2 : 1.0
  const factor = presetCfg.value.priceFactor ?? 1.0
  return Math.ceil(base * dur * res * aud * factor)
})

// 模型说明条：套餐 vs 模型的关系，spec §3.1
const modelHint = computed(() => {
  if (!p.value) return ''
  const cfg = presetCfg.value
  const model = modelLabel(p.value.ai_model)
  return `当前套餐「${cfg.label}」：${cfg.desc} 已启用 ${cfg.features.slice(0, 3).join('、')}。底层模型「${model}」仅为执行引擎，时长上限、速度档位、积分单价由套餐决定。`
})

// 切换预设时自动对齐：模型必须落在套餐可用列表内，时长不得超过套餐上限
watch(() => p.value?.preset_id, (id, oldId) => {
  if (!p.value || !id || id === oldId) return
  const cfg = PRESET_CONFIG[id]
  if (!cfg || oldId === undefined) return
  if (!cfg.allowedModels.includes(p.value.ai_model)) p.value.ai_model = cfg.defaultModel
  if ((p.value.duration || 0) > cfg.durationMax) p.value.duration = cfg.defaultDuration
}, { flush: 'post' })"""
reps.append((a2,n2,'estimatedCredits/modelHint/watcher'))

# 3) 替换顶部预设模板
a3="""        <div class="fgrp"><label>预设</label>
          <el-select v-model="p.preset_id" size="small" style="width:160px">
            <el-option v-for="o in PRESET_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </div>
        <div class="fgrp"><label>模型</label>"""
n3="""        <div class="fgrp preset-grp"><label>生成套餐</label>
          <el-select v-model="p.preset_id" size="small" style="width:200px" popper-class="preset-dropdown">
            <template #prefix><span class="preset-dot" :style="{ background: presetCfg.color }"></span></template>
            <el-option v-for="cfg in PRESET_LIST" :key="cfg.value" :label="cfg.label" :value="cfg.value">
              <div class="preset-option">
                <div class="preset-option-head">
                  <span class="preset-dot" :style="{ background: cfg.color }"></span>
                  <span class="preset-option-title">{{ cfg.label }}</span>
                  <span class="preset-tags"><el-tag v-for="t in cfg.tags" :key="t" size="small" effect="plain">{{ t }}</el-tag></span>
                </div>
                <div class="preset-option-meta">{{ cfg.slogan }} · 单段≤{{ cfg.durationMax }}s · 积分系数 {{ cfg.priceFactor }}x</div>
              </div>
            </el-option>
          </el-select>
          <el-popover placement="bottom" :width="320" trigger="hover">
            <template #reference>
              <span class="preset-info-icon">ⓘ</span>
            </template>
            <div class="preset-detail">
              <div class="preset-detail-title" :style="{ color: presetCfg.color }">{{ presetCfg.label }}</div>
              <div class="preset-detail-slogan">{{ presetCfg.slogan }}</div>
              <div class="preset-detail-line"><b>速度档位：</b>{{ presetCfg.speedLabel }}</div>
              <div class="preset-detail-line"><b>单段时长上限：</b>{{ presetCfg.durationMax }} 秒</div>
              <div class="preset-detail-line"><b>默认模型：</b>{{ modelLabel(presetCfg.defaultModel) }}</div>
              <div class="preset-detail-line"><b>可用模型：</b>{{ presetCfg.allowedModels.map(modelLabel).join('、') }}</div>
              <div class="preset-detail-line"><b>积分系数：</b>{{ presetCfg.priceFactor }}x</div>
              <div class="preset-detail-line"><b>画质策略：</b>{{ presetCfg.features.join('、') }}</div>
              <div class="preset-detail-desc">{{ presetCfg.desc }}</div>
            </div>
          </el-popover>
        </div>
        <div class="fgrp"><label>底层模型</label>"""
reps.append((a3,n3,'preset template'))

# 4) 插入预设 CSS
a4=""".fgrp label { font-size: 12px; color: var(--sub); }
.credits-tag { font-size: 12px; font-weight: 700; color: var(--pur); background: rgba(138,92,255,.15); border: 1px solid rgba(138,92,255,.4); border-radius: 10px; padding: 5px 12px; }"""
n4=""".fgrp label { font-size: 12px; color: var(--sub); }
.credits-tag { font-size: 12px; font-weight: 700; color: var(--pur); background: rgba(138,92,255,.15); border: 1px solid rgba(138,92,255,.4); border-radius: 10px; padding: 5px 12px; }

/* 套餐选择器：降低决策成本，把速度/时长/模型/价格/画质策略打包展示 */
.preset-grp { align-items: center; }
.preset-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
.preset-info-icon { font-size: 14px; color: var(--sub); cursor: pointer; margin-left: 2px; user-select: none; }
.preset-info-icon:hover { color: var(--pur); }
.preset-option { padding: 6px 0; }
.preset-option-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.preset-option-title { font-weight: 700; color: var(--txt); }
.preset-tags { display: flex; gap: 4px; margin-left: auto; }
.preset-option-meta { font-size: 12px; color: var(--sub); margin-top: 4px; }
.preset-detail { font-size: 13px; color: var(--txt); }
.preset-detail-title { font-size: 15px; font-weight: 800; margin-bottom: 6px; }
.preset-detail-slogan { color: var(--sub); margin-bottom: 10px; }
.preset-detail-line { margin-bottom: 6px; }
.preset-detail-line b { color: var(--sub); font-weight: 600; }
.preset-detail-desc { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); color: var(--sub); line-height: 1.5; }
:global(.preset-dropdown .el-select-dropdown__item) { height: auto; padding: 8px 16px; }"""
reps.append((a4,n4,'preset CSS'))

for old,new,desc in reps:
    c=s.count(old)
    if c!=1:
        print('[FAIL]',desc,'count=',c); sys.exit(1)
    s=s.replace(old,new,1)
    print('[OK]',desc)

io.open(PATH,'w',encoding='utf-8').write(s)
print('DONE')
