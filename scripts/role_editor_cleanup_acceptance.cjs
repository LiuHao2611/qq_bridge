#!/usr/bin/env node
/**
 * 角色编辑弹窗去重验收脚本
 * 验证：身份卡内不再重复展示角色封面图（人脸缩略图）和 512 维状态徽章
 */
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const BASE = 'http://127.0.0.1:8099'
const PID = '04ac82eb-e65a-4e1a-b800-44d86bdaf89f'
const EVIDENCE_DIR = path.join(__dirname, '..', 'docs', 'evidence')

function assert(cond, msg) {
  if (!cond) throw new Error('ASSERT FAIL: ' + msg)
}

async function main() {
  fs.mkdirSync(EVIDENCE_DIR, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()

  try {
    // 1) 打开项目资产页
    await page.goto(`${BASE}/projects/${PID}/assets`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('.asset-card', { timeout: 15000 })

    // 2) 点击第一个角色的编辑按钮
    const editBtn = await page.locator('.asset-card').first().locator('button[title="编辑角色"]').first()
    await editBtn.waitFor({ state: 'visible', timeout: 10000 })
    await editBtn.click()

    // 3) 等待弹窗
    const dialog = page.locator('.role-editor-dialog')
    await dialog.waitFor({ state: 'visible', timeout: 15000 })

    // 4) 左侧封面大图应存在（这是唯一的角色封面展示）
    const cover = dialog.locator('.re-cover')
    assert(await cover.count() === 1, '左侧角色封面图应存在且唯一')

    // 5) 身份卡内不应再出现重复的人脸缩略图
    const thumbCount = await dialog.locator('.re-idcard .re-idcard-thumb').count()
    assert(thumbCount === 0, `身份卡内不应有人脸缩略图，实际 ${thumbCount}`)

    // 6) 身份卡内不应再出现 512 维状态徽章（阶段详情和参考图集已提供）
    const vecBadgeCount = await dialog.locator('.re-idcard .re-idcard-vec').count()
    assert(vecBadgeCount === 0, `身份卡内不应有 512 维徽章，实际 ${vecBadgeCount}`)

    // 7) 截图留证
    await dialog.screenshot({ path: path.join(EVIDENCE_DIR, 'role_editor_clean.png') })

    const result = {
      passed: true,
      pid: PID,
      checks: {
        coverUnique: true,
        idCardThumbCount: thumbCount,
        idCardVecBadgeCount: vecBadgeCount,
      },
      timestamp: new Date().toISOString(),
    }
    fs.writeFileSync(path.join(EVIDENCE_DIR, 'role_editor_cleanup_proof.json'), JSON.stringify(result, null, 2))
    console.log('✅ 角色编辑弹窗去重验收通过')
    console.log(JSON.stringify(result, null, 2))
  } catch (e) {
    await page.screenshot({ path: path.join(EVIDENCE_DIR, 'role_editor_cleanup_fail.png'), fullPage: true })
    const fail = { passed: false, error: e.message, timestamp: new Date().toISOString() }
    fs.writeFileSync(path.join(EVIDENCE_DIR, 'role_editor_cleanup_proof.json'), JSON.stringify(fail, null, 2))
    console.error('❌', e.message)
    process.exit(1)
  } finally {
    await browser.close()
  }
}

main().catch((e) => { console.error(e); process.exit(1) })
