# -*- coding: utf-8 -*-
"""语义模板库（手册 §2.4 / 设计思路 §4.5）。

职责：用「语义别名」编写模板（checkpoint_loader / ip_adapter / ksampler …），
运行时动态映射为真实数字节点 ID；模板与具体节点 ID 解耦，新增模型零改代码。

- load_template(template_id) -> (workflow, semantic_to_id, schema)
- discover_templates()：扫描 templates/*.json 与 templates/user_custom/*.json，
  user_custom 同名模板覆盖系统模板。
- placeholder_schema：声明每个占位符的 type / source / default / range / required。
"""
import os
import json
import logging

from .errors import TemplateError

LOG = logging.getLogger("intent_engine")

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


class TemplateEngine:
    def __init__(self, templates_dir=None):
        self.templates_dir = templates_dir or _TEMPLATES_DIR

    # ---- §2.4.3 模板发现 ----

    def discover_templates(self):
        """返回 {template_id: 绝对路径}，user_custom 同名覆盖系统模板。"""
        found = {}
        for root in (self.templates_dir, os.path.join(self.templates_dir, "user_custom")):
            if not os.path.isdir(root):
                continue
            for fn in os.listdir(root):
                if fn.endswith(".json"):
                    tid = fn[:-5]
                    found[tid] = os.path.join(root, fn)
        return found

    def get_template_ids(self):
        return sorted(self.discover_templates().keys())

    # ---- §2.4.2 节点 ID 动态映射 ----

    def load_template(self, template_id):
        paths = self.discover_templates()
        path = paths.get(template_id)
        if not path:
            raise TemplateError("模板不存在：%s（已知：%s）" % (template_id, list(paths.keys())))
        with open(path, "r", encoding="utf-8") as f:
            template = json.load(f)

        semantic_nodes = template.get("semantic_nodes")
        if not semantic_nodes:
            raise TemplateError("模板 %s 缺少 semantic_nodes" % template_id)

        workflow, semantic_to_id = {}, {}
        id_counter = 1
        for semantic_name, node_def in semantic_nodes.items():
            node_id = str(id_counter)
            semantic_to_id[semantic_name] = node_id
            id_counter += 1
            workflow[node_id] = {
                "inputs": {},
                "class_type": node_def["class_type"],
                "_meta": {"title": semantic_name},
            }
        # 解析引用：["ip_adapter", 0] → [semantic_to_id["ip_adapter"], 0]
        for semantic_name, node_def in semantic_nodes.items():
            node_id = semantic_to_id[semantic_name]
            for k, v in node_def.get("inputs", {}).items():
                if (isinstance(v, list) and len(v) == 2
                        and isinstance(v[0], str) and v[0] in semantic_to_id):
                    workflow[node_id]["inputs"][k] = [semantic_to_id[v[0]], v[1]]
                else:
                    workflow[node_id]["inputs"][k] = v

        schema = self._normalize_schema(template.get("placeholder_schema", {}))
        self._validate_no_residual_semantic(workflow)
        return workflow, semantic_to_id, schema

    # ---- §2.4.4 占位符 schema 校验 ----

    @staticmethod
    def _normalize_schema(raw):
        """键统一归一为裸名（兼容 '{{checkpoint}}' 与 'checkpoint'）。"""
        norm = {}
        for key, spec in (raw or {}).items():
            name = key.strip()
            if name.startswith("{{") and name.endswith("}}"):
                name = name[2:-2].strip()
            spec = dict(spec or {})
            norm[name] = spec
        return norm

    def _validate_no_residual_semantic(self, workflow):
        """所有 [语义名, 槽位] 引用必须已被解析为 [节点ID, 槽位]，否则抛错。

        节点 ID 为字符串（"1".."N"，与 ComfyUI 一致），故已解析的引用形如
        ["1", 0]——其首元素是 workflow 已有的节点键，合法；仅当首元素是
        未在 semantic_to_id 中定义、且不属于任何节点键的语义名时才判定残留。
        """
        ids = set(workflow.keys())
        for node_id, node in workflow.items():
            for k, v in node.get("inputs", {}).items():
                if (isinstance(v, list) and len(v) == 2
                        and isinstance(v[0], str) and v[0] not in ids):
                    raise TemplateError(
                        "模板残留未解析语义引用 %s @ node %s input %s" % (v, node_id, k))

    def validate_schema(self, schema):
        """校验 schema 字段合法性（type 必须已知）。"""
        known_types = {"string", "int", "float", "base64"}
        for name, spec in schema.items():
            t = spec.get("type")
            if t not in known_types:
                raise TemplateError("占位符 %s 类型非法：%s" % (name, t))
        return True
