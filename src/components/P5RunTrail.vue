<script setup>
/**
 * P5RunTrail.vue —— P5 动漫逃跑路径加载动画（红色闪电）
 * 依据：
 *  - 《P5动漫跑路轨迹动画_行为规格v3》：白色光点（奔跑者）沿无规则路径奔跑，身后拖红色尾巴（尾部持续消散），
 *    头部端实色、尾部渐隐；分帧 stroke-opacity（不用固定渐变）；方向正确（头=实色端）。
 *  - 《P5主题二次强化补充规格》第24行 + 第575行：逃跑路径 = 行动本身（红色闪电）
 *    = 红色折线 + 烟尘 + 四角星/五角星 + 硬影。
 * 视觉落点：
 *  - 折线/闪电：路径为尖锐折角折线（straight segment + butt cap + 大摆幅），非平滑曲线 → "硬"且像逃跑。
 *  - 硬影：深红(#8A0018)偏移(+7,+7)投影层，印刷式零模糊硬影。
 *  - 烟尘：仅 2 团小淡白 puff（远少于上一版 3×4 大云朵）。
 *  - 四角星爆：3 颗白色四角星随头部拖尾（P5 签名星爆）。
 *  - 全屏贯穿：组件铺满 .p5-loading-overlay；颜色硬编码（P5 专用，避免依赖 html.t-p5 令牌解析时机）。
 */
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'

const props = defineProps({
  replayKey: { type: Number, default: 0 },
  loop: { type: Boolean, default: false },
})

// 全屏坐标系：viewBox 1000 x 600，路径从左贯穿到右（preserveAspectRatio=slice 填满视口）
const W = 1000
const H = 600
const DURATION = 1700 // 奔跑节奏 ≈1.7s
const TAIL_LEN = 300 // 拖尾（viewBox 单位）——按用户要求拉长，明显一条红色闪电
const HEAD_R = 14 // 白色光点半径
const HEAD_W = 20 // 绯红折线线宽（粗，硬）
const SHADOW_W = 22 // 硬影线宽
const SHADOW_DX = 7 // 硬影偏移 X
const SHADOW_DY = 7 // 硬影偏移 Y

const head = ref(null)
const segs = ref([])
const dust = ref([])
const stars = ref([])

let raf = 0
let replayTimer = 0
let startTime = 0
let history = [] // 轨迹历史，history[0] 为最新（头部）
let controlPts = []
let cum = []
let totalLen = 0
let reduced = false

const rand = (a, b) => a + Math.random() * (b - a)

// 2.1 无规则路径生成：起点(左) → 终点(右)，中间 5~7 个随机折线控制点（大摆幅、随机折向 → 闪电感）
function buildPath() {
  const P0 = { x: 20, y: 300 }
  const Pn = { x: 980, y: 300 }
  const n = Math.floor(rand(5, 8)) // 5~7
  const pts = [P0]
  for (let i = 1; i <= n; i++) {
    const fx = i / (n + 1)
    const x = P0.x + (Pn.x - P0.x) * fx
    // 随机折向 + 随机摆幅（非规则 Z、非波形）→ 不规则闪电折线
    const dir = Math.random() < 0.5 ? -1 : 1
    const y = 300 + dir * (90 + Math.random() * 170) // ±90~260 大摆幅
    pts.push({ x, y })
  }
  pts.push(Pn)
  controlPts = pts
  cum = [0]
  for (let i = 1; i < pts.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y))
  }
  totalLen = cum[cum.length - 1]
}

// 沿路径按弧长取坐标（分段线性插值）
function posAt(dist) {
  if (dist <= 0) return { ...controlPts[0] }
  if (dist >= totalLen) return { ...controlPts[controlPts.length - 1] }
  let i = 1
  while (i < cum.length && cum[i] < dist) i++
  const segLen = cum[i] - cum[i - 1] || 1
  const t = (dist - cum[i - 1]) / segLen
  const a = controlPts[i - 1]
  const b = controlPts[i]
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
}

function reset() {
  history = []
  segs.value = []
  dust.value = []
  stars.value = []
  head.value = null
  buildPath()
  startTime = performance.now()
}

// 四角星（P5 星爆）path
function starPath(S, s) {
  return `M0,${-S} L${s},${-s} L${S},0 L${s},${s} L0,${S} L${-s},${s} L${-S},0 L${-s},${-s} Z`
}
const STAR_D = starPath(13, 4)

