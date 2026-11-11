# -*- coding: utf-8 -*-
"""守门：紫卡「最强紫卡」基准 = 该武器理论最优组合（官方副本，2026-11-11 全量对齐）。

防的是用户图1：伤害分虚高到 100(S)。真根因（宿主对照实验证伪了「暴击封顶」旧判断）：
  `_sim` 旧实现 `best_b = _apply_riven(std, stats, maxed=True)` 只把**当前同样 4 条**
  洗满（组合锁定）——当前卡 roll 越接近上限，`gain≈gain_max` 越严重，
  `dmg = gain/gain_max×100` 越被顶到 100。图2 Rabbit 的「最强」是该武器**理论最优
  词条组合天花板**（武器潜力基准，与当前卡词条无关）：

  * 图2 Coda Motovore  `当前+15.2% → 最强+138.4%`、击杀 `8.6→9.9→20.5`
  * 阿利乌参考卡(reference_sample) `gain=16.3% / gain_max=161.1%`

修复：新增 `riven_card._theoretical_best_stats()` 从 `riven_stat_base.json[类型]` 真表
派生最优组合（满 roll 边际用 `_dps` 真函数测，零写死卡名/数值）；`_sim` 改用它；
`_scores.total = max(dmg, mkt)`（两张参考卡反推的综合口径）。

锚定 official 副本根（parents[1]）。纯 AST + 隔离装配真 riven_card，宿主可跑（stub httpx/nonebot）。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CARD = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_card.py"

_P = _F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-52s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-52s %s" % (name, detail))


def _load_card():
    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def debug(self, *a, **k): pass
        def error(self, *a, **k): pass
        def exception(self, *a, **k): pass
    nb = types.ModuleType("nonebot"); nbl = types.ModuleType("nonebot.log")
    nbl.logger = _L(); nb.log = nbl
    sys.modules.setdefault("nonebot", nb); sys.modules.setdefault("nonebot.log", nbl)
    httpx = types.ModuleType("httpx")
    for n in ("HTTPError", "HTTPStatusError", "RequestError", "TimeoutException"):
        setattr(httpx, n, type(n, (Exception,), {}))
    httpx.AsyncClient = object
    sys.modules.setdefault("httpx", httpx)
    wf = types.ModuleType("_gt.warframe")
    wf._load_riven_tables = lambda: ({}, {}, {})
    pkg = types.ModuleType("_gt"); pkg.__path__ = []
    sys.modules["_gt"] = pkg; sys.modules["_gt.warframe"] = wf
    spec = importlib.util.spec_from_file_location("_gt.riven_card", _CARD)
    m = importlib.util.module_from_spec(spec); sys.modules["_gt.riven_card"] = m
    spec.loader.exec_module(m)
    return m


def _fn(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _code_strings(node):
    doc_ids = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                doc_ids.add(id(b[0].value))
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in doc_ids}


# 图2 Rabbit Coda Motovore 的 4 条真值（配卡守门同源输入）
_TGT_STATS = [
    {"en": "Critical Chance", "value": 222.0, "unit": "%", "sign": "+"},
    {"en": "Impact Damage", "value": 146.4, "unit": "%", "sign": "+"},
    {"en": "Heavy Attack Efficiency", "value": 90.4, "unit": "%", "sign": "+"},
    {"en": "Critical Chance for Slide Attack", "value": 109.3, "unit": "%", "sign": "-"},
]
_MELEE_CARDS = ("暗病打击", "压迫 Prime", "牺牲斩铁", "肢解", "北风", "病毒")


def section_a_static():
    print("== A. 静态纪律：理论最优组合从真表派生、零写死卡名 ==")
    src = _CARD.read_text(encoding="utf-8")
    tree = ast.parse(src)

    f_tb = _fn(tree, "_theoretical_best_stats")
    ok(f_tb is not None, "A1 存在 _theoretical_best_stats 唯一出口")
    tb_src = ast.get_source_segment(src, f_tb) or ""
    # 必须从真表读 + 用 _dps 真函数测边际
    ok("_load_riven_tables" in tb_src or "base_tbl" in tb_src,
       "A2 理论最优组合读 riven_stat_base 真表")
    ok("_dps(" in tb_src, "A3 边际用 _dps 真函数（非静态热度表）")
    ok("_DPS_STAT_KEYS" in tb_src, "A4 只从可进 DPS 桶的词条键筛选")
    ok("_ZERO_IMPACT_NEG" in tb_src, "A5 负槽取零影响候选（不伪造 DPS 增益）")

    # 执行代码不含写死的具体卡名（真表键必须是 en 名，中文卡名一个都不许出现）
    tb_strings = _code_strings(f_tb)
    leaked = [s for s in tb_strings if any(c in s for c in _MELEE_CARDS)]
    ok(not leaked, "A6 理论最优组合执行代码零写死具体卡名", "leak=%s" % leaked[:3])

    # _sim 必须改用它（且保留兜底）
    f_sim = _fn(tree, "_sim")
    sim_src = ast.get_source_segment(src, f_sim) or ""
    ok("_theoretical_best_stats" in sim_src, "A7 _sim 用理论最优组合作 best 基准")
    ok("_apply_riven(best_b, stats, maxed=True)" in sim_src,
       "A8 _sim 保留真表缺失时的旧语义兜底（诚实降级）")

    # _scores.total = max(dmg, mkt)
    f_sc = _fn(tree, "_scores")
    sc_src = ast.get_source_segment(src, f_sc) or ""
    ok("max(dmg_score, mkt)" in sc_src, "A9 综合分 = max(伤害, 市场)")
    ok('"total": total' in sc_src, "A10 _scores 返回 total=新口径")


def section_b_behavior():
    print("== B. 真行为：伤害分不再虚高、gain_max 拉开（隔离装配真模块） ==")
    # 用真 warframe 提供武器面板（桩 nonebot/httpx，warframe 只读本地 json 不触网）
    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def debug(self, *a, **k): pass
        def error(self, *a, **k): pass
        def exception(self, *a, **k): pass
    for name in list(sys.modules):
        if name.startswith("_gt"):
            del sys.modules[name]
    nb = types.ModuleType("nonebot"); nbl = types.ModuleType("nonebot.log")
    nbl.logger = _L(); nb.log = nbl
    sys.modules["nonebot"] = nb; sys.modules["nonebot.log"] = nbl
    httpx = types.ModuleType("httpx")
    for n in ("HTTPError", "HTTPStatusError", "RequestError", "TimeoutException"):
        setattr(httpx, n, type(n, (Exception,), {}))
    httpx.AsyncClient = object
    sys.modules["httpx"] = httpx
    GT = _CARD.parent
    pkg = types.ModuleType("gt"); pkg.__path__ = [str(GT)]; sys.modules["gt"] = pkg

    def load(n):
        s = importlib.util.spec_from_file_location("gt." + n, GT / (n + ".py"))
        m = importlib.util.module_from_spec(s); sys.modules["gt." + n] = m
        s.loader.exec_module(m); return m

    load("warframe")  # 真 warframe：_load_raw_weapons 读本地 json
    rc = load("riven_card")
    w = rc._weapon_raw("Coda Motovore")
    ok(w is not None, "B0 真武器面板 Coda Motovore 可加载")

    parsed = {"type": "Melee", "disp": 1.2, "rank": 8,
              "stats": [dict(s) for s in _TGT_STATS]}
    tb = rc._theoretical_best_stats(parsed, w)
    ok(len(tb) >= 3, "B1 理论最优组合非空且含正槽", "n=%d" % len(tb))
    ok(sum(1 for s in tb if s["sign"] != "-") >= 3, "B2 正槽 3 个")
    ok(any(s["sign"] == "-" for s in tb), "B3 含 1 零影响负槽（还原 3正1负布局）")
    neg = [s for s in tb if s["sign"] == "-"]
    ok(neg and neg[0]["en"] in rc._ZERO_IMPACT_NEG,
       "B4 负槽是零影响词条", neg[0]["en"] if neg else "-")

    sim = rc._sim(parsed, w); sc = rc._scores(parsed, sim)
    ok(sc["gain_max"] > sc["gain"] * 1.5,
       "B5 gain_max 显著大于 gain（不再≈当前）",
       "gain=%.1f%% gain_max=%.1f%%" % (sc["gain"] * 100, sc["gain_max"] * 100))
    ok(sc["dmg"] < 40, "B6 伤害分落到低档（不再虚高 100）", "dmg=%.1f" % sc["dmg"])
    ok(rc._letter(sc["dmg"]) in ("E", "D", "F"),
       "B7 伤害分评级为低档", rc._letter(sc["dmg"]))
    ok(abs(sc["total"] - max(sc["dmg"], sc["mkt"])) < 1e-6,
       "B8 综合分 = max(伤害,市场)", "total=%.1f" % sc["total"])
    ok(sim["ke_best"] > sim["ke_cur"] > sim["ke_base"],
       "B9 击杀效率 无紫卡<当前<最强", "%.1f/%.1f/%.1f" % (
           sim["ke_base"], sim["ke_cur"], sim["ke_best"]))

    # B10 与 Rabbit 定性对齐（不逐值，因靶子/配卡口径不同，模块 docstring 已声明）
    ok(11.0 <= sc["dmg"] <= 30.0, "B10 伤害分落在 Rabbit 图2(11.0)同量级区间",
       "ours=%.1f rabbit=11.0" % sc["dmg"])


def section_c_injection():
    print("== C. 注入反证：旧实现必须被守门判据抓住 ==")
    src = _CARD.read_text(encoding="utf-8")
    tree = ast.parse(src)
    f_sim = _fn(tree, "_sim")

    # C1 注入「_sim 退回旧 best（当前4条洗满）」→ B5/B6 必须失效
    inj = ast.parse(src)
    inj_sim = _fn(inj, "_sim")
    # 把 `_theoretical_best_stats(parsed, w)` 调用结果改成 []（强制走兜底旧语义）
    found = False
    for n in ast.walk(inj_sim):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
                and isinstance(n.value.func, ast.Name) \
                and n.value.func.id == "_theoretical_best_stats":
            n.value = ast.Constant(value=[])
            found = True
            break
    ok(found, "C1 注入 _best=[] 强制旧语义（反证样本构造）")
    inj_src = ast.unparse(inj_sim)
    ok("_theoretical_best_stats" not in inj_src,
       "C2 注入后 _sim 不再调用理论最优组合（判据可抓）")

    # C3 当前真源码确有该调用 → 守门有效
    real_src = ast.get_source_segment(src, f_sim) or ""
    ok("_theoretical_best_stats" in real_src, "C3 当前真源码有理论最优组合调用（守门有效）")

    # C4 注入 total=dmg_score（旧口径）→ A9 判据必须抓到
    inj2 = ast.parse(src)
    inj_sc = _fn(inj2, "_scores")
    replaced = False
    for n in ast.walk(inj_sc):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "total" for t in n.targets):
            n.value = ast.Name(id="dmg_score", ctx=ast.Load())
            replaced = True
            break
    ok(replaced, "C4 注入 total=dmg_score（旧口径反证）")
    ok("max(dmg_score, mkt)" not in ast.unparse(inj_sc),
       "C5 注入后 max 口径消失（A9 判据可抓）")


if __name__ == "__main__":
    print("== 模块D. 最强紫卡=gain_max 基准 + 综合评分口径守门 ==")
    section_a_static()
    print()
    section_b_behavior()
    print()
    section_c_injection()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
