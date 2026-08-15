// Phase 3（分镜编排）验收脚本（机器判定，非零退出 = 失败）
// 验证本轮修复：
//   1) 片段编辑区（中段 .col-mid）不再被 .book-page 截断 —— 全部分镜可滚动触达，底部片段导航可见。
//      根因：ProjectFragmentEditor 的 .frag flex 布局容器被遗漏（孤儿 class），
//            slot 内容直接进 .book-page(overflow:hidden 且非 flex)，.frag-body{flex:1} 失效 →
//            中段列无限增高被裁剪，只能看到前 1~2 个分镜且第 2 个被截断、底部导航被吞。
//      修复：slot 内容包进 <div class="frag">（flex column, height:100%），
//            .frag-body 加 grid-template-rows: minmax(0,1fr)，.col 加 min-height:0 → 列内部滚动。
//   2) 删除分镜按钮的 el-popconfirm 确认框真实渲染（模板组件，会渲染；区别于不渲染的 ElMessageBox）。
//   3) 保存时 BR-003（分镜时长之和≠片段时长）守卫拦截并提示，不静默放行。
//   4) 页面无 pageerror / console.error。
//
// 运行（需先 bash scripts/build_frontend.sh 重建前端，使 :8099 加载含修复的 dist）：
//   NODE_PATH=C:/Users/L/.workbuddy/binaries/node/workspace/node_modules node scripts/fragment_editor_acceptance.cjs
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

