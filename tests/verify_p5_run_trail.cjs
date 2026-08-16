// 机判验收：P5 跑路轨迹加载动画（全屏贯穿版）
// 铁律：当场重跑、断言 + 非零退出码；额外用 canvas 栅格化内联 SVG 统计红/白像素，证明红色真的渲染。
const { chromium } = require('playwright')

const URL = 'http://127.0.0.1:8099/'
const SHOT_DIR = 'D:/AI/manju_agent/.tmp'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const fails = []
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  const errors = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', (e) => errors.push(String(e)))

  await page.goto(URL, { waitUntil: 'networkidle' })

  // 等遮罩 + 红色拖尾线段出现
  await page.waitForSelector('.p5-loading-overlay', { timeout: 8000 }).catch(() => fails.push('遮罩未出现'))
  await page.waitForFunction(() => document.querySelectorAll('.rt-red line').length > 5, { timeout: 8000 })
    .catch(() => fails.push('红色拖尾线段未渲染'))

  // 1) 全屏铺满：overlay ≈ 视口；svg ≈ 视口（轨迹贯穿整页，无居中面板）
  const layout = await page.evaluate(() => {
    const ov = document.querySelector('.p5-loading-overlay')
    const svg = document.querySelector('.rt-svg')
    const ovb = ov.getBoundingClientRect()
    const sb = svg.getBoundingClientRect()
    const inner = document.querySelector('.p5-loading-inner')
    const cs = getComputedStyle(ov)
    return {
      ovW: ovb.width, ovH: ovb.height, svgW: sb.width, svgH: sb.height,
      vw: window.innerWidth, vh: window.innerHeight,
      hasInner: !!inner, ovBorder: cs.borderTopWidth + ' ' + cs.borderStyle,
    }
  })
  if (Math.abs(layout.ovW - layout.vw) > 5 || Math.abs(layout.ovH - layout.vh) > 5)
    fails.push(`遮罩未铺满: ov=${layout.ovW}x${layout.ovH} vw=${layout.vw}x${layout.vh}`)
  if (layout.svgW < layout.vw * 0.85) fails.push(`SVG 未贯穿整页: svgW=${layout.svgW.toFixed(0)} vw=${layout.vw}`)
  if (layout.hasInner) fails.push('仍存在居中面板 .p5-loading-inner（应无背景板）')
  if (!/^(0px|none)\s/.test(layout.ovBorder)) fails.push('遮罩有边框(背景板): ' + layout.ovBorder)

  // 2) 颜色机器证明：getComputedStyle 红线 = rgb(228,0,43)；白点 = #FFF
  const colors = await page.evaluate(() => {
    const rl = document.querySelector('.rt-red line')
    const hd = document.querySelector('.rt-head')
    return { red: rl ? getComputedStyle(rl).stroke : null, head: hd ? getComputedStyle(hd).fill : null }
  })
  if (!colors.red || !/228,\s*0,\s*43/.test(colors.red)) fails.push('红色 stroke 未解析为绯红: ' + colors.red)
  if (!colors.head || !/rgb\(255,\s*255,\s*255\)/.test(colors.head)) fails.push('头部非白色: ' + colors.head)

  // 3) 像素级证明：对真实截图在浏览器内取像素，统计红/白像素（证明红色真的画在屏幕上）
  const shotBuf = await page.screenshot()
  const pix = await page.evaluate(async (b64) => {
    const img = new Image()
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = 'data:image/png;base64,' + b64 })
    const cv = document.createElement('canvas'); cv.width = img.width; cv.height = img.height
    const ctx = cv.getContext('2d'); ctx.drawImage(img, 0, 0)
    const d = ctx.getImageData(0, 0, cv.width, cv.height).data
    let red = 0, white = 0
    for (let i = 0; i < d.length; i += 4) {
      const r = d[i], g = d[i + 1], b = d[i + 2], a = d[i + 3]
      if (a < 20) continue
      if (r > 150 && g < 90 && b < 90) red++
      if (r > 230 && g > 230 && b > 230) white++
    }
    return { red, white, w: img.width, h: img.height }
  }, shotBuf.toString('base64'))
  if (pix.red < 200) fails.push(`红色像素过少(疑似未渲染): red=${pix.red}`)
  if (pix.white < 100) fails.push(`白色像素过少: white=${pix.white}`)

  // 4) 逐帧密集采样：拖尾从近零生长→中段恒定；头部段内前进并抵达右端；出现 3 团云朵
  //    密集采样(70帧×35ms≈2450ms)以覆盖一次完整循环，捕获循环重启的近零长度帧（尾部消散）
  const samples = []
  for (let i = 0; i < 70; i++) {
    const s = await page.evaluate(() => {
      const lines = [...document.querySelectorAll('.rt-red line')]
      const segs = lines.map((l) => ({
        x1: +l.getAttribute('x1'), y1: +l.getAttribute('y1'),
        x2: +l.getAttribute('x2'), y2: +l.getAttribute('y2'),
        o: +l.getAttribute('stroke-opacity'),
      }))
      const head = document.querySelector('.rt-head')
      const hg = head ? head.parentElement : null
      const ht = hg ? hg.getAttribute('transform') : null
      const headX = ht ? +ht.replace(/.*?translate\(([-\d.]+),.*/, '$1') : null
      let tailLen = 0
      for (const s of segs) tailLen += Math.hypot(s.x2 - s.x1, s.y2 - s.y1)
      const firstOp = segs.length ? segs[0].o : null
      const lastOp = segs.length ? segs[segs.length - 1].o : null
      const clouds = document.querySelectorAll('.rt-clouds > g').length
      return { n: segs.length, tailLen, firstOp, lastOp, headX, clouds }
    })
    samples.push(s)
    await sleep(35)
  }
  const withSvg = samples.filter((s) => s.n > 0)
  if (!withSvg.length) fails.push('无任何拖尾线段')
  // 尾部消散：存在近零长度帧（循环重启 t=0）
  const minTail = Math.min(...withSvg.map((s) => s.tailLen))
  if (minTail >= 45) fails.push('未出现尾部消散(近零长度帧): minTail=' + minTail.toFixed(1))
  // 中段恒定：取长度较大的样本，max-min 不应过大
  const big = withSvg.filter((s) => s.tailLen > 120).map((s) => s.tailLen)
  if (big.length) {
    const mx = Math.max(...big), mn = Math.min(...big)
    if (mx - mn > 120) fails.push(`拖尾长度不稳: min=${mn.toFixed(0)} max=${mx.toFixed(0)}`)
  }
  // 头部实、尾部虚（取中段样本）
  const mid = withSvg.find((s) => s.tailLen > 120) || withSvg[withSvg.length - 1]
  if (mid.firstOp == null || mid.firstOp < 0.8) fails.push('头部端不实色: firstOp=' + mid.firstOp)
  if (mid.lastOp == null || mid.lastOp > 0.15) fails.push('尾部端未透明: lastOp=' + mid.lastOp)
  // 头部抵达右端（viewBox x≈980）
  const maxHeadX = Math.max(...withSvg.map((s) => s.headX || 0))
  if (maxHeadX < 880) fails.push('头部未抵达右端: maxHeadX=' + maxHeadX)
  // 3 团云朵
  if (!withSvg.some((s) => s.clouds === 3)) fails.push('未出现 3 团云朵')

  // 截图留存（视觉复核）
  await page.screenshot({ path: SHOT_DIR + '/p5trail_full_early.png' })
  await sleep(900)
  await page.screenshot({ path: SHOT_DIR + '/p5trail_full_mid.png' })

  // 5) 加载结束后遮罩消失（P5 放慢 5s；留足余量）
  await page.waitForTimeout(6500)
  const overlayGone = (await page.$('.p5-loading-overlay')) === null
  if (!overlayGone) fails.push('加载结束后遮罩未消失')

  if (errors.length) fails.push('页面错误: ' + errors.slice(0, 3).join(' | '))

  await browser.close()

  console.log('=== P5 跑路轨迹验收 ===')
  console.log('布局:', JSON.stringify(layout))
  console.log('颜色:', JSON.stringify(colors))
  console.log('像素 red/white:', JSON.stringify(pix))
  console.log('样本:', JSON.stringify(withSvg))
  if (fails.length) {
    console.log('FAIL(' + fails.length + '): ' + fails.join(' ; '))
    process.exit(1)
  }
  console.log('PASS · 全屏贯穿 / 红色渲染 / 拖尾消散 / 头部白点 / 云朵×3 全部满足')
  process.exit(0)
})().catch((e) => { console.error('RUN_ERROR', e); process.exit(2) })
