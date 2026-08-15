# -*- coding: utf-8 -*-
"""P3 M2 用户自定义模板 + 可视化编辑器后端（编排层可插拔）。

衔接（手册 §0.3）：自定义模板注册进 P0 M3 的 task_type→template_id 映射
  —— 落地方式 = 写 intent_engine/templates/user_custom/{task_type}.json
     （TemplateEngine.discover_templates 扫描 user_custom 且同名覆盖系统模板，
       director 经 _select_template(task_type) 命中即自动选用，无创性不改 P0）。

实现逻辑铁律（§0.2）：import_workflow 三步（识别占位符 → 语义节点 → schema）。
红线（§5）：禁原样存 JSON 不解析（必须占位符识别 + 注册进 P0 映射）；
          禁 hash() 生成 ID（template_id 由调用方按合法 task_type 指定）。
"""
import os
import re
import json
import logging

from intent_engine.template_engine import TemplateEngine
from intent_engine.production_intent import VALID_TASK_TYPES

LOG = logging.getLogger("user_templates")

_USER_CUSTOM_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "intent_engine", "templates", "user_custom")

# 占位符识别规则：字段名/类名 → 语义占位符名 + 类型
# （手册 §2：ckpt_name/model_name → checkpoint；text(CLIPTextEncode) → positive_prompt；
#  seed/steps/cfg/strength/width/height → 同名词占位符）
_TYPE_BY_PLACEHOLDER = {
    "checkpoint": "string",
    "positive_prompt": "string",
    "negative_prompt": "string",
    "seed": "int",
    "steps": "int",
    "cfg_scale": "float",
    "strength": "float",
    "width": "int",
    "height": "int",
}


def _is_link(val):
    return (isinstance(val, list) and len(val) == 2
            and isinstance(val[0], (int, str)) and isinstance(val[1], int))


def _classify_field(class_type, key):
    k = (key or "").lower()
    ct = (class_type or "").lower()
    if k in ("ckpt_name", "model_name") or k == "ckpt":
        return "checkpoint"
    if k == "text" and ("clip" in ct or "encode" in ct or "text" in ct):
        return "positive_prompt"
    if k in ("negative", "neg_text") or "negative" in k:
        return "negative_prompt"
    if k == "seed":
        return "seed"
    if k == "steps":
        return "steps"
    if k in ("cfg", "cfg_scale"):
        return "cfg_scale"
    if k == "strength":
        return "strength"
    if k == "width":
        return "width"
    if k == "height":
        return "height"
    return None


def detect_placeholders(comfy_json):
    """输入：ComfyUI workflow {node_id:{class_type,inputs}}
       处理：遍历节点输入，按字段名/类名规则识别可参数化字段（跳过连线引用）
       输出：placeholder_map = {占位符名: {"nodes":[(node_id,input_key)], "type","default"}}"""
    placeholder_map = {}
    for nid, node in (comfy_json or {}).items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type") or ""
        for key, val in (node.get("inputs") or {}).items():
            if _is_link(val):
                continue  # 节点拓扑连线，不是占位符
            pname = _classify_field(ct, key)
            if not pname:
                continue
            entry = placeholder_map.setdefault(
                pname, {"nodes": [], "type": _TYPE_BY_PLACEHOLDER[pname], "default": None})
            entry["nodes"].append((str(nid), key))
            if entry["default"] is None and val is not None:
                entry["default"] = val
    return placeholder_map


