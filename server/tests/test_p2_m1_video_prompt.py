# -*- coding: utf-8 -*-
"""P2 M1 单元测试：运镜映射 + 动作扩写 + 视频 prompt 组装。

每个函数附真实代码 + 断言（手册 §3 总要求）。
含 P0 依赖打通证据：semantic.build 调 build_video_prompt；camera.build 调 CAMERA_MOVEMENT_PROMPT。
"""
import time
import unittest
from unittest import mock

from intent_engine import ProductionIntent
from capabilities.semantic import SemanticCapability
from capabilities.camera import CameraCapability
from features.camera_mapping import (
    CAMERA_MOVEMENT_PROMPT, resolve_camera_fragment, resolve_camera_movement,
)
from features.video_prompt import (
    expand_action, build_spatial_fragment, build_video_prompt, build_shot_video_prompt,
)


class TestCameraMapping(unittest.TestCase):
    def test_official_keys_present(self):
        for k in ("static", "push_in", "pull_back", "pan_left", "pan_right",
                  "tilt_up", "tilt_down", "tracking", "handheld"):
            self.assertIn(k, CAMERA_MOVEMENT_PROMPT)

    def test_official_fragments_match_manual(self):
        # 逐值对照 P2 v2 手册 §4.1 CAMERA_MOVEMENT_PROMPT，确保运镜语义完整（非仅键存在）
        manual = {
            "static":    "fixed camera, no camera movement",
            "push_in":   "camera slowly pushing in toward the subject",
            "pull_back": "camera slowly pulling back, revealing the scene",
            "pan_left":  "camera panning left",
            "pan_right": "camera panning right",
            "tilt_up":   "camera tilting up",
            "tilt_down": "camera tilting down",
            "tracking":  "camera tracking alongside the moving subject",
            "handheld":  "handheld camera with subtle natural shake",
        }
        for k, v in manual.items():
            self.assertEqual(resolve_camera_fragment(k), v,
                             msg="运镜 %r 未对齐手册 §4.1 片段" % k)
        # capability 声明 SUPPORTED 必须覆盖手册全部 9 值（声明层对齐）
        from capabilities.camera import CameraCapability
        for k in manual:
            self.assertIn(k, CameraCapability.SUPPORTED,
                          msg="capability SUPPORTED 未声明手册运镜 %r" % k)

    def test_frontend_alias_resolves(self):
        # 前端真实枚举别名都应映射到非 static 片段（不丢运镜语义）
        for mv in ("pull_out", "dolly", "zoom", "aerial", "follow",
                   "pan_lr", "pan", "tilt"):
            frag = resolve_camera_fragment(mv)
            self.assertNotEqual(frag, CAMERA_MOVEMENT_PROMPT["static"])
            self.assertTrue(frag)

    def test_unknown_falls_back_static_with_warning(self):
        with self.assertLogs("features.camera_mapping", level="WARNING") as cm:
            frag = resolve_camera_fragment("not_a_real_move")
        self.assertEqual(frag, CAMERA_MOVEMENT_PROMPT["static"])
        self.assertTrue(any("未知运镜" in m for m in cm.output))

    def test_resolve_camera_movement_all_sources(self):
        # dict: camera.movement
        self.assertEqual(resolve_camera_movement({"camera": {"movement": "push_in"}}), "push_in")
        # dict: optional_controls.camera_motion
        self.assertEqual(
            resolve_camera_movement({"optional_controls": {"camera_motion": "aerial"}}), "aerial")
        # dict: 直传 camera_movement（mock.js 形态）
        self.assertEqual(resolve_camera_movement({"camera_movement": "dolly"}), "dolly")
        # dataclass
        pi = ProductionIntent(task_type="shot_video_text2video", intent_tags=[],
                              optional_controls={"camera_motion": "zoom"})
        self.assertEqual(resolve_camera_movement(pi), "zoom")
        # 默认 static
        self.assertEqual(resolve_camera_movement({}), "static")


class TestExpandAction(unittest.TestCase):
    def test_explicit_action_used(self):
        self.assertEqual(expand_action({"action": "主角向前奔跑", "description": "x"}),
                         "主角向前奔跑")

    def test_ollama_expand_called_and_used(self):
        fake = mock.MagicMock(return_value="主角推门而入")
        out = expand_action({"description": "女主在咖啡馆等男友"}, ollama_fn=fake)
        self.assertEqual(out, "主角推门而入")
        fake.assert_called_once()

    def test_ollama_timeout_falls_back_to_description(self):
        def slow(_d):
            time.sleep(1.0)
            return "never"
        out = expand_action({"description": "静态描述文本"}, ollama_fn=slow, timeout=0.1)
        self.assertEqual(out, "静态描述文本")  # 超时退回，不阻塞

    def test_ollama_exception_falls_back(self):
        def boom(_d):
            raise RuntimeError("model down")
        out = expand_action({"description": "另一段描述"}, ollama_fn=boom)
        self.assertEqual(out, "另一段描述")


