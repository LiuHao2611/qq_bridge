# -*- coding: utf-8 -*-
"""守门：WFM 紫卡拍卖 slug 必须走**下划线族名**权威表（官方副本，2026-11-12）。

防的是用户图2 Coda Motovore 市场两盒空：`_weapon_slug` 旧实现走
``warframe._resolve_riven_weapon`` 拼出**连字符** slug（``coda-motovore``），
而 WFM ``/v1/auctions/search`` 的 ``weapon_url_name`` 是**下划线族名**
（``coda_motovore``）——连字符形态恒定 ``400 item_not_exist``（真网络矩阵坐实：
同参数 ``rubico``/``coda_motovore`` 200/≈107KB、``coda-motovore`` 400）。

修法纪律（本守门要钉死的）：
  1. ``_weapon_slug`` 唯一权威源 = ``riven_trend.resolve_weapon``，它读既有
     ``data/warframe_items/riven_wfm_slugs.json``（414 条 en→下划线 slug）；
  2. 未命中才退回旧逻辑，且**必须**把连字符归一为下划线；
  3. **通用、零特例**：源码执行代码不得出现任何单把武器名 / 写死 slug；
  4. 输出 slug 必须匹配 ``[a-z0-9_]+``（无连字符），否则 WFM 恒定 400。

静态段用 AST + 真 JSON 事实源；行为段隔离装配真模块（宿主缺 httpx，不能全链
import，故桩 nonebot.log / warframe 最小面，保留 _weapon_slug / resolve_weapon
真实现）。容器真出图覆盖中文 OCR 全链路。

锚定 official 副本根（parents[1]）。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_GT = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools"
_CARD = _GT / "riven_card.py"
_TREND = _GT / "riven_trend.py"
_SLUGS = _REPO / "data" / "warframe_items" / "riven_wfm_slugs.json"
_DISP = _REPO / "data" / "warframe_items" / "riven_disp.json"

_P = _F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-52s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-52s %s" % (name, detail))


def _fn(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _doc_ids(node):
    ids = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                ids.add(id(b[0].value))
    return ids


def _code_strings(node):
    di = _doc_ids(node)
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in di}


def section_a_static():
    print("== A. 静态纪律：下划线族名事实源 + 通用无特例 ==")
    src = _CARD.read_text(encoding="utf-8")
    tree = ast.parse(src)
    f = _fn(tree, "_weapon_slug")
    ok(f is not None, "A1 找到 _weapon_slug 唯一出口")
    fsrc = ast.get_source_segment(src, f) or ""

    # A2 用 riven_trend.resolve_weapon 作权威源
    ok("riven_trend" in fsrc and "resolve_weapon" in fsrc,
       "A2 _weapon_slug 调用 riven_trend.resolve_weapon")
    calls = {c.func.attr for c in ast.walk(f)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
    ok("resolve_weapon" in calls, "A3 AST 真调用 resolve_weapon",
       "attrs=%s" % sorted(calls))

    # A4 兜底把连字符归一为下划线
    ok('"-"' in fsrc and '"_"' in fsrc and "replace" in fsrc,
       "A4 兜底做连字符→下划线归一")
    # A5 输出只认 [a-z0-9_]+（无连字符）
    ok('[a-z0-9_]+' in fsrc.replace(" ", ""),
       "A5 输出 slug 正则排除连字符")
    # A6 通用无特例：执行代码不含任何单把武器名 / 写死目标 slug
    strs = _code_strings(f)
    hard = [s for s in strs if "coda" in s.lower() or "motovore" in s.lower()
            or "rubico" in s.lower() or "终幕" in s]
    ok(not hard, "A6 执行代码零硬编码武器名/slug", "hard=%r" % hard[:4])

    # A7 事实源：riven_wfm_slugs.json 存在、414 条、全下划线（0 连字符）
    slugs = json.loads(_SLUGS.read_text(encoding="utf-8"))
    ok(isinstance(slugs, dict) and len(slugs) >= 400,
       "A7 riven_wfm_slugs 事实源齐备", "n=%d" % len(slugs))
    hyphen = [k for k in slugs if "-" in k]
    ok(not hyphen, "A8 事实源 slug 全下划线（0 连字符）", "hyphen=%d" % len(hyphen))
    ok(slugs.get("coda_motovore") == "Coda Motovore",
       "A9 图2 武器在事实源（coda_motovore）")
    # A10 事实源绝大多数键匹配 [a-z0-9_]+（个别 WFM 遗留键含括号，如
    #      dark_split_sword_(dual_swords)，属 WFM 自身数据，_weapon_slug 正则会拒→走兜底）
    clean = [k for k in slugs if re.fullmatch(r"[a-z0-9_]+", k)]
    ok(len(clean) / len(slugs) >= 0.99,
       "A10 事实源 ≥99% 键为纯下划线规范",
       "clean=%d/%d bad=%r" % (len(clean), len(slugs),
                               [k for k in slugs if k not in clean][:3]))


def _load_isolated():
    """隔离装配 riven_trend + riven_card 真模块（桩 nonebot/配置/warframe 最小面）。

    warframe 桩提供：_resolve_weapon（用 riven_disp.json 做等价 zh/en→官方 en）、
    _weapon_zh（disp zh 反查）、MARKET_BASE、_fetch/_fetch_auction_page 等（市场
    真请求由容器覆盖，这里只为模块能加载）；riven_card 依赖 _load_riven_tables。
    """
    pkg = types.ModuleType("qq_bridge.gt"); pkg.__path__ = []
    nb = types.ModuleType("nonebot"); nbl = types.ModuleType("nonebot.log")

    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
        def debug(self, *a, **k): pass
    nbl.logger = _L(); nb.log = nbl
    sys.modules.setdefault("nonebot", nb); sys.modules.setdefault("nonebot.log", nbl)
    sys.modules["qq_bridge"] = pkg
    sys.modules["qq_bridge.gt"] = pkg
    cfg = types.ModuleType("qq_bridge.config")
    cfg.get_logger = lambda: _L()
    cfg.jlog = lambda *a, **k: ""
    sys.modules["qq_bridge.config"] = cfg

    disp = json.loads(_DISP.read_text(encoding="utf-8"))
    # 保留 CJK：中文武器名要能 norm 后精确命中 disp 的 zh（生产 _resolve_weapon
    # 也是中英文都接受的，桩必须等价，否则纯中文用例会假 FAIL）
    norm = lambda s: re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (s or "").lower())
    by_norm = {}
    for en, rec in disp.items():
        by_norm.setdefault(norm(en), en)
        if isinstance(rec, dict) and rec.get("zh"):
            by_norm.setdefault(norm(rec["zh"]), en)

    wf = types.ModuleType("qq_bridge.gt.warframe")
    wf.MARKET_BASE = "https://api.warframe.market"
    wf._resolve_weapon = lambda q: ({"en": by_norm[norm(q)]}
                                     if norm(q) in by_norm else None)
    # 兜底用（_weapon_slug 在 resolve_weapon 未命中时调它）：产出连字符 slug，
    # 用于验证「归一为下划线」这条兜底路径。生产是 disp/物品库 slug。
    wf._resolve_riven_weapon = lambda q: by_norm.get(norm(q), "").lower().replace(" ", "-")
    wf._weapon_zh = lambda en: next(
        (r.get("zh") for k, r in disp.items() if isinstance(r, dict)
         and k == en and r.get("zh")), "")
    wf._load_riven_tables = lambda: ({}, {}, {})
    wf._fetch = lambda *a, **k: None
    async def _noop(*a, **k):
        return None
    wf._fetch = _noop
    sys.modules["qq_bridge.gt.warframe"] = wf

    def _load(modname, path):
        spec = importlib.util.spec_from_file_location(modname, path)
        m = importlib.util.module_from_spec(spec)
        sys.modules[modname] = m
        spec.loader.exec_module(m)
        return m

    # 先 card 后 trend：riven_trend 顶层 `from . import riven_card as rc`
    card = _load("qq_bridge.gt.riven_card", _CARD)
    trend = _load("qq_bridge.gt.riven_trend", _TREND)
    return trend, card


def section_b_behavior():
    print("== B. 真行为：中英文武器 → 下划线族名（隔离装配真模块） ==")
    trend, card = _load_isolated()
    # resolve_weapon 真行为
    cases = [
        ("Coda Motovore", "coda_motovore"),
        ("终幕·噬轮", "coda_motovore"),
        ("Rubico Prime", "rubico"),
        ("Kuva Bramma", "kuva_bramma"),
        ("Acceltra", "acceltra"),
    ]
    for q, want in cases:
        got = (trend.resolve_weapon(q) or {}).get("slug")
        ok(got == want, "B resolve_weapon(%r)→%s" % (q, want), "got=%r" % got)
    # _weapon_slug 真行为（parsed 同时给 en + zh，生产 OCR 形态）
    for en, zh, want in [
        ("Coda Motovore", "终幕·噬轮", "coda_motovore"),
        ("Rubico Prime", "绝路", "rubico"),
        ("Kuva Bramma", "赤毒·布拉玛", "kuva_bramma"),
        ("Acceltra", "阿克里", "acceltra"),
    ]:
        got = card._weapon_slug({"weapon": en, "weapon_zh": zh})
        ok(got == want, "B _weapon_slug(%s)→%s" % (zh, want), "got=%r" % got)
        ok(not re.search(r"[-]", got or ""), "B _weapon_slug(%s) 无连字符" % zh,
           "got=%r" % got)
    # 只给中文（OCR 常见）：经 disp zh→en 桥接
    ok(card._weapon_slug({"weapon_zh": "终幕·噬轮"}) == "coda_motovore",
       "B 纯中文名桥接→coda_motovore")
    # 无法解析：显式报错，不返回中文/连字符
    try:
        bad = card._weapon_slug({"weapon": "", "weapon_zh": ""})
        ok(False, "B 空名显式报错", "got=%r" % bad)
    except Exception as e:
        ok("RivenMarketError" in type(e).__name__,
           "B 空名显式 RivenMarketError", type(e).__name__)


def section_c_injection():
    print("== C. 注入反证：连字符还原必须被守门判据抓到 ==")
    src = _CARD.read_text(encoding="utf-8")
    # C1 注入「去掉 replace 归一」→ A4 判据必须失效（证明 A4 不是摆设）
    injected = src.replace('slug = slug.replace("-", "_") if', 'slug = slug if')
    ok(('slug = slug if' in injected) and (injected != src),
       "C1 构造过去掉连字符归一的反证样本")
    f = _fn(ast.parse(injected), "_weapon_slug")
    inj_src = ast.get_source_segment(injected, f) or ""
    ok(not ('"-"' in inj_src and '"_"' in inj_src and "replace" in inj_src),
       "C2 反证样本被 A4 判据判为 FAIL（守门有效）")
    # C3 当前真源码 A4 判据必须通过
    real = _fn(ast.parse(src), "_weapon_slug")
    real_src = ast.get_source_segment(src, real) or ""
    ok('"-"' in real_src and '"_"' in real_src and "replace" in real_src,
       "C3 当前真源码通过 A4 判据")
    # C4 注入写死单把武器 slug → A6 判据必须抓到
    inj2 = src.replace("tried.append((q, (info or {}).get(\"slug\")))",
                       "tried.append((q, 'coda_motovore'))")
    f2 = _fn(ast.parse(inj2), "_weapon_slug")
    hard = [s for s in _code_strings(f2) if "coda" in s.lower() or "motovore" in s.lower()]
    ok("coda_motovore" in hard, "C4 注入写死 slug 被 A6 判据抓到", "hard=%r" % hard)


if __name__ == "__main__":
    print("== 模块. WFM 紫卡 slug 下划线族名守门 ==")
    section_a_static()
    print()
    section_b_behavior()
    print()
    section_c_injection()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
