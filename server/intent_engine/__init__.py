# -*- coding: utf-8 -*-
"""意图翻译引擎包（手册 M1 / 设计思路 §4）。

导演 Agent 与 ComfyUI 之间的「精通 ComfyUI 的翻译官」：结构化意图 → 资源目录匹配 →
语义模板填充 → 合法 ComfyUI workflow。绝不由 LLM 手搓节点 JSON（铁律 2.2 Rule First）。

子模块：
  production_intent  意图契约（BoundAsset/GenerationParams/StyleConstraints/ProductionIntent）
  resource_catalog   运行时探测 + 双重过滤 + 语义匹配
  node_family_mapper 节点族注册表 + 自动降级
  template_engine    语义别名 → 数字节点 ID 动态映射
  parameter_injector 占位符注入（类型/范围校验、无残留）+ derive_seed(SHA256)
  translator         翻译主入口（七步）+ 视频专用分支
"""
from .production_intent import (
    BoundAsset, GenerationParams, StyleConstraints, ProductionIntent, VALID_TASK_TYPES)
from .resource_catalog import ResourceCatalog, _infer_auto_tags
from .node_family_mapper import NodeFamilyMapper, NODE_FAMILY_REGISTRY
from .template_engine import TemplateEngine
from .parameter_injector import ParameterInjector, derive_seed
from .translator import IntentTranslator
from .errors import FeatureUnavailable, TemplateError, InjectError, TranslateError
# M1 §6 融合：运行时翻译 facade（原顶层 intent_translator.py 收敛进本包，消除双管线）
from .translator_facade import (
    translate_intent_to_workflow, translate_typed_workflow,
    build_shot_image_intent, build_character_card_intent,
    load_catalog, find_best_match, find_compatible_tool, load_template,
    CatalogError, _build_character_positive,
)

__all__ = [
    "BoundAsset", "GenerationParams", "StyleConstraints", "ProductionIntent",
    "VALID_TASK_TYPES",
    "ResourceCatalog", "NodeFamilyMapper", "NODE_FAMILY_REGISTRY", "TemplateEngine",
    "ParameterInjector", "derive_seed", "IntentTranslator",
    "FeatureUnavailable", "TemplateError", "InjectError", "TranslateError",
    "translate_intent_to_workflow", "translate_typed_workflow",
    "build_shot_image_intent", "build_character_card_intent",
    "load_catalog", "find_best_match", "find_compatible_tool", "load_template",
    "CatalogError",
]