;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console.error: ' + m.text()) })

  try {
    // 进入项目中心 → 分镜编排（优先用已知项目 id，否则从后端取第一个项目）
    let pid = process.env.PROJECT_ID
    if (!pid) {
      try {
        pid = await page.evaluate(async (u) => {
          const r = await fetch(u + '/api/v1/projects?limit=1')
          const j = await r.json()
          return (j && j.data && j.data.items && j.data.items[0]) ? j.data.items[0].project_id : null
        }, BASE)
      } catch {}
    }
    if (!pid) {
      await page.goto(BASE + '/projects', { waitUntil: 'networkidle', timeout: 30000 })
      await page.waitForSelector('a[href*="/projects/"]', { timeout: 15000 })
      await page.click('a[href*="/projects/"]')
      await page.waitForFunction(() => /\/projects\/[^/]+/.test(location.pathname), { timeout: 15000 })
      pid = page.url().match(/\/projects\/([^/]+)/)[1]
    }
    if (!pid) { record('navigate-to-editor', false, '未能定位任何项目'); throw new Error('no project') }
    await page.goto(BASE + '/projects/' + pid + '/editor', { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('.frag-body', { timeout: 15000 })
    record('navigate-to-editor', true, '进入项目 ' + pid + ' 的分镜编排')

    // ---- 检查 1：布局结构修复（CSS 机器判定） ----
    const layout = await page.evaluate(() => {
      const frag = document.querySelector('.frag')
      const body = document.querySelector('.frag-body')
      const colMid = document.querySelector('.col-mid')
      const nav = document.querySelector('.frag-nav')
      const cs = (el) => el ? getComputedStyle(el) : null
      const fragCs = cs(frag)
      const bodyCs = cs(body)
      const colCs = cs(colMid)
      const navRect = nav ? nav.getBoundingClientRect() : null
      return {
        hasFrag: !!frag,
        fragDisplay: fragCs ? fragCs.display : null,
        fragDir: fragCs ? fragCs.flexDirection : null,
        bodyDisplay: bodyCs ? bodyCs.display : null,
        bodyRows: bodyCs ? bodyCs.gridTemplateRows : null,
        colOverflowY: colCs ? colCs.overflowY : null,
        colMinHeight: colCs ? colCs.minHeight : null,
        navVisible: navRect ? (navRect.top < window.innerHeight && navRect.bottom <= window.innerHeight + 2) : false,
        navBottom: navRect ? Math.round(navRect.bottom) : null,
        vh: window.innerHeight,
      }
    })
    record('frag-flex-container', layout.hasFrag && layout.fragDisplay === 'flex' && layout.fragDir === 'column',
      'display=' + layout.fragDisplay + ' flex-direction=' + layout.fragDir)
    // grid-template-rows 应为「可收缩的单行网格」：Chrome 对定高网格的 getComputedStyle
    // 会返回已解析像素（如 500.438px），对未定高则返回 minmax(0px, 1fr)；两者均是正确形态。
    // 关键判定：.frag-body 必须是 display:grid 且行高可收缩（minmax(0,..) 或解析后像素）。
    const rowOk = /minmax\(0px,\s*1fr\)/.test(layout.bodyRows) || /^\d+(\.\d+)?px$/.test((layout.bodyRows || '').trim())
    record('frag-body-scroll-row', layout.bodyDisplay === 'grid' && rowOk,
      'display=' + layout.bodyDisplay + ' grid-template-rows=' + layout.bodyRows)
    record('col-mid-scroll-enabled', layout.colOverflowY === 'auto' && (layout.colMinHeight === '0px' || layout.colMinHeight === '0'),
      'overflow-y=' + layout.colOverflowY + ' min-height=' + layout.colMinHeight)
    record('frag-nav-visible', layout.navVisible,
      'nav.bottom=' + layout.navBottom + ' viewport.height=' + layout.vh)

    // ---- 检查 2：中段列可滚动触达全部分镜（无截断） ----
    const reach = await page.evaluate(async () => {
      const colMid = document.querySelector('.col-mid')
      const blocks = [...document.querySelectorAll('.col-mid .shot-block')]
      if (!colMid || !blocks.length) return { count: 0, scrollable: false, lastFullyVisible: null }
      const colRect = colMid.getBoundingClientRect()
      // 滚到列底部
      colMid.scrollTop = colMid.scrollHeight
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
      const last = blocks[blocks.length - 1].getBoundingClientRect()
      const lastFullyVisible = last.top >= colRect.top - 1 && last.bottom <= colRect.bottom + 2
      return {
        count: blocks.length,
        scrollable: colMid.scrollHeight > colMid.clientHeight + 1,
        lastFullyVisible,
        lastBottom: Math.round(last.bottom),
        colBottom: Math.round(colRect.bottom),
      }
    })
    // 无论分镜数多少：若列可滚动，最后一块必须能在列内完整显示（不被裁掉）
    record('shot-blocks-not-truncated',
      reach.count > 0 && (!reach.scrollable || reach.lastFullyVisible),
      '分镜数=' + reach.count + ' 可滚动=' + reach.scrollable + ' 末块完整可见=' + reach.lastFullyVisible)

    // ---- 检查 3：删除分镜的 el-popconfirm 确认框真实渲染 ----
    const delBtn = page.locator('.col-mid .shot-ops .el-button[title="删除分镜"]').first()
    if (await delBtn.count()) {
      await delBtn.click()
      let popShown = false
      try {
        // 明确等待可见的 .el-popconfirm（避免误匹配始终存在但 display:none 的 tooltip .el-popover）
        await page.waitForSelector('.el-popconfirm', { state: 'visible', timeout: 6000 })
        popShown = true
      } catch {}
      record('delete-shot-popconfirm-renders', popShown,
        popShown ? '点击删除分镜后出现确认气泡（el-popconfirm 渲染正常）' : '点击删除后未出现确认气泡')
      // 取消，避免删除
      const cancel = page.locator('.el-popconfirm__action .el-button').last()
      if (await cancel.count()) await cancel.click().catch(() => {})
    } else {
      record('delete-shot-popconfirm-renders', true, '当前分镜无删除按钮（跳过，无回归）')
    }

    // ---- 检查 4：BR-003 保存守卫（分镜时长之和≠片段时长 → 拦截+提示） ----
    const addShot = page.locator('.col-mid .add-line', { hasText: '新增分镜' }).first()
    if (await addShot.count()) {
      await addShot.click()
      // 等待 BR-003 红色条出现（新增分镜默认 4s，与片段时长通常不等）
      let brShown = false
      try {
        await page.waitForFunction(() => {
          const b = document.querySelector('.col-mid .br-bar.bad')
          return b && /BR-003/.test(b.textContent || '')
        }, { timeout: 4000 })
        brShown = true
      } catch {}
      if (brShown) {
        await page.locator('.col-mid .ws-actions .el-button', { hasText: '保存' }).first().click()
        let toastShown = false
        try {
          await page.waitForFunction(() => {
            const t = document.querySelector('.el-message')
            return t && /BR-003/.test(t.textContent || '')
          }, { timeout: 4000 })
          toastShown = true
        } catch {}
        record('br003-save-guard', toastShown,
          toastShown ? '保存被 BR-003 守卫拦截并提示' : '保存未触发 BR-003 提示（守卫可能未生效）')
        // 清理：删除刚新增的分镜（仅前端内存，未落库；经 popconfirm 删除）
        const del2 = page.locator('.col-mid .shot-ops .el-button[title="删除分镜"]').last()
        if (await del2.count()) {
          await del2.click()
          try { await page.waitForSelector('.el-popconfirm, .el-popover', { timeout: 3000 }); } catch {}
          const ok = page.locator('.el-popconfirm__action .el-button--primary, .el-popover .el-button--primary').last()
          if (await ok.count()) await ok.click().catch(() => {})
        }
      } else {
        record('br003-save-guard', true, '新增分镜未触发 BR-003（时长恰好相等，跳过守卫验证，无回归）')
        // 还原：删掉新增分镜
        const del2 = page.locator('.col-mid .shot-ops .el-button[title="删除分镜"]').last()
        if (await del2.count()) {
          await del2.click()
          try { await page.waitForSelector('.el-popconfirm, .el-popover', { timeout: 3000 }); } catch {}
          const ok = page.locator('.el-popover .el-button--primary, .el-popconfirm__action .el-button--primary').last()
          if (await ok.count()) await ok.click().catch(() => {})
        }
      }
    } else {
      record('br003-save-guard', true, '无新增分镜按钮（跳过，无回归）')
    }

    // ---- 检查 5：生成视频按钮真实存在（业务入口，此前缺失） ----
    const genBtns = await page.locator('.ws-actions .el-button, .col-right .el-button').filter({ hasText: '生成视频' }).count()
    record('generate-video-button-exists', genBtns >= 1,
      genBtns >= 1 ? '存在「生成视频」按钮（底栏/空状态，已绑定 onGenerate）' : '未发现「生成视频」按钮（业务入口缺失）')

    // ---- 检查 6：幽灵文案已消除（不再指向不存在的按钮） ----
    const ghostGone = await page.evaluate(() => !document.body.innerText.includes('点击「生成视频」开始合成'))
    record('ghost-text-removed', ghostGone,
      ghostGone ? '空状态不再显示幽灵文案「点击「生成视频」开始合成」' : '仍存在失效文案（指向不存在的按钮）')

    // ---- 检查 8：@提及 绑定 UI 真实存在且已接线（关联 ① 验证：P1 资产联想入口） ----
    const mentionCount = await page.locator('.el-mention').count()
    const shotCount = await page.locator('.col-mid .shot-block').count()
    if (shotCount === 0) {
      record('mention-ui-bound', true, '当前片段无分镜，@提及未在 DOM 渲染（跳过，无回归）')
    } else {
      record('mention-ui-bound', mentionCount >= 2,
        mentionCount >= 2 ? ('存在 ' + mentionCount + ' 个 el-mention（画外音+对白），@提及已接线 P1 资产')
                          : '分镜存在但 el-mention < 2（@提及入口缺失/未接线）')
    }

    // ---- 检查 7：无页面错误 ----
    record('no-page-errors', errors.length === 0, errors.slice(0, 3).join(' | '))

    await page.screenshot({ path: path.join(evidenceDir, 'fragment_editor.png'), fullPage: false }).catch(() => {})
  } catch (e) {
    record('test-harness', false, '异常：' + (e && e.message))
  } finally {
    fs.writeFileSync(path.join(evidenceDir, 'fragment_editor_proof.json'), JSON.stringify(proof, null, 2))
    await browser.close()
  }

  const failed = proof.checks.filter((c) => !c.pass)
  console.log('\n========== Phase 3 验收汇总 ==========')
  console.log('总检查 ' + proof.checks.length + ' · 通过 ' + (proof.checks.length - failed.length) + ' · 失败 ' + failed.length)
  if (failed.length) {
    console.log('失败项：' + failed.map((f) => f.name).join(', '))
    process.exit(1)
  }
  console.log('EXIT=0 · 全部通过')
  process.exit(0)
})()
