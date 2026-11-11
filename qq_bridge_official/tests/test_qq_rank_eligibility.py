# -*- coding: utf-8 -*-
"""市价卡 rank 按钮「物品类别资格」验收（2026-11-11 第五次修改，prove-it，独立基准）。

## 针对用户第五次纠正：「战甲武器可没有等级，只有赋能和 mod 才有等级」

第四次修改给所有「市价口令 + 物品名」一律挂 ``满级 / 0级``，导致 ``wm 绝路``
（绝路=武器 Rubico Prime）也冒出等级按钮，点了必然进 ``market_text`` 的
``if not is_mod: return "不是 MOD，无法按等级过滤"``。用户要求举一反三覆盖全部
物品类别。本文件守门 :mod:`rank_eligibility` + ``qq_keyboard`` 的接线，**标准答案
独立于实现**：

* **解析契约（外部事实，非被测对象）**：直接加载真 ``warframe._resolve_url_name``
  ——实现侧也用它，这是「绝路→武器 / 狂怒→MOD / 充沛→赋能」零歧义的唯一契约。
  被测对象是 ``rank_eligibility.can_have_rank`` 的**tags 分类逻辑**与
  ``qq_keyboard._rank_button_data`` 的**是否据此挂按钮**，不是解析本身。
* **分类独立基准**：本文件自己用 ``json.load`` 读 ``wf_items_v2.json`` 建
  ``slug -> tags``，用本文件里**另一套**等价判定（``mod`` 且非 ``riven_mod``，或
  ``arcane_enhancement`` 且非 ``_helmet``）机器派生「全部 3840 件里哪些 slug 有
  等级」，逐条与实现比对。实现把分类写反/漏一类 → FAIL。
* **锚点固定**（防基准被削弱 + 用户点名 + 类别代表）：武器/战甲/部件/紫卡/头盔
  必须 ``False``；MOD/赋能/中文别名/英文/Prime 后缀/去 rank 幂等必须 ``True``。
* **按钮接线**：``wm <武器>`` → ``buttons_for == ()`` 且 ``make_keyboard == (None,())``；
  ``wm <MOD/赋能>`` → 恰好 ``满级/R0`` 两个带参按钮。
* **注入验证（prove-it 强制）**：把实现 backend 换成恒 ``True`` / 恒 ``False`` /
  分类逻辑反转三种错，对应守门必须报错，再恢复断言全绿 —— 证明守门不是摆设。

跑法（宿主纯标准库 / 容器真 nonebot 均可）：
  python tests/test_qq_rank_eligibility.py
  docker exec -i qq-bridge-official /opt/venv/bin/python -m pytest tests/test_qq_rank_eligibility.py -v
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src/plugins/qq_bridge"
_GT = _SRC / "game_tools"
_CH = _SRC / "channel"
_WF_ITEMS = _REPO / "data/wf_items_v2.json"


def _load(path: Path, name: str):
    """按文件直载模块，绕开 plugins.qq_bridge/__init__.py 的重量级链式 import。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_real_warframe():
    """构造假包上下文加载真 warframe.py（宿主/容器一致，读磁盘真文件、不联网）。"""
    qb = types.ModuleType("qq_bridge")
    qb.__path__ = [str(_SRC)]
    sys.modules["qq_bridge"] = qb
    pkg = types.ModuleType("qq_bridge.game_tools")
    pkg.__path__ = [str(_GT)]
    sys.modules["qq_bridge.game_tools"] = pkg
    return importlib.import_module("qq_bridge.game_tools.warframe")


# 真 warframe（解析契约）+ 真数据（分类契约）
_wf = _load_real_warframe()
_ITEMS = json.loads(_WF_ITEMS.read_text(encoding="utf-8"))
_SLUG_TAGS = {it["slug"]: (it.get("tags") or []) for it in _ITEMS}

