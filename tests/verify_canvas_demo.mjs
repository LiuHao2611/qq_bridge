import { chromium } from 'playwright';
import fs from 'fs';

const PID = '383722ac-79cd-4308-a595-8744949a9fba';
const URL = `http://localhost:8099/projects/${PID}/canvas`;
const EXE = 'D:/AI/.pw-browsers/chromium-1234/chrome-win64/chrome.exe';
const OUT = 'D:/AI/manju_agent/.tmp/canvas_shots';
fs.mkdirSync(OUT, { recursive: true });
const LOG = `${OUT}/verify.log`;
fs.writeFileSync(LOG, '');
const log = (...a) => { const s = a.map(x => typeof x === 'string' ? x : JSON.stringify(x, null, 2)).join(' '); fs.appendFileSync(LOG, s + '\n'); };

let browser;
try {
  const errors = [];
  browser = await chromium.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));

  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await page.waitForSelector('.canvas-banner', { timeout: 15000 }).catch(() => log('WARN: .canvas-banner missing'));

  const bannerVisible = await page.locator('.canvas-banner').isVisible().catch(() => false);
  const bannerText = (await page.locator('.canvas-banner').innerText().catch(() => '')) || '';
  const primaryBtnText = (await page.locator('.btn.primary').innerText().catch(() => '')) || '';
  const hasOldRun = (await page.locator('button:has-text("运行工作流")').count()) > 0;
  const hintText = (await page.locator('.canvas-hint').innerText().catch(() => '')) || '';
  const toolbarBtns = await page.locator('.ws-toolbar .btn').allInnerTexts().catch(() => []);

  await page.screenshot({ path: `${OUT}/01_canvas.png`, fullPage: false });

  const checks = {
    bannerVisible,
    bannerMentionsPlanning: bannerText.includes('规划模式') && bannerText.includes('片段编辑器'),
    bannerText,
    toolbarBtns,
    primaryBtnText,
    primaryBtnHonest: primaryBtnText.includes('AI 润色主题'),
    oldRunButtonGone: !hasOldRun,
    hintMentionsEditor: hintText.includes('片段编辑器'),
    consoleErrors: errors,
  };
  log('=== Canvas demo-mode verification ===');
  log(checks);
  const pass = bannerVisible && checks.bannerMentionsPlanning && checks.primaryBtnHonest && checks.oldRunButtonGone && errors.length === 0;
  log(pass ? 'RESULT: PASS' : 'RESULT: FAIL');
  await browser.close();
  process.exit(pass ? 0 : 1);
} catch (e) {
  log('FATAL: ' + (e && e.stack ? e.stack : e));
  if (browser) await browser.close().catch(() => {});
  process.exit(2);
}
