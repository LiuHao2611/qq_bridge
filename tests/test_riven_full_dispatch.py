# -*- coding: utf-8 -*-
"""tests/test_riven_full_dispatch.py —— 「紫卡全面」口令/截图链路回归测试。

复现线上日志（2026-11-06 19:43:38 / 19:44:14 / 19:44:34）：

    gt_msg text_len=6 text=紫卡全面分析
    gt_match cmd=紫卡 arg=全面分析          ← 被更短的「紫卡」前缀吞掉
    gt_dispatch cmd=紫卡 rlen=78            → 查市价，不是分析
    gt_msg text_len=0 segs=[('image', ...)] ← 文字与图片是**两条独立消息**
    mm_look_failed                          ← 纯图没预约/没@/没关键字 → 落到 AI 看图

本测试**真实加载** game_tools/__init__.py（只桩掉 nonebot 等外部依赖），
回放「文字 → 纯图」两消息序列，断言：
  1. 口令词表含「紫卡全面」系列且预约登记成功；
  2. 文字消息不会被 _match_natural 拆成 cmd=紫卡 arg=全面分析；
  3. 纯图消息（text_len=0）凭预约进入全面分析分支；
  4. 预约一次性消费，不会重复触发。

运行：python qq_bridge/tests/test_riven_full_dispatch.py  （0=通过，1=失败）
"""
import asyncio
import importlib.util
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PKG = "qq_bridge.game_tools"
_failed = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        _failed.append(name)


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__path__ = []
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def load_game_tools():
    """加载真实 game_tools/__init__.py，桩掉 nonebot / 上级包 / 重模块。"""
    root = os.path.join(REPO, "qq_bridge", "src", "plugins")
    pkg_dir = os.path.join(root, "qq_bridge")

    class _Logger:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass

    # nonebot
    _stub("nonebot", on_message=lambda *a, **k: None)
    _stub("nonebot.log", logger=_Logger())
    _stub("nonebot.adapters")
    _stub("nonebot.adapters.onebot")
    _stub("nonebot.adapters.onebot.v11",
          Bot=type("Bot", (), {}), MessageEvent=type("MessageEvent", (), {}))

    # 上级包（真实目录，但 __init__ 不执行）
    qq = _stub("qq_bridge")
    qq.__path__ = [pkg_dir]
    _stub("qq_bridge.config", get_logger=lambda: _Logger(),
          jlog=lambda ev, **f: "event=" + ev)
    _stub("qq_bridge.planstore")
    _stub("qq_bridge.queue")

    class _AiMode:
        def is_on(self, scope):
            return False
    _stub("qq_bridge.ai_mode", ai_mode_store=_AiMode(),
          event_scope=lambda e: "u:test")

    # 重模块桩（本测试只关心口令解析/预约状态机，不需要真实数据）
    gt = _stub(PKG)
    gt.__path__ = [os.path.join(pkg_dir, "game_tools")]
    for nm in ("atlas", "data", "games", "riven", "warframe", "wiki"):
        m = _stub(PKG + "." + nm)
        setattr(gt, nm, m)
    # riven.riven_img_parse 用于断言截图分支确实调用
    gt.riven.riven_img_parse = None
    gt.warframe.riven_text = lambda a: "紫卡市价占位"

    spec = importlib.util.spec_from_file_location(
        PKG, os.path.join(pkg_dir, "game_tools", "__init__.py"),
        submodule_search_locations=[os.path.join(pkg_dir, "game_tools")])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeEvent:
    def __init__(self, text="", image=False):
        self.user_id = 1743124119
        self.group_id = 0
        self._text = text
        self._segs = ([{"type": "image", "data": {"file": "x.png"}}] if image else [])
        if text:
            self._segs.insert(0, {"type": "text", "data": {"text": text}})

    def get_message(self):
        return self._segs


class FakeBot:
    def __init__(self):
        self.sent = []
        self.self_id = 2503891418

    async def send(self, event, msg, **kw):
        self.sent.append(str(msg))
        return {"message_id": len(self.sent)}