# 实现侧（被测对象）：rank_eligibility + qq_keyboard + 依赖
inv_mod = _load(_CH / "command_inventory.py", "command_inventory")
cb = _load(_CH / "card_buttons.py", "card_buttons")
rel = _load(_CH / "rank_eligibility.py", "rank_eligibility")


def _real_tags(slug: str):
    return _SLUG_TAGS.get(slug) or []


# 把 business 真函数注入实现后端（宿主/容器都不该让 rank_eligibility 自己 import
# nonebot/httpx；测试显式注入磁盘真函数的解析与真数据 tags，契约同源）。
rel.set_backend(_wf._resolve_url_name, _real_tags)

kb = _load(_CH / "qq_keyboard.py", "qq_keyboard")  # 顶层 from rank_eligibility import


# ---------------------------------------------------------------------------
# 独立基准：本文件自己的 tags 分类判定（不调用 rel._is_rankable_tags / can_have_rank）
# ---------------------------------------------------------------------------
def _baseline_rankable(slug: str) -> bool:
    """独立等价判定：MOD(非紫卡) 或 赋能(非头盔) 才有等级。"""
    t = set(_SLUG_TAGS.get(slug) or ())
    is_mod = ("mod" in t) and ("riven_mod" not in t)
    is_arcane = ("arcane_enhancement" in t) and ("_helmet" not in (slug or ""))
    return bool(is_mod or is_arcane)


# 固定锚点：用户点名 + 类别代表（防基准被削弱，且钉死判错的边界词）
_FALSE_ANCHORS = (
    "绝路", "沙皇", "赤毒沙皇", "绝路 Prime 枪管", "犀牛", "月神", "拉托",
    "阿屎", "轮椅", "普莱多斯", "nonsense", "", "   ",
)
_TRUE_ANCHORS = (
    "持久力", "持久力 Prime", "延伸", "耗弱链接", "加速冲击", "盲怒", "瞬时坚毅",
    "狂怒", "加速",                      # 中文名恰好也是战甲外号，但真解析=MOD
    "充沛", "能量充沛", "充沛赋能", "赋能·充沛", "赋能 神盾",  # 赋能中文/别名
    "暗影聚精会神",                          # 中文官方名 → intensify (mod)
    "Arcane Energize", "Arcane Acceleration",
    "Transient Fortitude", "Overextended",  # 英文 MOD
)


# ---------------------------------------------------------------------------
# 守门
# ---------------------------------------------------------------------------
def test_baseline_sane():
    """基准本身：全量词典非空、含 MOD/赋能/武器/紫卡/头盔各类，锚点解析非空。"""
    assert len(_ITEMS) >= 3000, "wf_items_v2 词典异常偏少=%d" % len(_ITEMS)
    kinds = {
        "mod": sum(1 for t in _SLUG_TAGS.values() if "mod" in t),
        "arcane": sum(1 for t in _SLUG_TAGS.values() if "arcane_enhancement" in t),
        "riven": sum(1 for t in _SLUG_TAGS.values() if "riven_mod" in t),
        "helmet": sum(1 for t in _SLUG_TAGS.values() if "arcane_helmet" in t),
        "weapon": sum(1 for t in _SLUG_TAGS.values() if "weapon" in t),
        "warframe": sum(1 for t in _SLUG_TAGS.values() if "warframe" in t),
    }
    assert kinds["mod"] > 1000 and kinds["arcane"] > 100, "词典分类异常：%s" % kinds
    assert kinds["riven"] > 0 and kinds["helmet"] > 0, "紫卡/赋能头盔样本缺失：%s" % kinds
    assert _wf._resolve_url_name("绝路") == "rubico_prime", "解析契约漂移：绝路应=武器"
    assert _wf._resolve_url_name("充沛") == "arcane_energize", "解析契约漂移：充沛应=赋能"


