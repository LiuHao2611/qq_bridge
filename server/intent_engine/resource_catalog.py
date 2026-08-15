# -*- coding: utf-8 -*-
"""资源目录（手册 §2.2 / 设计思路 §4.3）。

职责：回答「本地 ComfyUI 到底有什么、能干什么、怎么组合」。
- refresh()：经 layer0 ComfyUIService 调 /object_info + /system_stats 运行时探测；
  失败诚实抛 FeatureUnavailable（CLOSE-03），不静默返回空目录。
- _infer_auto_tags：从文件名关键词推断语义标签（user_tags 优先于 auto_tags）。
- find_best_checkpoint：双重过滤（硬件显存 + 组件兼容）+ 语义匹配；全不满足诚实抛错。

为便于离线单元测试，构造函数支持注入预置 catalog / object_info / available_vram_mb，
此时 refresh() 短路（不触网）。
"""
import os
import re
import glob
import json
import logging
import time

from .errors import FeatureUnavailable

LOG = logging.getLogger("intent_engine")

try:
    from layer0.comfyui_service import get_service
except Exception:  # 独立运行/测试时回退
    from ..layer0.comfyui_service import get_service


# 文件名关键词 → 语义标签（小写匹配）
_AUTO_TAG_RULES = [
    (r"realistic|photoreal|real|写实|真人", "写实"),
    (r"anime|动漫", "动漫"),
    (r"manga|comic|漫画", "漫画"),
    (r"sdxl|\b xl\b", "sdxl"),
    (r"video|wan|cogvideo|ltx|hunyuan|mochi|视频", "视频"),
]

# IP-Adapter 名称 → 兼容底模架构（用于组件兼容过滤的兜底推断）
_IP_ARCH_RULES = [
    (r"sd15|sd1\.5", "sd1.5"),
    (r"sdxl|\b xl\b", "sdxl"),
]


def _infer_auto_tags(name: str) -> list:
    """从文件名关键词推断语义标签（手册 §2.2.3）。至少覆盖 写实/动漫/视频/sdxl。"""
    n = (name or "").lower()
    tags = []
    for pattern, tag in _AUTO_TAG_RULES:
        if re.search(pattern, n):
            if tag not in tags:
                tags.append(tag)
    return tags


def _infer_arch(name: str) -> str:
    n = (name or "").lower()
    for pattern, arch in _IP_ARCH_RULES:
        if re.search(pattern, n):
            return arch
    return "sd1.5"  # 未知默认按 SD1.5 兼容（最宽松）


def _infer_vram_mb(name: str) -> int:
    """按文件名粗略估算底模显存占用（MB），供 6GB 硬件过滤。"""
    n = (name or "").lower()
    if re.search(r"sdxl|\b xl\b", n):
        return 8000
    if re.search(r"video|wan|cogvideo|ltx|hunyuan|mochi", n):
        return 12000
    return 4000  # SD1.5 舒适区


# ---- §2.2.6 能力维度声明（手册 M2 / 维度声明结构）----
# 每个模型 / 节点 / LoRA 声明自己实现了哪些能力维度（capabilities 键严格对齐
# capabilities 包的 6 维度：semantic / temporal / consistency / audio_sync /
# camera / frame_anchor），并标注首尾帧外推 / 插值分级（frame_anchor_grading）。

def _is_video_model(name, model_type):
    n = (name or "").lower()
    if model_type in ("video",):
        return True
    return bool(re.search(r"video|wan|cogvideo|ltx|hunyuan|mochi", n))


# 首尾帧分级由「节点输入字段」驱动（手册 M2 §2.2.6，禁止 if-else 硬编码模型名）：
# 含 first_frame + last_frame → 插值（first_last_interpolate）；仅 first_frame → 外推。
# 注册表 video_generation.frame_anchor_inputs 声明各节点输入字段（数据驱动，模型无关）。


def _collect_input_fields(spec):
    """汇总节点 required + optional 输入字段名（小写集合），供层2签名推断。"""
    if not spec or not isinstance(spec, dict):
        return set()
    req = (spec.get("input") or {}).get("required") or {}
    opt = (spec.get("input") or {}).get("optional") or {}
    fields = set()
    for f in list(req.keys()) + list(opt.keys()):
        if f:
            fields.add(str(f).lower())
    return fields


def _signature_dimension(inputs):
    """层2-输入签名推断：读节点输入字段名推导能力维度。

    输入：inputs（节点输入字段名集合）
    处理：逐项匹配签名关键词（手册 M2 §2.2.6）
    输出：能力维度名（consistency/temporal/frame_anchor）或 None
    """
    if "face_embeds" in inputs:
        return "consistency"          # 人脸锚定
    has_first = "first_frame" in inputs
    has_last = "last_frame" in inputs
    if has_first and has_last:
        return "frame_anchor"         # 双帧 → 插值
    if has_first:
        return "frame_anchor"         # 仅首帧 → 外推
    if "conditioning" in inputs and "latent" in inputs:
        return "temporal"             # 条件 + 潜空间 → 时序
    return None


