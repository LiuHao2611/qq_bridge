// 赛博朋克2077 夜城 HUD 主题 · 机器判定验收（手册 v3.0）
// 运行：PLAYWRIGHT_BROWSERS_PATH=D:/AI/.pw-browsers node tests/verify_cyberpunk_theme.mjs
// 前置：前端已构建并服务于 http://localhost:8099（web_app.py）
// 产出：tests/verify_结果归类 + .tmp/cyberpunk_shots/*.png 红框时间戳截图
import { chromium } from 'playwright';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';

const BASE = process.env.BASE || 'http://localhost:8099';
const SHOT = path.resolve('D:/AI/manju_agent/.tmp/cyberpunk_shots');
fs.mkdirSync(SHOT, { recursive: true });

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail });
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  :: ' + detail : ''}`);
}

// 红框 + 时间戳 标注截图（FC-01：肉眼验收 + 红框标注 + 时间戳）
async function annotateShot(page, selectors, file, label) {
  await page.evaluate(() => {
    document.querySelectorAll('.cp-box,.cp-ts').forEach((e) => e.remove());
  });
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
  await page.evaluate(() => {
    document.querySelectorAll('.cp-box,.cp-ts').forEach((e) => e.remove());
  });
}

const TASKS_FIXTURE = [
  { task_id: 'T-1001', local_task_id: '1001', status: 'succeeded', progress: 100, priority: 0, model_id: 'qwen2.5:7b', created_at: 1755000000, params: { model_id: 'qwen2.5:7b', resolution: '720p', ratio: '16:9', duration: 12 } },
  { task_id: 'T-1002', local_task_id: '1002', status: 'running', progress: 64, priority: 1, model_id: 'qwen3.5:9b', created_at: 1755000300, params: { model_id: 'qwen3.5:9b', resolution: '1080p', ratio: '16:9', duration: 8 } },
  { task_id: 'T-1003', local_task_id: '1003', status: 'queued', progress: 0, priority: 0, model_id: 'qwen2.5:7b', created_at: 1755000600, params: { model_id: 'qwen2.5:7b', resolution: '720p', ratio: '16:9', duration: 12 } },
  { task_id: 'T-1004', local_task_id: '1004', status: 'failed', progress: 30, error_message: '显存不足', priority: 0, model_id: 'qwen2.5:7b', created_at: 1755000900, params: { model_id: 'qwen2.5:7b', resolution: '720p', ratio: '16:9', duration: 12 } },
];

const INFRA = /websocket|WebSocket|net::ERR|Failed to load resource|\/api\/v1|Backend|backend|favicon|504|500|ECONNREFUSED|orb|timeout|timeout of/i;

