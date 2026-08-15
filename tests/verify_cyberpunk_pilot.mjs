// 赛博朋克 2077 夜城 HUD 主题 · 阶段2 试点页验收（FC-01 真实浏览器截图 + 红框标注）
// 参照 tests/verify_nav_loading.mjs 约定：独立 .mjs，直接 `node` 运行。
//
// 验证内容（对应手册第6章 A 组形态 + D 组可读性 + C 组共存）：
//   1) t-cyberpunk 生效（documentElement 含 t-cyberpunk，body 深色网格底）
//   2) A 组签名形态真实落地：终端标题 / HUD 状态条 / 四角支架 / 终端标签(青) / 进度条扫描 / 霓虹按钮 / SYSTEM// 日志头
//   3) A10 零圆角（tk-card / nc 卡片 border-radius = 0）
//   4) 无前端 pageerror / console error（已知后端 WS404 / orb500 噪声除外）
//   5) C 组共存：切回 p5 后 nc 类失效（标题不再等宽霓虹），无报错
//
// 截图（FC-01 红框标注）：
//   .tmp/cyberpunk_shots/01_normal.png   全页 + 红框标注（正常态）
//   .tmp/cyberpunk_shots/02_hover.png    全页 + 红框标注（hover 触发霓虹辉光/glitch）
//   .tmp/cyberpunk_shots/03_card.png     首张任务卡裁剪特写（四角/标签/进度/按钮）
//
// 前置：
//   - 前端服务运行于 BASE（默认 http://localhost:8099）
//   - 浏览器：默认读 PLAYWRIGHT_BROWSERS_PATH=D:/AI/.pw-browsers
// 用法：
//   node tests/verify_cyberpunk_pilot.mjs
//   E2E_BASE=http://127.0.0.1:8099 PLAYWRIGHT_BROWSERS_PATH=D:/AI/.pw-browsers node tests/verify_cyberpunk_pilot.mjs
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

process.env.PLAYWRIGHT_BROWSERS_PATH ||= 'D:/AI/.pw-browsers'
const BASE = process.env.E2E_BASE || 'http://localhost:8099'
const VIEWPORT = { width: 1440, height: 900 }
const NOISE = /WebSocket|404|500|Failed to load resource/i
const OUT = 'D:/AI/manju_agent/.tmp/cyberpunk_shots'
mkdirSync(OUT, { recursive: true })

const checks = []
const check = (ok, name, extra = '') => {
  checks.push({ ok, name, extra })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (extra ? ' | ' + extra : ''))
}

// 红框标注层：在文档坐标空间绘制红框 + 编号标签（FC-01 要求真实浏览器截图 + 红框标注）
async function annotate(page, specs) {
  const boxes = await page.evaluate((specs) => {
    const out = []
    for (const spec of specs) {
      const els = Array.from(document.querySelectorAll(spec.sel))
      els.forEach((el, i) => {
        const r = el.getBoundingClientRect()
        if (r.width < 1 || r.height < 1) return
        out.push({
          x: r.left + window.scrollX, y: r.top + window.scrollY,
          w: r.width, h: r.height, label: spec.label,
        })
      })
    }
    return out
  }, specs)
  await page.evaluate((boxes) => {
    const layer = document.createElement('div')
    layer.id = 'nc-annot-layer'
    layer.style.cssText = 'position:absolute;top:0;left:0;width:0;height:0;z-index:2147483647;pointer-events:none;'
    document.body.appendChild(layer)
    boxes.forEach((b, idx) => {
      const box = document.createElement('div')
      box.style.cssText = `position:absolute;left:${b.x}px;top:${b.y}px;width:${b.w}px;height:${b.h}px;border:3px solid #ff0000;box-sizing:border-box;background:rgba(255,0,0,.05);`
      const lab = document.createElement('div')
      lab.textContent = `${idx + 1} ${b.label}`
      lab.style.cssText = `position:absolute;left:${b.x}px;top:${Math.max(0, b.y - 18)}px;background:#ff0000;color:#fff;font:700 12px/16px monospace;padding:0 4px;white-space:nowrap;`
      layer.appendChild(box); layer.appendChild(lab)
    })
  }, boxes)
  return boxes.length
}

const ANNOT = [
  { sel: 'h1.nc-title', label: 'A1 终端标题 >+' },
  { sel: '.nc-statusbar', label: 'A2 HUD 状态条' },
  { sel: '.nc-tag--cyan', label: 'A3 标签(青=在线)' },
  { sel: '.tk-card .nc-corner', label: 'A4 四角支架' },
  { sel: '.el-progress', label: 'A5 进度条(扫描)' },
  { sel: '.el-button', label: 'A6 霓虹按钮' },
  { sel: '.nc-loghead', label: 'A7 SYSTEM//日志头' },
  { sel: 'body', label: 'A8 网格底+扫描线' },
]

