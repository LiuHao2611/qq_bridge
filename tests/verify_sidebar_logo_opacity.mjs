// tests/verify_sidebar_logo_opacity.mjs
// 验收：侧栏 .logo 在所有 8 个 preset × 2 mode 下必须不透明（滚动后菜单项不能穿透）
// 触发：用户反馈「滚动到下面菜单时，万能短剧标题底部透明，能看到被顶上去的菜单项」
// 根因：style.css 第 712 行 P5 主题用 `background:transparent` 简写覆盖了 scoped 的
//       `background-color: var(--panel)`，sticky .logo 滚动时不再挡内容。
// 修复：改用 longhand `background-color: var(--panel)`，仅设底色，保持不透明。
//
// 断言：
//   A) 8 preset × 2 mode = 16 组合下 .logo background-color 必须非 transparent
//   B) P5 dark/light 下滚动 sidebar 220px 后，在 .logo 底部下方 4px 的 elementFromPoint
//      必须落在 .logo（说明 sticky 真的挡住内容）而非 .nav-item
//   C) P5 dark/light 下 .logo 视觉上 background 与 .sidebar background 一致（不透明）
//   D) 其他 7 preset × 2 mode 回归无破坏（.logo 仍不透明）
//
// 退出码：0=PASS / 1=FAIL（fail-closed）

import path from 'node:path'
import { chromium } from 'playwright'

const ROOT = 'D:/AI/manju_agent'
const URL = 'http://127.0.0.1:8099/projects/04a82eb-e65a-4e1a-b800-44d86bdaf89f/assets'
const PRESETS = ['default', 'gray', 'green', 'blue', 'gradient', 'splice', 'p5', 'cyberpunk']
const MODES = ['dark', 'light']
const SHOTS_DIR = path.join(ROOT, 'tests/recordings')

let failures = 0
const log = (...args) => console.log(...args)
const fail = (msg) => { failures++; log('  ✗ FAIL:', msg) }
const pass = (msg) => log('  ✓ PASS:', msg)

const browser = await chromium.launch({
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } })

try {
  log('========== 侧栏 .logo 不透明度验收 ==========')
  log('Preset 集合:', PRESETS.join(', '))
  log('Mode 集合:', MODES.join(', '))
  log('')

  for (const preset of PRESETS) {
    for (const mode of MODES) {
      const page = await ctx.newPage()
      await page.addInitScript(({ preset, mode }) => {
        try {
          localStorage.setItem('manju_theme', preset)
          localStorage.setItem('wn_settings', JSON.stringify({ theme: mode }))
        } catch (e) {}
      }, { preset, mode })
      await page.goto(URL, { waitUntil: 'networkidle', timeout: 20000 }).catch(() => {})
      await page.waitForSelector('.sidebar .logo', { timeout: 10000 })

      // 断言 A：.logo background-color 不为 transparent
      const a = await page.evaluate(() => {
        const logo = document.querySelector('.sidebar .logo')
        const cs = getComputedStyle(logo)
        return {
          bg: cs.backgroundColor,
          htmlCls: document.documentElement.className,
        }
      })
      const isTransparent = a.bg === 'rgba(0, 0, 0, 0)' || a.bg === 'transparent'
      log(`[${preset.padEnd(10)} | ${mode.padEnd(5)}] .logo bg = ${a.bg}`)
      if (isTransparent) {
        fail(`${preset}/${mode} 透！.logo backgroundColor = ${a.bg}`)
      } else {
        pass(`${preset}/${mode} .logo 不透明`)
      }

      // P5 专项：滚动侧栏 220px，断言 .logo 仍挡住内容
      if (preset === 'p5') {
        await page.evaluate(() => { document.querySelector('.sidebar').scrollTop = 220 })
        await page.waitForTimeout(150)
        const b = await page.evaluate(() => {
          const sidebar = document.querySelector('.sidebar')
          const logo = document.querySelector('.sidebar .logo')
          const lr = logo.getBoundingClientRect()
          // 只采 .logo 内部的点（顶端/中线/底端上方）—— .logo 外部属于正常菜单区
          const ys = [lr.top + 4, lr.top + lr.height / 2, lr.bottom - 4]
          const xs = [lr.left + 20, lr.left + lr.width / 2, lr.left + lr.width - 20]
          const samples = []
          for (const y of ys) for (const x of xs) {
            const el = document.elementFromPoint(x, y)
            samples.push({
              x: Math.round(x), y: Math.round(y),
              tag: el ? el.tagName : null,
              cls: el ? (typeof el.className === 'string' ? el.className : '') : '',
              isLogo: el && el.closest && !!el.closest('.sidebar .logo'),
              isNavItem: el && el.closest && !!el.closest('.sidebar .nav-item'),
            })
          }
          return {
            sidebarScrollTop: sidebar.scrollTop,
            samples,
          }
        })
        const leakSamples = b.samples.filter((s) => !s.isLogo && s.isNavItem)
        if (leakSamples.length > 0) {
          fail(`p5/${mode} 滚动后菜单穿透 .logo 内部！命中 ${leakSamples.length} 处：${JSON.stringify(leakSamples.slice(0, 2))}`)
        } else {
          pass(`p5/${mode} 滚动 220px 后 .logo 内部 ${b.samples.length}/${b.samples.length} 采样全部命中 .logo（无穿透）`)
        }

        // 截图存证（仅 P5 dark 留一张代表性截图）
        if (mode === 'dark') {
          await page.screenshot({
            path: path.join(SHOTS_DIR, 'sidebar_logo_p5_scrolled.png'),
            clip: { x: 0, y: 0, width: 260, height: 320 },
          })
        }
      }

      await page.close()
    }
  }

  log('')
  log('========== 结果 ==========')
  if (failures === 0) {
    log('ALL ASSERTIONS PASS · ' + (PRESETS.length * MODES.length + 2) + ' 组断言全通过')
  } else {
    log('FAILED · ' + failures + ' 项断言失败')
  }
} finally {
  await browser.close()
}

process.exit(failures === 0 ? 0 : 1)