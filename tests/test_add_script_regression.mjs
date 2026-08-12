// 针对性回归测试：覆盖「添加剧本」弹窗（Assets.vue 提取资产弹窗）触发路径
// 之前 bug: Assets.vue:1050 用 props.project?.id 但未 defineProps → undefined.project 崩溃
// 本测试：导航到 /projects/<id>/assets → 点"📝 从剧本一键提取资产"按钮 → 监听 pageerror
import { chromium } from 'playwright'
import path from 'path'
import fs from 'fs'

const WEB = 'http://127.0.0.1:8099'
const API = 'http://127.0.0.1:7788/api/v1'

// 强制 D 盘 tmp — 禁止 chromium 把 userDataDir 写 C:\Users\...\AppData\Local\Temp
const TMP_BASE = 'D:/AI/manju_agent/.tmp'
fs.mkdirSync(path.join(TMP_BASE, 'playwright'), { recursive: true })
const userDataDir = path.join(TMP_BASE, 'playwright', `regression_${Date.now()}_${process.pid}`)
fs.mkdirSync(userDataDir, { recursive: true })
process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(TMP_BASE, 'playwright-browsers')
process.env.TMPDIR = TMP_BASE
process.env.TEMP = TMP_BASE
process.env.TMP = TMP_BASE

let PASS = 0, FAIL = 0
function ok(name){ PASS++; console.log('PASS  '+name) }
function bad(name, e){ FAIL++; console.log('FAIL  '+name+' :: '+(e?.message||e)) }

async function api(path, opts={}) {
  const r = await fetch(API+path, opts)
  const b = await r.json().catch(()=>null)
  if (!r.ok) throw new Error('HTTP '+r.status+' '+(b?.message||''))
  if (b && 'code' in b) {
    if (b.code !== 0) throw new Error('code='+b.code+' '+(b.message||''))
    return b.data
  }
  return b
}

async function main() {
  // 1) 创建测试项目
  let pid
  try {
    const p = await api('/projects', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ name:'回归_添加剧本_'+Date.now(), resolution:'720p', ratio:'16:9' })
    })
    pid = p.project?.project_id
  } catch(e) { bad('创建测试项目', e); return }
  if (!pid) { bad('创建测试项目:无 pid', new Error('no pid')); return }
  ok('创建测试项目 pid=' + pid)

  // 2) 启动浏览器（强制 D 盘 userDataDir）
  const browser = await chromium.launch({ env: process.env })
  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  const errors = []
  const consoleErrors = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()) })

  try {
    // 3) 导航到 /projects/<id>/assets (原 bug 触发页面)
    await page.goto(WEB + '/projects/' + pid + '/assets', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    let url = page.url()
    if (/\/assets$/.test(url)) ok('进入项目 assets 页面')
    else bad('导航到 assets', new Error('实际=' + url))

    // 4) 验证页面渲染（本集/全集 tab 可见）
    const tabsVisible = await page.locator('.tab').count()
    if (tabsVisible > 0) ok('页面本集/全集 tab 渲染 OK（tab 数=' + tabsVisible + '）')
    else bad('页面渲染', new Error('未找到 .tab'))

    // 5) 关键步骤：点击「📝 从剧本提取」按钮（触发原 bug 的路径）
    const addBtn = page.locator('button.import-btn:has-text("从剧本提取")').first()
    const hasBtn = await addBtn.count()
    if (hasBtn > 0) {
      ok('找到「添加剧本」按钮')
      await addBtn.click()
      await page.waitForTimeout(800)

      // 6) 验证弹窗打开
      const modalOpen = await page.locator('.modal-box.wide.extract-box, .extract-box').count()
      if (modalOpen > 0) ok('提取弹窗打开')
      else bad('提取弹窗打开', new Error('未找到 .extract-box'))

      // 7) 验证弹窗内 HistoryPanel 渲染（之前 props.project 崩的地方）
      const histTab = await page.locator('.hist-tab').count()
      if (histTab > 0) ok('HistoryPanel（左侧历史栏）渲染 OK')
      else bad('HistoryPanel 渲染', new Error('未找到 .hist-tab'))

      // 8) 核心断言：整个流程无 JS 运行时错误
      if (errors.length === 0) ok('全程无 pageerror')
      else bad('有 pageerror', new Error(errors.slice(0,3).join(' | ')))

      if (consoleErrors.length === 0) ok('全程无 console.error')
      else bad('有 console.error', new Error(consoleErrors.slice(0,3).join(' | ')))

      // 9) 关闭弹窗
      await page.keyboard.press('Escape').catch(() => {})
      await page.waitForTimeout(300)
    } else {
      bad('找添加剧本按钮', new Error('未找到按钮'))
    }

    // 10) 多次在 modal 内切换步骤（来源/确认）验证无崩溃
    await addBtn.click().catch(() => {})
    await page.waitForTimeout(500)
    // 试试粘贴剧本源 + 切回项目源
    const pasteOpt = page.locator('.extract-option:has-text("粘贴")').first()
    if (await pasteOpt.count() > 0) {
      await pasteOpt.click()
      await page.waitForTimeout(300)
      ok('切换到「粘贴剧本」源 OK')
      const projOpt = page.locator('.extract-option:has-text("使用项目")').first()
      if (await projOpt.count() > 0) {
        await projOpt.click()
        await page.waitForTimeout(300)
        ok('切回「使用项目」源 OK')
      }
    }

    // 11) 最后整页多次切换 hash 路由，确认无残留错误
    await page.goto(WEB + '/projects/' + pid + '/assets', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(500)
    await page.goto(WEB + '/projects/' + pid + '/planning', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(500)
    await page.goto(WEB + '/projects/' + pid + '/assets', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(500)

    // 12) 终检
    if (errors.length === 0) ok('多路由切换后也无 pageerror')
    else bad('路由切换后报错', new Error(errors.slice(0,3).join(' | ')))

  } catch(e) { bad('主流程', e) }
  finally { await browser.close() }

  console.log('\n============ 添加剧本专项回归 ============')
  console.log(`合计: ${PASS} 通过 / ${FAIL} 失败`)
  process.exit(FAIL > 0 ? 1 : 0)
}

main().catch(e => { console.log('FATAL '+e.message); process.exit(2) })
