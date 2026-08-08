<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { usePersistedState } from '../composables/usePersist'
import { creativeGenerate } from '../api.js'

defineOptions({ name: 'Creative' })

const router = useRouter()

const cats = [
  { key: 'all', label: '全部' },
  { key: 'story', label: '剧情模板' },
  { key: 'ad', label: '广告模板' },
  { key: 'festival', label: '节日模板' },
  { key: 'edu', label: '知识科普' },
]
const activeCat = ref('all')

const templates = [
  {
    id: 1, cat: 'story', icon: '💔', name: '都市情感逆袭', heat: 9821,
    desc: '北漂女孩从被甩到逆袭成品牌主理人的 12 集短剧。',
    tags: ['情感', '逆袭', '女性向'],
    detail: '经典“先抑后扬”情感短剧结构，适合抖音/快手竖屏。提供主角人设、关键转折钩子与分镜节奏表，新手可直接套用。',
    storyboard: [
      '第1幕：深夜出租屋哭戏，字幕“第 365 天，他又走了”。',
      '第3幕：地铁偶遇贵人，特写一张名片。',
      '第8幕：新品发布会逆光升格，弹幕“这就是女王”。',
    ],
    prompt: '电影感竖屏，冷蓝色调开场，第8幕转为暖金逆光，主角红色风衣。',
    scenes: ['情感号矩阵', '女频剧情号', '品牌人设短剧'],
  },
  {
    id: 2, cat: 'story', icon: '🕵️', name: '悬疑反转探案', heat: 7640,
    desc: '每集一个密室谜题，第 10 集揭开连环真凶。',
    tags: ['悬疑', '反转', '烧脑'],
    detail: '强钩子结构：每集结尾留 1 个致命悬念。附谜题库与反转点排布示意，适合做连载涨粉。',
    storyboard: [
      '开场：钟声 12 下，门从外面反锁。',
      '中段：放大镜扫过 3 处矛盾证词。',
      '结尾：凶手竟是画外音旁白者。',
    ],
    prompt: '暗调霓虹，雨夜窗面倒影，低机位压迫感，胶片颗粒。',
    scenes: ['悬疑剧情号', '解谜涨粉', '互动投票剧'],
  },
  {
    id: 3, cat: 'ad', icon: '🍜', name: '美食种草带货', heat: 11203,
    desc: '30 秒一碗面的暴风吸入，挂车转化专用。',
    tags: ['带货', '美食', '种草'],
    detail: '黄金 3 秒 + 咀嚼 ASMR + 价格钩子。内置口播文案与促销贴片位，适合抖音小店。',
    storyboard: [
      '0-3s：热气腾腾特写 + “饿了别划走”。',
      '15s：筷子挑起面条慢动作。',
      '28s：价格贴片“9.9 三连包”。',
    ],
    prompt: '高饱和暖光美食特写，浅景深，蒸汽粒子，食欲感拉满。',
    scenes: ['抖音小店', '本地生活', '达人带货'],
  },
  {
    id: 4, cat: 'ad', icon: '💄', name: '美妆测评对比', heat: 8654,
    desc: 'Before/After 左右分屏，一款到底测评。',
    tags: ['美妆', '测评', '对比'],
    detail: '标准化分屏模板：左素颜右妆后，含上妆过程快剪与成分口播，适合新品类冷启动。',
    storyboard: [
      '前 5s：素颜怼脸原相机。',
      '中 15s：分区上妆加速剪辑。',
      '后 10s：妆后 4 角度轮播。',
    ],
    prompt: '柔光棚拍，肤色还原真实，妆后加微珠光高光，干净背景。',
    scenes: ['美妆品牌', '测评号', '新品首发'],
  },
  {
    id: 5, cat: 'festival', icon: '🏮', name: '春节团圆合家欢', heat: 6902,
    desc: '返乡团聚温情向，适合节前 2 周投放。',
    tags: ['春节', '温情', '家庭'],
    detail: '团圆饭 + 长途返乡 + 一句点题金句。含节气配色与灯笼/春联素材位，节庆氛围拉满。',
    storyboard: [
      '开场：高铁窗外的雪与归途字幕。',
      '高潮：一桌年夜饭全家举杯。',
      '收尾：字幕“有钱没钱，回家过年”。',
    ],
    prompt: '暖红金配色，灯笼虚化前景，烟火热气，喜庆但不艳俗。',
    scenes: ['节庆营销', '品牌温情片', '政务宣传'],
  },
  {
    id: 6, cat: 'festival', icon: '🌕', name: '中秋月圆思念', heat: 5210,
    desc: '异地恋/游子思乡，月饼礼盒植入位。',
    tags: ['中秋', '思念', '礼盒'],
    detail: '明月意象贯穿，含产品软植入桥段与团圆反转，适合食品礼盒品牌。',
    storyboard: [
      '开场：两地同望一轮月。',
      '转折：快递盒上的手写便签。',
      '团圆：视频通话里的月饼。',
    ],
    prompt: '深蓝夜空大月亮，冷调孤独感，结尾转暖室内光。',
    scenes: ['食品礼盒', '文旅宣传', '情感号'],
  },
  {
    id: 7, cat: 'edu', icon: '🔬', name: '3 分钟硬核科普', heat: 8033,
    desc: '把复杂原理讲成动画，涨知粉利器。',
    tags: ['科普', '知识', '动画'],
    detail: '“设问—类比—结论”三段式，附知识脚本框架与信息图分镜，适合做知识付费引流。',
    storyboard: [
      '设问：“黑洞为什么连光都逃不掉？”',
      '类比：把时空比作蹦床。',
      '结论：一句话总结 + 关注引导。',
    ],
    prompt: '扁平信息图风格，蓝紫科技色，图标动效，无真人出镜。',
    scenes: ['知识付费', '科普号', '教育机构'],
  },
  {
    id: 8, cat: 'edu', icon: '📚', name: '职场避坑指南', heat: 6451,
    desc: '打工人共鸣向，每条一个真实坑。',
    tags: ['职场', '干货', '共鸣'],
    detail: '情景短剧 + 字幕金句 + 避坑清单。内置常见话术库，适合做职场 IP 人设。',
    storyboard: [
      '情景：同事甩锅现场。',
      '拆解：3 个识别信号。',
      '金句：“活干完，邮件也要发完”。',
    ],
    prompt: '办公室自然光，手持跟拍，真实感，加字幕条与高亮标注。',
    scenes: ['职场 IP', 'HR 机构', '知识矩阵'],
  },
  {
    id: 9, cat: 'story', icon: '👻', name: '民俗怪谈惊悚', heat: 5988,
    desc: '中式恐怖，老宅红衣单元剧。',
    tags: ['惊悚', '民俗', '单元'],
    detail: '中式留白恐怖：音效 > 画面。含氛围分镜与禁用的跳脸清单，适合做午夜档。',
    storyboard: [
      '长镜：走廊尽头忽明忽暗的灯。',
      '声效：木楼梯吱呀声渐强。',
      '收尾：镜中多了一个人影。',
    ],
    prompt: '青灰阴冷调，烛光闪烁，噪点胶片，克制不血腥。',
    scenes: ['惊悚号', '民俗文创', '深夜档'],
  },
  {
    id: 10, cat: 'ad', icon: '🚗', name: '汽车卖点快剪', heat: 4367,
    desc: '10 秒讲清一个核心卖点。',
    tags: ['汽车', '快剪', '参数'],
    detail: '参数可视化模板：速度/空间/智驾分镜模块化，附裸眼 3D 转场示意，适合新车型上市。',
    storyboard: [
      '0-3s：钥匙靠近自动解锁。',
      '4-7s：中控大屏滑动演示。',
      '8-10s：夜路智驾光轨。',
    ],
    prompt: '电影级车漆反光，城市夜景光轨，低饱和高级灰。',
    scenes: ['车企上市', '经销商', '汽车媒体'],
  },
  {
    id: 11, cat: 'festival', icon: '🎃', name: '万圣糖果狂欢', heat: 3102,
    desc: '亲子向搞怪短剧，糖果品牌植入。',
    tags: ['万圣', '亲子', '搞怪'],
    detail: '扮装 + 讨糖动线 + 反转结局，含服装道具清单，适合快消与亲子号。',
    storyboard: [
      '开门：幽灵装小孩讨糖。',
      '反转：门后大人更吓人。',
      '收尾：全家分糖大笑。',
    ],
    prompt: '紫橙霓虹，南瓜灯暖光，卡通惊悚，明快节奏。',
    scenes: ['快消品牌', '亲子号', '线下活动'],
  },
  {
    id: 12, cat: 'edu', icon: '🌱', name: '健康养生小课堂', heat: 7225,
    desc: '每天一个养生误区，银发族最爱。',
    tags: ['健康', '养生', '银发'],
    detail: '“误区—真相—做法”结构，字大易懂，附医生口播提示，适合银发流量变现。',
    storyboard: [
      '误区：“喝醋能软化血管？”',
      '真相：动画演示血管结构。',
      '做法：3 步正确养护。',
    ],
    prompt: '清新浅绿白配色，真人出镜 + 图标辅助，字幕加大。',
    scenes: ['银发流量', '养生号', '大健康'],
  },
]

