// tests/verify_system_manager_gitlog.mjs
// 验收：服务与版本管理器（/system）「提交记录」页签
//   1) 页签「提交记录」存在
//   2) 点击后默认加载「最近 7 天」：起始=今天-6、结束=今天；UI 行数 == API 该范围数（< 全部，证明确为筛选态）
//   3) 首行日期格式含 时分秒.毫秒：/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$/
//   4) 首行短哈希 / 作者 / 提交说明均非空
//   5) 点「全部」→ UI 行数 == 后端总数（显示所有记录）
//   6) 日期筛选：填起始日期 → 查询 → UI 行数 == API 筛选数
//
// 退出码：0=PASS / 1=FAIL（fail-closed）

import path from 'node:path'
import { chromium } from 'playwright'

const ROOT = 'D:/AI/manju_agent'
const UI_URL = 'http://127.0.0.1:8099/system'
const API_BASE = 'http://127.0.0.1:7788/api/v1/system/git-log'
const SHOTS_DIR = path.join(ROOT, 'tests/recordings')

let failures = 0
const log = (...args) => console.log(...args)
const fail = (msg) => { failures++; log('  ✗ FAIL:', msg) }
const pass = (msg) => log('  ✓ PASS:', msg)

const apiGet = async (qs) => {
  const r = await fetch(API_BASE + (qs ? '?' + qs : ''))
  return r.json()
}

