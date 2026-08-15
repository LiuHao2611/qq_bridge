// 万能短剧 · 页面自定义 · 正式验收（Playwright 真浏览器 · 机器判定）
// 校验：沙盒左栏完全镜像真实应用 7 组 19 项；每个真实页面含其真实内部页签/弹窗；
// 全部页面可加载且可编辑（改标题实时生效）；真实设置页「自定义」主题集成。
// 任何一个 check 失败 → 进程非零退出；全部通过 → 退出 0。
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.env.E2E_BASE || 'http://localhost:8099'
const SHOTS = 'D:/AI/manju_agent/.tmp/custompage_shots'
mkdirSync(SHOTS, { recursive: true })

const results = []
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail })
  console.log(`${cond ? 'PASS' : 'FAIL'} | ${name}${detail ? ' :: ' + detail : ''}`)
}

// 红框标注：在关键元素上叠加固定定位红框（截图用）
async function annotate(page, selectors, prefix = '') {
  await page.evaluate(({ sels, pf }) => {
    document.querySelectorAll('.e2e-box').forEach((e) => e.remove())
    let i = 0
    sels.forEach((sel) => {
      document.querySelectorAll(sel).forEach((el) => {
        const r = el.getBoundingClientRect()
        if (!r.width && !r.height) return
        const box = document.createElement('div')
        box.className = 'e2e-box'
        box.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;z-index:99999;border:2px solid #ff2d2d;box-sizing:border-box;pointer-events:none;`
        const tag = document.createElement('span')
        tag.textContent = pf + (i++)
        tag.style.cssText = 'position:absolute;top:-18px;left:0;background:#ff2d2d;color:#fff;font-size:10px;padding:0 4px;border-radius:3px;font-family:monospace;'
        box.appendChild(tag)
        document.body.appendChild(box)
      })
    })
  }, { sels: selectors, pf: prefix })
}
async function clearAnnotate(page) {
  await page.evaluate(() => document.querySelectorAll('.e2e-box').forEach((e) => e.remove()))
}

// 真实应用 19 个导航项（与 src/components/SideBar.vue 完全一致）
const PAGE_IDS = [
  { id: 'home', title: '创作驾驶舱' },
  { id: 'projects', title: '我的项目' },
  { id: 'tasks', title: '推理监控' },
  { id: 'custom', title: '页面自定义' },
  { id: 'txt2img', title: '文生图' },
  { id: 'img2img', title: '图生图' },
  { id: 'creative', title: '创意工坊' },
  { id: 'memoir', title: '人生回忆录' },
  { id: 'global-assets', title: '全局资产库' },
  { id: 'img2prompt', title: '图片反推' },
  { id: 'lora-train', title: 'LoRA 训练' },
  { id: 'history', title: '历史记录' },
  { id: 'membership', title: '会员中心' },
  { id: 'messages', title: '消息中心' },
  { id: 'help', title: '帮助与反馈' },
  { id: 'my-data', title: '我的数据' },
  { id: 'logs', title: '操作日志' },
  { id: 'settings', title: '设置' },
  { id: 'system', title: '服务与版本' },
]