def test_all_items_classification_matches_baseline():
    """机器全量：每个 EN slug 直接喂分类逻辑，实现 == 独立基准（分类写反/漏类 FAIL）。"""
    wrong = []
    for slug in _SLUG_TAGS:
        want = _baseline_rankable(slug)
        got = rel._is_rankable_tags(slug, _real_tags(slug))
        if got != want:
            wrong.append((slug, got, want))
    assert not wrong, "tags 分类与独立基准不一致（前 15）：%s" % (wrong[:15],)


def test_can_have_rank_anchors():
    """固定锚点：武器/战甲/部件/未知 False，MOD/赋能/别名/英文 True。"""
    bad_false = [(p, rel.can_have_rank(p)) for p in _FALSE_ANCHORS if rel.can_have_rank(p)]
    assert not bad_false, "这些应无等级却判有等级（武器/战甲被误挂）：%s" % bad_false
    bad_true = [(p, rel.can_have_rank(p)) for p in _TRUE_ANCHORS if not rel.can_have_rank(p)]
    assert not bad_true, "这些应可挂等级却判无（漏 MOD/赋能）：%s" % bad_true


def test_can_have_rank_full_catalog_by_name():
    """举一反三：对全量词典每件物品，用其中文/英文官方名喂 can_have_rank，
    结果必须等于该 slug 的独立基准。真正覆盖「每个功能」而非只拉黑绝路一词。"""
    wrong = []
    seen = 0
    for it in _ITEMS:
        slug = it["slug"]
        want = _baseline_rankable(slug)
        i18n = it.get("i18n") or {}
        names = {
            ((i18n.get("zh-hans") or {}).get("name") or "").strip(),
            ((i18n.get("en") or {}).get("name") or "").strip(),
        }
        for nm in names:
            if not nm:
                continue
            # 只对「能解析回本 slug」的名字断言：跨词同名（如 狂怒/加速 既是 MOD
            # 官方名又是战甲外号）由business 解析优先级定，真解析=谁就按谁的类别。
            resolved = _wf._resolve_url_name(nm)
            if resolved != slug:
                continue
            seen += 1
            got = rel.can_have_rank(nm)
            if got != want:
                wrong.append((nm, slug, got, want))
            if len(wrong) >= 15:
                break
        if len(wrong) >= 15:
            break
    assert seen >= 2000, "可回验的官方名字本太少=%d（解析契约回归？）" % seen
    assert not wrong, "全量物品名等级资格与基准不一致（前 15）：%s" % (wrong[:15],)


def test_rank_strip_idempotent_before_eligibility():
    """幂等：物品名带任意 rank 写法（前/后/别名），先剥离再判资格，结果不变。"""
    ranks = cb.build_card_inventory().rank_strip_rules
    assert ranks, "源码 rank 剥离规则为空（实现退化）"
    # 走真实按钮层链路：_rank_button_data 先剥 rank token，再判资格
    for p, want in (("wm 绝路 满级", False), ("wm 0级 绝路", False),
                    ("wm 绝路 R5", False), ("市价 绝路 max", False),
                    ("wm 0级 持久力", True), ("市价 持久力 R5", True),
                    ("wm 满级 充沛", True)):
        btns = kb.buttons_for(p)
        assert bool(btns) == want, "幂等输入资格错 %r -> %s" % (p, [b.cmd for b in btns])


def test_button_wiring_by_category():
    """按钮接线：无等级物品零按钮+(None,())；有等级恰好满级/R0 两个带参按钮。"""
    for p in ("wm 绝路", "wm 沙皇", "wm 绝路 Prime 枪管", "wm 犀牛", "市价 月神 满级"):
        assert kb.buttons_for(p) == (), "无等级物品却挂了按钮 %r" % p
        assert kb.make_keyboard(p) == (None, ()), "无等级物品 make_keyboard 非空 %r" % p
    for p in ("wm 持久力", "wm 充沛", "市价 延伸", "wm 狂怒", "wm 加速"):
        data = [b.cmd for b in kb.buttons_for(p)]
        assert len(data) == 2 and all(" " in d for d in data), \
            "有等级物品应恰好满级/R0 两按钮 %r -> %s" % (p, data)
        assert data[0].endswith("满级") and data[1].endswith("R0"), \
            "rank 按钮 token 错 %r -> %s" % (p, data)


