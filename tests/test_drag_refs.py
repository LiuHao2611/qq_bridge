"""REDUN-03 资产拖拽引用：文本 @引用 冗余解析单元测试（不依赖 DB / 网络）。

校验 server.routers._reconcile_segment_refs：
- 文本 @[资产名] 与 @资产名 两种写法都能解析为 asset_id
- 与客户端提交的 asset_refs 去重合并
- 返回片段级唯一引用集合（供 BR-009 兜底）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from routers import _reconcile_segment_refs

NAME_TO_ID = {
    "小明": "a1",
    "小红": "a2",
    "客厅": "s1",
    "魔法杖": "p1",
}


def mk_shot(**kw):
    s = {"duration": 4, "description": "", "image_prompt": "", "voiceover": "",
         "dialogue": "", "setting": "", "asset_refs": []}
    s.update(kw)
    return s


def test_bracketed_and_plain_mentions():
    shots = [mk_shot(description="镜头里 @[小明] 与 @小红 并肩而立")]
    seg = _reconcile_segment_refs(shots, NAME_TO_ID)
    assert seg == {"a1", "a2"}, seg
    ids = {r["asset_id"] for r in shots[0]["asset_refs"]}
    assert ids == {"a1", "a2"}, ids


def test_mentions_across_fields():
    shots = [mk_shot(description="在 @[客厅] 中", dialogue="@小明 说：出发")]
    seg = _reconcile_segment_refs(shots, NAME_TO_ID)
    assert seg == {"s1", "a1"}, seg


def test_unknown_name_ignored():
    shots = [mk_shot(description="@[不存在的人] 出现")]
    seg = _reconcile_segment_refs(shots, NAME_TO_ID)
    assert seg == set(), seg
    assert shots[0]["asset_refs"] == [], shots[0]["asset_refs"]


def test_dedup_with_client_refs():
    shots = [mk_shot(description="@[小明]", asset_refs=[{"asset_id": "a1", "asset_type": "role", "used_in": "shot"}])]
    seg = _reconcile_segment_refs(shots, NAME_TO_ID)
    assert seg == {"a1"}, seg
    assert len(shots[0]["asset_refs"]) == 1, shots[0]["asset_refs"]


def test_br009_counting():
    # 片段级唯一引用 >9（跨两镜：第一镜 9 个、第二镜再加 1 个新资产）→ seg_unique 应为 10
    # （单镜被 [:9] 截断；BR-009 段级上限由路由层据此抛 MAX_REFS_EXCEEDED）
    names_a = ["角色%d" % i for i in range(1, 10)]   # 9 个
    names_b = ["角色10"]                              # 第 10 个（新）
    shots = [
        mk_shot(description=" ".join("@[" + n + "]" for n in names_a)),
        mk_shot(description=" ".join("@[" + n + "]" for n in names_b)),
    ]
    extra = {n: "x%d" % i for i, n in enumerate(names_a + names_b)}
    seg = _reconcile_segment_refs(shots, extra)
    assert len(seg) == 10, len(seg)


def test_per_shot_cap_nine():
    # 单镜写入 12 个唯一引用 → 落库应被截断到 9
    names = ["n%d" % i for i in range(12)]
    shots = [mk_shot(description=" ".join("@[" + n + "]" for n in names))]
    extra = {n: "x%d" % i for i, n in enumerate(names)}
    _reconcile_segment_refs(shots, extra)
    assert len(shots[0]["asset_refs"]) == 9, len(shots[0]["asset_refs"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
        passed += 1
    print("ALL_PASS %d/%d" % (passed, len(fns)))