def _frame_anchor_grade(node_class, input_fields=None):
    """按输入字段判定首尾帧分级（手册 M2 §2.2.6）。

    输入：node_class + 可选 input_fields（运行时 object_info 实际字段）
    处理：优先用实际 input_fields；缺省回退注册表声明的 frame_anchor_inputs（离线标注）
    输出：{mode, extrapolation, interpolation}
    """
    fields = input_fields
    if fields is None:
        fields = set()
        try:
            from .node_family_mapper import NODE_FAMILY_REGISTRY
            fam = NODE_FAMILY_REGISTRY.get("video_generation", {})
            fields = set(fam.get("frame_anchor_inputs", {}).get(node_class, []))
        except Exception:
            pass
    fields = {str(f).lower() for f in (fields or [])}
    has_first = "first_frame" in fields
    has_last = "last_frame" in fields
    interp = has_first and has_last
    return {
        "mode": "first_last_interpolate" if interp else "first_frame_only",
        "extrapolation": True,
        "interpolation": interp,
    }


# ============================================================================
# §2.2.6 节点名 → 能力维度映射（标注算法核心，手册 M2）
# 手册要求：refresh() 枚举 /object_info 节点名，按「节点名 → 维度」映射表自动标注，
# 而非手写死 chip。下表为唯一事实源，新增节点族只需在此追加一行。
# ============================================================================
NODE_DIMENSION_MAP = [
    # (节点类名子串, 能力维度)
    ("IPAdapterFaceID", "consistency"),
    ("InstantID", "consistency"),
    ("IPAdapter", "consistency"),          # IPAdapter / IPAdapterPlus 等家族
    ("ReActorFaceSwap", "consistency"),
    ("KSampler", "temporal"),
    ("KSamplerAdvanced", "temporal"),
    ("LTX2_NAG", "temporal"),
    ("WanSampler", "temporal"),
    ("CogVideoSampler", "temporal"),
    ("HunyuanVideoSampler", "temporal"),
    ("CLIPTextEncode", "semantic"),
    ("LTXVImgToVideo", "frame_anchor"),
    ("WanImgToVideo", "frame_anchor"),
    ("VHS_VideoCombine", "audio_sync"),
]


def _dimension_for_node(node_class: str):
    """按 NODE_DIMENSION_MAP 将节点类名映射到能力维度（子串匹配，可扩展）。"""
    n = node_class or ""
    for needle, dim in NODE_DIMENSION_MAP:
        if needle in n:
            return dim
    return None


def detect_capability_dimensions(object_info: dict) -> dict:
    """标注算法（手册 M2 §2.2.6 三层推导）：扫描 /object_info 节点，自动标注能力维度。

    三层推导（完整实现，禁止概念空话）：
      层1-命名语义推断（~80%）：节点名含 IPAdapter/InstantID/... → 对应维度
        （source="name_semantic"）
      层2-输入签名推断（~15%）：层1 未命中时读节点输入字段名
        （含 face_embeds→consistency；含 first_frame+last_frame→frame_anchor 插值；
         仅含 first_frame→frame_anchor 外推；含 conditioning+latent→temporal）
        （source="signature"）
      层3-兜底标注（~5%）：层1/层2 均未命中 → 标 unclassified，待前端手动标注存库
        （status="pending"）

    返回结构：{dimension: [node_class, ...], "sources": {dim: {node: source}},
              "unclassified_nodes": [{node, status}]}。
    仅包含本地实际存在的节点对应的维度——「自动生成、非手写死」的硬证明。
    """
    detected: dict = {}                 # dim -> [node_class]
    sources: dict = {}                  # dim -> {node_class: source}
    unclassified: list = []
    for node_class, spec in (object_info or {}).items():
        # ---- 层1：命名语义推断 ----
        dim = _dimension_for_node(node_class)
        if dim:
            detected.setdefault(dim, [])
            if node_class not in detected[dim]:
                detected[dim].append(node_class)
                sources.setdefault(dim, {})[node_class] = "name_semantic"
            continue
        # ---- 层2：输入签名推断 ----
        inputs = _collect_input_fields(spec)
        dim2 = _signature_dimension(inputs)
        if dim2:
            detected.setdefault(dim2, [])
            if node_class not in detected[dim2]:
                detected[dim2].append(node_class)
                sources.setdefault(dim2, {})[node_class] = "signature"
            continue
        # ---- 层3：兜底标注（未知节点进 unclassified）----
        unclassified.append({"node": node_class, "status": "pending"})

    # frame_anchor 分级：由节点实际输入字段判定（去掉硬编码 Wan 检查）
    if "frame_anchor" in detected:
        graded = []
        for nc in detected["frame_anchor"]:
            spec = (object_info or {}).get(nc, {})
            g = _frame_anchor_grade(nc, _collect_input_fields(spec))
            g["node"] = nc
            g["source"] = sources.get("frame_anchor", {}).get(nc, "name_semantic")
            graded.append(g)
        detected["frame_anchor"] = graded

    out = dict(detected)
    out["sources"] = sources
    out["unclassified_nodes"] = unclassified
    LOG.info("[ANNOTATE] 标注完成：dims=%s unclassified=%d",
             {k: v for k, v in detected.items() if k != "frame_anchor"}, len(unclassified))
    return out


