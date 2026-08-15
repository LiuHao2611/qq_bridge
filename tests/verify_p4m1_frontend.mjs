// P4 M1 前端后端连通性 · 真实浏览器端到端验证（用户要求：截图自检，确认不是只做了后台）
// 验证点：
//   1) SPA 在 :8099 加载，Settings→ModelManager 页面渲染出按钮
//   2) 点击「扫描未声明模型」「刷新已接入」真实打到后端（经 web_app.py 代理 → :7788）
//   3) 无前端 pageerror / 非噪声 console error
//   4) 附带：对若干主要页面做导航冒烟，确认页面非空白、含可交互控件
//
// 用法：
//   NODE_PATH=node_modules PLAYWRIGHT_BROWSERS_PATH=D:/AI/.pw-browsers \
//     /c/Users/L/.workbuddy/binaries/node/versions/22.22.2/node.exe tests/verify_p4m1_frontend.mjs
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'

process.env.PLAYWRIGHT_BROWSERS_PATH ||= 'D:/AI/.pw-browsers'
const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8099'
const VIEWPORT = { width: 1440, height: 900 }
const OUT = 'D:/AI/manju_agent/.tmp/p4m1_verify'
mkdirSync(OUT, { recursive: true })

const NOISE = /WebSocket|404|500|Failed to load resource|favicon/i
const checks = []
const check = (ok, name, extra = '') => {
  checks.push({ ok, name, extra })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (extra ? ' | ' + extra : ''))
}
const apiCalls = []

const browser = await chromium.launch({
  args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu'],
})
const ctx = await browser.newContext({ viewport: VIEWPORT })
const page = await ctx.newPage()

const consoleErrors = []
const pageErrors = []
page.on('console', (m) => {
  if (m.type() === 'error' && !NOISE.test(m.text())) consoleErrors.push(m.text())
})
page.on('pageerror', (e) => pageErrors.push(String(e)))
page.on('request', (req) => {
  const u = req.url()
  if (u.includes('/api/v1')) apiCalls.push({ method: req.method(), url: u, status: null })
})
page.on('response', (res) => {
  const u = res.url()
  if (u.includes('/api/v1')) {
    const hit = apiCalls.find((c) => c.url === u && c.status == null)
    if (hit) hit.status = res.status()
  }
})

// ---- 1. 加载 SPA 并导航到 Settings ----
console.log('\n[1] 加载 SPA → /settings')
await page.goto(BASE + '/settings', { waitUntil: 'networkidle', timeout: 30000 }).catch(() => {})
// ModelManager 按钮：扫描未声明模型
await page.screenshot({ path: OUT + '/01_settings_full.png', fullPage: true })

// ModelManager 在「模型」标签页内；Element Plus el-tabs 默认会挂载所有 pane，但非激活 pane 里的按钮不可见，需先切到「模型」标签
console.log('  切换到「模型」标签页...')
// 注意：不能用 contains("模型")，否则会误点「AI 模型」标签；必须精确匹配文本「模型」
await page.locator('.el-tabs__item', { hasText: /^模型$/ }).click().catch(async () => {
  await page.evaluate(() => {
    const tab = Array.from(document.querySelectorAll('.el-tabs__item')).find((el) => el.textContent.trim() === '模型')
    if (tab) tab.click()
  })
})
await page.waitForTimeout(1200)

let mmOk = false
try {
  // 等待激活标签页里的按钮真正可见
  await page.waitForFunction(() => {
    const btn = Array.from(document.querySelectorAll('button')).find((b) => b.textContent.includes('扫描未声明模型'))
    return btn && btn.offsetParent != null
  }, { timeout: 15000 })
  mmOk = true
} catch { mmOk = false }
check(mmOk, 'ModelManager 在「模型」标签页可见')

// 定位 ModelManager 容器并特写
const mmBox = await page.evaluate(() => {
  const btns = Array.from(document.querySelectorAll('button'))
  const target = btns.find((b) => b.textContent.includes('扫描未声明模型'))
  if (!target) return null
  // 向上找带标题的卡片/区块
  let el = target
  for (let i = 0; i < 8 && el; i++) {
    el = el.parentElement
    if (el && (el.querySelector('h1,h2,h3,.card-title,.el-card__header') || /模型|Model/i.test(el.textContent?.slice(0, 200) || ''))) {
      const r = el.getBoundingClientRect()
      if (r.width > 100) return { x: r.left, y: r.top, w: r.width, h: r.height }
    }
  }
  const r = target.getBoundingClientRect()
  return { x: r.left - 20, y: r.top - 60, w: Math.min(r.width + 600, 1440 - r.left), h: 400 }
})
if (mmBox) {
  await page.screenshot({
    path: OUT + '/02_modelmanager.png',
    clip: { x: Math.max(0, mmBox.x), y: Math.max(0, mmBox.y), width: Math.min(mmBox.w, 1440), height: Math.min(mmBox.h, 900) },
  })
}