class TestSpatialFragment(unittest.TestCase):
    def test_multi_subject(self):
        spatial = {"subjects": [
            {"asset_id": "a1", "position": "左", "orientation": "facing_right", "gaze": "to_a2"},
            {"asset_id": "a2", "position": "右", "orientation": "facing_left", "gaze": "to_a1"},
        ]}
        frag = build_spatial_fragment(spatial)
        self.assertIn("a1在画面左，facing_right，视线to_a2", frag)
        self.assertIn("a2在画面右，facing_left，视线to_a1", frag)

    def test_empty_spatial(self):
        self.assertEqual(build_spatial_fragment(None), "")
        self.assertEqual(build_spatial_fragment({}), "")


class TestBuildVideoPrompt(unittest.TestCase):
    def test_six_segments_joined(self):
        p = build_video_prompt(
            scene="雨夜咖啡馆", action="主角推门", camera_frag="camera panning left",
            spatial_frag="a1在画面左", style_anchor={"lighting": "warm_cafe"},
            consistency_sentence="同发型同服装")
        self.assertIn("主角推门", p)
        self.assertIn("雨夜咖啡馆", p)
        self.assertIn("a1在画面左", p)
        self.assertIn("camera panning left", p)
        self.assertIn("warm cafe", p)  # 下划线转空格
        self.assertIn("同发型同服装", p)

    def test_all_empty_fallback(self):
        self.assertEqual(build_video_prompt(""), "cinematic scene")


class TestBuildShotVideoPrompt(unittest.TestCase):
    def test_full_orchestration(self):
        intent = {
            "description": "林夏推门入店",
            "camera_movement": "push_in",
            "spatial": {"subjects": [
                {"asset_id": "c1", "position": "中", "orientation": "facing_front", "gaze": "to_cam"}]},
            "style_anchor": {"lighting": "neon_light"},
        }
        identity_card = {"consistency_sentence": "黑长直发同款风衣"}
        out = build_shot_video_prompt(intent, identity_card=identity_card)
        self.assertIn("camera slowly pushing in", out)   # 4.1 运镜
        self.assertIn("林夏推门入店", out)                # 场景
        self.assertIn("c1在画面中", out)                  # 4.3 站位
        self.assertIn("neon light", out)                  # 风格锚点
        self.assertIn("黑长直发同款风衣", out)            # 一致句（P1）

    def test_expand_action_in_orchestration(self):
        intent = {"description": "苏蔓将照片滑过桌面", "camera_movement": "static"}
        fake = mock.MagicMock(return_value="苏蔓推照片")
        out = build_shot_video_prompt(intent, ollama_fn=fake)
        self.assertIn("苏蔓推照片", out)


class TestP0DependencyWiring(unittest.TestCase):
    """P0 依赖打通证据（手册 §3 总要求 3）。"""

    def test_semantic_build_calls_build_video_prompt(self):
        # 隔离 Ollama（离线）：固定动作扩写，验证 semantic.build 触发 video_prompt
        intent = ProductionIntent(
            task_type="shot_video_text2video", intent_tags=["cinematic"],
            optional_controls={"camera_motion": "push_in"},
            spatial={"subjects": [
                {"asset_id": "a1", "position": "左", "orientation": "facing_right", "gaze": "to_a2"}]},
        )
        with mock.patch("features.video_prompt.expand_action",
                        return_value="主角向前走"):
            res = SemanticCapability().build(intent)
        self.assertIn("video_prompt", res)
        self.assertIn("camera slowly pushing in", res["video_prompt"])   # build_video_prompt 产出
        self.assertIn("a1在画面左", res["video_prompt"])                   # 站位片段
        # 增量：动作/站位并入 positive_prompt（运镜由 camera 维度注入，避免重复）
        self.assertIn("a1在画面左", res["positive_prompt"])

    def test_camera_build_uses_camera_movement_prompt(self):
        with mock.patch("capabilities.camera.resolve_camera_fragment",
                        side_effect=lambda m: CAMERA_MOVEMENT_PROMPT.get(m, CAMERA_MOVEMENT_PROMPT["static"])) as spy:
            # wf 非 None 才进入运镜片段注入分支（resolve_camera_fragment 在此调用）
            CameraCapability().build(
                ProductionIntent(task_type="shot_video_text2video", intent_tags=[],
                                optional_controls={"camera_motion": "aerial"}),
                wf=mock.MagicMock())
        self.assertTrue(spy.called)  # camera.build 调 CAMERA_MOVEMENT_PROMPT（经 resolve_camera_fragment）

    def test_camera_build_infers_and_resolves(self):
        # 关键词推断「摇」→ pan_lr（手册对齐别名，非废弃的 pan）；仍走 CAMERA_MOVEMENT_PROMPT 解析
        res = CameraCapability().build(
            ProductionIntent(task_type="shot_video_text2video", intent_tags=["摇镜头"],
                             optional_controls={}))
        self.assertEqual(res["camera_motion"], "pan_lr")
        frag = resolve_camera_fragment("pan_lr")
        self.assertIn("panning", frag)


if __name__ == "__main__":
    unittest.main()
