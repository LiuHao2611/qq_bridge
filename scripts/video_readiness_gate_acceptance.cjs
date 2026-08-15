#!/usr/bin/env node
/**
 * 视频生成前置闸门 · 正式验收（用户诉求：1660Ti 6GB 跑不动视频，别白等 30 分钟）。
 *
 * 机器判定不变量：
 *   A. 生产页 engine-bar 必须渲染「视频算力」状态芯片，其文本（充足 / 不足）
 *      必须与 /api/v1/video/readiness.feasible 一致（被动诚实展示，分发前即知）。
 *   B. feasible===false 时，点击「一键分发全部」必须触发含原因文本的 confirm 警告；
 *      用户取消后，【绝不允许】向后端发起任何 /segments/.../generate 或 /regenerate
 *      POST（否则用户仍会白等 30 分钟才 OOM）—— 这是本验收的核心红线。
 *
 * 运行前置：后端 :7788（含 /video/readiness）+ 前端 :8099（已构建含本闸门）已启动。
 *   需 playwright：可用托管 workspace 的副本，例如
 *   NODE_PATH=C:/Users/L/.workbuddy/binaries/node/workspace/node_modules \
 *     node scripts/video_readiness_gate_acceptance.cjs
 *   PID=xxxx node scripts/video_readiness_gate_acceptance.cjs   # 指定项目
 *
 * 退出码：0 = 通过；非 0 = 违反不变量 / 环境未就绪。
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

  const evidence = { base: BASE, pid: PID, invariants: {}, violations: [] }
  let dialogMsg = null
  const generatePosts = []

  // 拦截：任何 /segments/.../generate 或 /regenerate 的 POST 都记录（红线 B）
  page.on('request', (req) => {
    const u = req.url()
    if (req.method() === 'POST' && /\/segments\/[^/]+\/(generate|regenerate)$/.test(u)) {
      generatePosts.push(u)
    }
  })
  // 拦截 confirm 对话框：记录原因并「取消」（模拟用户不想白等）
  page.on('dialog', async (dialog) => {
    dialogMsg = dialog.message()
    await dialog.dismiss() // 取消 = 停止分发
  })

  try {
    // 0) 读后端真实就绪状态（机器判定基准）
    const readiness = await page.evaluate(async () => {
      const r = await fetch('/api/v1/video/readiness')
      const j = await r.json()
      return j.data || null
    })
    assert(readiness && typeof readiness.feasible === 'boolean',
      '后端 /video/readiness 应返回 { feasible }')
    evidence.readiness = readiness

    // 1) 打开生产页
    await page.goto(`${BASE}/projects/${PID}/production`, { waitUntil: 'networkidle', timeout: 30000 })

    // 2) 等待 engine-bar 的「视频算力」芯片出现
    const chip = page.locator('.engine-bar .chip', { hasText: '视频算力' }).first()
    await chip.waitFor({ state: 'visible', timeout: 20000 })
    const chipText = (await chip.innerText()).replace(/\s+/g, ' ')

    // ---- 不变量 A：芯片文本与 feasible 一致 ----
    if (readiness.feasible) {
      assert(chipText.includes('充足'), `feasible=true 但芯片显示「${chipText}」(应含「充足」)`)
    } else {
      assert(chipText.includes('不足'), `feasible=false 但芯片显示「${chipText}」(应含「不足」)`)
    }
    evidence.invariants.A = { pass: true, chipText, feasible: readiness.feasible }

    // ---- 不变量 B：feasible=false 时，分发必须弹警告且取消后不发 /generate ----
    if (!readiness.feasible) {
      // 点击「一键分发全部」
      const dispatchBtn = page.getByText('一键分发全部', { exact: true }).first()
      await dispatchBtn.waitFor({ state: 'visible', timeout: 10000 })
      await dispatchBtn.click()

      // 等待 confirm 对话框出现并被取消
      const deadline = Date.now() + 15000
      while (dialogMsg === null && Date.now() < deadline) {
        await page.waitForTimeout(200)
      }
      assert(dialogMsg !== null, 'feasible=false 时点击分发应弹出 confirm 警告')
      assert(/视频生成|显存|OOM|失败/.test(dialogMsg || ''),
        `confirm 文本应含视频生成不可行原因，实际：「${dialogMsg}」`)
      evidence.dialogMessage = dialogMsg

      // 等待前端消化取消逻辑
      await page.waitForTimeout(1500)

      assert(generatePosts.length === 0,
        `取消后不应发起任何 /generate 或 /regenerate POST，实际发起 ${generatePosts.length} 次: ${generatePosts.join(' | ')}`)
      evidence.invariants.B = { pass: true, generatePosts: generatePosts.length, cancelled: true }
    } else {
      // feasible=true：B 不适用，标记为 N/A（不扣分）
      evidence.invariants.B = { pass: true, skipped: true, reason: 'feasible=true，无需拦截' }
    }

    evidence.ok = true
    const out = path.join(EVIDENCE_DIR, 'video_readiness_gate_proof.json')
    fs.writeFileSync(out, JSON.stringify(evidence, null, 2))
    console.log('PASS: 视频生成前置闸门验收通过')
    console.log('  ' + JSON.stringify(evidence.invariants))
    await browser.close()
    process.exit(0)
  } catch (e) {
    evidence.ok = false
    evidence.error = e.message
    evidence.violations.push(e.message)
    const out = path.join(EVIDENCE_DIR, 'video_readiness_gate_proof.json')
    fs.writeFileSync(out, JSON.stringify(evidence, null, 2))
    console.error('FAIL: ' + e.message)
    await browser.close().catch(() => {})
    process.exit(1)
  }
}

main().catch((e) => { console.error(e); process.exit(1) })
