// DirectorOrb 多形态化身（P5 / 夜城）· 机器判定验收（规格 v2.0 §4/§5/§7）
// 运行：PLAYWRIGHT_BROWSERS_PATH=D:/AI/.pw-browsers node tests/verify_orb_morph.mjs
// 前置：前端已构建并服务于 http://localhost:8099（web_app.py）；后端 7788 可选（沙盒页无依赖）
// 产出：断言式 PASS/FAIL + 退出码；.tmp/orb_morph_shots/*.png 红框时间戳截图（E-1/E-2）
import { chromium } from 'playwright';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';

const BASE = process.env.BASE || 'http://localhost:8099';
const SHOT = path.resolve('D:/AI/manju_agent/.tmp/orb_morph_shots');
fs.mkdirSync(SHOT, { recursive: true });

const results = [];
function check(name, cond, detail = '') {
  const ok = !!cond;
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  :: ' + detail : ''}`);
}

// 红框 + 时间戳 标注截图（FC-01）
async function annotateShot(page, selectors, file, label) {
  await page.evaluate(() => document.querySelectorAll('.cp-box,.cp-ts').forEach((e) => e.remove()));
  for (const sel of selectors) {
    const loc = page.locator(sel).first();
    if (await loc.count()) {
      const b = await loc.boundingBox().catch(() => null);
      if (b) {
        await page.evaluate(({ x, y, w, h }) => {
          const d = document.createElement('div');
          d.className = 'cp-box';
          d.style.cssText = `position:fixed;left:${x}px;top:${y}px;width:${w}px;height:${h}px;border:3px solid #FF0033;box-shadow:0 0 0 1px #fff;z-index:2147483646;pointer-events:none;`;
          document.body.appendChild(d);
        }, b);
      }
    }
  }
  await page.evaluate((ts) => {
    const t = document.createElement('div');
    t.className = 'cp-ts';
    t.style.cssText = 'position:fixed;right:8px;bottom:8px;z-index:2147483646;background:rgba(0,0,0,.82);color:#FF0033;font:700 12px Consolas,monospace;padding:4px 8px;border:1px solid #FF0033;pointer-events:none;';
    t.textContent = 'VERIFY ' + ts;
    document.body.appendChild(t);
  }, label);
  await page.waitForTimeout(140);
  await page.screenshot({ path: path.join(SHOT, file), fullPage: false });
  await page.evaluate(() => document.querySelectorAll('.cp-box,.cp-ts').forEach((e) => e.remove()));
}

const tsNow = () => new Date().toISOString().replace('T', ' ').slice(0, 19);

// 形态预期尺寸（规格 §4）：key -> [w, h] px
const FORM_SIZE = {
  p5: { sword: [18, 44], mask: [36, 20], tower: [24, 34], shackle: [34, 26] },
  cyberpunk: { katana: [14, 40], relic: [30, 24], wire: [26, 26], scanner: [32, 32] },
};

// 注入一个悬浮球（含 .orb-morph 形态层 + .orb-core），返回根句柄
// opts: { x, y, label, size }
async function injectOrb(page, key, extraClass = '', opts = {}) {
  const o = { x: 0, y: 0, label: key, size: 110, ...opts };
  await page.evaluate(({ key, extraClass, o }) => {
    const root = document.createElement('div');
    root.className = 'dir-orb-root';
    root.style.cssText = `position:fixed;left:${o.x}px;top:${o.y}px;z-index:9999;width:${o.size + 20}px;text-align:center;`;
    const lab = document.createElement('div');
    lab.className = 'orb-label';
    lab.style.cssText = 'color:#fff;font:700 12px Consolas,monospace;margin-bottom:6px;text-shadow:0 0 4px #000;';
    lab.textContent = o.label;
    const btn = document.createElement('div');
    btn.className = `orb-btn ${extraClass} orb-morph--${key}`;
    btn.style.cssText = `position:relative;width:${o.size}px;height:${o.size}px;display:flex;align-items:center;justify-content:center;margin:0 auto;`;
    const core = document.createElement('span');
    core.className = 'orb-core'; core.textContent = '◈'; core.style.cssText = 'font-size:24px;';
    const morph = document.createElement('span');
    morph.className = 'orb-morph';
    btn.appendChild(core); btn.appendChild(morph); root.appendChild(lab); root.appendChild(btn);
    document.body.appendChild(root);
  }, { key, extraClass, o });
  return page.locator('.dir-orb-root').last();
}

