# -*- coding: utf-8 -*-
"""卡片级「对应功能按钮」验收（2026-11-11 第四次修改，prove-it，独立基准）。

## 针对用户第三/第四次反馈：「每张卡只挂对应功能按钮」→「再去掉自己+主菜单」

第三次返工实现「每卡挂自己档位」，但真实群截图里每张卡仍带一个蓝色 primary
「当前卡自己」按钮（红框）+ 末尾主菜单。用户第四次明确：「不要显示当前卡片
按钮，还有去掉主菜单按钮，只展示当前卡片相关按钮」。本文件守门两件事，且
**不信任** card_buttons/qq_keyboard 的实现：档位族、市价口令、rank token 全部
用本文件里**第二套独立 AST 遍历**从源码重算，实现与基准任何不一致都 FAIL。

五道硬守门：
  ① 档位族精确：card_buttons 派生的每卡档位全集 == 独立基准（漏档/错配 FAIL）；
  ② 每卡按钮 == 该卡档位**去掉当前口令自己**（不多不少、无邻卡错位、无主菜单）；
  ③ 带参串零编造：市价按钮 data 严格 `<真实市价口令> <物品> <真实rank>`
     （注入假 rank `满级X` / 假口令 `wm2` FAIL）；
  ④ 满级/0级专属（第五次修改）：只有 MOD/赋能挂等级——`wm 持久力`(MOD) 出
     `满级/R0`、data 带物品名、重复 rank 幂等剥离；`wm 绝路`(武器) 零按钮；
  ⑤ 零按钮：单档卡 / GENERIC / 无等级市价物品 → buttons_for==() 且
     make_keyboard==(None,())。
  注入验证 `test_injection_guards`：把自己/主菜单/假 rank/假口令放回，断言守门
  报错，恢复全绿 —— 证明守门不是摆设。

第五次修改（用户「战甲武器可没有等级，只有赋能和 mod 才有等级」）：市价带参卡
不再对**所有**物品挂 ``满级/R0``，而是经 ``rank_eligibility`` 判物品类别资格——
只有 MOD / 赋能才挂（持久力/延伸/充沛/狂怒…），武器/战甲/部件不挂（绝路/沙皇…
→ 零按钮）。本文件的市价正例锚点相应从「绝路」（武器，现应为零按钮）改为
「持久力」（MOD）；rank 资格本身由 ``test_qq_rank_eligibility.py`` 独立机器全量守门，
本文件只守「按钮接线 + 档位族 + 带参零编造」，两文件互不调用对方派生函数。

跑法：python tests/test_qq_card_buttons.py（宿主纯标准库）
      docker exec -i qq-bridge-official /opt/venv/bin/python -m pytest tests/test_qq_card_buttons.py -v
"""
from __future__ import annotations

import ast
import importlib
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
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _install_rank_backend():
    """给 rank_eligibility 注入 business 真函数（宿主/容器一致，读磁盘真数据）。"""
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
    rel_mod = _load(_CH / "rank_eligibility.py", "rank_eligibility")
    rel_mod.set_backend(wf._resolve_url_name, lambda s: slug_tags.get(s) or [])
    return wf


inv_mod = _load(_CH / "command_inventory.py", "command_inventory")
cb = _load(_CH / "card_buttons.py", "card_buttons")
_real_wf = _install_rank_backend()
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