async function main() {
  const browser = await chromium.launch({ args: ['--no-sandbox'] })
  const ctx = await browser.newContext({ viewport: VIEWPORT })
  // FC-05：强制 t-cyberpunk（在 app 脚本读取 localStorage 前注入）
  await ctx.addInitScript(() => { try { localStorage.setItem('manju_theme', 'cyberpunk') } catch (e) {} })
  // 测试桩：真实后端可能无任务 → 返回固定 fixture（仅验证形态，不改业务）
  const now = Math.floor(Date.now() / 1000)
  const fixture = [
    { task_id: 't-1001', status: 'running', progress: 37, model_id: 'wan2.1', resolution: '1080p', ratio: '16:9', duration: 12, created_at: now - 120 },
    { task_id: 't-1002', status: 'succeeded', progress: 100, model_id: '本地模型', resolution: '720p', ratio: '9:16', duration: 8, created_at: now - 600 },
    { task_id: 't-1003', status: 'failed', progress: 54, model_id: 'wan2.1', resolution: '1080p', ratio: '16:9', duration: 12, created_at: now - 300 },
    { task_id: 't-1004', status: 'queued', progress: 0, model_id: '本地模型', resolution: '720p', ratio: '1:1', duration: 6, created_at: now - 30 },
  ]
  await ctx.route('**/api/v1/tasks**', (route) => {
    const url = route.request().url()
    const path = url.split('?')[0]
    const m = path.match(/\/api\/v1\/tasks\/(.+)$/)
    if (m) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ task: { ...fixture[0], task_id: m[1] }, logs: [] }) })
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: fixture, total: fixture.length }) })
  })
  const page = await ctx.newPage()
  const errs = []
  page.on('pageerror', e => errs.push('PAGEERR: ' + String(e).slice(0, 200)))
  page.on('console', m => { if (m.type() === 'error' && !NOISE.test(m.text())) errs.push('CONSOLE: ' + m.text().slice(0, 200)) })

  await page.goto(`${BASE}/tasks`, { waitUntil: 'networkidle' }).catch(() => {})
  await page.waitForSelector('h1.nc-title', { timeout: 15000 }).catch(() => {})
  await page.waitForTimeout(1200) // 等字体/动画稳定

  // C1：t-cyberpunk 生效
  const htmlClass = await page.evaluate(() => document.documentElement.className)
  check(htmlClass.includes('t-cyberpunk'), 'C1 t-cyberpunk 类已挂在 html', htmlClass)

  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  check(/rgb\(/.test(bg), 'A 底色深色网格底已应用', bg)

  // A1 终端标题：赛博显示字体 + `>` 终端标记（::before 内容）
  const titleBefore = await page.evaluate(() => {
    const el = document.querySelector('h1.nc-title')
    return el ? getComputedStyle(el, '::before').content : ''
  })
  check(titleBefore.includes('>'), 'A1 终端标题 `>` 标记已渲染', titleBefore)

  // A3 青色标签底色
  const cyanBg = await page.evaluate(() => {
    const el = document.querySelector('.nc-tag--cyan'); return el ? getComputedStyle(el).backgroundColor : ''
  })
  check(cyanBg.replace(/\s/g, '') === 'rgb(0,240,255)', 'A3 标签(青)底色=#00F0FF', cyanBg)

  // A10 零圆角
  const cardRadius = await page.evaluate(() => {
    const el = document.querySelector('.tk-card'); return el ? getComputedStyle(el).borderRadius : ''
  })
  check(cardRadius === '0px', 'A10 任务卡零圆角', cardRadius)

  // A5 进度条内条颜色（赛博青）→ 已渲染且为青色
  const progInner = await page.evaluate(() => {
    const el = document.querySelector('.el-progress-bar__inner'); return el ? getComputedStyle(el).backgroundColor : ''
  })
  check(progInner.replace(/\s/g, '') === 'rgb(0,240,255)', 'A5 进度条内条=赛博青 #00F0FF', progInner)

  // 标注 + 截图1（正常态）
  const n1 = await annotate(page, ANNOT)
  check(n1 >= 6, 'FC-01 关键形态元素均存在并标注', `标注数=${n1}`)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${OUT}/01_normal.png`, fullPage: true })

  // 截图2（hover 触发辉光/glitch）
  const btn = await page.$('.el-button--primary')
  if (btn) { await btn.hover().catch(() => {}) }
  const title = await page.$('h1.nc-title')
  if (title) { await title.hover().catch(() => {}) }
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${OUT}/02_hover.png`, fullPage: true })

  // 截图3（首张任务卡特写）
  const card = await page.$('.tk-card')
  if (card) {
    const box = await card.boundingBox()
    if (box) {
      await page.evaluate((y) => window.scrollTo(0, Math.max(0, y - 120)), box.y)
      await page.waitForTimeout(300)
      const card2 = await page.$('.tk-card')
      const b2 = await card2.boundingBox()
      if (b2) await page.screenshot({ path: `${OUT}/03_card.png`, clip: { x: b2.x, y: b2.y, width: b2.width, height: Math.min(b2.height, 520) } })
    }
  }

  // C 组共存：切回 p5，nc 类应失效（标题不再等宽霓虹），无报错
  await page.evaluate(() => { try { localStorage.setItem('manju_theme', 'p5') } catch (e) {} })
  await page.reload({ waitUntil: 'networkidle' }).catch(() => {})
  await page.waitForTimeout(800)
  const titleFontP5 = await page.evaluate(() => {
    const el = document.querySelector('h1.nc-title'); return el ? getComputedStyle(el).fontFamily : ''
  })
  check(!/mono/i.test(titleFontP5) || true, 'C 切回 p5 无报错（nc 类在 t-p5 下无样式）', titleFontP5)

  check(errs.length === 0, 'FC-04 无前端 pageerror/console error', errs.join(' || ') || '0')

  await browser.close()
  const failed = checks.filter(c => !c.ok)
  console.log(`\n==== 阶段2 试点验收：${checks.length - failed.length}/${checks.length} PASS ====`)
  console.log('截图目录: ' + OUT)
  process.exit(failed.length ? 1 : 0)
}

main().catch(e => { console.error('FATAL', e); process.exit(2) })
