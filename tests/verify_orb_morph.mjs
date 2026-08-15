// Real-DOM DirectorOrb morph verification + acceptance recordings.
// Strategy: 2 theme-level recordings (cyberpunk, p5) — each cycles all 4 forms
// showing IDLE then DRAG on the REAL project page. Machine-judged assertions:
//   - orb-btn mounts with correct orb-morph--{key}
//   - .orb-morph has real size + a drawn shape (clip-path / background)
//   - orb-btn is NOT a round ball (no gradient bg, no 50% radius)
//   - during drag: dragging class present; shape signature IDENTICAL to idle
//     (proves the form is preserved, never reverts to a generic round ball)
//   - during drag: a form-specific animation is active (animation-name/duration changes)
import { chromium } from 'playwright';
import fs from 'fs';

const BASE = 'http://localhost:8099';
const PID = '383722ac-79cd-4308-a595-8744949a9fba';
const REC = 'D:/AI/manju_agent/tests/recordings';
const THEMES = {
  default: ['sword', 'mask', 'tower', 'shackle'],
  gray: ['sword', 'mask', 'tower', 'shackle'],
  cyberpunk: ['katana', 'relic', 'wire', 'scanner'],
  p5: ['sword', 'mask', 'tower', 'shackle'],
};

// 手册 §4 规定的「拖动专属演出」keyframe（用于强断言：证明是特殊形态，不是通用旋转）
const EXPECTED_DRAG = {
  sword: { layer: 'm', name: 'p5-sword-fly' },
  mask: { layer: 'm', name: 'p5-mask-split' },
  tower: { layer: 'm', name: 'p5-tower-spin' },
  shackle: { layer: 'm', name: 'p5-shackle-whip' },
  katana: { layer: 'm', name: 'nc-katana-swing' },
  relic: { layer: 'm', name: 'nc-relic-fast' },
  wire: { layer: 'b', name: 'nc-wire-whip' },
  // scanner: 拖动加速扫描线（::after 时长变短），无独立 keyframe
};

const fails = [];
const warns = [];
function assert(cond, msg) {
  if (cond) console.log('  ok  ' + msg);
  else { fails.push(msg); console.log('  FAIL ' + msg); }
}

async function currentKey(page) {
  return page.evaluate(() => {
    const el = document.querySelector('.orb-btn');
    if (!el) return null;
    const m = el.className.match(/orb-morph--([a-z]+)/);
    return m ? m[1] : null;
  });
}

async function cycleTo(page, key) {
  for (let i = 0; i < 10; i++) {
    const k = await currentKey(page);
    if (k === key) return true;
    await page.locator('.orb-btn').first().dblclick();
    await page.waitForTimeout(280);
  }
  return (await currentKey(page)) === key;
}

