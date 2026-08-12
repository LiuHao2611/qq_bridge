// 前端五阶段流程 UI 自动化测试（Playwright）
// 覆盖本次改动的接线：Phase1 真实上传→缩略图、Phase3 @提及→引用资产、流程导航、生成/合成按钮
// 不重复跑 ComfyUI 长任务（由 pipeline_e2e.mjs 覆盖后端生成逻辑）
import { chromium } from 'playwright'
import fs from 'fs'
import path from 'path'

const WEB = 'http://127.0.0.1:8099'
const API = 'http://127.0.0.1:7788/api/v1'

// 强制 D 盘 tmp — 禁止 chromium 把 userDataDir 写 C:\Users\...\AppData\Local\Temp（这会撑爆用户回收站）
const TMP_BASE = 'D:/AI/manju_agent/.tmp'
fs.mkdirSync(path.join(TMP_BASE, 'playwright'), { recursive: true })
const userDataDir = path.join(TMP_BASE, 'playwright', `ui_flow_${Date.now()}_${process.pid}`)
fs.mkdirSync(userDataDir, { recursive: true })
process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(TMP_BASE, 'playwright-browsers')
process.env.TMPDIR = TMP_BASE
process.env.TEMP = TMP_BASE
process.env.TMP = TMP_BASE
const results = []
let PASS = 0, FAIL = 0
function ok(name){ PASS++; results.push('PASS  '+name) }
function bad(name, e){ FAIL++; results.push('FAIL  '+name+' :: '+(e?.message||e)) }
async function api(path, opts={}){
  const r = await fetch(API+path, opts)
  const b = await r.json().catch(()=>null)
  if (!r.ok) throw new Error('HTTP '+r.status+' '+(b?.message||''))
  if (b && 'code' in b){ if (b.code!==0) throw new Error('code='+b.code+' '+(b.message||'')); return b.data }
  return b
}
const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC'

