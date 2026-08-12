# -*- coding: utf-8 -*-
"""意图翻译引擎（手册 SPEC-DIR-001 §3，开发优先级 §6 地基）。

核心职责：将「自然语言意图 / 结构化生产意图」转化为「ComfyUI 工作流 JSON」。
严格遵循铁律 1.2 —— **绝对禁止**调用方自主拼凑 ComfyUI 节点 JSON；
一切生成必须通过「结构化意图 + 模板填充」完成：

    意图解析  ->  资源映射（查 resource_catalog）  ->  模板填充（workflow_templates）

流程（对齐手册 §3.3 伪代码）：
  1. find_best_match(catalog['checkpoints'], style_tags)        选模型
  2. find_compatible_tool(catalog['ip_adapters'], model, ...)   选工具（IP-Adapter）
  3. load_template(task_type)                                   载预设模板
  4. 填充 {{占位符}} 并返回 ComfyUI API 格式工作流字典

调用方（ai_local / director）拿到返回的 wf 字典后，交给 Layer0 ComfyUIService 提交，
自己不再构造任何节点 JSON。
"""
import os
import re
import json

_BASE = os.path.dirname(os.path.abspath(__file__))
_CATALOG_PATH = os.path.join(_BASE, "resource_catalog.json")
_TEMPLATE_DIR = os.path.join(_BASE, "workflow_templates")

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class CatalogError(Exception):
    """资源目录缺失 / 无可匹配资源时抛出（诚实报错，不静默降级）。"""


def load_catalog(path=None):
    """加载资源目录 JSON。"""
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
    # 若完全无标签重叠且最佳者不可用，也应尽量给一个可用的兜底
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
    # 按 inject_type 精确匹配（face -> type=="face"）
    for a in bound_assets:
        it = a.get("inject_type")
        for t in compat:
            if it and t.get("type") == it:
                return t
        # 退化：bound_assets 未声明 inject_type 但有 asset_id -> 取首个兼容
        if a.get("asset_id") and compat:
            return compat[0]
    return compat[0] if compat else None


def load_template(task_type, template_dir=None):
    """加载工作流模板（含 {{占位符}}）。"""
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
    """从 bound_assets 取首个可作为身份参考图的 ComfyUI 内文件名（comfy_image）。

    仅在 bound_assets 确实提供了资产参考图时返回；未提供则返回空串，
    由调用方回退到 intent 显式传入的 identity_image。
    """
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
        # 风格路必须接在身份路之后；无身份时直接接 UnifiedLoader(10)
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
    """意图翻译主入口（手册 §3.3）。

    :param intent: 生产意图对象，字段见下；缺省值保证可独立单元测试。
        {
          "task_type": "shot_image" | "shot_image_gen_ip" | "shot_video",
          "style_tags": ["写实", "电影感"],
          "bound_assets": [{"asset_id":"ASSET-001","inject_type":"face","weight":0.85,
                            "comfy_image":"xxx.png"}],
          "prompt_text": "中年侦探在雨夜行走...",
          "negative_prompt": "...",            # 可选
          "identity_image": "ref.png",         # 可选（角色定妆照，ComfyUI 内文件名）
          "style_image": "style.png",          # 可选（全局风格参考图）
          "style_weight": 0.4,                 # 可选
          "generation_params": {"width":1024,"height":1024,"seed":42,"steps":28,"cfg":8.5}
        }
    :param catalog: 资源目录 dict（默认从 resource_catalog.json 载入）
    :return: ComfyUI API 格式工作流字典（编号节点），可直接交 ComfyUIService 提交。
    """
    catalog = catalog or load_catalog()
    style_tags = intent.get("style_tags", ["通用"])
    base_task = intent.get("task_type", "shot_image")
    # 兼容历史：task_type 只区分基础/视频，IP 由 bound_assets 决定
    bound_assets = intent.get("bound_assets") or []
    identity_image = intent.get("identity_image") or _first_asset_image(bound_assets)
    style_image = intent.get("style_image") or ""

    # 1. 选模型
    model = find_best_match(catalog.get("checkpoints", []), style_tags)
    # 2. 选工具（IP-Adapter 等）：优先按 bound_assets.inject_type 精确匹配；
    #    风格路/身份图存在但无 bound_assets（如全局风格参考图）时，退回模型兼容的首个 IP-Adapter。
    tool = find_compatible_tool(catalog.get("ip_adapters", []), model, bound_assets)
    if tool is None and (identity_image or style_image):
        comp = os.path.splitext(model["name"])[0]
        compat = [t for t in catalog.get("ip_adapters", [])
                  if os.path.splitext(t.get("compatible_checkpoint", ""))[0] in (comp, "", None)]
        tool = compat[0] if compat else None
    # IP 驱动判定：有可用 IP-Adapter 且提供了身份图或风格图（铁律1.1 资产/风格强约束）
    need_ip = (tool is not None) and (bool(identity_image) or bool(style_image))

    # 3. 选模板（img2img 首帧锚定优先于 IP 判定：锚定是纯像素承接，不叠 IP 路）
    if base_task == "shot_video":
        tpl = load_template("shot_video_gen")
    elif base_task == "shot_image_img2img":
        tpl = load_template("shot_image_img2img")
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
        # 首帧锚定：init_image 为 ComfyUI 内部文件名（由 Layer0 upload_image 预上传），denoise<1
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


# 便捷单图生成意图构造（供 ai_local 漫画管线等调用）
def build_shot_image_intent(prompt_text, style_tags=None, generation_params=None,
                            identity_image=None, style_image=None, style_weight=0.4,
                            bound_assets=None, negative_prompt=None,
                            init_image=None, denoise=None):
    """构造单图出图意图。

    - init_image / denoise 同时存在时，task_type 自动升级为 shot_image_img2img
      （首帧锚定模板）；调用方也可显式设置 intent["task_type"]="shot_image_img2img"。
    - init_image 为本地图路径或 ComfyUI 内部文件名均可：若为本地面路径，
      由 _comfy_text2img_via_template 经 Layer0 上传并回填为内部文件名。
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