async function sampleShape(page) {
  return page.evaluate(() => {
    const orb = document.querySelector('.orb-btn');
    if (!orb) return { mounted: false };
    const morph = orb.querySelector('.orb-morph');
    const mcs = morph ? getComputedStyle(morph) : null;
    const b = morph ? getComputedStyle(morph, '::before') : null;
    const a = morph ? getComputedStyle(morph, '::after') : null;
    const rect = morph ? morph.getBoundingClientRect() : null;
    const pick = (cs, p) => (cs ? cs.getPropertyValue(p) : 'none');
    const morphCp = pick(mcs, 'clip-path');
    const morphBg = pick(mcs, 'background-image');
    const beforeCp = pick(b, 'clip-path');
    const beforeBg = pick(b, 'background-image');
    const afterCp = pick(a, 'clip-path');
    const afterBg = pick(a, 'background-image');
    const orbBg = getComputedStyle(orb).getPropertyValue('background-image');
    const orbBgColor = getComputedStyle(orb).getPropertyValue('background-color');
    const orbRadius = getComputedStyle(orb).getPropertyValue('border-radius');
    const morphBgColor = mcs ? mcs.getPropertyValue('background-color') : 'transparent';
    const solidMorph = morphBgColor && morphBgColor !== 'transparent' && morphBgColor !== 'rgba(0, 0, 0, 0)';
    const shapeDrawn =
      (morphCp && morphCp !== 'none') ||
      (morphBg && morphBg !== 'none') ||
      solidMorph ||
      (beforeCp && beforeCp !== 'none') ||
      (beforeBg && beforeBg !== 'none') ||
      (afterCp && afterCp !== 'none') ||
      (afterBg && afterBg !== 'none');
    const orbMorphSized = !!rect && rect.width > 1 && rect.height > 1;
    const hasGradient =
      (orbBg && orbBg !== 'none' && orbBg.includes('gradient')) ||
      (orbBgColor && orbBgColor !== 'transparent' && orbBgColor !== 'rgba(0, 0, 0, 0)');
    const isRound = orbRadius === '50%' || orbRadius === '50% 50%';
    // byte-exact shape signature: clip-path/background geometry only.
    // NOTE: drag rotations legitimately change the *rendered* bounding box,
    // so we intentionally EXCLUDE rect size — a rotated katana still has the
    // same clip-path, proving the form is preserved (not reverted to a ball).
    // bodySig = the MAIN morph-body geometry (morph element's own clip-path +
    // background), excluding pseudos. Drag residuals legitimately alter
    // ::before/::after (残影分裂/疾转残影) — those are allowed to differ.
    const shapeSig = [morphCp, morphBg, beforeCp, beforeBg, afterCp, afterBg].join('|');
    const bodySig = morphCp + '|' + morphBg + '|' + morphBgColor;
    const animSig = JSON.stringify({
      m: { n: pick(mcs, 'animation-name'), d: pick(mcs, 'animation-duration') },
      b: { n: pick(b, 'animation-name'), d: pick(b, 'animation-duration') },
      a: { n: pick(a, 'animation-name'), d: pick(a, 'animation-duration') },
    });
    return {
      mounted: true,
      cls: orb.className,
      orbMorphSized,
      shapeDrawn,
      orbBtnNoBall: !hasGradient && !isRound,
      shapeSig,
      animSig,
      mAnim: pick(mcs, 'animation-name'),
      bAnim: pick(b, 'animation-name'),
      aDur: pick(a, 'animation-duration'),
    };
  });
}

async function performDrag(page) {
  const box = await page.locator('.orb-btn').first().boundingBox();
  if (!box) return false;
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  const steps = 34;
  for (let i = 0; i < steps; i++) {
    const ang = (i / steps) * Math.PI * 2;
    await page.mouse.move(cx + Math.cos(ang) * 55, cy + Math.sin(ang) * 55, { steps: 2 });
    await page.waitForTimeout(45);
  }
  // mouse stays DOWN so the caller can sample the dragging state
  return true;
}