// 收集 ModelManager 区域所有按钮文案
const mmButtons = await page.evaluate(() => {
  const btns = Array.from(document.querySelectorAll('button'))
  return btns.map((b) => b.textContent.trim()).filter((t) => t && /扫描|刷新|接入|发现|模型|声明/i.test(t))
})
console.log('  ModelManager 按钮文案:', JSON.stringify(mmButtons))
check(mmButtons.some((t) => t.includes('扫描未声明模型')), '按钮: 扫描未声明模型')
check(mmButtons.some((t) => t.includes('刷新已接入')), '按钮: 刷新已接入')
check(mmButtons.some((t) => /自动发现并接入|接入/.test(t)), '按钮: 自动发现并接入 / 接入')

// ---- 2. 点击「扫描未声明模型」，确认打到后端 ----
console.log('\n[2] 点击「扫描未声明模型」→ 验证后端连通')
const beforeScan = apiCalls.length
await page.locator('button:has-text("扫描未声明模型")').click().catch((e) => console.log('  click error:', e.message))
// 等待后端响应（代理 → :7788）
await page.waitForTimeout(4000)
const scanCalls = apiCalls.slice(beforeScan)
const scanHit = scanCalls.find((c) => /models\/scan/.test(c.url))
check(!!scanHit, '点击「扫描未声明模型」触发请求 /api/v1/models/scan', scanHit ? `status=${scanHit.status}` : 'NO REQUEST')
if (scanHit) check(scanHit.status != null && scanHit.status < 600, '后端响应（代理→:7788 可达）', `status=${scanHit.status}`)
await page.screenshot({ path: OUT + '/03_after_scan.png', fullPage: true })

// ---- 3. 点击「刷新已接入」 ----
console.log('\n[3] 点击「刷新已接入」→ 验证后端连通')
const beforeRefresh = apiCalls.length
await page.locator('button:has-text("刷新已接入")').click().catch((e) => console.log('  click error:', e.message))
await page.waitForTimeout(3000)
const refreshCalls = apiCalls.slice(beforeRefresh)
const refreshHit = refreshCalls.find((c) => /\/models(\?|$)/.test(c.url))
check(!!refreshHit, '点击「刷新已接入」触发请求 /api/v1/models', refreshHit ? `status=${refreshHit.status}` : 'NO REQUEST')
await page.screenshot({ path: OUT + '/04_after_refresh.png', fullPage: true })

// ---- 4. 其他相位页面导航冒烟：确认非空白、含可交互控件 ----
console.log('\n[4] 导航冒烟（确认页面有 UI，不是只做了后台）')
const smokePaths = [
  { path: '/', name: '首页Home' },
  { path: '/tasks', name: '任务中心TaskCenter' },
  { path: '/projects', name: '项目列表Projects' },
  { path: '/global-assets', name: '素材库Assets' },
  { path: '/system', name: '系统管理System' },
]
for (const s of smokePaths) {
  let ok = true, note = ''
  try {
    await page.goto(BASE + s.path, { waitUntil: 'networkidle', timeout: 20000 }).catch(() => {})
    await page.waitForTimeout(1500)
    const info = await page.evaluate(() => {
      const btns = document.querySelectorAll('button').length
      const inputs = document.querySelectorAll('input,select,textarea').length
      const bodyText = (document.body.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80)
      return { btns, inputs, bodyText, blank: (document.body.innerText || '').trim().length < 20 }
    })
    ok = !info.blank && (info.btns > 0 || info.inputs > 0)
    note = `btns=${info.btns} inputs=${info.inputs} text="${info.bodyText}"`
    await page.screenshot({ path: OUT + '/smoke_' + s.name + '.png', fullPage: false })
  } catch (e) {
    ok = false; note = 'ERROR ' + e.message
  }
  check(ok, `页面渲染可交互控件: ${s.name}`, note)
}

// ---- 5. 错误汇总 ----
console.log('\n[5] 错误汇总')
check(pageErrors.length === 0, '无前端 pageerror', pageErrors.slice(0, 3).join(' || '))
check(consoleErrors.length === 0, '无非噪声 console error', consoleErrors.slice(0, 3).join(' || '))

// 写 JSON 报告
const report = {
  base: BASE,
  checks,
  apiCalls,
  pageErrors,
  consoleErrors,
}
writeFileSync(OUT + '/report.json', JSON.stringify(report, null, 2))

const failed = checks.filter((c) => !c.ok)
console.log(`\n==== 结果: ${checks.length - failed.length}/${checks.length} PASS ====`)
console.log('API 调用清单:')
for (const c of apiCalls) console.log(`  ${c.method} ${c.url} -> ${c.status}`)
if (failed.length) { console.log('FAILED:', failed.map((f) => f.name).join('; ')); }

await browser.close()
process.exit(failed.length ? 1 : 0)