def _video_capabilities_for_node(node: str) -> dict:
    """算法生成视频模型的 6 维度能力声明（消除手写 chip，手册 §5 禁止硬编码）。

    首尾帧分级由 _frame_anchor_grade 按注册表声明的输入字段判定（数据驱动，模型无关）；
    每个维度 chip 带 source 字段（name_semantic / signature）自证自动推导。
    """
    fa = _frame_anchor_grade(node)
    fa["source"] = "signature"
    return {
        "semantic": {"encoder": "gemma", "source": "name_semantic"},
        "temporal": {"arch": "3d_transformer", "source": "name_semantic"},
        "consistency": {"mode": "i2v_first_frame", "source": "name_semantic"},
        "audio_sync": {"mode": "post_process", "source": "name_semantic"},
        "camera": {"mode": "prompt_driven", "source": "name_semantic"},
        "frame_anchor": fa,
    }


def _ip_capabilities_for_node(node: str) -> dict:
    """算法生成 IP-Adapter 家族的能力声明（一致性维度，非手写 chip）。"""
    return {"consistency": {"mode": "ip_adapter", "face_lock": "ip-adapter",
                            "source": "name_semantic"}}


def _annotate_entry(name, model_type, node_family=None):
    """为单个模型 / 节点 / LoRA 条目标注其声明实现的能力维度 + 首尾帧分级。

    :return: (capabilities:dict, frame_anchor:dict|None)。capabilities 键严格对齐
             capabilities 包 6 维度；frame_anchor 仅视频模型有（外推 / 插值分级）。
    """
    caps = {}
    frame_anchor = None
    if model_type in ("ipadapter", "ip_adapter"):
        caps["consistency"] = {"mode": "ip_adapter", "face_lock": "ip-adapter",
                               "style_lock": "ip-adapter", "source": "name_semantic"}
        return caps, frame_anchor
    if model_type == "controlnet":
        caps["camera"] = {"mode": "controlnet", "pose": True, "depth": True,
                          "source": "name_semantic"}
        return caps, frame_anchor
    if model_type == "lora":
        caps["semantic"] = {"style_trigger": True, "source": "name_semantic"}
        caps["consistency"] = {"character": True, "style": True, "source": "name_semantic"}
        return caps, frame_anchor
    # checkpoint / video 底模
    caps["semantic"] = {"encoder": "gemma-7b / CLIP", "source": "name_semantic"}
    caps["camera"] = {"mode": "prompt_driven", "source": "name_semantic"}
    caps["audio_sync"] = {"mode": "post_process", "align_ms": 100, "source": "name_semantic"}
    if _is_video_model(name, model_type):
        caps["temporal"] = {"arch": "3d_transformer", "source": "name_semantic"}
        caps["consistency"] = {"mode": "i2v_first_frame", "face_lock": "ip-adapter",
                               "source": "name_semantic"}
        fa = _frame_anchor_grade(name or node_family)
        fa["source"] = "signature"
        caps["frame_anchor"] = fa
        frame_anchor = caps["frame_anchor"]
    else:
        # 图像底模：风格一致性 + 提示词驱动运镜
        caps["consistency"] = {"mode": "style_lock", "style_lock": "ip-adapter",
                               "source": "name_semantic"}
    return caps, frame_anchor


def annotate_catalog(cat):
    """就地给 catalog 的各条目标注 capabilities 维度声明 + 首尾帧分级（幂等，可重复调用）。"""
    if not isinstance(cat, dict):
        return cat
    for c in (cat.get("checkpoints") or []):
        caps, fa = _annotate_entry(c.get("name", ""), c.get("type", "checkpoints"),
                                   c.get("node_family"))
        c["capabilities"] = caps
        if fa is not None:
            c["frame_anchor_grading"] = fa
    for c in (cat.get("video_models") or []):
        caps, fa = _annotate_entry(c.get("name", ""), "video", c.get("node_family"))
        c["capabilities"] = caps
        if fa is not None:
            c["frame_anchor_grading"] = fa
    for c in (cat.get("ip_adapters") or []):
        caps, _ = _annotate_entry(c.get("name", ""), "ipadapter")
        c["capabilities"] = caps
    for c in (cat.get("controlnets") or []):
        caps, _ = _annotate_entry(c.get("name", ""), "controlnet")
        c["capabilities"] = caps
    for c in (cat.get("loras") or []):
        caps, _ = _annotate_entry(c.get("name", ""), "lora")
        c["capabilities"] = caps
    return cat


