# -*- coding: utf-8 -*-
"""守门：紫卡词条中文别名补丁层（官方副本，2026-11-11）。

防的是「图2正确参照」里三条卡面文本在旧代码下漏判/错认：
  * '+90.4% 近战连击效率'      -> None（整条丢）
  * '+90.4% 连击效率'          -> None（整条丢）
  * '-109.3% 近战攻击时暴击率'  -> Critical Chance（错认成普通暴击率）
修复只允许「补 aliases」，不允许造 en 键 / 造 base / 改生成物 / 改 riven.py。

锚定 official 副本根（parents[1]），绝不加载原件 qq_bridge/。
纯标准库 + 隔离导入，宿主可直接跑（不依赖 httpx）。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]          # official 副本根
_ROOT = _REPO / "src" / "plugins" / "qq_bridge"
_GT = _ROOT / "game_tools"
_PATCH = _GT / "riven_alias_patch.py"
_ZH = _REPO / "data" / "warframe_items" / "riven_stat_zh.json"
_BASE = _REPO / "data" / "warframe_items" / "riven_stat_base.json"
_RIVEN_PY = _GT / "riven.py"

_P = 0
_F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-42s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-42s %s" % (name, detail))


def _load_patch():
    """从文件路径直接导入补丁模块（避免触发完整 game_tools 包的 NoneBot 依赖）。"""
    spec = importlib.util.spec_from_file_location("_riven_alias_patch_under_test", _PATCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# A. 静态断言：补丁内容纪律（只读源码，不执行业务）
# ---------------------------------------------------------------------------
def section_static():
    src = _PATCH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    patch = _load_patch()

    # A1 只补两个已存在的 en 键
    ok(set(patch.ALIAS_PATCH) == {
        "Heavy Attack Efficiency", "Critical Chance for Slide Attack",
    }, "A1 补丁只覆盖两个既有 en 键", str(sorted(patch.ALIAS_PATCH)))

    # A2 补丁不造数值、不读 base 表：只查**执行代码**（排除 docstring，防把说明文字当代码）。
    # 判据一：ALIAS_PATCH 这个字面量里每个 value 都是纯字符串元组，无任何 float/int base 值。
    alias_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "ALIAS_PATCH":
                    alias_node = node.value
        elif isinstance(node, ast.AnnAssign):  # ALIAS_PATCH: dict[...] = {...}
            if isinstance(node.target, ast.Name) and node.target.id == "ALIAS_PATCH":
                alias_node = node.value
    numeric = []
    if isinstance(alias_node, ast.Dict):
        for v in alias_node.values:
            for n in ast.walk(v):
                if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
                        and not isinstance(n.value, bool):
                    numeric.append(n.value)
    ok(alias_node is not None and numeric == [], "A2a 补丁无任何数值常量（不造 base）", str(numeric))
    # 判据二：执行代码里没有读取 riven_stat_base.json / 访问 base 表的行为
    # （用 AST 收集字符串常量但排除 docstring 节点——去 Rabbit 化同款教训）。
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                doc_ids.add(id(body[0].value))
    code_strs = [n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and id(n) not in doc_ids]
    ok(not any("riven_stat_base" in s or "base_tbl" in s for s in code_strs),
       "A2b 执行代码不触碰 base 表", str(code_strs)[:120])
    # 新增别名全是中文字符串，不含英文 en 键形态
    for en, aliases in patch.ALIAS_PATCH.items():
        ok(all(not any(c.isascii() and c.isalpha() for c in a) for a in aliases),
           "A2c 别名 %s 全中文" % en, str(aliases))

    # A3 生成物零改：图2两条同义写法的来源 en 已在生成物里且 base 存在
    zh = json.loads(_ZH.read_text(encoding="utf-8"))
    base = json.loads(_BASE.read_text(encoding="utf-8"))
    melee_base = base.get("Melee") or {}
    # base 表是按武器类型分组的 dict；确认两个 en 键至少在某组里存在
    all_base_keys = set()
    for attrs in base.values():
        if isinstance(attrs, dict):
            all_base_keys.update(attrs.keys())
    ok("Heavy Attack Efficiency" in zh and "Critical Chance for Slide Attack" in zh,
       "A3a 两个 en 键已在生成物 zh 表")
    ok("Heavy Attack Efficiency" in all_base_keys
       and "Critical Chance for Slide Attack" in all_base_keys,
       "A3b 两个 en 键已在生成物 base 表",
       "HAE=%s CSA=%s" % (melee_base.get("Heavy Attack Efficiency"),
                          melee_base.get("Critical Chance for Slide Attack")))

    # A4 riven.py 零改：源码里不出现补丁别名（补丁是独立层，没写回生成物/业务文件）
    rsrc = _RIVEN_PY.read_text(encoding="utf-8")
    ok("近战连击效率" not in rsrc and "近战攻击时暴击率" not in rsrc
       and "滑行攻击时暴击率" not in rsrc,
       "A4 riven.py 未含补丁别名（零改铁律）")

    # A5 生成物 zh 表未被写入补丁别名（补丁只在内存叠加，不落盘）
    zh_raw = _ZH.read_text(encoding="utf-8")
    ok("近战连击效率" not in zh_raw and "近战攻击时暴击率" not in zh_raw,
       "A5 生成物 riven_stat_zh.json 零改")

    # A6 validate 反证：把别名挂到别的 en 名下必须抛错
    poisoned = {
        "Heavy Attack Efficiency": {"zh": "重击效率", "aliases": []},
        "Critical Chance": {"zh": "暴击几率", "aliases": ["近战连击效率"]},
    }
    threw = False
    try:
        patch.validate(poisoned)
    except ValueError:
        threw = True
    ok(threw, "A6 注入冲突别名 -> validate 抛错", "防越权抢词条")

    # A7 en 键缺失反证
    threw = False
    try:
        patch.validate({"Critical Chance": {"zh": "暴击几率"}})
    except ValueError:
        threw = True
    ok(threw, "A7 注入缺失 en 键 -> validate 抛错", "防造新词条")


# ---------------------------------------------------------------------------
# B. 行为断言：真 _find_stat（隔离加载 riven.py 的纯逻辑，不拉 NoneBot 主流程）
# ---------------------------------------------------------------------------
def _load_riven_isolated():
    """直接加载 riven.py 源码模块；它 import nonebot.log / ocr / warframe，
    这里用桩替换，只跑 _load_riven_data / _find_stat 的纯逻辑。"""
    # nonebot.log 桩
    nb = types.ModuleType("nonebot")
    nb_log = types.ModuleType("nonebot.log")
    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
    nb_log.logger = _L()
    nb.log = nb_log
    sys.modules.setdefault("nonebot", nb)
    sys.modules.setdefault("nonebot.log", nb_log)

    pkg = types.ModuleType("_gt_pkg")
    pkg.__path__ = [str(_GT)]
    sys.modules["_gt_pkg"] = pkg
    ocr = types.ModuleType("_gt_pkg.ocr"); sys.modules["_gt_pkg.ocr"] = ocr
    wf = types.ModuleType("_gt_pkg.warframe")
    wf._load_translate_data = lambda: None
    wf._resolve_weapon = lambda *a, **k: None
    wf._weapon_zh = lambda *a, **k: ""
    sys.modules["_gt_pkg.warframe"] = wf

    spec = importlib.util.spec_from_file_location("_gt_pkg.riven", _RIVEN_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_gt_pkg.riven"] = mod
    spec.loader.exec_module(mod)
    return mod


def section_behavior():
    R = _load_riven_isolated()
    patch = _load_patch()

    # 未打补丁前：三条卡面文本漏判/错认（还原缺陷，证明守门有效）
    R._load_riven_data()
    pre = {
        "+90.4% 近战连击效率": R._find_stat("+90.4% 近战连击效率"),
        "+90.4% 连击效率": R._find_stat("+90.4% 连击效率"),
        "-109.3% 近战攻击时暴击率": R._find_stat("-109.3% 近战攻击时暴击率"),
    }
    ok(pre["+90.4% 近战连击效率"] is None, "B0 复现旧缺陷: 近战连击效率=None")
    ok(pre["+90.4% 连击效率"] is None, "B0 复现旧缺陷: 连击效率=None")
    ok(pre["-109.3% 近战攻击时暴击率"] == "Critical Chance",
       "B0 复现旧缺陷: 近战攻击时暴击率错认", pre["-109.3% 近战攻击时暴击率"])

    # 手动叠加补丁 + 重建 zh_flat（等价于 ensure() 对 riven 侧做的动作，隔离环境无 riven_card）
    zh = R._RIVEN["zh"]
    patch.validate(zh)
    patch.merge_into(zh)
    R._RIVEN["zh_flat"] = patch.rebuild_zh_flat(zh)

    cases = {
        "+90.4% 近战连击效率": "Heavy Attack Efficiency",
        "+90.4% 连击效率": "Heavy Attack Efficiency",
        "+90.4% 近战连击": "Heavy Attack Efficiency",
        "-109.3% 近战攻击时暴击率": "Critical Chance for Slide Attack",
        "-109.3% 滑行攻击时暴击率": "Critical Chance for Slide Attack",
        "-109.3% 滑暴": "Critical Chance for Slide Attack",
        "+90.4% 重击效率": "Heavy Attack Efficiency",
        "+222.0% 暴击几率": "Critical Chance",
        "+146.4% 冲击伤害": "Impact Damage",
    }
    for line, expect in cases.items():
        got = R._find_stat(line)
        ok(got == expect, "B1 _find_stat %s" % line, "-> %s" % got)

    # B2 zh_flat 重建规则与原版一致：去空格 + 小写 + 长度降序
    flat = R._RIVEN["zh_flat"]
    ok(all(flat[i][0].__len__() >= flat[i + 1][0].__len__()
           for i in range(len(flat) - 1)), "B2a zh_flat 长度降序")
    ok(all(n == n.lower() and " " not in n and "\t" not in n for n, _ in flat),
       "B2b zh_flat 全小写无空白")
    ok(any(n == "近战连击效率" and k == "Heavy Attack Efficiency" for n, k in flat),
       "B2c 补丁别名已进 zh_flat")

    # B3 merge 幂等：重复合并不产生重复别名
    before = len(zh["Heavy Attack Efficiency"]["aliases"])
    patch.merge_into(zh)
    after = len(zh["Heavy Attack Efficiency"]["aliases"])
    ok(before == after, "B3 merge_into 幂等", "%d -> %d" % (before, after))

    # B4 主名未变（不造新中文主名，只加 aliases）
    ok(zh["Heavy Attack Efficiency"]["zh"] == "重击效率", "B4a 主名重击效率未变")
    ok(zh["Critical Chance for Slide Attack"]["zh"] == "滑行攻击暴击几率",
       "B4b 主名滑行攻击暴击几率未变")


if __name__ == "__main__":
    print("== A. 静态纪律 ==")
    section_static()
    print("== B. 真 _find_stat 行为 ==")
    section_behavior()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
