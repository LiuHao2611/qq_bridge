// FC-05 验证：P5 主题二次强化 7 家族渲染 + 零泄漏 + p5ft 门控
// 用法：node tests/audit_p5_reinforce.mjs
// 退出码：0 = 全部通过；1 = 存在泄漏 / 缺失 / 门控失效
import { chromium } from 'playwright'

const BASE = 'http://localhost:8099/'
const PRESET_KEY = 'manju_theme'
const THEMES = ['p5', 'cyberpunk', 'default', 'gray']

// 在某主题下注入 7 家族样例 DOM，读取关键 computed-style 签名
const INJECT = () => {
  const host = document.createElement('div')
  host.id = 'p5-fixture'
  host.innerHTML = `
    <div class="p5-dbox p5-dbox--cloud"><span class="who">导演</span><p>x</p></div>
    <div class="p5-banner"><span class="top">短剧项目</span><span class="bottom">PROJECT</span></div>
    <span class="p5-relic"><i class="glow"></i><i class="core"></i></span>
    <button class="p5-btn--sticker"><span class="ico">✎</span><span>任务</span></button>
    <button class="p5-btn--stroke">+</button>
    <div class="p5-keygroup"><span class="p5-keygroup__item"><kbd>LSHFT</kbd></span></div>
    <div class="p5-countdown"><span>01:15</span><em></em></div>
    <span class="p5-key">TAB</span>
    <div class="p5-texband"></div>
    <h2 class="p5-title--poster">STAGE</h2>
  `
  document.body.appendChild(host)
  return true
}

const READ = () => {
  const q = (s) => document.querySelector(s)
  const cs = (s, p) => { const el = q(s); return el ? getComputedStyle(el)[p] : null }
  const rect = (s) => { const el = q(s); return el ? el.offsetWidth : null }
  return {
    cloud_border: cs('.p5-dbox--cloud', 'borderTopWidth'),
    banner_clip: cs('.p5-banner .top', 'clipPath'),
    relic_filter: cs('.p5-relic', 'filter'),
    sticker_border: cs('.p5-btn--sticker', 'borderTopWidth'),
    stroke_w: rect('.p5-btn--stroke'),
    keygroup_display: cs('.p5-keygroup__item', 'display'),
    countdown_clip: cs('.p5-countdown', 'clipPath'),
    key_border: cs('.p5-key', 'borderTopWidth'),
    texband_h: cs('.p5-texband', 'height'),
    poster_stroke: cs('.p5-title--poster', 'webkitTextStrokeWidth'),
  }
}

const EXPECT = {
  cloud_border: '3px',
  banner_clip: (v) => v && v.includes('polygon'),
  relic_filter: (v) => v && v !== 'none' && v.includes('drop-shadow'),
  sticker_border: '3px',
  stroke_w: 56,
  keygroup_display: 'flex',
  countdown_clip: (v) => v && v.includes('polygon'),
  key_border: '2px',
  texband_h: '14px',
  poster_stroke: '2px',
}

function check(theme, data) {
  const problems = []
  if (theme === 'p5') {
    for (const [k, exp] of Object.entries(EXPECT)) {
      const v = data[k]
      const ok = typeof exp === 'function' ? exp(v) : v === exp
      if (!ok) problems.push(`P5 缺失签名 ${k}=${JSON.stringify(v)} (期望 ${exp})`)
    }
  } else {
    // 非 P5：这些类必须完全不生效（FC-05 零泄漏）
    const leakChecks = [
      ['cloud_border', data.cloud_border, '3px'],
      ['banner_clip', data.banner_clip, 'polygon'],
      ['relic_filter', data.relic_filter, 'drop-shadow'],
      ['sticker_border', data.sticker_border, '3px'],
      ['countdown_clip', data.countdown_clip, 'polygon'],
      ['key_border', data.key_border, '2px'],
      ['poster_stroke', data.poster_stroke, '2px'],
    ]
    for (const [k, v, sig] of leakChecks) {
      if (v && String(v).includes(sig)) problems.push(`${theme} 泄漏 ${k}=${JSON.stringify(v)} (含 P5 签名 ${sig})`)
    }
  }
  return problems
}

const run = async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] })
  const results = {}
  let allProblems = []
  for (const theme of THEMES) {
    const page = await browser.newPage()
    await page.addInitScript((arg) => { localStorage.setItem(arg.k, arg.v) }, { k: PRESET_KEY, v: theme })
    await page.goto(BASE, { waitUntil: 'networkidle' })
    await page.waitForTimeout(400)
    await page.evaluate(INJECT)
    const data = await page.evaluate(READ)
    results[theme] = data
    const probs = check(theme, data)
    allProblems = allProblems.concat(probs.map((p) => `[${theme}] ${p}`))
    await page.close()
  }

  // p5ft 门控测试：在 p5 下关闭 p5ft-dialog，确认对话框不再生效
  {
    const page = await browser.newPage()
    await page.addInitScript((arg) => { localStorage.setItem(arg.k, arg.v) }, { k: PRESET_KEY, v: 'p5' })
    await page.goto(BASE, { waitUntil: 'networkidle' })
    await page.waitForTimeout(400)
    // 关闭 dialog 家族
    await page.evaluate(() => document.documentElement.classList.remove('p5ft-dialog'))
    await page.evaluate(INJECT)
    const data = await page.evaluate(READ)
    if (data.cloud_border === '3px') allProblems.push('[p5ft-gate] 关闭 p5ft-dialog 后对话框仍生效（门控失效）')
    // 重新开启
    await page.evaluate(() => document.documentElement.classList.add('p5ft-dialog'))
    await page.evaluate(INJECT)
    const data2 = await page.evaluate(READ)
    if (data2.cloud_border !== '3px') allProblems.push('[p5ft-gate] 重新开启 p5ft-dialog 后对话框未恢复')
    await page.close()
  }

  await browser.close()

  // 截图 P5 样例（供肉眼验收）
  const browser2 = await chromium.launch({ args: ['--no-sandbox'] })
  const page = await browser2.newPage()
  await page.addInitScript((arg) => { localStorage.setItem(arg.k, arg.v) }, { k: PRESET_KEY, v: 'p5' })
  await page.setViewportSize({ width: 900, height: 700 })
  await page.goto(BASE, { waitUntil: 'networkidle' })
  await page.waitForTimeout(400)
  await page.evaluate(INJECT)
  await page.evaluate(() => { document.getElementById('p5-fixture').style.cssText = 'position:fixed;left:16px;top:16px;z-index:9999;background:#000;padding:16px;' })
  await page.waitForTimeout(200)
  await page.screenshot({ path: 'tests/recordings/p5_reinforce_fixture.png' })
  await browser2.close()

  const out = { results, problems: allProblems }
  console.log(JSON.stringify(out, null, 2))
  if (allProblems.length) {
    console.error(`\n❌ 失败 ${allProblems.length} 项`)
    process.exit(1)
  }
  console.log('\n✅ 全部通过：P5 7 家族渲染 + 零泄漏 + p5ft 门控 OK')
  process.exit(0)
}
run().catch((e) => { console.error(e); process.exit(1) })
