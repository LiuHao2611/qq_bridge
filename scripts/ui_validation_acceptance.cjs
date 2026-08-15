// UI 基本校验/提示 验收脚本（机器判定，非零退出 = 失败）
// 验证本轮修复的「假阳性 / 缺守卫 / 静默弹窗」类缺陷：
//   1) 全量静态扫描：src 下所有 .vue 不得再出现 ElMessageBox.confirm/alert/prompt
//      —— 本应用为自研弹窗，Element Plus 的 ElMessageBox 不渲染，调用即静默失败
//         （delete 类用 await 会永久挂起，confirm 类点了没反应）。
//   2) 全量静态扫描：不得再出现硬编码「512维向量」假阳性徽标（角色卡无图也显示向量）。
//   3) 运行时：全局资产删除必须先弹原生确认框，未确认不得删除。
//   4) 运行时：全局资产卡片不再伪造「已定稿」绿标。
// 运行：NODE_PATH=C:/Users/L/.workbuddy/binaries/node/workspace/node_modules node scripts/ui_validation_acceptance.cjs
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const BASE = process.env.BASE || 'http://127.0.0.1:8099'
const evidenceDir = 'docs/evidence'
try { fs.mkdirSync(evidenceDir, { recursive: true }) } catch {}

const proof = { checks: [], ok: true }
function record(name, pass, detail) {
  proof.checks.push({ name, pass, detail })
  if (!pass) proof.ok = false
  console.log((pass ? 'PASS ' : 'FAIL ') + name + (detail ? ' — ' + detail : ''))
}

// ---- 全量静态扫描：遍历 src 下所有 .vue，断言无静默弹窗 / 假阳性徽标 ----
function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walk(p, out)
    else if (e.name.endsWith('.vue')) out.push(p)
  }
  return out
}
function staticSourceChecks() {
  const SRC = path.resolve(__dirname, '../src')
  const files = walk(SRC)
  let bad = 0
  const detail = []
  for (const f of files) {
    const t = fs.readFileSync(f, 'utf8')
    if (/ElMessageBox\.(confirm|alert|prompt)/.test(t)) {
      bad++
      detail.push(path.relative(SRC, f) + ' 仍有 ElMessageBox 弹窗（不渲染/静默失败）')
    }
    if (/512维向量/.test(t)) {
      bad++
      detail.push(path.relative(SRC, f) + ' 仍硬编码 512维向量 假阳性徽标')
    }
  }
  record('no-silent-elmessagebox-dialogs', bad === 0,
    bad === 0 ? `扫描 ${files.length} 个 .vue，无 ElMessageBox 弹窗 / 无 512 维假阳性` : detail.join('; '))
}

;(async () => {
  staticSourceChecks()

  const browser = await chromium.launch()
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  // 原生 confirm：Playwright 默认自动 dismiss；监听 dialog 事件以验证确认框出现，并取消（不删除）
  let dialogShown = false
  let dialogMsg = ''
  page.on('dialog', async (d) => {
    dialogShown = true
    dialogMsg = d.message()
    await d.dismiss() // 取消，验证取消后不删除
  })
  try {
    await page.goto(BASE + '/global-assets', { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('.asset-card', { timeout: 20000 })

    // ---- 运行时检查：不应存在伪造的「已定稿」绿标 ----
    const fakeBadge = await page.locator('.asset-card .stage', { hasText: '已定稿' }).count()
    record('global-asset-no-fake-done', fakeBadge === 0,
      fakeBadge === 0 ? '无硬编码「已定稿」' : `仍发现 ${fakeBadge} 个「已定稿」`)

    // ---- 运行时检查：删除必须先弹确认框，且取消后不删除 ----
    const before = await page.locator('.asset-card').count()
    const firstCard = page.locator('.asset-card').first()
    await firstCard.hover()
    const delBtn = firstCard.locator('button:has-text("删除")')
    await delBtn.click()
    await page.waitForTimeout(800)
    record('global-asset-delete-confirm-shown', dialogShown,
      dialogShown ? ('点击删除后出现确认框：' + dialogMsg.slice(0, 24) + '…') : '点击删除后无确认框（零确认直接删）')
    if (dialogShown) {
      // dismiss 已在 dialog 事件里执行；取消后资产数不变
      await page.waitForTimeout(400)
      const after = await page.locator('.asset-card').count()
      record('global-asset-not-deleted-without-confirm', after === before,
        after === before ? `取消后资产数不变(${before})` : `取消后资产数变化 ${before}→${after}`)
    }

    if (errors.length) record('no-page-errors', false, 'pageerror: ' + errors.slice(0, 3).join(' | '))
    else record('no-page-errors', true, '无未捕获页面错误')

    await page.screenshot({ path: evidenceDir + '/ui_validation.png', fullPage: false }).catch(() => {})
  } catch (e) {
    record('script-execution', false, String(e))
  } finally {
    await browser.close()
  }
  fs.writeFileSync(evidenceDir + '/ui_validation_proof.json', JSON.stringify(proof, null, 2))
  console.log('\nRESULT: ' + (proof.ok ? 'ALL PASS' : 'HAS FAILURES'))
  process.exit(proof.ok ? 0 : 1)
})()
