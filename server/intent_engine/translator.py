# -*- coding: utf-8 -*-
"""意图翻译引擎主入口（手册 §2.6 / 设计思路 §4.7 / §4.8）—— 单一运行时翻译器。

本模块是「意图翻译引擎」的**唯一实现**（M1 §6 融合收口：原顶层 intent_translator.py
与包内 translator.py 双管线已合并于此；templates_runtime/ 已并入手册指定的 templates/）。

提供两条等价入口，二者共用同一套模板目录 templates/（手册 §1.3/§2.7）与同一 catalog 语义，
杜绝「双模板目录 / 双翻译实现」的设计偏离：

  - IntentTranslator.translate(intent: ProductionIntent)  —— 手册七步语义契约入口（§2.1/§2.6），
    用于导演 Agent / 单元测试，使用 templates/ 手册模板 + TemplateEngine 语义映射
    + ResourceCatalog.refresh() 实时探测 /object_info。
  - IntentTranslator.translate_intent(intent_dict) / translate_typed(task_type, values)
    —— 生产同步入口（供 ai_local 调用），使用 templates/ 生产模板 + 静态
    resource_catalog.json（经 ResourceCatalog 离线注入，零网络），含 IP 节点链手术
    （_wire_ip_chain）。模块级 translate_intent_to_workflow / translate_typed_workflow /
    build_*_intent 是其薄封装，保持 ai_local 调用点零改动。

铁律 1.2 严守：绝不手搓 ComfyUI 节点 JSON，一切经「结构化意图 + 模板填充」完成。
种子派生红线（§9）：_deterministic_seed 使用 SHA256（禁用 md5/hash()/random）。
"""
import os
import re
import json
import hashlib
import logging

from .production_intent import ProductionIntent
from .resource_catalog import ResourceCatalog
from .node_family_mapper import NodeFamilyMapper
from .template_engine import TemplateEngine
from .parameter_injector import ParameterInjector
from .errors import FeatureUnavailable, TranslateError

LOG = logging.getLogger("intent_engine")

# intent_engine/ 为 server/intent_engine；server 根为上一级
_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASE = os.path.dirname(os.path.abspath(__file__))
# 手册 §1.3/§2.7 指定目录：生产模板已并入此处，与手册七步模板共用同一目录
_TEMPLATE_DIR = os.path.join(_BASE, "templates")
_CATALOG_PATH = os.path.join(_SERVER, "resource_catalog.json")

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")

# 需要人脸锁（IP-Adapter）的任务类型（手册七步路径使用）
_FACE_LOCK_TASKS = {"character_genesis"}


class CatalogError(Exception):
    """资源目录缺失 / 无可匹配资源时抛出（诚实报错，不静默降级）。"""


# ============================================================================
# 模块级生产入口（供 ai_local 导入，调用点零改动）
# ============================================================================

def load_catalog(path=None):
    """加载资源目录 JSON（静态 resource_catalog.json）。"""
    p = path or _CATALOG_PATH
    if not os.path.isfile(p):
        raise CatalogError("资源目录不存在: %s" % p)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def find_best_match(checkpoints, style_tags):
    """按 style_tags 与 checkpoint.tags 的重叠度选最优底模（手册 §3.3 第1步）。

    评分规则：标签重叠度为主；**available=true 的权重强制优先**（诚实降级：
    未放入权重的底模不可选，避免生成时找不到 checkpoint 报错）。两者皆无匹配时
    退回首个 available 底模；全不可用时退回首个（由下游 ComfyUI 报错暴露缺权重）。
    """
    style_tags = list(style_tags or [])
    cands = list(checkpoints or [])
    if not cands:
        raise CatalogError("资源目录无可匹配 checkpoint: tags=%s" % style_tags)

    def _score(cp):
        tags = set(cp.get("tags", []))
        overlap = sum(1 for t in style_tags if t in tags)
        avail = 1 if cp.get("available", True) else 0
        # available 作为高位权重，确保“可用且标签匹配”永远胜出
        return (avail, overlap)

    ranked = sorted(cands, key=_score, reverse=True)
    best = ranked[0]
    if best.get("available", True) is False:
        for cp in ranked:
            if cp.get("available", True) is True:
                return cp
    return best


