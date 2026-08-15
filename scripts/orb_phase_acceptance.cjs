// 悬浮球「阶段对齐」机器判定式验收（用户反馈：切到资产初始化仍显示其他阶段问题）
// 收敛为可重复运行的验收脚本（替代一次性 .tmp 验证）。
// 覆盖：suggestions / decisions / conversation / translator选型卡 / lockedCharacter角色锁定 /
//       feed实时生成任务 / output-card成片 —— 全部必须只对应「当前阶段」。
// 运行：PLAYWRIGHT_BROWSERS_PATH=D:/AI/.pw-browsers node scripts/orb_phase_acceptance.cjs
// 退出码：0=全部断言通过；非0=有断言失败（机器判定，非人工读日志）
const { chromium } = require('playwright');

const PID = process.env.ORB_PID || '3a60dd23-c203-48d9-9433-45fab34e2c45';
const FE = process.env.ORB_FE || 'http://127.0.0.1:8099';
const OUT = 'D:/AI/manju_agent/docs/evidence';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

let failures = 0;
function assert(cond, msg) {
  if (cond) { console.log('  PASS: ' + msg); }
  else { console.log('  FAIL: ' + msg); failures++; }
}

// 向悬浮球单例注入跨阶段测试数据（P1 / P2 各一组）
function injectCrossPhase() {
  const s = window.__orbState;
  if (!s) return 'NO_ORB_STATE';
  const now = Date.now();
  // 管家建议（P1/P2 各一条）
  s.suggestions.push({ id: 't-p1-sug', phase: 'P1', severity: 'suggestion', category: 'asset', title: '【测P1】资产就绪建议', detail: '仅资产初始化阶段可见', action_type: null, created_at: now });
  s.suggestions.push({ id: 't-p2-sug', phase: 'P2', severity: 'warning', category: 'script', title: '【测P2】连续同场景', detail: '其他阶段问题不应在P1显示', action_type: null, created_at: now });
  // 决策（P1/P2 各一条）
  s.decisions.push({ decision_id: 't-p1-dec', phase: 'P1', category: 'A', title: '【测P1】资产决策', description: 'P1决策', options: ['确认'], status: 'pending', answer: null });
  s.decisions.push({ decision_id: 't-p2-dec', phase: 'P2', category: 'A', title: '【测P2】策划决策', description: 'P2决策', options: ['确认'], status: 'pending', answer: null });
  // 对话气泡（P1/P2 各一条）
  s.conversation.push({ id: 't-p1-conv', kind: 'notice', text: '【测P1】导演资产发言', ts: now, phase: 'P1' });
  s.conversation.push({ id: 't-p2-conv', kind: 'notice', text: '【测P2】导演策划发言', ts: now, phase: 'P2' });
  // 导演选型卡（归属 P2，P1 不应显示）
  s.translator = { task_type: 'test', checkpoint: 'ckpt-test', node: 'N-test', ip_adapter_weight: 0.85, loras: ['lora-test'], seed: '123', phase: 'P2' };
  // 角色锁定卡（归属 P2，P1 不应显示）
  s.lockedCharacter = { name: '角色X', lockMode: 'i2v_first_frame', firstFrame: '', faceVectorDim: 512, consistencyPrompt: 'x', referenceImages: [], phase: 'P2' };
  // 实时生成任务（P1/P2 各一条，必须只显示当前阶段）
  s.feed.push({ id: 't-p1-feed', kind: 'image', title: '【测P1】P1生成任务', status: 'running', progress: 20, steps: [], ts: now, log: [], phase: 'P1' });
  s.feed.push({ id: 't-p2-feed', kind: 'image', title: '【测P2】P2生成任务', status: 'running', progress: 20, steps: [], ts: now, log: [], phase: 'P2' });
  // 成片数据（即便有，P1/P2 也不应显示成片卡，仅 P5 显示）
  s.runStatus = 'done';
  s.finalOutput = 'x';
  return 'OK';
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-gpu'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const logs = [];
  page.on('console', m => logs.push(m.type() + ': ' + m.text()));
  page.on('pageerror', e => logs.push('PAGEERROR: ' + e.message));

  console.log('[1] goto P1 (资产初始化) page');
  await page.goto(`${FE}/projects/${PID}/assets`, { waitUntil: 'load', timeout: 30000 });
  await page.waitForSelector('.orb-btn', { timeout: 30000 });
  await sleep(600);
  await page.click('.orb-btn', { force: true });
  await page.waitForSelector('.orb-panel', { timeout: 8000 });
  await sleep(800);

  console.log('[2] ensure phase is P1 + inject cross-phase data');
  await page.evaluate(() => { if (window.__orbState) window.__orbState.phase = 1; });
  const inj = await page.evaluate(injectCrossPhase);
  assert(inj === 'OK', '注入跨阶段测试数据成功 (orb state 可用)');
  await sleep(500);

  console.log('[3] assert P1-only visibility (所有信息只对应资产初始化阶段)');
  const p1 = await page.evaluate(() => {
    const advice = Array.from(document.querySelectorAll('.persona-advice .advice-item')).map(e => e.innerText.replace(/\s+/g,' ').trim());
    const decisions = Array.from(document.querySelectorAll('.m3-decision .pd-item')).map(e => e.innerText.replace(/\s+/g,' ').trim());
    const conv = Array.from(document.querySelectorAll('.m3-conv .conv-bubble')).map(e => e.innerText.replace(/\s+/g,' ').trim());
    const feeds = Array.from(document.querySelectorAll('.feed-card .fc-title')).map(e => e.innerText.replace(/\s+/g,' ').trim());
    return {
      advice, decisions, conv, feeds,
      translatorCards: document.querySelectorAll('.m3-translator').length,
      lockCards: document.querySelectorAll('.m3-lock').length,
      outputCards: document.querySelectorAll('.output-card').length,
      phase: window.__orbState ? window.__orbState.phase : null,
    };
  });
  console.log('  P1 =', JSON.stringify(p1, null, 0));

  const adviceHasP1 = p1.advice.some(t => t.includes('【测P1】'));
  const adviceHasP2 = p1.advice.some(t => t.includes('【测P2】'));
  const decHasP1 = p1.decisions.some(t => t.includes('【测P1】'));
  const decHasP2 = p1.decisions.some(t => t.includes('【测P2】'));
  const convHasP1 = p1.conv.some(t => t.includes('【测P1】'));
  const convHasP2 = p1.conv.some(t => t.includes('【测P2】'));
  const feedHasP1 = p1.feeds.some(t => t.includes('【测P1】'));
  const feedHasP2 = p1.feeds.some(t => t.includes('【测P2】'));

  assert(p1.phase === 1, '当前阶段确为 P1');
  assert(adviceHasP1 && !adviceHasP2, 'P1：管家建议显示【测P1】，不显示跨阶段【测P2】');
  assert(decHasP1 && !decHasP2, 'P1：待确认决策显示【测P1】，不显示跨阶段【测P2】');
  assert(convHasP1 && !convHasP2, 'P1：对话气泡显示【测P1】，不显示跨阶段【测P2】');
  assert(p1.translatorCards === 0, 'P1：导演选型卡（P2 归属）不显示');
  assert(p1.lockCards === 0, 'P1：角色锁定卡（P2 归属）不显示');
  assert(feedHasP1 && !feedHasP2, 'P1：实时生成任务只显示【测P1】，不显示跨阶段【测P2】');
  assert(p1.outputCards === 0, 'P1：即便有成片数据，成片卡也不显示（仅 P5 展示）');

  console.log('[4] simulate switching to P2 实时切换');
  await page.evaluate(() => { if (window.__orbState) window.__orbState.phase = 2; });
  await sleep(500);
  const p2 = await page.evaluate(() => {
    const advice = Array.from(document.querySelectorAll('.persona-advice .advice-item')).map(e => e.innerText.replace(/\s+/g,' ').trim());
    const decisions = Array.from(document.querySelectorAll('.m3-decision .pd-item')).map(e => e.innerText.replace(/\s+/g,' ').trim());
    const conv = Array.from(document.querySelectorAll('.m3-conv .conv-bubble')).map(e => e.innerText.replace(/\s+/g,' ').trim());
    const feeds = Array.from(document.querySelectorAll('.feed-card .fc-title')).map(e => e.innerText.replace(/\s+/g,' ').trim());
    const translator = document.querySelector('.m3-translator');
    const lock = document.querySelector('.m3-lock');
    return {
      advice, decisions, conv, feeds,
      translatorText: translator ? translator.innerText.replace(/\s+/g,' ').trim() : '',
      lockText: lock ? lock.innerText.replace(/\s+/g,' ').trim() : '',
      outputCards: document.querySelectorAll('.output-card').length,
      phase: window.__orbState ? window.__orbState.phase : null,
    };
  });
  console.log('  P2 =', JSON.stringify(p2, null, 0));

  const adviceP2HasP1 = p2.advice.some(t => t.includes('【测P1】'));
  const adviceP2HasP2 = p2.advice.some(t => t.includes('【测P2】'));
  const decP2HasP1 = p2.decisions.some(t => t.includes('【测P1】'));
  const decP2HasP2 = p2.decisions.some(t => t.includes('【测P2】'));
  const convP2HasP1 = p2.conv.some(t => t.includes('【测P1】'));
  const convP2HasP2 = p2.conv.some(t => t.includes('【测P2】'));
  const feedP2HasP1 = p2.feeds.some(t => t.includes('【测P1】'));
  const feedP2HasP2 = p2.feeds.some(t => t.includes('【测P2】'));

  assert(p2.phase === 2, '切换后阶段确为 P2');
  assert(adviceP2HasP2 && !adviceP2HasP1, '切到 P2：管家建议显示【测P2】，不显示 P1');
  assert(decP2HasP2 && !decP2HasP1, '切到 P2：待确认决策显示【测P2】，不显示 P1');
  assert(convP2HasP2 && !convP2HasP1, '切到 P2：对话气泡显示【测P2】，不显示 P1');
  assert(p2.translatorText.includes('ckpt-test'), '切到 P2：导演选型卡显示（底模 ckpt-test）');
  assert(p2.lockText.includes('角色X'), '切到 P2：角色锁定卡显示（已锁定角色X）');
  assert(feedP2HasP2 && !feedP2HasP1, '切到 P2：实时生成任务只显示【测P2】，不显示 P1');
  assert(p2.outputCards === 0, 'P2：成片卡仍不显示（仅 P5 展示）');

  await page.screenshot({ path: `${OUT}/orb_phase_p1.png` });
  await page.evaluate(() => { if (window.__orbState) window.__orbState.phase = 1; });
  await sleep(400);
  await page.screenshot({ path: `${OUT}/orb_phase_backtop1.png` });

  const fs = require('fs');
  if (!fs.existsSync(OUT)) fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(`${OUT}/orb_phase_proof.json`, JSON.stringify({ p1, p2, inject: inj, failures }, null, 2));
  console.log('[5] wrote docs/evidence/orb_phase_proof.json');

  await browser.close();
  console.log('\n==== orb_phase_acceptance result: failures=' + failures + ' ====');
  process.exit(failures > 0 ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(2); });
