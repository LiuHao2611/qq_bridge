// 保姆级教程 · 真实页面截图（带红框编号标注）
// 走完整「从 0 到 1 做一部短剧」动线，对每个关键页面/弹窗截图并红框标注按钮。
// 输出：outputs/tutorial_shots/*.png
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

process.env.PLAYWRIGHT_BROWSERS_PATH ||= 'D:/AI/.pw-browsers'
const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8099'
const VIEWPORT = { width: 1440, height: 900 }
const OUT = 'D:/AI/manju_agent/outputs/tutorial_shots'
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu'] })
const ctx = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1 })
const page = await ctx.newPage()
page.on('console', () => {})
page.on('pageerror', () => {})

const sleep = (ms) => page.waitForTimeout(ms)

// 红框标注 + 编号；specs: [{sel,label}]  sel 支持 Playwright 选择器（含 :has-text）
async function annotate(specs) {
  const boxes = []
  for (const spec of specs) {
    let locs = []
    try { locs = await page.locator(spec.sel).all() } catch {}
    for (const loc of locs) {
      let b = null
      try {
        b = await loc.evaluate((el) => {
          const r = el.getBoundingClientRect()
          if (r.width < 1 || r.height < 1) return null
          return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height }
        })
      } catch {}
      if (b) boxes.push({ ...b, label: spec.label })
    }
  }
  await page.evaluate((boxes) => {
    const old = document.getElementById('tut-annot')
    if (old) old.remove()
    const layer = document.createElement('div')
    layer.id = 'tut-annot'
    layer.style.cssText = 'position:absolute;top:0;left:0;width:0;height:0;z-index:2147483647;pointer-events:none;'
    document.body.appendChild(layer)
    boxes.forEach((b, i) => {
      const d = document.createElement('div')
      d.style.cssText = `position:absolute;left:${b.x}px;top:${b.y}px;width:${b.w}px;height:${b.h}px;border:3px solid #ff2d2d;box-sizing:border-box;box-shadow:0 0 0 1px rgba(255,45,45,.35);`
      const tag = document.createElement('div')
      tag.textContent = String(i + 1) + (b.label ? '  ' + b.label : '')
      tag.style.cssText = 'position:absolute;left:0;top:-22px;background:#ff2d2d;color:#fff;font:12px/18px system-ui;padding:1px 6px;white-space:nowrap;border-radius:3px;'
      d.appendChild(tag)
      layer.appendChild(d)
    })
  }, boxes)
  return boxes.length
}
async function shoot(name, specs = []) {
  const n = specs.length ? await annotate(specs) : 0
  await sleep(400)
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true })
  await page.evaluate(() => { const l = document.getElementById('tut-annot'); if (l) l.remove() })
  console.log(`  shot ${name}.png (${n} 标注)`)
}