async function main(){
  // 先建一个测试项目（拿到 pid）
  let pid
  try {
    const p = await api('/projects', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ name:'UI测试_'+Date.now(), resolution:'720p', ratio:'16:9' }) })
    pid = p.project?.project_id
  } catch(e){ bad('创建测试项目', e); return finish() }
  if (!pid){ bad('创建测试项目', new Error('无 pid')); return finish() }

  const browser = await chromium.launch({ env: process.env })
  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  try {
    // ---- 默认阶段：新建项目 → 入口自动落到第一阶段（资产初始化）----
    await page.goto(WEB + '/projects/' + pid, { waitUntil: 'domcontentloaded' })
    await page.waitForURL(/\/projects\/[^/]+\/assets$/, { timeout: 8000 }).catch(() => {})
    let url0 = page.url()
    if (/\/projects\/[^/]+\/assets$/.test(url0)) ok('新建项目→默认进入资产初始化(第一阶段)')
    else bad('新建项目默认第一阶段', new Error('实际路由=' + url0))

    // ---- 点「资产初始化」步骤：停留在项目内（不跳出到 /global-assets）----
    await page.goto(WEB + '/projects/' + pid + '/editor', { waitUntil: 'domcontentloaded' })
    await page.locator('.flow-nav .step').first().click()
    await page.waitForTimeout(700)
    let urlA = page.url()
    if (/\/projects\/[^/]+\/assets$/.test(urlA) && !urlA.includes('/global-assets')) ok('点「资产初始化」停留在项目内(不出项目)')
    else bad('点资产步骤不出项目', new Error('实际路由=' + urlA))

    // ---- 旧项目→进行中阶段：建第二个项目并上传本集资产（Phase1 完成 → current_phase=2）----
    const p2 = await api('/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: '旧项目_' + Date.now(), resolution: '720p', ratio: '16:9' }) })
    const pid2 = p2.project?.project_id
    if (pid2) {
      const fd2 = new FormData()
      fd2.append('file', new Blob([Buffer.from(PNG_B64, 'base64')], { type: 'image/png' }), 'b.png')
      fd2.append('name', '旧项目角色'); fd2.append('type', 'role')
      await fetch(API + '/projects/' + pid2 + '/assets', { method: 'POST', body: fd2 })
      const gp = await api('/projects/' + pid2)
      await page.goto(WEB + '/projects/' + pid2, { waitUntil: 'domcontentloaded' })
      await page.waitForURL(/\/(planning|editor|assets)$/, { timeout: 8000 }).catch(() => {})
      let url2 = page.url()
      if (/\/projects\/[^/]+\/planning$/.test(url2)) ok('打开旧项目→默认进入进行中阶段(分场拆解)')
      else bad('旧项目默认进行中阶段', new Error('current_phase=' + gp.project.current_phase + ' 实际路由=' + url2))
    } else bad('创建旧项目', new Error('无 pid2'))

    // ---- Phase 1：全局资产库 真实上传 → 缩略图回流 ----
    await page.goto(WEB + '/global-assets', { waitUntil:'networkidle' })
    const sample = 'tests/ui_sample.png'
    fs.writeFileSync(sample, Buffer.from(PNG_B64,'base64'))
    const before = await page.locator('.asset-grid .thumb-img').count()
    await page.locator('label.upload-btn input[type=file]').setInputFiles(sample)
    // 等待新缩略图出现
    await page.waitForFunction((b)=>document.querySelectorAll('.asset-grid .thumb-img').length > b, before, { timeout: 15000 })
    ok('Phase1 全局资产上传→缩略图回流')

    // ---- 流程导航：5 步 ----
    await page.goto(WEB + '/projects/'+pid+'/editor', { waitUntil:'networkidle' })
    const navSteps = await page.locator('.flow-nav .step, .flow-nav .fn-step, [class*=flow] .step').count()
    // ProjectFlowNav 渲染 5 个步骤（用文本断言更稳）
    const navText = await page.locator('.frag').innerText().catch(()=> '')
    if (/全局资产|分场拆解|分镜编排|异步|合成全集|资产初始化|策划|编排|推理|合成/.test(navText) && navText.includes('合成全集')) ok('Phase 导航 含 5 阶段流程入口')
    else bad('Phase 导航 5 阶段', new Error('未检测到流程导航文本'))

    // ---- Phase 3：@提及 → 引用资产 chips ----
    // 全局库里已有 阿浩_E2E（由 pipeline 测试创建）；保险起见再建一个本测试用角色资产
    const g = await api('/assets/upload', { method:'POST',
      body: (()=>{ const fd=new FormData(); fd.append('file', new Blob([Buffer.from(PNG_B64,'base64')],{type:'image/png'}),'a.png'); fd.append('name','UI阿浩'); fd.append('type','role'); return fd })() })
    // 重新加载编辑器以刷新资产库
    await page.goto(WEB + '/projects/'+pid+'/editor', { waitUntil:'networkidle' })
    const voice = page.locator('.shot-block').first().locator('.sg.full:has(> label:has-text("画外音")) .el-textarea__inner')
    await voice.fill('@UI阿浩 走进昏暗的房间')
    await page.waitForTimeout(800)
    const chipCount = await page.locator('.shot-block').first().locator('.shot-refs .asset-tag').count()
    if (chipCount >= 1) ok('Phase3 @提及→引用资产 chip ('+chipCount+')')
    else bad('Phase3 @提及→引用资产', new Error('未生成引用 chip'))

    // ---- Phase 4 / 5 按钮接线存在 ----
    const hasRegen = await page.locator('button:has-text("重新生成")').count()
    const hasSynth = await page.locator('button:has-text("合成全集")').count()
    if (hasRegen && hasSynth) ok('Phase4/5 重新生成 & 合成全集 按钮存在')
    else bad('Phase4/5 按钮', new Error('regen='+hasRegen+' synth='+hasSynth))

    if (errors.length) bad('页面无 JS 运行时错误', new Error(errors.slice(0,3).join(' | ')))
    else ok('页面无 JS 运行时错误')
  } catch(e){ bad('UI 主流程', e) }
  finally { await browser.close() }
  finish()
}
function finish(){
  console.log('\n============ UI 流程测试报告 ============')
  for (const r of results) console.log(r)
  console.log(`\n合计: ${PASS} 通过 / ${FAIL} 失败`)
  process.exit(FAIL>0?1:0)
}
main().catch(e=>{ console.log('FATAL '+e.message); finish() })
