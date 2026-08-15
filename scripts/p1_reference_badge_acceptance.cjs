#!/usr/bin/env node
/**
 * P1 资产初始化 · 参考图集徽标诚实性验收（用户红线：无图却显示 512 维）。
 *
 * 不变量：参考图（标准肖像 + 4 张参考图）的 <img> 若加载失败（文件缺失 / 404），
 * 其对应的 .re-vec-badge 文本【绝不允许】为 "512维✓"；必须是 "图片缺失" 或 "无向量"。
 * 这正是用户投诉「角色卡连图片都没有，却显示有 512 维」要杜绝的情形。
 *
 * 运行前置：后端 :7788 + 前端 :8099 已在运行（web_app.py 反代 /api/v1）。
 *   node scripts/p1_reference_badge_acceptance.cjs
 *   PID=xxxx node scripts/p1_reference_badge_acceptance.cjs   # 指定项目
 *
 * 退出码：0 = 通过（无「无图却标 512 维」的矛盾）；非 0 = 发现矛盾 / 环境未就绪。
 */
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const BASE = 'http://127.0.0.1:8099'
const PID = process.env.PID || '04ac82eb-e65a-4e1a-b800-44d86bdaf89f'
const EVIDENCE_DIR = path.join(__dirname, '..', 'docs', 'evidence')

function assert(cond, msg) {
  if (!cond) throw new Error('ASSERT FAIL: ' + msg)
}

async function main() {
  fs.mkdirSync(EVIDENCE_DIR, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  const violations = []

  try {
    // 1) 打开项目资产页
    await page.goto(`${BASE}/projects/${PID}/assets`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('.asset-card', { timeout: 15000 })

    // 2) 点击第一个角色的编辑按钮
    const editBtn = await page.locator('.asset-card').first().locator('button[title="编辑角色"]').first()
    await editBtn.waitFor({ state: 'visible', timeout: 10000 })
    await editBtn.click()

    // 3) 等待角色编辑弹窗与参考图集徽标
    const dialog = page.locator('.role-editor-dialog')
    await dialog.waitFor({ state: 'visible', timeout: 15000 })
    await page.waitForSelector('.re-vec-badge', { timeout: 15000 })

    // 4) 遍历每个参考图容器（标准肖像 + 4 张缩略图），核对「图坏 ≠ 512维✓」
    const containers = await page.locator('.re-refset-portrait, .re-refset-thumb').count()
    assert(containers > 0, '应存在至少一个参考图容器（标准肖像或参考图）')

    for (let i = 0; i < containers; i++) {
      const c = page.locator('.re-refset-portrait, .re-refset-thumb').nth(i)
      const imgInfo = await c.locator('img').first().evaluate((el) => ({
        src: el.getAttribute('src') || '',
        broken: el.complete && el.naturalWidth === 0,
      })).catch(() => ({ src: '', broken: false }))
      const badgeText = (await c.locator('.re-vec-badge').first().innerText().catch(() => '')) || ''
      if (imgInfo.broken && badgeText.includes('512维')) {
        violations.push({ index: i, src: imgInfo.src, badge: badgeText })
      }
    }

    assert(violations.length === 0,
      '发现「图片缺失却标 512 维✓」的矛盾：' + JSON.stringify(violations))

    // 5) 额外：所有徽标文本都必须属于诚实集合（512维✓ / 图片缺失 / 无向量）
    const allBadges = await page.locator('.re-vec-badge').allInnerTexts()
    const HONEST = ['512维✓', '图片缺失', '无向量']
    for (const t of allBadges) {
      assert(HONEST.some((h) => t.trim().includes(h)),
        `徽标文本 "${t}" 不属于诚实集合 ${JSON.stringify(HONEST)}`)
    }

    // 6) 截图留证
    await dialog.screenshot({ path: path.join(EVIDENCE_DIR, 'p1_reference_badge.png') })

    const result = {
      passed: true,
      pid: PID,
      containers,
      badgeCount: allBadges.length,
      badges: allBadges,
      violations,
      timestamp: new Date().toISOString(),
    }
    fs.writeFileSync(path.join(EVIDENCE_DIR, 'p1_reference_badge_proof.json'), JSON.stringify(result, null, 2))
    console.log('✅ P1 参考图集徽标诚实性验收通过（无「无图却标512维」矛盾）')
    console.log(JSON.stringify(result, null, 2))
  } catch (e) {
    await page.screenshot({ path: path.join(EVIDENCE_DIR, 'p1_reference_badge_fail.png'), fullPage: true }).catch(() => {})
    const fail = { passed: false, error: e.message, violations, timestamp: new Date().toISOString() }
    fs.writeFileSync(path.join(EVIDENCE_DIR, 'p1_reference_badge_proof.json'), JSON.stringify(fail, null, 2))
    console.error('❌', e.message)
    process.exit(1)
  } finally {
    await browser.close()
  }
}

main().catch((e) => { console.error(e); process.exit(1) })
