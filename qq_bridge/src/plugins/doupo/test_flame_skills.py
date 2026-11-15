# -*- coding: utf-8 -*-
"""异火技能验收（零依赖、exit-code 可验证）。

守的是玩家真正感受得到的行为：
  * 5 种异火每种都有独一无二的主动技 + 被动，且描述带原著出处；
  * **没收服**就不该出现在斗技池里；**收服后**自动进池、可上阵、能被战斗选中；
  * 「异火」口令要把主动/被动/原著出处显示给玩家；
  * 描述里不许再出现原著 0 命中的旧实机词。

运行：
    python src/plugins/doupo/test_flame_skills.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for mod, attrs in (("nonebot.adapters", {}), ("nonebot.adapters.onebot", {}),
                       ("nonebot.adapters.onebot.v11",
                        {"MessageEvent": object, "MessageSegment": object, "Message": object})):
        m = types.ModuleType(mod)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[mod] = m


_stub_nonebot()

from doupo import commands, config as cfg, core, flame_skills as fs, skills  # noqa: E402


def _p(lv=60):
    p = core.create_player("10001", "测试", "20001")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


def test_every_flame_has_unique_active_and_passive():
    assert set(fs.FLAME_SKILLS) == set(cfg.FLAME_NAMES), "技能表未覆盖全部养成异火"
    names = [r["active"]["name"] for r in fs.FLAME_SKILLS.values() if r.get("active")]
    assert len(names) == len(cfg.FLAME_NAMES) == len(set(names)), "主动技不够独特（有缺失或重名）"
    for flame, rec in fs.FLAME_SKILLS.items():
        a, pv = rec["active"], rec["passive"]
        for k in ("name", "desc", "source", "novel", "brief", "dmg", "kind", "scope"):
            assert a.get(k), f"{flame} 主动技缺 {k}"
        for k in ("name", "effect", "desc", "novel"):
            assert pv.get(k), f"{flame} 被动缺 {k}"
        assert "章「" in a["novel"], f"{flame} 主动技缺原著章节"
        assert "章「" in pv["novel"], f"{flame} 被动缺原著章节"
        assert a["dmg"] > 0, f"{flame} 主动技没有伤害系数"


def test_unowned_flame_not_in_pool():
    p = _p()
    pool = core.skill_pool(p)
    assert not any(e.get("faction") == "异火" for e in pool), "没收服就有异火技，等于白送"
    assert not any(e["name"] == fs.active_of("帝炎")["name"] for e in pool)


def test_owned_flame_enters_pool_and_loadout():
    p = _p()
    p["flames"]["青莲地心火"] = 1
    pool = core.skill_pool(p)
    hit = next((e for e in pool if e["name"] == "青莲火"), None)
    assert hit, "收服后斗技池没有青莲火"
    assert hit["rank"] == "异火" and hit["tier"] == "特殊"
    assert hit["unlocked"], "收服了却还锁着"
    assert "第二百章「青莲地心火」" in hit["novel"]
    assert "火山喷发" in hit["desc"], "描述没落到原著特性上"

    core.set_loadout(p, ["青莲火"])
    assert "青莲火" in [e["name"] for e in core.loadout_entries(p)], "上阵不了异火技"

    # 帝炎走成就位，不是喂养等级
    p["diyan"] = True
    assert any(e["name"] == "帝炎破世" for e in core.skill_pool(p)), "成就斗帝后没有帝炎技"


def test_flame_skill_can_be_picked_in_fight():
    p = _p()
    p["flames"]["净莲妖火"] = 2
    core.set_loadout(p, ["净化万物"])
    entry, cost = core._pick_skill(p, core.mp_max(p), rng=lambda: 0.0)
    assert entry is not None and entry["name"] == "净化万物", "战斗选不到异火技"
    assert cost > 0, "异火技不耗斗气"


class MemStore:
    """最小存储桩：只实现 commands 用到的两个方法。"""

    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p


def test_cmd_skills_shows_flame_group_without_duplication():
    p = _p()
    p["flames"]["陨落心炎"] = 1
    out = asyncio.run(commands.cmd_skills(MemStore(p), "10001"))
    assert "〔异火技〕" in out, "斗技列表没把异火技单独分组"
    assert "心火分体" in out and "陨落心炎" in out, "没标出技名与所属异火"
    assert out.count("心火分体") == 1, "异火技在「特殊」分组里重复出现了"


def test_cmd_flames_shows_active_passive_and_source():
    p = _p()
    p["flames"]["青莲地心火"] = 2
    out = asyncio.run(commands.cmd_flames(MemStore(p), "10001"))
    assert "主动【青莲火】" in out, "异火面板没显示主动技"
    assert "被动【地心精纯】" in out, "异火面板没显示被动"
    assert "第二百章「青莲地心火」" in out, "异火面板没显示原著出处"
    assert "上阵" in out, "没告诉玩家怎么把主动技放进四槽"


def test_config_desc_no_banned_words_and_has_cite():
    banned = ("不生不死", "无色无相", "天地源气", "净化世间万物")
    for f in cfg.FLAMES:
        for w in banned:
            assert w not in f["desc"], f"{f['name']} 描述仍含原著 0 命中词 {w}"
        assert "章「" in f["desc"], f"{f['name']} 描述没带原著出处"


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok, fail = 0, []
    for name, fn in fns:
        try:
            fn()
            print("PASS ", name)
            ok += 1
        except Exception as e:  # noqa: BLE001
            print("FAIL ", name, "->", type(e).__name__, str(e)[:140])
            fail.append(name)
    print("\nFLAME_SKILL_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