def test_each_card_buttons_equal_siblings():
    """守门② 每卡按钮 == 该卡档位全集去掉当前口令自己（不多不少、无错位/自己/主菜单）。"""
    base = _baseline_groups()
    for fn, modes in base.items():
        if len(modes) <= 1:
            continue  # 单档卡零按钮，由守门⑤验
        for probe in modes:
            btns = kb.buttons_for(probe)
            # 独立基准：本卡档位去掉 probe 自己（大小写不敏感）
            expected = {m for m in modes if m.lower() != probe.lower()}
            got = {b.cmd for b in btns if " " not in b.cmd}
            assert got == expected, \
                "卡 %s(口令 %s) 邻档错：got=%s expected=%s" % (fn, probe, got, expected)
            for b in btns:
                assert not b.primary, "残留蓝色 primary（当前卡自己按钮）：%s -> %s" % (probe, b)
                assert b.cmd != "帮助" and b.label != "主菜单", \
                    "残留主菜单按钮：%s -> %s" % (probe, b)
                assert " " not in b.cmd, "档位卡不该出现市价带参按钮：%s -> %s" % (probe, b.cmd)
    # 具体反例：裂隙卡绝不能出现自己/赏金/市价/紫卡/主菜单
    fissure = [b.cmd for b in kb.buttons_for("裂隙")]
    for forbidden in ("裂隙", "赏金", "钢铁赏金", "市价", "紫卡", "抽签", "帮助"):
        assert forbidden not in fissure, "裂隙卡混入 %s：%s" % (forbidden, fissure)


def test_rank_button_no_fabrication():
    """守门③ 带参串零编造：市价按钮 data 严格 <真实口令> <物品> <真实rank>。"""
    base_market = _baseline_market()
    base_ranks = _baseline_ranks()
    inv_cmds = inv_mod.build_inventory().commands
    # 第五次修改：rank 正例用 MOD/赋能（持久力/延伸/充沛/狂怒），武器绝路零按钮
    probes = ["wm 绝路", "市价 延伸", "市场 持久力 p", "赤毒 绝路",
              "WM 持久力 满级", "wm 持久力 R0", "价格 满级 延伸",
              "wm 充沛", "wm 狂怒", "wm 加速"]
    seen_rank_btn = 0
    for p in probes:
        for b in kb.buttons_for(p):
            if " " not in b.cmd:
                assert b.cmd in inv_cmds, \
                    "零参按钮 data 编造（主菜单/自己/邻卡越界都会命中）：%r -> %s" % (p, b.cmd)
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
    """守门④ 满级/0级专属（第五次修改）：只有 MOD/赋能挂等级。

    * ``wm 持久力``(MOD) 恰好出 ``满级/R0``，无市价口令自己/主菜单/primary，
      data 携带物品名「持久力」，重复 rank 幂等剥离；
    * ``wm 绝路``(武器) 零按钮——第五次用户纠正的核心回归点。
    """
    bs = kb.buttons_for("wm 持久力")
    data = [b.cmd for b in bs]
    assert "WM 持久力 满级" in data or "wm 持久力 满级" in data, \
        "缺满级按钮（或没携带物品名持久力）：%s" % data
    assert "WM 持久力 R0" in data or "wm 持久力 R0" in data, \
        "缺 0级/R0 按钮（或没携带物品名持久力）：%s" % data
    # 市价卡第四次修改：只这两个带参按钮，无市价口令自己、无主菜单、无 primary
    assert len(bs) == 2, "市价卡应恰好满级+R0 两个按钮：%s" % data
    for b in bs:
        assert not b.primary and b.cmd != "帮助", "市价卡残留主菜单/primary：%s" % b
        if b.label in ("满级", "0级"):
            assert "持久力" in b.cmd, "rank 按钮没带物品名：%s" % b.cmd
    # 第五次修改：武器绝路即使带 rank 写法也零按钮（无等级资格）
    for p in ("wm 绝路", "WM 绝路 满级", "wm 0级 绝路", "市价 绝路 R5"):
        assert kb.buttons_for(p) == (), "无等级武器绝路不该有 rank 按钮：%r" % p
    # 赋能充沛（arcane_enhancement）也应有等级
    assert len(kb.buttons_for("wm 充沛")) == 2, "赋能充沛应出满级/R0"
    # 幂等：已带 rank 的输入再派生，物品名里不应残留任何源码可识别的 rank 写法
    # （包括别名 0级/未升级/零级/R0/R5/5级/max 等），否则会出现重复堆叠。
    import re as _re
    strip_rules = cb.build_card_inventory().rank_strip_rules
    assert strip_rules, "源码 rank 剥离规则为空（实现退化）"
    for p in ("wm 持久力 满级", "市价 延伸 R0", "wm 0级 持久力",
              "wm 未升级 持久力", "wm 持久力 R5", "wm max 持久力"):
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
    """结构合法：腾讯平台硬限（label≤10 / 行≤5 / 无 primary）；宿主无 nonebot 只验 Btn 元组。

    第四次修改：去掉自己+主菜单后，档位卡按钮数 = 档位数-1（1~2），市价卡=2，
    单档/GENERIC=0；不再有 primary、不再有末位主菜单。零按钮的 make_keyboard
    必须返 (None,())，绝不构造空 keyboard 下发。
    """
    base = _baseline_groups()
    multi = {c for modes in base.values() if len(modes) > 1 for c in modes}
    # 第五次修改：无等级市价物品（绝路=武器/沙皇=武器）也零按钮
    zero = {"帮助", "", "/ai", "闲聊", "wm", "警报", "科研", "仲裁表",
            "wm 绝路", "wm 沙皇", "wm 绝路 Prime 枪管"}
    priced = {"wm 持久力", "市价 延伸", "wm 充沛", "wm 狂怒"}
    for p in multi:
        btns = kb.buttons_for(p)
        assert 1 <= len(btns) <= 4, "档位卡按钮数越界 %r -> %d" % (p, len(btns))
        assert all(not b.primary and b.cmd != "帮助" for b in btns), \
            "档位卡残留 primary/主菜单 %r" % p
        for b in btns:
            assert 0 < len(b.label) <= 10, "label 越界 %r -> %s" % (p, b.label)
    for p in priced:
        btns = kb.buttons_for(p)
        assert 1 <= len(btns) <= 4 and all(not b.primary for b in btns), \
            "市价卡按钮数/primary 越界 %r -> %d" % (p, len(btns))
    for p in zero:
        assert kb.buttons_for(p) == (), "零按钮卡产出按钮了 %r" % p
        assert kb.make_keyboard(p) == (None, ()), \
            "零按钮卡 make_keyboard 未返 (None,()) %r" % p
    try:
        from nonebot.adapters.qq.models.common import MessageKeyboard  # noqa: F401
    except ImportError:
        print("[skip] nonebot 不在宿主环境，真 MessageKeyboard 结构项在容器内跑")
        return
    for p in multi | priced:
        model, _btns = kb.make_keyboard(p)
        assert isinstance(model, MessageKeyboard), "有按钮卡却没键盘 %r" % p
        rows = model.content.rows
        assert 1 <= len(rows) <= 5 and all(1 <= len(r.buttons) <= 5 for r in rows), \
            "keyboard 行列越界 %r" % p
        for r in rows:
            for b in r.buttons:
                assert len(b.render_data.label) <= 10 and b.action.data, \
                    "按钮字段非法 %r" % p
                assert b.render_data.style in (0, 1, 3, 4), "style 非法 %r" % p


