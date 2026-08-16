<script setup>
/**
 * P5RunTrail.vue —— P5 动漫跑路轨迹加载动画
 * 依据《P5动漫跑路轨迹动画_行为规格v3》实现：
 *  - 白色光点（奔跑者）沿无规则蜿蜒路径向右前方奔跑
 *  - 身后恒定 ~170(viewBox单位) 绯红拖尾，尾部持续消散（分段 stroke-opacity，沿路径衰减）
 *  - 3 团白色卡通云朵（近大远小、近实远淡）+ 放射速度线
 *  - 头部白色光点 + 辉光；拖尾头部端实色绯红 + 黑描边，尾部渐隐至透明
 * 全屏贯穿：组件铺满父级（.p5-loading-overlay 全屏），SVG 用 slice 填满视口，
 * 轨迹从左贯穿到右，不再塞进小面板。颜色为本组件硬编码（P5 专用，避免依赖 html.t-p5 令牌解析时机）。
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
const TAIL_LEN = 170 // 拖尾（viewBox 单位，≈17% 宽，满屏下明显可见）
const HEAD_R = 16 // 白色光点半径
const HEAD_W = 16 // 绯红拖尾线宽
const INK_W = 26 // 黑描边线宽（HEAD_W + 10）

const head = ref(null)
const segs = ref([])
const clouds = ref([])

let raf = 0
let replayTimer = 0
let startTime = 0
let history = [] // 轨迹历史，history[0] 为最新（头部）
let controlPts = []
let cum = []
let totalLen = 0
let reduced = false

const rand = (a, b) => a + Math.random() * (b - a)

// 2.1 无规则路径生成：起点(左) → 终点(右)，中间 5~7 个随机控制点，大弯折
function buildPath() {
  const P0 = { x: 20, y: 330 }
  const Pn = { x: 980, y: 250 }
  const n = Math.floor(rand(5, 8)) // 5~7
  const pts = [P0]
  for (let i = 1; i <= n; i++) {
    const fx = i / (n + 1)
    const x = P0.x + (Pn.x - P0.x) * fx
    const yBase = P0.y + (Pn.y - P0.y) * fx
    const y = yBase + (Math.random() - 0.5) * 280 // ±140 随机大弯折
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
  clouds.value = []
  head.value = null
  buildPath()
  startTime = performance.now()
}

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

  // 2.3 卡通云朵：落后头部 10/22/36 个轨迹点；scale 1.0/0.8/0.6；opacity .9/.6/.35
  const scales = [1.0, 0.8, 0.6]
  const ops = [0.9, 0.6, 0.35]
  const idxs = [10, 22, 36]
  const cl = []
  for (let k = 0; k < 3; k++) {
    const idx = idxs[k]
    if (history.length > idx) {
      const c = history[idx]
      const dxh = c.x - h.x
      const dyh = c.y - h.y
      const len = Math.hypot(dxh, dyh) || 1
      const ux = dxh / len
      const uy = dyh / len
      const slen = 26 + k * 8 // 速度线 26~42px，向「远离头部」方向延伸
      cl.push({ x: c.x, y: c.y, r: 16 * scales[k], op: ops[k], dx: ux * slen, dy: uy * slen })
    }
  }
  clouds.value = cl

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
    // reduced-motion：静态显示头部终点一帧（不播放）
    buildPath()
    head.value = { ...controlPts[controlPts.length - 1] }
    segs.value = []
    clouds.value = []
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

// replayKey 变化时重放
watch(() => props.replayKey, () => { if (!reduced) start() })
</script>

<template>
  <div class="p5-run-trail" role="img" aria-label="加载中">
    <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="xMidYMid slice" class="rt-svg">
      <defs>
        <filter id="rtGlow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="6" />
        </filter>
      </defs>

      <!-- 黑描边层（头部端清晰，尾部随透明度同步消散） -->
      <g class="rt-ink">
        <line v-for="(s, i) in segs" :key="'k' + i"
          :x1="s.x1" :y1="s.y1" :x2="s.x2" :y2="s.y2"
          :stroke-width="INK_W" :stroke-opacity="s.o" stroke-linecap="round" />
      </g>

      <!-- 绯红拖尾层 -->
      <g class="rt-red">
        <line v-for="(s, i) in segs" :key="'r' + i"
          :x1="s.x1" :y1="s.y1" :x2="s.x2" :y2="s.y2"
          :stroke-width="HEAD_W" :stroke-opacity="s.o" stroke-linecap="round" />
      </g>

      <!-- 卡通云朵 + 速度线 -->
      <g class="rt-clouds">
        <g v-for="(c, i) in clouds" :key="'c' + i"
          :transform="`translate(${c.x},${c.y})`" :opacity="c.op">
          <line :x1="0" :y1="0" :x2="c.dx" :y2="c.dy" class="rt-speed" />
          <circle :cx="-1.2 * c.r" cy="0" :r="c.r" class="rt-cloud" />
          <circle cx="0" :cy="-0.4 * c.r" :r="c.r" class="rt-cloud" />
          <circle :cx="1.2 * c.r" cy="0" :r="c.r" class="rt-cloud" />
          <circle cx="0" :cy="0.2 * c.r" :r="c.r" class="rt-cloud" />
        </g>
      </g>

      <!-- 头部白色光点 + 辉光 -->
      <g v-if="head" :transform="`translate(${head.x},${head.y})`">
        <circle :r="HEAD_R * 2.2" class="rt-glow" filter="url(#rtGlow)" />
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
/* 颜色硬编码：本组件仅 P5 专用，避免依赖 html.t-p5 令牌解析时机导致红色缺失 */
.rt-ink line { stroke: #0B0B0B; }
.rt-red line { stroke: #E4002B; }
.rt-glow { fill: #FFFFFF; opacity: 0.35; }
.rt-head { fill: #FFFFFF; }
.rt-cloud { fill: #FFFFFF; stroke: rgba(0, 0, 0, 0.25); stroke-width: 1; }
.rt-speed { stroke: #FFFFFF; stroke-width: 3; stroke-linecap: round; opacity: 0.6; }
.rt-label {
  position: absolute; left: 50%; bottom: 9%; transform: translateX(-50%);
  font-family: 'Arial Narrow', 'Oswald', sans-serif;
  letter-spacing: 0.32em; text-transform: uppercase;
  font-weight: 800; font-size: clamp(16px, 2.6vw, 34px);
  color: #FFFFFF; white-space: nowrap;
  text-shadow: 0 0 16px rgba(228, 0, 43, 0.85), 2px 2px 0 #0B0B0B;
  pointer-events: none;
}
.rt-label::before { content: '▶ '; color: #E4002B; }
</style>