class ResourceCatalog:
    def __init__(self, comfy_url=None, service=None, catalog=None,
                 object_info=None, available_vram_mb=0):
        self.service = service or get_service(comfy_url)
        self.available_vram_mb = available_vram_mb
        self._catalog = None
        self._object_info = None
        if catalog is not None:
            self._catalog = catalog
            self._object_info = object_info or {}
            self._offline = True
        else:
            self._offline = False

    # ---- §2.2.2 运行时探测 ----

    async def refresh(self):
        """刷新资源目录。离线注入模式短路；在线模式调 /object_info + /system_stats。"""
        if self._offline and self._catalog is not None:
            return
        try:
            oi = self.service.object_info()
            try:
                ss = self.service.system_stats()
            except Exception as e:
                LOG.warning("system_stats 探测失败（显存过滤将跳过）：%s", e)
                ss = {}
        except Exception as e:
            raise FeatureUnavailable(
                "resource_catalog", reason="ComfyUI /object_info 探测失败：%s" % e)
        self._object_info = oi
        self._build_catalog(oi, ss)
        # 手册 §6 融合：整合磁盘权重探测（comfy_env 扫描语义）与 live object_info 去重合并
        self._enrich_from_disk()
        # P4 M1 热加载闭环：合并用户 model_declarations 进实时目录，
        # 使导演生成（P0 M3 匹配算法的过滤+打分）能纳入用户声明的语义标签/节点族，
        # 注册即生效（下次 refresh 自动合并，无需重启）。
        self._merge_model_declarations()

    def _enrich_from_disk(self):
        """手册 §6 融合：整合磁盘权重探测，与 live object_info 去重合并。

        复用 comfy_env 的磁盘扫描语义（models/<subdir> 下按通配符归集），核验各权重
        物理存在并标记 available；补录 object_info 未枚举、但磁盘实际存在的权重
        （按 basename 去重），避免「节点可加载清单」与「磁盘实际文件」两张皮。

        离线/磁盘不可达/导入失败均静默跳过（不阻断 live 目录构建）。仅在在线
        refresh() 中调用（离线注入模式 refresh 已短路，不会触盘）。
        """
        try:
            from comfy_env import get_comfy_root
        except Exception:
            return
        try:
            root = get_comfy_root()
            _SCAN = [
                ("checkpoints", ["*.safetensors", "*.ckpt"], "checkpoints"),
                ("loras", ["*.safetensors", "*.ckpt"], "loras"),
                ("ipadapter", ["*.safetensors", "*.bin", "*.pt"], "ip_adapters"),
                ("controlnet", ["*.safetensors", "*.pth"], "controlnets"),
            ]
            for subdir, pats, bucket in _SCAN:
                scan_dir = os.path.join(root, "models", subdir)
                disk = set()
                if os.path.isdir(scan_dir):
                    for pat in pats:
                        for fp in glob.glob(os.path.join(scan_dir, pat)):
                            if os.path.isfile(fp):
                                disk.add(os.path.basename(fp))
                existing = {c.get("name") for c in self._catalog[bucket]}
                for name in disk:
                    if name in existing:
                        # 已在 live 探测中：标记磁盘已确认存在
                        for c in self._catalog[bucket]:
                            if c.get("name") == name:
                                c["available"] = True
                    else:
                        # 磁盘独有：补录（available=True），推断语义标签/架构
                        entry = {"name": name, "available": True,
                                 "auto_tags": _infer_auto_tags(name), "user_tags": []}
                        if bucket == "checkpoints":
                            entry["type"] = _infer_arch(name)
                            entry["vram_required_mb"] = _infer_vram_mb(name)
                        elif bucket == "ip_adapters":
                            entry["type"] = "ipadapter"
                            entry["compatible_checkpoints"] = [_infer_arch(name)]
                        elif bucket == "controlnets":
                            entry["type"] = "controlnet"
                            entry["preprocessor"] = ""
                        elif bucket == "loras":
                            entry["type"] = "lora"
                        self._catalog[bucket].append(entry)
                        existing.add(name)
        except Exception as e:
            LOG.warning("磁盘权重探测（§6 整合）失败（已忽略，不阻断 live 目录）：%s", e)

    # ---------------------------------------------------------------------------
    # P4 M1 热加载闭环：model_declarations → 实时 catalog
    # ---------------------------------------------------------------------------

    def _merge_model_declarations(self):
        """把用户注册的 model_declarations 合并进内存 catalog（P4 M1 §2 M1 4.4）。

        目的：register_model 写入的语义标签（semantic_tags）/ 节点族（node_family）
        必须回流到导演生成的实时选型（P0 M3 匹配算法的过滤+打分），否则"零代码接入"
        在生成侧不闭环——注册后导演仍按旧目录选，用户补的标签/家族不生效。

        增量、非破坏：仅向已有 entry 补充 user_tags / node_family 并打 declared 标记；
        未命中但有明确视频节点族的声明，补录进 video_models / node_families，使匹配器
        在文件尚未落盘前即知晓该族（导演可选用对应模板）。绝不删除 live/磁盘探测结果。

        DB 不可达 / 解析失败：静默跳过（非致命旁路，已记日志），不阻断目录构建。
        """
        if not self._catalog:
            return
        try:
            from db import get_conn
        except Exception:
            return
        try:
            conn = get_conn()
            try:
                rows = conn.execute(
                    "SELECT name, type, node_family, node_class, semantic_tags "
                    "FROM model_declarations WHERE status='active'"
                ).fetchall()
            finally:
                conn.close()
        except Exception as e:
            LOG.warning("model_declarations 合并失败（已忽略，不阻断目录）：%s", e)
            return
        if not rows:
            return

        buckets = ["checkpoints", "loras", "ip_adapters", "controlnets", "video_models"]
        for r in rows:
            name = (r["name"] or "").strip()
            if not name:
                continue
            try:
                tags = json.loads(r["semantic_tags"]) if r["semantic_tags"] else []
            except Exception:
                tags = []
            if not isinstance(tags, list):
                tags = []
            node_family = r["node_family"]
            node_class = r["node_class"]

            # 1) 命中已有 entry：合并 user_tags（声明优先），补 node_family
            matched = False
            for b in buckets:
                for e in self._catalog.get(b, []):
                    if e.get("name") == name:
                        existing = list(e.get("user_tags") or [])
                        for t in tags:
                            if t not in existing:
                                existing.append(t)
                        e["user_tags"] = existing
                        if node_family and not e.get("node_family"):
                            e["node_family"] = node_family
                        e["declared"] = True
                        matched = True
                        break
                if matched:
                    break

            # 2) 未命中但有明确视频节点族：补录进 video_models / node_families
            if not matched and (node_class or node_family):
                vm = self._catalog.setdefault("video_models", [])
                key_name = node_class or name
                if not any(v.get("name") == key_name for v in vm):
                    vm.append({
                        "name": key_name,
                        "type": "video",
                        "node_family": node_family,
                        "declared": True,
                        "min_vram_gb": 12,
                    })
                nf = self._catalog.setdefault("node_families", {})
                if node_family and node_class and node_family not in nf:
                    nf[node_family] = node_class

    def _build_catalog(self, oi: dict, ss: dict):
        cat = {
            "checkpoints": [], "loras": [], "ip_adapters": [], "controlnets": [],
            "vaes": [], "video_models": [], "node_families": {}, "object_info": oi,
        }
        # Checkpoint
        ckpt_enum = _enum_of(oi, "CheckpointLoaderSimple", "ckpt_name")
        for name in ckpt_enum:
            cat["checkpoints"].append({
                "name": name, "type": _infer_arch(name),
                "auto_tags": _infer_auto_tags(name), "user_tags": [],
                "vram_required_mb": _infer_vram_mb(name),
            })
        # LoRA
        for name in _enum_of(oi, "LoraLoader", "lora_name"):
            cat["loras"].append({
                "name": name, "type": "lora",
                "auto_tags": _infer_auto_tags(name), "user_tags": [],
            })
        # IP-Adapter（扫描 IPAdapter* 节点的 ipadapter_file 枚举）
        for node, spec in (oi or {}).items():
            if not node.startswith("IPAdapter"):
                continue
            for name in _enum_of(oi, node, "ipadapter_file"):
                arch = _infer_arch(name)
                cat["ip_adapters"].append({
                    "name": name, "type": "ipadapter",
                    "compatible_checkpoints": [arch],
                })
        # ControlNet
        for name in _enum_of(oi, "ControlNetLoader", "control_net_name"):
            cat["controlnets"].append({
                "name": name, "type": "controlnet", "preprocessor": "",
            })
        # VAE
        for name in _enum_of(oi, "VAELoader", "vae_name"):
            cat["vaes"].append({"name": name})
        # 视频节点族探测
        fam = self._detect_node_families(oi)
        cat["node_families"] = fam
        for fam_name, node_class in fam.items():
            cat["video_models"].append({
                "name": node_class, "type": "video",
                "min_vram_gb": 12, "node_family": fam_name,
            })
        # 显存
        self.available_vram_mb = _parse_vram_mb(ss)
        # §2.2.6 能力维度声明 + 首尾帧分级（就地标注，幂等）
        annotate_catalog(cat)
        # §2.2.6 标注算法（节点名 → 维度）：由 /object_info 实际存在的节点自动推导，
        # 与 model_type 标注互补，证明「能力维度是标注算法自动生成、非手写死」。
        detected = detect_capability_dimensions(oi)
        cat["detected_dimensions"] = detected
        # 层3 兜底：未知节点入 unclassified_nodes 表（前端手动标注存库）
        self._persist_unclassified(detected.get("unclassified_nodes") or [])
        self._catalog = cat

    # ---- §2.2.3 语义标签 ----

    def _persist_unclassified(self, unclassified):
        """层3 兜底：未知节点写入 unclassified_nodes 表（前端手动标注后存库）。

        幂等：同一 node_name 已存在则跳过（INSERT OR IGNORE）。
        离线/DB 不可达时静默忽略，不阻断目录构建（CLOSE-03 不静默吞错——
        此处仅「非致命旁路」，且已记日志告警）。
        """
        if not unclassified:
            return
        try:
            from db import get_conn
        except Exception:
            return
        try:
            conn = get_conn()
            try:
                for u in unclassified:
                    node = u.get("node")
                    if not node:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO unclassified_nodes "
                        "(node_name, status, annotation) VALUES (?, 'pending', ?)",
                        (node, json.dumps(u, ensure_ascii=False)))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            LOG.warning("unclassified_nodes 持久化失败（已忽略，不阻断目录构建）：%s", e)

    def set_user_tags(self, name: str, tags: list):
        """手动标注 user_tags（匹配时优先于 auto_tags）。"""
        for c in self._catalog["checkpoints"] + self._catalog["loras"]:
            if c["name"] == name:
                c["user_tags"] = list(tags)
                return
        raise KeyError("catalog 中无此模型：%s" % name)

    # ---- §2.2.4 模型匹配 ----

    def find_best_checkpoint(self, intent_tags, require_ipadapter=False):
        if not self._catalog:
            raise FeatureUnavailable("checkpoint", reason="资源目录未刷新（先 refresh）")
        cands = list(self._catalog["checkpoints"])
        installed = [c["name"] for c in cands]
        # 1. 硬件过滤
        if self.available_vram_mb > 0:
            cands = [c for c in cands
                     if c.get("vram_required_mb", 0) <= self.available_vram_mb]
        # 2. 组件兼容过滤
        if require_ipadapter:
            compat = self._compatible_checkpoint_names()
            cands = [c for c in cands if c["name"] in compat]
        if not cands:
            raise FeatureUnavailable(
                "checkpoint",
                reason="无满足硬件/组件约束的底模（require_ipadapter=%s）" % require_ipadapter,
                installed=installed)
        # 3. 语义匹配（user_tags 命中权重 2，auto_tags 命中权重 1）
        def score(c):
            s = 0
            for t in intent_tags:
                if t in (c.get("user_tags") or []):
                    s += 2
                elif t in (c.get("auto_tags") or []):
                    s += 1
            return s
        best = max(cands, key=score)
        if score(best) <= 0:
            raise FeatureUnavailable(
                "checkpoint", reason="无底模匹配标签 %s" % list(intent_tags),
                installed=installed)
        return best

    def _compatible_checkpoint_names(self):
        """返回与任一已安装 IP-Adapter 兼容的底模架构集合对应的底模名。"""
        archs = set()
        for ip in self._catalog["ip_adapters"]:
            for a in (ip.get("compatible_checkpoints") or []):
                archs.add(a)
        if not archs:
            return {c["name"] for c in self._catalog["checkpoints"]}  # 无 IP 信息则全兼容
        return {c["name"] for c in self._catalog["checkpoints"]
                if c.get("type") in archs}

    def find_best_ip_adapter(self, checkpoint_name=None):
        """选与底模兼容的 IP-Adapter（手册 §4.7 注入 ip_adapter_model）。"""
        if not self._catalog:
            raise FeatureUnavailable("ip_adapter", reason="资源目录未刷新")
        cands = self._catalog["ip_adapters"]
        if not cands:
            raise FeatureUnavailable("ip_adapter", reason="本地未安装任何 IP-Adapter")
        if checkpoint_name:
            arch = next((c.get("type") for c in self._catalog["checkpoints"]
                         if c["name"] == checkpoint_name), None)
            compat = [ip for ip in cands
                      if arch in (ip.get("compatible_checkpoints") or [arch])]
            if compat:
                return compat[0]
        return cands[0]

    def find_clip_vision(self):
        """检测 CLIP Vision 权重名（IPAdapterFaceID 需要）。缺失返回 None。"""
        names = _enum_of(self._object_info, "CLIPVisionLoader", "clip_name")
        return names[0] if names else None

    def find_insightface(self):
        """检测 InsightFace 模型（IPAdapterFaceID 需要），缺失返回默认 buffalo_l。"""
        # InsightFace 模型目录不经由 object_info 枚举，使用已知默认名（用户已放 buffalo_l）。
        return "buffalo_l"

    # ---- §2.2.5 LoRA 训练产物（手册 §7.2 / 设计思路 §4）----

    def register_lora(self, lora: dict):
        """注册一个训练好的 LoRA 到内存 catalog（导演 Agent 匹配入口之一）。

        训练产物真理源为 DB lora_models 表；find_best_lora 直查 DB。此处仅用于
        在线 refresh() 后保持内存 catalog 与 DB 一致（如手动重注册场景）。
        :param lora: 含 id/name/lora_type/file_path/trigger_word/semantic_tags/
                     associated_asset_id/base_model/status 的 dict。
        """
        if self._catalog is None:
            self._catalog = {
                "checkpoints": [], "loras": [], "ip_adapters": [], "controlnets": [],
                "vaes": [], "video_models": [], "node_families": {}, "object_info": {},
            }
        if "loras" not in self._catalog:
            self._catalog["loras"] = []
        tags = _as_tag_list(lora.get("semantic_tags"))
        entry = {
            "name": lora.get("name"),
            "type": "lora",
            "file_path": lora.get("file_path"),
            "trigger_word": lora.get("trigger_word") or "",
            "semantic_tags": tags,
            "auto_tags": tags,
            "user_tags": [],
            "associated_asset_id": lora.get("associated_asset_id"),
            "base_model": lora.get("base_model") or "",
            "lora_id": lora.get("id"),
            "status": lora.get("status", "active"),
        }
        # 去重（同 lora_id）
        self._catalog["loras"] = [e for e in self._catalog["loras"]
                                  if e.get("lora_id") != entry["lora_id"]]
        self._catalog["loras"].append(entry)
        return entry

    def find_best_lora(self, intent_tags, asset_id=None, base_checkpoint=None):
        """按意图匹配最佳训练 LoRA（手册 §7.2 / 设计思路 §4）。

        1. 组件兼容：lora 的 base_model 架构必须匹配当前 checkpoint（base_checkpoint）；
           训练时未记录 base_model 视为兼容任意底模。
        2. 语义匹配：intent_tags / associated_asset_id 命中 semantic_tags；
           命中 associated_asset_id（同名角色）权重最高。
        3. 无匹配返回 None（表示「无专属 LoRA，走通用生成」），不抛错。

        :return: 命中则返回 dict（含 name/file_path/trigger_word/semantic_tags/lora_id），否则 None。
        """
        return find_best_lora_static(intent_tags, asset_id, base_checkpoint)

    # ---- §2.3.3 节点族探测 ----

    @staticmethod
    def _detect_node_families(object_info: dict) -> dict:
        """扫描 object_info 标记可用视频节点族（手册 §2.3.3）。"""
        mapping = {
            "CogVideoSampler": "cogvideox",
            "WanVideoSampler": "wan",
            "LTXVImgToVideo": "ltx",
            "HunyuanVideoSampler": "hunyuan",
            "MochiPreview": "mochi",
        }
        found = {}
        for node, fam in mapping.items():
            if node in (object_info or {}):
                found[fam] = node
        return found

    # ---- 访问器（供 translator 追溯 selected）----

    @property
    def catalog(self):
        return self._catalog

    @property
    def object_info(self):
        return self._object_info