def test_zero_button_cards_and_market():
    """守门⑤ 零按钮 + 市价裸口令：单档/GENERIC/裸市价/**无等级物品** → () 且 (None,())；
    MOD/赋能市价带物品名 → 只满级/R0。"""
    zero = ["帮助", "", "/ai", "闲聊", "wm", "市价", "警报", "科研", "仲裁表",
            "平原", "配卡", "词条推荐", "执政官", "紫卡融合", "紫卡全面", "角色", "模拟开卡",
            # 第五次修改：武器/战甲/部件无等级 → 零按钮
            "wm 绝路", "wm 沙皇", "wm 绝路 Prime 枪管", "wm 犀牛", "市价 绝路 满级"]
    for p in zero:
        assert kb.buttons_for(p) == (), "零按钮卡产出按钮了 %r" % p
        assert kb.make_keyboard(p) == (None, ()), "零按钮卡未返 (None,()) %r" % p
    # MOD/赋能市价带物品名：只两个带参 rank，无市价口令自己/主菜单
    for p in ("wm 持久力", "市价 延伸", "wm 充沛", "wm 狂怒", "WM 持久力 满级"):
        data = [b.cmd for b in kb.buttons_for(p)]
        assert len(data) == 2 and all(" " in d for d in data), \
            "有等级市价卡应只出两个带参 rank %r -> %s" % (p, data)
        assert all(b.cmd != "帮助" and not b.primary for b in kb.buttons_for(p)), \
            "市价卡残留主菜单/primary %r" % p


