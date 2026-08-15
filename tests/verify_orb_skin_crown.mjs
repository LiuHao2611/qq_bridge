// tests/verify_orb_skin_crown.mjs
// 验收：P5 主题 Director Orb 新增第 5 个形态皮肤「怪盗王冠」（crown）
//   1) 构建产物：dist CSS 含 .orb-morph--crown 选择器；dist JS 含注册表 label「怪盗王冠」
//   2) 渲染：P5 主题 + orbSkin=crown 下，阶段页悬浮球按钮带 .orb-morph--crown 类，
//      且其内部 .orb-morph 包围盒 > 0（证明 CSS 形态已绘制，非隐形）
//   3) 设置页：P5 主题下悬浮球皮肤下拉含「怪盗王冠」选项（证明设置可枚举到新皮肤）
//
// 退出码：0=PASS / 1=FAIL（fail-closed）

import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'

const ROOT = 'D:/AI/manju_agent'
const UI_BASE = 'http://127.0.0.1:8099'
const PID = 'c3f56bfa-4c52-40c0-bb60-46430f1efde9'
const PHASE_URL = `${UI_BASE}/projects/${PID}/assets`
const SETTINGS_URL = `${UI_BASE}/settings`
const SHOTS_DIR = path.join(ROOT, 'tests/recordings')

let failures = 0
const log = (...a) => console.log(...a)
const fail = (m) => { failures++; log('  ✗ FAIL:', m) }
const pass = (m) => log('  ✓ PASS:', m)

// 1) 构建产物断言（grep dist）
const distDir = path.join(ROOT, 'frontend/dist')
const findAsset = (suffix) => {
  const d = path.join(distDir, 'assets')
  if (!fs.existsSync(d)) return null
  const files = fs.readdirSync(d).filter((f) => f.endsWith(suffix))
  return files.length ? path.join(d, files[0]) : null
}
const cssFile = findAsset('.css')
if (!cssFile) fail('未找到 dist CSS 产物')
else if (fs.readFileSync(cssFile, 'utf8').includes('.orb-morph--crown')) pass('dist CSS 含 .orb-morph--crown 选择器')
else fail('dist CSS 缺失 .orb-morph--crown 选择器')
// JS 分包：扫描全部 .js 产物（label 在应用主 chunk，不在 vendor 分包）
const d = path.join(distDir, 'assets')
const jsFiles = fs.existsSync(d) ? fs.readdirSync(d).filter((f) => f.endsWith('.js')) : []
const jsHit = jsFiles.some((f) => fs.readFileSync(path.join(d, f), 'utf8').includes('怪盗王冠'))
if (!jsFiles.length) fail('未找到 dist JS 产物')
else if (jsHit) pass('dist JS 含注册表 label「怪盗王冠」（主 chunk）')
else fail('dist JS 缺失「怪盗王冠」label')

// 2)+3) 浏览器渲染断言
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const page = await ctx.newPage()
page.on('console', (m) => { if (m.type() === 'error') log('  [console.error]', m.text()) })
// 预设 P5 主题 + crown 皮肤（wn_settings.accent 默认 p5；orbSkin=crown）
await page.addInitScript(() => {
  localStorage.setItem('manju_theme', 'p5')
  localStorage.setItem('wn_settings', JSON.stringify({ accent: 'p5', orbSkin: 'crown' }))
})

try {
  log('========== P5 悬浮球 crown 皮肤验收 ==========')
  await page.goto(PHASE_URL, { waitUntil: 'load' })
  const orb = page.locator('.orb-btn.orb-morph--crown')
  try {
    await orb.first().waitFor({ state: 'visible', timeout: 20000 })
    pass('阶段页悬浮球渲染 .orb-morph--crown（P5 + crown）')
  } catch {
    fail('阶段页 20s 内未出现 .orb-btn.orb-morph--crown（检查主题/皮肤是否生效）')
  }

  if (await orb.count()) {
    const box = await orb.first().locator('.orb-morph').boundingBox().catch(() => null)
    if (box && box.width > 0 && box.height > 0) pass(`.orb-morph 包围盒 ${Math.round(box.width)}x${Math.round(box.height)} > 0（形态已绘制）`)
    else fail('.orb-morph 包围盒为空（形态未绘制）')
    // 计算样式确认 clip-path 生效（crown 多边形）
    const clip = await page.evaluate(() => {
      const el = document.querySelector('.orb-btn.orb-morph--crown .orb-morph')
      if (!el) return null
      return getComputedStyle(el).clipPath
    })
    if (clip && /polygon/.test(clip)) pass('crown 形态 clip-path 已生效: ' + clip.slice(0, 40) + '…')
    else fail('crown 形态 clip-path 未生效: ' + JSON.stringify(clip))

    try {
      // 全页截图（含阶段页上下文）
      await page.screenshot({ path: path.join(SHOTS_DIR, 'orb_skin_crown_page.png'), fullPage: false })
      log('  证据截图(全页):', path.join(SHOTS_DIR, 'orb_skin_crown_page.png'))
      // 悬浮球形态特写（只截 orb 元素，便于看清王冠形状）
      await orb.first().screenshot({ path: path.join(SHOTS_DIR, 'orb_skin_crown.png') })
      log('  证据截图(王冠特写):', path.join(SHOTS_DIR, 'orb_skin_crown.png'))
    } catch (e) { log('  [warn] 截图失败:', e.message) }
  }

  // 3) 设置页下拉含「怪盗王冠」
  await page.goto(SETTINGS_URL, { waitUntil: 'load' })
  const label = page.locator('.ac-label', { hasText: '怪盗王冠' })
  try {
    await label.first().waitFor({ state: 'visible', timeout: 10000 })
    pass('设置页悬浮球皮肤下拉含「怪盗王冠」选项')
  } catch {
    // 兜底：打印当前下拉所有 label，便于排查
    const all = await page.locator('.ac-label').allInnerTexts().catch(() => [])
    fail('设置页未找到「怪盗王冠」选项；当前下拉: ' + JSON.stringify(all))
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
log('\n========== 验收通过：P5 悬浮球 crown 皮肤 OK ==========')
process.exit(0)
