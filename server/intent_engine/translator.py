# -*- coding: utf-8 -*-
"""意图翻译引擎主入口（手册 §2.6 / 设计思路 §4.7 / §4.8）。

IntentTranslator.translate(intent) 严格七步：
  refresh → 选模板 → 选模型 → 选节点族 → 填占位符 → 注入 → 校验
输出合法、无残留占位符、引用完整的 ComfyUI workflow JSON；
并把本次选中的 checkpoint / 节点 / 种子记入 last_selection，供 translator 频道追溯（§4.1）。

视频专用 translate_video_intent：按节点族选视频模型；有首帧 → with_anchor，
无首帧 → text2video；按模型族填默认帧率（CogVideoX 49/8，Wan 81/16）。
"""
import logging

from .production_intent import ProductionIntent
from .resource_catalog import ResourceCatalog
from .node_family_mapper import NodeFamilyMapper
from .template_engine import TemplateEngine
from .parameter_injector import ParameterInjector
from .errors import FeatureUnavailable, TranslateError

LOG = logging.getLogger("intent_engine")

# 需要人脸锁（IP-Adapter）的任务类型
_FACE_LOCK_TASKS = {"character_genesis"}


class IntentTranslator:
    def __init__(self, comfy_url=None, service=None, catalog=None,
                 object_info=None, available_vram_mb=0, user_prefs=None):
        self.catalog = ResourceCatalog(comfy_url, service, catalog,
                                       object_info, available_vram_mb)
        self.templates = TemplateEngine()
        self.mapper = NodeFamilyMapper()
        self.user_prefs = user_prefs or {}
        self.last_selection = None

    async def translate(self, intent: ProductionIntent) -> dict:
        if intent.task_type.startswith("shot_video"):
            return await self.translate_video_intent(intent)
        return await self._translate_image(intent)

    # ---- 图像意图翻译（七步）----

    async def _translate_image(self, intent: ProductionIntent) -> dict:
        await self.catalog.refresh()                                   # 1
        template_id = self._select_template(intent.task_type)          # 2
        workflow, _sem_map, schema = self.templates.load_template(template_id)
        self.templates.validate_schema(schema)

        require_ip = intent.task_type in _FACE_LOCK_TASKS              # 3 选模型
        checkpoint = self.catalog.find_best_checkpoint(
            intent.intent_tags, require_ipadapter=require_ip)

        img_node = self.mapper.resolve(                                 # 4 选节点族
            "image_generation", self.catalog.object_info or {})
        face_node = None
        if require_ip:
            face_node = self.mapper.resolve(
                "face_lock", self.catalog.object_info or {})
            cv = self.catalog.find_clip_vision()
            if not cv:
                raise FeatureUnavailable(
                    "clip_vision", reason="IP-Adapter 需要 CLIP Vision 权重但未探测到")

        injector = ParameterInjector(schema)                           # 5 填占位符
        injector.set("checkpoint", checkpoint["name"])
        injector.set("positive_prompt", self._build_prompt(intent, "pos"))
        injector.set("negative_prompt", self._build_prompt(intent, "neg"))
        injector.set("seed", intent.generation_params.seed)
        injector.set("steps", intent.generation_params.steps)
        injector.set("cfg_scale", intent.generation_params.cfg_scale)
        injector.set("width", intent.generation_params.width)
        injector.set("height", intent.generation_params.height)
        injector.set("sampler", "euler")
        injector.set("scheduler", "normal")

        if require_ip:
            ip = self.catalog.find_best_ip_adapter(checkpoint["name"])
            injector.set("ip_adapter_model", ip["name"])
            injector.set("clip_vision_model", cv)
            injector.set("insightface", self.catalog.find_insightface())
            fv = next((a.face_vector for a in intent.bound_assets
                       if a.face_vector), None)
            if fv:
                injector.set("face_vector_base64", fv)

        if intent.task_type == "shot_img2img":
            injector.set("init_image",
                         intent.first_frame_path or "")
            injector.set("denoise", 0.6)

        injector.set_defaults()
        final = injector.inject(workflow)                              # 6 注入
        self._validate(final)                                          # 7 校验
        self.last_selection = {
            "task_type": intent.task_type,
            "template_id": template_id,
            "checkpoint": checkpoint["name"],
            "node": (face_node or img_node)["node_class"],
            "ip_adapter": (self.catalog.find_best_ip_adapter(checkpoint["name"])["name"]
                           if require_ip else None),
            "seed": intent.generation_params.seed,
        }
        return final

    # ---- 视频意图翻译（手册 §4.8）----

    async def translate_video_intent(self, intent: ProductionIntent) -> dict:
        await self.catalog.refresh()
        template_id = ("shot_video_with_anchor"
                       if intent.first_frame_path else "shot_video_text2video")
        workflow, _sem_map, schema = self.templates.load_template(template_id)
        self.templates.validate_schema(schema)

        video_family = self.mapper.resolve(
            "video_generation", self.catalog.object_info or {}, self.user_prefs)
        checkpoint = self.catalog.find_best_checkpoint(intent.intent_tags)

        injector = ParameterInjector(schema)
        injector.set("checkpoint", checkpoint["name"])
        injector.set("positive_prompt", self._build_prompt(intent, "pos"))
        injector.set("negative_prompt", self._build_prompt(intent, "neg"))
        injector.set("seed", intent.generation_params.seed)
        injector.set("steps", intent.generation_params.steps)
        injector.set("cfg_scale", intent.generation_params.cfg_scale)
        injector.set("width", intent.generation_params.width)
        injector.set("height", intent.generation_params.height)
        injector.set("sampler", "euler")
        injector.set("scheduler", "normal")

        # 按模型族填默认帧率（手册 §4.8）
        nc = video_family["node_class"]
        nf = intent.generation_params.num_frames
        fps = intent.generation_params.fps
        if nc == "CogVideoSampler":
            injector.set("num_frames", nf or 49)
            injector.set("fps", fps or 8)
        elif nc == "WanVideoSampler":
            injector.set("num_frames", nf or 81)
            injector.set("fps", fps or 16)
        else:
            injector.set("num_frames", nf or 49)
            injector.set("fps", fps or 8)

        if intent.first_frame_path:
            injector.set("init_image", intent.first_frame_path)
            injector.set("denoise", 0.6)

        injector.set_defaults()
        final = injector.inject(workflow)
        self._validate(final)
        self.last_selection = {
            "task_type": intent.task_type,
            "template_id": template_id,
            "checkpoint": checkpoint["name"],
            "node": nc,
            "seed": intent.generation_params.seed,
        }
        return final

    # ---- 辅助 ----

    @staticmethod
    def _select_template(task_type: str) -> str:
        return task_type  # 模板 id 与 task_type 同名

    @staticmethod
    def _build_prompt(intent: ProductionIntent, kind: str) -> str:
        """结构化 Prompt 组装（手册 §9 提示词纪律：禁止散写裸字符串）。

        M1 先用确定性令牌拼接；M2 角色卡管线再由 LLM 增强为含 trigger_words 的完整 prompt。
        """
        if kind == "neg":
            return ("low quality, blurry, watermark, deformed face, extra limbs, "
                    "multiple panels, text, bad anatomy")
        parts = list(intent.intent_tags)
        sc = intent.style_constraints
        if sc.lighting:
            parts.append(sc.lighting)
        if any(a.asset_type == "character" for a in intent.bound_assets):
            parts.append("character focus")
        parts += ["high quality", "detailed", "cinematic"]
        return ", ".join(parts)

    @staticmethod
    def _validate(workflow: dict):
        """结构合法性校验：每个节点有 class_type；所有 [id,slot] 引用指向存在的节点。"""
        ids = set(workflow.keys())
        for node_id, node in workflow.items():
            if "class_type" not in node:
                raise TranslateError("节点 %s 缺少 class_type" % node_id)
            for k, v in node.get("inputs", {}).items():
                if (isinstance(v, list) and len(v) == 2
                        and isinstance(v[0], int) and isinstance(v[1], int)):
                    if str(v[0]) not in ids:
                        raise TranslateError(
                            "节点 %s 输入 %s 引用了不存在的节点 %s" % (node_id, k, v[0]))
