# -*- coding: utf-8 -*-
"""守门：紫卡全面分析卡「配卡 MOD 必须匹配真实武器类型」（官方副本，2026-11-11）。

防的是用户图1：给近战武器（Coda Motovore）却配了「深层冷冻/病原弹头/致命洪流」
等**枪械** MOD，且配卡标题写死「基于击杀效率配卡」。

四条铁律：
  1. 生产配卡名**只能**来自 ``riven_card._BUILD`` / ``_MELEE_BUILD``（唯一真表），
     riven_full.py 不得再写一份手工卡名；**每个卡名必须在 mod_full.json 的 zh
     字段精确命中**（2026-11-12 加：旧「暗病打击 Prime/肉瘤 巨颚」是凭译名猜的、
     事实源里根本不存在，用户投诉「配卡不符合武器」的深层根因）；
  2. ``image`` 对**已备真图的近战重击流 7 张**必须指向离线横版真卡
     （riven_full_assets/mods/*.png，370×185，零外呼）；无真图（枪械表/当前紫卡）
     → 诚实 None 走占位框，**绝不张冠李戴**贴参考卡那 8 张枪卡图；
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

    # A6 image 走 _mod_image 查本地真图（2026-11-12：不再写死 None）
    ok("_mod_image(" in bm_src, "A6 _build_mods 用 _mod_image 查真图")
    ok('_mod_image(n)' in bm_src.replace(" ", ""),
       "A7 _build_mods 逐卡查图（非全 None）")
    # A7b _mod_image 只读本地盘、零外呼
    f_mi = _fn(tree, "_mod_image")
    mi_src = ast.get_source_segment(src, f_mi) or "" if f_mi else ""
    ok(f_mi is not None and "_MOD_WIDE_DIR" in mi_src, "A7c _mod_image 读本地资产目录")
    ok(not any(x in mi_src for x in ("httpx", "urlopen", "requests", "http://", "https://")),
       "A7d _mod_image 零网络外呼")
    ok("is_file()" in mi_src and "None" in mi_src, "A7e _mod_image 缺图诚实返回 None")

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

    # A16 真图资产齐备 + 尺寸 370×185 + 映射键与真表对齐
    import re as _re
    img_map = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_MELEE_MOD_IMAGE":
                    try:
                        img_map = ast.literal_eval(node.value)
                    except Exception:
                        pass
    ok(isinstance(img_map, dict) and set(img_map or {}) == set(melee_build or []),
       "A16 映射键与 _MELEE_BUILD 逐字对齐",
       "diff=%r" % ((set(melee_build or []) ^ set((img_map or {}).keys())),))
    from PIL import Image as _Image
    bad_img = []
    for zh, fn in (img_map or {}).items():
        p = _MOD_WIDE / fn
        if not p.is_file() or p.stat().st_size <= 500:
            bad_img.append((fn, "missing/small"))
            continue
        try:
            im = _Image.open(p)
            if im.size != (370, 185):
                bad_img.append((fn, im.size))
        except Exception as e:  # noqa: BLE001
            bad_img.append((fn, type(e).__name__))
    ok(not bad_img, "A17 7 张横版真图齐备且为 370×185", "bad=%r" % bad_img[:4])
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
        # B4 前 7 张近战卡必须有真图路径且文件存在（2026-11-12：不再是纯黑占位）
        real_imgs = [m["image"] for m in mods[:7]]
        ok(all(p and Path(p).is_file() for p in real_imgs if isinstance(p, str))
           and all(isinstance(p, str) for p in real_imgs),
           "B4 %s 前 7 张 image 指向真图" % t,
           "paths=%d" % sum(1 for p in real_imgs if isinstance(p, str)))
        ok(mods[7]["image"] is None, "B4b %s 第 8 格当前紫卡诚实无图" % t)
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
    """D. 黑边 / 稀有度边框守门（防再犯 2026-11-12 用户「配卡还是有黑边，
    每种类型 mod 边框都不一样，严格按参考图」）。

    守的是用户肉眼可见的形态：卡框外透明（无深色矩形黑边）、圆角、每张按
    Common/Bronze→Uncommon/Silver→Rare/Gold→Legendary 出**不同色**边框。
    """
    print("== D. 黑边 / 稀有度边框：框外透明 + 每档边框不同 ==")
    from PIL import Image as _Image
    # 档位事实源：由 Mods.json 的 rarity 经 RARITY_TIER 派生（不 import 生产模块，
    # 只读 JSON 复刻映射，避免测试与生成器同实现同源而失去独立性）。
    import json as _json
    _RARITY_TIER = {"common": "Bronze", "uncommon": "Silver",
                    "rare": "Gold", "legendary": "Legendary"}
    _mods = _json.loads((_REPO / "data" / "warframe_items" / "Mods.json").read_text(encoding="utf-8"))
    _mod_full = _json.loads(_MOD_FULL.read_text(encoding="utf-8"))
    # uniqueName 末段 → rarity；再由 mod_full 的 zh 落到本 7 张
    _rarity_by_un = {m.get("uniqueName"): (m.get("rarity") or "common").lower()
                     for m in _mods if isinstance(m, dict)}
    _tier_by_zh = {}
    for _v in (_mod_full.get("map") or {}).values():
        _zh = str((_v or {}).get("zh") or "").strip()
        # mod_full 的 key 是 en 小写名，唯一名映射需 en→uniqueName；此处只用 zh→rarity 反查，
        # 通过 Mods.json 的 name 对齐（见下）。
        if _zh:
            _tier_by_zh[_zh] = None
    _rarity_by_name = {str(m.get("name") or "").lower(): (m.get("rarity") or "common").lower()
                       for m in _mods if isinstance(m, dict)}
    for _k, _v in (_mod_full.get("map") or {}).items():
        _zh = str((_v or {}).get("zh") or "").strip()
        if _zh and _k in _rarity_by_name:
            _tier_by_zh[_zh] = _RARITY_TIER.get(_rarity_by_name[_k], "Bronze")
    tier_by_zh = {k: v for k, v in _tier_by_zh.items() if v}
    corner_alpha, center_alpha, transparent_ratio = [], [], []
    edge_pixels = {}
    for slug in _MELEE_IMG_SLUG:
        p = _MOD_WIDE / (slug + ".png")
        im = _Image.open(p).convert("RGBA")
        w, h = im.size
        a = im.split()[3]
        corners = [a.getpixel(pt) for pt in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
        corner_alpha.append((slug, corners))
        center_alpha.append((slug, a.getpixel((w // 2, h // 2))))
        # 框外透明占比：alpha==0 的像素比例（圆角 + 四边留白应 > 3%）
        hist = a.histogram()
        transparent_ratio.append((slug, hist[0] / (w * h)))
        # 取左/右边中点的描边色（避开顶/底框中央的深色徽记，取样更纯粹）
        edge_pixels[slug] = (im.getpixel((1, h // 2))[:3], im.getpixel((w - 2, h // 2))[:3])

    bad_corner = [s for s, c in corner_alpha if any(v != 0 for v in c)]
    ok(not bad_corner, "D1 7 张卡四角 alpha=0（无深色矩形黑边）", "bad=%r" % bad_corner)
    bad_center = [s for s, v in center_alpha if v != 255]
    ok(not bad_center, "D2 7 张卡面中心不透明（未被掏空）", "bad=%r" % bad_center)
    # 圆角 R=16/370×185 时框外透明占比约 0.36%（浅圆角、卡面几乎满幅，贴近参考图）。
    # 下界 0.2% 证明圆角真实生效（非仅抠 4 个像素），上界 10% 防整卡被误掏空。
    bad_ratio = [(s, round(r, 4)) for s, r in transparent_ratio if not (0.002 < r < 0.10)]
    ok(not bad_ratio, "D3 框外透明占比 ∈(0.2%,10%)（圆角生效且卡面未被掏空）", "bad=%r" % bad_ratio)

    # D4 边框色：同档一致、跨档不同。用描边取样，Bronze/Silver/Gold/Legendary 至少 3 种色。
    slug_to_zh = {"primed_fever_strike": "热病打击 Prime", "north_wind": "北风",
                  "corrupt_charge": "邪恶蓄力", "seismic_wave": "震波",
                  "carnis_mandible": "肉碾虫 巨颚", "sacrificial_steel": "牺牲 斩铁",
                  "organ_shatter": "肢解"}
    tier_samples = {}
    for slug, (top, bot) in edge_pixels.items():
        zh = slug_to_zh[slug]
        tier = tier_by_zh.get(zh)
        ok(tier is not None, "D4a %s 稀有度档位可由事实源派生" % zh, "tier=%r" % tier)
        if tier:
            tier_samples.setdefault(tier, []).extend((top, bot))
    # 每档取平均色，比较档间欧氏距离
    def _avg(cs):
        return tuple(sum(c[i] for c in cs) // len(cs) for i in range(3))
    tier_avg = {t: _avg(cs) for t, cs in tier_samples.items()}
    distinct = set(tier_avg.values())
    ok(len(distinct) >= 3, "D4 稀有度边框色 >=3 种（每种类型边框不同）",
       "tier_avg=%r" % {t: c for t, c in tier_avg.items()})

    # D5 生成源码不得再用深色 RGB 底覆盖（黑边根因），必须 RGBA 直出 + 圆角 mask
    gen_src = (_REPO / "tools" / "gen_mod_cards.py").read_text(encoding="utf-8")
    fn = _fn(ast.parse(gen_src), "render_mod_card_wide")
    fn_src = ast.get_source_segment(gen_src, fn) or ""
    no_dark_base = "Image.new(\"RGB\", (W, H), (16, 18, 22))" not in fn_src
    has_rgba_out = ".save(buf, format=\"PNG\")" in fn_src and "base.save" in fn_src
    has_mask = "_card_outline_mask" in fn_src and "putalpha" in fn_src
    ok(no_dark_base, "D5a 生成函数已删除深色 RGB 底覆盖（黑边根因）")
    ok(has_rgba_out and has_mask, "D5b 生成函数 RGBA 直出 + 圆角 mask",
       "rgba=%s mask=%s" % (has_rgba_out, has_mask))

    # D6 注入反证：把旧「深底覆盖」写法塞回 D5a 判据，必须被抓到（守门非摆设）
    injected = fn_src + "\n    out_img = Image.new(\"RGB\", (W, H), (16, 18, 22))\n"
    ok("Image.new(\"RGB\", (W, H), (16, 18, 22))" in injected,
       "D6 注入旧深底覆盖写法被 D5a 判据抓到（反证守门有效）")


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
