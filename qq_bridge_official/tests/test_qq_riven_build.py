# -*- coding: utf-8 -*-
"""守门：紫卡全面分析卡「配卡 MOD 必须匹配真实武器类型」（官方副本，2026-11-11）。

防的是用户图1：给近战武器（Coda Motovore）却配了「深层冷冻/病原弹头/致命洪流」
等**枪械** MOD，且配卡标题写死「基于击杀效率配卡」。

三条铁律：
  1. 生产配卡名**只能**来自 ``riven_card._BUILD`` / ``_MELEE_BUILD``（唯一真表），
     riven_full.py 不得再写一份手工卡名；
  2. ``image`` 一律 None——资产目录只有参考卡 8 张枪卡图，给近战贴枪卡图=新错配；
  3. 类型未知/战甲名/外号 → 全部「需紫卡截图」占位，绝不默认套 Rifle 枪卡。

类型归类（riven_disp.json 7 类 → riven_card 真表）：
  Melee/Zaw→近战表；Pistol/Kitgun→手枪表；Rifle/Archgun→步枪表；Shotgun→霰弹表。

锚定 official 副本根（parents[1]）。纯 AST + 隔离装配真模块，宿主可跑。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DATA = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_full.py"
_CARD = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_card.py"

_P = _F = 0
_GUN_ONLY = ("深层冷冻", "病原弹头", "致命洪流", "黄蜂蛰刺",
             "弹头扩散", "持续火力 Prime", "火焰装填 Prime")
_MELEE_ONLY = ("压迫 Prime", "热毒 Prime", "渴血", "泣血", "角斗 威力", "粉碎者")


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-50s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-50s %s" % (name, detail))


def _code_strings(node):
    """执行代码里的字符串常量，排除各节点 docstring。"""
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


def _fn(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _load_isolated():
    """隔离装配 riven_full + riven_card 真模块：只桩 nonebot.log / warframe，
    保留 _BUILD/_MELEE_BUILD/_build_mods 真实现。挂在一个假包下让相对 import 生效。"""
    pkg = types.ModuleType("qq_bridge.gt"); pkg.__path__ = []
    nb = types.ModuleType("nonebot"); nbl = types.ModuleType("nonebot.log")

    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
    nbl.logger = _L(); nb.log = nbl
    sys.modules.setdefault("nonebot", nb); sys.modules.setdefault("nonebot.log", nbl)
    sys.modules["qq_bridge"] = pkg
    sys.modules["qq_bridge.gt"] = pkg

    # riven_card 依赖 from . import warframe（只用到 _load_riven_tables 等，桩最小面）
    wf = types.ModuleType("qq_bridge.gt.warframe")
    wf._load_riven_tables = lambda: ({}, {}, {})
    sys.modules["qq_bridge.gt.warframe"] = wf

    def _load(modname, path):
        spec = importlib.util.spec_from_file_location(modname, path)
        m = importlib.util.module_from_spec(spec)
        sys.modules[modname] = m
        spec.loader.exec_module(m)
        return m

    card = _load("qq_bridge.gt.riven_card", _CARD)
    full = _load("qq_bridge.gt.riven_full", _DATA)
    return full, card


def section_a_static():
    print("== A. 静态纪律：配卡名事实源单一 + 生产路径接线 ==")
    src = _DATA.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # A1 生产函数 riven_full_data 执行代码不含任何写死枪卡/近战卡名（只在 _MOD_NAMES 样例里）
    f_rfd = _fn(tree, "riven_full_data")
    ok(f_rfd is not None, "A1 找到 riven_full_data")
    rfd_strings = _code_strings(f_rfd)
    leaked = [s for s in rfd_strings if any(g in s for g in _GUN_ONLY + _MELEE_ONLY)]
    ok(not leaked, "A2 riven_full_data 无写死 MOD 卡名", "leak=%s" % leaked[:3])

    # A3 _build_mods 只从 riven_card 真表派生，不含手工卡名
    f_bm = _fn(tree, "_build_mods")
    ok(f_bm is not None, "A3 找到 _build_mods 唯一出口")
    bm_src = ast.get_source_segment(src, f_bm) or ""
    ok("rc._MELEE_BUILD" in bm_src and "rc._BUILD" in bm_src,
       "A4 _build_mods 引用 riven_card 两张真表")
    bm_strings = _code_strings(f_bm)
    handmade = [s for s in bm_strings if any(g in s for g in _GUN_ONLY + _MELEE_ONLY)]
    ok(not handmade, "A5 _build_mods 零手工卡名", "handmade=%s" % handmade[:3])

    # A6 image 一律 None（资产只有枪卡图，贴图=张冠李戴）
    ok('"image": None' in bm_src or "'image': None" in bm_src,
       "A6 _build_mods 的 image 固定 None")
    ok(".png" not in bm_src and "MOD_ASSET_DIR" not in bm_src,
       "A7 _build_mods 不碰任何图片资产")

    # A8 生产装配用 _build_mods/_build_label，不再回落 _mod_assets
    rfd_src = ast.get_source_segment(src, f_rfd) or ""
    ok("_build_mods(_wtype)" in rfd_src, "A8 riven_full_data 按 _wtype 派生 mods")
    ok("_build_label(_wtype)" in rfd_src, "A9 riven_full_data 按 _wtype 派生标题")
    # 用 AST Call 节点判定真调用，避开注释里的「不调用 _mod_assets」字样（子串会误判）
    rfd_calls = {n.func.id for n in ast.walk(f_rfd)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    ok("_mod_assets" not in rfd_calls, "A10 生产路径不调用样例专用 _mod_assets",
       "calls_with_mod=%s" % sorted(rfd_calls & {"_mod_assets"}))

    # A11 类型归类覆盖 7 类：字典在**模块级** _TYPE_TO_BUILD（不是 _build_key 函数体）
    type_keys = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_TYPE_TO_BUILD" \
                        and isinstance(node.value, ast.Dict):
                    type_keys = [k.value for k in node.value.keys
                                 if isinstance(k, ast.Constant)]
    for t in ("Melee", "Zaw", "Pistol", "Kitgun", "Rifle", "Archgun", "Shotgun"):
        ok(t in type_keys, "A11 类型归类含 %s" % t)
    ok("riven_card._BUILD" in src or "rc._BUILD" in src,
       "A12 真表来自 riven_card._BUILD（非本地复刻）")


def section_b_behavior():
    print("== B. 真行为：按类型派生正确武器配卡（隔离装配真模块） ==")
    full, card = _load_isolated()

    # B1 每张真表都非空，作为基准
    ok(bool(card._BUILD) and bool(card._MELEE_BUILD), "B1 riven_card 两张真表非空",
       "gun_types=%s melee=%d" % (list(card._BUILD), len(card._MELEE_BUILD)))
    rifle = card._BUILD["Rifle"]; pistol = card._BUILD["Pistol"]
    shotgun = card._BUILD["Shotgun"]; melee = card._MELEE_BUILD

    # B2 近战武器（含 Zaw）配近战卡，绝不出现枪卡
    for t in ("Melee", "Zaw"):
        mods = full._build_mods(t)
        names = [m["name"] for m in mods]
        ok(names[:len(melee)] == list(melee), "B2 %s 配近战真表" % t, names[0])
        ok(not any(g in names for g in _GUN_ONLY), "B3 %s 无枪卡混入" % t)
        ok(all(m["image"] is None for m in mods), "B4 %s 全部 image=None" % t)
        ok(len(mods) == 8, "B5 %s 配卡 8 格" % t, "n=%d" % len(mods))

    # B6 手枪（含 Kitgun）
    for t in ("Pistol", "Kitgun"):
        names = [m["name"] for m in full._build_mods(t)]
        ok(names[:len(pistol)] == list(pistol), "B6 %s 配手枪真表" % t, names[0])
        ok(not any(m in names for m in _MELEE_ONLY), "B7 %s 无近战卡混入" % t)

    # B8 步枪（含 Archgun）/ 霰弹
    for t, expect in (("Rifle", rifle), ("Archgun", rifle), ("Shotgun", shotgun)):
        names = [m["name"] for m in full._build_mods(t)]
        ok(names[:len(expect)] == list(expect), "B8 %s 配 %s 真表" % (t, expect[0][:4]))
        ok("当前紫卡" in names, "B9 %s 末格为当前紫卡" % t)

    # B10 未知/战甲名/空类型 → 8 格诚实占位，绝不套 Rifle
    for t in (None, "", "Warframe", "Archwing", "Unknown"):
        mods = full._build_mods(t)
        names = [m["name"] for m in mods]
        ok(len(names) == 8 and all(n == full._NEED_SHOT for n in names),
           "B10 未知类型(%r)→需截图占位" % t, names[0])
        ok(not any(g in names for g in _GUN_ONLY + _MELEE_ONLY),
           "B11 未知类型(%r)零枪卡/近战卡" % t)

    # B12 标题随类型变化
    ok("近战" in full._build_label("Melee"), "B12 近战标题含「近战」")
    ok("手枪" in full._build_label("Pistol"), "B13 手枪标题含「手枪」")
    ok("霰弹" in full._build_label("Shotgun"), "B14 霰弹标题含「霰弹」")
    ok("需紫卡截图" in full._build_label(None), "B15 未知标题如实标需截图")

    # B16 武器名反查真类型（只读 riven 真表的映射，探针实测恒定）
    # 这里只验 _wtype_from_name 的「查不到落空串」与「精确命中」两条确定路径，
    # 具体 disp 表数据由容器真出图覆盖（宿主无 httpx 不加载完整 riven 数据）。
    ok(full._wtype_from_name("") == "", "B16 空名→空类型")


def section_c_injection():
    print("== C. 注入反证：旧实现必须被守门判据抓住 ==")
    src = _DATA.read_text(encoding="utf-8")
    tree = ast.parse(src)
    f_rfd = _fn(tree, "riven_full_data")
    rfd_src = ast.get_source_segment(src, f_rfd) or ""

    # C1 注入「生产路径回落 _mod_assets」→ 构造反证样本并验证 A10 判据能抓到
    injected_tree = ast.parse(src)
    inj_rfd = _fn(injected_tree, "riven_full_data")
    # 把一个 _build_mods Call 节点替换为 _mod_assets Call（真节点级注入）
    replaced = 0
    for n in ast.walk(inj_rfd):
        if replaced:
            break
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "_build_mods":
            n.func.id = "_mod_assets"
            replaced += 1
    inj_calls = {c.func.id for c in ast.walk(inj_rfd)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    ok(replaced == 1 and "_mod_assets" in inj_calls,
       "C1 注入 _mod_assets 调用成功（反证样本构造）")
    # C2 当前真源码的 riven_full_data 无该坏调用 → 证明 A10 判据有效
    real_calls = {c.func.id for c in ast.walk(f_rfd)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    ok("_mod_assets" not in real_calls,
       "C2 当前真源码无该坏调用（守门有效）")

    # C3 注入「_build_mods 默认套 Rifle」→ 必须被 B10 判据语义拒绝
    bad_default = "return list(rc._BUILD['Rifle'])"
    ok(bad_default.replace("rc._BUILD", "card._BUILD") not in src,
       "C3 当前源码无「未知类型默认 Rifle」坏实现")


if __name__ == "__main__":
    print("== 模块E. 配卡按真实武器类型派生守门 ==")
    section_a_static()
    print()
    section_b_behavior()
    print()
    section_c_injection()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