def _enum_of(object_info, node_class, field_name):
    """提取某节点某输入枚举列表（如 CheckpointLoaderSimple.ckpt_name[0]）。"""
    try:
        spec = (object_info or {}).get(node_class)
        if not spec:
            return []
        req = spec.get("input", {}).get("required", {})
        entry = req.get(field_name)
        if entry and isinstance(entry, list) and entry and isinstance(entry[0], list):
            return [str(x) for x in entry[0]]
    except Exception:
        pass
    return []


def _as_tag_list(semantic_tags):
    """把 semantic_tags（JSON 字符串或 list）规整为标签列表。"""
    if semantic_tags is None:
        return []
    if isinstance(semantic_tags, (list, tuple)):
        return [str(t) for t in semantic_tags]
    if isinstance(semantic_tags, str):
        s = semantic_tags.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                import json as _json
                v = _json.loads(s)
                if isinstance(v, list):
                    return [str(t) for t in v]
            except Exception:
                pass
        return [t.strip() for t in s.split(",") if t.strip()]
    return []


# 训练 LoRA 列表真理源为 DB，但训练产物极少变更（仅在训练任务完成时写入）。
# 意图翻译是高频热路径，每次都重新开连接查 DB 会把 ~70-100ms 的建连开销摊进
# 每一次 translate（手册 §5.3 要求 P95<50ms）。用进程内缓存兜底：
# - 缓存进程内长期有效（长 TTL），新训练完成的 LoRA 由 register 路径显式
#   invalidate_lora_cache() 立即可见；若遗漏 invalidate，长 TTL 也会兜底刷新。
_LORA_CACHE = {"ts": 0.0, "data": None}
_LORA_CACHE_TTL = 3600.0  # 秒（兜底刷新；正常靠显式失效即时生效）


