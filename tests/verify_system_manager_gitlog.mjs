// tests/verify_system_manager_gitlog.mjs
// 验收：服务与版本管理器（/system）新增「提交记录」页签
//   - 页签「提交记录」存在
//   - 点击后调用 GET /api/v1/system/git-log，提交记录表格渲染 ≥1 行
//   - 首行短哈希符合 /^[0-9a-f]{7}$/（Git 短哈希）
//   - 首行作者 / 日期 / 提交说明均非空
//
// 退出码：0=PASS / 1=FAIL（fail-closed）

import path from 'node:path'
import { chromium } from 'playwright'

const ROOT = 'D:/AI/manju_agent'
const URL = 'http://127.0.0.1:8099/system'
const SHOTS_DIR = path.join(ROOT, 'tests/recordings')

let failures = 0
const log = (...args) => console.log(...args)
const fail = (msg) => { failures++; log('  ✗ FAIL:', msg) }
const pass = (msg) => log('  ✓ PASS:', msg)

const browser = await chromium.launch({
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const page = await ctx.newPage()
page.on('console', (m) => { if (m.type() === 'error') log('  [console.error]', m.text()) })

try {
  log('========== 服务与版本管理器 · 提交记录页签验收 ==========')
  await page.goto(URL, { waitUntil: 'load' })

  // 1) 页签存在
  const tabItem = page.locator('.el-tabs__item', { hasText: '提交记录' })
  await tabItem.waitFor({ timeout: 8000 })
  pass('页签「提交记录」已渲染')

  // 2) 点击页签 → 触发加载
  await tabItem.click()
  log('  已点击「提交记录」页签，等待提交记录表格…')

  // 提交记录表格独有特征：首列用 <code class="mono"> 渲染短哈希。
  // 其它两张表（服务 / 模块）没有这个 class，故可用它把行唯一定位到
  // 「提交记录」页签（避免命中另两个页签隐藏的 .el-table__row）。
  const ghRows = page.locator('.el-table__row').filter({ has: page.locator('code.mono') })
  let rowsAppeared = false
  try {
    await ghRows.first().waitFor({ state: 'visible', timeout: 20000 })
    rowsAppeared = true
  } catch {
    const errAlert = page.locator('.el-alert--error')
    if (await errAlert.count()) {
      const t = await errAlert.first().innerText().catch(() => '')
      fail('提交记录加载出错：' + t.replace(/\s+/g, ' ').slice(0, 160))
    } else {
      fail('点击「提交记录」后 20s 内未出现提交记录表格行')
    }
  }

  if (rowsAppeared) {
    const n = await ghRows.count()
    if (n < 1) {
      fail('提交记录表格行数为 0')
    } else {
      pass(`提交记录表格渲染 ${n} 行`)

      const first = ghRows.first()
      const cells = first.locator('.cell')
      const shortHash = (await cells.nth(0).innerText()).trim()
      const author = (await cells.nth(1).innerText()).trim()
      const date = (await cells.nth(2).innerText()).trim()
      const msg = (await cells.nth(3).innerText()).trim()

      if (/^[0-9a-f]{7}$/.test(shortHash)) pass('首行短哈希格式正确: ' + shortHash)
      else fail('首行短哈希格式异常: ' + JSON.stringify(shortHash))

      if (author) pass('首行作者非空: ' + author)
      else fail('首行作者为空')

      if (date) pass('首行日期非空: ' + date)
      else fail('首行日期为空')

      if (msg) pass('首行提交说明非空: ' + msg)
      else fail('首行提交说明为空')
    }
  }

  // 证据截图
  try {
    await page.screenshot({ path: path.join(SHOTS_DIR, 'system_manager_gitlog.png'), fullPage: false })
    log('  证据截图:', path.join(SHOTS_DIR, 'system_manager_gitlog.png'))
  } catch (e) {
    log('  [warn] 截图失败:', e.message)
  }
} catch (e) {
  fail('测试异常: ' + e.message)
} finally {
  await browser.close()
}

if (failures > 0) {
  log(`\n========== 验收失败：${failures} 项 ==========`)
  process.exit(1)
}
log('\n========== 验收通过：提交记录页签 OK ==========')
process.exit(0)
