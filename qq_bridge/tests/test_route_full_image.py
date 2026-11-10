# -*- coding: utf-8 -*-
"""回归测试：'全面分析' 口令 + 图片 一起发，必须直接进 OCR 全量分析，
而不是被当成「只发口令等图」的预约。

复刻 2026-11-07 线上问题：用户把「全面分析」和紫卡截图同一条消息发出，
机器人却回「已登记紫卡全面分析，请直接发送紫卡截图」，图根本没分析。

对照：'紫卡分析' + 图片 能正常 OCR，因为它命中 _kw（紫卡|裂罅|riven）；
'全面分析' 命中的是 _full_kw，旧代码没把它放进图片分支触发条件。

运行：cd qq_bridge && .venv/Scripts/python.exe tests/test_route_full_image.py
退出码 0 = 全过；非 0 = 有失败。
"""
import asyncio
import os
import re
import sys

# 让 qq_bridge.game_tools 可导入（与容器内包布局一致）：
#   <repo>/src/plugins → qq_bridge 顶层包（含 planstore/ai_mode 等兄弟模块）
#   <repo>             → src 顶层包（含 group_switch）的父目录
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../qq_bridge 仓库根
for _p in (os.path.join(_REPO, "src", "plugins"), _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 先显式解析 src 命名空间包（无 __init__.py），避免 qq_bridge/__init__.py
# 内 `from src.group_switch` 在 src 尚未被索引时失败。
import src  # noqa: E402,F401
from src.group_switch import group_enabled  # noqa: E402,F401

import qq_bridge.game_tools as gt  # noqa: E402


class Seg:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class FakeEvent:
    def __init__(self, texts, image_urls=()):
        segs = []
        for t in texts:
            segs.append(Seg("text", {"text": t}))
        for u in image_urls:
            segs.append(Seg("image", {"url": u, "path": "", "file": ""}))
        self.original_message = segs
        self.message = segs
        self.message_type = "group"
        self.group_id = 123
        self.user_id = 900000002
        self.message_id = 1

    def get_matcher(self):
        class M:
            def stop_propagation(self):
                pass
        return M()

    def get_plaintext(self):
        return ""


class FakeBot:
    def __init__(self):
        self.self_id = "9984"
        self.sent = []

    async def send(self, event, message=""):
        self.sent.append(str(message))
        return {"message_id": 0}


def _install_mocks(bot, parsed):
    """桩掉外呼：下载返回假字节、OCR 返回 parsed、出卡/排队只记录。"""
    async def _fake_download(url):
        return b"FAKEIMG"
    gt._download_riven_image = _fake_download

    async def _fake_parse(data):
        return parsed
    gt.riven.riven_img_parse = _fake_parse

    calls = {"full_parsed": 0}

    async def _fake_send_full(b, e, p):
        calls["full_parsed"] += 1
    gt._send_riven_full_parsed = _fake_send_full

    async def _fake_recall(b, e):
        pass
    gt._riven_recall_queue = _fake_recall

    async def _fake_guide(b, e, title, rv, card_data=None):
        calls.setdefault("guide", 0)
        calls["guide"] += 1
    gt._send_guide = _fake_guide

    # AI 模式门禁关闭，确保不提前 return
    gt.ai_mode_store.is_on = lambda scope: False
    return calls


def _run(texts, image_urls, parsed, *, disable_full_kw=False):
    bot = FakeBot()
    calls = _install_mocks(bot, parsed)
    # 隔离：清空跨用例泄漏的「等图预约」状态（否则上一个用例登记的全面分析
    # 预约会被下一个用例的 _pop_riven_full_wait 误消费）。
    gt._riven_full_wait.clear()
    gt._riven_wait.clear()

    real_search = re.search

    def _patched_search(pattern, string, *a, **k):
        if disable_full_kw and "紫卡全面" in pattern:
            return None  # 禁用 _full_kw → 复刻旧 bug 的触发条件缺口
        return real_search(pattern, string, *a, **k)

    if disable_full_kw:
        gt.re.search = _patched_search
    try:
        asyncio.run(gt._handle(bot, FakeEvent(texts, image_urls)))
    finally:
        if disable_full_kw:
            gt.re.search = real_search
    return bot, calls


_PARSED = {"weapon": "Akarius", "weapon_zh": "阿利乌双枪", "rank": 8,
           "stats": [{"en": "Status Chance", "zh": "触发几率", "value": 135.4,
                      "unit": "%", "sign": "+"}]}


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else "  -> " + detail))
    check.failed = check.failed or (not ok)
check.failed = False


def main():
    # 1) 修复后：'全面分析' + 图片 → 进 OCR 全量分析，不发等图预约
    bot, calls = _run(["全面分析"], ["http://x/riven.png"], _PARSED)
    ocr_called = calls["full_parsed"] == 1
    wait_sent = any("已登记紫卡全面分析" in s for s in bot.sent)
    check("full_analysis_image_routes_to_ocr", ocr_called,
          "full_parsed=%s" % calls["full_parsed"])
    check("full_analysis_image_no_wait_message", not wait_sent,
          "sent=%s" % bot.sent)

    # 2) 反证：禁用 _full_kw（旧代码缺口）→ 必须回到等图预约、不出 OCR
    bot2, calls2 = _run(["全面分析"], ["http://x/riven.png"], _PARSED,
                        disable_full_kw=True)
    ocr_called2 = calls2["full_parsed"] >= 1
    wait_sent2 = any("已登记紫卡全面分析" in s for s in bot2.sent)
    check("regression_old_bug_returns_wait", (not ocr_called2) and wait_sent2,
          "full_parsed=%s sent=%s" % (calls2["full_parsed"], bot2.sent))

    # 3) 不带图：'全面分析' 只发口令 → 仍是等图预约（行为不变，不能退化成无响应）
    bot3, calls3 = _run(["全面分析"], [], _PARSED)
    ocr_called3 = calls3["full_parsed"] >= 1
    wait_sent3 = any("已登记紫卡全面分析" in s for s in bot3.sent)
    check("full_analysis_no_image_still_registers_wait", (not ocr_called3) and wait_sent3,
          "full_parsed=%s sent=%s" % (calls3["full_parsed"], bot3.sent))

    # 4) 对照：'紫卡分析' + 图片 仍走紫卡评估（guide），不被全面分析路径抢走
    bot4, calls4 = _run(["紫卡分析"], ["http://x/riven.png"], _PARSED)
    guide_called = calls4.get("guide", 0) >= 1
    ocr_called4 = calls4["full_parsed"] >= 1
    check("riven_analysis_image_still_guide", guide_called and (not ocr_called4),
          "guide=%s full_parsed=%s" % (calls4.get("guide", 0), calls4["full_parsed"]))

    print("\n结果：%s" % ("ALL PASS" if not check.failed else "HAS FAIL"))
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
