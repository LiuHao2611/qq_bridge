// D 盘安全 Chromium Launcher
// 全局禁用：chromium user-data-dir / npm tmp / playwright video 等所有 default temp 位置
// 所有临时产物强制放 D:/AI/manju_agent/.tmp/playwright-* 下
import { chromium } from 'playwright'
import path from 'path'
import fs from 'fs'

const TMP_BASE = 'D:/AI/manju_agent/.tmp'
// userDataDir = chromium profile dir，浏览器 cache/cookies/Screenshots 都在这里
// 不指定时，默认走 C:/Users/<u>/AppData/Local/Temp/playwright_chromiumdir-*
function makeUserDataDir(label) {
  const p = path.join(TMP_BASE, 'playwright', `${label}_${Date.now()}_${process.pid}`)
  fs.mkdirSync(p, { recursive: true })
  return p
}

// 内置 after-hook：测试结束强制清空 userDataDir（避免 D 盘累积无用的 profile）
async function cleanupDir(p) {
  try { fs.rmSync(p, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 }) }
  catch (_) {}
}

export async function launchChromium({ label = 'default', ...opts } = {}) {
  const userDataDir = makeUserDataDir(label)
  const browser = await chromium.launch({
    ...opts,
    // 强制 chromium 自己下载的 playwright browser 也用 D 盘（修改 PLAYWRIGHT_BROWSERS_PATH）
    env: {
      ...process.env,
      PLAYWRIGHT_BROWSERS_PATH: 'D:/AI/manju_agent/.tmp/playwright-browsers',
      TMPDIR: TMP_BASE,
      TEMP: TMP_BASE,
      TMP: TMP_BASE,
      ...(opts.env || {}),
    },
  })
  const ctx = await browser.newContext({ ...(opts.contextOpts || {}) })
  return {
    browser,
    ctx,
    async [Symbol.asyncDispose]() {
      try { await browser.close() } catch (_) {}
      await cleanupDir(userDataDir)
    },
    _userDataDir: userDataDir,
  }
}