def test_injection_guards():
    """prove-it：注入 恒True / 恒False / 分类反转 三种错后端，守门必须 FAIL。

    标准模式：把「锚点守门」和「全量守门」抽成函数，注入错后端后断言这些守门函数
    **确实抛 AssertionError**（= 守门生效、能抓错）；恢复真后端后断言它们全过。
    这样证明守门不是摆设——错误实现一进来就红。
    """
    real_resolve, real_tags = _wf._resolve_url_name, _real_tags

    # 锚点守门：在【真解析后端】下，物品名→资格的固定锚点全对。
    def anchor_guard():
        bad_false = [p for p in _FALSE_ANCHORS if rel.can_have_rank(p)]
        bad_true = [p for p in _TRUE_ANCHORS if not rel.can_have_rank(p)]
        assert not bad_false and not bad_true, "锚点失守 false=%s true=%s" % (bad_false, bad_true)

    # 全量分类守门：在【恒等 resolve 后端】下（绕开「物品名→slug」的解析歧义，
    # 专测 tags→资格 全量分类），3840 件逐条 == 独立基准。
    def catalog_guard(tags_fn=None):
        # 恒等 resolve，隔离解析层；tags_fn 缺省为真 tags，注入验证时传反转版
        rel.set_backend(lambda s: s, tags_fn or _real_tags)
        try:
            wrong = [s for s in _SLUG_TAGS if rel.can_have_rank(s) != _baseline_rankable(s)]
            assert not wrong, "全量分类失守 %d 件（如 %s）" % (len(wrong), wrong[:5])
        finally:
            rel.set_backend(real_resolve, real_tags)

    def expect_guard_fails(guard, label):
        try:
            guard()
        except AssertionError:
            return  # 守门成功报错 = 抓到了注入的错
        raise AssertionError("守门没抓到注入错误：%s" % label)

    # 注入 1：恒 True（退回第四次错——连武器都挂等级）→ 锚点守门必须 FAIL
    rel.set_backend(real_resolve, lambda _s: ["mod"])
    try:
        expect_guard_fails(anchor_guard, "恒True 被锚点漏过")
    finally:
        rel.set_backend(real_resolve, real_tags)

    # 注入 2：恒 False（漏挂全部 MOD/赋能）→ 锚点守门必须 FAIL
    rel.set_backend(real_resolve, lambda _s: ["weapon"])
    try:
        expect_guard_fails(anchor_guard, "恒False 被锚点漏过")
    finally:
        rel.set_backend(real_resolve, real_tags)

    # 注入 3：分类反转（MOD→weapon）→ 全量分类守门在恒等 resolve 下必须 FAIL
    def flip_tags(slug):
        t = list(_real_tags(slug))
        return ["weapon"] if ("mod" in t and "riven_mod" not in t) else t
    expect_guard_fails(lambda: catalog_guard(flip_tags), "分类反转被全量漏过")

    # 恢复断言：真实实现下两个守门全过
    anchor_guard()
    catalog_guard()  # 内部自己切恒等 resolve 再恢复
    assert [b.cmd for b in kb.buttons_for("wm 绝路")] == []
    assert len(kb.buttons_for("wm 持久力")) == 2


if __name__ == "__main__":
    import traceback
    tests = [
        test_baseline_sane,
        test_all_items_classification_matches_baseline,
        test_can_have_rank_anchors,
        test_can_have_rank_full_catalog_by_name,
        test_rank_strip_idempotent_before_eligibility,
        test_button_wiring_by_category,
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