def find_compatible_tool(ip_adapters, model, bound_assets):
    """根据绑定资产类型匹配兼容的 IP-Adapter（手册 §3.3 第2步）。

    仅当 bound_assets 非空且有 face/style 类注入需求时才返回工具；否则返回 None
    （表示无需 IP-Adapter，走纯文生图模板）。
    """
    if not bound_assets:
        return None
    comp = os.path.splitext(model.get("name", ""))[0]  # 去掉扩展名再比，避免 "x.safetensors" vs "x"
    compat = [t for t in (ip_adapters or [])
              if os.path.splitext(t.get("compatible_checkpoint", ""))[0] in (comp, "", None)]
    if not compat:
        return None
    for a in bound_assets:
        it = a.get("inject_type")
        for t in compat:
            if it and t.get("type") == it:
                return t
        if a.get("asset_id") and compat:
            return compat[0]
    return compat[0] if compat else None


def load_template(task_type, template_dir=None):
    """加载工作流模板（含 {{占位符}}）于手册指定的 templates/ 目录。"""
    d = template_dir or _TEMPLATE_DIR
    fp = os.path.join(d, "%s.json" % task_type)
    if not os.path.isfile(fp):
        raise CatalogError("工作流模板不存在: %s" % fp)
    with open(fp, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def _fill_placeholders(obj, values):
    """递归填充 {{key}} 占位符：整串单占位符替换为带类型的值；嵌入式替换为字符串。"""
    if isinstance(obj, dict):
        return {k: _fill_placeholders(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill_placeholders(v, values) for v in obj]
    if isinstance(obj, str):
        m = _PLACEHOLDER_RE.fullmatch(obj.strip())
        if m:
            key = m.group(1)
            return values[key] if key in values else obj
        return _PLACEHOLDER_RE.sub(lambda mm: str(values.get(mm.group(1), mm.group(0))), obj)
    return obj


def _first_asset_image(bound_assets):
    """从 bound_assets 取首个可作为身份参考图的 ComfyUI 内文件名（comfy_image）。"""
    for a in (bound_assets or []):
        if a.get("inject_type") in (None, "face") or a.get("asset_id"):
            if a.get("comfy_image"):
                return a["comfy_image"]
    return ""


def _first_asset_weight(bound_assets, default=0.85):
    for a in (bound_assets or []):
        if a.get("inject_type") in (None, "face") or a.get("asset_id"):
            return float(a.get("weight", default))
    return float(default)


def _wire_ip_chain(prompt, identity_image, style_image):
    """按实际提供的参考图裁剪 IP 节点并重连 KSampler.model（手册 §3.2 / §5.2）。

    - 有身份图：保留 13/15；否则移除。
    - 有风格图：保留 23/20，且节点 20.model 指向身份节点(15)或 UnifiedLoader(10)；否则移除。
    - KSampler.model 终态：风格路 20 > 身份路 15 > 底模直出 4。
    - 均无（理论不会走到此分支）：KSampler.model -> 4（底模直出），移除 10/11。
    """
    has_id = bool(identity_image)
    has_style = bool(style_image)
    if not has_id:
        for n in ("13", "15"):
            prompt.pop(n, None)
    if not has_style:
        for n in ("23", "20"):
            prompt.pop(n, None)
    if has_style:
        prompt["20"]["inputs"]["model"] = ["15", 0] if has_id else ["10", 0]
        prompt["3"]["inputs"]["model"] = ["20", 0]
    elif has_id:
        prompt["3"]["inputs"]["model"] = ["15", 0]
    else:
        prompt["3"]["inputs"]["model"] = ["4", 0]
        for n in ("10", "11"):
            prompt.pop(n, None)
    return prompt


def translate_intent_to_workflow(intent, catalog=None):
    """意图翻译主入口（手册 §3.3）—— 薄封装，委托 IntentTranslator 生产同步路径。"""
    return IntentTranslator().translate_intent(intent, catalog=catalog)


def translate_typed_workflow(task_type, values):
    """通用模板填充（手册 §3.3）—— 薄封装，委托 IntentTranslator.translate_typed。"""
    return IntentTranslator().translate_typed(task_type, values)


def build_shot_image_intent(prompt_text, style_tags=None, generation_params=None,
                            identity_image=None, style_image=None, style_weight=0.4,
                            bound_assets=None, negative_prompt=None,
                            init_image=None, denoise=None):
    """构造单图出图意图。

    - init_image / denoise 同时存在时，task_type 自动升级为 shot_image_img2img
      （首帧锚定模板）；调用方也可显式设置 intent["task_type"]="shot_image_img2img"。
    - init_image 为本地图路径或 ComfyUI 内部文件名均可：若为本地面路径，
      由下层经 Layer0 上传并回填为内部文件名。
    """
    task_type = "shot_image_img2img" if (init_image and denoise is not None) else "shot_image"
    return {
        "task_type": task_type,
        "style_tags": style_tags or ["写实", "通用"],
        "bound_assets": bound_assets or [],
        "prompt_text": prompt_text,
        "negative_prompt": negative_prompt,
        "identity_image": identity_image,
        "style_image": style_image,
        "style_weight": style_weight,
        "generation_params": generation_params or {},
        "init_image": init_image,
        "denoise": denoise,
    }


def _deterministic_seed(key):
    """由稳定字符串（角色ID/名）派生确定性种子，保证同一角色多次生成结果一致。

    严守种子派生红线：使用 SHA256（禁用 md5/hash()/random）。角色卡首图不再依赖
    随机 seed，而是锚定角色身份（character_id）稳定产出，后续 IP 复用也以此图为参考，
    跨阶段一致。
    """
    h = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
    return int(h[:8], 16) % (2 ** 31 - 1)


def _build_character_positive(attrs, style_tags):
    """由结构化角色属性确定性拼接正向提示词（中文，与项目既有中文 prompt 风格一致）。

    属性来自剧本/资产定义（铁律 1.1：锚定资产，禁止自由创意 Prompt 凭空生成角色）。
    """
    parts = []
    gender = (attrs or {}).get("gender") or ""
    if gender:
        parts.append(str(gender))
    age = (attrs or {}).get("age")
    if age is not None:
        try:
            age = int(age)
            if age <= 12:
                parts.append("儿童")
            elif age < 18:
                parts.append("青少年")
            elif age < 35:
                parts.append("青年")
            elif age < 55:
                parts.append("中年")
            else:
                parts.append("老年")
        except Exception:
            pass
    for ap in ((attrs or {}).get("appearance") or []):
        if ap:
            parts.append(str(ap))
    for st in (style_tags or []):
        if st and st not in ("通用",):
            parts.append(str(st))
    parts.append("角色定妆照")
    parts.append("单人半身肖像")
    parts.append("高清细节")
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return "，".join(out)


def build_character_card_intent(attrs, character_id=None, style_tags=None,
                                generation_params=None, negative_prompt=None):
    """构造角色卡（定妆照/概念图）生成意图——属性驱动，确定性种子（草稿 #1）。

    解决草稿 #1 点名的「鸡生蛋」矛盾：角色卡首张参考图必须在资产 ID 可用前生成，
    但铁律 1.1 禁止仅靠自由 Prompt 生成角色。本函数以**结构化角色属性**
    （name/age/gender/appearance）确定性拼装正向提示词，并以 character_id 哈希
    派生确定性 seed —— 既锚定资产（属性来自剧本/资产定义），又保证稳定不随机。
    """
    attrs = attrs or {}
    seed_key = character_id or attrs.get("name") or "character"
    gp = dict(generation_params or {})
    if "seed" not in gp:
        gp["seed"] = _deterministic_seed(seed_key)   # 修复「每次随机」
    gp.setdefault("width", 512)    # 默认肖像比例 2:3
    gp.setdefault("height", 768)
    gp.setdefault("steps", 28)
    gp.setdefault("cfg", 8.5)
    positive = _build_character_positive(attrs, style_tags)
    default_neg = (
        "low quality, blurry, watermark, text, extra limbs, deformed hands, "
        "mutated fingers, bad anatomy, asymmetric eyes, deformed face, "
        "extra fingers, missing limbs, distorted proportions, ugly, disfigured"
    )
    return {
        "task_type": "character_card",
        "style_tags": style_tags or ["写实", "通用"],
        "bound_assets": [],            # 首张卡无参考图，纯文生图（鸡生蛋环节）
        "prompt_text": positive,
        "negative_prompt": negative_prompt or default_neg,
        "identity_image": "",
        "style_image": "",
        "style_weight": 0.4,
        "generation_params": gp,
    }


# ============================================================================
# 唯一运行时翻译器
# ============================================================================

class IntentTranslator:
    def __init__(self, comfy_url=None, service=None, catalog=None,
                 object_info=None, available_vram_mb=0, user_prefs=None):
        self.catalog = ResourceCatalog(comfy_url, service, catalog,
                                       object_info, available_vram_mb)
        self.templates = TemplateEngine()
        self.mapper = NodeFamilyMapper()
        self.user_prefs = user_prefs or {}
        self.last_selection = None

    # ---- 手册七步契约入口（ProductionIntent，用于导演 Agent / 测试）----

    async def translate(self, intent: ProductionIntent) -> dict:
        if intent.task_type.startswith("shot_video"):
            return await self.translate_video_intent(intent)
        return await self._translate_image(intent)

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

    # ---- 生产同步入口（供 ai_local 调用，零网络）----

    def translate_intent(self, intent, catalog=None):
        """生产意图翻译（同步）：等价于旧 facade.translate_intent_to_workflow。

        使用 templates/ 生产模板 + 静态 resource_catalog.json（零网络），
        含 IP 节点链手术（_wire_ip_chain）。返回可直接交 ComfyUIService 的 workflow dict。
        """
        catalog = catalog or load_catalog()
        style_tags = intent.get("style_tags", ["通用"])
        base_task = intent.get("task_type", "shot_image")
        bound_assets = intent.get("bound_assets") or []
        identity_image = intent.get("identity_image") or _first_asset_image(bound_assets)
        style_image = intent.get("style_image") or ""

        # 1. 选模型
        model = find_best_match(catalog.get("checkpoints", []), style_tags)
        # 2. 选工具（IP-Adapter 等）
        tool = find_compatible_tool(catalog.get("ip_adapters", []), model, bound_assets)
        if tool is None and (identity_image or style_image):
            comp = os.path.splitext(model["name"])[0]
            compat = [t for t in catalog.get("ip_adapters", [])
                      if os.path.splitext(t.get("compatible_checkpoint", ""))[0] in (comp, "", None)]
            tool = compat[0] if compat else None
        need_ip = (tool is not None) and (bool(identity_image) or bool(style_image))

        # 3. 选模板（img2img 首帧锚定优先于 IP 判定）
        if base_task == "shot_video":
            tpl = load_template("shot_video_gen")
        elif base_task == "shot_image_img2img":
            tpl = load_template("shot_image_img2img")
        elif base_task == "character_card":
            tpl = load_template("character_card_gen")
        elif need_ip:
            tpl = load_template("shot_image_gen_ip")
        else:
            tpl = load_template("shot_image_gen")
        prompt = {k: v for k, v in tpl["prompt"].items()}  # 浅拷贝，避免污染模板

        # 4. 组装占位符值
        gp = intent.get("generation_params", {}) or {}
        values = {
            "checkpoint": model["name"],
            "positive_prompt": intent.get("prompt_text", ""),
            "negative_prompt": intent.get("negative_prompt", "low quality, blurry, watermark, text"),
            "seed": int(gp.get("seed", 42)),
            "steps": int(gp.get("steps", 28)),
            "cfg": float(gp.get("cfg", 8.5)),
            "width": int(gp.get("width", 768)),
            "height": int(gp.get("height", 1024)),
        }
        if base_task == "shot_image_img2img":
            values["init_image"] = intent.get("init_image", "")
            values["denoise"] = float(intent.get("denoise", 0.5))
        elif need_ip and tool:
            values["ip_adapter_name"] = tool["name"]
            values["ip_adapter_preset"] = tool.get("preset", "STANDARD (medium strength)")
            values["identity_image"] = identity_image or ""
            values["identity_weight"] = _first_asset_weight(bound_assets)
            values["style_image"] = style_image or ""
            values["style_weight"] = float(intent.get("style_weight", 0.4))

        filled = _fill_placeholders(prompt, values)
        # 资源映射后的节点链整理（按需裁剪 IP 节点）
        if need_ip:
            filled = _wire_ip_chain(filled, identity_image, style_image)
        return filled

    def translate_typed(self, task_type, values):
        """通用模板填充（同步）：等价于旧 facade.translate_typed_workflow。

        用于视频生成 / 人脸修复 / ControlNet 等场景——具体权重名/尺寸已由调用方
        解析（如 Wan 模型名、GFPGAN 权重、ControlNet 名），此处只做结构化模板填充，
        **不构造任何节点拓扑**（拓扑由 intent_engine/templates/* 决定，铁律 1.2）。
        """
        tpl = load_template(task_type)
        prompt = {k: v for k, v in tpl["prompt"].items()}
        return _fill_placeholders(prompt, values)

    # ---- 辅助 ----

    @staticmethod
    def _select_template(task_type: str) -> str:
        return task_type  # 模板 id 与 task_type 同名

    @staticmethod
    def _build_prompt(intent: ProductionIntent, kind: str) -> str:
        """结构化 Prompt 组装（手册 §9 提示词纪律：禁止散写裸字符串）。"""
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
