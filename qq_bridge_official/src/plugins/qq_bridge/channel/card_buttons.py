# -*- coding: utf-8 -*-
"""卡片级「对应功能按钮」派生（仅官方副本，2026-11-11 第三次按钮返工，AST 零手工）。

## 为什么第三次返工
前两版 ``buttons_for`` 只按 family/domain 机械挑邻居，同族不足 3 个就用
``_POPULAR`` 全局高频词（赏金/裂隙/市价/紫卡）补齐 → 用户真实群截图里**每张卡**
都显示成「赏金/钢路/钢铁/裂隙/主菜单」这种全局导航，而不是「这张卡自己的下钻按钮」。
用户第三次明确：「按钮只展示对应功能的按钮，裂隙卡下面有钢铁裂隙/普通裂隙，
市价卡下面有满级/0级，举一反三，每个功能每个卡片都加上对应功能按钮」。

## 唯一事实源（全部 AST 派生，零手工口令/别名表）
1. **出卡档位族**：扫 ``game_tools/__init__.py::_handle`` 的 ``if cmd in (...)``
   分支，取分支体第一个 ``_send_*(...)`` 调用的函数名作 group key；每个分支 =
   一个档位（canonical = 分支元组首个非共享词，aliases = 分支全部 cmd 字面量）。
   若分支体含 ``{...}.get(cmd, default)`` 的 mode 字典（如裂隙），则按字典 key
   拆成多个档位（default=裂隙 / 普通裂隙 / 钢铁裂隙）。
   → 裂隙族 [_send_fissure] = 裂隙|普通裂隙|钢铁裂隙；赏金族 [_send_bounty] =
   赏金|钢铁赏金；信条/终幕族 [_send_tenet] = 信条|终幕。
   **共享别名**（出现在 ≥2 个 group 的词，如 wf/WF/星际战甲/战甲）自动排除，
   绝不做档位按钮（点了不是那张专属卡）。
2. **市价口令集**：扫全文件所有 body 含 ``warframe.market_text(...)`` 的 if 分支
   cmd 字面量，以及 ``_wf_dispatch`` 字典里 value 含 market_text 调用的 key。
   → {价格, 市价, wm, WM, 市场, 赤毒, ...}。
3. **rank token 白名单**：扫 ``warframe.py::_parse_mod_rank`` 所有 Return 的
   rank_label 字面量（第 3 个元素）→ {满级, R0}（``f"R{rank}"`` 动态值跳过）。
   这两个正是该函数的规范 label，点下去必被正确解析成满级 / 0 级市价卡。

新增一个 ``_send_*`` 出卡分支 / 新 rank label → 本模块下次 import 自动纳入；
反之按钮没跟上，``tests/test_qq_card_buttons.py`` 的第二套独立 AST 基准直接 FAIL。

纯标准库（ast/dataclasses/pathlib），不 import nonebot / 不 import game_tools /
不 import warframe，故可在宿主离线单测，也不触发插件重量级链式 import。
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_GT = Path(__file__).resolve().parents[1] / "game_tools" / "__init__.py"
_WF = _GT.parent / "warframe.py"


@dataclass(frozen=True)
class Mode:
    """一张卡的一个档位（如「钢铁裂隙」）；canonical 作按钮 data（∈ commands）。"""
    canonical: str
    aliases: frozenset[str]


@dataclass(frozen=True)
class CardGroup:
    """一个出卡函数对应的一张功能卡及其所有档位。"""
    send_fn: str
    modes: tuple[Mode, ...]
    source: str


@dataclass
class CardInventory:
    groups: tuple[CardGroup, ...]
    market_cmds: frozenset[str]
    rank_tokens: tuple[str, ...]
    #: 从 warframe._MOD_RANK_PATTERNS_* 派生的「剥离物品名已有 rank」规则：
    #: (正则字面量, clean_group)，clean_group="first" 表示物品名在 group(1)
    #: （END 模式），"last" 表示物品名在最后一个 group（START/START_NOSPACE 模式）。
    #: 保字节来自源码，与 _parse_mod_rank 取 clean_item 的规则同源。
    rank_strip_rules: tuple[tuple[str, str], ...] = ()
    #: cmd -> (send_fn, mode 的 canonical)；共享别名不收
    slot_of: dict[str, tuple[str, str]] = field(default_factory=dict)
    shared_aliases: frozenset[str] = frozenset()

    def slots_for(self, cmd: str) -> tuple[str, ...]:
        """返回当前 cmd 所属卡的「所有档位 canonical」（含自己），保序；单档卡=自己。"""
        slot = self.slot_of.get(cmd)
        if not slot:
            return ()
        send_fn = slot[0]
        for g in self.groups:
            if g.send_fn == send_fn:
                return tuple(m.canonical for m in g.modes)
        return ()


_inv: CardInventory | None = None


# ---------------------------------------------------------------------------
# AST 工具
# ---------------------------------------------------------------------------
def _local_tuple_literals(func: ast.AST) -> dict[str, list[str]]:
    """函数内局部 tuple/list/set 字面量（保序），供 ``cmd in <变量>`` 展开。"""
    out: dict[str, list[str]] = {}
    for n in ast.walk(func):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    try:
                        val = ast.literal_eval(n.value)
                    except Exception:  # noqa: BLE001
                        continue
                    if isinstance(val, (tuple, list, set, frozenset)):
                        words = [x for x in val if isinstance(x, str)]
                        if words:
                            out[t.id] = words
    return out


def _cmd_words_in_test(test: ast.AST, local: dict[str, list[str]],
                       mod_tables: dict[str, list[str]]) -> list[str]:
    """按源码顺序取 test 里所有 ``cmd in (...)`` / ``cmd == 'x'`` 的字面量。

    只认被比较主体是 ``cmd``（不含 ``arg.split()[0].lower() in (...)`` 之类二级
    子命令，避免把 wf 的二级词误当档位）。保序：元组 elts 顺序即源码顺序。
    """
    words: list[str] = []
    for n in ast.walk(test):
        if not isinstance(n, ast.Compare):
            continue
        left = n.left
        is_cmd = isinstance(left, ast.Name) and left.id == "cmd"
        if not is_cmd and isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute) \
                and left.func.attr == "lower" and isinstance(left.func.value, ast.Name) \
                and left.func.value.id == "cmd":
            is_cmd = True
        if not is_cmd:
            continue
        for op, comp in zip(n.ops, n.comparators):
            if isinstance(op, ast.In):
                if isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                    words += [e.value for e in comp.elts
                              if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                elif isinstance(comp, ast.Name):
                    words += list(local.get(comp.id) or mod_tables.get(comp.id) or ())
            elif isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) \
                    and isinstance(comp.value, str):
                words.append(comp.value)
    # 去重保序
    seen: set[str] = set()
    return [w for w in words if not (w in seen or seen.add(w))]


def _first_send_call(body: list[ast.stmt]) -> str:
    """分支体里第一个 ``_send_*(...)`` 调用的函数名；无则空串。"""
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                    and n.func.id.startswith("_send_"):
                return n.func.id
    return ""


def _mode_dict_keys(body: list[ast.stmt]) -> dict[str, str]:
    """分支体里 ``{"口令": "mode"}.get(cmd, <default>)`` 的 mode 字典（保序）。

    ``{...}.get(...)`` 在 AST 里是 ``Call(func=Attribute(value=Dict, attr="get"))``，
    字典在 ``func.value``（不是 ``args[0]``）。裂隙 ``_send_fissure`` 分支：
    ``{"普通裂隙": "normal", "钢铁裂隙": "steel"}.get(cmd, "quick")``
    → 返回 ``{"普通裂隙": "normal", "钢铁裂隙": "steel"}``；default（裂隙）由调用方补。
    """
    out: dict[str, str] = {}
    for stmt in body:
        for n in ast.walk(stmt):
            if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute) \
                    or n.func.attr != "get":
                continue
            d = n.func.value  # 方法调用的对象：{...}
            if isinstance(d, ast.Dict):
                for k, v in zip(d.keys, d.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        out.setdefault(k.value, v.value if isinstance(v, ast.Constant) else "")
    return out


def _has_market_text(node) -> bool:
    """node 可是单个 AST 节点或语句 list；在其中找 warframe.market_text 调用。"""
    if isinstance(node, (list, tuple)):
        return any(_has_market_text(x) for x in node)
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "market_text":
            return True
    return False


def _module_tables(tree: ast.AST) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    try:
                        val = ast.literal_eval(node.value)
                    except Exception:  # noqa: BLE001
                        continue
                    if isinstance(val, (tuple, list, set, frozenset)):
                        words = [x for x in val if isinstance(x, str)]
                        if words:
                            out[t.id] = words
    return out


# ---------------------------------------------------------------------------
# 三个派生
# ---------------------------------------------------------------------------
def _build_card_groups(tree: ast.AST) -> tuple[CardGroup, ...]:
    handle = next((fn for fn in tree.body
                   if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and fn.name == "_handle"), None)
    if handle is None:
        return ()
    local = _local_tuple_literals(handle)
    mod_tables = _module_tables(tree)

    # 每个 (send_fn, source, ordered words, mode_map) 先收为一个原始档位描述
    raw: list[tuple[str, str, list[str], dict[str, str]]] = []
    for node in ast.walk(handle):
        if not isinstance(node, ast.If):
            continue
        words = _cmd_words_in_test(node.test, local, mod_tables)
        if not words:
            continue
        send_fn = _first_send_call(node.body)
        if not send_fn:
            continue
        raw.append((send_fn, "_handle:%d" % node.lineno, words, _mode_dict_keys(node.body)))

    # 统计词出现在多少个不同 send_fn → ≥2 为共享别名（wf/WF/战甲…），排除
    per_fn_words: dict[str, set[str]] = {}
    for send_fn, _src, words, _mk in raw:
        per_fn_words.setdefault(send_fn, set()).update(w for w in words if w)
    cnt = Counter(w for ws in per_fn_words.values() for w in ws)
    shared = {w for w, n in cnt.items() if n >= 2}

    # group -> 保序档位（canonical, aliases）
    groups: dict[str, list[Mode]] = {}
    sources: dict[str, str] = {}
    for send_fn, src, words, mode_map in raw:
        clean = [w for w in words if w and w not in shared]
        sources.setdefault(send_fn, src)
        if mode_map:
            # 一个分支多档：mode 字典 key 各自成档，default（分支词里不在 key 的）成一档
            default_words = [w for w in clean if w not in mode_map]
            bucket: list[Mode] = groups.setdefault(send_fn, [])
            if default_words and not any(m.canonical == default_words[0] for m in bucket):
                bucket.append(Mode(default_words[0], frozenset(default_words)))
            for k in mode_map:
                if k not in shared and not any(m.canonical == k for m in bucket):
                    bucket.append(Mode(k, frozenset({k})))
        else:
            if not clean:
                continue
            bucket = groups.setdefault(send_fn, [])
            canonical, aliases = clean[0], frozenset(clean)
            if any(m.canonical == canonical for m in bucket):
                continue
            bucket.append(Mode(canonical, aliases))

    return tuple(CardGroup(send_fn, tuple(modes), sources.get(send_fn, ""))
                 for send_fn, modes in groups.items() if modes)


def _build_market_cmds(tree: ast.AST) -> frozenset[str]:
    """含 warframe.market_text 调用的 if 分支 cmd 字面量 + _wf_dispatch 字典 key。"""
    local_mod = _module_tables(tree)
    out: set[str] = set()
    all_funcs = [fn for fn in tree.body
                 if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in all_funcs:
        local = _local_tuple_literals(fn)
        for n in ast.walk(fn):
            if isinstance(n, ast.If) and _has_market_text(n.body):
                out |= set(_cmd_words_in_test(n.test, local, local_mod))
            # _wf_dispatch 的 m = { "赤毒": await warframe.market_text(...) }
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) \
                            and _has_market_text(v):
                        out.add(k.value)
    return frozenset(w for w in out if w)


def _build_rank_tokens(wf_tree: ast.AST) -> tuple[str, ...]:
    """_parse_mod_rank 所有 Return 的 rank_label 字面量（第 3 元素），保序去重。"""
    fn = next((f for f in wf_tree.body
               if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
               and f.name == "_parse_mod_rank"), None)
    if fn is None:
        return ()
    out: list[str] = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple) \
                and len(n.value.elts) >= 3:
            lbl = n.value.elts[2]
            if isinstance(lbl, ast.Constant) and isinstance(lbl.value, str) \
                    and lbl.value not in out:
                out.append(lbl.value)
    return tuple(out)


def _rank_pattern_names() -> tuple[str, ...]:
    """承载 MOD rank 正则的模块级列表变量名（源码事实，非手工 token）。"""
    return ("_MOD_RANK_PATTERNS_END", "_MOD_RANK_PATTERNS_START",
            "_MOD_RANK_PATTERNS_START_NOSPACE")


def _build_rank_strip_patterns(wf_tree: ast.AST) -> tuple[tuple[str, str], ...]:
    """从 warframe 三个 ``_MOD_RANK_PATTERNS_*`` 列表取所有 re.compile 正则 + 取组规则。

    用途：派生 rank 按钮前，先把物品名里**已有的任意 rank 写法**（满级/R0/0级/
    未升级/R5/5级/max，开头或结尾、有空格或紧贴）剥掉，避免重复堆叠
    （如输入 ``wm 0级 绝路`` 再挂按钮变成 ``wm 0级 绝路 满级``）。
    取组规则来自列表变量名：``*_END`` 物品名在 group(1)（"first"），
    ``*_START*`` 物品名在最后 group（"last"）——与 _parse_mod_rank 同源，零手工。
    """
    out: list[tuple[str, str]] = []
    for node in wf_tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        hit = names & set(_rank_pattern_names())
        if not hit:
            continue
        rule = "first" if any("_END" in n for n in hit) else "last"
        for n in ast.walk(node.value):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "compile" and n.args:
                pat = n.args[0]
                if isinstance(pat, ast.Constant) and isinstance(pat.value, str):
                    pair = (pat.value, rule)
                    if pair not in out:
                        out.append(pair)
    return tuple(out)


def build_card_inventory(gt_src: Path | None = None,
                         wf_src: Path | None = None) -> CardInventory:
    gt_src = gt_src or _GT
    wf_src = wf_src or _WF
    gt_tree = ast.parse(Path(gt_src).read_text(encoding="utf-8"))
    wf_tree = ast.parse(Path(wf_src).read_text(encoding="utf-8"))

    groups = _build_card_groups(gt_tree)
    market_cmds = _build_market_cmds(gt_tree)
    rank_tokens = _build_rank_tokens(wf_tree)
    rank_strip_rules = _build_rank_strip_patterns(wf_tree)

    shared: set[str] = set()
    counts = Counter(w for g in groups for m in g.modes for w in m.aliases)
    # 判定共享别名需跨 group；这里用 slot 阶段重建（与 _build_card_groups 同口径）
    per_fn: dict[str, set[str]] = {}
    for g in groups:
        per_fn.setdefault(g.send_fn, set()).update(
            w for m in g.modes for w in m.aliases)
    c2 = Counter(w for ws in per_fn.values() for w in ws)
    shared = {w for w, n in c2.items() if n >= 2}

    slot_of: dict[str, tuple[str, str]] = {}
    for g in groups:
        for m in g.modes:
            for w in m.aliases:
                if w not in shared:
                    slot_of[w] = (g.send_fn, m.canonical)

    return CardInventory(
        groups=groups,
        market_cmds=market_cmds,
        rank_tokens=rank_tokens,
        rank_strip_rules=rank_strip_rules,
        slot_of=slot_of,
        shared_aliases=frozenset(shared),
    )


def get_card_inventory() -> CardInventory:
    global _inv
    if _inv is None:
        _inv = build_card_inventory()
    return _inv


def reload_card_inventory() -> CardInventory:
    global _inv
    _inv = build_card_inventory()
    return _inv


if __name__ == "__main__":
    inv = build_card_inventory()
    print("GROUPS =", len(inv.groups))
    for g in inv.groups:
        print("  %-24s %s  %s" % (
            g.send_fn, "|".join(m.canonical for m in g.modes), g.source))
    print("\nMARKET_CMDS =", sorted(inv.market_cmds))
    print("RANK_TOKENS =", list(inv.rank_tokens))
    print("SHARED(excluded) =", sorted(inv.shared_aliases))
    print("\n[用户举例核对]")
    for c in ("裂隙", "普通裂隙", "钢铁裂隙", "赏金", "钢铁赏金", "信条", "终幕"):
        print("  %-6s slots=%s" % (c, inv.slots_for(c)))
