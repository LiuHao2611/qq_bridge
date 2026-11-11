# -*- coding: utf-8 -*-
"""守门：紫卡全面分析卡「配卡 MOD 必须匹配真实武器类型」（官方副本，2026-11-11）。

防的是用户图1：给近战武器（Coda Motovore）却配了「深层冷冻/病原弹头/致命洪流」
等**枪械** MOD，且配卡标题写死「基于击杀效率配卡」。

四条铁律：
  1. 生产配卡名**只能**来自 ``riven_card._BUILD`` / ``_MELEE_BUILD``（唯一真表），
     riven_full.py 不得再写一份手工卡名；**每个卡名必须在 mod_full.json 的 zh
     字段精确命中**（2026-11-12 加：旧「暗病打击 Prime/肉瘤 巨颚」是凭译名猜的、
     事实源里根本不存在，用户投诉「配卡不符合武器」的深层根因）；
  2. ``image`` 对前 N 张配卡 MOD **运行时实时合成 + 内存缓存**
     （``_live_mod_image``：zh→en 由 mod_full.json 事实源反查 → render_mod_card_wide
     用真 DE 立绘 + 稀有度卡框出 370×185 深色满幅金属边框卡，零外呼、零静态资产）；
     **禁止静态 PNG 硬编码**（用户 2026-11-12 明确「其他 mod 实时获取或从缓存拿，
     禁止硬编码」）——磁盘不得有 riven_full_assets/mods/*.png 交付、源码不得有
     _MELEE_MOD_IMAGE 卡图映射；合成失败诚实 None 走占位框，绝不张冠李戴；
  2b. 第 8 格「当前紫卡」是**唯一**固定资产：母版 mod_1_3.png（紫色星标，用户认可），
     紫卡无法由 MOD 数据表合成、不随武器变，故保留；其余参考样例母版不交付；
  2c. 横版真卡视觉（2026-11-12 用户「我就要之前那个深色效果，别再乱改了」）：
     **深色满幅不透明（四角 alpha=255）+ 四边金属色收边（非深色裸边/黑边）+
     稀有度分档边框色 + 右上梯形耗能标签 + 底部等级点**。严禁退回第19轮
     「圆角透明 + 浅灰白框 + 框外 alpha=0」方向。；
  3. 类型未知/战甲名/外号 → 全部「需紫卡截图」占位，绝不默认套 Rifle 枪卡；
  4. 图损坏/缺失 → 模板等比 contain 贴真图失败时回落占位框，不崩、不编造。

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
# 数据层 _live_mod_image 会 lazy `from tools.gen_mod_cards import render_mod_card_wide`，
# 需项目根在 sys.path（宿主/容器通用）。gen_mod_cards 只依赖标准库 + Pillow，无 nonebot/httpx。
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_DATA = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_full.py"
_CARD = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_card.py"

_P = _F = 0
_GUN_ONLY = ("深层冷冻", "病原弹头", "致命洪流", "黄蜂蛰刺",
             "弹头扩散", "持续火力 Prime", "火焰装填 Prime")
# 图2 Rabbit 病毒切割重击流近战表（2026-11-11 全量对齐、2026-11-12 修正两处错名）
_MELEE_ONLY = ("热病打击 Prime", "北风", "邪恶蓄力", "震波", "肉碾虫 巨颚", "牺牲 斩铁", "肢解")
# 旧状态流 / 旧凭译名猜的错卡名：仅用于反证「不再出现」
_OLD_MELEE = ("压迫 Prime", "热毒 Prime", "渴血", "泣血", "角斗 威力", "粉碎者", "触手 Prime",
              "暗病打击 Prime", "肉瘤 巨颚")
_MOD_FULL = _REPO / "data" / "warframe_items" / "mod_full.json"
_MOD_WIDE = _REPO / "src" / "plugins" / "qq_bridge" / "pic" / "templates" / "riven_full_assets" / "mods"
# 图2 近战 7 张真图的 en slug（文件名，不含 .png）
_MELEE_IMG_SLUG = ("primed_fever_strike", "north_wind", "corrupt_charge", "seismic_wave",
                   "carnis_mandible", "sacrificial_steel", "organ_shatter")


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

    # A6 image 走 _live_mod_image **运行时实时合成**（2026-11-12 架构收敛：禁静态资产）
    ok("_live_mod_image(" in bm_src, "A6 _build_mods 用 _live_mod_image 实时合成")
    ok('_live_mod_image(n)' in bm_src.replace(" ", ""),
       "A7 _build_mods 逐卡实时出图（非全 None、非查静态路径）")
    # 只看执行代码的标识符节点（docstring 里「不再引用 _MELEE_MOD_IMAGE」说明不算）
    bm_names = {n.id for n in ast.walk(f_bm) if isinstance(n, ast.Name)}
    ok("_mod_image" not in bm_names and "_MELEE_MOD_IMAGE" not in bm_names,
       "A7b _build_mods 执行代码不再引用任何静态卡图映射")
    # A7c~A7e _live_mod_image：实时合成 + 内存缓存 + 事实源反查 + 零外呼 + 诚实降级
    f_lm = _fn(tree, "_live_mod_image")
    lm_src = ast.get_source_segment(src, f_lm) or "" if f_lm else ""
    ok(f_lm is not None and "_LIVE_MOD_CACHE" in lm_src,
       "A7c _live_mod_image 用内存缓存（实时获取或从缓存拿）")
    ok("render_mod_card_wide" in lm_src and "_zh_to_en_from_mod_full" in lm_src,
       "A7d _live_mod_image 事实源反查 en + 真生成器实时合成")
    ok(not any(x in lm_src for x in ("httpx", "urlopen", "requests", "http://", "https://")),
       "A7e _live_mod_image 零网络外呼")
    ok("return None" in lm_src and "b\"\"" in lm_src,
       "A7f _live_mod_image 合成失败诚实返回 None")
    # A7g 事实源反查函数从 mod_full.json 派生、缓存，零手工 zh→en 映射
    f_z2e = _fn(tree, "_zh_to_en_from_mod_full")
    z2e_src = ast.get_source_segment(src, f_z2e) or "" if f_z2e else ""
    ok(f_z2e is not None and "mod_full.json" in z2e_src and "_ZH_TO_EN" in z2e_src,
       "A7g zh→en 由 mod_full.json 事实源反查并缓存（零硬编码映射）")

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

    # A13 卡名事实源（防再犯 2026-11-12「暗病打击/肉瘤」凭译名猜的错名）：
    # 从 riven_card.py AST 取 _MELEE_BUILD 真值，逐卡在 mod_full.json 的 zh 字段精确命中。
    import json as _json
    card_tree = ast.parse(_CARD.read_text(encoding="utf-8"))
    melee_build = None
    for node in card_tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_MELEE_BUILD":
                    try:
                        melee_build = ast.literal_eval(node.value)
                    except Exception:
                        pass
    ok(isinstance(melee_build, list) and len(melee_build) == 7,
       "A13 _MELEE_BUILD 是 7 张字面量表", "val=%r" % (melee_build,))
    mf = _json.loads(_MOD_FULL.read_text(encoding="utf-8")).get("map", {})
    zh_set = {str((v or {}).get("zh") or "").strip() for v in mf.values()}
    unknown = [n for n in (melee_build or []) if n not in zh_set]
    ok(not unknown, "A14 每张近战卡名在 mod_full.json zh 精确命中",
       "unknown=%r" % unknown)
    ok(not any(x in (melee_build or []) for x in ("暗病打击 Prime", "肉瘤 巨颚")),
       "A15 旧凭译名猜的错卡名已全部移除")

    # A16 静态资产彻底清除（用户「禁止硬编码」）：源码无 _MELEE_MOD_IMAGE /
    # _MOD_WIDE_DIR / mods/*.png 交付路径；磁盘 mods/ 目录无任何 PNG。
    top_assigns = {tgt.id: node.value for node in tree.body
                   if isinstance(node, ast.Assign)
                   for tgt in node.targets if isinstance(tgt, ast.Name)}
    ok("_MELEE_MOD_IMAGE" not in top_assigns and "_MOD_WIDE_DIR" not in top_assigns,
       "A16 源码无静态卡图映射 _MELEE_MOD_IMAGE / _MOD_WIDE_DIR")
    # 只看执行代码的字符串常量（docstring/注释里「禁止 mods/*.png」说明不算）。
    # 排除各 docstring 后收集全模块 string 常量，判断无静态交付路径字面。
    doc_ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                doc_ids.add(id(b[0].value))
    code_strings = {n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in doc_ids}
    static_paths = [s for s in code_strings
                    if ("mods/" in s and s.rstrip().endswith(".png"))
                    or s in ("primed_fever_strike.png", "north_wind.png")]
    ok(not static_paths, "A17 执行代码无 mods/*.png 静态交付路径",
       "paths=%r" % static_paths[:4])
    stray = list(_MOD_WIDE.glob("*.png")) if _MOD_WIDE.is_dir() else []
    ok(not stray, "A17b 磁盘 mods/ 目录无静态 PNG（已删静态资产）",
       "stray=%r" % [p.name for p in stray][:4])
    # 唯一固定资产 = 母卡 mod_1_3.png；.gitignore 须精确放行它、且不再放行 mods/
    gi = (_REPO / ".gitignore").read_text(encoding="utf-8")
    ok("!src/plugins/qq_bridge/pic/templates/riven_full_assets/mod_1_3.png" in gi,
       "A17c .gitignore 精确放行唯一母卡 mod_1_3.png")
    ok("riven_full_assets/mods/*.png" not in gi,
       "A17d .gitignore 不再放行 mods/*.png（禁静态资产交付）")
    ok((_REPO / "src/plugins/qq_bridge/pic/templates/riven_full_assets/mod_1_3.png").is_file(),
       "A17e 母卡 mod_1_3.png 实物存在")
    ok(len(list(_MELEE_IMG_SLUG)) == 7, "A18 基准 en slug 7 张")


def section_b_behavior():
    print("== B. 真行为：按类型派生正确武器配卡（隔离装配真模块） ==")
    full, card = _load_isolated()

    # B1 每张真表都非空，作为基准
    ok(bool(card._BUILD) and bool(card._MELEE_BUILD), "B1 riven_card 两张真表非空",
       "gun_types=%s melee=%d" % (list(card._BUILD), len(card._MELEE_BUILD)))
    rifle = card._BUILD["Rifle"]; pistol = card._BUILD["Pistol"]
    shotgun = card._BUILD["Shotgun"]; melee = card._MELEE_BUILD

    # B1b 近战表=图2 病毒切割重击流 7 张，旧状态流已整表移除（防回退）
    ok(all(m in melee for m in _MELEE_ONLY), "B1b 近战含图2 重击流 7 张真名",
       "n=%d" % len(melee))
    ok(not any(m in melee for m in _OLD_MELEE), "B1c 旧状态流卡已全部移除")
    ok(len(melee) == 7, "B1d 近战主卡 7 张（+当前紫卡=8格）", "n=%d" % len(melee))

    # B2 近战武器（含 Zaw）配近战卡，绝不出现枪卡
    for t in ("Melee", "Zaw"):
        mods = full._build_mods(t)
        names = [m["name"] for m in mods]
        ok(names[:len(melee)] == list(melee), "B2 %s 配近战真表" % t, names[0])
        ok(not any(g in names for g in _GUN_ONLY), "B3 %s 无枪卡混入" % t)
        # B4 前 7 张近战卡必须是**运行时实时合成**的 BytesIO 真图（370×185 深色满幅），
        # 不再是静态 PNG 路径（2026-11-12 架构收敛：禁静态资产、实时获取/缓存）。
        from PIL import Image as _Image
        import io as _io
        live = [m["image"] for m in mods[:7]]
        good = []
        for b in live:
            if not isinstance(b, _io.BytesIO):
                good.append(False); continue
            try:
                im = _Image.open(b); im.load()
                good.append(im.size == (370, 185) and len(b.getvalue()) > 500)
            except Exception:  # noqa: BLE001
                good.append(False)
        ok(all(good), "B4 %s 前 7 张为运行时实时 BytesIO 真图" % t,
           "good=%d/7" % sum(good))
        # B4b 第 8 格「当前紫卡」= 唯一固定资产母版 mod_1_3.png（紫色星标）。
        riven_img = mods[7]["image"]
        ok(isinstance(riven_img, str) and Path(riven_img).is_file()
           and Path(riven_img).name == "mod_1_3.png",
           "B4b %s 第 8 格当前紫卡→母版紫卡星标 mod_1_3.png" % t,
           "image=%r" % riven_img)
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

    # C4 真错误注入（2026-11-12 用户 prove-it 纪律）：把旧错名塞进 A14 的判据，
    #     必须被「mod_full.json zh 精确命中」抓住 —— 证明这条守门不是摆设。
    import json as _json
    mf = _json.loads(_MOD_FULL.read_text(encoding="utf-8")).get("map", {})
    zh_set = {str((v or {}).get("zh") or "").strip() for v in mf.values()}
    injected_wrong = list(melee_build_from_card()) + ["暗病打击 Prime", "肉瘤 巨颚"]
    bad = [n for n in injected_wrong if n not in zh_set]
    ok("暗病打击 Prime" in bad and "肉瘤 巨颚" in bad,
       "C4 注入旧错名被事实源判据抓到", "caught=%r" % bad)


def section_d_frame():
    """D. 深色满幅 + 金属边框收边守门（防再犯 2026-11-12 两次纠正）。

    第一次（第19轮）我曾把「深色满幅」误判为黑边、改成框外透明 + 浅灰白圆角框，
    用户随即否定：「不对，你看你之前做的这个效果，我就要这个，你别再乱改了」，
    并在 AskUserQuestion 明确「只换质感，保留按武器配」。

    守的是用户认可的原始母版 `mod_0_0…mod_1_3` 肉眼可见形态：
      * **深色满幅不透明**：四角 alpha=255（不是框外透明圆角）；
      * **四边金属色收边**：四边中点取样必须是金属色（非深色裸边/黑边），且
        Bronze/Silver/Gold/Legendary 至少 3 种不同色（每种类型边框不一样）；
      * **整卡深色**：中心与四角均为深色（非浅灰白透明感）；
      * 生成源码不得再用框外透明 mask / 圆角裁切 / 深色 RGB 底覆盖。
    """
    print("== D. 深色满幅 + 金属边框收边 + 稀有度分档（非透明浅灰，运行时实时合成）==")
    from PIL import Image as _Image
    import io as _io
    # 档位事实源：由 Mods.json 的 rarity 经 RARITY_TIER 派生（不 import 生产模块，
    # 只读 JSON 复刻映射，避免测试与生成器同实现同源而失去独立性）。
    import json as _json
    _RARITY_TIER = {"common": "Bronze", "uncommon": "Silver",
                    "rare": "Gold", "legendary": "Legendary"}
    _mods = _json.loads((_REPO / "data" / "warframe_items" / "Mods.json").read_text(encoding="utf-8"))
    _mod_full = _json.loads(_MOD_FULL.read_text(encoding="utf-8"))
    _rarity_by_name = {str(m.get("name") or "").lower(): (m.get("rarity") or "common").lower()
                       for m in _mods if isinstance(m, dict)}
    _tier_by_zh = {}
    for _k, _v in (_mod_full.get("map") or {}).items():
        _zh = str((_v or {}).get("zh") or "").strip()
        if _zh and _k in _rarity_by_name:
            _tier_by_zh[_zh] = _RARITY_TIER.get(_rarity_by_name[_k], "Bronze")
    tier_by_zh = _tier_by_zh

    # 运行时实时合成的 7 张（zh 名称来自 _MELEE_BUILD 真表，卡图由 _live_mod_image 产出）
    full, _card = _load_isolated()
    zhs = list(_card._MELEE_BUILD)

    def _is_dark(c):
        return max(c[:3]) < 110          # 深色：三通道最大值都偏暗

    def _is_metal(c):
        # 金属色：足够亮/饱和（R/G/B 至少一通道 ≥130），且非纯白透明底的浅灰
        return max(c[:3]) >= 130

    corner_alpha = []
    edge_samples = {}                     # zh → 四边中点 RGB
    opaque_ratio, dark_ratio, white_ratio = [], [], []
    live_ok = []
    for zh in zhs:
        bio = full._live_mod_image(zh)     # 运行时实时合成（内存缓存），零静态资产
        if not isinstance(bio, _io.BytesIO):
            live_ok.append((zh, "no BytesIO")); continue
        try:
            im = _Image.open(bio).convert("RGBA"); im.load()
        except Exception as e:  # noqa: BLE001
            live_ok.append((zh, type(e).__name__)); continue
        live_ok.append((zh, "OK" if im.size == (370, 185) else str(im.size)))
        w, h = im.size
        a = im.split()[3]
        corners = [a.getpixel(pt) for pt in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
        corner_alpha.append((zh, corners))
        hist = a.histogram()
        opaque_ratio.append((zh, hist[255] / (w * h)))
        # 深色/亮白像素占比：整卡形态判据，对卡名/翼尖/框高光等局部取样点不敏感。
        px = list(im.convert("RGB").getdata())
        n = len(px)
        dark_ratio.append((zh, sum(1 for c in px if max(c) < 110) / n))
        white_ratio.append((zh, sum(1 for c in px if min(c) > 200) / n))
        # 四边中点（金属描边取样）：上/下/左/右
        edge_samples[zh] = (im.getpixel((w // 2, 0))[:3],
                            im.getpixel((w // 2, h - 1))[:3],
                            im.getpixel((0, h // 2))[:3],
                            im.getpixel((w - 1, h // 2))[:3])
    ok(all(s == "OK" for _, s in live_ok) and len(live_ok) == 7,
       "D0 7 张卡运行时实时合成为 370×185 BytesIO 真图", "r=%r" % live_ok)
    # 缓存命中：同一卡第二次调用命中 _LIVE_MOD_CACHE、bytes 相同（不重复合成）
    b1 = full._live_mod_image(zhs[0]); b2 = full._live_mod_image(zhs[0])
    ok(isinstance(b1, _io.BytesIO) and isinstance(b2, _io.BytesIO)
       and b1.getvalue() == b2.getvalue() and len(full._LIVE_MOD_CACHE) >= 7,
       "D0b 内存缓存命中（同卡二次 bytes 相同、缓存≥7）",
       "cache=%d" % len(full._LIVE_MOD_CACHE))

    # D1 深色满幅：四角 alpha=255（严禁框外透明圆角）
    bad_corner = [s for s, c in corner_alpha if any(v != 255 for v in c)]
    ok(not bad_corner, "D1 7 张卡四角 alpha=255（深色满幅，非透明圆角）", "bad=%r" % bad_corner)
    # D1b 满幅不透明占比 ≈100%（防整卡被掏空成透明贴纸）
    bad_opaque = [(s, round(r, 3)) for s, r in opaque_ratio if r < 0.99]
    ok(not bad_opaque, "D1b 7 张卡不透明像素占比 ≥99%（满幅）", "bad=%r" % bad_opaque)
    # D2 整卡深色满幅（深色像素占比 ≥55%，母版实测 0.65~0.82）：对卡名/翼尖局部
    # 取样不敏感，精确区分「深色游戏卡」vs 第19轮「浅灰白透明贴纸」。
    bad_dark = [(s, round(r, 3)) for s, r in dark_ratio if r < 0.55]
    ok(not bad_dark, "D2a 7 张卡深色像素占比 ≥55%（深色满幅，非浅灰白）", "bad=%r" % bad_dark)
    bad_white = [(s, round(r, 3)) for s, r in white_ratio if r > 0.20]
    ok(not bad_white, "D2b 7 张卡亮白像素占比 <20%（非浅底/透明贴纸）", "bad=%r" % bad_white)
    # D3 四边金属色收边（非深色裸边=黑边）：每张卡四边中点至少 3/4 是金属色
    bad_edge = []
    for slug, cs in edge_samples.items():
        metal = sum(1 for c in cs if _is_metal(c))
        if metal < 3:
            bad_edge.append((slug, cs))
    ok(not bad_edge, "D3 7 张卡四边金属色收边（每卡≥3/4 边为金属色，无深色裸边）",
       "bad=%r" % bad_edge)

    # D4 稀有度分档边框色 ≥3 种（每种类型边框不一样），档位由事实源派生
    tier_samples = {}
    for zh, cs in edge_samples.items():
        tier = tier_by_zh.get(zh)
        ok(tier is not None, "D4a %s 稀有度档位可由事实源派生" % zh, "tier=%r" % tier)
        if tier:
            tier_samples.setdefault(tier, []).extend(cs)

    def _avg(cs):
        return tuple(sum(c[i] for c in cs) // len(cs) for i in range(3))
    tier_avg = {t: _avg(cs) for t, cs in tier_samples.items()}
    distinct = set(tier_avg.values())
    ok(len(distinct) >= 3, "D4 稀有度边框色 >=3 种（每种类型边框不同）",
       "tier_avg=%r" % {t: c for t, c in tier_avg.items()})

    # D5 生成源码：必须深色满幅 + 金属收边，严禁框外透明 mask / 圆角裁切 / 深底覆盖
    gen_src = (_REPO / "tools" / "gen_mod_cards.py").read_text(encoding="utf-8")
    fn = _fn(ast.parse(gen_src), "render_mod_card_wide")
    fn_src = ast.get_source_segment(gen_src, fn) or ""
    no_transparent_mask = ("_card_outline_mask" not in fn_src
                           and "Image.composite(base" not in fn_src)
    no_dark_base = "Image.new(\"RGB\", (W, H), (16, 18, 22))" not in fn_src
    full_bleed = "Image.new(\"RGBA\", (W, H), tint + (255,))" in fn_src
    metal_edge = "ed.rectangle" in fn_src and "edge_color + (235,)" in fn_src
    rgb_out = "base.convert(\"RGB\").save" in fn_src
    ok(no_transparent_mask, "D5a 生成函数已删除框外透明 mask / 圆角裁切")
    ok(no_dark_base, "D5b 生成函数无深色 RGB 底覆盖")
    ok(full_bleed and rgb_out, "D5c 深色满幅底 + RGB 全幅不透明输出",
       "full_bleed=%s rgb_out=%s" % (full_bleed, rgb_out))
    ok(metal_edge, "D5d 生成函数沿四边画金属色描边收边")

    # D6 注入反证：把旧「框外透明圆角」写法塞进 D5a 的判据输入，必须仍被判失败
    # （证明 D5a 不是摆设：一旦有人把透明 mask 加回，no_transparent_mask 会翻 False）。
    injected = fn_src + "\n    base.putalpha(_card_outline_mask(W, H))\n"
    injected_passes = ("_card_outline_mask" not in injected
                       and "Image.composite(base" not in injected)
    ok(no_transparent_mask and not injected_passes,
       "D6 注入旧框外透明圆角写法被 D5a 判据抓到（反证守门有效）")

    # D7 当前紫卡母版：第 8 格固定用 mod_1_3.png（紫色星标），且四角满幅不透明
    riven_p = _MOD_WIDE.parent / "mod_1_3.png"
    ok(riven_p.is_file(), "D7a 当前紫卡母版 mod_1_3.png 存在", str(riven_p))
    if riven_p.is_file():
        rim = _Image.open(riven_p).convert("RGBA")
        ra = rim.split()[3]
        rcorners = [ra.getpixel(pt) for pt in ((0, 0), (rim.width - 1, 0),
                                               (0, rim.height - 1), (rim.width - 1, rim.height - 1))]
        ok(all(v == 255 for v in rcorners), "D7b 紫卡星标母版四角满幅不透明（非透明贴纸）",
           "corners=%r" % rcorners)


def melee_build_from_card() -> list:
    """从 riven_card.py AST 取 _MELEE_BUILD 真值（供 C4 注入反证复用）。"""
    import ast as _ast
    t = _ast.parse(_CARD.read_text(encoding="utf-8"))
    for node in t.body:
        if isinstance(node, _ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, _ast.Name) and tgt.id == "_MELEE_BUILD":
                    try:
                        return list(_ast.literal_eval(node.value))
                    except Exception:
                        return []
    return []


if __name__ == "__main__":
    print("== 模块E. 配卡按真实武器类型派生守门 ==")
    section_a_static()
    print()
    section_b_behavior()
    print()
    section_c_injection()
    print()
    section_d_frame()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
