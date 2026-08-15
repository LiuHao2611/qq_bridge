#!/usr/bin/env python3
"""P3 M2 用户自定义模板单测（手册 §0.3 / §4 / §5）。

覆盖（全部离线，不依赖 ComfyUI 真实服务）：
  §0.2 输入→处理→输出：import_workflow 三步（识别占位符 → 语义节点 → schema）
  §0.3 衔接 P0 M3 映射：register_user_template 落 user_custom 且 TemplateEngine 可发现并加载
  §5 红线：禁原样存 JSON（必须占位符识别 + 语义节点）；禁 hash() 生成 ID

全部走自建临时 sqlite（D 盘 .tmp）；user_custom 覆盖文件在 finally 中清理，避免污染其它测试。
"""
import os
import sys
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
db.init_db()

from features.user_templates import (  # noqa: E402
    import_workflow, register_user_template, list_user_templates,
    test_user_template as _tmpl_test_func,
)
from intent_engine.template_engine import TemplateEngine  # noqa: E402

_USER_CUSTOM = os.path.join(_SERVER, "intent_engine", "templates", "user_custom")


class P3M2TemplatesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.DB_PATH = _DB
        db.init_db()

    def tearDown(self):
        # 清理本测试可能写入的 user_custom 覆盖文件，避免影响其它测试
        for fn in os.listdir(_USER_CUSTOM) if os.path.isdir(_USER_CUSTOM) else []:
            if fn.endswith(".json"):
                try:
                    os.remove(os.path.join(_USER_CUSTOM, fn))
                except Exception:
                    pass

    def _sample_comfy(self):
        return {
            "10": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd15.safetensors"}},
            "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["10", 1]}},
            "12": {"class_type": "KSampler", "inputs": {"seed": 123, "steps": 20, "cfg": 7.0,
                                                        "model": ["10", 0], "positive": ["11", 0]}},
            "13": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768}},
        }

    # ---- §0.2 import_workflow 三步 ----
    def test_import_workflow_detects_placeholders(self):
        res = import_workflow(self._sample_comfy())
        ph = set(res["detected_placeholders"])
        # 识别规则：ckpt_name→checkpoint；CLIPTextEncode.text→positive_prompt；
        # seed/steps/cfg/width/height → 同名占位符
        for expected in ("checkpoint", "positive_prompt", "seed", "steps", "cfg_scale", "width", "height"):
            self.assertIn(expected, ph)
        # 语义节点：4 个原始节点 → 4 个语义节点
        self.assertEqual(len(res["semantic_nodes"]), 4)
        # schema 含对应类型
        self.assertEqual(res["placeholder_schema"]["seed"]["type"], "int")

    def test_import_workflow_preserves_links_as_semantic_refs(self):
        res = import_workflow(self._sample_comfy())
        nodes = res["semantic_nodes"]
        # 找到 ksampler 节点（含 positive 引用，应为 [语义名, 0]）
        ks = next(n for n in nodes.values() if n["class_type"] == "KSampler")
        self.assertIsInstance(ks["inputs"]["positive"], list)
        self.assertEqual(ks["inputs"]["positive"][1], 0)
        # 参数化字段被替换为 {{占位符}}
        self.assertEqual(ks["inputs"]["seed"], "{{seed}}")

    # ---- §0.3 注册进 P0 映射（user_custom + TemplateEngine 可发现）----
    def test_register_user_template_discoverable(self):
        semantic = import_workflow(self._sample_comfy())
        # 以合法 task_type 同名覆盖方式注册（无创性，不改 P0 VALID_TASK_TYPES）
        result = register_user_template("shot_txt2img", semantic)
        self.assertTrue(result["registered"])
        self.assertTrue(result["usable_by_director"])
        self.assertTrue(os.path.isfile(os.path.join(_USER_CUSTOM, "shot_txt2img.json")))
        # director（TemplateEngine）能发现并加载 → 印证注册进 P0 映射
        wf, _sem, _schema = TemplateEngine().load_template("shot_txt2img")
        self.assertEqual(len(wf), 4)

    def test_register_rejects_invalid_task_type(self):
        semantic = import_workflow(self._sample_comfy())
        with self.assertRaises(ValueError):
            register_user_template("user_自定义名", semantic)

    def test_list_and_test_user_template(self):
        semantic = import_workflow(self._sample_comfy())
        register_user_template("shot_txt2img", semantic)
        lst = list_user_templates()
        self.assertTrue(any(t["template_id"] == "shot_txt2img" for t in lst))
        res = _tmpl_test_func("shot_txt2img")
        self.assertEqual(res["node_count"], 4)


if __name__ == "__main__":
    unittest.main()
