// FC-05 排查：夜城主题混入 P5 样式。
// 机器判定（非零退出）：强制夜城主题，逐个读取混用元素的 computed style（text-stroke / text-shadow /
// box-shadow / background / clip-path），识别 P5 特征来源；并截图首页/项目策划/分镜编排（夜城 + P5 对照）。
import { chromium } from 'playwright';
import fs from 'fs';

const BASE = 'http://localhost:8099';
const PID = '383722ac-79cd-4308-a595-8744949a9fba';
const REC = 'D:/AI/manju_agent/tests/recordings';
const PRESET_KEY = 'manju_theme';

const P5RED = 'rgb(228, 0, 43)';
const P5INK = 'rgb(11, 11, 11)';

function p5Leak(cs) {
  const stroke = cs.webkitTextStrokeWidth;
  const ts = cs.textShadow || '';
  const bg = cs.backgroundColor || '';
  const bs = cs.boxShadow || '';
  const cp = cs.clipPath || 'none';
  const hard = /(-?\d+)px (-?\d+)px 0px( 0px)?/.test(bs) || /(-?\d+)px (-?\d+)px 0px/.test(bs);
  const leak =
    (stroke && stroke !== '0px') ||
    ts.includes(P5RED) || ts.includes(P5INK) ||
    bg.includes(P5RED) ||
    (hard && (bg.includes(P5RED) || bg.includes(P5INK) || cp !== 'none'));
  return { leak, stroke, ts: ts.slice(0, 90), bg, bs: bs.slice(0, 70), cp };
}

async function auditPage(page, pageLabel, out) {
  const data = await page.evaluate((arg) => {
    const res = [];
    const sel = 'h1,h2,h3,h4, .el-button--primary, button.btn, .p5-btn, .nc-btn, .el-tag, .p5-tag, .nc-tag, .mc-tag';
    document.querySelectorAll(sel).forEach((el) => {
      const cs = getComputedStyle(el);
      const stroke = cs.webkitTextStrokeWidth;
      const ts = cs.textShadow || '';
      const bg = cs.backgroundColor || '';
      const bs = cs.boxShadow || '';
      const cp = cs.clipPath || 'none';
      const hard = /(-?\d+)px (-?\d+)px 0px( 0px)?/.test(bs);
      const leak =
        (stroke && stroke !== '0px') ||
        ts.includes(arg.P5RED) || ts.includes(arg.P5INK) ||
        bg.includes(arg.P5RED) ||
        (hard && (bg.includes(arg.P5RED) || bg.includes(arg.P5INK) || cp !== 'none'));
      res.push({
        tag: el.tagName,
        cls: (el.className && el.className.toString ? el.className.toString() : '').slice(0, 90),
        text: (el.textContent || '').trim().slice(0, 18),
        borderRadius: cs.borderTopLeftRadius,
        clipPath: cp,
        boxShadow: bs.slice(0, 70),
        bg: bg,
        textStrokeW: stroke,
        textShadow: ts.slice(0, 90),
        transform: (cs.transform || 'none').slice(0, 30),
        leak,
      });
    });
    return res;
  }, { P5RED, P5INK });
  data.forEach((d) => out.push({ page: pageLabel, ...d }));
}

async function run() {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const report = [];
  const fails = [];

  const auditTheme = async (themeName, shots) => {
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    await page.addInitScript((arg) => { try { localStorage.setItem(arg.k, arg.v); } catch (e) {} }, { k: PRESET_KEY, v: themeName });
    page.on('pageerror', (e) => fails.push(`[${themeName}] pageerror: ${e.message}`));

    // 首页
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(400);
    await auditPage(page, `首页(${themeName})`, report);
    if (shots.home) await page.screenshot({ path: `${REC}/${shots.home}` });

    // 项目策划
    await page.goto(`${BASE}/projects/${PID}/planning`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(500);
    await auditPage(page, `项目策划(${themeName})`, report);
    if (shots.planning) await page.screenshot({ path: `${REC}/${shots.planning}` });

    // 分镜编排（片段编辑器）
    await page.goto(`${BASE}/projects/${PID}/fragment`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(500);
    await auditPage(page, `分镜编排(${themeName})`, report);
    if (shots.fragment) await page.screenshot({ path: `${REC}/${shots.fragment}` });

    await page.close();
  };

  // 夜城：重点排查泄漏
  await auditTheme('cyberpunk', {
    home: 'nc_home.png', planning: 'nc_planning.png', fragment: 'nc_fragment.png',
  });
  // P5：对照（确认修复不破坏 P5）
  await auditTheme('p5', {
    home: 'p5_home.png', planning: 'p5_planning.png', fragment: 'p5_fragment.png',
  });

  await browser.close();

  // 输出排查表
  const leaks = report.filter((r) => r.leak);
  console.log('\n========== FC-05 排查表（夜城主题下 P5 泄漏元素）==========');
  console.log('元素(page) | tag | class | 生效的 P5 特征 | 来源');
  leaks.forEach((r) => {
    const feats = [];
    if (r.textStrokeW && r.textStrokeW !== '0px') feats.push(`text-stroke:${r.textStrokeW}`);
    if (r.textShadow.includes(P5RED)) feats.push('text-shadow含绯红');
    if (r.boxShadow && r.boxShadow !== 'none') feats.push(`box-shadow:${r.boxShadow}`);
    if (r.bg.includes(P5RED)) feats.push(`bg:绯红`);
    if (r.clipPath && r.clipPath !== 'none') feats.push(`clip-path:${r.clipPath.slice(0, 24)}`);
    const src = r.cls.includes('p5-title') ? 'style.css:1115(.p5-title 全局无作用域)'
      : r.cls.includes('p5-btn') ? 'style.css:1137(.p5-btn 全局无作用域)'
      : r.cls.includes('p5-tag') ? 'style.css:1121(.p5-tag 全局无作用域)'
      : r.cls.includes('p5-badge-star') ? 'style.css:1165(.p5-badge-star 全局无作用域)'
      : r.cls.includes('p5-tear') ? 'style.css:1182(.p5-tear 全局无作用域)'
      : r.cls.includes('p5-card') ? 'style.css:1131(.p5-card 全局无作用域)'
      : '未知';
    console.log(`- [${r.page}] <${r.tag}> .${r.cls} | ${feats.join(' / ')} | ${src}`);
  });
  console.log(`\n夜城主题下检测到的 P5 泄漏元素数：${leaks.filter((l) => l.page.includes('cyberpunk')).length}`);
  console.log(`P5 主题下（预期有 P5 特征，非泄漏）：${leaks.filter((l) => l.page.includes('p5')).length}`);

  fs.writeFileSync(`${REC}/p5_leak_audit.json`, JSON.stringify(report, null, 2));

  // 退出码：夜城泄漏 > 0 → 失败（交给后续修复步）
  const ncLeaks = leaks.filter((l) => l.page.includes('cyberpunk')).length;
  console.log(`\nNC_LEAK_COUNT=${ncLeaks}`);
  process.exit(ncLeaks > 0 ? 1 : 0);
}

run().catch((e) => { console.error('FATAL', e); process.exit(2); });
