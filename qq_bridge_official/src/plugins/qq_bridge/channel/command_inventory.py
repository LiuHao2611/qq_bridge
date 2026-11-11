# -*- coding: utf-8 -*-
"""真实出卡口令清单 —— 从 game_tools 分发源码 AST 派生（唯一事实源，仅官方副本）。

为什么存在（2026-11-11 第二次返工的根因）：
  上一版 qq_keyboard.py 手工维护 `_GROUPS`(130+ 键) + `_ALIASES`，测试又从
  NATURAL_CMDS 词表抽词去验同一张手工表 —— 「自己考自己」，导致：
    * 编造别名 `fhelp` / `w M` 源码里根本不存在，却混进按钮 data（点了静默）；
    * 错误映射 `"终幕": "信条"`，与 _handle:1960/1963 两个独立分支冲突；
    * 15 个「只在分发函数体出现、词表/HELP 都没登记」的真实口令 100% 漏检
      （alert/alerts/netracell/今日运势/作答/成语接龙/放弃/灵化武器/灵化轮换/
       猜数/科研任务/认输/运气/退出/问答）。

本模块把「哪些口令真实会出卡、谁和谁是同一个命令的别名、每个命令出什么卡」
全部交给源码 AST，**不设任何手工口令/别名表**。新增一个 `if cmd in ("新口令",)`
分支 → 本模块下次 import 自动纳入 → 按钮自动覆盖；反之若按钮没跟上，测试
（tests/test_qq_keyboard.py）会拿本模块派生的全集当独立基准直接判 FAIL。

纯标准库（ast/pathlib/re），不 import nonebot / 不 import game_tools，故可在无
依赖环境离线单测，也不触发插件重量级链式 import。只在 import 时解析一次并缓存。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

# 分发源码路径：本文件在 channel/ 下，game_tools 是其兄弟目录
_SRC = Path(__file__).resolve().parents[1] / "game_tools" / "__init__.py"

# 会把 cmd 分发出去并出卡的函数（_handle 主体 + dispatch + 任务订阅入口）。
# 只包含「读 cmd 并决定出什么」的函数；纯工具函数不扫。
_DISPATCH_FUNCS = {
    "_handle", "_handle_safe", "dispatch",
    "_handle_task_sub", "_task_sub_intent",  # 订阅：c in _SUB_* 分发
}

# 模块级「命令词表」变量：这些 tuple/set/dict 的每个字面量都是用户可发的口令。
_TABLE_VARS = (
    "NATURAL_CMDS", "GUIDE_TITLES", "ZERO_ARG_OK",
    "_PLAN_CMD_WORDS", "_DISPATCH_CMD_WORDS",
    "_SUB_ADD_WORDS", "_SUB_DEL_WORDS", "_SUB_LIST_WORDS",
    "_FULL_WAIT_WORDS",  # 紫卡全面分析预约（text in ... 前置分支）
)


@dataclass(frozen=True)
class Family:
    """一个 `if cmd in (a, b, c)` 分支 = 一个别名族（同一条命令的多种写法）。

    - canonical: 族代表（元组第一个，用于按钮展示/归组），永远是该族真实口令之一
    - aliases:   该分支里所有可触发同一出卡逻辑的口令
    - domain:    该分支调用的出卡函数/资料函数（用于挑语义相近的邻居按钮），
                 取不到记为 ""。同 domain 的不同 family 互为「相关命令」。
    - source:    源码位置（函数:行），用于测试报告定位。
    """
    canonical: str
    aliases: frozenset[str]
    domain: str
    source: str


@dataclass
class Inventory:
    commands: frozenset[str]                       # 真实出卡口令全集
    families: tuple[Family, ...] = ()              # 所有别名族
    domain_of: dict[str, str] = field(default_factory=dict)   # cmd -> domain
    family_of: dict[str, str] = field(default_factory=dict)   # cmd -> canonical
    _loaded: bool = False


_inv: Inventory | None = None


def _literal_strings(node: ast.AST) -> set[str]:
    """取节点里所有字符串常量（用于 `cmd in ("a","b")` / `cmd == "x"` 的全字面量）。"""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
    return out


def _domain_of_body(body: list[ast.stmt]) -> str:
    """从分支体里取「出卡函数」名（第一个 await/call 的业务函数），作为语义域。

    例：``await _send_bounty(...)`` -> ``_send_bounty``
        ``return await warframe.sortie_text()`` -> ``warframe.sortie_text``
    只用于给按钮挑邻居，不影响「是否覆盖/是否编造」的硬判定。
    """
    skip = {"str", "int", "len", "bool", "print", "getattr", "setattr",
            "isinstance", "dict", "list", "tuple", "set", "range", "enumerate"}
    for stmt in body:
        # 展开 return / await / expr 找最外层 call
        node: ast.AST | None = stmt
        for _ in range(4):
            if isinstance(node, ast.Return):
                node = node.value
            elif isinstance(node, ast.Expr):
                node = node.value
            elif isinstance(node, ast.Await):
                node = node.value
            else:
                break
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                return "%s.%s" % (f.value.id, f.attr)
            if isinstance(f, ast.Name) and f.id not in skip and not f.id.startswith("_flog"):
                return f.id
    return ""


def _iter_cmd_branches(func: ast.AST, module_literals: dict[str, set[str]]):
    """遍历函数体里所有「以 cmd 为左值」的 Compare 节点，产出 (aliases, domain, source)。

    覆盖形态：
      * ``if cmd in ("赏金",): ...``
      * ``if cmd == "猜": ...``
      * ``if cmd in GUIDE_TITLES: ...``（模块级词表）
      * ``if c in _SUB_ADD_WORDS: ...``（模块级订阅词表，c 是 cmd 的局部别名）
      * ``if cmd in _tr_words:``（函数内局部词表）
      * ``if cmd.lower() in ("下发计划",...):``
      * ``if cmd in ("wf",...) and arg.split()[0] in (...):``（复合条件取 cmd 部分）
    """
    # 函数内局部字面量变量（_tr_words / _sp_words / _fusion_words / _full_words 等）
    local_literals: dict[str, set[str]] = {}
    for n in ast.walk(func):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    try:
                        val = ast.literal_eval(n.value)
                    except Exception:
                        continue
                    collapse = _string_leaves(val, dict_keys_only=(t.id == "GUIDE_TITLES"))
                    if collapse is not None:
                        local_literals[t.id] = collapse

    for node in ast.walk(func):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        # 识别 cmd / c（订阅函数里的局部别名）/ text（预约分支如
        # `if text in _FULL_WAIT_WORDS`）/ .lower() 作为被比较主体。
        # 注意：绝不含 `t`——它是 _match_natural 里遍历 NATURAL_CMDS 的循环变量
        # （`if t == cmd`），纳入会误把 cmd 自己当口令字面量。
        _CMD_VARS = ("cmd", "c", "text")  # noqa: N806
        base_name = None
        if isinstance(left, ast.Name) and left.id in _CMD_VARS:
            base_name = left.id
        elif (isinstance(left, ast.Call)
              and isinstance(left.func, ast.Attribute) and left.func.attr == "lower"
              and isinstance(left.func.value, ast.Name) and left.func.value.id in _CMD_VARS):
            base_name = left.func.value.id
        if base_name is None:
            continue
        for op, comp in zip(node.ops, node.comparators):
            aliases: set[str] = set()
            broad = False  # 整表兜底（如 `cmd in GUIDE_TITLES`）：不是别名族
            if isinstance(op, (ast.In, ast.NotIn)):
                aliases |= _comparison_operand(comp, module_literals, local_literals)
                # dict 变量(GUIDE_TITLES)/超 12 词的集合 = 命令集合兜底，非同一命令的别名
                broad = (isinstance(comp, ast.Name)
                         and (comp.id == "GUIDE_TITLES" or len(aliases) > 12))
            elif isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                aliases.add(comp.value)
            if not aliases:
                continue
            # domain：找包含此 Compare 的上一级 if 的 body
            domain = _domain_for_compare(node, func)  # type: ignore[arg-type]
            yield aliases, domain, "%s:%d" % (getattr(func, "name", "?"), node.lineno), broad


def _string_leaves(val, *, dict_keys_only: bool = False) -> set[str] | None:
    """把字面量（tuple/list/set/dict/str）收成字符串集合；非纯字面量返回 None。

    dict 默认只取 key：GUIDE_TITLES 的 value 是「卡片标题」（如「星际战甲·MOD」、
    「游戏工具箱」），不是用户能发的口令，绝不能进按钮 data（点了会静默）。
    """
    out: set[str] = set()
    if isinstance(val, str):
        return {val}
    if isinstance(val, dict):
        out |= {k for k in val if isinstance(k, str)}
        if not dict_keys_only:
            out |= {v for v in val.values() if isinstance(v, str)}
        return out
    if isinstance(val, (tuple, list, set, frozenset)):
        for x in val:
            if isinstance(x, str):
                out.add(x)
        return out or None
    return None


def _comparison_operand(comp: ast.AST, module_l: dict, local_l: dict) -> set[str]:
    if isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
        return {e.value for e in comp.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
        return {comp.value}
    if isinstance(comp, ast.Name):
        return set(local_l.get(comp.id) or module_l.get(comp.id) or ())
    return set()


def _domain_for_compare(compare: ast.Compare, func: ast.AST) -> str:
    """向上找到最近的 If，取 Then 体里第一个业务调用作为 domain。"""
    target_lineno = compare.lineno
    best: str = ""
    for n in ast.walk(func):
        if isinstance(n, ast.If) and n.lineno <= target_lineno:
            # 只认最接近且 body 内确实含本 compare 的 if（用行号区间近似）
            end = getattr(n, "end_lineno", n.lineno) or n.lineno
            if n.lineno <= target_lineno <= end:
                d = _domain_of_body(n.body)
                if d:
                    best = d
    return best


def _module_literals(tree: ast.AST) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id in _TABLE_VARS:
                    try:
                        val = ast.literal_eval(node.value)
                    except Exception:
                        continue
                    lv = _string_leaves(val, dict_keys_only=(t.id == "GUIDE_TITLES"))
                    if lv is not None:
                        out[t.id] = lv
    return out


def build_inventory(src: Path | None = None) -> Inventory:
    """解析源码，构造清单。可被测试用自定义 src 做注入验证。"""
    src = src or _SRC
    tree = ast.parse(Path(src).read_text(encoding="utf-8"))

    all_cmds: set[str] = set()
    families: list[Family] = []
    domain_of: dict[str, str] = {}
    family_of: dict[str, str] = {}

    # 1) 模块级权威词表全部计入「用户能发出的口令」
    for _name, words in _module_literals(tree).items():
        all_cmds |= words

    # 2) HELP 文本里的 #命令
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "HELP":
                    try:
                        help_text = ast.literal_eval(node.value)
                    except Exception:
                        continue
                    if isinstance(help_text, str):
                        for tok in re.findall(r"#([^\s／/，,。：:]+)", help_text):
                            if tok:
                                all_cmds.add(tok)

    # 3) 分发函数体里的 cmd 分支 → 别名族候选 + domain
    #    模块级字面量（含 _SUB_ADD_WORDS 等订阅词表）提前构建，供 `cmd/c in <变量>` 展开
    module_l = _module_literals(tree)
    candidates: list[tuple[set[str], str, str, bool]] = []  # (aliases, domain, source, broad)
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name not in _DISPATCH_FUNCS:
            continue
        for aliases, domain, source, broad in _iter_cmd_branches(fn, module_l):
            all_cmds |= aliases
            candidates.append((aliases, domain, source, broad))

    # domain：任意分支里出现的非空 domain 都记（供按钮挑语义邻居，不参与 family 归属）
    for aliases, domain, _src, _broad in candidates:
        if domain:
            for a in aliases:
                domain_of.setdefault(a, domain)

    # family 归属遵循「最小分支优先」：一个口令属于包含它的最小别名族。
    # 步骤：先按 aliases 大小升序处理非 broad 分支，逐词锁定；broad 分支
    #（GUIDE_TITLES 整表兜底）只给「尚无专属 family」的词补一个单元素 family。
    non_broad = sorted((c for c in candidates if not c[3]), key=lambda c: len(c[0]))
    locked: dict[str, str] = {}  # cmd -> canonical（最小分支已锁定）
    fam_map: dict[str, Family] = {}
    for aliases, domain, source, _b in non_broad:
        # 该分支里「还没被更小分支锁定的词」才归本族
        free = {a for a in aliases if a not in locked}
        if not free:
            continue
        canonical = sorted(free, key=lambda s: (len(s), s))[0]
        fam = Family(canonical, frozenset(free), domain or domain_of.get(canonical, ""), source)
        fam_map[canonical] = fam
        for a in free:
            locked[a] = canonical
    # broad 兜底：为仍未归属的口令补单元素 family（domain 取 guide 兜底标记）
    broad_domain = "guide_fallback"
    for aliases, _domain, source, broad in candidates:
        if not broad:
            continue
        for a in sorted(aliases):
            if a not in locked:
                fam_map[a] = Family(a, frozenset({a}), broad_domain, source)
                locked[a] = a

    families = tuple(fam_map.values())
    family_of = {a: f.canonical for f in families for a in f.aliases}

    return Inventory(
        commands=frozenset(c for c in all_cmds if c),
        families=families,
        domain_of=domain_of,
        family_of=family_of,
        _loaded=True,
    )


def get_inventory() -> Inventory:
    global _inv
    if _inv is None:
        _inv = build_inventory()
    return _inv


def reload_inventory() -> Inventory:
    """强制重解析（测试注入验证用；生产不调用）。"""
    global _inv
    _inv = build_inventory()
    return _inv


if __name__ == "__main__":
    inv = build_inventory()
    print("TOTAL =", len(inv.commands))
    print("FAMILIES =", len(inv.families))
    print("\n[无词表/HELP 登记的纯分支口令]（上一轮漏检区）")
    table = set()
    for _n, _w in _module_literals(ast.parse(_SRC.read_text(encoding="utf-8"))).items():
        table |= _w
    pure = sorted(c for c in inv.commands if c not in table)
    print(len(pure), pure)
    print("\n[final answer 抽样：关键族]")
    for fam in inv.families:
        if fam.aliases & {"终幕", "coda", "信条", "tenet", "赏金", "钢铁赏金"}:
            print("  %-12s aliases=%s domain=%s %s" % (
                fam.canonical, sorted(fam.aliases), fam.domain, fam.source))
