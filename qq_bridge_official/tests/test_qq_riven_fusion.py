# -*- coding: utf-8 -*-
"""守门：紫卡「融合词条分析」只列官方候选属性、零编造继承数值（官方副本，2026-11-11）。

事实源边界（koishi `riven-data.md:76` + DE Public Export）：
  * 官方只给出「哪些属性属于融合属性」（18 种 splicedNames），**未公开融合数值继承算法**；
  * 因此本项目融合表只做：候选属性名（来自官方真表）+ 数值沿用当前词条真实落点，
    每行标「（参考）」、卡面 hint/脚注声明「继承方式未验证」；
  * 绝不写死图2 Rabbit 的「连击持续时间=重击准备速度」这类**未经验证的社区因果配方**，
    绝不编造融合溢价/市价/继承数值。

图2 Rabbit 融合结构：可融合/可锁定状态 + 候选融合结果 + 词条数值(约X%/偏差/三刻度)。
本项目对齐该结构，但把「确定的因果配方」降级为「候选属性 + 参考」。

锚定 official 副本根（parents[1]）。纯 AST + 隔离装配 riven_full，宿主可跑。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DATA = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_full.py"
_KOISHI_ADAPTER = _REPO / "data" / "warframe_items" / "koishi_ref" / "riven-export-adapter.ts"

_P = _F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-52s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-52s %s" % (name, detail))


def _load_full():
    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def debug(self, *a, **k): pass
        def error(self, *a, **k): pass
        def exception(self, *a, **k): pass
    nb = types.ModuleType("nonebot"); nbl = types.ModuleType("nonebot.log")
    nbl.logger = _L(); nb.log = nbl
    sys.modules.setdefault("nonebot", nb); sys.modules.setdefault("nonebot.log", nbl)
    pkg = types.ModuleType("gt"); pkg.__path__ = [str(_DATA.parent)]; sys.modules["gt"] = pkg
    spec = importlib.util.spec_from_file_location("gt.riven_full", _DATA)
    m = importlib.util.module_from_spec(spec); sys.modules["gt.riven_full"] = m
    spec.loader.exec_module(m)
    return m


def _fn(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _code_strings(node):
    """执行代码里的字符串常量，排除各节点 docstring（说明文字提到「=重击」不算写死配方）。"""
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


_STATS = [
    {"name": "暴击几率", "value": "+222.0%", "dev": "高9.6%", "bar": 0.96,
     "low": "182.2%", "mean": "202.5%", "high": "222.8%", "neg": False},
    {"name": "冲击伤害", "value": "+146.4%", "dev": "高8.7%", "bar": 0.87,
     "low": "121.2%", "mean": "134.7%", "high": "148.1%", "neg": False},
    {"name": "近战连击效率", "value": "+90.4%", "dev": "高9.4%", "bar": 0.94,
     "low": "74.4%", "mean": "82.6%", "high": "90.9%", "neg": False},
    {"name": "滑行攻击时暴击率", "value": "-109.3%", "dev": "高1.2%", "bar": 0.12,
     "low": "97.2%", "mean": "108.0%", "high": "118.8%", "neg": True},
]


def section_a_static():
    print("== A. 静态纪律：候选属性来自官方真表、零编造因果/溢价 ==")
    src = _DATA.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # A1 融合属性清单常量存在且带 koishi 事实源注释
    ok("_FUSION_ATTRS" in src, "A1 存在官方融合属性清单 _FUSION_ATTRS")
    ok("riven-export-adapter.ts" in src and "splicedNames" in src,
       "A2 融合属性清单标注 koishi/官方导出事实源")

    # A3 清单来自 koishi splicedNames（核对中文名交集，防漂移）
    koishi = _KOISHI_ADAPTER.read_text(encoding="utf-8")
    m = _load_full()
    overlap = [x for x in m._FUSION_ATTRS if x in koishi]
    ok(len(overlap) >= 10, "A3 融合属性清单与 koishi splicedNames 真表一致",
       "overlap=%d/%d" % (len(overlap), len(m._FUSION_ATTRS)))

    # A4 诚实声明：官方未公开继承方式 / 仅候选 / 谨慎参考，至少出现在 2 处（hint+脚注/降级）
    honesty_hits = sum(1 for kw in ("继承方式", "未公开", "仅列候选", "候选属性")
                       if kw in src)
    ok(honesty_hits >= 2, "A4 卡面/脚注含「继承方式未公开·仅候选」声明",
       "hits=%d" % honesty_hits)

    # A5 执行代码不含写死的社区因果配方（X=Y / 组合待洗 / 融合溢价数字）
    # 只看执行代码字符串，排除 docstring 里「不写死 X=Y」这类说明文字（子串会误判）
    f_fn = _fn(tree, "_fusion_from_stats")
    fn_src = ast.get_source_segment(src, f_fn) or ""
    code_strs = _code_strings(f_fn)
    bad = [kw for kw in ("组合待洗", "=重击", "=收武器", "溢价", "融合市价")
           if any(kw in s for s in code_strs)]
    ok(not bad, "A5 _fusion_from_stats 执行代码无写死因果配方/溢价", "bad=%s" % bad)

    # A6 数值必须沿用该词条真落点（ticks=value/dev/bar 都来自入参 s，不新算）
    ok('"ticks": [s["low"], s["mean"], s["high"]]' in fn_src,
       "A6 ticks 沿用当前词条真区间")
    ok('"value": "约" + str(s["value"])' in fn_src,
       "A7 数值沿用当前词条真落点（前缀约）")

    # A8 每行带（参考）标签
    ok("（参考）" in fn_src, "A8 候选结果带（参考）标签")


def section_b_behavior():
    print("== B. 真行为：融合表结构对齐图2、数值真、标注全 ==")
    m = _load_full()
    rows = m._fusion_from_stats(_STATS)
    ok(len(rows) == 4, "B1 四词条→四融合行", "n=%d" % len(rows))

    # 状态结构对齐图2：首行可融合，其余可锁定
    ok(rows[0]["state"] == "可融合", "B2 首行=可融合")
    ok(all(r["state"] == "可锁定" for r in rows[1:]), "B3 其余行=可锁定")

    # pair = 当前词条真名，颜色随正负
    ok([r["pair"] for r in rows] == [s["name"] for s in _STATS],
       "B4 pair 逐行=当前词条真名")
    ok(rows[-1]["pair_color"] == "red" and rows[0]["pair_color"] == "gray",
       "B5 负词条红/正词条灰（对齐图2）")

    # 候选属性来自官方真表、带候选/参考、无确定因果
    for i, r in enumerate(rows):
        cand = [t for t, c in r["result_parts"] if c == "purple"]
        ok(len(cand) == 1 and cand[0] in m._FUSION_ATTRS,
           "B6.%d 候选属性来自官方真表" % i, cand[0] if cand else "-")
        ok(any("参考" in t for t, _ in r["result_parts"]),
           "B7.%d 候选带（参考）标注" % i)

    # 数值全沿用真落点，不编造
    for i, (r, s) in enumerate(zip(rows, _STATS)):
        ok(r["ticks"] == [s["low"], s["mean"], s["high"]],
           "B8.%d ticks 沿用真落点" % i)
        ok(r["value"] == "约" + s["value"], "B9.%d value=约+真值" % i, r["value"])
        ok(r["dev"] == s["dev"] and r["bar"] == s["bar"],
           "B10.%d dev/bar 沿用真值" % i)

    # 脚注/降级路径也带诚实声明
    ok("继承方式官方未公开" in m._FUSION_REF_NOTE, "B11 融合脚注声明继承方式未公开")
    txt = m.full_text({"status": "ok", "fusion_rows": rows})
    ok("继承方式官方未公开" in txt, "B12 纯文本降级路径也带融合诚实声明")


def section_c_injection():
    print("== C. 注入反证：编造继承数值/去掉参考标注必须被抓住 ==")
    src = _DATA.read_text(encoding="utf-8")
    tree = ast.parse(src)
    f_fn = _fn(tree, "_fusion_from_stats")
    fn_src = ast.get_source_segment(src, f_fn) or ""

    # C1 注入「硬编码融合后数值」→ A6/B9 判据应抓到
    injected = fn_src.replace('"value": "约" + str(s["value"])',
                              '"value": "约250.0%"')
    ok(injected != fn_src and '"value": "约250.0%"' in injected,
       "C1 注入硬编码融合数值（反证样本）")
    ok('"value": "约" + str(s["value"])' not in injected,
       "C2 注入后真落点引用消失（A7 判据可抓）")
    ok('"value": "约" + str(s["value"])' in fn_src,
       "C3 当前真源码用真落点（守门有效）")

    # C4 当前真源码执行代码无社区因果配方（同样排除 docstring 说明文字）
    real_code = _code_strings(_fn(ast.parse(src), "_fusion_from_stats"))
    ok(not any(kw in s for s in real_code for kw in ("组合待洗", "=重击", "=收武器")),
       "C4 当前源码执行代码无写死因果配方（守门有效）")


if __name__ == "__main__":
    print("== 模块F. 融合表只列官方候选·零编造守门 ==")
    section_a_static()
    print()
    section_b_behavior()
    print()
    section_c_injection()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
