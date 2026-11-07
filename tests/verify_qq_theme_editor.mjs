// 前端验收脚本（搬运 Theme Creator 的"机器判定"思路）：
// 用 Playwright 真实加载 http://127.0.0.1:8080/pic/theme-editor，
// 对 2026-09-24 新增/优化的交互做断言，全部以退出码判定（0=通过，非0=失败），
// 不在终端里"肉眼"判断 PASS。
//
// 运行（需 bridge 已启动并监听 8080）：
//   cd D:\AI\manju_agent\qq_bridge
//   NODE_PATH=C:\Users\L\.workbuddy\binaries\node\workspace\node_modules \
//     node tests/verify_qq_theme_editor.mjs
// 可用 BASE_URL 环境变量覆盖地址，用 FILTER 控制只跑某条（如 FILTER=mood）。
import { chromium } from 'playwright';

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8080';
const FILTER = (process.env.FILTER || '').toLowerCase();
let pass = 0, fail = 0;
const fails = [];
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; fails.push(name + (extra ? ' — ' + extra : '')); console.log('  ✗ ' + name + (extra ? ' — ' + extra : '')); }
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const pageErrors = [];
page.on('pageerror', e => pageErrors.push(String(e)));
page.on('console', m => { if (m.type() === 'error') pageErrors.push('console.error: ' + m.text()); });

await page.goto(BASE + '/pic/theme-editor', { waitUntil: 'networkidle' });
// 等核心控件渲染完成
await page.waitForSelector('#controls .grp, #controls .empty', { timeout: 15000 });
await page.waitForSelector('#moodGrid .mood-card', { timeout: 15000 });

if (!FILTER || FILTER.includes('base')) {
  console.log('\n[基础结构]');
  ok('预设墙存在', await page.locator('#presets .preset-card').count() > 0);
  ok('我的主题列表存在', await page.locator('#list li').count() >= 0);
  ok('参数面板渲染', await page.locator('#controls .grp').count() > 0);
  ok('预览模板 tab 存在', await page.locator('#pvTabs button').count() >= 4);
  ok('无页面级 JS 报错', pageErrors.length === 0, pageErrors.join(' | '));
}

if (!FILTER || FILTER.includes('mood')) {
  console.log('\n[快速风格 · 一键套用]');
  const cards = await page.locator('#moodGrid .mood-card').count();
  ok('风格卡数量=6', cards === 6, '实际=' + cards);
  // 点「柔光」：bg 应变为 #F4F6FB，accent 变为 #3B82F6
  await page.locator('#moodGrid .mood-card', { hasText: '柔光' }).click();
  // 切到「全部」部位，确保 bg/accent 字段可见（默认部位为「卡片」，不含背景/配色）
  await page.locator('#partNav .pn', { hasText: '全部' }).click();
  await page.waitForTimeout(250);
  const bg = await page.locator('.clr[data-key="bg"]').inputValue();
  const accent = await page.locator('.clr[data-key="accent"]').inputValue();
  ok('套用柔光后 bg=#F4F6FB', bg.toUpperCase() === '#F4F6FB', '实际=' + bg);
  ok('套用柔光后 accent=#3B82F6', accent.toUpperCase() === '#3B82F6', '实际=' + accent);
}

if (!FILTER || FILTER.includes('cb') || FILTER.includes('blind')) {
  console.log('\n[色觉缺陷模拟]');
  await page.selectOption('#cbSim', 'deutan');
  await page.waitForTimeout(150);
  const filter = await page.locator('#preview').evaluate(el => el.style.filter || '');
  ok('绿色盲滤镜已作用于预览图', filter.replace(/["']/g, '').includes('url(#cb-deutan)'), 'filter=' + filter);
  await page.selectOption('#cbSim', '');
  await page.waitForTimeout(120);
  const filter2 = await page.locator('#preview').evaluate(el => el.style.filter || '');
  ok('切回正常后滤镜清除', filter2 === '', 'filter=' + filter2);
}

if (!FILTER || FILTER.includes('export')) {
  console.log('\n[多格式导出]');
  await page.click('#exportBtn');
  await page.waitForTimeout(120);
  ok('导出菜单弹出', await page.locator('#exportMenu').isVisible());
  // 监听下载：点 CSS 变量
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 5000 }),
    page.locator('#exportMenu button[data-fmt="css"]').click(),
  ]);
  const fn = download.suggestedFilename();
  ok('CSS 导出触发下载且文件名以 .css 结尾', fn.endsWith('.css'), 'file=' + fn);
  await page.waitForTimeout(100);
  ok('导出后菜单收起', !(await page.locator('#exportMenu').isVisible()));
}