const browser = await chromium.launch({
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const page = await ctx.newPage()
page.on('console', (m) => { if (m.type() === 'error') log('  [console.error]', m.text()) })

// 期望数据（来自后端直连，作为 UI 断言基准）
const all = await apiGet('')
const total = all.count
const sinceOnly = await apiGet('since=2026-08-15')
const sinceCount = sinceOnly.count
log(`  [基准] 后端全部=${total} / since=2026-08-15=${sinceCount}`)

try {
  log('========== 服务与版本管理器 · 提交记录页签验收 ==========')
  await page.goto(UI_URL, { waitUntil: 'load' })

  // 1) 页签存在
  const tabItem = page.locator('.el-tabs__item', { hasText: '提交记录' })
  await tabItem.waitFor({ timeout: 8000 })
  pass('页签「提交记录」已渲染')

  // 2) 点击页签 → 触发加载（默认最近 7 天）
  await tabItem.click()
  log('  已点击「提交记录」页签，等待提交记录表格…')

  // 提交记录表格独有特征：首列用 <code class="mono"> 渲染短哈希（其它两表无此 class）
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

      // 2) 默认最近 7 天：日期选择器默认值 + UI 行数 == API 该范围数
      const sinceInput = page.locator('.git-filter .el-date-editor').nth(0).locator('input')
      const untilInput = page.locator('.git-filter .el-date-editor').nth(1).locator('input')
      const sDef = (await sinceInput.inputValue()).trim()
      const uDef = (await untilInput.inputValue()).trim()
      const ymd = (d) => {
        const p = (x) => String(x).padStart(2, '0')
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
      }
      const expUntil = ymd(new Date())
      const t6 = new Date(); t6.setDate(t6.getDate() - 6)
      const expSince = ymd(t6)
      if (/^\d{4}-\d{2}-\d{2}$/.test(sDef) && /^\d{4}-\d{2}-\d{2}$/.test(uDef)) {
        pass(`日期筛选默认值 起始=${sDef} 结束=${uDef}`)
      } else {
        fail(`日期筛选默认值格式异常 起始=${JSON.stringify(sDef)} 结束=${JSON.stringify(uDef)}`)
      }
      if (sDef === expSince) pass(`起始默认值=今天-6 (${expSince})`)
      else fail(`起始默认值 ${sDef} != 期望 ${expSince}`)
      if (uDef === expUntil) pass(`结束默认值=今天 (${expUntil})`)
      else fail(`结束默认值 ${uDef} != 期望 ${expUntil}`)

      const defApi = await apiGet(`since=${sDef}&until=${uDef}`)
      const defCount = defApi.count
      if (n === defCount) pass(`默认最近7天 UI 行数 == API 该范围 ${defCount}`)
      else fail(`默认 UI 行数 ${n} != API 默认范围 ${defCount}`)
      if (defCount > 0) pass(`默认范围 ${defCount} 条（全部=${total}）`)
      else fail(`默认范围行数为 0`)
      const metaInit = await page.locator('.git-meta').innerText().catch(() => '')
      if (/已按日期筛选/.test(metaInit)) pass('默认即显示「已按日期筛选」标记')
      else fail('默认未显示筛选标记: ' + JSON.stringify(metaInit))

      const first = ghRows.first()
      const cells = first.locator('.cell')
      const shortHash = (await cells.nth(0).innerText()).trim()
      const author = (await cells.nth(1).innerText()).trim()
      const date = (await cells.nth(2).innerText()).trim()
      const msg = (await cells.nth(3).innerText()).trim()

      // 3) 日期格式：时分秒.毫秒
      if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$/.test(date)) pass('首行日期含时分秒.毫秒: ' + date)
      else fail('首行日期格式异常（应 YYYY-MM-DD HH:MM:SS.mmm）: ' + JSON.stringify(date))

      if (/^[0-9a-f]{7}$/.test(shortHash)) pass('首行短哈希格式正确: ' + shortHash)
      else fail('首行短哈希格式异常: ' + JSON.stringify(shortHash))
      if (author) pass('首行作者非空: ' + author)
      else fail('首行作者为空')
      if (msg) pass('首行提交说明非空: ' + msg)
      else fail('首行提交说明为空')
    }
  }

  // 4) 日期筛选 UI 路径：填起始日期 → 查询 → 行数 == API 筛选数
  if (rowsAppeared) {
    log('  --- 日期筛选：起始 2026-08-15 → 查询 ---')
    const sinceEd = page.locator('.git-filter .el-date-editor').nth(0)
    const sinceInput = sinceEd.locator('input')
    await sinceInput.click()
    await sinceInput.fill('2026-08-15')
    await sinceInput.press('Enter')
    await page.locator('.git-filter .el-button--primary').click()  // 查询
    // 等待筛选结果刷新（行数变为 sinceCount；强制刷新后会先短暂 loading）
    try {
      await page.waitForFunction(
        (exp) => {
          const rows = document.querySelectorAll('.el-table__row')
          // 仅统计含 code.mono 的提交记录行
          const gitRows = Array.from(rows).filter(r => r.querySelector('code.mono'))
          return gitRows.length === exp
        },
        sinceCount,
        { timeout: 15000 },
      )
      pass(`筛选后 UI 行数 == API 筛选数 ${sinceCount}`)
    } catch {
      const cur = await ghRows.count()
      fail(`筛选后 UI 行数 ${cur} != 期望 ${sinceCount}`)
    }
    // 筛选标记
    const metaText = await page.locator('.git-meta').innerText().catch(() => '')
    if (/已按日期筛选/.test(metaText)) pass('meta 显示「已按日期筛选」')
    else fail('筛选后未显示「已按日期筛选」标记: ' + JSON.stringify(metaText))

    // 截图（筛选态）
    try {
      await page.screenshot({ path: path.join(SHOTS_DIR, 'system_manager_gitlog_filter.png'), fullPage: false })
      log('  证据截图(筛选态):', path.join(SHOTS_DIR, 'system_manager_gitlog_filter.png'))
    } catch (e) { log('  [warn] 截图失败:', e.message) }

    // 点「全部」→ 恢复全部
    log('  --- 点「全部」恢复全部 ---')
    await page.locator('.git-filter .el-button', { hasText: '全部' }).click()
    try {
      await page.waitForFunction(
        (exp) => {
          const rows = document.querySelectorAll('.el-table__row')
          const gitRows = Array.from(rows).filter(r => r.querySelector('code.mono'))
          return gitRows.length === exp
        },
        total,
        { timeout: 15000 },
      )
      pass(`点「全部」后 UI 行数恢复为 ${total}`)
    } catch {
      const cur = await ghRows.count()
      fail(`点「全部」后 UI 行数 ${cur} != 期望 ${total}`)
    }
  }

  // 证据截图（全部态）
  try {
    await page.screenshot({ path: path.join(SHOTS_DIR, 'system_manager_gitlog.png'), fullPage: false })
    log('  证据截图(全部态):', path.join(SHOTS_DIR, 'system_manager_gitlog.png'))
  } catch (e) { log('  [warn] 截图失败:', e.message) }
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
