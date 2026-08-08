// 自动化测试：验证 Bug #1 (提示词翻译) + Bug #2 (日志打包) 修复
import { chromium } from 'playwright';
import fs from 'fs';

const BASE = 'http://localhost:8099';
const EDGE_PATH = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';

const results = [];
function log(test, passed, detail = '') {
  const status = passed ? '✅ PASS' : '❌ FAIL';
  results.push({ test, passed, detail });
  console.log(`${status} | ${test}${detail ? ' | ' + detail : ''}`);
}

async function run() {
  // ========== API 级测试 ==========
  console.log('\n=== API 级测试 ===\n');

  // 测试 1：日志打包 API
  console.log('--- 测试 1：日志打包 API ---');
  try {
    const packResp = await fetch(BASE + '/api/v1/logs/pack');
    const packBlob = await packResp.blob();
    log('日志打包 HTTP 200', packResp.status === 200, `status=${packResp.status}`);
    log('日志打包非空', packBlob.size > 100, `size=${packBlob.size} bytes`);
    log('日志打包类型正确', packBlob.type === 'application/zip', `type="${packBlob.type}"`);
  } catch (e) {
    log('日志打包 API', false, e.message);
  }

  // 测试 2：提交文生图任务
  console.log('\n--- 测试 2：提交文生图任务 ---');
  let taskId = null;
  try {
    const genResp = await fetch(BASE + '/api/v1/t2i/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: '一朵玫瑰花',
        negative: '人类',
        width: 512,
        height: 512,
        steps: 15,
        count: 1,
        seed: -1,
        enhance: true,
        orchestrator: 'dify',
        style: '写实',
      }),
    });
    const genData = await genResp.json();
    taskId = genData?.data?.task_id;
    log('提交文生图任务', genResp.status === 200, `task_id=${taskId}`);
  } catch (e) {
    log('提交文生图任务', false, e.message);
  }

  // 测试 3：轮询任务结果
  console.log('\n--- 测试 3：轮询任务结果 ---');
  let taskResult = null;
  if (taskId) {
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 5000));
      try {
        const pollResp = await fetch(BASE + '/api/v1/t2i/tasks/' + taskId);
        const pollData = await pollResp.json();
        // 正确路径：data.task.status
        const task = pollData?.data?.task;
        const status = task?.status;

        if (status === 'succeeded' || status === 'failed') {
          taskResult = task;
          console.log(`  任务完成: status=${status} (${(i + 1) * 5}s)`);
          break;
        }
        if (i % 6 === 0) console.log(`  ...等待中 (${(i + 1) * 5}s) status=${status}`);
      } catch (e) {
        // 继续轮询
      }
    }
  }

  // 测试 4：验证提示词翻译结果
  console.log('\n--- 测试 4：验证提示词翻译结果 ---');
  if (taskResult) {
    const result = typeof taskResult.result === 'string'
      ? JSON.parse(taskResult.result) : taskResult.result;

    log('任务状态 succeeded', taskResult.status === 'succeeded', `status=${taskResult.status}`);

    if (result?.prompt_used) {
      const isChinese = result.prompt_used.includes('一朵玫瑰花');
      log('提示词已翻译为英文（非中文原文）', !isChinese, `prompt_used="${result.prompt_used?.substring(0, 120)}"`);

      const hasEnglish = /^[a-zA-Z]/.test(result.prompt_used?.trim() || '');
      log('提示词为英文格式', hasEnglish);

      // 验证包含关键词 "rose"
      const hasRose = result.prompt_used.toLowerCase().includes('rose');
      log('提示词包含 rose 关键词', hasRose);
    }

    if (result?.negative_used) {
      const hasHuman = result.negative_used.toLowerCase().includes('human');
      log('负向提示词已翻译（human）', hasHuman, `negative_used="${result.negative_used}"`);
    }

    if (result?.orchestrator_used) {
      log('编排器标记为 dict_fallback', result.orchestrator_used === 'dict_fallback', `orchestrator_used="${result.orchestrator_used}"`);
      log('编排器非 ollama(fallback)（不再误标）', result.orchestrator_used !== 'ollama(fallback)');
    }

    if (result?.note) {
      const hasWarning = result.note.includes('词典') || result.note.includes('不可用');
      log('结果包含翻译警告', hasWarning, `note="${result.note?.substring(0, 100)}"`);
    }

    // 验证图片已生成
    if (result?.images && result.images.length > 0) {
      log('图片已生成', true, `count=${result.images.length}, url="${result.images[0].url}"`);

      // 下载图片验证非空
      try {
        const imgResp = await fetch(BASE + result.images[0].url);
        const imgBlob = await imgResp.blob();
        log('图片文件非空', imgBlob.size > 1000, `size=${imgBlob.size} bytes`);
      } catch (e) {
        log('图片文件验证', false, e.message);
      }
    } else {
      log('图片已生成', false, '无图片');
    }
  } else {
    log('任务完成', false, '超时未获取到结果');
  }

  // ========== UI 级测试 ==========
  console.log('\n=== UI 级测试 ===\n');

  const browser = await chromium.launch({
    headless: true,
    executablePath: EDGE_PATH,
  });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);

  try {
    // 测试 5：页面加载
    console.log('--- 测试 5：SPA 加载 ---');
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForSelector('#app > *', { timeout: 10000 });
    await page.waitForTimeout(2000);
    log('SPA 加载', await page.locator('#app > *').count() > 0);

    // 测试 6：导航到文生图（点击侧边栏链接）
    console.log('\n--- 测试 6：导航到文生图 ---');
    // 尝试点击侧边栏中的"文生图"链接
    const t2iLink = page.locator('a:has-text("文生图"), [href*="text-to-image"], .nav-item:has-text("文生图")');
    if (await t2iLink.count() > 0) {
      await t2iLink.first().click();
      await page.waitForTimeout(3000);
    } else {
      // 直接 hash 导航
      await page.goto(BASE + '/#/text-to-image', { waitUntil: 'networkidle' });
      await page.waitForTimeout(3000);
    }

    const bodyText = await page.textContent('body');
    const hasT2iPage = bodyText && bodyText.includes('文生图') && bodyText.includes('ComfyUI');
    log('文生图页面加载', hasT2iPage);

    if (hasT2iPage) {
      // 检查服务状态条
      const hasSvcBar = await page.locator('.svc-bar').count() > 0;
      log('服务状态条显示', hasSvcBar);

      // 检查提示词输入
      const hasPromptInput = await page.locator('textarea').count() > 0;
      log('提示词输入框存在', hasPromptInput);

      // 检查编排方选择器
      const hasOrchestrator = await page.locator('.el-select').count() > 0;
      log('编排方选择器存在', hasOrchestrator);
    }

    // 测试 7：导航到日志页面
    console.log('\n--- 测试 7：导航到日志页面 ---');
    const logsLink = page.locator('a:has-text("日志"), [href*="logs"], .nav-item:has-text("日志")');
    if (await logsLink.count() > 0) {
      await logsLink.first().click();
      await page.waitForTimeout(3000);
    } else {
      await page.goto(BASE + '/#/logs', { waitUntil: 'networkidle' });
      await page.waitForTimeout(3000);
    }

    const logsBodyText = await page.textContent('body');
    const hasLogsPage = logsBodyText && (logsBodyText.includes('系统日志') || logsBodyText.includes('日志文件'));
    log('日志页面加载', hasLogsPage);

    if (hasLogsPage) {
      // 检查打包按钮
      const packBtn = page.locator('button:has-text("打包")');
      const hasPackBtn = await packBtn.count() > 0;
      log('打包按钮存在', hasPackBtn);

      if (hasPackBtn) {
        // 点击打包
        const downloadPromise = page.waitForEvent('download', { timeout: 15000 }).catch(() => null);
        await packBtn.first().click();
        const download = await downloadPromise;

        if (download) {
          log('日志打包触发下载', true, `filename="${download.suggestedFilename()}"`);
          log('下载文件为 zip', download.suggestedFilename().endsWith('.zip'));
        } else {
          // 检查是否有成功提示
          await page.waitForTimeout(2000);
          const successMsg = page.locator('.el-message--success');
          const hasSuccess = await successMsg.count() > 0;
          log('日志打包触发下载', hasSuccess, hasSuccess ? '显示成功提示' : '未捕获下载事件');
        }
      }

      // 检查日志文件列表
      const hasLogList = await page.locator('.log-list').count() > 0;
      log('日志文件列表显示', hasLogList);
    }

  } catch (e) {
    console.error('UI 测试异常:', e.message);
    log('UI 测试执行', false, e.message);
  } finally {
    await browser.close();
  }

  // ========== 汇总 ==========
  console.log('\n========== 测试汇总 ==========');
  const passed = results.filter(r => r.passed).length;
  const failed = results.filter(r => !r.passed).length;
  console.log(`通过: ${passed} | 失败: ${failed} | 总计: ${results.length}`);
  if (failed > 0) {
    console.log('\n失败项:');
    results.filter(r => !r.passed).forEach(r => console.log(`  ❌ ${r.test} | ${r.detail}`));
  }
  console.log('');
  process.exit(failed > 0 ? 1 : 0);
}

run().catch(e => { console.error(e); process.exit(1); });