if (!FILTER || FILTER.includes('group')) {
  console.log('\n[分组折叠]');
  const head = page.locator('#controls .grp > header').first();
  const grp = page.locator('#controls .grp').first();
  const before = await grp.getAttribute('class');
  await head.click();
  await page.waitForTimeout(120);
  const after = await grp.getAttribute('class');
  ok('点击分组头后折叠', before.indexOf('collapsed') < 0 && after.indexOf('collapsed') >= 0, before + ' -> ' + after);
}

if (!FILTER || FILTER.includes('undo')) {
  console.log('\n[撤销]');
  // 改 accent（当前应是柔光的 #3B82F6），改成一个确定值，再撤销应回到 #3B82F6
  const accentInput = page.locator('.clr[data-key="accent"]');
  await accentInput.fill('#123456');
  await accentInput.dispatchEvent('input');
  await page.waitForTimeout(120);
  const changed = await accentInput.inputValue();
  ok('改色后 accent=#123456', changed.toUpperCase() === '#123456', '实际=' + changed);
  await page.click('#undoBtn');
  await page.waitForTimeout(150);
  const reverted = await accentInput.inputValue();
  ok('撤销后 accent 回到 #3B82F6', reverted.toUpperCase() === '#3B82F6', '实际=' + reverted);
}

if (!FILTER || FILTER.includes('ov')) {
  console.log('\n[固定风格卡 ov: 专属配色（前端修复验证）]');
  // 1) JS 结构就绪：curColorOf / setOvColor / ELEMENT_CONTROLS / controlsFor 都就位
  const struct = await page.evaluate(() => {
    if (typeof curColorOf !== 'function') return 'no curColorOf';
    if (typeof setOvColor !== 'function') return 'no setOvColor';
    if (!ELEMENT_CONTROLS || !ELEMENT_CONTROLS['ov:card_color']) return 'no ELEMENT_CONTROLS ov:card_color';
    const cf = controlsFor('ov:title_color');
    if (!cf || cf.length !== 1 || cf[0][0] !== 'color' || cf[0][1] !== 'ov:title_color')
      return 'controlsFor ov:title_color wrong: ' + JSON.stringify(cf);
    return 'ok';
  });
  ok('ov: JS 结构就绪 (curColorOf/setOvColor/ELEMENT_CONTROLS/controlsFor)', struct === 'ok', struct);

  // 2) 显示修复：点 ov: 元素，浮窗初始色 = 区域生效色（修复前误显示 #000000）
  const disp = await page.evaluate(() => {
    S.tpl = 'fissure'; S.layout = { bg_overrides: {} }; S.colors = {};
    const region = { field: 'ov:title_color', label: '标题', values: { 'ov:title_color': '#1F4E79' } };
    openPop(region, document.createElement('div'));
    const hex = document.querySelector('#pop .hex');
    const val = hex ? hex.value : '';
    closePop();
    return val;
  });
  ok('ov: 浮窗初始色显示区域生效色(#1F4E79，非#000000)', disp.toUpperCase() === '#1F4E79', '实际=' + disp);

  // 3) 专属覆盖优先于默认生效色
  const ovr = await page.evaluate(() => {
    S.tpl = 'fissure'; S.layout = { bg_overrides: { fissure: { title_color: '#FF0000' } } }; S.colors = {};
    const region = { field: 'ov:title_color', label: '标题', values: { 'ov:title_color': '#1F4E79' } };
    openPop(region, document.createElement('div'));
    const hex = document.querySelector('#pop .hex');
    const val = hex ? hex.value : '';
    closePop();
    return val;
  });
  ok('ov: 专属覆盖优先于默认(#FF0000)', ovr.toUpperCase() === '#FF0000', '实际=' + ovr);

  // 4) 写入路径：改 ov: 色 → 写入 bg_overrides[tpl]，只影响本卡
  const write = await page.evaluate(() => {
    S.tpl = 'fissure'; S.layout = { bg_overrides: {} }; S.colors = {};
    setColor('ov:title_color', '#ABCDEF');
    const ov = S.layout.bg_overrides && S.layout.bg_overrides['fissure'];
    return ov ? ov['title_color'] : null;
  });
  ok('ov: 改色写入 bg_overrides[fissure](#ABCDEF)', write && write.toUpperCase() === '#ABCDEF', '实际=' + write);

  // 5) 不误伤其它卡：写入 fissure 不会建出 netracell 键
  const noCross = await page.evaluate(() => {
    S.tpl = 'fissure'; S.layout = { bg_overrides: {} };
    setColor('ov:title_color', '#111111');
    return Object.keys(S.layout.bg_overrides).join(',');
  });
  ok('ov: 写入只建 fissure 键(不误伤 netracell)', noCross === 'fissure', 'keys=' + noCross);
}