def main():
    print("== 紫卡全面 口令/截图链路回归 ==")
    gt = load_game_tools()

    # 桩掉依赖函数，聚焦口令解析 + 预约状态机
    def plain_text(ev):
        return ev._text

    def segments(ev):
        return [types.SimpleNamespace(type=s["type"], data=s.get("data", {}))
                for s in ev._segs]

    def at_bot(ev, bot):
        return False          # 私聊无 @，还原线上场景

    def extract_images(ev):
        return [s["data"] for s in ev._segs if s["type"] == "image"]

    def sk(ev):
        return "u%d" % ev.user_id

    gt._plain_text = plain_text
    gt._event_segments = segments
    gt._at_bot = at_bot
    gt._extract_images = extract_images
    gt._sk = sk
    gt.is_game_command = lambda t: True     # 关闭 AI 门禁干扰
    gt.ai_mode_store = types.SimpleNamespace(is_on=lambda s: False)

    calls = {"parse": 0, "full_send": 0, "classic": 0}

    async def download(img):
        return b"fake-riven-png"

    async def riven_img_parse(data):
        calls["parse"] += 1
        return {"weapon": "rubico", "weapon_zh": "绝路", "rank": 8,
                "disp": 1.2, "stats": []}

    async def send_full_parsed(bot, ev, parsed):
        calls["full_send"] += 1
        await bot.send(ev, "[全面分析卡]")

    async def send_guide(bot, ev, *a, **kw):
        calls["classic"] += 1
        await bot.send(ev, "[旧紫卡分析卡]")

    gt._download_riven_image = download
    gt.riven.riven_img_parse = riven_img_parse
    gt._send_riven_full_parsed = send_full_parsed
    gt._send_guide = send_guide
    gt._riven_recall_queue = lambda bot, ev: asyncio.sleep(0)

    # ---- 断言 1：口令词表含「紫卡全面」系列 ----
    for w in ("紫卡全面", "紫卡全面分析", "全面分析"):
        check("口令词表含 %s" % w, w in gt._FULL_WAIT_WORDS)
        check("NATURAL_CMDS 含 %s" % w, w in gt.NATURAL_CMDS)

    # ---- 断言 2：文字消息不会拆成 cmd=紫卡 arg=全面分析 ----
    m = gt._match_natural("紫卡全面分析")
    check("文字不被短前缀吞掉", m is not None and m[0] == "紫卡全面分析",
          "实际 _match_natural=%r" % (m,))
    m2 = gt._match_natural("紫卡全面")
    check("「紫卡全面」整词命中", m2 is not None and m2[0] == "紫卡全面",
          "实际=%r" % (m2,))

    # ---- 回放线上序列：文字 → 纯图（两条独立消息）----
    async def replay():
        bot = FakeBot()
        ev_text = FakeEvent(text="紫卡全面")
        ev_img = FakeEvent(image=True)          # text_len=0，无 @、无关键字
        await gt._handle(bot, ev_text)
        after_text = dict(calls)
        check("文字消息只登记预约、不误发分析卡",
              after_text["parse"] == 0 and after_text["full_send"] == 0,
              str(after_text))
        check("文字消息回了预约提示",
              any("登记" in s or "截图" in s for s in bot.sent), str(bot.sent))
        await gt._handle(bot, ev_img)
        return bot

    bot = asyncio.run(replay())
    check("纯图凭预约触发 OCR", calls["parse"] == 1, str(calls))
    check("纯图输出全面分析卡", calls["full_send"] == 1, str(calls))
    check("未误走旧紫卡分析流程", calls["classic"] == 0, str(calls))
    check("全面分析卡已发送", any("全面分析卡" in s for s in bot.sent), str(bot.sent))

    # ---- 断言 4：预约一次性消费 ----
    async def replay_second_image():
        b2 = FakeBot()
        await gt._handle(b2, FakeEvent(image=True))
        return b2
    b2 = asyncio.run(replay_second_image())
    check("预约一次性消费，第二张纯图不重复触发",
          calls["parse"] == 1 and calls["full_send"] == 1, str(calls))

    print("== 结果：%s ==" % ("ALL PASS" if not _failed else "FAILED: " + ", ".join(_failed)))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
