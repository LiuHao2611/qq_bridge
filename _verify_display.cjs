const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    args: ['--no-sandbox', '--disable-gpu']
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('PAGEERR: ' + e.message));

  const out = {};

  // 1) Project list -> cover thumbnail
  await page.goto('http://localhost:8099/', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);
  const listImgs = await page.$$eval('.card .cover img', els => els.map(e => ({ src: e.src, w: e.naturalWidth, h: e.naturalHeight })));
  out.listCoverLoaded = listImgs.filter(i => i.w > 0).length;
  out.listCoverTotal = listImgs.length;
  out.listCoverSample = listImgs[0] || null;
  await page.screenshot({ path: 'D:/AI/manju_agent/_shot_list.png', fullPage: true });

  // 2) Asset library -> 场景 tab (refluxed generated images)
  await page.goto('http://localhost:8099/project/1785942734524/assets', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(1500);
  const tabs = await page.$$('.tab');
  for (const t of tabs) {
    const txt = await t.innerText();
    if (txt.includes('场景')) { await t.click(); break; }
  }
  await page.waitForTimeout(2000);
  const assetImgs = await page.$$eval('.asset-card img', els => els.map(e => ({ src: e.src, w: e.naturalWidth, h: e.naturalHeight })));
  out.assetScenesLoaded = assetImgs.filter(i => i.w > 0).length;
  out.assetScenesTotal = assetImgs.length;
  out.assetScenesSample = assetImgs[0] || null;
  await page.screenshot({ path: 'D:/AI/manju_agent/_shot_assets.png', fullPage: true });

  // 3) Workbench
  await page.goto('http://localhost:8099/project/1785942734524', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: 'D:/AI/manju_agent/_shot_workbench.png', fullPage: true });

  out.consoleErrors = errors;
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
})();
