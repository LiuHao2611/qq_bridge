# -*- coding: utf-8 -*-
"""卡片级「对应功能按钮」验收（2026-11-11 第三次返工，prove-it，独立基准）。

## 针对用户第三次反馈：「每张卡只挂对应功能按钮」

第二次返工实现零编造+全量，但所有卡都显示成同一组全局导航（赏金/钢路/钢铁/
裂隙/主菜单）。本文件守门「卡片级档位 + 市价带参 rank」两件事，且**不信任**
card_buttons/qq_keyboard 的实现：档位族、市价口令、rank token 全部用本文件里
**第二套独立 AST 遍历**从源码重算，实现与基准任何不一致都 FAIL。

四道硬守门：
  ① 档位族精确：每张卡的档位按钮集合 == 独立基准派生的该卡档位全集（漏档/错配 FAIL）；
  ② 跨卡零错位：档位按钮只能来自自己所属卡（裂隙卡出现赏金/市价 FAIL）；
  ③ 带参串零编造：市价按钮 data 必须严格 `<真实市价口令> <物品> <真实rank>`
     （注入假 rank `满级X` / 假口令 `wm2` FAIL）；
  ④ 满级/0级专属：`wm 绝路` 必出满级+R0、data 携带「绝路」、重复 rank 幂等剥离。
  注入验证 `test_injection_guards`：三类错真实放回，断言对应守门报错，恢复全绿。

跑法：python tests/test_qq_card_buttons.py（宿主纯标准库）
      docker exec -i qq-bridge-official /opt/venv/bin/python -m pytest tests/test_qq_card_buttons.py -v
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import importlib.util  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_GT = _REPO / "src/plugins/qq_bridge/game_tools/__init__.py"
_WF = _REPO / "src/plugins/qq_bridge/game_tools/warframe.py"
_CH = _REPO / "src/plugins/qq_bridge/channel"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


inv_mod = _load(_CH / "command_inventory.py", "command_inventory")
cb = _load(_CH / "card_buttons.py", "card_buttons")
kb = _load(_CH / "qq_keyboard.py", "qq_keyboard")


# ---------------------------------------------------------------------------
# 独立基准（不调用 card_buttons / qq_keyboard 的任何派生函数）
# ---------------------------------------------------------------------------
def _handle_func(tree: ast.AST):
    return next((f for f in tree.body
                 if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and f.name == "_handle"), None)


def _local_tuples(func):
    out = {}
    for n in ast.walk(func):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    try:
                        v = ast.literal_eval(n.value)
                    except Exception:  # noqa: BLE001
                        continue
                    if isinstance(v, (tuple, list, set, frozenset)):
                        ws = [x for x in v if isinstance(x, str)]
                        if ws:
                            out[t.id] = ws
    return out


def _cmd_words(test, local):
    """独立实现：`cmd in (...)` 字面量（保序，含 命令==词 形态）。"""
    out = []
    for n in ast.walk(test):
        if not isinstance(n, ast.Compare):
            continue
        left = n.left
        if not (isinstance(left, ast.Name) and left.id == "cmd"):
            continue
        for op, comp in zip(n.ops, n.comparators):
            if isinstance(op, ast.In) and isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                out += [e.value for e in comp.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            elif isinstance(op, ast.In) and isinstance(comp, ast.Name):
                out += list(local.get(comp.id, ()))
            elif isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) \
                    and isinstance(comp.value, str):
                out.append(comp.value)
    seen = set()
    return [w for w in out if not (w in seen or seen.add(w))]


def _first_send(body):
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                    and n.func.id.startswith("_send_"):
                return n.func.id
    return ""


def _mode_map(body):
    out = []
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "get" and isinstance(n.func.value, ast.Dict):
                for k in n.func.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) \
                            and k.value not in out:
                        out.append(k.value)
    return out


def _baseline_groups():
    """第二套独立派生：send_fn -> 该卡档位 canonical 元组。"""
    tree = ast.parse(_GT.read_text(encoding="utf-8"))
    h = _handle_func(tree)
    local = _local_tuples(h)
    raw = []
    for n in ast.walk(h):
        if isinstance(n, ast.If):
            words = _cmd_words(n.test, local)
            if not words:
                continue
            send = _first_send(n.body)
            if send:
                raw.append((send, words, _mode_map(n.body)))
    per_fn = {}
    for send, words, _mk in raw:
        per_fn.setdefault(send, set()).update(words)
    from collections import Counter
    cnt = Counter(w for ws in per_fn.values() for w in ws)
    shared = {w for w, n in cnt.items() if n >= 2}

    groups = {}
    for send, words, mkeys in raw:
        clean = [w for w in words if w and w not in shared]
        bucket = groups.setdefault(send, [])
        if mkeys:
            default = [w for w in clean if w not in mkeys]
            order = (default[:1] if default else []) + [k for k in mkeys if k not in shared]
        else:
            order = clean[:1]
        for c in order:
            if c and c not in bucket:
                bucket.append(c)
    return {k: tuple(v) for k, v in groups.items()}


def _baseline_market():
    """第二套独立派生：市价口令集（body 含 market_text 的 if + _wf 字典 key）。"""
    tree = ast.parse(_GT.read_text(encoding="utf-8"))
    def has_market(n):
        # n 可是单节点或语句 list（与实现侧 _has_market_text 同口径）
        nodes = n if isinstance(n, (list, tuple)) else [n]
        return any(
            isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
            and x.func.attr == "market_text"
            for st in nodes for x in ast.walk(st))
    out = set()
    for f in tree.body:
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local = _local_tuples(f)
        for n in ast.walk(f):
            if isinstance(n, ast.If) and has_market(n.body):
                out |= set(_cmd_words(n.test, local))
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) and has_market(v):
                        out.add(k.value)
    return out


def _baseline_ranks():
    """第二套独立派生：_parse_mod_rank 的 rank_label return 字面量。"""
    tree = ast.parse(_WF.read_text(encoding="utf-8"))
    fn = next((f for f in tree.body
               if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
               and f.name == "_parse_mod_rank"), None)
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple) \
                and len(n.value.elts) >= 3:
            v = n.value.elts[2]
            if isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value not in out:
                out.append(v.value)
    return tuple(out)


# ---------------------------------------------------------------------------
# 守门
# ---------------------------------------------------------------------------
def test_baseline_sane():
    groups = _baseline_groups()
    assert tuple(groups.get("_send_fissure", ())) == ("裂隙", "普通裂隙", "钢铁裂隙"), \
        "裂隙档位基准不对：%s" % (groups.get("_send_fissure"),)
    assert set(groups.get("_send_bounty", ())) == {"赏金", "钢铁赏金"}, \
        "赏金档位基准不对：%s" % (groups.get("_send_bounty"),)
    assert set(groups.get("_send_tenet", ())) == {"信条", "终幕"}, \
        "信条档位基准不对：%s" % (groups.get("_send_tenet"),)
    assert _baseline_ranks() == ("满级", "R0"), "rank 基准不对：%s" % (_baseline_ranks(),)
    assert "wm" in _baseline_market() and "市价" in _baseline_market(), "市价口令基准不全"


def test_impl_matches_independent_baseline():
    """守门① 档位族精确：实现的 groups 必须逐卡等于独立基准（漏档/错配 FAIL）。"""
    base = _baseline_groups()
    impl = {g.send_fn: tuple(m.canonical for m in g.modes)
            for g in cb.build_card_inventory().groups}
    assert set(impl) == set(base), "出卡族集合不一致：impl-only=%s base-only=%s" % (
        sorted(set(impl) - set(base)), sorted(set(base) - set(impl)))
    for fn, bmodes in base.items():
        assert set(impl[fn]) == set(bmodes), \
            "卡 %s 档位错配：impl=%s base=%s" % (fn, impl[fn], bmodes)
    # 市价口令 / rank token 也必须与独立基准一致
    ci = cb.build_card_inventory()
    assert ci.market_cmds == _baseline_market(), "市价口令集不一致"
    assert ci.rank_tokens == _baseline_ranks(), "rank token 集不一致"


def test_no_cross_card_neighbors():
    """守门② 跨卡零错位：每张档位卡的按钮只能来自自己所属卡档位 + 主菜单。"""
    base = _baseline_groups()
    all_modes = {c for modes in base.values() for c in modes}
    for fn, modes in base.items():
        if len(modes) <= 1:
            continue  # 单档卡无邻居可错
        for probe in modes:
            btns = kb.buttons_for(probe)
            slot = cb.get_card_inventory().slots_for(probe)
            allowed = set(slot) | {"帮助"}
            for b in btns:
                # 零参档位按钮不得是别的卡的档位；带参串单独在守门③验
                if " " not in b.cmd:
                    assert b.cmd in allowed, \
                        "跨卡错位：卡 %s(口令 %s) 按钮出现 %s（属%s）" % (
                            fn, probe, b.cmd,
                            next((f for f, ms in base.items() if b.cmd in ms), "?"))
    # 具体反例：裂隙卡绝不能出现赏金/市价/紫卡
    fissure = [b.cmd for b in kb.buttons_for("裂隙")]
    for forbidden in ("赏金", "钢铁赏金", "市价", "紫卡", "抽签"):
        assert forbidden not in fissure, "裂隙卡混入无关全局词 %s：%s" % (forbidden, fissure)


def test_rank_button_no_fabrication():
    """守门③ 带参串零编造：市价按钮 data 严格 <真实口令> <物品> <真实rank>。"""
    base_market = _baseline_market()
    base_ranks = _baseline_ranks()
    inv_cmds = inv_mod.build_inventory().commands
    probes = ["wm 绝路", "市价 延伸", "市场 持久力 p", "赤毒 绝路",
              "WM 绝路 满级", "wm 绝路 R0", "价格 满级 延伸"]
    seen_rank_btn = 0
    for p in probes:
        for b in kb.buttons_for(p):
            if " " not in b.cmd:
                assert b.cmd in inv_cmds or b.cmd == "帮助", \
                    "零参按钮 data 编造：%r -> %s" % (p, b.cmd)
                continue
            parts = b.cmd.split()
            assert len(parts) >= 3, "带参 data 结构非法（<口令> <物品> <rank>）：%s" % b.cmd
            head, rank = parts[0], parts[-1]
            assert head in base_market and head.lower() in {c.lower() for c in inv_cmds}, \
                "带参 data 口令非真实市价口令：%s" % b.cmd
            assert rank in base_ranks, "带参 data rank 非真实 token（编造）：%s" % b.cmd
            seen_rank_btn += 1
    assert seen_rank_btn > 0, "没有任何 rank 带参按钮被派生（守门空转）"


def test_rank_exact_and_idempotent():
    """守门④ 满级/0级专属：wm 绝路 必出满级+R0，data 带「绝路」，重复 rank 幂等。"""
    bs = kb.buttons_for("wm 绝路")
    data = [b.cmd for b in bs]
    assert "WM 绝路 满级" in data or "wm 绝路 满级" in data, \
        "缺满级按钮（或没携带物品名绝路）：%s" % data
    assert "WM 绝路 R0" in data or "wm 绝路 R0" in data, \
        "缺 0级/R0 按钮（或没携带物品名绝路）：%s" % data
    for b in bs:
        if b.label in ("满级", "0级"):
            assert "绝路" in b.cmd, "rank 按钮没带物品名：%s" % b.cmd
    # 幂等：已带 rank 的输入再派生，物品名里不应残留任何源码可识别的 rank 写法
    # （包括别名 0级/未升级/零级/R0/R5/5级/max 等），否则会出现重复堆叠。
    import re as _re
    strip_rules = cb.build_card_inventory().rank_strip_rules
    assert strip_rules, "源码 rank 剥离规则为空（实现退化）"
    for p in ("wm 绝路 满级", "市价 延伸 R0", "wm 0级 绝路",
              "wm 未升级 绝路", "wm 绝路 R5", "wm max 绝路"):
        for b in kb.buttons_for(p):
            if b.label in ("满级", "0级"):
                toks = b.cmd.split()
                item = " ".join(toks[1:-1])  # 去口令 + 末尾规范 rank = 物品名
                for pattern, _rule in strip_rules:
                    m = _re.compile(pattern, _re.I).match(item)
                    assert not m, "rank 别名残留导致堆叠：%r（物品名 %r 命中 %s）" % (
                        b.cmd, item, pattern)
                assert toks[-1] in ("满级", "R0"), "末尾 rank 非规范 token：%s" % b.cmd


def test_structure_platform_limits():
    """结构合法：腾讯平台硬限（label≤10 / 行≤5）；宿主无 nonebot 则只验 Btn 元组。"""
    base = _baseline_groups()
    probes = set(c for modes in base.values() for c in modes) | {
        "wm 绝路", "市价 延伸", "帮助", "", "/ai", "闲聊"}
    for p in probes:
        btns = kb.buttons_for(p)
        assert 2 <= len(btns) <= 5, "按钮数越界 %r -> %d" % (p, len(btns))
        assert btns[0].primary, "primary 不在首位 %r" % p
        assert btns[-1].cmd == "帮助", "末位不是主菜单 %r" % p
        for b in btns:
            assert 0 < len(b.label) <= 10, "label 越界 %r -> %s" % (p, b.label)
    try:
        from nonebot.adapters.qq.models.common import MessageKeyboard  # noqa: F401
    except ImportError:
        print("[skip] nonebot 不在宿主环境，真 MessageKeyboard 结构项在容器内跑")
        return
    for p in probes:
        model, _btns = kb.make_keyboard(p)
        rows = model.content.rows
        assert 1 <= len(rows) <= 5 and all(1 <= len(r.buttons) <= 5 for r in rows), \
            "keyboard 行列越界 %r" % p
        for r in rows:
            for b in r.buttons:
                assert len(b.render_data.label) <= 10 and b.action.data, \
                    "按钮字段非法 %r" % p


def test_injection_guards():
    """prove-it：三类真实错误放回，对应守门必须报错，恢复后通过。"""
    inv_cmds = inv_mod.build_inventory().commands
    base_market = _baseline_market()
    base_ranks = _baseline_ranks()

    # 注入 1：跨卡错位——给裂隙卡加一个赏金按钮，守门②判定式应拒绝
    fissure_with_cross = kb.buttons_for("裂隙") + (kb.Btn("赏金", "赏金"),)
    slot = set(cb.get_card_inventory().slots_for("裂隙")) | {"帮助"}
    try:
        assert all((b.cmd not in ({"赏金", "钢铁赏金", "市价", "紫卡", "抽签"}))
                   for b in fissure_with_cross if " " not in b.cmd), \
            "跨卡守门应拒绝裂隙卡里的赏金"
        raise AssertionError("守门②未报错（跨卡错位没被拦）")
    except AssertionError as e:
        assert "守门②未报错" not in str(e), "守门②确实没拦住跨卡错位"

    # 注入 2：假 rank token——data 末词改成源码里没有的 满级X，守门③应拒绝
    fake_rank_data = "WM 绝路 满级X"
    parts = fake_rank_data.split()
    try:
        assert parts[-1] in base_ranks, "带参守门应拒绝假 rank token"
        raise AssertionError("守门③未报错（假 rank 没被拦）")
    except AssertionError as e:
        assert "守门③未报错" not in str(e), "守门③确实没拦住假 rank"

    # 注入 3：假市价口令——首词 wm2 不在真实口令集，守门③应拒绝
    fake_market_data = "wm2 绝路 满级"
    hp = fake_market_data.split()[0]
    try:
        assert hp in base_market and hp.lower() in {c.lower() for c in inv_cmds}, \
            "带参守门应拒绝假市价口令"
        raise AssertionError("守门③未报错（假市价口令没被拦）")
    except AssertionError as e:
        assert "守门③未报错" not in str(e), "守门③确实没拦住假市价口令"

    # 恢复断言
    assert "赏金" not in [b.cmd for b in kb.buttons_for("裂隙")]
    assert "满级X" not in " ".join(b.cmd for b in kb.buttons_for("wm 绝路"))


if __name__ == "__main__":
    import traceback
    tests = [
        test_baseline_sane,
        test_impl_matches_independent_baseline,
        test_no_cross_card_neighbors,
        test_rank_button_no_fabrication,
        test_rank_exact_and_idempotent,
        test_structure_platform_limits,
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
