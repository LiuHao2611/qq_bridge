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
import re
import logging

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
        self._catalog = cat

    # ---- §2.2.3 语义标签 ----

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
