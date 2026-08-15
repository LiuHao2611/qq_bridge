#!/usr/bin/env python3
"""P1 M3 Lock-Then-Animate · 四杠杆一致性计划离线测试（不依赖 ComfyUI / insightface）。

覆盖：
  ① 参考图像：build_lock_plan 从 DB 加载标准肖像 + 参考图集作为 IP-Adapter 输入；
  ② 起始帧锚定：standard_portrait 作为 i2v 首帧；
  ③ 主体特征：绑定资产的 512 维向量注入；
  ④ 提示词锚点：身份卡一致句 + 服装/发型/体型逐字进入 consistency_prompt；
  以及无角色绑定时 lock_mode=style_only 且四杠杆均 missing（诚实）。
  另验证 probe_readiness 返回结构（权重缺失也诚实列出，不崩溃）。
"""
import os
import sys
import tempfile
import unittest

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER)

_TMP = os.path.join(_SERVER, ".tmp")
os.makedirs(_TMP, exist_ok=True)
_fd, _DB = tempfile.mkstemp(suffix=".db", dir=_TMP)
os.close(_fd)
os.environ["MANJU_DB_PATH"] = _DB


class ConsistencyM3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db
        cls.db = db
        db.DB_PATH = _DB  # 强制本模块独占 temp 库，避免与 M2/M4 串库（DB_PATH 在导入期冻结）
        db.init_db()  # 创建完整 schema + 迁移（含 assets / asset_reference_set / 身份卡四列）
        conn = db.get_conn()
        try:
            # 资产主表 + 身份卡四列（迁移已建；此处确保行存在）
            conn.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, outfit, hairstyle, body_type, consistency_sentence) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("char1", db.SINGLE_USER_ID, "周岩", "role", "黑色校服外套", "齐肩短发", "瘦削170cm", ""),
            )
            conn.commit()
            # 参考图集（5 张：标准肖像 + 4 参考图）
            rows = [
                ("standard_portrait", "/file?name=sp.png", "VEC"),
                ("front", "/file?name=f.png", "VEC"),
                ("three_quarter", "/file?name=tq.png", "VEC"),
                ("expression", "/file?name=e.png", None),   # 无向量
                ("full_body", "/file?name=fb.png", "VEC"),
            ]
            for kind, url, fv in rows:
                conn.execute(
                    "INSERT INTO asset_reference_set (ref_id, asset_id, ref_kind, image_url, face_vector, created_at) "
                    "VALUES (?,?,?,?,?,strftime('now'))",
                    ("R-" + kind, "char1", kind, url, fv),
                )
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        # 每测试钉死本模块独占 temp 库：db.DB_PATH 是模块级全局，
        # pytest-randomly 跨文件重排时其他模块 setUpClass 可能改写它。
        self.db.DB_PATH = _DB

    def _plan(self, intent):
        from capabilities.consistency import ConsistencyCapability
        return ConsistencyCapability().build_lock_plan(intent)

    def test_four_levers_resolved_for_character(self):
        intent = {
            "bound_assets": [{
                "asset_id": "char1", "asset_type": "character",
                "inject_type": "face", "weight": 0.8, "face_vector": "BASE64VEC512",
            }],
        }
        plan = self._plan(intent)
        # ③ 主体向量
        self.assertEqual(plan["face_vector"], "BASE64VEC512")
        # ② 首帧锚定 = 标准肖像
        self.assertEqual(plan["first_frame"], "/file?name=sp.png")
        # ① 参考图像：标准肖像 + 4 张 = 5
        self.assertEqual(len(plan["reference_images"]), 5)
        self.assertIn("/file?name=sp.png", plan["reference_images"])
        self.assertIn("/file?name=fb.png", plan["reference_images"])
        # ④ 提示词锚点：一致句 + 服装/发型/体型逐字
        cp = plan["consistency_prompt"]
        self.assertIn("周岩", cp)
        self.assertIn("黑色校服外套", cp)
        self.assertIn("齐肩短发", cp)
        self.assertIn("瘦削170cm", cp)
        # 四杠杆均 present
        for lever in ("reference_image", "first_frame", "subject_vector", "prompt_anchor"):
            self.assertTrue(plan["levers"][lever]["present"], "杠杆 %s 应 present" % lever)
        self.assertEqual(plan["lock_mode"], "i2v_first_frame")

    def test_no_character_binding_falls_to_style_only(self):
        intent = {"bound_assets": [{"asset_id": "s1", "asset_type": "scene", "inject_type": "style", "weight": 0.4}]}
        plan = self._plan(intent)
        self.assertEqual(plan["lock_mode"], "style_only")
        self.assertFalse(plan["levers"]["subject_vector"]["present"])
        self.assertFalse(plan["levers"]["first_frame"]["present"])
        self.assertFalse(plan["levers"]["prompt_anchor"]["present"])

    def test_probe_readiness_structure(self):
        from capabilities.consistency import ConsistencyCapability
        r = ConsistencyCapability().probe_readiness()
        self.assertIn("weights", r)
        for k in ("ipadapter", "clip_vision", "controlnet", "insightface"):
            self.assertIn(k, r["weights"])
        self.assertIn("all_ready", r)
        self.assertIn("missing", r)
        self.assertIsInstance(r["missing"], list)


if __name__ == "__main__":
    unittest.main()
