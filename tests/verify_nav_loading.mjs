// 前端 E2E 验证：项目中心/打开项目加载动画 + 5 阶段双向折角导航（Request B 收口）
// 约定（参照 tests/verify_*.mjs）：独立 .mjs，直接 `node` 运行，非 @playwright/test spec。
//
// 验证内容：
//   1) 项目中心首次加载出现 .pl-loading，随后渲染卡片
//   2) 打开项目出现 .entry-loading，落到某 phase 路由且 book-shell 出现
//   3) 5 个阶段页（planning/editor/production/canvas/assets）折角按钮双向导航，
//      每步 .main 非空 + 有 shell（无黑屏）；终点(canvas)的"下一页"=合成触发(不跳转，预期)，
//      起点(assets)的"上一页"=退回项目中心(预期边界)
//   4) P5 默认主题 + gray 非 P5 主题各跑一遍（证明全主题无关）
//   5) 无前端 pageerror / console error（已知后端 WS404 / orb500 噪声除外）
//
// 前置：
//   - 前端服务运行于 BASE（默认 http://localhost:8099，可用 E2E_BASE 覆盖）
//   - 项目列表至少有 1 个项目（运行时点第一个 .pl-card，不硬编码 ID）
//   - 浏览器：默认读 PLAYWRIGHT_BROWSERS_PATH；本项目装在 D:/AI/.pw-browsers
//
// 用法：
//   node tests/verify_nav_loading.mjs
//   E2E_BASE=http://127.0.0.1:8099 PLAYWRIGHT_BROWSERS_PATH=D:/AI/.pw-browsers node tests/verify_nav_loading.mjs
//   npm run test:e2e:nav
import { chromium } from 'playwright'

// 本项目浏览器装在 D:/AI/.pw-browsers（非默认位置）；仅当未设置时兜底，默认安装位置的机器不受影响。
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
  if (theme) await page.addInitScript(t => { try { localStorage.setItem('manju_theme', t) } catch (e) {} }, theme)
  else await page.addInitScript(() => { try { localStorage.removeItem('manju_theme') } catch (e) {} })
  return { ctx, page, errs }
}

async function state(page) {
  return await page.evaluate(() => ({
    route: location.pathname,
    mainEmpty: (document.querySelector('.main')?.innerHTML || '').trim().length < 10,
    book: !!document.querySelector('.book-shell'),
    canvas: !!document.querySelector('.canvas-shell'),
  }))
}

// 阶段遍历：next 到尽头（canvas 末步"下一页"=合成触发，route 不变→预期终点），再 prev 回尽头
// （assets 首步"上一页"=退回项目中心，无 shell→预期边界）。真正 FAIL 仅当跳转后黑屏或落到的阶段页无 shell。
async function traverse(page, label) {
  async function clickThrough(sel, dir) {
    for (let i = 0; i < 8; i++) {
      const present = await page.evaluate(s => !!document.querySelector(s), sel).catch(() => false)
      if (!present) break
      const before = (await state(page)).route
      await page.click(sel)
      // 先等路由真正切换（最多 4s）；终点(合成触发/退回)不切换会超时但被 catch 吞掉
      await page.waitForFunction(b => location.pathname !== b, before, { timeout: 4000 }).catch(() => {})
      const s0 = await state(page)
      const moved = s0.route !== before
      if (!moved) {
        if (/\/projects\/[^/]+\/(planning|assets|editor|production|canvas)/.test(s0.route)) {
          check(true, `${label} ${dir} -> 到达终点(合成触发, route 不变, 预期)`); return true
        }
        check(false, `${label} ${dir} -> 折角点击后 route 未变且非阶段页(异常)`); return false
      }
      // 已移动到新阶段：等该阶段自身 shell 出现（planning 需等 getProject 返回，可能数秒；
      // 必须路由切换后再等，避免旧页面 .book-shell 残留被误判为已加载）
      await page.waitForSelector('.book-shell, .canvas-shell', { timeout: 20000 }).catch(() => {})
      await page.waitForTimeout(200)
      const s = await state(page)
      const isExit = /\/projects\/?$/.test(s.route) && !/\/projects\/[^/]+\/(planning|assets|editor|production|canvas)/.test(s.route)
      if (isExit) { check(true, `${label} ${dir} -> 退回到项目中心(预期边界)`); return true }
      const shelled = s.book || s.canvas
      check(!s.mainEmpty && shelled, `${label} ${dir} -> ${s.route.split('/').pop()} (mainEmpty=${s.mainEmpty}, shell=${shelled})`)
      if (s.mainEmpty || !shelled) return false
    }
    return true
  }
  await clickThrough('.fold-next', 'next')
  await clickThrough('.fold-prev', 'prev')
}

async function runTheme(browser, theme) {
  const tag = theme || 'p5(default)'
  const { ctx, page, errs } = await newPage(browser, theme)

  await page.goto(BASE + '/projects', { waitUntil: 'domcontentloaded' })
  let sawLoading = false
  for (let i = 0; i < 40; i++) {
    if (await page.evaluate(() => !!document.querySelector('.pl-loading'))) { sawLoading = true; break }
    await page.waitForTimeout(100)
  }
  await page.waitForSelector('.pl-card', { timeout: 15000 }).catch(() => {})
  const cards = await page.evaluate(() => document.querySelectorAll('.pl-card').length)
  check(sawLoading, `[${tag}] 项目中心加载动画出现 (.pl-loading)`)
  check(cards > 0, `[${tag}] 项目中心加载后卡片渲染 (cards=${cards})`)

  await page.click('.pl-card')
  let sawEntry = false
  for (let i = 0; i < 40; i++) {
    if (await page.evaluate(() => !!document.querySelector('.entry-loading'))) { sawEntry = true; break }
    await page.waitForTimeout(100)
  }
  check(sawEntry, `[${tag}] 打开项目加载动画出现 (.entry-loading)`)
  await page.waitForSelector('.book-shell, .canvas-shell', { timeout: 20000 }).catch(() => {})
  const land = await state(page)
  check(/\/projects\/[^/]+\/(planning|assets|editor|production|canvas)/.test(land.route), `[${tag}] 打开项目后落到 phase 路由 (${land.route})`)
  check(!land.mainEmpty && (land.book || land.canvas), `[${tag}] 打开后页面非空且 book-shell 出现 (mainEmpty=${land.mainEmpty}, book=${land.book})`)

  await traverse(page, `[${tag}]`)

  check(errs.length === 0, `[${tag}] 无前端 pageerror/console error (count=${errs.length})`)
  if (errs.length) errs.slice(0, 8).forEach(e => console.log('   ' + e))
  await ctx.close()
}

const browser = await chromium.launch({ args: ['--no-sandbox'] })
await runTheme(browser, null)
await runTheme(browser, 'gray')
await browser.close()

const failed = checks.filter(c => !c.ok)
console.log(`\n==== 结果：${checks.length - failed.length} PASS / ${failed.length} FAIL ====`)
if (failed.length) { failed.forEach(f => console.log('  FAIL - ' + f.name + (f.extra ? ' | ' + f.extra : ''))); process.exit(1) }
console.log('ALL PASS'); process.exit(0)
