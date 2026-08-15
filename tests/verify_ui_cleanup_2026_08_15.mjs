// 回归测试：2026-08-15 用户反馈的两个 UI 错位
//   A. 导演 Agent 面板头部不应出现用户名/头像（曾经把 userNickname+userAvatar 错误迁移到这里）
//   B. 项目内翻页按钮 .wave-fold 不应在非 P5 主题下渲染 P5 硬角红按钮（曾经硬编码 class="p5-pager"）
//
// 运行前置：frontend 已 build；后端 :7788 + 前端 :8099 + Ollama 任意即可；
//          测试项目用默认 P5 fallback 项目 383722ac-79cd-4308-a595-8744949a9fba（历史稳定）。
import { chromium } from 'playwright';

const BASE = 'http://localhost:8099';
const PID = '383722ac-79cd-4308-a595-8744949a9fba';

let failed = 0;
function assert(cond, msg) {
  if (!cond) {
    failed += 1;
    console.log('  [FAIL]', msg);
  } else {
    console.log('  [ok]  ', msg);
  }
}

async function prime(page, theme) {
  if (theme) await page.addInitScript((t) => { try { localStorage.setItem('manju_theme', t) } catch (e) {} }, theme);
}

async function foldButtonStyles(page) {
  return page.evaluate(() => {
    return [...document.querySelectorAll('.wave-fold')].map((el) => {
      const cs = getComputedStyle(el);
      return {
        cls: el.className,
        bg: cs.backgroundColor,
        border: cs.borderColor,
        clipPath: cs.clipPath,
        shadow: (cs.boxShadow || '').slice(0, 60),
      };
    });
  });
}

const b = await chromium.launch({ args: ['--no-sandbox', '--disable-gpu'] });

// =========================================================================
//  Part A · 导演 Agent 面板：不应渲染用户名/头像
// =========================================================================
console.log('\n[Part A] 导演 Agent 面板头部洁净性（default + p5 双主题）');
for (const theme of ['default', 'p5']) {
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  await prime(p, theme);
  await p.goto(`${BASE}/projects/${PID}/planning`, { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.orb-btn', { timeout: 15000 });
  await p.locator('.orb-btn').first().click();
  await p.waitForTimeout(500);

  const headInfo = await p.evaluate(() => {
    const head = document.querySelector('.orb-head-right');
    return {
      hasUserChip: !!document.querySelector('.orb-user-chip'),
      headText: head ? (head.innerText || '').replace(/\s+/g, ' ').trim() : 'no-panel',
      orphanImgCount: document.querySelectorAll('.orb-head img').length,
    };
  });

  console.log(`  -- theme=${theme} --`);
  assert(!headInfo.hasUserChip, `[${theme}] .orb-user-chip 不应出现在面板头部`);
  assert(headInfo.orphanImgCount === 0, `[${theme}] 面板头部不应再有 <img>`);
  assert(
    !/小志|UD|userNickname|userAvatar|edit-icon/i.test(headInfo.headText),
    `[${theme}] 面板头部文本不包含用户名/编辑图标 (实际: "${headInfo.headText}")`
  );

  await ctx.close();
}

// =========================================================================
//  Part B · 翻页按钮：仅 P5 主题才渲染 P5 硬角红按钮
// =========================================================================
console.log('\n[Part B] 项目翻页按钮主题作用域（default / gray / p5 / cyberpunk 四主题）');

const P5_RED = 'rgb(228, 0, 43)';     // var(--p5-red, #E4002B)
const P5_INK = 'rgb(11, 11, 11)';     // var(--p5-ink, #141414) 渲染后可能略变

function isP5Styled(style) {
  // P5 主题特征：红色背景 + 非 none 的 clip-path + 硬阴影
  const hasRed = style.bg === P5_RED || style.bg.startsWith('rgb(228');
  const hasClip = style.clipPath && style.clipPath !== 'none';
  const hasHardShadow = style.shadow && style.shadow !== 'none' && /4px\s+4px/.test(style.shadow);
  return hasRed && hasClip && hasHardShadow;
}

function isPlainCard(style) {
  // 通用方块卡片：clip-path=none + box-shadow=none + 不是红色
  const noClip = !style.clipPath || style.clipPath === 'none';
  const noShadow = !style.shadow || style.shadow === 'none';
  const notRed = !(style.bg === P5_RED || style.bg.startsWith('rgb(228'));
  return noClip && noShadow && notRed;
}

for (const theme of ['default', 'gray', 'p5', 'cyberpunk']) {
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  await prime(p, theme);
  await p.goto(`${BASE}/projects/${PID}/planning`, { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.wave-fold', { timeout: 15000 });
  await p.waitForTimeout(600);

  const folds = await foldButtonStyles(p);

  console.log(`  -- theme=${theme} --`);
  for (const f of folds) {
    const hasP5Pager = /\bp5-pager\b/.test(f.cls);
    if (theme === 'p5') {
      assert(hasP5Pager, `[${theme}] 翻页按钮必须挂 .p5-pager class`);
      assert(isP5Styled(f), `[${theme}] 翻页按钮必须渲染 P5 硬角红 (bg=${f.bg}, clip=${(f.clipPath||'').slice(0,30)}, shadow=${f.shadow})`);
    } else {
      assert(!hasP5Pager, `[${theme}] 翻页按钮不应挂 .p5-pager class`);
      assert(isPlainCard(f), `[${theme}] 翻页按钮应降级为通用方块 (bg=${f.bg}, clip=${f.clipPath}, shadow=${f.shadow})`);
    }
  }

  await ctx.close();
}

await b.close();

// =========================================================================
//  收尾
// =========================================================================
if (failed > 0) {
  console.log(`\n[RESULT] ${failed} assertion(s) FAILED — exit 1`);
  process.exit(1);
}
console.log('\n[RESULT] ALL ASSERTIONS PASS — exit 0');
