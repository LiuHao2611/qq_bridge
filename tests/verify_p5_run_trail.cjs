/* tests/verify_p5_run_trail.cjs
 * 验证 P5 首页跑路轨迹加载动画（依据《P5动漫跑路轨迹动画_行为规格v3》）
 * 机判：尾部消散 / 尾巴长度恒定≈100px / 头部实尾部虚 / 路径前进 / 3团云朵
 * 同时输出逐帧截图供人工慢放核对。
 */
const { chromium } = require('playwright')
const fs = require('fs')

const URL = process.env.URL || 'http://127.0.0.1:8099/'
const OUT = 'D:/AI/manju_agent/.tmp'
fs.mkdirSync(OUT, { recursive: true })

// 读取 SVG 当前帧几何，返回机器可判指标
async function sample(page) {
  return await page.evaluate(() => {
    const overlay = document.querySelector('.p5-loading-overlay')
    const svg = document.querySelector('.p5-loading-overlay .rt-svg')
    if (!overlay || !svg) return { overlay: !!overlay, svg: false }
    const redLines = [...svg.querySelectorAll('.rt-red line')]
    const inkLines = [...svg.querySelectorAll('.rt-ink line')]
    const clouds = svg.querySelectorAll('.rt-clouds > g').length
    const headG = svg.querySelector('.rt-glow')
    // 重建红色拖尾折线，计算总长度与首尾透明度
    let tailLen = 0
    let headX = null, headY = null
    if (redLines.length) {
      let prev = { x: +redLines[0].getAttribute('x1'), y: +redLines[0].getAttribute('y1') }
      for (const l of redLines) {
        const x1 = +l.getAttribute('x1'), y1 = +l.getAttribute('y1')
        const x2 = +l.getAttribute('x2'), y2 = +l.getAttribute('y2')
        tailLen += Math.hypot(x2 - x1, y2 - y1)
        prev = { x: x2, y: y2 }
      }
      headX = +redLines[0].getAttribute('x1')
      headY = +redLines[0].getAttribute('y1')
    }
    const firstOp = redLines.length ? +redLines[0].getAttribute('stroke-opacity') : null
    const lastOp = redLines.length ? +redLines[redLines.length - 1].getAttribute('stroke-opacity') : null
    const headFill = headG ? getComputedStyle(headG).fill : null
    return {
      overlay: true, svg: true,
      redLines: redLines.length, inkLines: inkLines.length,
      clouds, headX, headY, tailLen, firstOp, lastOp, headFill,
    }
  })
}

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console:' + m.text()) })

  await page.goto(URL, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.p5-loading-overlay', { timeout: 8000 })

  const samples = []
  const offsets = [120, 350, 700, 1000, 1300, 1700, 2100, 2600, 3200]
  let prev = null
  for (const off of offsets) {
    await page.waitForTimeout(off - (samples.length ? offsets[samples.length - 1] : 0))
    const s = await sample(page)
    s.t = off
    samples.push(s)
    await page.screenshot({ path: `${OUT}/p5trail_${String(off).padStart(4, '0')}.png` })
  }

  // 收尾：等加载结束，确认 overlay 消失（P5 5s 延迟 + 数据加载后）
  await page.waitForTimeout(9000)
  const overlayGone = (await page.$('.p5-loading-overlay')) === null

  await browser.close()

  // ---- 机判 ----
  const fails = []
  const withSvg = samples.filter((s) => s.svg)
  if (!withSvg.length) fails.push('SVG 未渲染')

  // 头部 X：按循环分段（headX 骤降=新一轮），段内必须单调前进（拖尾随头走）
  const headXs = withSvg.map((s) => s.headX).filter((x) => x != null)
  const runs = []
  let cur = []
  for (let i = 0; i < headXs.length; i++) {
    if (cur.length && headXs[i] < headXs[i - 1] - 150) { runs.push(cur); cur = [] }
    cur.push(headXs[i])
  }
  if (cur.length) runs.push(cur)
  let segMono = true
  for (const r of runs) for (let i = 1; i < r.length; i++) if (r[i] < r[i - 1] - 1) segMono = false
  if (!segMono) fails.push('某轮内头部 X 非单调前进: ' + JSON.stringify(runs))
  const flatX = [].concat(...runs)
  if (flatX.length && (Math.max(...flatX) - Math.min(...flatX)) < 450) fails.push('头部未走完全程: ' + JSON.stringify(flatX))
  if (!runs.some((r) => Math.max(...r) > 580)) fails.push('头部未抵达终点(>580): ' + JSON.stringify(runs))

  // 尾巴长度恒定 ≈100px：排除每轮起步瞬态（headX<120 即 t≈0 无/短轨迹），
  // 仅对「已跑出一段」的帧考核恒定（spec：尾部长度恒定、头部前进多少尾部消散多少）
  const midSamples = withSvg.filter((s) => s.headX != null && s.headX > 120 && s.redLines >= 3)
  const tailLens = midSamples.map((s) => s.tailLen)
  const maxT = Math.max(...tailLens), minT = Math.min(...tailLens)
  if (tailLens.length && (maxT > 130 || minT < 55)) fails.push(`尾巴长度不稳: min=${minT.toFixed(1)} max=${maxT.toFixed(1)}`)
  // 反证：必须存在「起步近零轨迹」帧（证明拖尾从 0 生长，而非整条线停留）
  const hasZeroStart = withSvg.some((s) => s.svg && s.tailLen < 30)
  if (!hasZeroStart) fails.push('未观察到起步近零轨迹(应 t=0 无轨迹)')

  // 头部实(≈1)尾部虚(≈0)
  const last = withSvg[withSvg.length - 1]
  if (last.firstOp == null || last.firstOp < 0.8) fails.push('头部端不实色: firstOp=' + last.firstOp)
  if (last.lastOp == null || last.lastOp > 0.15) fails.push('尾部端未透明: lastOp=' + last.lastOp)

  // 3 团云朵（稳定后）
  const cloudCounts = withSvg.map((s) => s.clouds)
  if (!cloudCounts.includes(3)) fails.push('未出现 3 团云朵: ' + JSON.stringify(cloudCounts))

  // 无页面错误
  if (errors.length) fails.push('页面错误: ' + errors.slice(0, 3).join(' | '))

  console.log('=== SAMPLES ===')
  for (const s of samples) {
    console.log(`t=${s.t}ms overlay=${s.overlay} svg=${s.svg} redLines=${s.redLines} clouds=${s.clouds} headX=${s.headX && s.headX.toFixed(1)} tailLen=${s.tailLen && s.tailLen.toFixed(1)} firstOp=${s.firstOp && s.firstOp.toFixed(2)} lastOp=${s.lastOp && s.lastOp.toFixed(2)}`)
  }
  console.log('overlayGoneAfterLoad=', overlayGone)
  console.log('=== RESULT ===')
  if (fails.length) {
    console.log('FAIL(' + fails.length + '):')
    fails.forEach((f) => console.log('  - ' + f))
    process.exit(1)
  } else {
    console.log('PASS: 尾部消散/长度恒定/头实尾虚/前进/3云朵 全部满足；overlay 加载后消失=' + overlayGone)
    process.exit(0)
  }
})().catch((e) => { console.error('RUNNER ERROR', e); process.exit(2) })