def test_injection_guards():
    """prove-it：四类真实错误放回，对应守门必须报错，恢复后通过。"""
    inv_cmds = inv_mod.build_inventory().commands
    base_market = _baseline_market()
    base_ranks = _baseline_ranks()
    base_groups = _baseline_groups()

    def _fissure_siblings_ok(btns):
        expected = {m for m in base_groups.get("_send_fissure", ()) if m.lower() != "裂隙"}
        got = {b.cmd for b in btns if " " not in b.cmd}
        return got == expected and all(not b.primary and b.cmd != "帮助" for b in btns)

    # 注入 1：把「当前卡自己」塞回裂隙卡 → 守门②（== 邻档去自己）应拒绝
    fissure_with_self = kb.buttons_for("裂隙") + (kb.Btn("裂隙", "裂隙"),)
    try:
        assert _fissure_siblings_ok(fissure_with_self), "守门②应拒绝塞回的自己按钮"
        raise AssertionError("守门②未报错（自己按钮没被拦）")
    except AssertionError as e:
        assert "守门②未报错" not in str(e), "守门②确实没拦住自己按钮"

    # 注入 2：把主菜单塞回裂隙卡 → 守门②应拒绝
    fissure_with_menu = kb.buttons_for("裂隙") + (kb.Btn("主菜单", "帮助"),)
    try:
        assert _fissure_siblings_ok(fissure_with_menu), "守门②应拒绝塞回的主菜单"
        raise AssertionError("守门②未报错（主菜单没被拦）")
    except AssertionError as e:
        assert "守门②未报错" not in str(e), "守门②确实没拦住主菜单"

    # 注入 3：假 rank token——data 末词改成源码里没有的 满级X，守门③应拒绝
    fake_rank_data = "WM 绝路 满级X"
    parts = fake_rank_data.split()
    try:
        assert parts[-1] in base_ranks, "带参守门应拒绝假 rank token"
        raise AssertionError("守门③未报错（假 rank 没被拦）")
    except AssertionError as e:
        assert "守门③未报错" not in str(e), "守门③确实没拦住假 rank"

    # 注入 4：假市价口令——首词 wm2 不在真实口令集，守门③应拒绝
    fake_market_data = "wm2 绝路 满级"
    hp = fake_market_data.split()[0]
    try:
        assert hp in base_market and hp.lower() in {c.lower() for c in inv_cmds}, \
            "带参守门应拒绝假市价口令"
        raise AssertionError("守门③未报错（假市价口令没被拦）")
    except AssertionError as e:
        assert "守门③未报错" not in str(e), "守门③确实没拦住假市价口令"

    # 恢复断言（第五次修改：rank 正例用 MOD 持久力，武器绝路应零按钮）
    fissure_now = [b.cmd for b in kb.buttons_for("裂隙")]
    assert "裂隙" not in fissure_now and "帮助" not in fissure_now
    assert "满级X" not in " ".join(b.cmd for b in kb.buttons_for("wm 持久力"))
    assert kb.buttons_for("wm 绝路") == (), "恢复后武器绝路应零按钮"


if __name__ == "__main__":
    import traceback
    tests = [
        test_baseline_sane,
        test_impl_matches_independent_baseline,
        test_each_card_buttons_equal_siblings,
        test_rank_button_no_fabrication,
        test_rank_exact_and_idempotent,
        test_structure_platform_limits,
        test_zero_button_cards_and_market,
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
