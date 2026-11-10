# -*- coding: utf-8 -*-
"""本地生图端到端验收（真实 7788 + 真实 ComfyUI 8188）。

与 tests/test_taskmgr_local.py 的区别：
  - 那个用 MockTransport 验「提交体字段对不对」（纯逻辑，秒级）；
  - 这个打真实后端与真实 ComfyUI，验「本地通道到底能不能出图」（分钟级，占 GPU）。

运行方式（默认跳过，避免常规 pytest 被真实采样拖慢）：
    $env:MANJU_E2E_LOCAL = "1"
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_e2e_local_t2i.py -v -s

前置条件：
  1) 主项目后端 7788 在运行，且 settings 的 comfy.root 已配置；
  2) ComfyUI 8188 在运行（本用例不负责启动，也不会改写 ComfyUI 任何文件）；
  3) 至少有一个可用 checkpoint（当前环境：v1-5-pruned-emaonly.safetensors）。

判定：任务必须到 succeeded、必须产出图片、图片 URL 必须能下载到 >1KB 的字节。
"""
import os
import time

import httpx
import pytest

API = "http://127.0.0.1:7788/api/v1"
COMFY = "http://127.0.0.1:8188"

# 与 Config 的本地默认值保持一致（512 是 SD1.5 原生尺寸，6GB 显存稳定）
BODY = {
    "prompt": "a tiny orange cat sitting on a wooden table",
    "negative": "blurry, low quality, watermark, deformed",
    "width": 512,
    "height": 512,
    "steps": 20,
    "cfg": 7.0,
    "count": 1,
    "seed": 12345,
    "style": "写实",
    # Ollama 未运行时开启润色会直接失败（后端无词典兜底），故端到端用例固定关闭
    "enhance": False,
    "orchestrator": "ollama",
}


def _alive(url: str, timeout: float = 3.0) -> bool:
    try:
        httpx.get(url, timeout=timeout)
        return True
    except Exception:
        return False


@pytest.mark.slow
def test_local_t2i_end_to_end_produces_image():
    if os.environ.get("MANJU_E2E_LOCAL") != "1":
        pytest.skip("默认跳过真实生图用例（占 GPU ~1-2 分钟），设 MANJU_E2E_LOCAL=1 运行")
    if not _alive(API + "/t2i/active-task?source=local"):
        pytest.skip("主项目后端 7788 未启动")
    if not _alive(COMFY + "/system_stats"):
        pytest.skip("ComfyUI 8188 未启动")

    # 提交前确保平台本地通道空闲，避免打断用户可能在跑的任务
    r0 = httpx.get(API + "/t2i/active-task?source=local", timeout=10).json()
    if (r0.get("data") or {}).get("task"):
        pytest.skip("平台本地通道有任务在跑，跳过以免互相取消")

    r = httpx.post(API + "/t2i/generate", json=BODY, timeout=30)
    assert r.status_code == 200, "提交失败：%s %s" % (r.status_code, r.text[:300])
    tid = (r.json().get("data") or {}).get("task_id")
    assert tid, "未返回 task_id：%s" % r.text[:300]

    deadline = time.time() + 900          # 本地 512x512/20 步通常 60~120s，留足 15 分钟
    task = None
    while time.time() < deadline:
        t = httpx.get(API + "/t2i/tasks/%s" % tid, timeout=30).json()
        task = (t.get("data") or {}).get("task") or {}
        if task.get("status") in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(3)

    assert task is not None, "轮询未拿到任务"
    assert task.get("status") == "succeeded", "本地生图未成功：status=%s error=%s" % (
        task.get("status"), str(task.get("error_message"))[:300])

    images = (task.get("result") or {}).get("images") or []
    assert images, "任务成功但未产出图片"

    url = images[0].get("url") or ""
    assert url, "图片缺少 url：%s" % images[0]
    full = url if url.startswith("http") else "http://127.0.0.1:7788" + url
    resp = httpx.get(full, timeout=30)
    assert resp.status_code == 200, "图片不可访问：HTTP %s %s" % (resp.status_code, full)
    assert len(resp.content) > 1024, "图片疑似空文件（%d 字节）：%s" % (len(resp.content), full)