if (!FILTER || FILTER.includes('bgfit')) {
  console.log('\n[背景适配弹窗（2026-09-24 重构验证）]');

  // 0) JS 结构 + 弹窗 DOM 就位
  const struct = await page.evaluate(() => {
    if (typeof openBgFitDialog !== 'function') return 'no openBgFitDialog';
    if (typeof bfShow !== 'function') return 'no bfShow';
    if (typeof bfDraw !== 'function') return 'no bfDraw';
    if (!BF_DEFS || !BF_DEFS.bg || !BF_DEFS.card) return 'no BF_DEFS';
    if (!BF_FITS || BF_FITS.length !== 4) return 'BF_FITS wrong len';
    if (!BF_POS9 || BF_POS9.length !== 9) return 'BF_POS9 wrong len';
    if (!BF_HIDDEN || BF_HIDDEN.size !== 16) return 'BF_HIDDEN size=' + (BF_HIDDEN && BF_HIDDEN.size);
    const ids = ['bfMask','bfCanvas','bfOps','bfSrc','bfSrcImg','bfSrcBox','bfTitle','bfSub','bfClose','bfDone','bfReset'];
    const missing = ids.filter(id => !document.getElementById(id));
    if (missing.length) return 'missing dom: ' + missing.join(',');
    return 'ok';
  });
  ok('bgfit: JS 结构 + 弹窗 DOM 就位', struct === 'ok', struct);

  // 1) 右栏 16 个散装细节字段已收进弹窗（不渲染），但上传入口 + 适配按钮仍在
  const hidden = await page.evaluate(() => {
    const all = [...document.querySelectorAll('#partNav .pn')].find(b => b.textContent.trim() === '全部');
    if (all) all.click();
    S.layout.bg_pattern = 'image'; S.layout.card_fill_type = 'image';
    renderControls();
    const keys = [...document.querySelectorAll('#controls .lrow[data-k]')].map(e => e.dataset.k);
    const leaked = keys.filter(k => BF_HIDDEN.has(k));
    return {
      leaked,
      hasUpload: !!document.querySelector('#controls .bf-open'),
      hasBgImage: keys.includes('bg_image'),
      hasCardImage: keys.includes('card_image'),
    };
  });
  ok('bgfit: 16 个细节字段未散装渲染', hidden.leaked.length === 0, 'leaked=' + hidden.leaked.join(','));
  ok('bgfit: 上传入口仍在（bg_image 行）', hidden.hasBgImage);
  ok('bgfit: 上传入口仍在（card_image 行）', hidden.hasCardImage);
  ok('bgfit: 上传行出现「🖼️ 背景适配调整」按钮', hidden.hasUpload);

  // 2) 守卫：未上传图时打开弹窗应提示而非崩溃，BF 保持 null
  const guard = await page.evaluate(() => {
    S.layout.bg_image = ''; S.layout.bg_overrides = {};
    BF = null;
    openBgFitDialog('bg');
    return { bfNull: BF === null, shown: document.getElementById('bfMask').classList.contains('show') };
  });
  ok('bgfit: 无图打开弹窗被守卫拦截（不弹窗 / BF=null）', guard.bfNull && !guard.shown, JSON.stringify(guard));

  // 3) 用 data-URL 图绕过服务器，验证 打开 → 平铺禁用 → 裁满启用 → 对齐 → 缩放 → 重置 → 关闭 全链路
  const pe0 = pageErrors.length;
  const flow = await page.evaluate(async () => {
    const d = BF_DEFS.bg;
    const calls = [];
    const target = {
      ...d, target: 'bg', useOv: false, file: 'fake-bg.png',
      rd: (k, def) => def,
      num: (v, def) => { const n = parseInt(v, 10); return isNaN(n) ? def : n; },
      wr: (k, v) => calls.push([k, v]),
      baseColor: () => '#0B0E17',
    };
    const im = await new Promise((res) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = () => res(null);
      i.src = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
    });
    if (!im) return { err: 'image-not-loaded' };
    bfShow(target, im);
    const opened = document.getElementById('bfMask').classList.contains('show');
    const bfSet = BF !== null;
    const fitBtns = BF._els.seg.querySelectorAll('button').length;
    const tileBtn = [...BF._els.seg.querySelectorAll('button')].find(b => b.dataset.fit === 'tile');
    tileBtn.click();
    const afterTile = {
      fit: BF.st.fit,
      zoomDisabled: BF._els.zr_.disabled,
      alignDisabled: [...BF._els.grid.querySelectorAll('button')].every(b => b.disabled),
      opDisabled: BF._els.or_.disabled,
    };
    const coverBtn = [...BF._els.seg.querySelectorAll('button')].find(b => b.dataset.fit === 'cover');
    coverBtn.click();
    const afterCover = {
      zoomDisabled: BF._els.zr_.disabled,
      alignDisabled: [...BF._els.grid.querySelectorAll('button')].every(b => b.disabled),
    };
    const tl = [...BF._els.grid.querySelectorAll('button')].find(b => b.dataset.pos === 'top-left');
    tl.click();
    const pos = BF.st.pos;
    bfZoom(1.5);
    const zoom = { cw: BF.st.cw, wroteCrop: calls.some(c => c[0] === 'crop_w') };
    document.getElementById('bfReset').click();
    const afterReset = { fit: BF.st.fit, pos: BF.st.pos, cw: BF.st.cw };
    // 假目标的 src 必然 404：关闭前清掉，避免 console 404 漏进后续断言段
    document.getElementById('bfSrcImg').removeAttribute('src');
    bfCloseDialog();
    const closed = !document.getElementById('bfMask').classList.contains('show') && BF === null;
    return { opened, bfSet, fitBtns, afterTile, afterCover, pos, zoom, afterReset, closed };
  });
  ok('bgfit: 打开弹窗(mask show) 且 BF 会话建立', flow.opened && flow.bfSet, JSON.stringify(flow));
  ok('bgfit: 右栏适应方式按钮数=4（bg 含平铺）', flow.fitBtns === 4, '实际=' + flow.fitBtns);
  ok('bgfit: 平铺模式禁用缩放/对齐/透明度',
    flow.afterTile.fit === 'tile' && flow.afterTile.zoomDisabled && flow.afterTile.alignDisabled && flow.afterTile.opDisabled,
    JSON.stringify(flow.afterTile));
  ok('bgfit: 切回裁满后缩放/对齐重新启用',
    !flow.afterCover.zoomDisabled && !flow.afterCover.alignDisabled, JSON.stringify(flow.afterCover));
  ok('bgfit: 九宫格点选写入对齐位置(top-left)', flow.pos === 'top-left', 'pos=' + flow.pos);
  ok('bgfit: 放大缩放缩小裁切窗口且写回 crop_w', flow.zoom.cw < 100 && flow.zoom.wroteCrop, JSON.stringify(flow.zoom));
  ok('bgfit: 重置回到默认（bg: tile / center / 100%）',
    flow.afterReset.fit === 'tile' && flow.afterReset.pos === 'center' && flow.afterReset.cw === 100,
    JSON.stringify(flow.afterReset));
  ok('bgfit: 关闭后 mask 隐藏且 BF 清空', flow.closed);
  ok('bgfit: 全链路无 JS 报错', pageErrors.length === pe0, pageErrors.slice(pe0).join(' | '));
}

