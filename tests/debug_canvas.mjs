import { chromium } from 'playwright';
import fs from 'fs';

const PID = '383722ac-79cd-4308-a595-8744949a9fba';
const URL = `http://localhost:8099/projects/${PID}/canvas`;
const EXE = 'D:/AI/.pw-browsers/chromium-1234/chrome-win64/chrome.exe';
const LOG = 'D:/AI/manju_agent/.tmp/canvas_shots/debug.log';
fs.writeFileSync(LOG, '');
const log = (...a) => { const s = a.map(x => typeof x === 'string' ? x : JSON.stringify(x)).join(' '); fs.appendFileSync(LOG, s + '\n'); };

const browser = await chromium.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errs = [];
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errs.push(`[${m.type()}] ${m.text()}`); });
page.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(4000);
log('URL now:', page.url());
const appHtmlLen = await page.evaluate(() => (document.getElementById('app') || {}).innerHTML?.length || 0);
log('app innerHTML length:', appHtmlLen);
const bodyText = await page.evaluate(() => document.body.innerText.slice(0, 800));
log('BODY TEXT (first 800):\n' + bodyText);
const hasCanvasShell = await page.locator('.canvas-shell').count();
const hasBanner = await page.locator('.canvas-banner').count();
log('canvas-shell count:', hasCanvasShell, ' canvas-banner count:', hasBanner);
const buttons = await page.locator('.ws-toolbar .btn').allInnerTexts().catch(() => []);
log('toolbar buttons:', JSON.stringify(buttons));
log('CONSOLE ERRORS (' + errs.length + '):');
errs.slice(0, 20).forEach(e => log('  ' + e));
await page.screenshot({ path: 'D:/AI/manju_agent/.tmp/canvas_shots/debug.png' });
await browser.close();
log('done');