function frame(now) {
  if (reduced) return
  let t = (now - startTime) / DURATION
  if (t > 1) t = 1
  const d = t * totalLen
  const h = posAt(d)
  head.value = h
  // 2.2 轨迹数组：最新点在头部端（数组头）
  history.unshift(h)
  if (history.length > 400) history.pop()

  // 拖尾：头部前进多少，尾部就消散多少 → 取距头部弧长 ≤ TAIL_LEN 的点
  const pts = [h]
  let acc = 0
  for (let i = 1; i < history.length; i++) {
    const seg = Math.hypot(history[i - 1].x - history[i].x, history[i - 1].y - history[i].y)
    if (acc + seg >= TAIL_LEN) break
    acc += seg
    pts.push(history[i])
  }

  // 分段渲染：头部段 opacity=1 → 尾部段 opacity=0（方向正确：头=实色端）
  const m = pts.length - 1
  const arr = []
  for (let i = 0; i < m; i++) {
    const op = m > 1 ? 1 - i / (m - 1) : 1
    arr.push({
      x1: pts[i].x, y1: pts[i].y,
      x2: pts[i + 1].x, y2: pts[i + 1].y,
      o: Math.max(0, Math.min(1, op)),
    })
  }
  segs.value = arr

  // 烟尘：仅 2 团小淡白 puff（落后于头部 16 / 34 个轨迹点）
  const dl = []
  const dustIdx = [16, 34]
  const dustOps = [0.5, 0.32]
  const dustR = [9, 6]
  for (let k = 0; k < 2; k++) {
    const idx = dustIdx[k]
    if (history.length > idx) {
      const c = history[idx]
      dl.push({ x: c.x, y: c.y, r: dustR[k], op: dustOps[k] })
    }
  }
  dust.value = dl

  // 四角星爆：3 颗白色四角星随头部拖尾（落后于头部 9 / 21 / 33 个轨迹点）
  const sl = []
  const starIdx = [9, 21, 33]
  const starOps = [0.95, 0.7, 0.45]
  const starScale = [1.0, 0.8, 0.6]
  for (let k = 0; k < 3; k++) {
    const idx = starIdx[k]
    if (history.length > idx) {
      const c = history[idx]
      sl.push({ x: c.x, y: c.y, op: starOps[k], sc: starScale[k], rot: (k * 37) % 90 - 45 })
    }
  }
  stars.value = sl

  if (t >= 1) {
    if (props.loop) {
      raf = 0
      replayTimer = setTimeout(() => {
        if (!reduced) { reset(); raf = requestAnimationFrame(frame) }
      }, 500)
      return
    }
    raf = 0
    return
  }
  raf = requestAnimationFrame(frame)
}

function start() {
  if (raf) cancelAnimationFrame(raf)
  if (replayTimer) clearTimeout(replayTimer)
  if (reduced) {
    buildPath()
    head.value = { ...controlPts[controlPts.length - 1] }
    segs.value = []
    dust.value = []
    stars.value = []
    return
  }
  reset()
  raf = requestAnimationFrame(frame)
}

onMounted(() => {
  try {
    reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch (e) { reduced = false }
  start()
})

onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
  if (replayTimer) clearTimeout(replayTimer)
})

watch(() => props.replayKey, () => { if (!reduced) start() })
</script>

<template>
  <div class="p5-run-trail" role="img" aria-label="加载中">
    <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="xMidYMid slice" class="rt-svg">
      <defs>
        <filter id="rtGlow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="5" />
        </filter>
      </defs>

      <!-- 硬影层：深红偏移投影（印刷式零模糊硬影） -->
      <g class="rt-shadow" :transform="`translate(${SHADOW_DX},${SHADOW_DY})`">
        <line v-for="(s, i) in segs" :key="'s' + i"
          :x1="s.x1" :y1="s.y1" :x2="s.x2" :y2="s.y2"
          :stroke-width="SHADOW_W" :stroke-opacity="s.o" stroke-linecap="butt" />
      </g>

      <!-- 绯红折线层（头部端实色，尾部渐隐至透明） -->
      <g class="rt-red">
        <line v-for="(s, i) in segs" :key="'r' + i"
          :x1="s.x1" :y1="s.y1" :x2="s.x2" :y2="s.y2"
          :stroke-width="HEAD_W" :stroke-opacity="s.o" stroke-linecap="butt" stroke-linejoin="miter" />
      </g>

      <!-- 烟尘：少量小淡白 puff -->
      <g class="rt-dust">
        <g v-for="(c, i) in dust" :key="'d' + i" :transform="`translate(${c.x},${c.y})`" :opacity="c.op">
          <circle :r="c.r" class="rt-dust-puff" filter="url(#rtGlow)" />
        </g>
      </g>

      <!-- 四角星爆（P5 签名星） -->
      <g class="rt-stars">
        <path v-for="(s, i) in stars" :key="'t' + i"
          :d="STAR_D" class="rt-star"
          :transform="`translate(${s.x},${s.y}) rotate(${s.rot}) scale(${s.sc})`"
          :opacity="s.op" />
      </g>

      <!-- 头部白色光点 + 辉光（奔跑者） -->
      <g v-if="head" :transform="`translate(${head.x},${head.y})`">
        <circle :r="HEAD_R * 2.4" class="rt-glow" filter="url(#rtGlow)" />
        <circle :r="HEAD_R" class="rt-head" />
      </g>
    </svg>

    <div class="rt-label">载入创作驾驶舱…</div>
  </div>
</template>

<style scoped>
/* 铺满父级（.p5-loading-overlay 已是全屏 fixed），轨迹贯穿整页 */
.p5-run-trail { position: absolute; inset: 0; width: 100%; height: 100%; }
.rt-svg { width: 100%; height: 100%; display: block; overflow: visible; }
/* 颜色硬编码：本组件仅 P5 专用，避免依赖 html.t-p5 令牌解析时机导致红色/星缺失 */
.rt-shadow line { stroke: #8A0018; } /* 深红硬影 */
.rt-red line { stroke: #E4002B; }    /* 绯红折线 */
.rt-glow { fill: #FFFFFF; opacity: 0.4; }
.rt-head { fill: #FFFFFF; }
.rt-dust-puff { fill: #FFFFFF; }
.rt-star { fill: #FFFFFF; }
.rt-label {
  position: absolute; left: 50%; bottom: 9%; transform: translateX(-50%) skewX(-8deg);
  font-family: 'Arial Narrow', 'Oswald', sans-serif;
  letter-spacing: 0.32em; text-transform: uppercase;
  font-weight: 900; font-size: clamp(16px, 2.6vw, 34px);
  color: #FFFFFF; white-space: nowrap;
  text-shadow: 5px 5px 0 #E4002B, 5px 5px 0 #E4002B;
  pointer-events: none;
}
.rt-label::before { content: '▶ '; color: #E4002B; }
</style>