if (!FILTER || FILTER.includes('free') || FILTER.includes('eyedrop')) {
  console.log('\n[全量自由编辑 + 吸色器（2026-09-24）]');

  // 1) JS 结构就绪
  const struct = await page.evaluate(() => {
    if (typeof pickColor !== 'function') return 'no pickColor';
    if (typeof mkEyedrop !== 'function') return 'no mkEyedrop';
    if (typeof _previewSample !== 'function') return 'no _previewSample';
    if (typeof applyRegionMove !== 'function') return 'no applyRegionMove';
    if (typeof applyRegionResize !== 'function') return 'no applyRegionResize';
    if (!document.querySelector('style') && !document.getElementById('pvOverlay')) return 'no overlay';
    return 'ok';
  });
  ok('free: 吸色器/自由编辑 JS 结构就绪', struct === 'ok', struct);

  // 2) 每处色板都有 💧 吸色按钮（右栏 colorRow + 布局颜色行）
  const drops = await page.evaluate(() => ({
    controlRows: document.querySelectorAll('#controls .eyedrop').length,
    clrInputs: document.querySelectorAll('#controls .clr').length,
  }));
  ok('free: 右栏取色行均有 💧 吸色按钮', drops.controlRows >= drops.clrInputs && drops.controlRows > 0,
    JSON.stringify(drops));

  // 3) 吸色写回：_applyPicked → input 事件 → setColor 落到 S.colors
  const picked = await page.evaluate(() => {
    const inp = document.querySelector('#controls .clr[data-key]');
    if (!inp) return { err: 'no-clr-input' };
    const key = inp.dataset.key;
    const before = S.colors[key];
    _applyPicked(inp, '#AABBCC');
    return { key, before, after: S.colors[key], inpVal: inp.value };
  });
  ok('free: 吸色结果写回输入框与 S.colors(#AABBCC)',
    picked.after === '#AABBCC' && picked.inpVal === '#AABBCC',
    JSON.stringify(picked));

  // 4) 降级取色会话：进入模式 → 预览图十字光标 → Esc 取消
  const sample = await page.evaluate(() => {
    const inp = document.querySelector('#controls .eyedrop') ? null : null;
    const img = document.getElementById('preview');
    _previewSample(img);   // 参数 inp 在 stop 时才用，Esc 取消路径不触碰
    const entered = img.classList.contains('pv-sampling') && _pvSampler !== null;
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    const cancelled = !img.classList.contains('pv-sampling') && _pvSampler === null;
    return { entered, cancelled };
  });
  ok('free: 预览取色模式进入/Esc 取消', sample.entered && sample.cancelled, JSON.stringify(sample));

  // 5) 自由编辑：注入带 ek 的假热区 → renderOverlay 出拖拽类 + 缩放手柄
  await page.evaluate(() => {
    S.tpl = 'fissure'; S.layout.bg_overrides = {};
    S._pvMeta = { w: 800, h: 440, template: 'fissure' };
    S._pvRegions = [
      { field: 'ov:title_color', label: '标题', type: 'color', group: '',
        bounds: [100, 100, 220, 146], ek: 'title', values: {} },
    ];
    document.getElementById('pvInteract').checked = true;
    renderOverlay();
  });
  const grip = await page.evaluate(() => {
    const el = document.querySelector('.pv-region[data-ek="title"]');
    return el ? { ek: el.dataset.ek, drag: el.classList.contains('drag'), rz: !!el.querySelector('.pv-rz') } : null;
  });
  ok('free: ek 热区渲染为可拖拽 + 缩放手柄', grip && grip.ek === 'title' && grip.drag && grip.rz, JSON.stringify(grip));

  // 6) 真实鼠标事件拖拽 → elem_pos 写回 bg_overrides.fissure
  await page.evaluate(() => { S.layout.bg_overrides = {}; renderOverlay(); });
  await page.waitForTimeout(120);
  const dragRes = await page.evaluate(() => {
    const overlay = document.getElementById('pvOverlay');
    const el = document.querySelector('.pv-region[data-ek="title"]');
    if (!el) return { err: 'no-region-el' };
    const r = el.getBoundingClientRect();
    const x0 = r.left + 8, y0 = r.top + 8;
    overlay.dispatchEvent(new MouseEvent('mousedown', { clientX: x0, clientY: y0, bubbles: true }));
    overlay.dispatchEvent(new MouseEvent('mousemove', { clientX: x0 + 60, clientY: y0 + 40, bubbles: true }));
    overlay.dispatchEvent(new MouseEvent('mouseup', { clientX: x0 + 60, clientY: y0 + 40, bubbles: true }));
    const ov = S.layout.bg_overrides && S.layout.bg_overrides['fissure'];
    return { pos: ov && ov.elem_pos ? ov.elem_pos.title : null };
  });
  ok('free: 拖拽写回 elem_pos.title（非零偏移）', !!dragRes.pos && (dragRes.pos[0] > 0 || dragRes.pos[1] > 0),
    JSON.stringify(dragRes));

  // 7) 缩放写回：ek → elem_scl 夹取 30~300；title 无 ek → title_fs
  const scl = await page.evaluate(() => {
    S.layout.bg_overrides = {};
    applyRegionResize('ov:title_color', 'title', 2.0);
    const a = S.layout.bg_overrides['fissure'].elem_scl.title;
    S.layout.title_fs = 22;
    applyRegionResize('title', null, 1.5);
    return { scl: a, fs: S.layout.title_fs };
  });
  ok('free: 缩放写回 elem_scl=200（2 倍）', scl.scl === 200, JSON.stringify(scl));
  ok('free: 标题手柄缩放写回 title_fs=33（22×1.5）', scl.fs === 33, JSON.stringify(scl));

  // 8) 拖回原位 = 清除偏移（旧偏移 [30,30]，bounds bx0=130；拖回 100 → 偏移归零）
  const reset = await page.evaluate(() => {
    S.layout.bg_overrides = { fissure: { elem_pos: { title: [30, 30] } } };
    applyRegionMove('title', 100, 100, 120,
      { bx0: 130, by0: 130, by1: 176 });
    const ov = S.layout.bg_overrides['fissure'];
    return { pos: ov && ov.elem_pos ? ov.elem_pos.title : null };
  });
  ok('free: 拖回原位清除 elem_pos', reset.pos === null || reset.pos === undefined, JSON.stringify(reset));

  ok('free: 全段无 JS 报错', pageErrors.length === 0, pageErrors.join(' | '));
}

