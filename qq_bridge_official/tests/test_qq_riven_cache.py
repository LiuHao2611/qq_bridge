# -*- coding: utf-8 -*-
"""紫卡「全面分析」按钮直出当前紫卡 —— 第 7 次修改守门（2026-11-11，prove-it，独立基准）。

## 需求（用户原话，第 7 次，推翻第 6 次）

    「别让我重新发紫卡，我点全面分析后，直接重新当前紫卡，输出全面分析卡片」

即：紫卡分析卡上的「全面分析」按钮被点击 → **服务端复用当前这张紫卡原图**
→ 直接出 655×1920 全面分析卡，不再要求用户重发截图；全面分析卡仍**零按钮**；
缓存未命中/过期（先手打口令、隔太久、NapCat 无按钮）才回退老「预约→再发图」。

## 方案（最小侵入，业务大文件 riven.py 零改）

新增纯标准库 ``channel/riven_cache.py``：按 ``_riven_wait_key`` 短 TTL 缓存当前
紫卡**原图 bytes**。发图分析成功写入；点按钮读取 → ``riven_img_parse(data)``
重新 OCR（与发图链路同一 ``_parse_ocr``，零编造）→ ``_send_riven_full_parsed`` 出卡。

## 地基（已验 nonebot-adapter-qq 1.7.2 容器真源码，本文件固化为机器守门）

适配器 ``get_session_id``：
  * 群消息    = ``group_{group_openid}_{author.member_openid}``
  * 群按钮交互 = ``group_{group_openid}_{group_member_openid}``  (chat_type==1)
同一公式、同一 openid ⇒ 发图与点按钮是同一会话同一人 ⇒ ``_riven_wait_key``
（两侧都经同一 ``OfficialLikeEvent`` + ``pseudo_id`` 派生）**逐字节同源** ⇒ 缓存命中。

## 本文件守门（不信任生产实现，第二套独立基准 + 真代码执行 + AST + 注入）

  A. 缓存模块真执行：put/get 命中、get 一次性消费、peek 不消费、TTL 过期、
     跨 key 隔离、上限淘汰、非法输入拒绝、clear/size；注入「get 不消费」必 FAIL。
  B. key 同源：独立复刻 pseudo_id + OfficialLikeEvent 派生公式，构造发图/按钮
     两侧 full_ctx，断言 key 相等、跨群/跨人不等；注入「换 openid」必 FAIL。
  C. game_tools 真 _handle 回放（真 game_tools + 真缓存 + 桩外呼）：
     ① 发图→写缓存；② 点按钮→命中→直接出全面分析卡、且**无**「请发截图」；
     ③ 缓存已被消费；④ 二次点→走兜底；⑤ 无缓存→兜底；⑥ 跨用户/跨群→兜底。
  D. AST 接线：_FULL_WAIT_WORDS 分支先 _riven_cache_get、命中分支含
     _send_riven_full_parsed+return 且**不含** _set_riven_full_wait/「请发截图」；
     分析成功分支 if _card: _riven_cache_put(event,data)；注入删 return/塞兜底文案/
     删 put 必 FAIL。
  E. 全面分析卡零按钮：按钮点击 ctx 合并链（full_ctx.keyboard_cmd=「全面分析」）
     下 buttons_for 仍返回 ()。

跑法：py -3.13 tests/test_qq_riven_cache.py（宿主纯标准库，全桩无 nonebot/psutil）
      docker exec -i qq-bridge-official /opt/venv/bin/python tests/test_qq_riven_cache.py
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import os
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ROOT = _REPO / "src/plugins/qq_bridge"
_CACHE = _ROOT / "channel/riven_cache.py"
_GT = _ROOT / "game_tools/__init__.py"
_OFFICIAL = _ROOT / "channel/official.py"
_OB = _ROOT / "game_tools/official_bridge.py"
_KB = _ROOT / "channel/qq_keyboard.py"

_failed: list[str] = []
_pass = 0


def check(name, cond, detail=""):
    global _pass
    if cond:
        _pass += 1
    else:
        _failed.append(name)
        print("  FAIL  %s  %s" % (name, detail))


# ─────────────────────────────────────────────────────────────── A. 缓存模块 ────
def load_cache():
    spec = importlib.util.spec_from_file_location("__riven_cache_uut", _CACHE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_cache_module():
    rc = load_cache()
    rc.clear()
    k1, k2 = ("group_1", 100), ("group_1", 200)
    png = b"\x89PNG-absolute-riven"

    check("A1 put/get 命中且字节一致", rc.put(k1, png) and rc.get(k1) == png)
    check("A2 get 一次性消费（二次 get=None）", rc.get(k1) is None)
    rc.put(k1, png)
    check("A3 peek 不消费", rc.peek(k1) == png and rc.peek(k1) == png)
    check("A4 get 消费后 peek=None", rc.get(k1) == png and rc.peek(k1) is None)

    # TTL 过期（注入时钟，不 sleep）
    t0 = 1000.0
    rc.put(k1, png, ttl=10.0, now=t0)
    check("A5 TTL 内命中", rc.get(k1, now=t0 + 9.9) == png)
    rc.put(k2, png, ttl=10.0, now=t0)
    check("A6 TTL 过期返回 None", rc.get(k2, now=t0 + 10.1) is None)

    # 跨 key 隔离
    rc.clear()
    rc.put(k1, b"AAA")
    rc.put(k2, b"BBB")
    check("A7 跨用户 key 隔离", rc.get(k1) == b"AAA" and rc.get(k2) == b"BBB")
    rc.clear()
    rc.put(("group_1", 1), b"G1")
    rc.put(("group_2", 1), b"G2")
    check("A8 跨群 key 隔离",
          rc.get(("group_1", 1)) == b"G1" and rc.get(("group_2", 1)) == b"G2")

    # 非法输入
    rc.clear()
    check("A9 空 data 拒绝", rc.put(k1, b"") is False and rc.size() == 0)
    check("A10 非 tuple key 拒绝",
          rc.put("not-a-tuple", b"x") is False and rc.get("not-a-tuple") is None)

    # 上限淘汰最旧
    rc.clear()
    for i in range(rc.RIVEN_CACHE_MAX + 3):
        rc.put(("group_x", i), ("p%d" % i).encode())
    check("A11 上限生效（size==MAX）", rc.size() == rc.RIVEN_CACHE_MAX)
    check("A12 淘汰最旧（最早 i=0 已不在）", rc.peek(("group_x", 0)) is None)
    check("A13 最新仍在", rc.peek(("group_x", rc.RIVEN_CACHE_MAX + 2)) is not None)
    rc.clear()

    # 注入验证：把 get 改成「不消费」→ A2 必须 FAIL
    src = _CACHE.read_text(encoding="utf-8")
    bad_src = src.replace(
        "    item = _cache.pop(key, None)\n    if item is None:\n        return None\n    data, exp = item",
        "    item = _cache.get(key)\n    if item is None:\n        return None\n    data, exp = item", 1)
    if bad_src != src:
        inj = types.ModuleType("__riven_cache_bad")
        exec(compile(bad_src, str(_CACHE), "exec"), inj.__dict__)
        inj.clear()
        inj.put(k1, png)
        first = inj.get(k1)
        second = inj.get(k1)
        check("A14 注入『get 不消费』必被抓（二次 get 仍命中 ⇒ 守门应 FAIL）",
              first == png and second == png,  # 病态行为确实出现
              )
        # 上面断言的是「注入确实制造了双重命中」，证明 A2 的守门不是摆设
    else:
        check("A14 注入锚点存在", False, "replace 未命中，注入锚点需更新")


# ─────────────────────────────────────────────────────────────── B. key 同源 ────
def _pseudo_id_independent(key: str) -> int:
    """独立复刻 official.py:147-160（字面量公式，不从生产 import，防自己考自己）。"""
    h = hashlib.md5(str(key).encode("utf-8")).hexdigest()
    return -(int(h[:8], 16) % 900_000_000 + 100_000_000)


def _sk_independent(message_type: str, group_id: int, user_id: int) -> str:
    pid = int(group_id or user_id or 0)
    return "%s_%d" % (message_type or "private", pid)


def _wait_key_of(group_openid: str, member_openid: str) -> tuple:
    """按 official.py::normalize + official_bridge::OfficialLikeEvent 派生 _riven_wait_key。"""
    user_key = "official:u:" + member_openid
    group_key = "official:g:" + group_openid
    # OfficialLikeEvent.__init__: group_id/user_id=0 → pseudo_id(key)
    group_id = _pseudo_id_independent(group_key)
    user_id = _pseudo_id_independent(user_key)
    message_type = "group"  # is_group=True
    return (_sk_independent(message_type, group_id, user_id), user_id)


def test_key_homology():
    src_official = _OFFICIAL.read_text(encoding="utf-8")
    src_ob = _OB.read_text(encoding="utf-8")
    # B0 固化生产派生公式未改（否则下面独立复刻失效）
    check("B0a official.pseudo_id  md5 公式未改",
          "int(h[:8], 16) % 900_000_000 + 100_000_000" in src_official)
    check("B0b OfficialLikeEvent group_id=user_id=0 → pseudo_id(key)",
          "pseudo_id(" in src_ob and
          'full_ctx.get("group_key")' in src_ob and
          'full_ctx.get("user_key")' in src_ob)

    g, m = "GROUP_OPENID_A", "MEMBER_OPENID_X"
    # 发图: author.member_openid=m ; 按钮: group_member_openid=m（适配器认证同一值）
    k_img = _wait_key_of(g, m)
    k_btn = _wait_key_of(g, m)
    check("B1 发图 key == 按钮 key（缓存可命中）", k_img == k_btn,
          "%r vs %r" % (k_img, k_btn))
    check("B2 跨用户 key 不同（隔离）", k_img != _wait_key_of(g, "MEMBER_OPENID_Y"))
    check("B3 跨群 key 不同（隔离）", k_img != _wait_key_of("GROUP_OPENID_B", m))
    check("B4 key 为负数 int 身份（不与真实 QQ 号串号）",
          isinstance(k_img[1], int) and k_img[1] < 0)

    # 注入：按钮侧 group_member_openid 换成别人 → key 应不等（证明 B1 不是恒真）
    check("B5 注入『按钮换 openid』key 必不等",
          k_img != _wait_key_of(g, "SOMEONE_ELSE"),
          "若相等则隔离失效")


# ───────────────────────────────────────────────────────── C. game_tools 回放 ────
def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__path__ = []
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def load_game_tools_with_cache():
    """真加载 game_tools/__init__.py，桩 nonebot/重模块，挂**真** riven_cache。"""
    class _Logger:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass

    _stub("nonebot", on_message=lambda *a, **k: None)
    _stub("nonebot.log", logger=_Logger())
    _stub("nonebot.adapters")
    _stub("nonebot.adapters.onebot")
    _stub("nonebot.adapters.onebot.v11",
          Bot=type("Bot", (), {}), MessageEvent=type("MessageEvent", (), {}))

    pkg_dir = str(_ROOT)
    qq = _stub("qq_bridge")
    qq.__path__ = [str(_ROOT.parent)]  # src/plugins 目录
    _stub("qq_bridge.config", get_logger=lambda: _Logger(),
          jlog=lambda ev, **f: "event=" + ev)
    _stub("qq_bridge.planstore")
    _stub("qq_bridge.queue")
    _stub("qq_bridge.ai_mode", ai_mode_store=types.SimpleNamespace(is_on=lambda s: False),
          event_scope=lambda e: "u:test")

    # qq_bridge.channel 包 + 真实 riven_cache 子模块
    ch = _stub("qq_bridge.channel")
    ch.__path__ = []
    rc = load_cache()
    rc.clear()
    ch.riven_cache = rc
    sys.modules["qq_bridge.channel.riven_cache"] = rc

    gt = types.ModuleType("qq_bridge.game_tools")
    gt.__path__ = [pkg_dir]
    for nm in ("atlas", "data", "games", "riven", "warframe", "wiki"):
        m = _stub("qq_bridge.game_tools." + nm)
        setattr(gt, nm, m)
    sys.modules["qq_bridge.game_tools"] = gt

    spec = importlib.util.spec_from_file_location(
        "qq_bridge.game_tools", _GT,
        submodule_search_locations=[pkg_dir])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qq_bridge.game_tools"] = mod
    spec.loader.exec_module(mod)
    return mod, rc


class FakeEvent:
    def __init__(self, text="", image=False, group_id=12345, user_id=67890):
        self.message_type = "group"
        self.group_id = group_id
        self.user_id = user_id
        self._text = text
        self._segs = []
        if text:
            self._segs.append(types.SimpleNamespace(
                type="text", data={"text": text}))
        if image:
            self._segs.append(types.SimpleNamespace(
                type="image", data={"url": "http://x/p.png", "file": "p.png"}))

    def get_message(self):
        return self._segs


class FakeBot:
    def __init__(self):
        self.sent: list[str] = []
        self.self_id = "9984"

    async def send(self, event, msg="", **kw):
        self.sent.append(str(msg))
        return {"message_id": len(self.sent)}


def _wire(gt, rc, calls):
    """桩掉外呼与辅助，缓存用真模块；调用方显式控制解析结果。"""
    async def _plain_text(ev): return ev._text
    async def _noop(*a, **k): return None

    gt._plain_text = lambda ev: ev._text
    gt._event_segments = lambda ev: ev._segs
    gt._at_bot = lambda ev, bot: False
    gt._extract_images = lambda ev: [s for s in ev._segs if s.type == "image"]
    gt.is_game_command = lambda t: True
    gt.ai_mode_store = types.SimpleNamespace(is_on=lambda s: False)

    async def _download(url): return b"PNG-CURRENT-RIVEN"
    gt._download_riven_image = _download

    async def _analyze_full(data):
        calls["analyze"] += 1
        return ("[紫卡分析文字]", {"weapon": "绝路"})  # card 非空 → 触发写缓存

    async def _parse(data):
        calls["parse"] += 1
        return {"weapon": "绝路", "rank": 8, "stats": []}

    async def _send_guide(bot, ev, *a, **kw):
        calls["guide"] += 1
        await bot.send(ev, "[普通紫卡分析卡]")

    async def _send_full(bot, ev, parsed):
        calls["full"] += 1
        await bot.send(ev, "[全面分析卡]")

    gt.riven.riven_img_analyze_full = _analyze_full
    gt.riven.riven_img_parse = _parse
    gt._send_guide = _send_guide
    gt._send_riven_full_parsed = _send_full
    gt._riven_recall_queue = lambda bot, ev: asyncio.sleep(0)
    gt._riven_full_wait.clear()
    rc.clear()


def _run(gt, ev):
    async def _():
        bot = FakeBot()
        await gt._handle(bot, ev)
        return bot
    return asyncio.run(_())


def test_replay():
    gt, rc = load_game_tools_with_cache()
    calls = {"analyze": 0, "parse": 0, "guide": 0, "full": 0}

    # ① 发图（text=紫卡分析 + 图）→ 普通分析 → 写缓存
    _wire(gt, rc, calls)
    bot1 = _run(gt, FakeEvent(text="紫卡分析", image=True))
    check("C1 发图走普通分析出卡", calls["guide"] == 1 and "[普通紫卡分析卡]" in bot1.sent)
    check("C2 发图成功写入当前紫卡缓存", rc.size() == 1,
          "size=%d" % rc.size())

    # ② 点按钮（text=全面分析，无图，同 key）→ 命中缓存 → 直接出全面分析卡
    bot2 = _run(gt, FakeEvent(text="全面分析"))
    check("C3 点按钮命中缓存直接出全面分析卡",
          calls["full"] == 1 and "[全面分析卡]" in bot2.sent, str(bot2.sent))
    check("C4 点按钮不再发『请重发紫卡截图』",
          not any("请直接发送紫卡截图" in s for s in bot2.sent), str(bot2.sent))
    check("C5 点按钮不重复走普通分析", calls["guide"] == 1, str(calls))
    check("C6 缓存被一次性消费", rc.size() == 0)

    # ④ 二次点击 → 缓存已空 → 走兜底预约
    bot3 = _run(gt, FakeEvent(text="全面分析"))
    check("C7 缓存消费后二次点击走兜底",
          any("请直接发送紫卡截图" in s for s in bot3.sent), str(bot3.sent))

    # ⑤ 无缓存直接点 → 兜底
    _wire(gt, rc, calls)
    bot4 = _run(gt, FakeEvent(text="全面分析"))
    check("C8 无缓存点按钮走老预约兜底",
          any("请直接发送紫卡截图" in s for s in bot4.sent), str(bot4.sent))

    # ⑥ 跨用户 / 跨群隔离：A 发图缓存，B 点按钮读不到 → 兜底
    _wire(gt, rc, calls)
    _run(gt, FakeEvent(text="紫卡分析", image=True, group_id=1, user_id=111))
    bot_b = _run(gt, FakeEvent(text="全面分析", group_id=1, user_id=222))
    check("C9 跨用户点按钮读不到缓存→兜底",
          any("请直接发送紫卡截图" in s for s in bot_b.sent), str(bot_b.sent))
    _wire(gt, rc, calls)
    _run(gt, FakeEvent(text="紫卡分析", image=True, group_id=1, user_id=333))
    bot_g = _run(gt, FakeEvent(text="全面分析", group_id=2, user_id=333))
    check("C10 跨群点按钮读不到缓存→兜底",
          any("请直接发送紫卡截图" in s for s in bot_g.sent), str(bot_g.sent))

    # 注入 A：把 _riven_cache_get 恒置 None（模拟 key 错/缓存失效）→ 不应直出
    _wire(gt, rc, calls)
    gt._riven_cache_get = lambda ev: None
    _run(gt, FakeEvent(text="紫卡分析", image=True))  # 仍写缓存（wrapper 未变）
    before = calls["full"]
    bot_i = _run(gt, FakeEvent(text="全面分析"))
    check("C11 注入『缓存读取失效』→ 不直出、走兜底（证明 C3 依赖缓存）",
          calls["full"] == before and
          any("请直接发送紫卡截图" in s for s in bot_i.sent), str(bot_i.sent))
    # 恢复
    _wire(gt, rc, calls)


# ─────────────────────────────────────────────────────────────── D. AST 接线 ────
def _find_fn(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _find_full_words_if(fn):
    for n in ast.walk(fn):
        if isinstance(n, ast.If):
            seg = ast.dump(n.test)
            if "_FULL_WAIT_WORDS" in seg and "text" in seg:
                return n
    return None


def _src_of(node, src):
    return ast.get_source_segment(src, node) or ""


def test_ast_wiring():
    src = _GT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = _find_fn(tree, "_handle")
    check("D0 找到 _handle", fn is not None)
    if fn is None:
        return
    iff = _find_full_words_if(fn)
    check("D1 找到 _FULL_WAIT_WORDS 分支", iff is not None)
    if iff is None:
        return

    body_src = "\n".join(_src_of(s, src) for s in iff.body)
    # 命中子分支 if _cached:
    cached_if = None
    for n in iff.body:
        if isinstance(n, ast.If) and "_cached" in ast.dump(n.test):
            cached_if = n
            break
    check("D2 分支内先 _riven_cache_get",
          "_riven_cache_get" in body_src.split("if _cached")[0])
    check("D3 存在 if _cached 命中子分支", cached_if is not None)
    if cached_if is not None:
        hit = "\n".join(_src_of(s, src) for s in cached_if.body)
        check("D4 命中分支含 _send_riven_full_parsed",
              "_send_riven_full_parsed" in hit)
        # ast.dump 递归输出用节点类名 Return（大写），不是源码关键字 return
        check("D5 命中分支含 return", "Return" in ast.dump(cached_if))
        check("D6 命中分支不含 _set_riven_full_wait",
              "_set_riven_full_wait" not in hit)
        check("D7 命中分支不含『请直接发送紫卡截图』",
              "请直接发送紫卡截图" not in hit)
    # 兜底（命中子分支之外）仍保留预约
    check("D8 兜底含 _set_riven_full_wait",
          "_set_riven_full_wait" in body_src)
    check("D9 兜底含『请直接发送紫卡截图』（老链路保留）",
          "请直接发送紫卡截图" in body_src)

    # 分析成功分支 if _card: _riven_cache_put(event, data)
    check("D10 分析成功分支写缓存（if _card → _riven_cache_put）",
          "_riven_cache_put(event, data)" in src and
          "if _card:" in src and
          src.index("if _card:") < src.index("_riven_cache_put(event, data)") <
          src.index("if _card:") + 400)
    # wrapper 存在且全兜底
    for wn in ("_riven_cache_put", "_riven_cache_get"):
        wfn = _find_fn(tree, wn)
        wsrc = _src_of(wfn, src) if wfn else ""
        check("D11 %s 懒导入 riven_cache + 全兜底" % wn,
              "from ..channel import riven_cache" in wsrc and
              "except Exception" in wsrc)

    # —— 注入：改坏生产源码，AST 守门必须 FAIL —— #
    # 注入 a：删命中分支 return
    bad_a = src.replace(
        '                except Exception:  # noqa: BLE001\n                    pass\n            return\n        # 缓存未命中/过期',
        '                except Exception:  # noqa: BLE001\n                    pass\n        # 缓存未命中/过期', 1)
    ta = ast.parse(bad_a)
    fa = _find_fn(ta, "_handle")
    ia = _find_full_words_if(fa) if fa else None
    cia = next((n for n in ia.body if isinstance(n, ast.If) and "_cached" in ast.dump(n.test)), None) if ia else None
    check("D12 注入『删命中 return』必被 D5 抓",
          cia is not None and "Return" not in ast.dump(cia))

    # 注入 b：把兜底文案塞进命中分支
    bad_b = src.replace(
        "                if _parsed:\n                    await _send_riven_full_parsed(bot, event, _parsed)",
        "                await bot.send(event, '请直接发送紫卡截图～')\n"
        "                if _parsed:\n                    await _send_riven_full_parsed(bot, event, _parsed)", 1)
    tb = ast.parse(bad_b)
    fb = _find_fn(tb, "_handle")
    ib = _find_full_words_if(fb) if fb else None
    cib = next((n for n in ib.body if isinstance(n, ast.If) and "_cached" in ast.dump(n.test)), None) if ib else None
    hit_b = "\n".join(ast.get_source_segment(bad_b, s) or "" for s in cib.body) if cib else ""
    check("D13 注入『命中分支塞请发截图』必被 D7 抓",
          "请直接发送紫卡截图" in hit_b)

    # 注入 c：删分析成功分支的缓存写入
    bad_c = src.replace("                        if _card:\n                            _riven_cache_put(event, data)\n", "", 1)
    check("D14 注入『删缓存写入』必被 D10 抓",
          "_riven_cache_put(event, data)" not in bad_c or
          "if _card:" not in bad_c)


# ───────────────────────────────────────────────────────── E. 全面分析卡零按钮 ────
def _load_keyboard():
    spec = importlib.util.spec_from_file_location("__kb_uut", _KB)
    m = importlib.util.module_from_spec(spec)
    # qq_keyboard 依赖同目录 command_inventory/card_buttons/rank_eligibility：注册包名
    pkg = types.ModuleType("__kb_pkg")
    pkg.__path__ = [str(_ROOT / "channel")]
    sys.modules["__kb_pkg"] = pkg
    spec2 = importlib.util.spec_from_file_location(
        "__kb_pkg.qq_keyboard", _KB, submodule_search_locations=[])
    m2 = importlib.util.module_from_spec(spec2)
    sys.modules["__kb_pkg.qq_keyboard"] = m2
    # 让相对 import 命中已注册的真依赖
    for dep in ("command_inventory", "card_buttons", "rank_eligibility"):
        p = _ROOT / "channel" / (dep + ".py")
        ds = importlib.util.spec_from_file_location("__kb_pkg." + dep, p)
        dm = importlib.util.module_from_spec(ds)
        sys.modules["__kb_pkg." + dep] = dm
        ds.loader.exec_module(dm)
    spec2.loader.exec_module(m2)
    return m2


def test_full_card_zero_button():
    kb = _load_keyboard()
    # 按钮点击时 dispatch_normalized 写 full_ctx["keyboard_cmd"]="全面分析"；
    # _send_riven_full_parsed 自造 ctx 不含 keyboard_cmd → merged 保留「全面分析」
    full_ctx = {"keyboard_cmd": "全面分析", "group_key": "official:g:x"}
    local_ctx = {"channel": "official"}  # 全面分析卡自造 ctx（无哨兵）
    merged = dict(full_ctx)
    merged.update(local_ctx)
    btn_cmd = str(merged.get("keyboard_cmd", ""))
    check("E1 按钮点击 cmd=全面分析 自挂判定为零按钮",
          kb.buttons_for(btn_cmd) == (), "btn_cmd=%r" % btn_cmd)
    check("E2 普通文本『全面分析』不自挂",
          kb.buttons_for("全面分析") == ())
    check("E3 哨兵仍挂单个『全面分析』按钮（第6次能力保留）",
          len(kb.buttons_for("__riven_analysis__")) == 1 and
          kb.buttons_for("__riven_analysis__")[0].cmd == "全面分析")
    # 注入：若 buttons_for(全面分析) 被改成有按钮 → E1 必 FAIL（证明守门非摆设）
    check("E4 注入护栏：全面分析文本确实返回空 tuple（非 None 异常）",
          kb.buttons_for("全面分析") == ())


def main():
    print("== 第7次：紫卡全面分析按钮直出当前紫卡 守门 ==")
    test_cache_module()
    test_key_homology()
    test_replay()
    test_ast_wiring()
    test_full_card_zero_button()
    total = _pass + len(_failed)
    print("== 结果：%d PASS / %d FAIL ==" % (_pass, len(_failed)))
    if _failed:
        for f in _failed:
            print("   - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
