// 前端 E2E 验证：DirectorOrb 多形态化身（皮肤）实现（按《DirectorOrb悬浮球多形态化身增强规格》v2.0）
// 约定（参照 tests/verify_*.mjs）：独立 .mjs，直接 `node` 运行，非 @playwright/test spec。
//
// 验证内容：
//   1) 设置页「悬浮球皮肤」选择器渲染（5 个选项：随机/绅士细剑/怪盗面具/命运塔罗/心锁锁链）
//   2) 点击「怪盗面具」→ localStorage wn_settings.orbSkin === 'mask' 且选中态 on
//   3) 进入项目后悬浮球出现 .orb-morph 层，且 .orb-btn 带 orb-morph--mask（P5 主题下生效）
//   4) 双击悬浮球 → 形态切换（orb-morph--mask → 下一形态 tower），class 随之变化
//   5) 无前端 pageerror / console error（已知后端 WS404 / orb500 噪声除外）
//
// 前置：
//   - 前端服务运行于 BASE（默认 http://localhost:8099）
//   - 项目列表至少有 1 个项目
//   - 浏览器：默认读 PLAYWRIGHT_BROWSERS_PATH；本项目装在 D:/AI/.pw-browsers
//
// 用法：
//   node tests/verify_orb_skin.mjs
//   npm run test:e2e:orb
import { chromium } from 'playwright'

process.env.PLAYWRIGHT_BROWSERS_PATH ||= 'D:/AI/.pw-browsers'
const BASE = process.env.E2E_BASE || 'http://localhost:8099'
const VIEWPORT = { width: 1440, height: 900 }
const NOISE = /WebSocket|404|500|Failed to load resource/i

const checks = []
const check = (ok, name, extra = '') => {
  checks.push({ ok, name, extra })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (extra ? ' | ' + extra : ''))
}

async function newPage(browser, theme) {
  const ctx = await browser.newContext({ viewport: VIEWPORT })
  const page = await ctx.newPage()
  const errs = []
  page.on('pageerror', e => errs.push('PAGEERR: ' + String(e).slice(0, 200)))
  page.on('console', m => { if (m.type() === 'error' && !NOISE.test(m.text())) errs.push('CONSOLE: ' + m.text().slice(0, 200)) })
  // P5 主题（默认皮肤形态只在 P5 下可见）
  await page.addInitScript(t => { try { localStorage.setItem('manju_theme', t) } catch (e) {} }, theme || 'p5')
  return { ctx, page, errs }
}

// 进入第一个项目（不硬编码 ID），落到某 phase 路由且 book-shell/ 悬浮球出现
async function openFirstProject(page) {
  await page.goto(BASE + '/projects', { waitUntil: 'networkidle' })
  await page.waitForSelector('.pl-card', { timeout: 15000 })
  await page.locator('.pl-card').first().click()
  // 项目数据就绪需要后端，等待 book-shell 或 canvas-shell
  await page.waitForSelector('.book-shell, .canvas-shell', { timeout: 20000 })
  await page.waitForSelector('.orb-btn', { timeout: 10000 })
}

async function run() {
  const browser = await chromium.launch({ args: ['--no-sandbox'] })

  // ---- 1) 设置页皮肤选择器 ----
  {
    const { ctx, page, errs } = await newPage(browser, 'p5')
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' })
    const label = await page.getByText('悬浮球皮肤', { exact: true }).first()
    check(!!label, 'settings: 悬浮球皮肤 字段存在')
    const items = await page.locator('.accents .ac-item').count()
    // 设置页有多个 .accents（配色主题 + 悬浮球皮肤），这里取包含「悬浮球皮肤」的那个 field 内的按钮数
    const skinField = page.locator('.field', { hasText: '悬浮球皮肤' })
    const skinCount = await skinField.locator('.ac-item').count()
    check(skinCount === 5, 'settings: 皮肤选项 = 5（随机/剑/面具/塔罗/锁链）', 'got ' + skinCount)
    if (skinCount >= 5) {
      // 点击「怪盗面具」
      const maskBtn = skinField.locator('.ac-item', { hasText: '怪盗面具' })
      await maskBtn.click()
      await page.waitForTimeout(150)
      const stored = await page.evaluate(() => {
        try { return JSON.parse(localStorage.getItem('wn_settings') || '{}').orbSkin } catch (e) { return null }
      })
      check(stored === 'mask', 'settings: 点击面具后 wn_settings.orbSkin === mask', 'got ' + stored)
      const on = await maskBtn.evaluate(el => el.classList.contains('on'))
      check(on, 'settings: 面具按钮选中态 on')
    }
    // 回退到默认（随机），避免影响后续项目进入断言
    const randomBtn = skinField.locator('.ac-item', { hasText: '随机' })
    if (await randomBtn.count()) await randomBtn.click()
    check(errs.length === 0, 'settings: 无前端错误', errs.join(' ; '))
    await ctx.close()
  }

  // ---- 2) 项目内悬浮球形态 + 双击切换 ----
  {
    const { ctx, page, errs } = await newPage(browser, 'p5')
    // 预设皮肤 = 面具（mask）
    await page.addInitScript(() => { try { const s = JSON.parse(localStorage.getItem('wn_settings') || '{}'); s.orbSkin = 'mask'; localStorage.setItem('wn_settings', JSON.stringify(s)) } catch (e) {} })
    await openFirstProject(page)
    const morphExists = await page.locator('.orb-btn .orb-morph').count()
    check(morphExists === 1, 'orb: 项目内出现 .orb-morph 形态层')
    const hasMask = await page.locator('.orb-btn.orb-morph--mask').count()
    check(hasMask === 1, 'orb: .orb-btn 带 orb-morph--mask（P5 面具默认皮肤生效）')
    const coreHidden = await page.evaluate(() => {
      const c = document.querySelector('.orb-btn .orb-core')
      return c ? getComputedStyle(c).display === 'none' : false
    })
    check(coreHidden, 'orb: P5 下胶片图标 .orb-core 隐藏（改显形态）')

    // 双击切换形态：mask(1) → tower(2)
    await page.locator('.orb-btn').dblclick()
    await page.waitForTimeout(200)
    const classes = await page.locator('.orb-btn').getAttribute('class')
    const switchedToTower = /orb-morph--tower/.test(classes || '')
    const stillMask = /orb-morph--mask/.test(classes || '')
    check(switchedToTower && !stillMask, 'orb: 双击后形态切换为 tower（mask→tower）', classes)
    // 切换爆发态应在 ~600ms 内结束（不残留 switching）
    await page.waitForTimeout(700)
    const afterSwitch = await page.locator('.orb-btn').getAttribute('class')
    check(!/switching/.test(afterSwitch || ''), 'orb: 切换爆发态已结束（无残留 switching）')

    check(errs.length === 0, 'orb: 无前端错误', errs.join(' ; '))
    await ctx.close()
  }

  await browser.close()

  const failed = checks.filter(c => !c.ok)
  console.log(`\n==== orb-skin 验证：${checks.length - failed.length}/${checks.length} PASS ====`)
  if (failed.length) { failed.forEach(f => console.log('  FAIL ' + f.name + (f.extra ? ' | ' + f.extra : ''))); process.exit(1) }
  process.exit(0)
}

run().catch(e => { console.error('RUNNER ERROR', e); process.exit(2) })
