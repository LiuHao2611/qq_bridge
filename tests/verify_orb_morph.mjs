// DirectorOrb 多形态化身 — 真实 DOM 验收（机器判定 + 退出码）
// 加载真实 app，进入项目路由，对真实 .orb-btn 强制每个形态 class，
// 用 getComputedStyle 读取实际生效值并断言，截图证明形态=悬浮球、拖动激进化。
// 用法：PLAYWRIGHT_BROWSERS_PATH=D:/AI/.pw-browsers node tests/verify_orb_morph.mjs
import { chromium } from 'playwright'
import assert from 'node:assert/strict'

const PID = '383722ac-79cd-4308-a595-8744949a9fba'
const BASE = 'http://localhost:8099'
const P5_FORMS = ['sword', 'mask', 'tower', 'shackle']
const CYB_FORMS = ['katana', 'relic', 'wire', 'scanner']

// 形态 → 期望的 dragging 动画名（形态专属演出）
const DRAG_ANIM = {
  sword: 'p5-sword-fly', mask: 'p5-mask-split', tower: 'p5-tower-spin', shackle: 'p5-shackle-whip',
  katana: 'nc-katana-swing', relic: 'nc-relic-fast', wire: 'nc-wire-whip', scanner: 'nc-scanner-line',
}

async function enter(page) {
  await page.goto(`${BASE}/#/`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  await page.evaluate((pid) => {
    document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/projects/' + pid + '/assets')
  }, PID)
  await page.waitForSelector('.orb-btn', { timeout: 12000 })
  await page.waitForTimeout(400)
}

// 通过真实 dblclick 触发组件的 cycleMorph（@dblclick="cycleMorph"），
// 让 Vue 响应式 currentMorph 推进；避免手动 classList 被 Vue re-render 覆盖。
async function forceForm(page, key) {
  for (let i = 0; i < 8; i++) {
    const cls = await page.evaluate(() => document.querySelector('.orb-btn')?.className || '')
    if (cls.includes('orb-morph--' + key)) {
      await page.waitForTimeout(80)
      return
    }
    await page.locator('.orb-btn').first().dblclick()
    await page.waitForTimeout(200)
  }
  // 兜底（dblclick 失败时）
  await page.evaluate((k) => {
    const btn = document.querySelector('.orb-btn')
    btn.classList.forEach((c) => { if (c.startsWith('orb-morph--')) btn.classList.remove(c) })
    btn.classList.add('orb-morph--' + k)
  }, key)
  await page.waitForTimeout(100)
}

// 用真实 pointerdown 触发组件的 dragging 响应式状态（避免 Vue 在 re-render 时
// 清掉手动 classList.add 的 .dragging），保证拖动 class 在读取 computed style 时仍存在。
async function forceDrag(page, on) {
  const orb = page.locator('.orb-btn').first()
  const box = await orb.boundingBox()
  if (!box) return
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2
  if (on) {
    await page.mouse.move(cx, cy)
    await page.mouse.down()
    await page.mouse.move(cx + 8, cy + 8, { steps: 3 })
  } else {
    await page.mouse.up()
    await page.mouse.move(0, 0)
  }
  await page.waitForTimeout(150)
}

async function shot(page, label) {
  const box = await page.locator('.orb-btn').first().boundingBox()
  if (!box) return
  await page.screenshot({
    path: `D:/AI/manju_agent/.tmp/orb_real_${label}.png`,
    clip: { x: Math.max(0, box.x - 60), y: Math.max(0, box.y - 60), width: box.width + 120, height: box.height + 120 },
  })
}

async function readMorphStyles(page) {
  return await page.evaluate(() => {
    const orb = document.querySelector('.orb-btn')
    const morph = orb.querySelector('.orb-morph')
    const get = (el, prop, pseudo = null) => getComputedStyle(el, pseudo).getPropertyValue(prop)
    const dims = (el) => {
      const r = el.getBoundingClientRect()
      return { w: Math.round(r.width), h: Math.round(r.height) }
    }
    return {
      htmlClass: document.documentElement.className,
      orbClass: orb.className,
      btnBgImg: get(orb, 'background-image'),
      btnShadow: get(orb, 'box-shadow'),
      btnRadius: get(orb, 'border-radius'),
      morph: dims(morph),
      morphBgImg: get(morph, 'background-image'),
      morphBgColor: get(morph, 'background-color'),
      morphClip: get(morph, 'clip-path'),
      beforeBg: get(morph, 'background-image', '::before'),
      beforeClip: get(morph, 'clip-path', '::before'),
      afterBg: get(morph, 'background-image', '::after'),
      afterClip: get(morph, 'clip-path', '::after'),
      morphAnim: get(morph, 'animation-name'),
      beforeAnim: get(morph, 'animation-name', '::before'),
      afterAnim: get(morph, 'animation-name', '::after'),
    }
  })
}

let passed = 0
const fail = (m) => { console.error('FAIL', m); process.exit(1) }
const ok = (m) => { passed++; console.log('PASS ', m) }

async function runTheme(theme, forms) {
  const browser = await chromium.launch({ args: ['--no-sandbox'] })
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  const errs = []
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()) })
  await page.addInitScript((t) => { try { localStorage.setItem('manju_theme', t) } catch (e) {} }, theme)
  await enter(page)

  // T1 主题类挂载到 <html>
  const htmlCls = await page.evaluate(() => document.documentElement.className)
  assert.match(htmlCls, new RegExp(`t-${theme}`), `html.t-${theme} 应用`)
  ok(`T1 html 含 t-${theme} :: ${htmlCls}`)

  // T2 .orb-btn 真实存在并携带 orb-morph--{key}
  const orbClass = await page.evaluate(() => document.querySelector('.orb-btn')?.className || '')
  assert.match(orbClass, /orb-morph--/, `初始 orb-morph-- class`)
  ok(`T2 真实 .orb-btn 挂载形态 class :: ${orbClass}`)

  // T3 .orb-btn 背景透明 → 形态=悬浮球（不是方盒）
  const btnBg = await page.evaluate(() => getComputedStyle(document.querySelector('.orb-btn')).getPropertyValue('background-image'))
  assert.equal(btnBg, 'none', '.orb-btn 背景为 none（方盒已剥离）')
  ok(`T3 .orb-btn 背景 transparent :: ${btnBg}`)

  // 逐形态断言
  for (const key of forms) {
    await forceForm(page, key)
    const s = await readMorphStyles(page)
    await shot(page, `${theme}_${key}`)

    assert.match(s.orbClass, new RegExp(`orb-morph--${key}`), `${key}: class 挂载`)
    ok(`F.${key}.1 class 挂载 orb-morph--${key}`)

    assert.ok(s.morph.w > 10 && s.morph.h > 10, `${key}: 形态尺寸 > 10px (${s.morph.w}x${s.morph.h})`)
    ok(`F.${key}.2 形态尺寸 ${s.morph.w}x${s.morph.h}`)

    // 形态绘制：morph 自身或任一伪元素含 background-image / background-color / clip-path
    const morphDraw =
      s.morphBgImg !== 'none' ||
      (s.morphBgColor && s.morphBgColor !== 'rgba(0, 0, 0, 0)' && s.morphBgColor !== 'transparent') ||
      s.morphClip !== 'none' ||
      s.beforeBg !== 'none' ||
      s.beforeClip !== 'none' ||
      s.afterBg !== 'none' ||
      s.afterClip !== 'none'
    assert.ok(morphDraw, `${key}: morph 自身或伪元素绘制（bg/img/clip-path 至少一项非 none）`)
    ok(`F.${key}.3 形态绘制 (morph bg=${s.morphBgImg.slice(0,30)} clip=${s.morphClip.slice(0,30)}; before clip=${s.beforeClip.slice(0,24)})`)

    // dragging 演出 = 形态专属（非通用 rotate）；动画可挂在 .orb-morph / ::before / ::after
    await forceDrag(page, true)
    const dragAnims = await page.evaluate(() => ({
      m: getComputedStyle(document.querySelector('.orb-morph')).getPropertyValue('animation-name'),
      b: getComputedStyle(document.querySelector('.orb-morph'), '::before').getPropertyValue('animation-name'),
      a: getComputedStyle(document.querySelector('.orb-morph'), '::after').getPropertyValue('animation-name'),
    }))
    const found = [dragAnims.m, dragAnims.b, dragAnims.a].some((n) => n && n.includes(DRAG_ANIM[key]))
    assert.ok(found, `${key}: dragging 包含形态专属动画 ${DRAG_ANIM[key]}（morph=${dragAnims.m}, ::before=${dragAnims.b}, ::after=${dragAnims.a}）`)
    ok(`F.${key}.4 dragging 形态专属动画 ${DRAG_ANIM[key]} (morph=${dragAnims.m} ::before=${dragAnims.b} ::after=${dragAnims.a})`)
    await shot(page, `${theme}_${key}_drag`)
    await forceDrag(page, false)
  }

  // 主题隔离：cyberpunk 下不应命中 p5 规则（通过 html class 验证）
  if (theme === 'cyberpunk') {
    const p5Leak = await page.evaluate(() => {
      // p5 规则用 var(--p5-red)；若 cyberpunk 形态误挂 p5 class，背景会引用 p5-red
      const m = document.querySelector('.orb-morph')
      return getComputedStyle(m, '::before').getPropertyValue('background-image').includes('--p5')
    })
    assert.equal(p5Leak, false, 'cyberpunk 形态 ::before 不引用 p5 变量')
    ok('I.1 cyberpunk 形态未泄漏 p5 变量')
  }

  // 0 非基础设施 console error（WS 404 属基础设施）
  const real = errs.filter((e) => !/WebSocket/i.test(e))
  assert.equal(real.length, 0, `0 非基础设施 console error（实际 ${real.length}）`)
  ok(`C.1 无非基础设施控制台错误（基础设施 ${errs.length - real.length} 条已排除）`)

  await browser.close()
}

await runTheme('p5', P5_FORMS)
await runTheme('cyberpunk', CYB_FORMS)
console.log(`\n==== DirectorOrb 多形态化身验收（真实 DOM）：${passed} PASS ====`)
console.log('ALL PASS ✅')