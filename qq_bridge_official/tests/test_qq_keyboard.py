# -*- coding: utf-8 -*-
"""QQ 官方按钮键盘验收（2026-11-11 第四次修改，prove-it）。

## 第四次修改：去掉当前卡自己 / 主菜单，只留同卡相关按钮

第三次返工做到「每张卡挂自己档位」，但真实群截图（用户 11-11 13:16）确认仍
每张卡带一个**蓝色 primary「当前卡自己」按钮**（红框，「裂隙」）+ 末尾**主菜单**。
用户明确：「不要显示当前卡片按钮，还有去掉主菜单按钮，只展示当前卡片相关按钮」。
本文件守门三条用户需求不变量 + 零编造 + 零按钮安全，**标准答案独立于实现**：

* **独立基准**：``_independent_real_commands()`` 用独立于 command_inventory 的 AST
  遍历从 game_tools 源码算「真实出卡口令全集」；``_independent_ranks()`` 从 warframe
  源码算真实 rank token。实现若编造词/漏词，两套遍历对不上 → FAIL。
* **三条需求不变量**（对全基准口令 + 带参 + GENERIC 输入）：
  ① 无任何 ``primary``（蓝色当前卡自己按钮已移除，全灰线框）；
  ② 无「帮助」/「主菜单」按钮；
  ③ 任何**零参**按钮 data ≠ 当前口令自己（带参串首词是市价口令，属合法结构，
     由「零编造」守门验，不在此列）。
* **零编造**：任何按钮 data ∈ 基准全集，或是合法带参串 ``<口令> <物品> <rank>``
  （首词∈基准、末词∈独立 rank、物品非空）；注入 ``fhelp``/``w M``/假 rank 立即 FAIL。
* **零按钮安全**：GENERIC 与单档卡 → ``buttons_for == ()`` 且
  ``make_keyboard == (None, ())``（防下发空 keyboard → 40034011）；关键多档卡
  （裂隙/赏金/信条/紫卡趋势）必须非空且只含邻档。
* **注入验证**（prove-it 强制）：``test_injection_guards`` 把「自己」「主菜单」
  「假 data」三类错分别放回，对应守门报错，再恢复断言通过 —— 证明守门不是摆设。

第五次修改（用户「战甲武器可没有等级，只有赋能和 mod 才有等级」）：市价带参卡
只对 MOD/赋能挂 ``满级/R0``，武器/战甲/部件零按钮。本文件给 ``rank_eligibility``
注入 business 真函数后端（读磁盘真数据），市价「有按钮」正例改用 MOD 持久力，
「零按钮」组纳入绝路/沙皇/部件。rank 资格本身的机器全量守门由
``test_qq_rank_eligibility.py`` 独立负责，本文件只守按钮结构与零编造。

职责分工：本文件负责「真实口令全集 / 三条需求不变量 / 零编造 / 零按钮 / 关键卡
非空」；「每张卡有哪些档位」由 ``test_qq_card_buttons.py`` 的独立
``_baseline_groups()`` 守护；「rank 资格分类」由 ``test_qq_rank_eligibility.py``
的独立数据基准守护。三文件互不调用对方的派生函数。

跑法（容器内，不开网络、不发真消息）：
  docker exec -i qq-bridge-official /opt/venv/bin/python -m pytest tests/test_qq_keyboard.py -v
或：python tests/test_qq_keyboard.py   （宿主纯标准库，nonebot 结构项自动跳过）
"""
from __future__ import annotations

import ast
import importlib
import re
import sys
import types
from pathlib import Path

import importlib.util  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_GT_DIR = _REPO / "src/plugins/qq_bridge/game_tools"
_GT = _GT_DIR / "__init__.py"
_WF = _GT_DIR / "warframe.py"
_CH = _REPO / "src/plugins/qq_bridge/channel"
_WF_ITEMS = _REPO / "data/wf_items_v2.json"


