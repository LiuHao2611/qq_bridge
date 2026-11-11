# -*- coding: utf-8 -*-
"""QQ 官方被动回复 msg_seq 跨 send 去重守门（2026-11-11，prove-it）。

## 真实生产事故（用户「看看日志，紫卡分析没出结果」）

紫卡分析图**已渲染成功**（``pic_render template=riven_analysis bytes=176090``、
``gt_render_done kind=riven_analysis``），但发送被平台拒：

    channel_send_failed ... code=40054005, message=消息被去重，请检查请求msgseq

根因：适配器 ``Bot.send`` 对同一入站消息维护 ``event._reply_seq``，每次 send
``+=1`` 后透传 msg_seq（bot.py:691/706）——同一 msg_id 下第 N 次被动回复必须
``msg_seq==N``。我们的 ``OfficialChannel.send`` 绕过 Bot.send 直调
``send_to_group``，旧实现 ``msg_seq=(i+1)``（i 是单次 send 段内下标）导致每次
send 都从 1 起；紫卡一次事件两次 send（排队话术 + 结果图）撞 seq=1 → 去重拒收。

## 守门设计（行为为主、AST 为辅、真实错误注入）

* **行为**：宿主从源码直载 ``channel/passive_seq.py``（顶层零依赖）真跑分配器，
  用**另一套独立参考计数器**（本文件自实现，不 import 被测逻辑）逐条比对：
  ① 跨 send 严格递增（复现紫卡两次 send）；② 单 send 多段递增；③ 不同
  reply_ref 独立计数；④ 累计 >5 转主动返回 None 且不再推进；⑤ event_id /
  缺字段 / 非法值安全降级；⑥ peek 只读。
* **注入**：把被测分配器替换成「固定返回 1」的旧 bug 实现，断言同一套守门
  **必然抓到重复**（证明守门不是摆设，能拦住本次真实根因）。
* **AST**：``official.py::send`` 必须经 ``next_msg_seq(ref)`` 计算 msg_seq、
  两处 send_to_* 都传 ``msg_seq=msg_seq``、**不得残留** ``msg_seq=(i+1)``、
  顶层 import 了 passive_seq、日志带 ``reply_seq`` 可观测。

跑法：
  python tests/test_qq_msg_seq.py                        # 宿主纯标准库
  docker cp tests/test_qq_msg_seq.py <ctr>:/app/qk_test/ && \
    docker exec <ctr> /opt/venv/bin/python /app/qk_test/test_qq_msg_seq.py
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CH = _REPO / "src/plugins/qq_bridge/channel"
_PASSIVE_SEQ = _CH / "passive_seq.py"
_OFFICIAL = _CH / "official.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


seqmod = _load(_PASSIVE_SEQ, "qq_bridge_passive_seq_testmsgseq")


# ---------------------------------------------------------------------------
# 独立第二套基准：本文件自实现的参考计数器，与被测模块**逻辑等价但代码独立**。
# 自己考自己 = 拿 seqmod 的输出断言 seqmod；这里用 ref 计数器做镜像比对，
# 一旦被测实现被改成「固定 1 / 不推进 / 共享计数」，与镜像立即分叉。
# ---------------------------------------------------------------------------
class _ReferenceCounter:
    """按适配器 event._reply_seq 语义的独立参考实现：逐次 +1，上限封顶。"""

    def __init__(self, *, max_seq: int = 5, start: int = 1):
        self.cur = start
        self.max_seq = max_seq

    def take(self) -> int | None:
        if self.cur > self.max_seq:
            return None
        out = self.cur
        self.cur += 1
        return out


def _assert_allocator(alloc, *, label: str):
    """对任意 next_msg_seq 兼容实现跑全套行为基准（注入时复用，抓 bug 实现）。"""
    # ① 跨 send 严格递增（紫卡：排队话术 send 一段，结果图 send 一段）
    ref = {"msg_id": "m-riven", "msg_seq": 1, "ts": 123.0}
    s1 = alloc(ref)                       # 第一次 send（排队话术）
    s2 = alloc(ref)                       # 第二次 send（结果图）
    assert s1 == 1 and s2 == 2, (
        "%s 跨 send 未严格递增：紫卡两次 send 拿到 seq=(%r,%r)，重复会被 "
        "40054005 去重拒收（本次真实根因）" % (label, s1, s2)
    )
    assert s1 != s2, "%s 跨 send 序号重复（40054005）" % label

    # ② 同一 send 内多段递增（text + image + keyboard 三段）
    ref2 = {"msg_id": "m-multi", "msg_seq": 1}
    seqs = [alloc(ref2) for _ in range(3)]
    assert seqs == [1, 2, 3], "%s 单 send 多段未递增：%r" % (label, seqs)

    # ③ 不同 reply_ref 独立计数（ContextVar 每事件隔离，绝不能串号）
    a = {"msg_id": "m-a", "msg_seq": 1}
    b = {"msg_id": "m-b", "msg_seq": 1}
    assert alloc(a) == 1
    assert alloc(b) == 1, "%s 不同 reply_ref 未独立计数（串号）" % label
    assert alloc(a) == 2

    # ④ 累计超 5 次被动回复上限 → 返回 None 转主动，且不再推进计数
    ref3 = {"msg_id": "m-cap", "msg_seq": 1}
    got = [alloc(ref3) for _ in range(7)]
    assert got == [1, 2, 3, 4, 5, None, None], (
        "%s 被动上限/转主动语义错：%r（平台每条消息最多回 5 次）" % (label, got)
    )

    # ⑤ event_id 场景（按钮回调，无 msg_seq 字段）+ 非法值安全降级
    ev = {"event_id": "INTERACTION_CREATE:x"}
    assert alloc(ev) == 1, "%s event_id 场景未从 1 起" % label
    for bad in (None, "", "not-int", 0, -3):
        r = {"msg_id": "m", "msg_seq": bad}
        assert alloc(r) == 1, "%s 非法 msg_seq=%r 未安全降级为 1" % (label, bad)

    # 与独立参考计数器全序列比对（防实现被改坏而上面锚点恰好没覆盖）
    ref4 = {"msg_id": "m-ref", "msg_seq": 1}
    mirror = _ReferenceCounter(start=1)
    for _ in range(8):
        assert alloc(ref4) == mirror.take(), (
            "%s 与独立参考计数器分叉（实现被改坏）" % label
        )


def test_allocator_behavior_matches_independent_reference():
    _assert_allocator(seqmod.next_msg_seq, label="next_msg_seq")


def test_peek_is_read_only():
    ref = {"msg_id": "m", "msg_seq": 1}
    assert seqmod.peek_msg_seq(ref) == 1
    assert ref["msg_seq"] == 1, "peek 不得推进计数"
    seqmod.next_msg_seq(ref)
    assert seqmod.peek_msg_seq(ref) == 2
    assert seqmod.peek_msg_seq({}) == 1       # 缺字段安全
    assert seqmod.peek_msg_seq(None) == 1     # 非 dict 安全


def test_injection_old_fixed_seq_one_is_caught():
    """注入「固定返回 1」的旧 bug 实现 → 同一套守门必须 FAIL（证明守门有效）。"""

    def _buggy_fixed_one(ref):
        return 1  # 复现旧实现 msg_seq=(i+1)：每次 send 都从 1 起

    caught = False
    try:
        _assert_allocator(_buggy_fixed_one, label="INJECTED_fixed_1")
    except AssertionError as e:
        caught = True
        assert "跨 send" in str(e) or "重复" in str(e), \
            "守门应明确抓到跨 send 重复：%s" % e
    assert caught, "注入旧 bug（固定 seq=1）却没被守门抓到 —— 守门是摆设"


def test_injection_shared_counter_no_advance_is_caught():
    """注入「不推进计数」实现 → 守门必须在多段/上限处 FAIL。"""

    def _buggy_no_advance(ref):
        return 1  # 读完不写回 → 永远返回 1

    caught = False
    try:
        _assert_allocator(_buggy_no_advance, label="INJECTED_no_advance")
    except AssertionError:
        caught = True
    assert caught, "注入「不推进计数」却没被守门抓到"


# ---------------------------------------------------------------------------
# AST 接线守门：send() 必须真正用上分配器，杜绝「模块写对了但没接」
# ---------------------------------------------------------------------------
def _send_fn(src: str):
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "send"), None)
    assert fn is not None, "找不到 OfficialChannel.send"
    return fn


def _guard_send_wiring(src: str):
    tree = ast.parse(src)
    fn = _send_fn(src)
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]

    # ① 必须 import passive_seq
    imported = any(
        isinstance(n, (ast.Import, ast.ImportFrom))
        and "passive_seq" in ast.unparse(n) for n in ast.walk(tree)
    )
    assert imported, "official.py 未 import passive_seq（修复未接线）"

    # ② send() 必须调用 next_msg_seq(ref)
    uses_next = any(
        getattr(c.func, "id", "") == "next_msg_seq"
        and any(ast.unparse(a) == "ref" for a in c.args)
        for c in calls
    )
    assert uses_next, "send() 未调用 next_msg_seq(ref)（msg_seq 仍可能重复 → 40054005）"

    # ③ 两处 send_to_group / send_to_c2c 都必须传 msg_seq=msg_seq
    sends = [c for c in calls
             if getattr(c.func, "attr", "") in ("send_to_group", "send_to_c2c")]
    assert len(sends) == 2, "send() 的 send_to_group/send_to_c2c 调用数变了（%d）" % len(sends)
    for c in sends:
        kw = {k.arg: ast.unparse(k.value) for k in c.keywords}
        assert kw.get("msg_seq") == "msg_seq", \
            "send_to_* 未透传跨 send 递增的 msg_seq（拿到 %r）" % kw.get("msg_seq")

    # ④ 不得残留旧 bug msg_seq=(i+1) / (i + 1)
    fn_src = ast.unparse(fn)
    for banned in ("msg_seq=(i+1)", "msg_seq=(i + 1)", "i + 1) if use_passive"):
        assert banned not in fn_src, \
            "send() 残留旧 bug %r（每次 send 从 1 起 → 40054005）" % banned

    # ⑤ 日志带 reply_seq 可观测（排障去重直接看序号）
    assert "reply_seq" in fn_src and "peek_msg_seq" in fn_src, \
        "official_sent 日志缺 reply_seq 可观测字段"


def test_official_send_msg_seq_wiring():
    _guard_send_wiring(_OFFICIAL.read_text(encoding="utf-8"))


def test_passive_seq_has_zero_top_level_dependency():
    """passive_seq 顶层必须零依赖（宿主可直载真跑；不 import nonebot/warframe/httpx）。"""
    tree = ast.parse(_PASSIVE_SEQ.read_text(encoding="utf-8"))
    banned = ("nonebot", "warframe", "httpx", "requests", "PIL", "config")
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            mod = ast.unparse(n)
            for b in banned:
                assert b not in mod, "passive_seq 顶层不得依赖 %s（宿主直载会炸）：%s" % (b, mod)


# ---------------------------------------------------------------------------
# 真执行 send()：AST 抽真函数体 + 注入假依赖，宿主（无 nonebot/pytest/psutil）
# 与容器（真 nonebot）都能裸跑。这是「send() 接线正确」的最强行为证据，
# 不是只看 AST 字符串——真跑两次 send，断言 msg_seq 严格 1、2 不重复。
# ---------------------------------------------------------------------------
def _extract_send_callable(src: str, ns: dict):
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "send"), None)
    assert fn is not None, "找不到 OfficialChannel.send"
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "<official.send>", "exec"), ns)  # noqa: S102 — 仅测自己的源码
    return ns["send"]


class _NullLog:
    def __getattr__(self, _name):
        return lambda *_a, **_kw: None


class _FakeMsg(list):
    def __iadd__(self, other):
        if isinstance(other, list):
            self.extend(other)
        else:
            self.append(other)
        return self


class _FakeSeg:
    @staticmethod
    def text(content):
        return ("text", content)

    @staticmethod
    def mention_user(openid, username=None):
        return ("mention_user", openid)


class _FakeBot:
    def __init__(self):
        self.seqs: list = []
        self.msg_ids: list = []

    async def send_to_group(self, group_openid, message, msg_id=None, msg_seq=None,
                            event_id=None, msg_ref_id=None):
        self.seqs.append(msg_seq)
        self.msg_ids.append(msg_id)
        return type("R", (), {"id": "sent-%d" % len(self.seqs)})()

    async def send_to_c2c(self, openid, message, msg_id=None, msg_seq=None,
                          event_id=None, msg_ref_id=None):
        self.seqs.append(msg_seq)
        self.msg_ids.append(msg_id)
        return type("R", (), {"id": "sent-%d" % len(self.seqs)})()


class _Out:
    """最小 Outgoing 替身：只给 send() 用到的字段。"""

    def __init__(self, text=""):
        self.text = text
        self.image_urls = []
        self.images = []
        self.video = ""
        self.at_user = False
        self.keyboard_cmd = ""


def _send_namespace(bot: _FakeBot):
    """构造 send() 函数体所需的最小全局命名空间（不含任何真实适配器）。"""
    import time as _time

    class _Cfg:
        OFFICIAL_PASSIVE_MAX_AGE = 300

    class _Limiter:
        def allow(self, _k):
            return True

        def used(self, _k):
            return 0

        def add(self, _k):
            pass

    return {
        "time": _time,
        "str": str,
        "Any": object,
        "Config": _Cfg,
        "official_limiter": _Limiter(),
        "GROUP_PREFIX": "official:g:",
        "USER_PREFIX": "official:u:",
        "_IMAGE_BUTTON_MERGE": False,
        "current_official_ctx": lambda: None,     # 宿主直测：ctx 已完整，无需 ContextVar 补全
        "_get_official_bot": lambda _cls: bot,
        "_import_qq": lambda: (object, _FakeMsg, _FakeSeg,
                               object, object, object),
        "_buttons_for": lambda _cmd: [],           # 零按钮 → 纯 text 段，不触发 merge/素材
        "_download": lambda _u: None,
        "_build_merged_card_segment": None,
        "_build_button_segment": lambda *a, **k: (None, []),
        "next_msg_seq": seqmod.next_msg_seq,
        "peek_msg_seq": seqmod.peek_msg_seq,
        "log": _NullLog(),
        "jlog": lambda evt, **kw: {"event": evt, **kw},
        "Outgoing": _Out,
    }


def test_real_send_two_calls_have_increasing_msg_seq():
    """真执行官方副本 send() 两次（共享 reply_ref），断言 msg_seq==[1,2]。"""
    bot = _FakeBot()
    send = _extract_send_callable(_OFFICIAL.read_text(encoding="utf-8"),
                                  _send_namespace(bot))
    ctx = {
        "channel": "official",
        "group_key": "official:g:grp-x",
        "user_key": "official:u:member-x",
        "user_id": 0, "group_id": 0, "self_id": "bot", "is_group": True,
        "at_bot": False, "trace_id": "t",
        "reply_ref": {"msg_id": "msg-riven", "msg_seq": 1, "ts": __import__("time").time()},
    }
    r1 = __import__("asyncio").run(send(None, ctx, _Out("已加入分析队列，稍等！")))
    r2 = __import__("asyncio").run(send(None, ctx, _Out("紫卡分析结果图")))
    assert r1.get("ok") and r2.get("ok"), "两次 send 都应成功：r1=%r r2=%r" % (r1, r2)
    assert bot.seqs == [1, 2], \
        "真 send() 两次被动回复 msg_seq 必须 [1,2]（重复=40054005 紫卡没出结果）：%r" % bot.seqs
    assert bot.msg_ids == ["msg-riven", "msg-riven"], "两次应引用同一 msg_id"
    assert ctx["reply_ref"]["msg_seq"] == 3, \
        "send() 必须把递增后的 msg_seq 写回共享 reply_ref（供跨 send 继承）"


def test_real_send_injection_fixed_one_is_caught():
    """注入「固定 seq=1」的旧实现到真 send() → 必须被 [1,2] 断言抓到（prove-it）。"""
    bot = _FakeBot()
    ns = _send_namespace(bot)
    ns["next_msg_seq"] = lambda _ref: 1     # 复现旧 bug：每次都从 1 起
    send = _extract_send_callable(_OFFICIAL.read_text(encoding="utf-8"), ns)
    ctx = {
        "channel": "official", "group_key": "official:g:g", "user_key": "official:u:u",
        "user_id": 0, "group_id": 0, "self_id": "b", "is_group": True, "at_bot": False,
        "trace_id": "t",
        "reply_ref": {"msg_id": "m", "msg_seq": 1, "ts": __import__("time").time()},
    }
    __import__("asyncio").run(send(None, ctx, _Out("a")))
    __import__("asyncio").run(send(None, ctx, _Out("b")))
    caught = bot.seqs == [1, 1]
    assert caught, \
        "注入固定 seq=1 后真 send() 应复现 [1,1] 重复（守门要能抓到旧 bug），实际 %r" % bot.seqs
    # 再证明：同样的 [1,1] 会被上面正常断言拒绝
    try:
        assert bot.seqs == [1, 2], "msg_seq 必须 [1,2]"
        raise AssertionError("注入的 [1,1] 不应通过 [1,2] 断言")
    except AssertionError as e:
        assert "msg_seq 必须" in str(e) or "不应通过" in str(e)


if __name__ == "__main__":
    test_allocator_behavior_matches_independent_reference()
    test_peek_is_read_only()
    test_injection_old_fixed_seq_one_is_caught()
    test_injection_shared_counter_no_advance_is_caught()
    test_official_send_msg_seq_wiring()
    test_passive_seq_has_zero_top_level_dependency()
    test_real_send_two_calls_have_increasing_msg_seq()
    test_real_send_injection_fixed_one_is_caught()
    print("test_qq_msg_seq.py: 8 PASS / 0 FAIL")