const run = async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  const consoleErrors = []
  page.on('console', (m) => { if (m.type() === 'error' && !/WebSocket|404|500|Failed to load resource/i.test(m.text())) consoleErrors.push(m.text()) })
  page.on('pageerror', (e) => consoleErrors.push('PAGEERROR: ' + e.message))

  // ============ 预览模式 ============
  await page.goto(BASE + '/custom-page', { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-nid="home-title"]', { timeout: 15000 })

  // 清空上一轮残留的旧 custom_page / 草稿，确保加载全新的 19 页默认集
  await page.evaluate(() => {
    try { localStorage.removeItem('manju.customPage.v1') } catch {}
    try { const s = JSON.parse(localStorage.getItem('wn_settings') || '{}'); delete s.custom_page; localStorage.setItem('wn_settings', JSON.stringify(s)) } catch {}
  })
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('[data-nid="home-title"]')

  // P-1 沙盒左导航入口 + 镜像真实应用的「页面自定义」项
  const sideText = await page.locator('[data-nid="side"]').innerText()
  check('P-1 沙盒左导航渲染且含「页面自定义」入口', /页面自定义/.test(sideText), sideText.slice(0, 40))

  // P-2 首页复刻
  const homeTitle = await page.locator('[data-nid="home-title"]').textContent()
  check('P-2 沙盒复刻首页标题「创作驾驶舱」', /创作驾驶舱/.test(homeTitle || ''), homeTitle)

  // P-3 沙盒左导航完全镜像真实应用：7 组 + 19 个导航项
  const groupCount = await page.locator('[data-nid^="grp-"]').count()
  check('P-3 沙盒左导航含 7 个分组（镜像真实 SideBar）', groupCount === 7, 'count=' + groupCount)
  const navCount = await page.locator('[data-nid^="sid-"]').count()
  check('P-3 沙盒左导航含 19 个导航项（与真实应用一致）', navCount === 19, 'count=' + navCount)

  // 截图：首页预览全貌
  await annotate(page, ['[data-nid="side"]', '[data-nid="home-title"]', '[data-nid="home-stats"]'], 'A')
  await page.screenshot({ path: SHOTS + '/01_preview.png', fullPage: true })
  await clearAnnotate(page)

  // P-4 编辑首页标题实时生效（工具栏按钮须限定在 .cp-toolbar，避免与沙盒内同名按钮冲突）
  await page.locator('.cp-toolbar button', { hasText: '编辑当前页面' }).click()
  await page.waitForTimeout(250)
  await page.locator('[data-nid="home-title"]').click()
  await page.waitForTimeout(150)
  await page.locator('.pp-field textarea').first().fill('首页改了')
  await page.waitForTimeout(200)
  const homeEdited = await page.locator('[data-nid="home-title"]').textContent()
  check('P-4 首页标题可编辑（实时生效）', homeEdited === '首页改了', homeEdited)
  await page.locator('.cp-toolbar button', { hasText: '退出编辑' }).click()
  await page.waitForTimeout(200)

  // P-5 项目中心含真实内部页签（全部/短剧/回忆录/收藏/草稿）且可切换
  await page.locator('[data-nid="sid-projects"]').click()
  await page.waitForTimeout(250)
  const pjTitle = await page.locator('[data-nid="projects-title"]').textContent()
  check('P-5 项目中心页加载', /我的项目/.test(pjTitle || ''), pjTitle)
  const pjTabs = ['pj-all', 'pj-short', 'pj-memoir', 'pj-fav', 'pj-draft']
  let pjTabOk = true
  for (const t of pjTabs) {
    if (await page.locator(`[data-nid="tab-${t}"]`).count() === 0) { pjTabOk = false; break }
  }
  check('P-5 项目中心含 5 个真实内部页签（全部/短剧/回忆录/收藏/草稿）', pjTabOk)
  // 切换页签 → 对应子页面显示
  await page.locator('[data-nid="tab-pj-short"]').click()
  await page.waitForTimeout(200)
  const shortVisible = await page.locator('[data-nid="sub-pj-short"]').isVisible()
  check('P-5 点击页签切到「短剧」子页面（可见）', shortVisible)
  const pjBtn = await page.locator('.cp-stage button').count()
  const pjInp = await page.locator('.cp-input').count()
  check('P-5 项目中心含 按钮/输入框（可编辑元素）', pjBtn > 0 && pjInp > 0, `btn=${pjBtn} input=${pjInp}`)

  // P-6 弹窗开关（新建项目）
  await page.locator('[data-nid="pj-new-btn"]').click()
  await page.waitForTimeout(250)
  const dlgVisible = await page.locator('[data-nid="pj-dlg-new"]').isVisible()
  check('P-6 点击「新建项目」打开弹窗（模拟）', dlgVisible)
  await page.locator('[data-nid="pj-new-c"]').click()
  await page.waitForTimeout(200)
  check('P-6 弹窗可关闭', !(await page.locator('[data-nid="pj-dlg-new"]').isVisible()))

  // P-7 文生图预览弹窗（验证 tabPage 已修复：预构建 dialog 节点 dialogId 不再丢失）
  await page.locator('[data-nid="sid-txt2img"]').click()
  await page.waitForTimeout(250)
  await page.locator('[data-nid="t2i-pv-btn"]').click()
  await page.waitForTimeout(250)
  check('P-7 文生图「预览」打开弹窗', await page.locator('[data-nid="t2i-dlg"]').isVisible())
  await page.locator('[data-nid="t2i-pv-c"]').click()
  await page.waitForTimeout(200)
  check('P-7 文生图弹窗可关闭', !(await page.locator('[data-nid="t2i-dlg"]').isVisible()))

  // P-8 全局资产库「新建资产」弹窗
  await page.locator('[data-nid="sid-global-assets"]').click()
  await page.waitForTimeout(250)
  await page.locator('[data-nid="ga-new-btn"]').click()
  await page.waitForTimeout(250)
  check('P-8 全局资产库「新建资产」打开弹窗', await page.locator('[data-nid="ga-dlg-new"]').isVisible())
  await page.locator('[data-nid="ga-new-c"]').click()
  await page.waitForTimeout(200)
  check('P-8 全局资产库弹窗可关闭', !(await page.locator('[data-nid="ga-dlg-new"]').isVisible()))

  // P-9 推理监控「查看日志」弹窗
  await page.locator('[data-nid="sid-tasks"]').click()
  await page.waitForTimeout(250)
  await page.locator('[data-nid="tk-log-a1"]').click()
  await page.waitForTimeout(250)
  check('P-9 推理监控「查看日志」打开弹窗', await page.locator('[data-nid="tk-dlg-log"]').isVisible())
  await page.locator('[data-nid="tk-log-c"]').click()
  await page.waitForTimeout(200)
  check('P-9 推理监控弹窗可关闭', !(await page.locator('[data-nid="tk-dlg-log"]').isVisible()))

  // 截图：项目中心（含页签）——先回到项目中心页
  await page.locator('[data-nid="sid-projects"]').click()
  await page.waitForTimeout(250)
  await page.locator('[data-nid="tab-pj-all"]').click()
  await page.waitForTimeout(200)
  await annotate(page, ['[data-nid="projects-title"]', '[data-nid="tabrow"]', '[data-nid="pj-all-g"]'], 'B')
  await page.screenshot({ path: SHOTS + '/02_projects_tabs.png', fullPage: true })
  await clearAnnotate(page)

  // ============ M-1..M-19：19 个真实页面全部可加载 + 可编辑 ============
  for (const pg of PAGE_IDS) {
    await page.locator(`[data-nid="sid-${pg.id}"]`).click()
    await page.waitForTimeout(200)
    const titleVisible = await page.locator(`[data-nid="${pg.id}-title"]`).count()
    const t = titleVisible ? (await page.locator(`[data-nid="${pg.id}-title"]`).textContent()) : ''
    check(`M-${pg.id} 页面加载（页头标题存在）`, titleVisible > 0 && (t || '').trim().length > 0, t)
    const btnN = await page.locator('.cp-stage button').count()
    const inpN = await page.locator('.cp-input').count()
    check(`M-${pg.id} 含可编辑元素（按钮/输入框）`, btnN + inpN > 0, `btn=${btnN} input=${inpN}`)

    // 进入编辑 → 改页头标题 → 实时生效 → 退出
    await page.locator('.cp-toolbar button', { hasText: '编辑当前页面' }).click()
    await page.waitForTimeout(200)
    await page.locator(`[data-nid="${pg.id}-title"]`).click()
    await page.waitForTimeout(150)
    await page.locator('.pp-field textarea').first().fill(pg.id + '-edited')
    await page.waitForTimeout(200)
    const et = await page.locator(`[data-nid="${pg.id}-title"]`).textContent()
    check(`M-${pg.id} 页头标题可编辑（实时生效）`, et === pg.id + '-edited', et)
    await page.locator('.cp-toolbar button', { hasText: '退出编辑' }).click()
    await page.waitForTimeout(180)
  }

  // ============ 第 10 章：设置页「自定义」主题集成 ============
  await page.goto(BASE + '/settings', { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
  const accentCount = await page.locator('.accents').first().locator('.ac-item').count()
  check('T-1 配色主题含第 9 个「自定义」色块', accentCount === 9, 'count=' + accentCount)
  const lockedCount = await page.locator('.ac-item.locked').count()
  check('T-2 未保存时「自定义」色块置灰+锁', lockedCount >= 1)
  const lockGlyph = await page.locator('.ac-item.locked .lock').count()
  check('T-2 锁定图标存在', lockGlyph >= 1)

  // 注入已保存的自定义主题（模拟用户在沙盒保存过），重载使设置页读到
  await page.evaluate(() => {
    const raw = localStorage.getItem('wn_settings') || '{}'
    const s = JSON.parse(raw)
    s.custom_theme = {
      version: 1, savedAt: '2026-08-14 20:40:00', name: '我的自定义主题',
      previewColor: '#123456',
      variables: { '--card': '#101a2b' },
      rules: [{ selector: '.stat-card', style: { background: '#123456' } }],
    }
    localStorage.setItem('wn_settings', JSON.stringify(s))
  })
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
  const lockedAfter = await page.locator('.ac-item.locked').count()
  check('T-3 保存后「自定义」色块解锁（可选中）', lockedAfter === 0)

  // T-4 选中自定义 → 真实页面应用自定义样式
  await page.locator('.ac-item', { hasText: '自定义' }).click()
  await page.waitForTimeout(400)
  const tCustomClass = await page.evaluate(() => document.documentElement.classList.contains('t-custom'))
  check('T-4 选中自定义 → html 挂 t-custom', tCustomClass)
  await page.goto(BASE + '/', { waitUntil: 'networkidle' })
  await page.waitForTimeout(600)
  const statBg = await page.evaluate(() => {
    const el = document.querySelector('.stat-card')
    return el ? getComputedStyle(el).backgroundColor : ''
  })
  check('T-4 自定义规则作用于真实页面 .stat-card', statBg === 'rgb(18, 52, 86)', statBg)
  const varOverride = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--card').trim())
  check('T-4 自定义变量覆盖 --card 全局生效', varOverride === '#101a2b', varOverride)

  // T-5 单选互斥：选 P5 后自定义取消
  await page.goto(BASE + '/settings', { waitUntil: 'networkidle' })
  await page.waitForTimeout(400)
  await page.locator('.ac-item', { hasText: 'P5 怪盗' }).click()
  await page.waitForTimeout(400)
  const mutually = await page.evaluate(() => {
    const cl = document.documentElement.classList
    return { hasP5: cl.contains('t-p5'), hasCustom: cl.contains('t-custom') }
  })
  check('T-5 选 P5 后取消自定义（互斥）', mutually.hasP5 && !mutually.hasCustom, JSON.stringify(mutually))

  // T-6 切走后注入样式标签被移除（无残留）
  const residual = await page.evaluate(() => !document.getElementById('custom-theme-vars') && !document.getElementById('custom-theme-rules'))
  check('T-6 切走自定义 → 注入 style 标签已移除（无残留）', residual)

  // T-7 应用主题不修改沙盒数据（custom_page 未被主题改写）
  await page.goto(BASE + '/settings', { waitUntil: 'networkidle' })
  await page.waitForTimeout(400)
  const cpBefore = await page.evaluate(() => { try { return JSON.stringify(JSON.parse(localStorage.getItem('wn_settings')).custom_page) } catch { return null } })
  await page.locator('.ac-item', { hasText: '自定义' }).click()
  await page.waitForTimeout(400)
  const cpMid = await page.evaluate(() => { try { return JSON.stringify(JSON.parse(localStorage.getItem('wn_settings')).custom_page) } catch { return null } })
  check('T-7 应用主题不修改沙盒数据（custom_page 未被主题改写）', cpBefore !== null && cpBefore === cpMid, 'beforeLen=' + (cpBefore ? cpBefore.length : 0) + ' midLen=' + (cpMid ? cpMid.length : 0))

  // 进入沙盒编辑：改文本 + 改具体颜色
  await page.goto(BASE + '/custom-page', { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-nid="home-title"]')
  await page.locator('.cp-toolbar button', { hasText: '编辑当前页面' }).click()
  await page.waitForTimeout(300)
  await page.locator('[data-nid="home-title"]').click()
  await page.waitForTimeout(200)
  await page.locator('.pp-field textarea').first().fill('T7隔离文本')
  await page.waitForTimeout(150)
  const t7 = await page.evaluate(() => ({
    hasCustom: document.documentElement.classList.contains('t-custom'),
    hasVars: !!document.getElementById('custom-theme-vars'),
    card: getComputedStyle(document.documentElement).getPropertyValue('--card').trim(),
  }))
  check('T-7 沙盒编辑不影响已应用主题（t-custom + 注入样式仍在）', t7.hasCustom && t7.hasVars, JSON.stringify(t7))

  // T-8 文本改动仅沙盒生效；样式改动可进主题包
  await page.locator('.cp-toolbar').getByRole('button', { name: '保存', exact: true }).click()
  await page.waitForTimeout(400)
  await page.goto(BASE + '/', { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
  const realTitle = await page.locator('.p5-title').first().textContent().catch(() => '')
  check('T-8 文本改动仅沙盒生效（真实首页 .p5-title 未变）', !/T7隔离文本/.test(realTitle || ''), realTitle)

  // 样式改动进主题包：标题阴影含具体色 → 保存为自定义主题 → 主题包规则含该选择器
  await page.goto(BASE + '/custom-page', { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-nid="home-title"]')
  await page.locator('.cp-toolbar button', { hasText: '编辑当前页面' }).click()
  await page.waitForTimeout(300)
  await page.locator('[data-nid="home-title"]').click()
  await page.waitForTimeout(200)
  const shadowField8 = page.locator('.pp-field', { has: page.locator('label', { hasText: '阴影' }) }).locator('input')
  await shadowField8.fill('0 0 0 4px #33cc99')
  await page.waitForTimeout(200)
  await page.locator('.cp-toolbar button', { hasText: '保存为自定义主题' }).click()
  await page.waitForTimeout(400)
  const themePkg = await page.evaluate(() => { try { return JSON.parse(localStorage.getItem('wn_settings')).custom_theme } catch { return null } })
  const inPkg = !!(themePkg && themePkg.rules && themePkg.rules.some((r) => r.selector === '.p5-title' && /33cc99/i.test(JSON.stringify(r.style))))
  check('T-8 样式改动可进主题包（.p5-title 规则含具体色）', inPkg, inPkg ? 'ok' : JSON.stringify(themePkg && themePkg.rules))

  // 截图：自定义主题应用后首页
  await page.goto(BASE + '/', { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
  await annotate(page, ['.stat-card'], 'E')
  await page.screenshot({ path: SHOTS + '/05_custom_applied.png', fullPage: true })
  await clearAnnotate(page)

  // 控制台错误
  check('FC-04 无 pageerror / console error', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))

  await browser.close()

  const failed = results.filter((r) => !r.ok)
  console.log(`\n==== 验收汇总：${results.length - failed.length}/${results.length} PASS ====`)
  if (failed.length) {
    console.log('FAILED:')
    failed.forEach((f) => console.log(' - ' + f.name + (f.detail ? ' :: ' + f.detail : '')))
    process.exit(1)
  }
  process.exit(0)
}

run().catch((e) => { console.error('RUNNER ERROR', e); process.exit(2) })