const catLabel = (c) => (cats.find(x => x.key === c) || {}).label || c
const filtered = computed(() => activeCat.value === 'all' ? templates : templates.filter(t => t.cat === activeCat.value))

const detail = ref(null)
const dialogVisible = ref(false)
const openDetail = (t) => { detail.value = t; dialogVisible.value = true }
const applyTpl = () => {
  ElMessage.success('已套用模板到新项目（演示）')
  router.push('/projects')
}
const newTpl = () => ElMessage('“自建模板”功能演示中，敬请期待（演示）')

// ---- AI 灵感生成（Dify 编排优先 / Ollama 回退 / 模板兜底）----
const cIdea = usePersistedState('wn_creative_state', { idea: '', style: '短剧', result: null, used: '' })
const generating = ref(false)
const usedLabel = (u) => ({ dify: 'Dify 编排', ollama: 'Ollama 本地', template: '本地模板兜底' }[u] || u || '')
async function genCreative() {
  if (!cIdea.idea.trim()) { ElMessage.warning('请先输入创意点子'); return }
  if (generating.value) return
  generating.value = true
  try {
    const r = await creativeGenerate({ idea: cIdea.idea, style: cIdea.style })
    cIdea.result = r || null
    cIdea.used = r?.used || ''
    ElMessage.success('创意已生成（' + usedLabel(r?.used) + '）')
  } catch (e) {
    ElMessage.error('生成失败：' + (e.message || e))
  } finally {
    generating.value = false
  }
}
const applyCreative = () => {
  ElMessage.success('已套用创意到新项目（演示）')
  router.push('/projects')
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>创意工坊</h1>
        <span class="muted">精选剧情 / 广告 / 节日 / 科普模板，一键套用生成新项目</span>
      </div>
      <button class="btn pink sm" @click="newTpl">＋ 自建模板</button>
    </div>

    <!-- AI 灵感生成（Dify 编排 / Ollama 回退 / 模板兜底） -->
    <div class="ai-gen">
      <div class="ai-head">
        <div>
          <h2>✨ AI 灵感生成</h2>
          <p class="muted">一句话描述你的点子，由 Dify 编排大脑（或本地 Ollama）扩展为结构化创作简报</p>
        </div>
        <span class="badge" v-if="cIdea.used">{{ usedLabel(cIdea.used) }}</span>
      </div>
      <div class="ai-row">
        <el-input v-model="cIdea.idea" type="textarea" :rows="2" style="flex:1"
          placeholder="例如：一个社恐程序员靠 AI 伪造身份混入豪门晚宴，最后真爱破解骗局" />
        <el-select v-model="cIdea.style" style="width:160px">
          <el-option label="短剧" value="短剧" />
          <el-option label="广告" value="广告" />
          <el-option label="知识科普" value="知识科普" />
          <el-option label="节日" value="节日" />
        </el-select>
        <button class="btn pink" :disabled="generating" @click="genCreative">
          {{ generating ? '生成中…' : 'AI 生成创意' }}
        </button>
      </div>
      <div v-if="cIdea.result" class="brief">
        <div class="brief-title">{{ cIdea.result.title }}</div>
        <div class="brief-premise">{{ cIdea.result.premise }}</div>
        <div class="brief-tags">
          <span v-for="t in (cIdea.result.tags||[])" :key="t" class="badge" style="position:static;background:var(--card-2);color:var(--sub)">{{ t }}</span>
        </div>
        <el-divider content-position="left">分镜建议</el-divider>
        <div v-for="(s,i) in (cIdea.result.shots||[])" :key="i" class="list-row">
          <span class="idx">{{ i+1 }}</span><span style="flex:1">{{ s }}</span>
        </div>
        <el-divider content-position="left">适用场景</el-divider>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <el-tag v-for="sc in (cIdea.result.scenes||[])" :key="sc" type="info" effect="dark">{{ sc }}</el-tag>
        </div>
        <div class="brief-acts">
          <button class="btn sm" @click="applyCreative">套用到新项目</button>
        </div>
      </div>
    </div>

    <div class="tabs" style="margin-bottom:18px">
      <div
        v-for="c in cats" :key="c.key"
        class="tab" :class="{ active: activeCat === c.key }"
        @click="activeCat = c.key"
      >{{ c.label }}</div>
    </div>

    <div v-if="filtered.length" class="proj-grid">
      <div
        v-for="t in filtered" :key="t.id"
        class="proj-card"
        @click="openDetail(t)"
      >
        <div class="proj-cover" :style="{ background: 'linear-gradient(135deg,' + (t.cat==='ad' ? '#ff4d8d22,#8a5cff22' : t.cat==='festival' ? '#ffb02022,#ff4d8d22' : t.cat==='edu' ? '#4d9fff22,#8a5cff22' : '#8a5cff22,#ff4d8d22') + ')' }">
          <span style="font-size:54px">{{ t.icon }}</span>
          <span class="badge draft">{{ catLabel(t.cat) }}</span>
        </div>
        <div class="proj-body">
          <div class="proj-title">{{ t.name }}</div>
          <div class="proj-meta"><span class="t" style="color:var(--sub);font-size:12px">{{ t.desc }}</span></div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">
            <span v-for="tag in t.tags" :key="tag" class="badge" style="position:static;background:var(--card-2);color:var(--sub)">{{ tag }}</span>
          </div>
        </div>
        <div class="proj-actions">
          <span class="muted">🔥 {{ t.heat.toLocaleString() }}</span>
          <span style="margin-left:auto" class="btn ghost sm" @click.stop="openDetail(t)">查看</span>
        </div>
      </div>
    </div>
    <div v-else class="empty">该分类下暂无模板（演示）</div>

    <el-dialog
      v-model="dialogVisible"
      :title="detail ? detail.name : ''"
      width="560px"
      align-center
    >
      <template v-if="detail">
        <div style="display:flex;gap:10px;align-items:center;margin-bottom:14px">
          <span style="font-size:40px">{{ detail.icon }}</span>
          <div>
            <div style="font-weight:700;font-size:16px">{{ detail.name }}</div>
            <div class="muted">分类：{{ catLabel(detail.cat) }} · 🔥 {{ detail.heat.toLocaleString() }}</div>
          </div>
        </div>
        <p style="line-height:1.7;color:var(--txt)">{{ detail.detail }}</p>
        <el-divider content-position="left">示例分镜</el-divider>
        <div v-for="(s, i) in detail.storyboard" :key="i" class="list-row" style="margin-bottom:6px">
          <span class="idx">{{ i + 1 }}</span><span style="flex:1">{{ s }}</span>
        </div>
        <el-divider content-position="left">提示词片段</el-divider>
        <div class="textarea" style="background:#0f1117">{{ detail.prompt }}</div>
        <el-divider content-position="left">适用场景</el-divider>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <el-tag v-for="sc in detail.scenes" :key="sc" type="info" effect="dark">{{ sc }}</el-tag>
        </div>
      </template>
      <template #footer>
        <el-button @click="dialogVisible = false">关闭</el-button>
        <el-button type="primary" @click="applyTpl">一键套用</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.idx { display: inline-flex; width: 22px; height: 22px; align-items: center; justify-content: center; background: var(--pink); color: #fff; border-radius: 6px; font-weight: 700; font-size: 12px; flex: 0 0 22px; }
.ai-gen { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; margin-bottom: 18px; }
.ai-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.ai-head h2 { font-size: 16px; margin: 0 0 4px; }
.ai-head .muted { font-size: 12px; }
.ai-row { display: flex; gap: 12px; align-items: flex-start; }
.ai-row .btn.pink { flex: 0 0 auto; align-self: stretch; padding: 0 18px; }
.brief { margin-top: 16px; padding-top: 14px; border-top: 1px dashed var(--line); }
.brief-title { font-size: 18px; font-weight: 800; color: var(--txt); }
.brief-premise { color: var(--sub); margin: 6px 0 10px; line-height: 1.6; }
.brief-tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 4px; }
.list-row { display: flex; gap: 8px; align-items: flex-start; margin-bottom: 6px; color: var(--txt); line-height: 1.6; }
.brief-acts { margin-top: 14px; display: flex; justify-content: flex-end; }
</style>