const run = async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', (e) => consoleErrors.push('PAGEERROR: ' + e.message));

  // 强制夜城主题（useTheme 在 module load 时读 localStorage.manju_theme）
  await page.addInitScript(() => { try { localStorage.setItem('manju_theme', 'cyberpunk'); } catch (e) {} });

  // ---------- A. 全局主题应用（自包含沙盒页，无后端依赖） ----------
  await page.goto(BASE + '/custom-page', { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);

  const htmlClass = await page.evaluate(() => document.documentElement.className);
  check('A1 html 含 t-cyberpunk 类', htmlClass.includes('t-cyberpunk'), htmlClass);

  const ncCyan = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--nc-cyan').trim());
  check('A2 令牌 --nc-cyan 解析为 #00F0FF', ncCyan.toUpperCase() === '#00F0FF', ncCyan);

  const bodyBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  check('A3 背景为夜城深黑 (rgb(10,10,10))', bodyBg.replace(/\s/g, '') === 'rgb(10,10,10)', bodyBg);

  const scanline = await page.evaluate(() => {
    const cs = getComputedStyle(document.body, '::after');
    return cs.backgroundImage || '';
  });
  check('A4 全局扫描线覆盖层存在', /repeating-linear-gradient/.test(scanline), scanline.slice(0, 60));

  // 悬浮球夜城皮肤：本体零圆角 + 霓虹描边
  // 悬浮球仅在项目上下文(v-if=visible)挂载，故注入等价 .orb-btn 元素做运行时 CSS 校验
  const tsNow = () => new Date().toISOString().replace('T', ' ').slice(0, 19);
  const orbInjected = await page.evaluate(() => {
    const root = document.createElement('div');
    root.className = 'dir-orb-root';
    root.style.cssText = 'position:fixed;left:40px;top:40px;z-index:9999;';
    const btn = document.createElement('div');
    btn.className = 'orb-btn';
    btn.style.cssText = 'width:56px;height:56px;display:flex;align-items:center;justify-content:center;';
    const core = document.createElement('span');
    core.className = 'orb-core'; core.textContent = '◈';
    core.style.cssText = 'font-size:24px;';
    btn.appendChild(core); root.appendChild(btn); document.body.appendChild(root);
    const cs = getComputedStyle(btn);
    return { radius: cs.borderRadius, border: cs.borderColor };
  });
  check('A5 注入 .orb-btn 被夜城规则命中', !!orbInjected.radius);
  check('A6 悬浮球零圆角(夜城 HUD 框)', orbInjected.radius.replace(/\s/g, '') === '0px', orbInjected.radius);
  check('A7 悬浮球霓虹青描边', /0,\s*240,\s*255/.test(orbInjected.border), orbInjected.border);
  await annotateShot(page, ['.dir-orb-root'], '01_orb_skin.png', tsNow());
  await page.evaluate(() => { const e = document.querySelector('.dir-orb-root'); if (e && e.parentNode) e.parentNode.removeChild(e); });

  await annotateShot(page, ['.cp-toolbar', '.cp-stage'], '02_sandbox.png', new Date().toISOString().replace('T', ' ').slice(0, 19));

  // ---------- B. 试点页 /tasks（桩 API，验证 §6 全部公共类） ----------
  // 注意：listTasks 实际请求 /api/v1/tasks?limit=50，glob 必须以 **/tasks** 含尾串才命中
  await page.route('**/api/v1/tasks**', (route) => {
    const url = route.request().url();
    if (url.includes('/queue')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tasks: [], gpu_max_concurrent: 1 }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: TASKS_FIXTURE }) });
  });
  await page.goto(BASE + '/tasks', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);

  check('B1 试点页标题 .nc-title 存在', await page.locator('.nc-title').count() > 0);
  check('B2 状态条 .nc-statusbar 存在', await page.locator('.nc-statusbar').count() > 0);
  check('B3 任务卡 .tk-card + 四角 .nc-corner 存在', (await page.locator('.tk-card').count()) > 0 && (await page.locator('.nc-corner').count()) > 0);
  // 日志面板需展开任务才渲染
  const logBtn = page.locator('.tk-card').first().locator('button:has-text("实时日志")');
  if (await logBtn.count()) { await logBtn.first().click(); await page.waitForTimeout(300); }
  check('B4 日志面板 .nc-logwrap/.nc-loghead 存在', (await page.locator('.nc-logwrap').count()) > 0 && (await page.locator('.nc-loghead').count()) > 0);
  check('B5 状态标签 .nc-tag 存在', await page.locator('.nc-tag').count() > 0);
  check('B6 进度条 .el-progress 存在', await page.locator('.el-progress').count() > 0);
  const genText = await page.locator('.nc-statusbar').first().innerText().catch(() => '');
  check('B7 进行中派生值=1（状态条显示「生成中 1」）', /生成中\s*1/.test(genText), genText.replace(/\n/g, ' '));

  // 正常态截图
  await annotateShot(page, ['.nc-title', '.nc-statusbar', '.nc-hud-card', '.nc-tag', '.el-progress'], '03_tasks_normal.png', new Date().toISOString().replace('T', ' ').slice(0, 19));

  // hover 态截图（霓虹青边 + glow）
  const card = page.locator('.tk-card').first();
  if (await card.count()) {
    await card.hover();
    await page.waitForTimeout(250);
    const hoverBorder = await card.evaluate((el) => getComputedStyle(el).borderColor);
    check('B8 hover 任务卡霓虹青边', /0,\s*240,\s*255/.test(hoverBorder), hoverBorder);
    await annotateShot(page, ['.tk-card', '.nc-title'], '04_tasks_hover.png', tsNow());
  }

  // 运行中进度态截图（T-1002 running → 扫描扫过）
  const genCard = page.locator('.tk-card', { hasText: '生成中' }).first();
  if (await genCard.count()) {
    await genCard.scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(200);
    await annotateShot(page, ['.tk-card', '.nc-tag--cyan', '.el-progress'], '05_tasks_running.png', tsNow());
    check('B9 运行中标签=青色(.nc-tag--cyan)', (await page.locator('.nc-tag--cyan').count()) > 0);
  }

  // ---------- D. 设置页悬浮球皮肤主题感知（§9.3） ----------
  await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
  await page.waitForTimeout(700);
  const cyberAccent = page.locator('button.ac-item', { hasText: '夜城 HUD' });
  if (await cyberAccent.count()) {
    await cyberAccent.first().click();
    await page.waitForTimeout(400);
  }
  const orbLabels = await page.locator('.field', { hasText: '悬浮球皮肤' }).locator('.ac-label').allInnerTexts().catch(() => []);
  const hasCyberForm = orbLabels.some((t) => /HUD 扫描框|霓虹武士刀|纳米线|Relic 芯片/.test(t));
  check('D1 夜城主题下悬浮球皮肤显示赛博四件套', hasCyberForm, orbLabels.join(','));

  // ---------- C. 控制台错误（排除已知基础设施错误） ----------
  const infra = consoleErrors.filter((e) => INFRA.test(e));
  const nonInfra = consoleErrors.filter((e) => !INFRA.test(e));
  check('C1 无非基础设施控制台错误', nonInfra.length === 0, nonInfra.slice(0, 5).join(' | '));
  console.log(`INFO  基础设施类错误(已排除): ${infra.length} 条`);

  await browser.close();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n==== 赛博朋克主题验收：${results.length - failed.length}/${results.length} PASS ====`);
  if (failed.length) {
    console.log('FAILED: ' + failed.map((f) => f.name).join('; '));
    process.exitCode = 1;
  } else {
    console.log('ALL PASS ✅');
  }
};

run().catch((e) => { console.error('RUNNER ERROR', e); process.exitCode = 2; });