def to_semantic_nodes(comfy_json, placeholder_map):
    """输入：原始 workflow + 占位符映射；处理：每个节点分配语义名、引用解析为 [语义名,槽]、
       参数化字段值替换为 {{占位符}}；输出：semantic_nodes dict。"""
    rev = {}
    for pname, info in placeholder_map.items():
        for (nid, key) in info["nodes"]:
            rev[(str(nid), key)] = pname

    # 节点 → 语义名（类名去噪 + 重复计数）
    sem_names, counters = {}, {}
    for nid, node in (comfy_json or {}).items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type") or "node"
        base = re.sub(r"[^a-z0-9]", "_", ct.lower()).strip("_") or "node"
        counters[base] = counters.get(base, 0) + 1
        sem_names[str(nid)] = ("%s_%d" % (base, counters[base])
                               if counters[base] > 1 else base)

    semantic_nodes = {}
    for nid, node in (comfy_json or {}).items():
        if not isinstance(node, dict):
            continue
        sname = sem_names[str(nid)]
        ct = node.get("class_type") or ""
        new_inputs = {}
        for key, val in (node.get("inputs") or {}).items():
            if _is_link(val):
                other = str(val[0])
                new_inputs[key] = [sem_names.get(other, other), val[1]]
            elif (str(nid), key) in rev:
                new_inputs[key] = "{{%s}}" % rev[(str(nid), key)]
            else:
                new_inputs[key] = val
        semantic_nodes[sname] = {"class_type": ct, "inputs": new_inputs}
    return semantic_nodes


def build_placeholder_schema(placeholder_map):
    """输入：占位符映射；处理：生成每个占位符的 type/default/source；输出：schema dict。"""
    schema = {}
    for pname, info in placeholder_map.items():
        schema[pname] = {
            "type": info["type"],
            "default": info["default"],
            "source": "user_template",
            "required": False,
        }
    return schema


def import_workflow(comfy_json):
    """输入：ComfyUI workflow JSON；处理：三步（识别占位符→语义节点→schema）；
       输出：{semantic_nodes, placeholder_schema, detected_placeholders}。"""
    placeholder_map = detect_placeholders(comfy_json)
    semantic_nodes = to_semantic_nodes(comfy_json, placeholder_map)
    schema = build_placeholder_schema(placeholder_map)
    return {
        "semantic_nodes": semantic_nodes,
        "placeholder_schema": schema,
        "detected_placeholders": sorted(placeholder_map.keys()),
    }


def save_user_template(template_id, semantic_template):
    """输入：template_id + 语义模板；处理：写 user_custom/{template_id}.json；
       输出：落盘路径。"""
    os.makedirs(_USER_CUSTOM_DIR, exist_ok=True)
    path = os.path.join(_USER_CUSTOM_DIR, "%s.json" % template_id)
    data = {
        "template_id": template_id,
        "semantic_nodes": semantic_template["semantic_nodes"],
        "placeholder_schema": semantic_template.get("placeholder_schema", {}),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def register_user_template(template_id, semantic_template):
    """输入：template_id（须为合法 task_type，同名覆盖 P0 系统模板）+ 语义模板；
       处理：① 写 user_custom；② 校验 TemplateEngine 可发现并加载（= 注册进 P0 映射）
       输出：{registered, template_id, path, usable_by_director}。"""
    if template_id not in VALID_TASK_TYPES:
        raise ValueError(
            "template_id 必须是合法 task_type 之一（以同名覆盖方式注册进 P0 映射）：%s"
            % (VALID_TASK_TYPES,))
    path = save_user_template(template_id, semantic_template)
    # 注册验证：TemplateEngine 能发现并加载（director 经 _select_template 命中）
    engine = TemplateEngine()
    usable = template_id in engine.get_template_ids()
    return {
        "registered": True,
        "template_id": template_id,
        "path": path,
        "usable_by_director": usable,
    }


def list_user_templates():
    """输入：无；处理：扫描 user_custom；输出：模板摘要列表。"""
    if not os.path.isdir(_USER_CUSTOM_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(_USER_CUSTOM_DIR)):
        if not fn.endswith(".json"):
            continue
        tid = fn[:-5]
        try:
            with open(os.path.join(_USER_CUSTOM_DIR, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
            out.append({
                "template_id": tid,
                "node_count": len(d.get("semantic_nodes") or {}),
                "placeholders": list((d.get("placeholder_schema") or {}).keys()),
            })
        except Exception as e:
            LOG.warning("[user_templates] 读取失败 %s: %s", fn, e)
    return out


def test_user_template(template_id):
    """输入：template_id；处理：经 TemplateEngine 加载并校验；
       输出：{template_id, node_count, schema}（验证注册生效）。"""
    engine = TemplateEngine()
    wf, _sem, schema = engine.load_template(template_id)
    return {"template_id": template_id, "node_count": len(wf), "schema": schema}
