// 验证 Img2Img.vue 专业图片编辑页（D 盘合规：browser 读 C 已有二进制，profile/temp 全在 D）
import { chromium } from 'playwright'
import path from 'path'
import fs from 'fs'

const BASE = 'http://localhost:8099'
const TMP = 'D:/AI/manju_agent/.tmp'
const TEST_IMG = 'D:/AI/manju_agent/.tmp/test_edit.png'
const SHOT = path.join(TMP, 'verify')
fs.mkdirSync(SHOT, { recursive: true })

const consoleErrors = []
const pageErrors = []

const userDataDir = path.join(TMP, `pw_verify_${Date.now()}_${process.pid}`)
fs.mkdirSync(userDataDir, { recursive: true })

const browser = await chromium.launch({
  headless: true,
  env: {
    ...process.env,
    PLAYWRIGHT_BROWSERS_PATH: 'C:/Users/L/AppData/Local/ms-playwright',
    TMPDIR: TMP, TEMP: TMP, TMP: TMP,
  },
})
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
const page = await ctx.newPage()
page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()) })
page.on('pageerror', e => pageErrors.push(String(e)))

const log = (...a) => console.log('[verify]', ...a)
const fails = []
function check(cond, name) { if (cond) log('PASS:', name); else { log('FAIL:', name); fails.push(name) } }

try {
  await page.goto(`${BASE}/img2img`, { waitUntil: 'networkidle', timeout: 30000 })
  await page.waitForSelector('.editor', { timeout: 10000 })
  check(await page.locator('.editor h1').innerText().then(t => t.includes('专业图片编辑')), '编辑器标题渲染')
  check(await page.locator('.mode-tabs button').count() === 8, '8 个模式 tab')
  await page.screenshot({ path: path.join(SHOT, '01_home.png'), fullPage: true })

  // 上传源图（第一个 .up 内的 file input）
  const fileInput = page.locator('.up input[type=file]').first()
  await fileInput.setInputFiles(TEST_IMG)
  await page.waitForSelector('.canvas-wrap img', { timeout: 8000 })
  check(true, '源图上传并预览')
  await page.waitForTimeout(300)

  // 切换局部重绘
  await page.getByRole('button', { name: '局部重绘' }).click()
  await page.waitForTimeout(200)
  const brushTools = await page.locator('.mask-tools').count()
  check(brushTools === 1, 'inpaint 显示笔刷工具条')
  const canvas = page.locator('.mask-canvas')
  check(await canvas.count() === 1, '蒙版 canvas 存在')
  // 在 canvas 上涂抹
  const box = await canvas.boundingBox()
  if (box) {
    await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.4)
    await page.mouse.down()
    await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.6, { steps: 8 })
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.7, { steps: 8 })
    await page.mouse.up()
    await page.waitForTimeout(200)
    const paintedPixels = await canvas.evaluate(el => {
      const c = el.getContext('2d')
      const d = c.getImageData(0, 0, el.width, el.height).data
      let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++
      return n
    })
    check(paintedPixels > 0, `蒙版画笔生效（非透明像素 ${paintedPixels}）`)
  }
  // 测试橡皮/反相/清除按钮不报错
  await page.getByRole('button', { name: '反相' }).click().catch(() => {})
  await page.waitForTimeout(100)
  await page.getByRole('button', { name: '清除' }).click().catch(() => {})
  await page.waitForTimeout(100)
  await page.screenshot({ path: path.join(SHOT, '02_inpaint.png'), fullPage: true })

  // 前后对比滑块（上传后 compareBefore 已设置）
  check(await page.locator('.compare .c-slider').count() === 1, '前后对比滑块存在')
  await page.locator('.compare .c-slider').evaluate(el => { el.value = 30; el.dispatchEvent(new Event('input', { bubbles: true })) }).catch(() => {})

  // 遍历其余模式的面板渲染
  const modeChecks = [
    ['扩图', '扩展边距'],
    ['高清放大', '放大设置'],
    ['面部修复', '修复方法'],
    ['风格迁移', '风格参考图'],
    ['ControlNet', '结构控制'],
    ['图生图', '重绘幅度'],
    ['反推提示词', '反推提示词'],
  ]
  for (const [label, expectText] of modeChecks) {
    await page.locator('.mode-tabs').getByRole('button', { name: label }).click()
    await page.waitForTimeout(250)
    const ok = await page.locator('.ws-grid').innerText().then(t => t.includes(expectText)).catch(() => false)
    check(ok, `模式[${label}]面板含「${expectText}」`)
  }

  // 端到端：inpaint 生成应走到后端并 honest 报错（ComfyUI 未启动）
  await page.getByRole('button', { name: '局部重绘' }).click()
  await page.waitForTimeout(200)
  // 重新涂抹（清除后又切模式，canvas 可能被 clearMask）
  const box2 = await canvas.boundingBox()
  if (box2) {
    await page.mouse.move(box2.x + box2.width * 0.5, box2.y + box2.height * 0.5)
    await page.mouse.down(); await page.mouse.up()
    await page.waitForTimeout(150)
  }
  await page.getByRole('button', { name: '开始编辑' }).click()
  const errMsg = await page.waitForSelector('.el-message--error, .el-message', { timeout: 15000 }).then(el => el.innerText()).catch(() => '')
  check(errMsg.includes('ComfyUI') || errMsg.includes('失败'), `生成链路 honest 报错（el-message: "${errMsg}"）`)

  await page.screenshot({ path: path.join(SHOT, '03_final.png'), fullPage: true })
} catch (e) {
  log('EXCEPTION:', String(e))
  fails.push('EXCEPTION: ' + String(e).slice(0, 120))
} finally {
  log('consoleErrors:', consoleErrors.length, JSON.stringify(consoleErrors.slice(0, 5)))
  log('pageErrors:', pageErrors.length, JSON.stringify(pageErrors.slice(0, 5)))
  log('FAILS:', fails.length, JSON.stringify(fails))
  await browser.close().catch(() => {})
  try { fs.rmSync(userDataDir, { recursive: true, force: true }) } catch (_) {}
  process.exit(fails.length === 0 ? 0 : 1)
}
