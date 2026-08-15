// FC-05 验证：默认主题悬浮球 = 仅导演图标（无 P5/夜城形态化身），双击循环 5 色盘；
// P5/夜城形态主题：orb-btn 挂 orb-morph--{key}，不挂 orb-color--*。真实 DOM + 机器判定。
import { chromium } from 'playwright';
import fs from 'fs';

const BASE = 'http://localhost:8099';
const PID = '383722ac-79cd-4308-a595-8744949a9fba';
const REC = 'D:/AI/manju_agent/tests/recordings';
const PRESET_KEY = 'manju_theme';
const MORPH_CLASS = /^orb-morph--/;
const COLOR_CLASS = /^orb-color--/;

async function auditTheme(browser, themeName, shots) {
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  await page.addInitScript((arg) => { try { localStorage.setItem(arg.k, arg.v); } catch (e) {} }, { k: PRESET_KEY, v: themeName });
  const fails = [];
  page.on('pageerror', (e) => fails.push(`[${themeName}] pageerror: ${e.message}`));

  await page.goto(`${BASE}/projects/${PID}/assets`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.orb-btn', { timeout: 5000 });
  await page.waitForTimeout(400);

  // ---- 1) 静态断言（不依赖 dblclick） ----
  const initial = await page.evaluate(() => {
    const btn = document.querySelector('.orb-btn');
    if (!btn) return null;
    const cls = Array.from(btn.classList);
    return {
      cls,
      hasMorph: !!document.querySelector('.orb-btn .orb-morph'),
      morphDisplay: (() => {
        const m = document.querySelector('.orb-btn .orb-morph');
        if (!m) return 'absent';
        return getComputedStyle(m).display;
      })(),
      bg: getComputedStyle(btn).backgroundImage,
      shadow: getComputedStyle(btn).boxShadow,
      coreSvg: !!btn.querySelector('.orb-core svg'),
    };
  });
  console.log(`[${themeName}] initial:`, JSON.stringify(initial, null, 2));
  if (!initial) { fails.push(`[${themeName}] .orb-btn not found`); await page.close(); return fails; }

  const isMorphTheme = themeName === 'p5' || themeName === 'cyberpunk';

  // 不变量 1：形态主题必有 .orb-morph 元素；非形态主题必无
  if (isMorphTheme) {
    if (!initial.hasMorph) fails.push(`[${themeName}] 形态主题却无 .orb-morph 元素（回归）`);
    if (initial.morphDisplay === 'none') fails.push(`[${themeName}] .orb-morph display:none（被错隐藏）`);
  } else {
    if (initial.hasMorph) fails.push(`[${themeName}] 非形态主题却渲染了 .orb-morph 元素（用户硬约束违反）`);
  }

  // 不变量 2：形态主题挂 orb-morph--*；非形态主题必不挂
  const hasMorphClass = initial.cls.some((c) => MORPH_CLASS.test(c));
  const hasColorClass = initial.cls.some((c) => COLOR_CLASS.test(c));
  if (isMorphTheme) {
    if (!hasMorphClass) fails.push(`[${themeName}] 形态主题未挂 orb-morph--* class`);
    if (hasColorClass) fails.push(`[${themeName}] 形态主题不应挂 orb-color--* class`);
  } else {
    if (hasMorphClass) fails.push(`[${themeName}] 非形态主题不应挂 orb-morph--* class（用户报告回归）`);
    if (!hasColorClass) fails.push(`[${themeName}] 非形态主题必须挂 orb-color--* class（双击切色前提）`);
  }

  // 不变量 3：必有导演图标 SVG
  if (!initial.coreSvg) fails.push(`[${themeName}] 缺少 .orb-core svg 导演图标`);

  // 截图（初次状态）
  if (shots && shots.initial) await page.screenshot({ path: `${REC}/${shots.initial}` });

  // ---- 2) 双击循环：形态主题 → 切形态；非形态主题 → 切颜色 ----
  // 记录 dblclick 前 class，触发 3 次 dblclick，验证变化
  const before = initial.cls.join(' ');
  await page.dblclick('.orb-btn');
  await page.waitForTimeout(700);
  const after1 = (await page.evaluate(() => Array.from(document.querySelector('.orb-btn').classList).join(' ')));
  await page.dblclick('.orb-btn');
  await page.waitForTimeout(700);
  const after2 = (await page.evaluate(() => Array.from(document.querySelector('.orb-btn').classList).join(' ')));
  await page.dblclick('.orb-btn');
  await page.waitForTimeout(700);
  const after3 = (await page.evaluate(() => Array.from(document.querySelector('.orb-btn').classList).join(' ')));

  if (isMorphTheme) {
    // 形态主题：orb-morph--{key} 应变化
    const morphMatch = (s) => (s.match(/orb-morph--[\w-]+/) || [''])[0];
    const m0 = morphMatch(before);
    const m1 = morphMatch(after1);
    const m2 = morphMatch(after2);
    const m3 = morphMatch(after3);
    console.log(`[${themeName}] dblclick morphs: ${m0} → ${m1} → ${m2} → ${m3}`);
    const uniq = new Set([m0, m1, m2, m3]);
    if (uniq.size < 2) fails.push(`[${themeName}] 形态主题 3 次 dblclick 后形态未循环（仅 ${[...uniq].join(',')}）`);
  } else {
    // 非形态主题：orb-color--{key} 应变化
    const colorMatch = (s) => (s.match(/orb-color--[\w-]+/) || [''])[0];
    const c0 = colorMatch(before);
    const c1 = colorMatch(after1);
    const c2 = colorMatch(after2);
    const c3 = colorMatch(after3);
    console.log(`[${themeName}] dblclick colors: ${c0} → ${c1} → ${c2} → ${c3}`);
    const uniq = new Set([c0, c1, c2, c3]);
    if (uniq.size < 2) fails.push(`[${themeName}] 默认主题 3 次 dblclick 后颜色未循环（仅 ${[...uniq].join(',')}）`);
  }

  if (shots && shots.cycled) await page.screenshot({ path: `${REC}/${shots.cycled}` });

  await page.close();
  return fails;
}

async function run() {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const allFails = [];

  // 用户报告的现场：默认主题 + 资产初始化页（PID=383722ac-...）
  allFails.push(...await auditTheme(browser, 'default', {
    initial: 'orb_default_initial.png',
    cycled: 'orb_default_cycled.png',
  }));

  // 灰主题：同样应无形态
  allFails.push(...await auditTheme(browser, 'gray', {
    initial: 'orb_gray_initial.png',
    cycled: 'orb_gray_cycled.png',
  }));

  // P5 形态主题：回归保护（不能因为本次修破 P5 形态）
  allFails.push(...await auditTheme(browser, 'p5', {
    initial: 'orb_p5_initial.png',
    cycled: 'orb_p5_cycled.png',
  }));

  // 夜城形态主题：回归保护
  allFails.push(...await auditTheme(browser, 'cyberpunk', {
    initial: 'orb_cyberpunk_initial.png',
    cycled: 'orb_cyberpunk_cycled.png',
  }));

  await browser.close();

  console.log('\n========== FC-05 默认主题形态泄漏回归验证 ==========');
  if (allFails.length) {
    console.log(`FAIL · ${allFails.length} 项不通过：`);
    allFails.forEach((f) => console.log('  - ' + f));
    process.exit(1);
  }
  console.log('ALL PASS · 4 主题（default/gray/p5/cyberpunk）形态归属正确，dblclick 切换正确');
  process.exit(0);
}

run().catch((e) => { console.error('FATAL', e); process.exit(2); });