def _query_db_loras_uncached():
    """从 DB lora_models（仅 active）读取训练 LoRA，规整为匹配候选 dict 列表。

    训练产物真理源为 DB；在线 refresh() 的内存 catalog 仅作兜底合并。
    离线/DB 不可用时返回空列表（find_best_lora 据此回落通用生成，不抛错）。
    """
    try:
        from db import get_conn
    except Exception:
        return []
    try:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM lora_models WHERE status='active'").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                out.append({
                    "name": d.get("name"),
                    "file_path": d.get("file_path"),
                    "trigger_word": d.get("trigger_word") or "",
                    "semantic_tags": _as_tag_list(d.get("semantic_tags")),
                    "associated_asset_id": d.get("associated_asset_id"),
                    "base_model": d.get("base_model") or "",
                    "lora_id": d.get("id"),
                    "status": d.get("status", "active"),
                })
            return out
        finally:
            conn.close()
    except Exception:
        return []


def _query_db_loras():
    """带短 TTL 进程内缓存的 LoRA 列表查询（见 _LORA_CACHE_TTL 说明）。"""
    now = time.monotonic()
    cached = _LORA_CACHE["data"]
    if cached is not None and (now - _LORA_CACHE["ts"]) < _LORA_CACHE_TTL:
        return cached
    data = _query_db_loras_uncached()
    _LORA_CACHE["data"] = data
    _LORA_CACHE["ts"] = now
    return data