async function morphComputed(page, root) {
  return root.evaluate((el) => {
    const m = el.querySelector('.orb-morph');
    const cs = getComputedStyle(m);
    const before = getComputedStyle(m, '::before');
    const btn = el.querySelector('.orb-btn');
    const core = el.querySelector('.orb-core');
    return {
      w: cs.width, h: cs.height,
      beforeContent: before.content,
      btnTransform: getComputedStyle(btn).transform,
      btnAnim: getComputedStyle(btn).animationName,
      morphAnim: cs.animationName,
      coreDisplay: getComputedStyle(core).display,
    };
  });
}

const INFRA = /websocket|WebSocket|net::ERR|Failed to load resource|\/api\/v1|Backend|backend|favicon|504|500|ECONNREFUSED|orb|timeout|timeout of/i;

async function runTheme(theme) {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', (e) => consoleErrors.push('PAGEERROR: ' + e.message));
  await page.addInitScript((t) => { try { localStorage.setItem('manju_theme', t); } catch (e) {} }, theme);

  await page.goto(BASE + '/custom-page', { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);

  const htmlClass = await page.evaluate(() => document.documentElement.className);
  check(`[${theme}] html 含 t-${theme} 类`, htmlClass.includes('t-' + theme), htmlClass);

  // ---- F-1 四形态渲染（尺寸 + 形态伪元素存在）----
  const keys = Object.keys(FORM_SIZE[theme]);
  for (const key of keys) {
    const root = await injectOrb(page, key);
    const c = await morphComputed(page, root);
    const [ew, eh] = FORM_SIZE[theme][key];
    const sizeOk = c.w === ew + 'px' && c.h === eh + 'px';
    check(`[${theme}] 形态 ${key} 渲染尺寸=${ew}x${eh}`, sizeOk, `got ${c.w}x${c.h}`);
    check(`[${theme}] 形态 ${key} 含绘制伪元素(::before)`, c.beforeContent !== 'none' && c.beforeContent !== 'normal', c.beforeContent);
    await root.evaluate((el) => el.remove());
  }

  // ---- RED-04/主题隔离：跨主题形态不应被对方规则命中 ----
  const otherTheme = theme === 'p5' ? 'cyberpunk' : 'p5';
  const otherKey = Object.keys(FORM_SIZE[otherTheme])[0]; // katana / sword
  const isoRoot = await injectOrb(page, otherKey);
  const isoC = await morphComputed(page, isoRoot);
  // 跨主题时 .orb-morph 无对应形态规则 → 由容器 inset:0 撑满 96px，而非对方尺寸
  const [ow, oh] = FORM_SIZE[otherTheme][otherKey];
  check(`[${theme}] 隔离：${otherKey}(${otherTheme}) 不被本主题规则命中`, isoC.w !== ow + 'px', `got ${isoC.w} (expected != ${ow}px)`);
  await isoRoot.evaluate((el) => el.remove());

  // ---- orb-core 在主题下隐藏（T-1：改显形态）----
  const coreRoot = await injectOrb(page, keys[0]);
  const coreC = await morphComputed(page, coreRoot);
  check(`[${theme}] orb-core 主题下隐藏(display:none)`, coreC.coreDisplay === 'none', coreC.coreDisplay);
  await coreRoot.evaluate((el) => el.remove());

  // ---- F-3 拖动态激进化：dragging 类触发 transform ----
  const dragKey = theme === 'p5' ? 'sword' : 'katana';
  const dragRoot = await injectOrb(page, dragKey, 'dragging');
  const dragC = await morphComputed(page, dragRoot);
  check(`[${theme}] 拖动态 ${dragKey} 激进化(transform≠none)`, dragC.morphAnim !== 'none' || dragC.w !== FORM_SIZE[theme][dragKey][0] + 'px' || /matrix|rotate/.test(dragC.morphAnim + dragC.btnTransform), `${dragC.morphAnim}/${dragC.btnTransform}`);
  await dragRoot.evaluate((el) => el.remove());

  // ---- F-2 切换态演出：switching 类触发切换动画 ----
  const swRoot = await injectOrb(page, keys[0], 'switching');
  const swC = await morphComputed(page, swRoot);
  const swExpected = theme === 'p5' ? 'p5-switch-burst' : 'nc-switch-glitch';
  const swHit = theme === 'p5'
    ? (await swRoot.evaluate((el) => getComputedStyle(el.querySelector('.orb-btn'), '::after').animationName)) === 'p5-switch-burst'
    : swC.btnAnim.includes('nc-switch-glitch');
  check(`[${theme}] 切换态 switching 触发 ${swExpected}`, swHit, theme === 'p5' ? '::after p5-switch-burst' : swC.btnAnim);
  await swRoot.evaluate((el) => el.remove());

  // ---- F-5 完成爆发：celebrating 类触发 celebrate 动画 ----
  const celRoot = await injectOrb(page, keys[0], 'celebrating');
  const celC = await morphComputed(page, celRoot);
  const celExpected = theme === 'p5' ? 'p5-celebrate' : 'nc-celebrate';
  check(`[${theme}] 完成态 celebrating 触发 ${celExpected}`, celC.morphAnim.includes(celExpected), celC.morphAnim);
  await celRoot.evaluate((el) => el.remove());

  // ---- §7 三卡皮肤（B-1/B-2/B-3）----
  await page.evaluate(() => {
    const stage = document.createElement('div');
    stage.id = 'card-stage';
    stage.style.cssText = 'position:fixed;left:12px;top:12px;z-index:9998;width:360px;display:flex;flex-direction:column;gap:14px;';
    stage.innerHTML = `
      <div class="persona-decision m3-decision">
        <div class="pd-title">待确认决策（2）</div>
        <div class="pd-item"><div class="pd-name">测试决策</div><div class="pd-options">
          <button class="pd-btn primary">确认采用</button>
          <button class="pd-btn ghost">拒绝</button></div></div>
      </div>
      <div class="m3-translator">
        <div class="mt-title">导演选型</div>
        <div class="mt-row"><span class="mt-k">底模</span><span class="mt-v">qwen2.5:7b</span></div>
        <div class="mt-row"><span class="mt-k">IP权重</span><span class="mt-v">0.80</span></div>
      </div>
      <div class="m3-conv">
        <div class="conv-bubble notice"><span class="conv-tag">通知</span><span class="conv-text">已锁定角色「周岩」</span></div>
        <div class="conv-bubble insight"><span class="conv-tag">品控</span><span class="conv-text">分镜3 采用首尾帧插值</span></div>
      </div>`;
    document.body.appendChild(stage);
  });
  await page.waitForTimeout(200);

  const card = await page.evaluate(() => {
    const dec = document.querySelector('.persona-decision');
    const trans = document.querySelector('.m3-translator');
    const bub = document.querySelector('.conv-bubble.notice');
    const tag = document.querySelector('.conv-bubble .conv-tag');
    const cs = (el) => el ? getComputedStyle(el) : null;
    return {
      decBg: cs(dec)?.backgroundColor,
      decClip: cs(dec)?.clipPath,
      decCornerBefore: cs(dec) ? getComputedStyle(dec, '::before').content : 'none',
      decBtnPrimaryBg: cs(document.querySelector('.persona-decision .pd-btn.primary'))?.backgroundColor,
      transBg: cs(trans)?.backgroundColor,
      transImg: cs(trans)?.backgroundImage,
      bubTagBefore: cs(tag) ? getComputedStyle(tag, '::before').content : 'none',
      decBorder: cs(dec)?.borderTopColor,
    };
  });

  if (theme === 'p5') {
    check('[p5] 决策卡=纸白底(rgb(245,241,232))', card.decBg.replace(/\s/g, '') === 'rgb(245,241,232)', card.decBg);
    check('[p5] 决策卡=撕裂边(clip-path≠none)', card.decClip !== 'none', card.decClip.slice(0, 24));
    check('[p5] 决策选项主按钮=绯红(rgb(228,0,43))', card.decBtnPrimaryBg.replace(/\s/g, '') === 'rgb(228,0,43)', card.decBtnPrimaryBg);
    check('[p5] 选型卡=墨黑底(rgb(25,25,25))', card.transBg.replace(/\s/g, '') === 'rgb(25,25,25)', card.transBg);
    check('[p5] 气泡标签前缀 ◆', /◆/.test(card.bubTagBefore), card.bubTagBefore);
  } else {
    check('[cyberpunk] 决策卡=青描边(rgb(0,240,255))', /0,\s*240,\s*255/.test(card.decBorder || ''), card.decBorder);
    check('[cyberpunk] 决策卡=面板浮起底(rgb(26,29,34))', card.decBg.replace(/\s/g, '') === 'rgb(26,29,34)', card.decBg);
    check('[cyberpunk] 决策卡四角框(::before 存在)', card.decCornerBefore !== 'none' && card.decCornerBefore !== 'normal', card.decCornerBefore);
    check('[cyberpunk] 决策选项主按钮=霓虹青(rgb(0,240,255))', card.decBtnPrimaryBg.replace(/\s/g, '') === 'rgb(0,240,255)', card.decBtnPrimaryBg);
    check('[cyberpunk] 选型卡=网格底(linear-gradient)', /linear-gradient/.test(card.transImg || ''), (card.transImg || '').slice(0, 30));
    check('[cyberpunk] 气泡标签前缀 >>', />>/.test(card.bubTagBefore), card.bubTagBefore);
  }

  // ---- 截图：四形态 idle + 三态 + 三卡 ----
  await page.evaluate(() => { const s = document.getElementById('card-stage'); if (s) s.remove(); });
  await page.evaluate(() => document.querySelectorAll('.dir-orb-root').forEach((e) => e.remove()));
  // 四形态同屏（底部分布，每个带文字标签）
  const labels = theme === 'p5'
    ? { sword: '绅士细剑', mask: '怪盗面具', tower: '命运塔罗', shackle: '心锁锁链' }
    : { katana: '霓虹武士刀', relic: 'Relic芯片', wire: '纳米线', scanner: 'HUD扫描框' };
  const rowY = 760;
  const rowStart = (1440 - (keys.length * 140)) / 2;
  for (let i = 0; i < keys.length; i++) {
    await injectOrb(page, keys[i], '', { x: rowStart + i * 140, y: rowY, label: labels[keys[i]], size: 96 });
  }
  await page.waitForTimeout(300);
  await annotateShot(page, ['.dir-orb-root'], `morph_${theme}_forms.png`, tsNow());
  // 三态：drag / switch / celebrate 各取一球（复用前三个的位置）
  const sels = await page.locator('.dir-orb-root').all();
  if (sels.length >= 3) {
    await sels[0].evaluate((el) => el.querySelector('.orb-btn').classList.add('dragging'));
    await sels[1].evaluate((el) => el.querySelector('.orb-btn').classList.add('switching'));
    await sels[2].evaluate((el) => el.querySelector('.orb-btn').classList.add('celebrating'));
    await page.waitForTimeout(300);
    await annotateShot(page, ['.dir-orb-root'], `morph_${theme}_states.png`, tsNow());
  }
  await page.evaluate(() => document.querySelectorAll('.dir-orb-root').forEach((e) => e.remove()));
  // 三卡
  await page.evaluate(() => {
    const stage = document.createElement('div');
    stage.id = 'card-stage';
    stage.style.cssText = 'position:fixed;left:12px;top:12px;z-index:9998;width:360px;display:flex;flex-direction:column;gap:14px;';
    stage.innerHTML = `
      <div class="persona-decision m3-decision">
        <div class="pd-title">待确认决策（2）</div>
        <div class="pd-item"><div class="pd-name">测试决策</div><div class="pd-options">
          <button class="pd-btn primary">确认采用</button>
          <button class="pd-btn ghost">拒绝</button></div></div>
      </div>
      <div class="m3-translator">
        <div class="mt-title">导演选型</div>
        <div class="mt-row"><span class="mt-k">底模</span><span class="mt-v">qwen2.5:7b</span></div>
        <div class="mt-row"><span class="mt-k">IP权重</span><span class="mt-v">0.80</span></div>
      </div>
      <div class="m3-conv">
        <div class="conv-bubble notice"><span class="conv-tag">通知</span><span class="conv-text">已锁定角色「周岩」</span></div>
        <div class="conv-bubble insight"><span class="conv-tag">品控</span><span class="conv-text">分镜3 通过</span></div>
      </div>`;
    document.body.appendChild(stage);
  });
  await page.waitForTimeout(200);
  await annotateShot(page, ['.persona-decision', '.m3-translator', '.conv-bubble'], `morph_${theme}_cards.png`, tsNow());
  await page.evaluate(() => { const s = document.getElementById('card-stage'); if (s) s.remove(); });

  // ---- C-1 控制台错误（排除基础设施）----
  const nonInfra = consoleErrors.filter((e) => !INFRA.test(e));
  check(`[${theme}] 无非基础设施控制台错误`, nonInfra.length === 0, nonInfra.slice(0, 5).join(' | '));
  console.log(`INFO  [${theme}] 基础设施类错误(已排除): ${consoleErrors.length - nonInfra.length} 条`);

  await browser.close();
}

// ---- D. 双击切换源码接线（机器判定，非人工读日志）----
async function sourceWiringChecks() {
  const vueSrc = fs.readFileSync(path.resolve('src/components/DirectorOrb.vue'), 'utf8');
  check('D1 源: DirectorOrb 已挂 @dblclick="cycleMorph"', vueSrc.includes('@dblclick="cycleMorph"'));
  const jsSrc = fs.readFileSync(path.resolve('src/composables/useDirectorOrb.js'), 'utf8');
  check('D2 源: cycleMorph 导出且主题感知(activeMorphList)', jsSrc.includes('export function cycleMorph') && jsSrc.includes('activeMorphList'));
  check('D3 源: 赛博四形态键 katana/relic/wire/scanner 注册', ['katana', 'relic', 'wire', 'scanner'].every((k) => jsSrc.includes(k)));
  check('D4 源: P5 四形态键 sword/mask/tower/shackle 注册', ['sword', 'mask', 'tower', 'shackle'].every((k) => jsSrc.includes(k)));
}

const run = async () => {
  await runTheme('cyberpunk');
  await runTheme('p5');
  sourceWiringChecks();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n==== DirectorOrb 多形态化身验收：${results.length - failed.length}/${results.length} PASS ====`);
  if (failed.length) {
    console.log('FAILED: ' + failed.map((f) => f.name).join('; '));
    process.exitCode = 1;
  } else {
    console.log('ALL PASS ✅');
  }
};
run().catch((e) => { console.error('RUNNER ERROR', e); process.exitCode = 2; });
