# -*- coding: utf-8 -*-
"""QQ 官方按钮消息「发送有效性」回归守门（2026-11-11，prove-it）。

## 为什么单独建这个文件（针对真实生产事故）

按钮重做后真实群实测：主图三次全成功，按钮段三次全被平台拒绝：
    ActionFailed: 400, code=40034011, message=无效 markdown content
根因：``_build_button_segment`` 只发**纯 keyboard** 段 → 适配器置
``msg_type=2``，但官方要求 msg_type=2 必须同时有合法 ``markdown.content``
（官方「发送带有按钮的消息」：仅 markdown 消息支持消息按钮）。
修法：``make_button_message`` 统一构造 **markdown + keyboard 同段**。

上一轮 ``tests/test_qq_keyboard.py`` 只验 ``MessageKeyboard`` 模型结构，
**完全没有验最终发送的段组合**，所以纯 keyboard 这种「结构合法但平台拒收」
的错溜过了 8 个测试。本文件补上这一层，且用**真实错误的反向注入**证明
守门会报错，不是摆设。

## 三道硬守门

① 段组合：``make_button_message`` 必须产出 ``markdown → keyboard`` 两段，
   markdown.content 非空、keyboard 是传入模型（退化成纯 keyboard 立即 FAIL）；
② 接线：``official.py::_build_button_segment`` 必须调用 ``make_button_message``，
   且函数体内**不得**出现裸 ``keyboard(`` 直构（防绕过统一出口）；
③ adapter 隔离：``doupo``/``xiuxian`` 的 ``_mode_is._r`` 参数必须注解
   ``MessageEvent``（删注解 → 官方全量群消息刷 ``Rule check failed`` ERROR）。

另有容器专项（宿主 skip，nonebot 真实 payload）：
④ 用真 ``Bot._extract_send_message`` 断言 ``content=None / markdown.content
   非空 / keyboard 在 / msg_type=2``，与官方群消息示例逐字段对齐。

跑法：
  python tests/test_qq_button_payload.py                      # 宿主纯标准库，④自动 skip
  docker cp tests/test_qq_button_payload.py <ctr>:/app/qk_test/ && \
    docker exec <ctr> python /app/qk_test/test_qq_button_payload.py   # 容器含④
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CH = _REPO / "src/plugins/qq_bridge/channel"
_OFFICIAL = _CH / "official.py"
_DOUPO = _REPO / "src/plugins/doupo/__init__.py"
_XIUXIAN = _REPO / "src/plugins/xiuxian/__init__.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# 先注入依赖的 command_inventory，再载 qq_keyboard（与 test_qq_keyboard 同法）
_load(_CH / "command_inventory.py", "command_inventory")
kbmod = _load(_CH / "qq_keyboard.py", "qq_keyboard")


# ---- 假段类型：完全脱离 nonebot，宿主可测 make_button_message 的段组合 ----
class _Seg:
    calls: list = []

    @classmethod
    def markdown(cls, content):
        cls.calls.append(("markdown", content))
        return ("M", content)

    @classmethod
    def keyboard(cls, model):
        cls.calls.append(("keyboard", model))
        return ("K", model)


class _Msg:
    def __init__(self):
        self.parts = []

    def __iadd__(self, part):
        self.parts.append(part)
        return self


# ---------------------------------------------------------------------------
# 守门①：段组合 markdown → keyboard（真实 40034011 的根因）
# ---------------------------------------------------------------------------
def _guard_button_segment_order():
    _Seg.calls.clear()
    kb = object()  # 传入的键盘模型，必须原样挂到 keyboard 段
    msg = kbmod.make_button_message(kb, _Msg, _Seg)
    kinds = [c[0] for c in _Seg.calls]
    assert kinds == ["markdown", "keyboard"], (
        "按钮段必须是 markdown→keyboard 同段（缺 markdown 会被平台以 "
        "40034011 拒收），实际段序列=%s" % kinds
    )
    assert msg.parts[0][0] == "M" and msg.parts[0][1], (
        "markdown.content 必须非空，否则 40034011 无效 markdown content"
    )
    assert msg.parts[1][0] == "K" and msg.parts[1][1] is kb, (
        "keyboard 段必须原样携带传入的键盘模型"
    )


def test_button_segment_has_markdown_then_keyboard():
    _guard_button_segment_order()


def test_button_markdown_text_is_valid():
    """引导文案必须是非空纯文本/标准 markdown，不含模板占位符（个人主体无模板权限）。"""
    text = kbmod.BUTTON_MARKDOWN
    assert isinstance(text, str) and text.strip(), "BUTTON_MARKDOWN 不能为空"
    assert "template_id" not in text and "{{" not in text, (
        "BUTTON_MARKDOWN 不得依赖模板/占位符（个人主体无 Markdown 模板权限）"
    )


# ---------------------------------------------------------------------------
# 守门②：official.py 接线（防绕过统一出口再发纯 keyboard）
# ---------------------------------------------------------------------------
def _guard_official_build(src: str):
    tree = ast.parse(src)
    fn = next(
        (n for n in tree.body
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "_build_button_segment"),
        None,
    )
    assert fn is not None, "official.py 找不到 _build_button_segment"
    calls = [ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Call)]
    assert any("make_button_message" in c for c in calls), (
        "official.py::_build_button_segment 必须调用 make_button_message"
        "（统一构造 markdown+keyboard），防退回纯 keyboard → 40034011"
    )
    # 精确识别「段级」裸 keyboard 构造（_QQSegment.keyboard / seg += ...
    # .keyboard），但排除 make_keyboard(...) —— 它只是拿键盘模型、不含发段。
    bare_kb = [
        c for c in calls
        if "keyboard(" in c
        and "make_keyboard(" not in c
        and "make_button_message" not in c
    ]
    assert not bare_kb, (
        "_build_button_segment 内出现裸 keyboard(...) 段直构，可能绕过 markdown"
        " 又发纯 keyboard：%s" % bare_kb
    )
    # 顺带守住上一轮 P0：_import_qq 必须返回 6 元组（缺 return 会让官方 Bot 全瘫）
    imp = next(
        (n for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name == "_import_qq"), None)
    assert imp is not None and any(isinstance(n, ast.Return) and n.value
                                   for n in ast.walk(imp)), (
        "_import_qq 必须有返回 6 元组的 return（历史 P0：误删会全瘫）"
    )


def test_official_build_wires_markdown_keyboard():
    _guard_official_build(_OFFICIAL.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 守门③：doupo/xiuxian rule 参数注解（防官方全量消息刷 ERROR）
# ---------------------------------------------------------------------------
def _guard_rule_typed_event(src: str, label: str):
    tree = ast.parse(src)
    mode_is = next(
        (n for n in tree.body
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "_mode_is"),
        None,
    )
    assert mode_is is not None, "%s 找不到 _mode_is" % label
    inner = next(
        (n for n in ast.walk(mode_is)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_r"),
        None,
    )
    assert inner is not None, "%s::_mode_is 找不到内层 rule 函数 _r" % label
    ann = next((a.annotation for a in inner.args.args if a.arg == "event"), None)
    assert ann is not None and ast.unparse(ann) == "MessageEvent", (
        "%s::_mode_is._r 的 event 必须注解具体 MessageEvent，否则官方"
        " GroupMessageCreateEvent 会进入 rule 执行 event.user_id → 每条消息刷 "
        "Rule check failed ERROR" % label
    )


def test_doupo_xiuxian_rule_typed_event():
    _guard_rule_typed_event(_DOUPO.read_text(encoding="utf-8"), "doupo")
    _guard_rule_typed_event(_XIUXIAN.read_text(encoding="utf-8"), "xiuxian")


# ---------------------------------------------------------------------------
# 守门④：容器内用真 nonebot 段跑最终 payload 提取（宿主无 nonebot → skip）
# ---------------------------------------------------------------------------
def test_official_payload_extract_markdown_keyboard():
    try:
        from nonebot.adapters.qq import Bot as QQBot
        from nonebot.adapters.qq import Message as QQMessage
        from nonebot.adapters.qq import MessageSegment as QQSegment
    except ImportError:
        print("[skip] nonebot 不在宿主，真 payload 项在容器内跑")
        return

    kb_model, _ = kbmod.make_keyboard("赏金")
    seg = kbmod.make_button_message(kb_model, QQMessage, QQSegment)
    kwargs = QQBot._extract_send_message(seg, escape_text=False)
    assert kwargs.get("content") is None, (
        "官方要求传 markdown 后 content 必须全为空，实际=%r" % kwargs.get("content")
    )
    md = kwargs.get("markdown")
    assert md is not None and getattr(md, "content", None), (
        "markdown.content 必须非空，否则 40034011"
    )
    assert kwargs.get("keyboard") is not None, "keyboard 段必须在最终 payload 中"
    assert md or kwargs.get("keyboard"), "应判定 msg_type=2"


# ---------------------------------------------------------------------------
# 注入验证（prove-it 强制）：把这次的三类真实错放回去，守门必须分别 FAIL
# ---------------------------------------------------------------------------
def test_injection_guards():
    # 注入 A：退化 make_button_message 成「纯 keyboard」（= 40034011 根因）
    original = kbmod.make_button_message

    def _pure_keyboard(kb_model, QQMessage, QQSegment):
        seg = QQMessage()
        seg += QQSegment.keyboard(kb_model)   # 缺 markdown
        return seg

    try:
        kbmod.make_button_message = _pure_keyboard
        try:
            _guard_button_segment_order()
            raise AssertionError("守门①未报错（纯 keyboard 退化没被拦 → 40034011 会重现）")
        except AssertionError as e:
            assert "40034011" in str(e), "守门①报错位置不对：%s" % e
    finally:
        kbmod.make_button_message = original
    _guard_button_segment_order()  # 恢复后必须过

    src = _OFFICIAL.read_text(encoding="utf-8")
    anchor = "seg = make_button_message(kb_model, _QQMessage, _QQSegment)"
    assert anchor in src, "注入 B/C 没找到锚点"

    # 注入 B1：把统一出口调用整段删掉 → 守门②-a「必须调用 make_button_message」应报错
    bad_b1 = src.replace(anchor, "seg = _QQMessage()  # 退化为不接统一出口", 1)
    try:
        _guard_official_build(bad_b1)
        raise AssertionError("守门②-a未报错（缺 make_button_message 接线没被拦）")
    except AssertionError as e:
        assert "必须调用 make_button_message" in str(e), "守门②-a报错位置不对：%s" % e

    # 注入 B2：保留统一出口，但额外塞一段裸 keyboard（模拟绕过出口又发纯 keyboard）
    # → 守门②-b「不得有裸 keyboard 段直构」应报错
    bad_b2 = src.replace(
        anchor,
        anchor + "\n        seg += _QQSegment.keyboard(kb_model)", 1)
    try:
        _guard_official_build(bad_b2)
        raise AssertionError("守门②-b未报错（裸 keyboard 段直构没被拦）")
    except AssertionError as e:
        assert "裸 keyboard" in str(e), "守门②-b报错位置不对：%s" % e

    # 注入 C：删掉 doupo 的 MessageEvent 注解（官方全量消息会刷 ERROR）
    dp = _DOUPO.read_text(encoding="utf-8")
    bad_dp = dp.replace("async def _r(event: MessageEvent) -> bool:",
                        "async def _r(event) -> bool:", 1)
    assert bad_dp != dp, "注入 C 没找到锚点"
    try:
        _guard_rule_typed_event(bad_dp, "doupo")
        raise AssertionError("守门③未报错（删注解导致官方事件刷 ERROR 没被拦）")
    except AssertionError as e:
        assert "MessageEvent" in str(e), "守门③报错位置不对：%s" % e
    _guard_rule_typed_event(dp, "doupo")  # 恢复后必须过


if __name__ == "__main__":
    import traceback
    tests = [
        test_button_segment_has_markdown_then_keyboard,
        test_button_markdown_text_is_valid,
        test_official_build_wires_markdown_keyboard,
        test_doupo_xiuxian_rule_typed_event,
        test_official_payload_extract_markdown_keyboard,
        test_injection_guards,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print("PASS  %s" % t.__name__)
            passed += 1
        except Exception as e:
            print("FAIL  %s -> %s: %s" % (t.__name__, type(e).__name__, e))
            traceback.print_exc()
            failed += 1
    print("\n==== 结果: %d PASS / %d FAIL ====" % (passed, failed))
    sys.exit(1 if failed else 0)