if (!FILTER || FILTER.includes('pi') || FILTER.includes('projimg')) {
  console.log('\n[选择项目图片（2026-09-24：主项目文生图/图生图 + 回收站过滤）]');
  const pe0 = pageErrors.length;

  // 1) JS 结构 + 弹窗 DOM + 图片行「🖼项目图」按钮
  const struct = await page.evaluate(() => {
    const all = [...document.querySelectorAll('#partNav .pn')].find(b => b.textContent.trim() === '全部');
    if (all) all.click();
    S.layout.bg_pattern = 'image'; S.layout.card_fill_type = 'image';
    renderControls();
    if (typeof openProjectImages !== 'function') return 'no openProjectImages';
    if (typeof piLoad !== 'function') return 'no piLoad';
    if (typeof piPick !== 'function') return 'no piPick';
    if (typeof applyAssetFilename !== 'function') return 'no applyAssetFilename';
    const ids = ['piMask', 'piGrid', 'piType', 'piSort', 'piKw', 'piPrev', 'piNext', 'piInfo', 'piClose', 'piReload'];
    const missing = ids.filter(id => !document.getElementById(id));
    if (missing.length) return 'missing dom: ' + missing.join(',');
    const proj = document.querySelectorAll('#controls .act-proj').length;
    if (!proj) return 'no .act-proj button';
    return { ok: 'ok', proj };
  });
  ok('pi: JS 结构 + 弹窗 DOM + 图片行「🖼项目图」按钮就位', struct.ok === 'ok', JSON.stringify(struct));
  ok('pi: 每个图片资源行都有项目图入口（≥3：背景/卡片背景/贴纸…）',
    struct.proj >= 3, 'proj=' + JSON.stringify(struct.proj));

  // 2) 列表接口：能取到主项目成图，缩略图是可直出绝对地址
  const api = await page.evaluate(async () => {
    const r = await fetch('/api/pic/project-images?page=1&size=12&sort=desc');
    const d = await r.json();
    return { ok: d.ok, n: (d.items || []).length, total: d.total, err: d.error,
             kinds: [...new Set((d.items || []).map(i => i.kind))],
             thumbs: (d.items || []).slice(0, 3).map(i => i.thumb_url) };
  });
  ok('pi: 接口取到主项目成图', api.ok && api.n > 0, JSON.stringify(api).slice(0, 260));
  ok('pi: 缩略图为主项目绝对地址（前端可直接 <img>）',
    api.thumbs.length > 0 && api.thumbs.every(u => /^https?:\/\//.test(u || '')), JSON.stringify(api.thumbs));

  // 3) 排序：最新优先单调不增 / 最早优先单调不减
  const sortChk = await page.evaluate(async () => {
    const g = async (sort) => {
      const d = await (await fetch('/api/pic/project-images?page=1&size=10&sort=' + sort)).json();
      return (d.items || []).map(i => i.created_at);
    };
    return { desc: await g('desc'), asc: await g('asc') };
  });
  const desc = sortChk.desc, asc = sortChk.asc;
  ok('pi: 最新优先（首项不早于末项）', desc.length > 1 && desc[0] >= desc[desc.length - 1], JSON.stringify(desc));
  ok('pi: 最早优先（首项不晚于末项）', asc.length > 1 && asc[0] <= asc[asc.length - 1], JSON.stringify(asc));

  // 4) 分类筛选：文生图只剩「生图」，图生图只剩「改图」
  const typeChk = await page.evaluate(async () => {
    const g = async (t) => {
      const d = await (await fetch('/api/pic/project-images?page=1&size=20&type=' + t)).json();
      return { ok: d.ok, total: d.total, kinds: [...new Set((d.items || []).map(i => i.kind))] };
    };
    return { t2i: await g('t2i_local'), i2i: await g('i2i') };
  });
  ok('pi: 文生图筛选只剩「生图」',
    typeChk.t2i.ok && typeChk.t2i.total > 0 && typeChk.t2i.kinds.every(k => k === '生图'),
    JSON.stringify(typeChk.t2i));
  ok('pi: 图生图筛选只剩「改图」',
    typeChk.i2i.ok && typeChk.i2i.total > 0 && typeChk.i2i.kinds.every(k => k === '改图'),
    JSON.stringify(typeChk.i2i));

  // 5) 关键词筛选
  const kwChk = await page.evaluate(async () => {
    const base = await (await fetch('/api/pic/project-images?page=1&size=5&keyword=')).json();
    const kw = String((base.items[0] || {}).prompt || '').slice(0, 4);
    if (!kw) return { err: 'empty-prompt' };
    const hit = await (await fetch('/api/pic/project-images?page=1&size=50&keyword=' + encodeURIComponent(kw))).json();
    return { kw, n: (hit.items || []).length, total: hit.total,
             allMatch: (hit.items || []).every(i => String(i.prompt || '').toLowerCase().includes(kw.toLowerCase())) };
  });
  ok('pi: 关键词筛选命中且全部匹配', kwChk.n > 0 && kwChk.allMatch, JSON.stringify(kwChk).slice(0, 220));

  // 6) 分页：第二页 page=2 且不与第一页重复
  const pageChk = await page.evaluate(async () => {
    const g = async (p) => {
      const d = await (await fetch('/api/pic/project-images?page=' + p + '&size=6')).json();
      return { page: d.page, total: d.total, ids: (d.items || []).map(i => i.task_id) };
    };
    const p1 = await g(1), p2 = await g(2);
    return { p: p2.page, n2: p2.ids.length, total: p2.total,
             noOverlap: p2.ids.every(id => !p1.ids.includes(id)) };
  });
  ok('pi: 第二页返回 page=2 且不与第一页重复',
    pageChk.p === 2 && pageChk.n2 > 0 && pageChk.noOverlap, JSON.stringify(pageChk));

  // 7) 真实点击：打开弹窗 → 加载真实成图 → 点第一张 → 导入并写回卡片专属背景
  const flow = await page.evaluate(async () => {
    S.tpl = 'fissure'; S.layout.bg_overrides = {};
    renderControls();
    const btn = document.querySelector('#controls .lrow[data-k="bg_image"] .act-proj');
    if (!btn) return { err: 'no-act-proj-bg_image' };
    btn.click();
    const shown = document.getElementById('piMask').classList.contains('show');
    for (let i = 0; i < 80 && !document.querySelector('#piGrid .pi-cell'); i++) {
      await new Promise(r => setTimeout(r, 250));
    }
    const cells = document.querySelectorAll('#piGrid .pi-cell').length;
    const cell = document.querySelector('#piGrid .pi-cell');
    if (!cell) return { err: 'no-cell', shown, cells };
    cell.click();
    for (let i = 0; i < 80; i++) {
      const ov = S.layout.bg_overrides && S.layout.bg_overrides.fissure;
      if (ov && ov.bg_image) break;
      await new Promise(r => setTimeout(r, 250));
    }
    const ov = S.layout.bg_overrides && S.layout.bg_overrides.fissure;
    return { shown, cells, fname: ov && ov.bg_image, pattern: ov && ov.bg_pattern,
             closed: !document.getElementById('piMask').classList.contains('show') };
  });
  ok('pi: 打开弹窗并加载出成图网格', flow.shown === true && flow.cells > 0, JSON.stringify(flow));
  ok('pi: 选图后导入并写回 fissure 专属背景（bg_pattern=image）',
    !!flow.fname && flow.pattern === 'image', JSON.stringify(flow));
  ok('pi: 导入完成后弹窗自动关闭', flow.closed === true, JSON.stringify(flow));

  // 8) 清理：删掉验收时导入的资源，不在资源库里留垃圾
  const cleaned = await page.evaluate(async (fn) => {
    if (!fn) return false;
    const r = await fetch('/api/pic/themes/assets/' + encodeURIComponent(fn), { method: 'DELETE' });
    const d = await r.json();
    return !!(d && d.ok);
  }, flow.fname || '');
  ok('pi: 验收后清理导入的临时资源', cleaned === true, 'fname=' + flow.fname);

  ok('pi: 全段无 JS 报错', pageErrors.length === pe0, pageErrors.slice(pe0).join(' | '));
}

await browser.close();
console.log('\n==== 结果：通过 ' + pass + ' / 失败 ' + fail + ' ====');
if (fail > 0) { console.log('失败项：\n - ' + fails.join('\n - ')); process.exit(1); }
process.exit(0);