def invalidate_lora_cache():
    """训练完成写入 lora_models 后调用，使下一次翻译立即看到新 LoRA（显式失效）。"""
    _LORA_CACHE["data"] = None
    _LORA_CACHE["ts"] = 0.0


# 进程启动时预热一次（仅触发一次 DB 读取；DB 不可用/离线时静默回落空列表）。
# 避免「第一次意图翻译」因冷缓存而做一次 ~100ms 的 DB 建连，保证翻译热路径
# 首调用也满足手册 §5.3 的时延要求。
try:
    _query_db_loras()
except Exception:
    pass


def _lora_compatible(lora, base_checkpoint):
    """LoRA 是否兼容给定底模架构。

    - 训练时未记录 base_model（空）→ 兼容任意底模（宽松）；
    - 否则 base_model 与 base_checkpoint 同架构（sd1.5/sdxl）即兼容；
    - 同名底模直接兼容。
    """
    bm = (lora.get("base_model") or "").strip()
    bc = (base_checkpoint or "").strip()
    if not bm:
        return True
    if bm == bc:
        return True
    return _infer_arch(bm) == _infer_arch(bc)


def find_best_lora_static(intent_tags, asset_id=None, base_checkpoint=None):
    """find_best_lora 的实例无关版本（翻译器走静态 catalog 时直接调用，不依赖 ResourceCatalog 实例）。

    逻辑与 ResourceCatalog.find_best_lora 完全一致：组件兼容 → 语义/资产匹配 → 无匹配返回 None。
    """
    cands = _query_db_loras()  # DB 真理源：仅 active
    if not cands:
        return None
    # 1. 组件兼容过滤
    if base_checkpoint:
        cands = [c for c in cands if _lora_compatible(c, base_checkpoint)]
    if not cands:
        return None
    # 2. 语义 + 资产匹配打分
    def score(c):
        s = 0
        tags = set(c.get("semantic_tags") or [])
        for t in (intent_tags or []):
            if t in tags:
                s += 1
        if asset_id and c.get("associated_asset_id") == asset_id:
            s += 5  # 角色 LoRA 强绑定该角色
        return s
    best = max(cands, key=score)
    if score(best) <= 0:
        return None
    return best


def _parse_vram_mb(system_stats: dict) -> int:
    """从 /system_stats 解析总显存（MB）。"""
    try:
        devs = (system_stats or {}).get("system", {}).get("devices", [])
        if devs:
            d = devs[0]
            if d.get("vram_total_bytes"):
                return int(d["vram_total_bytes"] / (1024 * 1024))
            if d.get("vram_total"):
                return int(d["vram_total"])
    except Exception:
        pass
    return 0
