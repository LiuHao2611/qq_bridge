// 导演 Agent 悬浮球冒烟测试（安全：临时目录强制 D 盘）
import { launchChromium } from './launch_chromium_safe.mjs'

const PID = process.env.PID || '774979db-65fd-4bef-8f58-67e963e5fe68'
const BASE = 'http://127.0.0.1:8099'
const errors = []
let failures = 0

function check(cond, name) {
  if (cond) console.log('PASS ·', name)
  else { console.log('FAIL ·', name); failures++ }
}

const { browser, ctx } = await launchChromium({
  label: 'orb',
  // 复用已存在的 playwright 浏览器（只读执行，不向 C 盘写入；user-data-dir 仍走 D 盘 .tmp）
  env: { PLAYWRIGHT_BROWSERS_PATH: 'C:/Users/L/AppData/Local/ms-playwright' },
})
ctx.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
ctx.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message))

const page = await ctx.newPage()
await page.goto(`${BASE}/projects/${PID}/assets`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)

// 1) 悬浮球按钮存在
await page.evaluate(() => { try { localStorage.removeItem('manju_director_orb_pos') } catch (e) {} })
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(900)
const orbBtn = await page.$('.orb-btn')
check(!!orbBtn, '悬浮球按钮渲染 (P1 资产页)')

// 1.5) 拖动 + 磁吸边框验证
if (orbBtn) {
  const root = await page.$('.dir-orb-root')
  const before = await root.evaluate((el) => el.style.left + '|' + el.style.top)
  const box = await orbBtn.boundingBox()
  const sx = box.x + box.width / 2, sy = box.y + box.height / 2
  await page.mouse.move(sx, sy)
  await page.mouse.down()
  await page.mouse.move(sx - 180, sy - 120, { steps: 10 })
  await page.mouse.move(24, 24, { steps: 10 }) // 贴近左上角触发磁吸
  await page.mouse.up()
  await page.waitForTimeout(500)
  const after = await root.evaluate((el) => el.style.left + '|' + el.style.top)
  check(before !== after, '拖动后悬浮球位置改变 (' + before + ' -> ' + after + ')')
  const leftVal = await root.evaluate((el) => parseFloat(el.style.left))
  const topVal = await root.evaluate((el) => parseFloat(el.style.top))
  check(Math.abs(leftVal - 12) < 3 && Math.abs(topVal - 12) < 3, '磁吸到左上边框 (left=' + leftVal + ', top=' + topVal + ')')
  const saved = await page.evaluate(() => localStorage.getItem('manju_director_orb_pos'))
  check(!!saved, '位置持久化写入 localStorage')
}

// 2) 点击展开监督窗口
if (orbBtn) {
  await orbBtn.click()
  await page.waitForTimeout(600)
  const panel = await page.$('.orb-panel')
  check(!!panel, '点击后监督窗口展开')
  const dag = await page.$$('.orb-dag .orb-node')
  check(dag.length === 9, 'DAG 九节点渲染 (N01–N09) 实=' + dag.length)
  const secs = await page.$$('.orb-sec')
  check(secs.length >= 3, '监督窗口含多区块 (实现过程/小任务/日志)')
  // 当前阶段执行逻辑（P1 资产库 = 资产初始化）
  const stageDesc = await page.$('.orb-stage-desc')
  const logicItems = await page.$$('.orb-logic .logic-item')
  const metricCells = await page.$$('.orb-metrics .osp-cell')
  check(!!stageDesc, '当前阶段描述渲染 (P1)')
  check(logicItems.length >= 4, '当前阶段执行逻辑条目数 = ' + logicItems.length + ' (>=4)')
  check(metricCells.length >= 2, '当前阶段进度指标数 = ' + metricCells.length + ' (>=2)')
  // 点击空白处关闭（设计最佳实践：点击 orb 之外的任意位置即关闭）
  await page.mouse.click(700, 400)
  await page.waitForTimeout(400)
  const panel2 = await page.$('.orb-panel')
  check(!panel2, '点击空白处关闭悬浮窗口')
  // 再次点击球可重新展开
  await orbBtn.click()
  await page.waitForTimeout(400)
  const panel3 = await page.$('.orb-panel')
  check(!!panel3, '再次点击球重新展开')
}

// 3) 切换到其它阶段页，悬浮球仍应在
for (const suf of ['planning', 'editor', 'production']) {
  await page.goto(`${BASE}/projects/${PID}/${suf}`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  const b = await page.$('.orb-btn')
  check(!!b, `悬浮球存在 (${suf})`)
}

// 4) 非项目页（首页）不应出现悬浮球
await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
await page.waitForTimeout(700)
const homeOrb = await page.$('.orb-btn')
check(!homeOrb, '首页不显示悬浮球')

console.log('--- console errors (' + errors.length + ') ---')
errors.slice(0, 20).forEach((e) => console.log('  ', e))

await browser.close()
console.log(failures === 0 && errors.length === 0 ? '\n✅ ORB SMOKE OK' : `\n❌ ORB SMOKE: failures=${failures} errors=${errors.length}`)
process.exit(failures === 0 && errors.length === 0 ? 0 : 1)