// ---- 创建示例项目（拿真实 id） ----
console.log('[准备] 创建示例项目')
await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
const pid = await page.evaluate(async () => {
  const r = await fetch('/api/v1/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: '我的第一部短剧', kind: 'drama', package: 'flash_short', description: '教程示例', aspect_ratio: '16:9', resolution: '1080p', ai_video_model: 'seedance_2_mini', clip_duration: 12, gen_audio: true }) })
  const j = await r.json()
  return j.data.project.project_id
})
console.log('  项目 id =', pid)

// 1) 首页
console.log('[1] 首页 /')
await page.goto(BASE + '/', { waitUntil: "domcontentloaded" }); await sleep(1500)
await shoot('01_home', [
  { sel: 'input[placeholder*="一句话"], input[placeholder*="开启"]', label: '一句话创作搜索框' },
  { sel: 'button:has-text("新建项目")', label: '＋新建项目' },
  { sel: '.side-bar, .sidebar, aside', label: '左侧主导航' },
])

// 2) 项目中心
console.log('[2] 项目中心 /projects')
await page.goto(BASE + '/projects', { waitUntil: "domcontentloaded" }); await sleep(1500)
await shoot('02_projects', [
  { sel: 'button:has-text("新建项目")', label: '＋新建项目' },
  { sel: '.filter-tab, .el-tabs__item', label: '筛选Tab' },
])

// 3) 新建项目弹窗
console.log('[3] 新建项目弹窗')
const newBtn = page.locator('button:has-text("新建项目")').first()
await newBtn.click(); await sleep(1200)
await shoot('03_new_project_dialog', [
  { sel: '.el-dialog:has-text("新建项目") input', label: '项目名称等输入框' },
  { sel: '.el-dialog:has-text("新建项目") .el-select', label: '套餐/比例/分辨率/视频模型下拉' },
  { sel: '.el-dialog:has-text("新建项目") button:has-text("立即创建")', label: '立即创建' },
  { sel: '.el-dialog:has-text("新建项目") button:has-text("取消")', label: '取消' },
])
// 关闭弹窗
await page.locator('.el-dialog:has-text("新建项目") button:has-text("取消")').click().catch(() => {})
await sleep(500)

// 4) 资产初始化
console.log('[4] 资产初始化 /assets')
await page.goto(BASE + `/projects/${pid}/assets`, { waitUntil: "domcontentloaded" }); await sleep(1800)
await shoot('04_assets', [
  { sel: 'button:has-text("上传素材")', label: '＋上传素材' },
  { sel: 'button:has-text("从剧本提取")', label: '📝从剧本提取' },
  { sel: 'button:has-text("从全集导入")', label: '🌐从全集导入' },
  { sel: 'button:has-text("导入历史角色卡")', label: '📥导入历史角色卡' },
  { sel: '.tab', label: '角色/场景/道具 Tab' },
])

// 5) 资产页「导入类按钮」特写（本环境空项目不渲染上传弹窗，故改为按钮区放大，保证有图）
console.log('[5] 资产页导入按钮特写')
await page.goto(BASE + `/projects/${pid}/assets`, { waitUntil: "domcontentloaded" }); await sleep(2500)
const tb = page.locator('button.import-btn:has-text("从剧本提取"), button:has-text("从剧本提取")').first()
let clip = null
if (await tb.count()) {
  const bb = await tb.boundingBox().catch(() => null)
  if (bb) clip = { x: Math.max(0, bb.x - 24), y: Math.max(0, bb.y - 64), width: Math.min(1000, bb.width + 520), height: 150 }
}
if (clip) {
  await annotate([
    { sel: 'button.import-btn:has-text("从剧本提取"), button:has-text("从剧本提取")', label: '📝从剧本提取' },
    { sel: 'button.import-btn:has-text("从全集导入"), button:has-text("从全集导入")', label: '🌐从全集导入' },
    { sel: 'button.import-btn:has-text("上传素材"), button:has-text("上传素材")', label: '＋上传素材' },
    { sel: 'button.import-btn:has-text("导入历史角色卡"), button:has-text("导入历史角色卡")', label: '📥导入历史角色卡' },
  ])
  await sleep(400)
  await page.screenshot({ path: `${OUT}/05_assets_toolbar.png`, clip })
  await page.evaluate(() => { const l = document.getElementById('tut-annot'); if (l) l.remove() })
  console.log('  shot 05_assets_toolbar.png')
} else {
  await shoot('05_assets_toolbar', [{ sel: 'button:has-text("从剧本提取")', label: '📝从剧本提取' }])
}

// 6) 规划页
console.log('[6] 项目策划 /planning')
await page.goto(BASE + `/projects/${pid}/planning`, { waitUntil: "domcontentloaded" }); await sleep(3000)
await page.locator('textarea').first().scrollIntoViewIfNeeded().catch(() => {})
await sleep(600)
await shoot('06_planning', [
  { sel: 'textarea', label: '原始故事文本域' },
  { sel: 'button:has-text("AI 自动分场")', label: '✨AI 自动分场' },
  { sel: '.el-select', label: '模型选择下拉' },
  { sel: 'button:has-text("导入选中")', label: '导入选中 N 场' },
])

// 7) 分镜编排
console.log('[7] 分镜编排 /editor')
await page.goto(BASE + `/projects/${pid}/editor`, { waitUntil: "domcontentloaded" }); await sleep(1800)
await shoot('07_editor', [
  { sel: 'button:has-text("AI 润色")', label: '✨AI 润色' },
  { sel: 'button:has-text("合成全集")', label: '🎞️合成全集' },
  { sel: 'button:has-text("导出")', label: '导出▾' },
  { sel: 'button:has-text("新增分镜")', label: '＋新增分镜' },
  { sel: '.shot-block, [class*="shot"]', label: '分镜块' },
])

// 8) 生成生产
console.log('[8] 生成生产 /production')
await page.goto(BASE + `/projects/${pid}/production`, { waitUntil: "domcontentloaded" }); await sleep(1800)
await shoot('08_production', [
  { sel: 'button:has-text("导演一站式生成")', label: '🎬导演一站式生成' },
  { sel: 'button:has-text("一键分发全部")', label: '一键分发全部' },
  { sel: 'button:has-text("合成全集")', label: '合成全集' },
  { sel: 'button:has-text("分发生成")', label: '分发生成' },
  { sel: 'button:has-text("实时日志")', label: '实时日志' },
])

// 9) 任务监控
console.log('[9] 推理监控 /tasks')
await page.goto(BASE + '/tasks', { waitUntil: "domcontentloaded" }); await sleep(1800)
await shoot('09_tasks', [
  { sel: 'button:has-text("清理显存")', label: '清理显存' },
  { sel: '.el-tabs__item', label: '全部/排队/生成中/已完成/失败 Tab' },
  { sel: 'button:has-text("重试")', label: '重试' },
  { sel: 'button:has-text("实时日志")', label: '实时日志' },
])

// 10) 设置 - 外观
console.log('[10] 设置 /settings (外观)')
await page.goto(BASE + '/settings', { waitUntil: "domcontentloaded" }); await sleep(1200)
await page.locator('.el-tabs__item', { hasText: /^外观$/ }).click().catch(() => {})
await sleep(800)
await shoot('10_settings_appearance', [
  { sel: '.el-tabs__item', label: '各设置Tab' },
  { sel: 'button:has-text("保存设置")', label: '保存设置' },
])

// 11) 设置 - AI 模型
console.log('[11] 设置 /settings (AI 模型)')
await page.locator('.el-tabs__item', { hasText: /^AI 模型$/ }).click().catch(() => {})
await sleep(1000)
await shoot('11_settings_aimodel', [
  { sel: '.el-card', label: '4 张模型卡' },
  { sel: 'button:has-text("测试连接")', label: '测试连接' },
  { sel: 'button:has-text("刷新模型列表")', label: '刷新模型列表' },
])

// 12) 设置 - 模型 (ModelManager)
console.log('[12] 设置 /settings (模型/ModelManager)')
await page.locator('.el-tabs__item', { hasText: /^模型$/ }).click().catch(() => {})
await sleep(1000)
await shoot('12_settings_models', [
  { sel: 'button:has-text("扫描未声明模型")', label: '扫描未声明模型' },
  { sel: 'button:has-text("刷新已接入")', label: '刷新已接入' },
])

await browser.close()
console.log('\nDONE. 截图在', OUT)
