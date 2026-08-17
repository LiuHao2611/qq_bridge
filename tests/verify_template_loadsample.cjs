const { chromium } = require('playwright')

const BASE = 'http://127.0.0.1:8099'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const errors = []
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message))
  page.on('console', (m) => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text()) })

  const fails = []
  await page.goto(BASE + '/settings', { waitUntil: 'networkidle' })
  await sleep(700)

  await page.evaluate(() => {
    const t = [...document.querySelectorAll('.el-tabs__item')].find((x) => x.textContent.trim() === '模板')
    if (t) t.click()
  })
  await sleep(500)

  // 1) 修复后：挂载时不应预填示例
  const initial = await page.evaluate(() => {
    const ta = document.querySelector('.te-json')
    return ta ? ta.value : '(none)'
  })
  if (initial.trim().length > 0) fails.push('修复失败：挂载时文本框已预填示例 (len=' + initial.length + ')')

  // 2) 点击「载入示例」应填充且弹 toast
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find((x) => x.textContent.trim() === '载入示例')
    if (b) b.click()
  })
  await sleep(500)

  const after = await page.evaluate(() => {
    const ta = document.querySelector('.te-json')
    return ta ? ta.value : '(none)'
  })
  if (after.trim().length < 100) fails.push('点击后文本框未填充示例 (len=' + after.length + ')')

  const toast = await page.evaluate(() => {
    const msgs = [...document.querySelectorAll('.el-message')]
    return msgs.map((m) => m.textContent).join(' | ')
  })
  if (!/已载入示例工作流/.test(toast)) fails.push('未出现 toast 反馈: "' + toast + '"')

  // 3) 再次点击仍应给反馈（无反应问题根除）
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find((x) => x.textContent.trim() === '载入示例')
    if (b) b.click()
  })
  await sleep(400)
  const toast2 = await page.evaluate(() => [...document.querySelectorAll('.el-message')].map((m) => m.textContent).join(' | '))
  if (!/已载入示例工作流/.test(toast2)) fails.push('重复点击无反馈')

  if (errors.length) fails.push('页面错误: ' + errors.slice(0, 3).join(' | '))

  if (fails.length) {
    console.log('FAIL:')
    fails.forEach((f) => console.log(' - ' + f))
    process.exit(1)
  }
  console.log('PASS · 修复验证通过：挂载不预填 / 点击载入示例填充+toast / 重复点击仍有反馈 / 无页面错误')
  await browser.close()
})().catch((e) => { console.error('FATAL', e); process.exit(1) })