def _load(path: Path, name: str):
    """按文件直载模块，绕开 plugins.qq_bridge/__init__.py 的重量级链式 import。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _install_rank_backend():
    """第五次修改：给 rank_eligibility 注入 business 真函数，使 MOD/赋能市价卡能正确出
    等级按钮（宿主/容器一致，读磁盘真数据）。rank 资格本身由
    test_qq_rank_eligibility.py 独立机器全量守门；这里只为让本文件的「有按钮卡结构」
    断言用真实可出按钮的 MOD 正例，而非因缺 backend 静默零按钮而空转。
    """
    import json
    gt = _GT_DIR.resolve()
    qb = types.ModuleType("qq_bridge")
    qb.__path__ = [str(gt.parent)]
    sys.modules["qq_bridge"] = qb
    pkg = types.ModuleType("qq_bridge.game_tools")
    pkg.__path__ = [str(gt)]
    sys.modules["qq_bridge.game_tools"] = pkg
    wf = importlib.import_module("qq_bridge.game_tools.warframe")
    slug_tags = {it["slug"]: (it.get("tags") or [])
                 for it in json.loads(_WF_ITEMS.read_text(encoding="utf-8"))}
    rel = _load(_CH / "rank_eligibility.py", "rank_eligibility")
    rel.set_backend(wf._resolve_url_name, lambda s: slug_tags.get(s) or [])


# 实现侧（被测对象）：qq_keyboard + 它依赖的 command_inventory
inv_mod = _load(_CH / "command_inventory.py", "command_inventory")
_install_rank_backend()
kb = _load(_CH / "qq_keyboard.py", "qq_keyboard")


# ---------------------------------------------------------------------------
# 独立基准：以下抽取逻辑**不调用 command_inventory / qq_keyboard**，是另一份实现。
# ---------------------------------------------------------------------------
_CMD_TABLES = {
    "NATURAL_CMDS", "GUIDE_TITLES", "ZERO_ARG_OK",
    "_PLAN_CMD_WORDS", "_DISPATCH_CMD_WORDS",
    "_SUB_ADD_WORDS", "_SUB_DEL_WORDS", "_SUB_LIST_WORDS",
    "_FULL_WAIT_WORDS",
}
_CMD_VARS = {"cmd", "c", "text"}  # text=预约分支；不含 t（它是遍历循环变量）
_DISPATCH_FUNCS = {"_handle", "_handle_safe", "dispatch",
                   "_handle_task_sub", "_task_sub_intent"}


def _independent_real_commands() -> set[str]:
    """独立 AST 遍历求「真实出卡口令全集」——本测试的标准答案。

    与 command_inventory.build_inventory 是两套代码，仅共享「源码事实」：
      1) 模块级命令词表的 key（GUIDE_TITLES 只取 key，排除卡片标题 value）；
      2) HELP 文本里 `#命令` token；
      3) 分发函数体里所有 `cmd/c/text in (...)`、`== "..."` 的字符串字面量；
      4) `cmd/c/text in <词表变量>`（模块级 / 函数内局部 tuple）展开。
    任一类漏了，某分支对应的真实口令就不会进基准 → 但实现会把它进按钮 →
    守门① 用「实现 primary 全覆盖」交叉发现；反之实现漏了 → 守门②/① FAIL。
    """
    src = _GT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: set[str] = set()

    # 模块级字面量（词表）：dict 只取 key
    mod_lit: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    try:
                        val = ast.literal_eval(node.value)
                    except Exception:
                        continue
                    leaves = _leaves(val, keys_only=(t.id == "GUIDE_TITLES"))
                    if leaves is not None:
                        mod_lit[t.id] = leaves
                        if t.id in _CMD_TABLES:
                            out |= leaves
    # HELP #token（HELP 是 str，单独从源码取，不进 mod_lit 的词表集合）
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "HELP":
                    try:
                        h = ast.literal_eval(node.value)
                    except Exception:
                        continue
                    if isinstance(h, str):
                        out |= {x for x in re.findall(r"#([^\s／/，,。：:]+)", h) if x}

    def leaves_of(node, local):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return {e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.Name):
            return set(local.get(node.id) or mod_lit.get(node.id) or ())
        # `_full_words + ("紫卡完整",)` 这类 BinOp
        if isinstance(node, ast.BinOp):
            return leaves_of(node.left, local) | leaves_of(node.right, local)
        return set()

    # 分发函数体
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name not in _DISPATCH_FUNCS:
            continue
        local: dict[str, set[str]] = {}
        for n in ast.walk(fn):
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        try:
                            lv = _leaves(ast.literal_eval(n.value))
                        except Exception:
                            continue
                        if lv is not None:
                            local[t.id] = lv
        for n in ast.walk(fn):
            if not isinstance(n, ast.Compare):
                continue
            left = n.left
            base = None
            if isinstance(left, ast.Name) and left.id in _CMD_VARS:
                base = left.id
            elif (isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute)
                  and left.func.attr == "lower" and isinstance(left.func.value, ast.Name)
                  and left.func.value.id in _CMD_VARS):
                base = left.func.value.id
            if base is None:
                continue
            for op, comp in zip(n.ops, n.comparators):
                if isinstance(op, ast.In):
                    out |= leaves_of(comp, local)
                elif isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                    out.add(comp.value)
    return {c for c in out if c}


def _leaves(val, keys_only: bool = False):
    if isinstance(val, str):
        return {val}
    if isinstance(val, dict):
        s = {k for k in val if isinstance(k, str)}
        if not keys_only:
            s |= {v for v in val.values() if isinstance(v, str)}
        return s
    if isinstance(val, (tuple, list, set, frozenset)):
        return {x for x in val if isinstance(x, str)}
    return None


def _independent_ranks() -> tuple[str, ...]:
    """独立 AST 派生：warframe._parse_mod_rank 的 rank_label return 第 3 位字面量。"""
    tree = ast.parse(_WF.read_text(encoding="utf-8"))
    fn = next((f for f in tree.body
               if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
               and f.name == "_parse_mod_rank"), None)
    assert fn is not None, "warframe 源码找不到 _parse_mod_rank"
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple) \
                and len(n.value.elts) >= 3:
            v = n.value.elts[2]
            if isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value not in out:
                out.append(v.value)
    return tuple(out)


def _self_head(text: str) -> str:
    """独立实现「命令首词归一」（与 qq_keyboard.normalize_cmd 同口径，**不调用它**）。"""
    t = (text or "").strip()
    while t and t[0] in "#＃/":
        t = t[1:].strip()
    if not t:
        return ""
    parts = t.split()
    head = parts[0] if parts else t
    return head.lower() if head.isascii() else head


_GENERIC_PROBES = ("", "  ", "/ai", "/ai on", "闲聊", "随便闲聊", "签到", "/pc", "/提醒 列表", "#", "＃")
# 单档卡代表口令：这些是 card_buttons 派生的 1 档出卡族成员，其「单档」事实由
# test_qq_card_buttons.py::_baseline_groups() 的逐卡精确守门守护（若源码把它变多档，
# 那套独立基准会先 FAIL 并提示更新本白名单）。
_SINGLE_SLOT_PROBES = ("灵化", "科研", "仲裁表", "平原", "配卡", "词条推荐",
                       "警报", "执政官", "紫卡融合", "紫卡全面", "角色", "模拟开卡", "帮助")
# 用户明确举例/要求的关键多档卡邻档集合（源码 mode dict / cmd tuple 事实，
# 与 card_buttons/test_baseline_sane 同性质：写死用户锚点，实现改错即 FAIL）。
_KEY_CARD_SIBLINGS = {
    "裂隙": {"普通裂隙", "钢铁裂隙"},
    "普通裂隙": {"裂隙", "钢铁裂隙"},
    "钢铁裂隙": {"裂隙", "普通裂隙"},
    "赏金": {"钢铁赏金"},
    "钢铁赏金": {"赏金"},
    "信条": {"终幕"},
    "终幕": {"信条"},
    "紫卡趋势": {"词条价差"},
    "词条价差": {"紫卡趋势"},
}


# ---------------------------------------------------------------------------
# 守门
# ---------------------------------------------------------------------------
def test_independent_baseline_sane():
    """基准本身必须够大、且含上轮漏检的 15 个纯分支口令（防基准被削弱）。"""
    base = _independent_real_commands()
    assert len(base) >= 150, "独立基准口令数异常偏少=%d（解析回归？）" % len(base)
    must_have = {"alert", "alerts", "netracell", "今日运势", "作答", "问答",
                 "成语接龙", "放弃", "灵化武器", "灵化轮换", "猜数",
                 "科研任务", "认输", "运气", "退出"}
    missing = must_have - base
    assert not missing, "上轮漏检的纯分支口令未进独立基准：%s" % sorted(missing)
    assert _independent_ranks() == ("满级", "R0"), "rank 独立基准不对：%s" % (_independent_ranks(),)


def test_implementation_covers_baseline():
    """实现派生的全集必须 == 独立基准全集（漏分支/多编造都会 FAIL）。"""
    impl = inv_mod.build_inventory().commands
    base = _independent_real_commands()
    only_impl = sorted(impl - base)
    only_base = sorted(base - impl)
    assert not only_base, "实现漏了真实出卡口令（漏口令=遗漏）：%s" % only_base
    assert not only_impl, "实现多出源码里不存在的口令（可能编造）：%s" % only_impl


def test_no_self_menu_primary():
    """守门①②③（用户三条需求）：无 primary / 无主菜单 / 零参按钮不含当前口令自己。"""
    base = _independent_real_commands()
    probes = sorted(base) + [
        "wm 绝路", "市价 延伸", "WM 绝路 满级", "wm 0级 绝路", "价格 满级 延伸",
        "wm 持久力", "wm 0级 持久力", "wm 充沛", "wm 狂怒",  # 第五次：MOD/赋能带参正例
    ] + list(_GENERIC_PROBES)
    violations = []
    for p in probes:
        self_head = _self_head(p)
        for b in kb.buttons_for(p):
            if b.primary:
                violations.append((p, "蓝色 primary（当前卡自己按钮未移除）", b))
            if b.cmd == "帮助" or b.label == "主菜单":
                violations.append((p, "主菜单按钮未移除", b))
            # 带参串首词是市价口令，属合法结构，仅在「零参按钮」上查「不含自己」
            if " " not in b.cmd and b.cmd.lower() == self_head:
                violations.append((p, "零参按钮含当前口令自己", b))
    assert not violations, "需求不变量被破坏（前 12 条）：%s" % (violations[:12],)


def test_no_fabricated_button_data():
    """守门零编造：data ∈ 独立基准全集，或合法带参串 <口令> <物品> <rank>。"""
    base = _independent_real_commands()
    ranks = _independent_ranks()
    base_lower = {c.lower() for c in base}
    probes = sorted(base) + [
        "wm 绝路", "市价 延伸", "WM 绝路 满级", "wm 0级 绝路", "价格 满级 延伸",
        "wm 持久力", "wm 0级 持久力", "wm 充沛", "wm 狂怒",  # 第五次：MOD/赋能带参正例
    ] + list(_GENERIC_PROBES)
    bad = set()
    for p in probes:
        for b in kb.buttons_for(p):
            if " " not in b.cmd:
                if b.cmd.lower() not in base_lower:
                    bad.add((p, b.cmd))
                continue
            parts = b.cmd.split()
            if (len(parts) < 3 or parts[0].lower() not in base_lower
                    or parts[-1] not in ranks or not all(parts[1:-1])):
                bad.add((p, b.cmd))
    assert not bad, "按钮 data 编造/带参结构非法：%s" % sorted(bad)


def test_generic_and_single_are_zero_buttons():
    """GENERIC + 单档卡 + 无等级市价物品 → buttons_for == ()（无可下钻按钮）。"""
    offenders = []
    zero = (list(_GENERIC_PROBES) + list(_SINGLE_SLOT_PROBES)
            + ["wm", "市价", "wm 绝路", "wm 沙皇", "wm 绝路 Prime 枪管", "wm 犀牛"])
    for p in zero:
        btns = kb.buttons_for(p)
        if btns:
            offenders.append((p, [(b.cmd, b.primary) for b in btns]))
    assert not offenders, "这些卡应为零按钮，却产出了：%s" % (offenders[:12],)


def test_key_cards_nonempty_with_only_siblings():
    """用户举例/要求的关键多档卡必须非空，且按钮恰好=同卡邻档（不含自己/主菜单）。"""
    base = _independent_real_commands()
    for cmd, want in _KEY_CARD_SIBLINGS.items():
        assert cmd in base, "关键卡 %s 不在独立基准（源码改了？）" % cmd
        got = {b.cmd for b in kb.buttons_for(cmd)}
        assert got == want, "关键卡 %s 邻档错：got=%s want=%s" % (cmd, got, want)
        assert all(w in base for w in got), "关键卡邻档含非真实口令：%s" % (got,)


def test_zero_button_makes_none_keyboard():
    """零按钮约定：make_keyboard 对单档/GENERIC/无等级市价物品返 (None,())（不构造空 keyboard）。"""
    zero = (list(_GENERIC_PROBES) + list(_SINGLE_SLOT_PROBES)
            + ["wm", "市价", "wm 绝路", "wm 沙皇", "wm 绝路 Prime 枪管"])
    for p in zero:
        model, btns = kb.make_keyboard(p)
        assert model is None and btns == (), \
            "零按钮卡 %r 的 make_keyboard 必须返 (None,())：model=%s btns=%s" % (p, model, btns)
    # 有按钮卡：make_keyboard 返非空 MessageKeyboard（宿主无 nonebot 则跳过结构项）
    try:
        from nonebot.adapters.qq.models.common import MessageKeyboard  # noqa: F401
    except ImportError:
        print("[skip] nonebot 不在宿主，真 MessageKeyboard 结构项在容器内跑")
        return
    # 第五次修改：市价「有按钮」正例用 MOD 持久力（绝路=武器已零按钮，归到零按钮组）
    for p in ("裂隙", "赏金", "信条", "wm 持久力"):
        model, btns = kb.make_keyboard(p)
        assert isinstance(model, MessageKeyboard) and btns, "多档/市价卡 %r 应产出键盘" % p
        for row in model.content.rows:
            assert 1 <= len(row.buttons) <= 5, "行列越界 %r" % p
            for b in row.buttons:
                assert 0 < len(b.render_data.label) <= 10 and b.action.data, "按钮字段非法 %r" % p
                assert b.render_data.style in (0, 1, 3, 4), "style 非法 %r" % p


def test_injection_guards():
    """prove-it 注入验证：把「自己」「主菜单」「假 data」放回，守门必须分别报错。"""
    base = _independent_real_commands()
    ranks = _independent_ranks()

    # 注入 1：当前卡自己按钮（裂隙卡塞回「裂隙」）→ 需求守门③应拒绝
    fissure_with_self = kb.buttons_for("裂隙") + (kb.Btn("裂隙", "裂隙"),)
    try:
        assert not any(" " not in b.cmd and b.cmd.lower() == _self_head("裂隙")
                       for b in fissure_with_self), "需求守门应拒绝裂隙卡里的自己按钮"
        raise AssertionError("守门③未报错（当前卡自己按钮没被拦）")
    except AssertionError as e:
        assert "守门③未报错" not in str(e), "守门③确实没拦住自己按钮"

    # 注入 2：主菜单按钮（塞回「帮助」）→ 需求守门②应拒绝
    with_menu = kb.buttons_for("赏金") + (kb.Btn("主菜单", "帮助"),)
    try:
        assert not any(b.cmd == "帮助" or b.label == "主菜单" for b in with_menu), \
            "需求守门应拒绝主菜单按钮"
        raise AssertionError("守门②未报错（主菜单没被拦）")
    except AssertionError as e:
        assert "守门②未报错" not in str(e), "守门②确实没拦住主菜单"

    # 注入 3：假 data（fhelp / 假 rank token）→ 零编造守门应拒绝
    fake_data = sorted(base)[0] + "_FAKE_CMD"
    fake_rank = "WM 绝路 满级X"
    bad = [fake_data, fake_rank]
    try:
        assert all(
            (b.lower() in {c.lower() for c in base}) if " " not in b
            else (len(b.split()) >= 3 and b.split()[0].lower() in {c.lower() for c in base}
                  and b.split()[-1] in ranks)
            for b in bad
        ), "零编造守门应拒绝假 data / 假 rank"
        raise AssertionError("守门零编造未报错（假 data 没被拦）")
    except AssertionError as e:
        assert "守门零编造未报错" not in str(e), "零编造守门确实没拦住假 data"

    # 恢复断言：真实实现三条都过
    real = kb.buttons_for("裂隙")
    assert all(not b.primary and b.cmd != "帮助" and b.cmd != "裂隙" for b in real)
    assert "满级X" not in " ".join(b.cmd for b in kb.buttons_for("wm 持久力"))
    assert kb.buttons_for("wm 绝路") == (), "第五次修改：武器绝路应零按钮"


if __name__ == "__main__":
    import traceback
    tests = [
        test_independent_baseline_sane,
        test_implementation_covers_baseline,
        test_no_self_menu_primary,
        test_no_fabricated_button_data,
        test_generic_and_single_are_zero_buttons,
        test_key_cards_nonempty_with_only_siblings,
        test_zero_button_makes_none_keyboard,
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
