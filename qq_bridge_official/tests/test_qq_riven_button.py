# -*- coding: utf-8 -*-
"""紫卡分析结果图「全面分析」单按钮验收（2026-11-11 第六次修改，prove-it，独立基准）。

## 需求（用户原话）

「紫卡分析增加一个按钮，全面分析」+ 两项确认：
  * 点「全面分析」按钮 → 回「已登记，请再发一张紫卡截图」→ 用户再发图 →
    出 **655×1920 紫卡全面分析卡**（复用既有 ``_send_riven_full_parsed``）；
  * 该全面分析卡**不附带按钮**（零按钮）；
  * 按钮**只挂在紫卡分析（riven_analysis）结果图**这一张卡上。

## 为什么用哨兵键而不是真实口令分支

QQ 按钮回调 ``button_data`` 只能带回一个口令字符串，且伪事件**不带原图**——
所以点按钮不可能「一键复用刚才那张图出完整卡」，只能走「预约 → 再发图」的既有链路。
要区分「紫卡分析**结果图**该挂按钮」与「普通文本『全面分析』/『紫卡分析』不该
自挂按钮」，用一个**内部哨兵** ``__riven_analysis__`` 作 ``buttons_for`` 入参键：
只有 game_tools ``_send_guide`` 的 riven_analysis 专属模板分支往局部 ctx 写它，
经 official.py::send 的 ``btn_cmd`` 兜底链读到 → 命中专用分支 → 挂单个「全面分析」
按钮。Btn.cmd 仍是 **inventory 真实口令「全面分析」**，点击回传走既有预约。

## 本文件守门（不信任生产实现，第二套独立基准 + AST + 注入）

  ① 哨兵分支：``buttons_for(哨兵)`` == 恰好 1 个按钮，label/cmd=「全面分析」、
     primary=False、cmd ∈ inventory.commands（零编造）；
  ② 不自挂：``buttons_for("全面分析")`` / ``("紫卡分析")`` / 空 → 全部 ``()``；
  ③ 不污染既有：档位族（裂隙/赏金/信条）、市价（绝路零 / 持久力满级）、
     rank、裸口令，结果与第六次修改前一致；
  ④ 源码接线 AST：`_send_guide` 的 riven_analysis(card_data) 分支**确实**写
     ``ctx["keyboard_cmd"]=哨兵``；`_send_riven_full_parsed` **不含**该写入
     （→ 全面分析卡零按钮的静态保证）；
  ⑤ ctx 合并隔离链：独立复刻 official.py::send 的
     ``merged=dict(full); merged.update(ctx)`` + btn_cmd 兜底，断言
     紫卡分析图读到哨兵、全面分析图读空（互不串号）；
  ⑥ 注入验证：把哨兵分支改空 / 把哨兵写进全面分析分支 / 让 Btn.cmd 造假口令，
     守门必须 FAIL —— 证明不是摆设。

跑法：python tests/test_qq_riven_button.py（宿主纯标准库）
      docker exec -i qq-bridge-official /opt/venv/bin/python -m pytest tests/test_qq_riven_button.py -v
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path

import importlib.util as _ilu  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_ROOT = _REPO / "src/plugins/qq_bridge"
_QQ_KEYBOARD = _ROOT / "channel/qq_keyboard.py"
_GT = _ROOT / "game_tools/__init__.py"

# 与生产**独立**的哨兵期望值（故意字面量，不从生产常量 import，防自己考自己）
_SENTINEL = "__riven_analysis__"
_EXPECT_LABEL = "全面分析"
_EXPECT_CMD = "全面分析"


def _load(path: Path, name: str):
    spec = _ilu.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_keyboard():
    """按包上下文直载 qq_keyboard（相对 import 到同目录三模块）。"""
    root = _ROOT.resolve()
    qb = types.ModuleType("qbrb"); qb.__path__ = [str(root)]; sys.modules["qbrb"] = qb
    ch = types.ModuleType("qbrb.channel"); ch.__path__ = [str(root / "channel")]
    sys.modules["qbrb.channel"] = ch
    gt = types.ModuleType("qbrb.game_tools"); gt.__path__ = [str(root / "game_tools")]
    sys.modules["qbrb.game_tools"] = gt
    base = "qbrb.channel"
    _load(root / "channel/command_inventory.py", base + ".command_inventory")
    _load(root / "channel/card_buttons.py", base + ".card_buttons")
    # rank_eligibility 惰性导入 warframe 会在 can_have_rank 触发；注入恒 False 后端隔离
    rank = _load(root / "channel/rank_eligibility.py", base + ".rank_eligibility")
    rank.set_backend(lambda item: "slug_" + item, lambda slug: [])  # 全判无等级
    kb = _load(root / "channel/qq_keyboard.py", base + ".qq_keyboard")
    return kb


_KB = None


def kb():
    global _KB
    if _KB is None:
        _KB = _load_keyboard()
    return _KB


def _inv_commands():
    inv = sys.modules["qbrb.channel.command_inventory"].get_inventory()
    return set(inv.commands)


# ---------------------------------------------------------------------------
# ① 哨兵分支精确
# ---------------------------------------------------------------------------
def test_sentinel_gives_exactly_one_full_analysis_button():
    btns = kb().buttons_for(_SENTINEL)
    assert len(btns) == 1, "紫卡分析图必须恰好 1 个按钮，实得 %d" % len(btns)
    b = btns[0]
    assert b.label == _EXPECT_LABEL, (b.label, _EXPECT_LABEL)
    assert b.cmd == _EXPECT_CMD, (b.cmd, _EXPECT_CMD)
    assert b.primary is False, "按钮不得是 primary（用户已去掉当前卡蓝色按钮）"


def test_button_cmd_is_real_inventory_command_no_fabrication():
    btns = kb().buttons_for(_SENTINEL)
    assert btns, "哨兵分支不应为空"
    assert btns[0].cmd in _inv_commands(), (
        "Btn.cmd=%r 不是 inventory 真实口令（零编造不变量被破坏）" % btns[0].cmd)


# ---------------------------------------------------------------------------
# ② 不自挂：口令文本 / 空 一律零按钮
# ---------------------------------------------------------------------------
def test_plain_command_texts_do_not_self_attach():
    for text in (_EXPECT_CMD, "紫卡分析", "紫卡全面", "完整分析", "", "  "):
        assert kb().buttons_for(text) == (), (
            "文本 %r 不该自挂按钮（只有哨兵/档位/市价MOD才挂）" % text)


# ---------------------------------------------------------------------------
# ③ 不污染既有 53 守门的既有规则
# ---------------------------------------------------------------------------
def test_existing_rules_untouched():
    # 档位族（去自己后的邻档）
    assert [b.cmd for b in kb().buttons_for("裂隙")] == ["普通裂隙", "钢铁裂隙"]
    assert [b.cmd for b in kb().buttons_for("赏金")] == ["钢铁赏金"]
    assert [b.cmd for b in kb().buttons_for("信条")] == ["终幕"]
    # 市价：武器（本测试注入恒 False 后端）→ 零按钮；档位族口令裸用 → 零按钮
    assert kb().buttons_for("wm 绝路") == ()
    assert kb().buttons_for("市价 绝路") == ()
    # 非口令 / 斜杠 → 零按钮
    assert kb().buttons_for("随便聊两句") == ()
    assert kb().buttons_for("/help") == ()


def test_sentinel_is_not_treated_as_generic_or_family():
    # 哨兵必须独立命中，绝不能落进「档位族邻档」或 GENERIC 导航
    btns = kb().buttons_for(_SENTINEL)
    assert len(btns) == 1
    assert btns[0].cmd not in ("帮助", "主菜单", "赏金", "裂隙", "市价", "抽签")


# ---------------------------------------------------------------------------
# ④ 源码接线 AST（静态保证：挂对卡 + 全面分析卡零按钮）
# ---------------------------------------------------------------------------
def _fn(tree, name):
    return next((n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == name), None)


def test_ast_riven_analysis_branch_writes_sentinel():
    tree = ast.parse(_GT.read_text(encoding="utf-8"))
    send_guide = _fn(tree, "_send_guide")
    assert send_guide is not None, "_send_guide 不存在"
    # 定位 card_data 分支（if card_data:）
    cd_branch = None
    for n in ast.walk(send_guide):
        if isinstance(n, ast.If) and isinstance(n.test, ast.Name) \
                and n.test.id == "card_data":
            cd_branch = n
            break
    assert cd_branch is not None, "_send_guide 里找不到 if card_data 分支"
    # 该分支内必须存在 ctx["keyboard_cmd"] = <哨兵字面量>
    found = False
    for n in ast.walk(cd_branch):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Subscript):
            sub = n.targets[0]
            base = sub.value
            sl = sub.slice
            if isinstance(base, ast.Name) and base.id == "ctx" \
                    and isinstance(sl, ast.Constant) and sl.value == "keyboard_cmd" \
                    and isinstance(n.value, ast.Constant) and n.value.value == _SENTINEL:
                found = True
    assert found, "if card_data 分支未写入 ctx['keyboard_cmd']=%r（紫卡分析图将无按钮）" % _SENTINEL


def test_ast_full_analysis_card_does_not_write_sentinel():
    tree = ast.parse(_GT.read_text(encoding="utf-8"))
    fn = _fn(tree, "_send_riven_full_parsed")
    assert fn is not None, "_send_riven_full_parsed 不存在"
    src = ast.dump(fn)
    assert _SENTINEL not in src, (
        "全面分析卡 _send_riven_full_parsed 不得写哨兵，否则会错误挂按钮"
        "（用户明确：全面分析卡不附带按钮）")


def test_ast_only_one_writer_of_sentinel_in_whole_module():
    """全 game_tools/__init__.py 里哨兵字面量只能出现 1 次（写者唯一）。"""
    tree = ast.parse(_GT.read_text(encoding="utf-8"))
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value == _SENTINEL]
    assert len(hits) == 1, (
        "哨兵字面量应只写入 1 处（紫卡分析图分支），实得 %d 处" % len(hits))


# ---------------------------------------------------------------------------
# ⑤ ctx 合并隔离链（独立复刻 official.py::send，不 import nonebot）
# ---------------------------------------------------------------------------
def _btn_cmd_of(out_kb: str, full_ctx: dict, local_ctx: dict, out_text: str) -> str:
    merged = dict(full_ctx)
    merged.update(local_ctx)  # 与 official.py::send 同序：调用方显式字段优先
    return str(out_kb or merged.get("keyboard_cmd", "")
               or (out_text or "").strip().split("\n")[0])


def test_ctx_merge_isolation_riven_analysis_vs_full():
    full = {"keyboard_cmd": "", "group_key": "official:g:x"}  # 发图事件(纯图文本空)
    riven_analysis_ctx = {"channel": "official", "keyboard_cmd": _SENTINEL}
    riven_full_ctx = {"channel": "official"}  # 全面分析卡自造 ctx 不含哨兵
    c1 = _btn_cmd_of("", full, riven_analysis_ctx, "")
    c2 = _btn_cmd_of("", full, riven_full_ctx, "")
    assert c1 == _SENTINEL, "紫卡分析图没读到哨兵 → 按钮挂不上"
    assert c2 == "", "全面分析图错误继承哨兵 → 会误挂按钮"
    assert kb().buttons_for(c1), "紫卡分析图 btn_cmd 应命中哨兵分支"
    assert kb().buttons_for(c2) == (), "全面分析图应零按钮"


def test_media_layer_forwards_ctx_verbatim_sentinel_survives():
    """AST 守门：media 层把**原始 ctx 对象**逐字透传到 channel.send，不重建。

    ``send_pic(ctx, ...)`` 官方分支 → ``_send_via_channel(ctx, Outgoing(...))``
    → ``get_channel(ch).send(ctx, out)``：ctx 自始至终是同一个 dict，故
    ``ctx["keyboard_cmd"]`` 哨兵必然到达 official.py::send 的 ``merged.update(ctx)``。
    若哪天有人重构为 ``send(dict(ctx))`` 之外的「重建/白名单过滤 ctx」，本守门 FAIL。
    （不用重 stub 真跑：三行直传逐字可读，重 nonebot stub 反而让测试自身变脆。）
    """
    media = ast.parse((_ROOT / "media/__init__.py").read_text(encoding="utf-8"))
    fns = {n.name: n for n in media.body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    relay = fns.get("_send_via_channel")
    assert relay is not None, "_send_via_channel 不存在"
    # relay 内 get_channel(ch).send(ctx, out) 的第一个实参必须是形参 ctx（Name）
    send_calls = [n for n in ast.walk(relay)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "send"]
    assert send_calls, "_send_via_channel 未调用 channel.send"
    args = send_calls[0].args
    assert args and isinstance(args[0], ast.Name) and args[0].id == "ctx", (
        "channel.send 第一个实参不是原始 ctx（哨兵可能丢失）")

    send_pic = fns.get("send_pic")
    assert send_pic is not None, "send_pic 不存在"
    # send_pic 官方分支里 _send_via_channel(ctx, ...) 第一个实参须是形参 ctx
    relays = [n for n in ast.walk(send_pic)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "_send_via_channel"]
    assert relays, "send_pic 未调用 _send_via_channel"
    ra = relays[0].args
    assert ra and isinstance(ra[0], ast.Name) and ra[0].id == "ctx", (
        "send_pic 传给 _send_via_channel 的不是原始 ctx")


# ---------------------------------------------------------------------------
# ⑥ 注入验证：把实现改坏，守门必须抓到（prove-it）
# ---------------------------------------------------------------------------
def test_injection_guards():
    import textwrap
    src = _QQ_KEYBOARD.read_text(encoding="utf-8")

    # 注入 A：把哨兵分支的返回改成空 → 哨兵用例应失败
    bad_a = src.replace(
        "    if (text or \"\").strip() == _RIVEN_ANALYSIS_SENTINEL:\n"
        "        label, cmd = _RIVEN_FULL_BTN\n"
        "        return (Btn(label, cmd, False),)",
        "    if (text or \"\").strip() == _RIVEN_ANALYSIS_SENTINEL:\n"
        "        return ()")
    assert bad_a != src, "注入 A 未命中生产源码（守门探针失效）"
    # 注入副本必须与依赖模块同目录（channel/），否则 qq_keyboard 的
    # `except ImportError: from command_inventory import ...` 兜底找不到同目录模块。
    tmp = _QQ_KEYBOARD.parent / ".tmp_inject_qkb_a.py"
    tmp.write_text(bad_a, encoding="utf-8")
    try:
        # 用 qbrb.channel 包内名字加载 → 相对 import（.command_inventory 等）
        # 命中 sys.modules 里已注册的真依赖，无需兜底裸名 import。
        m = _load(tmp, "qbrb.channel.qkb_inject_a")
        try:
            btns = m.buttons_for(_SENTINEL)
            assert btns and btns[0].label == _EXPECT_LABEL, "注入 A 应让哨兵分支失效"
        except AssertionError:
            pass  # 守门抓到了坏实现（预期）
        else:
            raise AssertionError("注入 A : 哨兵分支被掏空却没被守门发现")
    finally:
        tmp.unlink(missing_ok=True)

    # 注入 B：源码里给全面分析卡也写哨兵 → AST 守门必须 FAIL
    gt_src = _GT.read_text(encoding="utf-8")
    marker = "async def _send_riven_full_parsed(bot: Bot, event: MessageEvent, parsed: dict) -> None:\n"
    assert marker in gt_src, "注入 B 锚点缺失"
    bad_b = gt_src.replace(marker, marker + '    ctx["keyboard_cmd"] = "%s"\n' % _SENTINEL, 1)
    tree_b = ast.parse(bad_b)
    fn_b = _fn(tree_b, "_send_riven_full_parsed")
    assert _SENTINEL in ast.dump(fn_b), "注入 B 应让全面分析卡带上哨兵"

    # 注入 C：让 Btn.cmd 造假口令 → 零编造守门必须 FAIL
    fake = src.replace('        return (Btn(label, cmd, False),)',
                       '        return (Btn(label, "全面分析X", False),)')
    assert fake != src, "注入 C 未命中"
    tmp2 = _QQ_KEYBOARD.parent / ".tmp_inject_qkb_c.py"
    tmp2.write_text(fake, encoding="utf-8")
    try:
        m2 = _load(tmp2, "qbrb.channel.qkb_inject_c")
        cmd = m2.buttons_for(_SENTINEL)[0].cmd
        assert cmd not in _inv_commands(), "注入 C : 假口令应不在 inventory"
    finally:
        tmp2.unlink(missing_ok=True)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print("PASS ", name)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL ", name, "->", type(e).__name__, e)
    print("\n==== 结果: %d PASS / %d FAIL ====" % (len(fns) - failed, failed))
    sys.exit(1 if failed else 0)
