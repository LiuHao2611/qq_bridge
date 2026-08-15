#!/usr/bin/env python3
"""P3 M1 Skills 技能库单测（手册 §0.3 / §4 / §5）。

覆盖（全部离线，真调 translator 用 mock 注入，不依赖 ComfyUI 真实服务）：
  §0.2 输入→处理→输出：load_skills / fill_template / run_skill 三步闭环
  §0.3 衔接 P0 M3 translator.translate：run_skill 真调（mock 验证被调用且入参为 ProductionIntent）
  §5 红线：intent_template 必须含 subjects[].role（ProductionIntent 结构）；禁 hash()（create 走 db.uid）
  CRUD：create_skill / update_skill / delete_skill 往返一致

全部走自建临时 sqlite（D 盘 .tmp），不污染全局 MANJU_DB_PATH。
"""
import os
import sys
import asyncio
import tempfile
import unittest

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER)

_TMP = os.path.join(_SERVER, ".tmp")
os.makedirs(_TMP, exist_ok=True)
_FD, _DB = tempfile.mkstemp(suffix=".db", dir=_TMP)
os.close(_FD)
os.environ["MANJU_DB_PATH"] = _DB

import db  # noqa: E402

db.DB_PATH = _DB
db.init_db()  # 重建 schema + 执行迁移（含 skills 表）

from skills import (  # noqa: E402
    load_builtin_skills, load_skills, get_skill, fill_template,
    normalize_task_type, run_skill, create_skill, update_skill, delete_skill,
)
from intent_engine.production_intent import ProductionIntent  # noqa: E402


class _FakeTranslator:
    """mock P0 M3 translator：记录被调用意图，返回确定 workflow（验证非返回固定文本）。"""
    def __init__(self):
        self.calls = []

    async def translate(self, intent):
        self.calls.append(intent)
        return {"prompt": {"10": {"class_type": "KSampler", "inputs": {}}}}


class P3M1SkillsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.DB_PATH = _DB
        db.init_db()

    # ---- §0.3 别名归一 ----
    def test_normalize_task_type(self):
        self.assertEqual(normalize_task_type("shot_video"), "shot_video_text2video")
        self.assertEqual(normalize_task_type("shot_txt2img"), "shot_txt2img")

    # ---- §0.2 load_skills（内置 + 用户）----
    def test_load_builtin_skills(self):
        bs = load_builtin_skills()
        self.assertGreaterEqual(len(bs), 3)
        ids = {b["skill_id"] for b in bs}
        self.assertIn("skill_camera_design", ids)
        self.assertIn("skill_script_plan", ids)
        for b in bs:
            self.assertEqual(b["is_builtin"], 1)
            # 内置技能必须符合 ProductionIntent 结构（含 subjects[].role）
            subs = b["intent_template"].get("subjects") or []
            for s in subs:
                self.assertIn("role", s)

    def test_load_skills_merges_user(self):
        sid = create_skill("我的运镜", {"task_type": "shot_video_text2video",
                                        "intent_tags": ["写实"]}, {"k": 1})
        all_ = load_skills(db.SINGLE_USER_ID)
        ids = {s["skill_id"] for s in all_}
        self.assertIn(sid, ids)
        self.assertIn("skill_camera_design", ids)
        delete_skill(sid)

    # ---- §0.2 fill_template → 合法 ProductionIntent（含 subjects.role）----
    def test_fill_template_produces_valid_production_intent(self):
        bs = {b["skill_id"]: b for b in load_builtin_skills()}
        tpl = bs["skill_camera_design"]["intent_template"]
        filled = fill_template(
            tpl, "推镜头",
            {"subject_asset_id": "asset_123", "style_lighting": "霓虹灯"})
        intent = ProductionIntent.from_dict(filled)
        # 红线：task_type 合法 + subjects[].role 存在
        self.assertEqual(intent.task_type, "shot_video_text2video")
        self.assertEqual(len(intent.subjects), 1)
        self.assertEqual(intent.subjects[0].role, "identity")
        self.assertEqual(intent.subjects[0].asset_id, "asset_123")
        self.assertEqual(intent.style_constraints.lighting, "霓虹灯")
        self.assertIn("推镜头", intent.intent_tags)

    # ---- §0.3 run_skill 真调 translator（mock 验证）----
    def test_run_skill_calls_translator(self):
        fake = _FakeTranslator()

        async def go():
            return await run_skill(
                "skill_camera_design", "推镜头",
                {"subject_asset_id": "asset_123", "style_lighting": "霓虹灯"},
                translator=fake)

        wf = asyncio.run(go())
        # 断言：translator 被真正调用（非返回固定文本）
        self.assertEqual(len(fake.calls), 1)
        intent = fake.calls[0]
        self.assertIsInstance(intent, ProductionIntent)
        self.assertEqual(intent.subjects[0].role, "identity")
        # 断言：返回的是 translator 产物
        self.assertEqual(wf["prompt"]["10"]["class_type"], "KSampler")

    def test_run_skill_unknown_raises(self):
        fake = _FakeTranslator()

        async def go():
            return await run_skill("no_such_skill", "x", {}, translator=fake)

        with self.assertRaises(ValueError):
            asyncio.run(go())
        self.assertEqual(len(fake.calls), 0)

    # ---- CRUD 往返 ----
    def test_create_update_delete_skill(self):
        sid = create_skill("测试技能", {"task_type": "shot_txt2img",
                                         "intent_tags": ["写实"]}, {"strength": 0.5})
        self.assertTrue(sid)
        got = get_skill(sid)
        self.assertIsNotNone(got)
        self.assertEqual(got["name"], "测试技能")
        self.assertEqual(got["is_builtin"], 0)
        # update
        self.assertTrue(update_skill(sid, name="测试技能2"))
        self.assertEqual(get_skill(sid)["name"], "测试技能2")
        # delete
        self.assertTrue(delete_skill(sid))
        self.assertIsNone(get_skill(sid))


if __name__ == "__main__":
    unittest.main()