const summary = [];
for (const [theme, forms] of Object.entries(THEMES)) {
  console.log(`\n===== THEME: ${theme} =====`);
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-gpu'] });
  const ctx = await browser.newContext({
    viewport: { width: 1100, height: 720 },
    recordVideo: { dir: REC, size: { width: 1100, height: 720 } },
  });
  const page = await ctx.newPage();
  await page.addInitScript((t) => { try { localStorage.setItem('manju_theme', t); } catch (e) {} }, theme);
  await page.goto(`${BASE}/projects/${PID}/assets`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.orb-btn', { timeout: 15000 });
  await page.waitForTimeout(500);

  for (const key of forms) {
    console.log(`-- form: ${key} --`);
    const cycled = await cycleTo(page, key);
    assert(cycled, `[${theme}/${key}] cycled to correct form`);

    const idle = await sampleShape(page);
    assert(idle.mounted, `[${theme}/${key}] orb-btn mounted`);
    assert(idle.orbMorphSized, `[${theme}/${key}] .orb-morph has real size`);
    assert(idle.shapeDrawn, `[${theme}/${key}] form shape drawn (clip-path/background present)`);
    assert(idle.orbBtnNoBall, `[${theme}/${key}] orb-btn not a round ball at idle`);
    await page.waitForTimeout(900); // let IDLE shape sit clearly on the recording

    const dragStarted = await performDrag(page);
    // Sample several frames during the drag: 3D-rotating forms (e.g. tower's
    // rotateY 540° spin) are momentarily edge-on (rendered width ~0) — so we
    // require the morph to render at non-zero size in AT LEAST one frame, while
    // body geometry / no-ball / animation checks come from a stable sample.
    let drag = null;
    let everSized = false;
    let everDragging = false;
    for (let s = 0; s < 8; s++) {
      await page.waitForTimeout(45);
      const d = await sampleShape(page);
      if (!drag) drag = d;
      if (d.orbMorphSized) everSized = true;
      if (d.cls.includes('dragging')) everDragging = true;
    }
    assert(dragStarted, `[${theme}/${key}] drag input sent`);
    assert(everDragging, `[${theme}/${key}] .dragging class present during drag`);
    assert(everSized, `[${theme}/${key}] .orb-morph rendered at non-zero size during drag`);
    assert(drag.shapeDrawn, `[${theme}/${key}] form shape still drawn during drag`);
    assert(drag.orbBtnNoBall, `[${theme}/${key}] orb-btn NOT a round ball during drag`);
    assert(drag.bodySig === idle.bodySig,
      `[${theme}/${key}] main morph body geometry IDENTICAL idle vs drag (no round-ball revert)`);
    assert(drag.animSig !== idle.animSig,
      `[${theme}/${key}] form-specific drag animation active (anim changed vs idle)`);

    // 强断言：拖动动画名必须是手册 §4 规定的该形态专属 keyframe，
    // 证明是「特殊形态演出」而非通用旋转/转圈。
    const exp = EXPECTED_DRAG[key];
    if (exp) {
      const got = drag[exp.layer === 'm' ? 'mAnim' : 'bAnim'];
      assert(got === exp.name,
        `[${theme}/${key}] drag uses spec animation "${exp.name}" (not a generic spin)`);
    } else if (key === 'scanner') {
      const idleDur = parseFloat(idle.aDur) || 0;
      const dragDur = parseFloat(drag.aDur) || 0;
      assert(dragDur < idleDur,
        `[${theme}/${key}] drag accelerates scan line (::after duration ${idleDur}s -> ${dragDur}s)`);
    }

    // —— 拖尾方框检测 —— 0 模糊 + 大偏移 = 实心矩形拖尾（动画期最常见的"变方框"根因）。
    //    box-shadow/drop-shadow 在 0px 模糊 + 偏移 > 8px 时，会叠出硬边矩形条。
    const trail = await page.evaluate(() => {
      const orb = document.querySelector('.orb-btn');
      const m = orb.querySelector('.orb-morph');
      const b = getComputedStyle(m, '::before');
      const a = getComputedStyle(m, '::after');
      const bs = (s) => (s ? s.getPropertyValue('box-shadow') : '');
      const fs = (s) => (s ? s.getPropertyValue('filter') : '');
      const tokens = (s) => s.split(/,(?![^()]*\))/).map(t => t.trim()).filter(Boolean);
      const hardRect = (tok) => {
        // 匹配 box-shadow / drop-shadow 的偏移量（支持负值）
        const m = tok.match(/(-?\d+(?:\.\d+)?)px\s+(-?\d+(?:\.\d+)?)px\s+(\d+(?:\.\d+)?)px/);
        if (!m) return false;
        const ox = Math.abs(+m[1]), oy = Math.abs(+m[2]), blur = +m[3];
        return blur === 0 && (ox > 8 || oy > 8);
      };
      const list = [
        ['orb',    bs(getComputedStyle(orb))],
        ['morph',  bs(getComputedStyle(m))],
        ['before', bs(b)],
        ['after',  bs(a)],
        ['orb-f',  fs(getComputedStyle(orb))],
        ['morph-f',fs(getComputedStyle(m))],
        ['before-f',fs(b)],
        ['after-f', fs(a)],
      ];
      const hits = [];
      for (const [tag, val] of list) {
        for (const t of tokens(val)) {
          if (hardRect(t)) hits.push(`${tag}: ${t}`);
        }
      }
      return hits;
    });
    assert(trail.length === 0,
      `[${theme}/${key}] NO hard-rectangle trailer (0-blur + large offset = solid box) during drag`
      + (trail.length ? `  → 违规则层: ${trail.join(' | ')}` : ''));

    await page.mouse.up();
    await page.waitForTimeout(600); // settle back to idle before next form
  }

  await ctx.close();
  const vp = page.video() ? await page.video().path() : null;
  if (vp) {
    const dest = `${REC}/${theme}-all-forms.webm`;
    try { fs.renameSync(vp, dest); console.log(`  video -> ${dest}`); summary.push(dest); }
    catch (e) { console.log('  video rename failed:', e.message); summary.push(vp); }
  }
  await browser.close();
}

console.log('\n========== SUMMARY ==========');
console.log('Recordings:');
summary.forEach((s) => console.log('  ' + s));
if (warns.length) { console.log('Warnings:'); warns.forEach((w) => console.log('  ! ' + w)); }
if (fails.length) {
  console.log(`\nFAILURES: ${fails.length}`);
  fails.forEach((f) => console.log('  - ' + f));
  process.exit(1);
} else {
  console.log('\nALL ASSERTIONS PASS');
}
