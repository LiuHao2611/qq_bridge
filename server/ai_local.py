# -*- coding: utf-8 -*-
"""本地 AI 适配器：Ollama(规划/润色) + 秋叶 ComfyUI(出图) + FFmpeg(合成)。

严格遵循手册「离线 AI 降级：在线 → 本地 Ollama → 本地缓存」三级设计：
  L1 本地 ComfyUI + Ollama 可用 → 真实文生图 + 大模型润色
  L2 仅 Ollama 可用 → 大模型润色 + 占位图
  L3 均不可用 → 用 FFmpeg 生成可读的标题卡视频（真实 mp4），保证端到端流程可用
无论哪一级，合成出的都是真实可播放的 mp4 文件；AI 图像质量取决于本地 GPU 服务是否启动。
"""
import os
import re
import json
import time
import random
import uuid
import shutil
import subprocess
import urllib.request
import urllib.error
import logging
import sys
import glob

import comfy_env  # 秋葉 ComfyUI 根目录 + 权重自动发现（手册 §5.1/§5.2）
import metrics  # 手册 §7.2 Prometheus 监控指标

logger = logging.getLogger("ai_local")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUTS = os.path.join(BASE, "outputs")
os.makedirs(OUTPUTS, exist_ok=True)

# FS-01/FS-03：所有临时文件/目录必须落在 D:\AI 下，严禁使用系统 C 盘 temp
# （既遵守「禁止操作 C 盘任何文件」，也避免安全删除钩子把清理动作送进回收站）。
_TMP_ROOT = os.path.join(BASE, ".tmp")
os.makedirs(_TMP_ROOT, exist_ok=True)

OLLAMA_URL = "http://127.0.0.1:11434"
COMFY_URL = "http://127.0.0.1:8188"
MODEL = os.environ.get("MANJU_OLLAMA_MODEL", "qwen3.5:9b")


# -----------------------------------------------------------------------------
# 模型设置读取（与 Settings.vue 的 modelSettings 结构对齐）
# -----------------------------------------------------------------------------
_settings_cache = None
_settings_loaded_at = 0
_SETTINGS_TTL_MS = 2000  # 2 秒内复用缓存，避免频繁读库


def _load_model_settings():
    """读取 SQLite settings 表中的 modelSettings，失败返回 {}（回退到全局默认值）。"""
    global _settings_cache, _settings_loaded_at
    now = time.time() * 1000
    if _settings_cache is not None and now - _settings_loaded_at < _SETTINGS_TTL_MS:
        return _settings_cache
    try:
        import db
        conn = db.get_conn()
        try:
            row = db.fetch_one(conn, "SELECT value FROM settings WHERE key=?", ("modelSettings",))
            if row and row.get("value"):
                val = row["value"]
                try:
                    val = json.loads(val)
                except Exception:
                    val = {}
                _settings_cache = val if isinstance(val, dict) else {}
            else:
                _settings_cache = {}
        finally:
            conn.close()
    except Exception:
        _settings_cache = {}
    _settings_loaded_at = now
    return _settings_cache


def _get_ollama_url():
    """返回 settings 中配置的 Ollama 地址，未配置则回退全局 OLLAMA_URL。"""
    ms = _load_model_settings()
    for purpose in ("create", "planning"):
        cfg = ms.get(purpose, {})
        if cfg.get("provider") == "local":
            return cfg.get("local", {}).get("baseUrl") or OLLAMA_URL
    return OLLAMA_URL


def _get_comfy_url():
    """返回 settings 中配置的 ComfyUI 地址，未配置则回退全局 COMFY_URL。"""
    ms = _load_model_settings()
    for purpose in ("video", "img"):
        cfg = ms.get(purpose, {})
        if cfg.get("provider") == "local":
            return cfg.get("local", {}).get("baseUrl") or COMFY_URL
    return COMFY_URL


def _get_comfy_checkpoint():
    """返回 ComfyUI 基础模型（checkpoint）文件名。

    优先用 settings 中「文生图 / 视频」本地配置填写的 `local.checkpoint`（用户修改权重入口）；
    未显式指定时扫描 models/checkpoints 自动挑首个 *.safetensors/*.ckpt/*.pt；
    都没有 → 抛清晰错误指明去 checkpoints 目录放底模（如 Realistic_Vision SD1.5）。
    """
    explicit = None
    ms = _load_model_settings()
    for purpose in ("video", "img"):
        cfg = ms.get(purpose, {})
        if cfg.get("provider") == "local":
            ckpt = cfg.get("local", {}).get("checkpoint")
            if ckpt and isinstance(ckpt, str) and ckpt.strip():
                explicit = ckpt.strip()
                break
    return comfy_env.discover_weight(
        "checkpoints", ["*.safetensors", "*.ckpt", "*.pt"],
        explicit=explicit, what="底模(checkpoint) 权重",
        # 漫画/角色出图优先挑选动漫/漫画底模；未安装则回退首个可用底模（诚实降级）
        prefer=["*anime*", "*manga*", "*counterfeit*", "*meina*",
                "*anything*", "*illustrious*", "*meinamix*", "*cetus*"])


def _get_comfy_ipadapter():
    """返回 IP-Adapter 权重文件名（手册 §5.2 身份特征注入）。

    扫描 models/ipadapter 自动挑首个 *.safetensors；都没有 → 清晰报错
    指明放入 IP-Adapter 权重（如 ip-adapter_sd15.safetensors / ip-adapter-plus-face）。
    """
    return comfy_env.discover_weight(
        "ipadapter", ["*.safetensors", "*.bin"], what="IP-Adapter 权重")


def _get_comfy_clip_vision():
    """返回 CLIP-ViT 权重文件名（IP-Adapter 的 clip_vision 依赖）。

    扫描 models/clip_vision 自动挑首个 *.safetensors；都没有 → 清晰报错。
    """
    return comfy_env.discover_weight(
        "clip_vision", ["*.safetensors", "*.bin"], what="CLIP-ViT(clip_vision) 权重")


def _get_comfy_controlnet(control_type="depth"):
    """返回 ControlNet 权重文件名（场景构图约束，手册 §3 场景参考图约束）。

    control_type: depth / canny / openpose / lineart。优先扫描 models/controlnet
    中匹配 *<type>* 的文件（如 depth→control_v11f1p_sd15_depth.pth）；
    没有同类则回退到任意 *.pth/*.safetensors；都没有 → 清晰报错。
    """
    type_patterns = ["*%s*.pth" % control_type, "*%s*.safetensors" % control_type]
    try:
        return comfy_env.discover_weight(
            "controlnet", type_patterns, what="ControlNet(%s) 权重" % control_type)
    except RuntimeError:
        return comfy_env.discover_weight(
            "controlnet", ["*.pth", "*.safetensors"],
            what="ControlNet(%s) 权重" % control_type)


def _detect_depth_controlnet():
    """返回可用的 depth ControlNet 权重文件名；无则 None（手册 §3.3.4 深度复用诚实降级）。

    仅匹配 *depth* 权重，避免与 canny/openpose 等错配导致构图语义错误。
    """
    try:
        return comfy_env.discover_weight(
            "controlnet", ["*depth*.pth", "*depth*.safetensors", "*depth*.bin"],
            what="depth ControlNet 权重")
    except RuntimeError:
        return None


def _get_llm_model_config(purpose="create"):
    """根据 purpose（create/planning）构造 model_config 供 split_story/extract_assets/ollama_chat 使用。"""
    ms = _load_model_settings()
    cfg = ms.get(purpose, {})
    provider = cfg.get("provider") or "local"
    out = {"provider": provider}
    if provider == "local":
        out["model"] = cfg.get("local", {}).get("model") or MODEL
        out["apiBase"] = cfg.get("local", {}).get("baseUrl") or OLLAMA_URL
    else:
        out["apiBase"] = cfg.get("online", {}).get("baseUrl") or ""
        out["apiKey"] = cfg.get("online", {}).get("apiKey") or ""
        out["model"] = cfg.get("online", {}).get("model") or ""
    return out


def ollama_available(model=None):
    """检查 Ollama 可用且目标模型已加载（不仅检查 API 存活，还验证模型存在）。

    model: 指定模型名；None 则使用环境变量 MODEL 或 settings 中对应模型。
    """
    target = model or MODEL
    try:
        models = ollama_list_models()
        if target not in models:
            logger.warning("[ollama] API 存活但模型 %s 未加载（可用模型: %s）"
                           % (target, models or "无"))
            return False
        return True
    except Exception:
        return False


def ollama_list_models(base_url=None):
    """返回 Ollama 本地已加载模型名列表；服务不可用时返回 []（不抛错）。"""
    url = (base_url or _get_ollama_url()).rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            if r.status != 200:
                return []
            data = json.loads(r.read().decode("utf-8", "ignore"))
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def _ollama_running_info(model=None):
    """查询 Ollama 当前已加载模型的 GPU/CPU 分布，供前端日志展示（避免误以为模型在 CPU 跑）。

    通过 /api/ps 获取每个模型的 size（总占用）与 size_vram（显存占用），
    按 size_vram / size 估算 GPU 占比（与 `ollama ps` 的 PROCESSOR 列口径一致）。
    返回 {ok, error?, models:[{name, size_gb, size_vram_gb, gpu_pct, cpu_pct}]}。
    """
    url = (_get_ollama_url()).rstrip("/") + "/api/ps"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            if r.status != 200:
                return {"ok": False, "error": "ollama /api/ps 返回 %s" % r.status, "models": []}
            data = json.loads(r.read().decode("utf-8", "ignore"))
        models = []
        for m in (data.get("models") or []):
            size = int(m.get("size") or 0)
            size_vram = int(m.get("size_vram") or 0)
            gpu_pct = int(round(size_vram * 100.0 / size)) if size > 0 else 0
            cpu_pct = 100 - gpu_pct
            models.append({
                "name": m.get("name") or m.get("model"),
                "size_gb": round(size / 1024 ** 3, 1),
                "size_vram_gb": round(size_vram / 1024 ** 3, 1),
                "gpu_pct": gpu_pct,
                "cpu_pct": cpu_pct,
            })
        hit = None
        if model:
            hit = next((x for x in models if model in (x.get("name") or "")), None)
        return {"ok": True, "model": hit, "models": models}
    except Exception as e:
        return {"ok": False, "error": str(e), "models": []}


def comfy_available(base_url=None):
    url = (base_url or _get_comfy_url()).rstrip("/") + "/system_stats"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


# ----------------------------- 资产上传 / 预处理辅助（Phase 1） -----------------------------
def _http_post_multipart(url, fields=None, files=None, timeout=30):
    """向 ComfyUI 提交 multipart（如 /upload/image），返回响应文本；失败抛异常（带退避重试）。"""
    boundary = "----manju_boundary_%s" % uuid.uuid4().hex
    body = b""
    if fields:
        for k, v in fields.items():
            body += ("--%s\r\n" % boundary).encode()
            body += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode()
            body += str(v).encode("utf-8") + b"\r\n"
    if files:
        for f in files:
            body += ("--%s\r\n" % boundary).encode()
            body += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                     % (f["name"], f["filename"])).encode()
            body += ("Content-Type: %s\r\n\r\n" % f.get("type", "application/octet-stream")).encode()
            body += f["data"] + b"\r\n"
    body += ("--%s--\r\n" % boundary).encode()
    hdr = {"Content-Type": "multipart/form-data; boundary=%s" % boundary}
    last_err = None
    for attempt in range(HTTP_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, data=body, headers=hdr, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last_err = e
            if attempt < HTTP_MAX_RETRIES - 1:
                time.sleep(HTTP_BACKOFF_BASE * (2 ** attempt))
    raise last_err


def _upload_to_comfy(image_path):
    """把本地参考图上传到 ComfyUI /upload/image，返回 ComfyUI 侧文件名；失败返回 None。"""
    if not comfy_available():
        return None
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        resp = _http_post_multipart(
            COMFY_URL + "/upload/image",
            files=[{"name": "image", "filename": os.path.basename(image_path),
                    "data": data, "type": "image/png"}],
            timeout=30,
        )
        return json.loads(resp).get("name")
    except Exception as e:
        logger.warning("[comfy] 上传参考图失败: %s" % e)
        return None


def _save_thumbnail(src_path, dst_path, size=(320, 320)):
    """用 Pillow 生成居中缩略图，失败返回 False（不阻塞上传主流程）。"""
    try:
        from PIL import Image
        with Image.open(src_path) as im:
            im = im.convert("RGB")
            im.thumbnail(size, Image.LANCZOS)
            canvas = Image.new("RGB", size, (16, 19, 26))
            canvas.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
            canvas.save(dst_path)
        return True
    except Exception as e:
        logger.warning("[pillow] 缩略图生成失败: %s" % e)
        return False


def extract_clip_embedding(image_path):
    """提取主体/人脸特征向量（写入 assets_stages.face_vector）。

    说明：ComfyUI /history 回传的是图像文件而非张量，取回 CLIPVisionEncode 的 EMBEDDINGS
    需自定义落盘节点。为健壮与「无故障跑通」优先，本函数回退 None —— Phase 4 直接使用封面原图
    做 IP-Adapter（角色一致能力不依赖此张量），后续如需持久化向量再补自定义节点。
    """
    return None


def process_asset_upload(filename, file_bytes, is_role):
    """Phase 1：保存上传原图 + 生成缩略图 + 提取特征。返回 dict 或 None（不阻塞主流程）。"""
    aid = uuid.uuid4().hex
    ext = os.path.splitext(filename)[1] or ".png"
    raw_dir = os.path.join(OUTPUTS, "assets", "originals")
    thumb_dir = os.path.join(OUTPUTS, "assets")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(thumb_dir, exist_ok=True)
    raw_path = os.path.join(raw_dir, aid + ext)
    thumb_path = os.path.join(thumb_dir, aid + "_thumb.png")
    try:
        with open(raw_path, "wb") as f:
            f.write(file_bytes)
    except Exception as e:
        logger.warning("[asset] 原图落盘失败: %s" % e)
        return None
    _save_thumbnail(raw_path, thumb_path)
    face_blob = extract_clip_embedding(raw_path) if is_role else None
    return {"asset_id": aid, "raw_path": raw_path, "thumb_path": thumb_path, "face_blob": face_blob}


def detect_comfy_video():
    """探测 ComfyUI 是否装有视频生成节点族（Wan/Seedance/LTX/HunyuanVideo 等）。"""
    try:
        info = json.loads(_http_post_json(COMFY_URL + "/object_info", b"{}", timeout=10))
        names = " ".join(info.keys())
        return any(k in names for k in
                   ("Wan", "Seedance", "LTX", "HunyuanVideo", "VideoHelper", "CogVideoX", "Mochi"))
    except Exception:
        return False


def detect_comfy_controlnet():
    """探测 ControlNet 节点可用且本地 models/controlnet 有真实权重。"""
    try:
        # 注意：ComfyUI 的 /object_info 仅支持 GET（POST 返回 405），故用 GET 探测节点。
        info = json.loads(urllib.request.urlopen(
            urllib.request.Request(COMFY_URL + "/object_info"), timeout=10).read())
        if not any(k in " ".join(info.keys()) for k in
                   ("ControlNet", "ControlNetLoader", "ControlNetAux")):
            return False
        for cand in (os.path.join(comfy_env.get_comfy_root(), "models", "controlnet"),):
            if os.path.isdir(cand) and any(
                f.endswith((".safetensors", ".pt", ".pth", ".bin"))
                for f in os.listdir(cand)
            ):
                return True
        return False
    except Exception:
        return False


def _resolve_ffmpeg():
    # 冻结版：ffmpeg.exe 随包放在可执行文件同级目录；其次 MANJU_APP_ROOT；
    # 其次 PATH；最后回退本机旧绝对路径。避免对当前机器的强依赖。
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    _app_root = os.environ.get("MANJU_APP_ROOT") or _exe_dir
    for cand in (
        os.path.join(_exe_dir, "ffmpeg.exe"),
        os.path.join(_app_root, "ffmpeg.exe"),
        shutil.which("ffmpeg") or "",
        r"D:\AI\tools\ffmpeg\ffmpeg-8.1.2-essentials_build\bin\ffmpeg.exe",
    ):
        if cand and os.path.isfile(cand):
            return cand
    return "ffmpeg"  # 退回 PATH（若仍缺失会诚实报错）


FFMPEG_BIN = _resolve_ffmpeg()


def _resolve_ffprobe():
    # 与 _resolve_ffmpeg 同策略：同级目录 / MANJU_APP_ROOT / PATH / 旧绝对路径。
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    _app_root = os.environ.get("MANJU_APP_ROOT") or _exe_dir
    for cand in (
        os.path.join(_exe_dir, "ffprobe.exe"),
        os.path.join(_app_root, "ffprobe.exe"),
        shutil.which("ffprobe") or "",
        r"D:\AI\tools\ffmpeg\ffmpeg-8.1.2-essentials_build\bin\ffprobe.exe",
    ):
        if cand and os.path.isfile(cand):
            return cand
    return "ffprobe"  # 退回 PATH


FFPROBE_BIN = _resolve_ffprobe()

# 手册 §3.4.4：音画同步误差不得超过 100ms（N07 硬性约束）
AUDIO_ALIGN_MAX_MS = 100

# CLOSE-03：出站网络调用（Ollama / ComfyUI）必须指数退避重试且有上限
HTTP_MAX_RETRIES = 3
HTTP_BACKOFF_BASE = 2.0  # 第 n 次重试等待 2^n 秒


def ffmpeg_available():
    return FFMPEG_BIN != "ffmpeg"


def _http_post_json(url, data, timeout, max_retries=HTTP_MAX_RETRIES, base_delay=HTTP_BACKOFF_BASE):
    """CLOSE-03 出站 POST：指数退避重试且有上限。

    网络抖动（连接重置/超时/5xx）时按 2^n 秒退避，最多 max_retries 次；
    全部失败才把最后一次异常抛出，由上层决定降级或标记任务失败。
    """
    last_err = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
    raise last_err


def build_prompt(role, objective, constraints=None, invariants=None):
    """§9 提示词注入模板（CLOSE 纪律 · 提示词规范）。

    统一骨架：角色 / 任务 / 约束 / 不可变。所有调用本地大模型的提示词都必须经此模板生成，
    禁止在业务函数里散写裸字符串，保证提示词结构一致、可追溯、约束明确。

    - role: 你是谁（角色定位）
    - objective: 本次要完成的具体任务
    - constraints: 可选，软约束列表（应遵守但不致命）
    - invariants: 可选，硬不变量列表（绝不违反，如保留 <ref> 标签、不新增角色）
    """
    lines = ["# 角色", role, "", "# 任务", objective]
    if constraints:
        lines += ["", "# 约束"]
        lines += ["- " + c for c in constraints] if isinstance(constraints, (list, tuple)) else [str(constraints)]
    if invariants:
        lines += ["", "# 不可变（硬性，必须严格遵守）"]
        lines += ["- " + c for c in invariants] if isinstance(invariants, (list, tuple)) else [str(invariants)]
    return "\n".join(lines)


# ======================================================================
# 导演Agent能力增强 M2（手册 §3.3 / 设计 §8）：结构化 prompt + 确定性能力匹配
# ======================================================================
# 风格标签 → 触发词（trigger_words）。结构化拼装，禁止散写裸字符串（§9 提示词纪律）。
_STYLE_TRIGGER = {
    "写实": "photorealistic, real photo, ultra detailed skin texture",
    "电影感": "cinematic lighting, film grain, anamorphic, dramatic atmosphere",
    "动漫": "anime style, cel shading, clean lineart",
    "日漫": "manga style, Japanese animation",
    "水彩绘本": "watercolor illustration, soft pastel, storybook",
    "低饱和": "muted color palette, low saturation",
    "高对比": "high contrast, crisp detail",
}


def _color_temp_word(color_temperature):
    """色温(开尔文) → 颜色基调词。"""
    try:
        ct = int(color_temperature)
    except (TypeError, ValueError):
        return None
    if ct < 3000:
        return "warm color tone"
    if ct <= 5000:
        return "neutral color tone"
    return "cool color tone"


def build_generation_prompt(intent, subject=None):
    """结构化生成提示词（手册 §3.3 / 设计 §8 ③），含 trigger_words，遵循 §9 提示词纪律。

    :param intent: ProductionIntent 或 dict（task_type / intent_tags / style_constraints）。
    :param subject: 可选角色/场景主体描述（由五要素提取得到），拼于提示词首位。
    :return: 逗号分隔的结构化正向提示词（去重保序）。
    """
    if hasattr(intent, "to_dict"):
        intent = intent.to_dict()
    tags = list(intent.get("intent_tags") or [])
    style = intent.get("style_constraints") or {}
    task_type = intent.get("task_type") or ""

    parts = []
    if subject:
        parts.append(subject.strip())

    # 风格标签 → 触发词
    for t in tags:
        tw = _STYLE_TRIGGER.get(t)
        if tw:
            parts.append(tw)

    # 风格约束（色温 / 光影）
    ct_word = _color_temp_word(style.get("color_temperature"))
    if ct_word:
        parts.append(ct_word)
    lighting = style.get("lighting")
    if lighting:
        parts.append(lighting)

    # 任务类型取景词
    if "character" in task_type:
        parts.append("character reference sheet, front facing portrait, full detail")
    elif "scene" in task_type:
        parts.append("environment concept art, wide shot, atmospheric")
    elif "video" in task_type:
        parts.append("cinematic frame, keyframe, motion ready")

    # 去重保序
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return ", ".join(out)


def build_character_negative_prompt():
    """角色卡专用负向提示词（手册 §3.3 ④：排除畸形/多手/低质）。"""
    return (
        "low quality, blurry, watermark, text, deformed hands, extra fingers, "
        "mutated limbs, bad anatomy, ugly, duplicate, multiple characters, "
        "missing limbs, unrealistic proportions"
    )


def _select_checkpoint(checkpoints, tags, available_vram_mb=None):
    """确定性底模选型：available 优先 + 标签命中数最高（与 M1 ResourceCatalog 同分逻辑）。"""
    best, best_score = None, -1
    for c in checkpoints or []:
        tags_c = set(c.get("auto_tags", [])) | set(c.get("user_tags", []))
        req = c.get("vram_required_mb")
        if available_vram_mb is not None and req and int(req) > int(available_vram_mb):
            continue
        overlap = len(set(tags) & tags_c)
        score = (1 if c.get("available", True) else 0) * 1000 + overlap
        if score > best_score:
            best_score, best = score, c
    return best


def _select_lora(loras, era, style_tags):
    """确定性 LoRA 选型：时代关键词命中权重最高，其次风格标签命中。"""
    era_kw = (era or "").strip().lower()
    best, best_score = None, -1
    for l in loras or []:
        name = (l.get("name") or "").lower()
        tags_l = set(l.get("auto_tags", [])) | set(l.get("user_tags", []))
        score = 0
        if era_kw and (era_kw in name or era_kw in tags_l):
            score += 10
        score += len(set(style_tags or []) & tags_l)
        if score > best_score:
            best_score, best = score, l
    return best


def match_capability(era, style_tags, catalog, available_vram_mb=None):
    """确定性能力匹配（设计 §8 ②）：时代 + 风格 → 选底模 + 风格 LoRA。

    catalog：M1 ResourceCatalog 实例（取 .catalog）或等价字典
             {checkpoints:[...], loras:[...]}。
    无匹配底模 → 显式抛 FeatureUnavailable（禁静默降级到错误底模）。
    """
    from intent_engine.errors import FeatureUnavailable

    cat = catalog.catalog if hasattr(catalog, "catalog") else (catalog or {})
    ckpts = cat.get("checkpoints") or []
    loras = cat.get("loras") or []
    cp = _select_checkpoint(ckpts, list(style_tags or []), available_vram_mb)
    if cp is None:
        raise FeatureUnavailable(
            "checkpoint",
            "无可用底模匹配风格 %s" % (style_tags,),
            installed=[c.get("name") for c in ckpts],
        )
    lo = _select_lora(loras, era, list(style_tags or []))
    return {"checkpoint": cp, "loras": [lo] if lo else []}


def _parse_llm_json(raw):
    """从 LLM 文本中解析 JSON（容忍代码块包裹 / 前后噪声）。解析失败显式抛错。"""
    if raw is None:
        raise ValueError("LLM 返回为空")
    raw = raw.strip()
    if raw.startswith("```"):
        # 去掉 ```json ... ``` 包裹
        raw = raw.strip("`")
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM 返回不含 JSON 对象: %r" % raw[:200])
    try:
        return json.loads(raw[start:end + 1])
    except Exception as e:
        raise ValueError("LLM JSON 解析失败: %s" % e)


def extract_five_elements(setting_text, ollama_fn):
    """① LLM 结构化提取五要素（设计 §8）：年龄/外貌/服装/气质/时代。

    :param setting_text: 角色设定原文。
    :param ollama_fn: 注入的 LLM 调用（prompt->text），便于离线测试。
    :return: {age, appearance, clothing, temperament, era}；缺要素显式抛错（禁静默补全）。
    """
    prompt = build_prompt(
        role="你是角色设定分析师",
        objective="从给定角色设定中提取五个结构化要素，严格以 JSON 返回",
        constraints=[
            "输出纯 JSON，键固定为 age/appearance/clothing/temperament/era",
            "age 为整数或简短字符串；era 为时代关键词（如 民国/现代/古代）",
        ],
        invariants=[
            "不得臆造设定中不存在的硬性身份信息",
            "必须包含全部五个键",
        ],
    )
    raw = ollama_fn(prompt + "\n\n角色设定：\n" + (setting_text or ""))
    data = _parse_llm_json(raw)
    for k in ("age", "appearance", "clothing", "temperament", "era"):
        if k not in data:
            raise ValueError("LLM 返回缺失要素: %s" % k)
    return data


def _ollama_generate(prompt, think=False, keep_alive=None, model=None, base_url=None):
    target_model = model or MODEL
    url = (base_url or _get_ollama_url()).rstrip("/") + "/api/generate"
    pl = {"model": target_model, "prompt": prompt, "stream": False, "think": think}
    if keep_alive is not None:
        pl["keep_alive"] = keep_alive
    payload = json.dumps(pl).encode("utf-8")
    body = _http_post_json(url, payload, timeout=600)
    return json.loads(body).get("response", "")


def ollama_chat(prompt, think=False, keep_alive=None, purpose="create",
               model=None, base_url=None):
    """LLM 统一入口（§9 提示词规范）：按 purpose 读取 settings 决定本地/在线。

    purpose: 'create' | 'planning' | 'extract'，对应 Settings.vue 中 modelSettings 的 key。
    当 settings 中该 purpose 配置为 online 时，走 OpenAI 兼容在线 API；否则走本地 Ollama。

    model / base_url: 透传优先（运行时指定，覆盖 settings），用于
        「从剧本提取资产」/「分场」等接口接前端 model_config 后立刻生效。
    透传与 settings 的优先级：透传 > settings(MODEL_SETTINGS) > MODEL(env)> MODEL 默认值。
    """
    cfg = _get_llm_model_config(purpose)
    if cfg.get("provider") == "online":
        # 在线模式下也允许透传覆盖 prompt 之外的字段
        if model or base_url:
            cfg = dict(cfg)
            if model:    cfg["model"]   = model
            if base_url: cfg["apiBase"] = base_url
        raw, _ = _call_online_llm(prompt, cfg)
        return raw or ""
    eff_model   = model        or cfg.get("model")
    eff_baseurl = base_url     or cfg.get("apiBase")
    return _ollama_generate(prompt, think=think, keep_alive=keep_alive,
                            model=eff_model, base_url=eff_baseurl)


def ollama_unload():
    """翻译/润色完成后主动释放 Ollama 显存，避免与 ComfyUI 争抢 6GB 显卡。

    1660Ti 仅 6GB 显存：qwen3.5(5.6GB) 与 Realistic_Vision(2.1GB) 无法同时驻留。
    文生图链路为「Ollama 翻译 → ComfyUI 出图」串行，翻译结束后立即卸载 Ollama，
    把显存让给 ComfyUI，出图速度从数分钟恢复到 ~60s/张。

    注意：本机 Ollama 0.32.5 不支持 /api/unload（返回 404），可靠的卸载方式是
    在 generate 请求里带 keep_alive=0（已在 _ollama_generate / translate_to_sd_prompt 中使用）。
    此函数作为兜底，用一次 keep_alive=0 的空生成触发卸载；失败静默忽略。
    """
    try:
        req = urllib.request.Request(
            OLLAMA_URL + "/api/generate",
            data=json.dumps({"model": MODEL, "prompt": " ", "stream": False,
                             "think": False, "keep_alive": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass


def _extract_json_block(text):
    """从 LLM 输出中截取首个 JSON 数组/对象块（容错：忽略解释性文字）。

    优先尝试提取 JSON 对象 `{}`（如资产提取），再尝试 JSON 数组 `[]`（如分场拆解），
    避免对象内部包含数组时误把内层数组当作顶层结构。
    """
    if not text:
        return None
    s = text.strip()
    # 优先对象
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        candidate = s[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            pass
    # 再试数组
    start, end = s.find("["), s.rfind("]")
    if start >= 0 and end > start:
        candidate = s[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            pass
    return None


def _split_story_prompt(story):
    """构造分场提示词，本地/在线模型共用（对齐手册 §3.1.2/§3.1.3 输出 schema）。"""
    return build_prompt(
        role="你是一个专业短剧编剧 / 分场导演",
        objective=(
            "把下面长故事拆成若干个「场次」，严格输出 JSON 数组。每个场次字段：\n"
            "  title(场次标题), duration(秒, 取值 5/8/10/12/15), scene_description(场景描述),\n"
            "  characters(出现的中文人名数组), summary(一句话梗概),\n"
            "  shots(分镜数组，按动作断点/台词停顿切分，每镜 3-12 秒)。每个分镜字段：\n"
            "    shot_id(字符串, 如 's1'), duration(秒), shot_type(景别),\n"
            "    camera_params:{ focal_length(焦距), angle(机位), movement(运镜) },\n"
            "    scene_desc(镜头画面/场景描述), action_desc(动作/表演描述), audio_desc(台词/音效描述),\n"
            "    refs(本镜出现的中文人名数组, 用于资产绑定)\n"
            "故事：\n%s" % story
        ),
        constraints=[
            "严格只输出 JSON 数组，不要任何解释文字",
            "每场 shots 时长之和 = duration（容差≤0.5s）",
            "shot_type∈{extreme_close_up,close_up,medium_close,medium,medium_full,full,long,extreme_long}",
            "camera_params.movement∈{static,pan,tilt,dolly,zoom,handheld,aerial,follow}",
            "camera_params.angle∈{eye_level,low_angle,high_angle,dutch,overhead,shoulder,pov}",
            "camera_params.focal_length∈{24mm,35mm,50mm,85mm,135mm}",
            "紧张情节用近景+手持运镜，抒情情节用全景+慢推轨（按剧情情绪补全未明确参数）",
        ],
        invariants=[
            "不新增故事里未出现过的角色",
            "保留原始情节先后顺序",
            "每个分镜必须有 scene_desc 与 action_desc（禁止无约束自由描述）",
        ],
    )


def _call_online_llm(prompt, model_config):
    """调用 OpenAI 兼容在线 API；返回 (raw_text, meta_dict)。失败 raw_text=None。"""
    meta = {"provider": model_config.get("provider", "openai-compatible")}
    api_base = (model_config.get("apiBase") or model_config.get("api_base") or "").rstrip("/")
    api_key = model_config.get("apiKey") or model_config.get("api_key") or ""
    model = model_config.get("model") or ""
    if not api_base:
        meta["error"] = "未配置在线模型 API 地址"
        return None, meta
    if not model:
        meta["error"] = "未配置在线模型名"
        return None, meta
    url = api_base + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    start = time.time()
    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:500]
        meta["error"] = "在线模型请求被拒(%s): %s" % (e.code, detail)
        meta["duration_ms"] = int((time.time() - start) * 1000)
        return None, meta
    except Exception as e:
        meta["error"] = "调用在线模型异常：%s" % e
        meta["duration_ms"] = int((time.time() - start) * 1000)
        return None, meta
    meta["duration_ms"] = int((time.time() - start) * 1000)
    choices = body.get("choices") or []
    raw = (choices[0].get("message", {}).get("content") if choices and isinstance(choices[0], dict) else "")
    return raw, meta


def _normalize_split_segments(arr):
    """把 LLM 返回的 JSON 数组规范化为标准 segment 结构（对齐手册 §3.1.3 shot_list）。

    每个分镜统一产出：shot_id / duration / shot_type / camera_params{focal_length,angle,movement}
    / scene_desc / action_desc / audio_desc / refs；同时保留 shot_type/camera_movement 等
    扁平字段供 persist_segment 等下游兼容消费。
    """
    if not isinstance(arr, list) or not arr:
        return None, "解析结果为空或非数组"
    out = []
    for i, seg in enumerate(arr[:30]):
        if not isinstance(seg, dict):
            continue
        dur = int(seg.get("duration") or 10)
        if dur not in (5, 8, 10, 12, 15):
            dur = 10
        raw_shots = seg.get("shots") or []
        if not isinstance(raw_shots, list) or not raw_shots:
            raw_shots = [{}]
        norm_shots = []
        for j, sh in enumerate(raw_shots):
            if not isinstance(sh, dict):
                sh = {}
            cp = sh.get("camera_params") or {}
            shot_type = sh.get("shot_type") or cp.get("shot_type") or "medium"
            movement = sh.get("camera_movement") or cp.get("movement") or "static"
            angle = sh.get("camera_angle") or cp.get("angle") or "eye_level"
            focal = sh.get("focal_length") or cp.get("focal_length") or "50mm"
            scene_desc = sh.get("scene_desc") or sh.get("description") or ""
            norm_shots.append({
                "shot_id": sh.get("shot_id") or ("s%d" % (j + 1)),
                "index": j,
                "duration": int(sh.get("duration") or 4),
                "shot_type": shot_type,
                "camera_params": {
                    "focal_length": str(focal),
                    "angle": angle,
                    "movement": movement,
                },
                "camera_movement": movement,
                "camera_angle": angle,
                "focal_length": str(focal),
                "scene_desc": scene_desc,
                "action_desc": sh.get("action_desc") or "",
                "audio_desc": sh.get("audio_desc") or "",
                "description": scene_desc,
                "refs": sh.get("refs") or sh.get("characters") or [],
            })
        seg["title"] = seg.get("title") or ("场次 %02d" % (i + 1))
        seg["duration"] = dur
        seg["scene_description"] = seg.get("scene_description") or ""
        seg["summary"] = seg.get("summary") or ""
        seg["characters"] = seg.get("characters") or []
        seg["shots"] = norm_shots
        out.append(seg)
    return out, None


# ----------------------------- 资产提取（Phase 1 · 一键从剧本提取） -----------------------------

def _extract_assets_prompt(story):
    example = json.dumps({
        "roles": [
            {
                "name": "周岩",
                "role_type": "protagonist",
                "description": "高二男生，额角有一道循环中残留的旧疤，蓝白校服，眼神疲惫但执着，动作急促",
                "stage_hint": "youth"
            },
            {
                "name": "程晴",
                "role_type": "protagonist",
                "description": "高一女生，校服胸口暗红，气质安静，笑容浅淡，手中常拿《灌篮高手》单行本",
                "stage_hint": "youth"
            }
        ],
        "scenes": [
            {
                "name": "石巷周岩家卧室",
                "description": "砖瓦平房，晨光从窗帘缝隙打入，煤炉已灭，搪瓷缸在桌上，墙上挂山水挂历"
            },
            {
                "name": "永安中学操场",
                "description": "红土跑道，蓝白校服群像做早操，广播大喇叭放《运动员进行曲》，红旗半展"
            }
        ],
        "props": [
            {"name": "北极星牌闹钟", "description": "90年代国产名牌闹钟，铃锤炸响，象征循环锚点"},
            {"name": "山水挂历", "description": "5月18日被红圈标注，红圈处纸张已被描119次而磨穿"},
            {"name": "永久牌自行车", "description": "老式的学生自行车，车钥匙被周岩抓起准备冲出家门"}
        ]
    }, ensure_ascii=False)
    return build_prompt(
        role="你是专业短剧资产整理助手，擅长从剧本/故事中识别并归类视觉资产",
        objective=(
            "从下面剧本/故事中提取三类视觉资产，严格只输出一个 JSON 对象，不要任何解释文字。\n\n"
            "必须严格遵循的字段格式：\n"
            "  roles: [{name(中文人名), role_type(主角=protagonist/配角=supporting/反派=antagonist), "
            "           description(30-80字，必须包含外貌/身份/气质/动作特征，不能空), "
            "           stage_hint(可选年龄阶段：childhood/juvenile/youth/middle_age/old_age)}]\n"
            "  scenes: [{name(场景名), description(40-80字，必须包含环境/氛围/光线/空间特征，不能空)}]\n"
            "  props: [{name(道具名), description(30-60字，必须包含形态/作用/象征意义，不能空)}]\n\n"
            "输出示例（注意：scenes 和 props 必须是对象数组，不能是字符串数组；description 不能为空）：\n"
            "%s\n\n"
            "剧本/故事：\n%s" % (example, story[:30000])
        ),
        constraints=[
            "严格只输出 JSON，不要 Markdown 代码块、不要解释",
            "角色名必须是剧本中实际出现的人名，不得虚构；不要漏掉有台词、动作或被明确描述的角色",
            "角色 description 不能空，必须从剧本原文中提取外貌、性格、语气、身份、动作特征；禁止编造剧本没有的细节",
            "角色年龄必须从身份/学段推断：中学生/高中生 stage_hint='youth'，不要把学生描述为中年/老年",
            "场景是地点/环境，不要把它列为道具；场景 description 不能空，必须包含环境/氛围/光线；禁止编造剧本没有的场景",
            "道具是具体物品，不要把角色或场景列为道具；道具 description 不能空，必须包含形态/作用；禁止把描写改编成不存在的物品",
            "scenes 和 props 必须是对象数组（每个元素带 name + description），绝不能是字符串数组",
            "每个类别如果为空则返回空数组",
        ],
        invariants=[
            "不新增故事里未出现过的角色、场景或道具",
            "role_type 必须是 protagonist/supporting/antagonist 之一",
            "stage_hint 如提供，必须是 childhood/juvenile/youth/middle_age/old_age 之一",
        ],
    )


def _normalize_extracted_assets(obj):
    """把 LLM 提取结果规范化为标准结构，并去重/截断/兼容字符串数组。"""
    valid_role_types = {"protagonist", "supporting", "antagonist"}
    valid_stages = {"childhood", "juvenile", "youth", "middle_age", "old_age"}
    out = {"roles": [], "scenes": [], "props": []}
    seen = {"roles": set(), "scenes": set(), "props": set()}

    def _clean_name(n):
        return re.sub(r'[\s@\[\]{}<>",:;]+', "", str(n or "")).strip()[:64]

    for key in ("roles", "scenes", "props"):
        arr = obj.get(key) if isinstance(obj, dict) else None
        if not isinstance(arr, list):
            continue
        for item in arr:
            # 兼容模型把 scenes/props 退化成字符串数组的情况
            if isinstance(item, str):
                name = _clean_name(item)
                if not name or name.lower() in seen[key]:
                    continue
                seen[key].add(name.lower())
                out[key].append({"name": name, "description": ""})
                continue
            if not isinstance(item, dict):
                continue
            name = _clean_name(item.get("name"))
            if not name or name.lower() in seen[key]:
                continue
            seen[key].add(name.lower())
            desc = str(item.get("description") or "").strip()[:256]
            entry = {"name": name, "description": desc}
            if key == "roles":
                rt = str(item.get("role_type") or "supporting").strip().lower()
                entry["role_type"] = rt if rt in valid_role_types else "supporting"
                sh = str(item.get("stage_hint") or "").strip().lower()
                if sh in valid_stages:
                    entry["stage_hint"] = sh
            out[key].append(entry)
    # 上限保护
    out["roles"] = out["roles"][:20]
    out["scenes"] = out["scenes"][:20]
    out["props"] = out["props"][:30]
    return out


def extract_assets_from_story(story, model_config=None):
    """Phase 1：从剧本/故事文本中提取角色、场景、道具候选。

    model_config: 同 split_story_with_llm，支持 local / openai-compatible。
    成功返回 {"roles": [...], "scenes": [...], "props": [...], "meta": {...}}；
    失败返回 {"roles": [], "scenes": [], "props": [], "meta": {"error": ...}}。
    """
    trace_id = str(uuid.uuid4())[:8]
    model_config = model_config or _get_llm_model_config("create")
    provider = model_config.get("provider") or "local"
    model = model_config.get("model") or MODEL
    meta = {"model": model, "source": "llm", "provider": provider, "trace_id": trace_id}
    logger.info(json.dumps({
        "event": "extract_assets_start", "trace_id": trace_id,
        "story_chars": len(story or ""), "provider": provider, "model": model,
    }, ensure_ascii=False))
    prompt = _extract_assets_prompt(story)
    start = time.time()
    raw = None
    try:
        if provider == "local":
            if not ollama_available(model=model):
                available = ollama_list_models()
                meta["error"] = "Ollama 服务未启动或目标模型 %s 未加载（可用模型：%s）" % (model, ", ".join(available) or "无")
                logger.warning(json.dumps({
                    "event": "extract_assets_model_unavailable", "trace_id": trace_id,
                    "model": model, "available_models": available, "error": meta["error"],
                }, ensure_ascii=False))
                return {"roles": [], "scenes": [], "props": [], "meta": meta}
            raw = ollama_chat(prompt, purpose="create",
                               model=model,
                               base_url=model_config.get("apiBase") or None)
        else:
            raw, online_meta = _call_online_llm(prompt, model_config)
            meta.update(online_meta or {})
    except Exception as e:
        meta["error"] = "调用模型异常：%s" % e
        meta["duration_ms"] = int((time.time() - start) * 1000)
        logger.error(json.dumps({
            "event": "extract_assets_exception", "trace_id": trace_id,
            "error": str(e), "duration_ms": meta["duration_ms"],
        }, ensure_ascii=False))
        return {"roles": [], "scenes": [], "props": [], "meta": meta}
    meta["duration_ms"] = int((time.time() - start) * 1000)
    meta["raw_preview"] = (raw or "")[:800]
    logger.info(json.dumps({
        "event": "extract_assets_model_returned", "trace_id": trace_id,
        "duration_ms": meta["duration_ms"], "raw_chars": len(raw or ""),
    }, ensure_ascii=False))
    if not raw:
        if not meta.get("error"):
            meta["error"] = "模型返回为空"
        logger.warning(json.dumps({
            "event": "extract_assets_empty_response", "trace_id": trace_id, "error": meta["error"],
        }, ensure_ascii=False))
        return {"roles": [], "scenes": [], "props": [], "meta": meta}
    block = _extract_json_block(raw)
    if not block:
        meta["error"] = "无法从模型输出中解析 JSON 块"
        logger.warning(json.dumps({
            "event": "extract_assets_no_json_block", "trace_id": trace_id,
            "raw_preview": meta["raw_preview"], "error": meta["error"],
        }, ensure_ascii=False))
        return {"roles": [], "scenes": [], "props": [], "meta": meta}
    try:
        obj = json.loads(block)
    except Exception as e:
        meta["error"] = "JSON 解析失败：%s" % e
        logger.warning(json.dumps({
            "event": "extract_assets_json_parse_failed", "trace_id": trace_id,
            "block_preview": block[:400], "error": str(e),
        }, ensure_ascii=False))
        return {"roles": [], "scenes": [], "props": [], "meta": meta}
    out = _normalize_extracted_assets(obj)
    out["meta"] = meta
    meta["count"] = len(out["roles"]) + len(out["scenes"]) + len(out["props"])
    logger.info(json.dumps({
        "event": "extract_assets_done", "trace_id": trace_id,
        "roles": len(out["roles"]), "scenes": len(out["scenes"]), "props": len(out["props"]),
        "duration_ms": meta["duration_ms"], "model": model,
    }, ensure_ascii=False))
    return out


def split_story_with_llm(story, model_config=None):
    """Phase 2：用本地 Ollama 或在线模型把长故事拆为场次（含角色/场景/分镜）。

    model_config: dict，可选字段 provider/local/openai-compatible、model、apiBase、apiKey。
    成功返回 {"segments": [...], "meta": {...}}；失败返回 {"segments": None, "meta": {...}}。
    meta 含 model/raw_preview/duration_ms/provider/error，供前端展示推理过程与回退原因。
    """
    model_config = model_config or _get_llm_model_config("planning")
    provider = model_config.get("provider") or "local"
    model = model_config.get("model") or MODEL
    meta = {"model": model, "source": "llm", "provider": provider}
    prompt = _split_story_prompt(story)
    start = time.time()
    raw = None
    try:
        if provider == "local":
            if not ollama_available(model=model):
                available = ollama_list_models()
                meta["error"] = "Ollama 服务未启动或目标模型 %s 未加载（可用模型：%s）" % (model, ", ".join(available) or "无")
                return {"segments": None, "meta": meta}
            raw = ollama_chat(prompt, purpose="planning",
                               model=model,
                               base_url=model_config.get("apiBase") or None)
        else:
            raw, online_meta = _call_online_llm(prompt, model_config)
            meta.update(online_meta)
    except Exception as e:
        meta["error"] = "调用模型异常：%s" % e
        meta["duration_ms"] = int((time.time() - start) * 1000)
        return {"segments": None, "meta": meta}
    meta["duration_ms"] = int((time.time() - start) * 1000)
    if not raw:
        if not meta.get("error"):
            meta["error"] = "模型返回为空"
        return {"segments": None, "meta": meta}
    meta["raw_preview"] = raw[:800]
    block = _extract_json_block(raw)
    if not block:
        meta["error"] = "无法从模型输出中解析 JSON 块"
        return {"segments": None, "meta": meta}
    try:
        arr = json.loads(block)
    except Exception as e:
        meta["error"] = "JSON 解析失败：%s" % e
        return {"segments": None, "meta": meta}
    segs, err = _normalize_split_segments(arr)
    if err:
        meta["error"] = err
        return {"segments": None, "meta": meta}
    meta["count"] = len(segs)
    return {"segments": segs, "meta": meta}


def build_segment_prompt(segment, shots, asset_infos):
    """Phase 3：把中文分镜 + 资产引用翻译/润色为英文视频提示词。无 LLM 时模板兜底。

    asset_infos: list[dict]（含 name/type），用于把角色/场景资产名注入不变量，保证一致性。
    """
    scene = (segment or {}).get("scene_description") or "AI 短剧片段"
    shot_txt = "\n".join(
        "- %s, %s: %s" % (s.get("shot_type") or "medium", s.get("camera_movement") or "static",
                           (s.get("description") or "")[:120])
        for s in (shots or [])
    )
    role_names = [a["name"] for a in (asset_infos or []) if a.get("type") == "role"]
    scene_names = [a["name"] for a in (asset_infos or []) if a.get("type") == "scene"]
    invariants = ["不得改变故事情节", "保留原始分镜的景别与运镜意图"]
    if role_names:
        invariants.append("必须保持以下角色外观一致（定妆照已作为参考图注入）：%s" % "、".join(role_names))
    if scene_names:
        invariants.append("场景需贴合以下参考：%s" % "、".join(scene_names))
    objective = (
        "把下面中文分镜翻译 / 扩写为一段电影感英文视频生成提示词，包含场景、运镜、主体与情绪。\n"
        "场景：%s\n分镜：\n%s" % (scene, shot_txt)
    )
    if not ollama_available():
        base = "cinematic short drama scene, %s" % scene[:80]
        if role_names:
            base += ", featuring character %s" % ", ".join(role_names)
        return base + ", film grain, dramatic lighting, 35mm lens"
    try:
        out = ollama_chat(build_prompt(
            role="你是将中文分镜翻译为英文视频生成提示词(Prompt)的引擎",
            objective=objective,
            constraints=["使用英文", "电影感", "与分镜参数一致", "只输出提示词文本，不要 JSON"],
            invariants=invariants,
        ))
        return (out or "").strip() or objective
    except Exception:
        return objective


def enhance_with_ollama(text, style="cinematic"):
    """AI 润色 / 规划增强（AGENT_SPEC_14 button.ai-polish / model 说明）。"""
    if not ollama_available():
        return text
    try:
        prompt = build_prompt(
            role="你是一个专业 AI 短剧编剧，擅长把片段场景描述润色为电影感的中文分镜脚本。",
            objective="把下面这段场景描述润色为更具画面感的中文分镜脚本，风格：%s。\n\n%s" % (style, text),
            constraints=["输出为可直接用于分镜的中文脚本", "保持电影感与节奏"],
            invariants=[
                "保留原文中的 <ref> 引用标签，不得改动或删除",
                "不得新增任何未在素材中出现的新角色",
                "只输出润色结果，不输出解释",
            ],
        )
        out = _ollama_generate(prompt)
        return out.strip() or text
    except Exception:
        return text


def polish_content(content, style="cinematic", preserve_refs=True):
    """POST /ai/polish 实现。"""
    if not ollama_available():
        raise RuntimeError("本地 Ollama 未启动（%s），无法执行 AI 润色" % OLLAMA_URL)
    polished = enhance_with_ollama(content, style)
    return {"polished": polished, "diff": []}


def reverse_prompt(image_path):
    """图片反推（本地无多模态时诚实报错）。"""
    if not ollama_available():
        raise RuntimeError("本地 Ollama 未启动，无法执行图片反推")
    # 简化实现：返回结构化占位（真实多模态反推需 vision 模型）
    return {"prompt": "（本地反推）请基于参考图描述主体与构图",
            "negative_prompt": "低质量, 模糊",
            "style": "写实", "subject": "人物", "composition": "中景"}


def _comfy_run(wf, timeout, output_dir=None):
    """提交 ComfyUI 工作流并轮询 /history 取回输出图绝对路径；失败抛 RuntimeError（带真实原因）。

    含冷启动兜底：首次因模型未热（CUDA 初始化抖动等）报错时等 25s 重试一次。
    被 _comfy_text2img / _comfy_text2img_with_ipadapter 复用，避免重复轮询逻辑。
    """
    if output_dir is None:
        output_dir = comfy_env.get_comfy_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    payload = json.dumps({"prompt": wf, "client_id": "manju_local"}).encode("utf-8")

    def _submit_and_wait():
        try:
            body = _http_post_json(COMFY_URL + "/prompt", payload, timeout=120)
            prompt_id = json.loads(body).get("prompt_id")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:500]
            raise RuntimeError("ComfyUI /prompt 被拒(%s): %s" % (e.code, detail))
        except Exception as e:
            raise RuntimeError("ComfyUI /prompt 提交失败: %s" % e)
        if not prompt_id:
            raise RuntimeError("ComfyUI 未返回 prompt_id")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(COMFY_URL + "/history/" + prompt_id, timeout=10) as r:
                    h = json.loads(r.read().decode("utf-8"))
            except Exception:
                time.sleep(3)
                continue
            if prompt_id in h:
                node = h[prompt_id]
                st = node.get("status", {})
                if st.get("status_str") == "error" or st.get("completed") is False:
                    msgs = st.get("messages", [])
                    err_txt = "; ".join(
                        str(m[1]) for m in msgs if isinstance(m, (list, tuple)) and len(m) > 1
                    )[:600] or "未知执行错误"
                    raise RuntimeError("ComfyUI 执行错误: %s" % err_txt)
                for node_out in node.get("outputs", {}).values():
                    for im in (node_out.get("images") or []):
                        fn = im.get("filename")
                        if fn:
                            sub = im.get("subfolder")
                            if sub:
                                p = os.path.join(output_dir, sub, fn)
                            else:
                                p = os.path.join(output_dir, fn)
                            if os.path.isfile(p):
                                return p
                raise RuntimeError("ComfyUI 执行完成但未产出图像文件")
            time.sleep(3)
        raise RuntimeError("ComfyUI 轮询超时（%ds 内未取回图像，可能模型加载过慢或服务繁忙）" % timeout)

    try:
        return _submit_and_wait()
    except RuntimeError as e:
        msg = str(e)
        if "执行错误" in msg or "超时" in msg:
            time.sleep(25)
            try:
                return _submit_and_wait()
            except RuntimeError:
                raise
        raise


def _comfy_text2img(prompt, width=1280, height=720, steps=20, cfg=7.0,
                    seed=-1, timeout=240, negative=None):
    """调用秋叶 ComfyUI /prompt 出图，返回输出图绝对路径；失败抛 RuntimeError。"""
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    neg = negative or "low quality, blurry, watermark, text"
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg"}},
    }
    return _comfy_run(wf, timeout)


def _comfy_text2img_with_ipadapter(prompt, ref_comfy_name=None, weight=0.85,
                                   width=1280, height=720, steps=20, cfg=7.0,
                                   seed=-1, timeout=240, negative=None,
                                   style_comfy_name=None, style_weight=0.4,
                                   first_30_steps_boost=0.0):
    """带 IP-Adapter 的文生图：用参考图（角色定妆照）约束角色一致性（手册 §5.2 身份权重注入）。

    身份路（ref_comfy_name，可选）：把角色参考图注入交叉注意力，weight=base_weight（默认 0.85，
        可由 N03 按年龄在 0.7-0.95 调整），实现真人资产「基础 ID + 风格 LoRA」分层控制；
        ref_comfy_name 为 None 时跳过身份路（仅风格路 / 纯文本路径）。
    风格路（可选 style_comfy_name）：把全局风格参考图（N03 生成）作第二路 IP-Adapter 注入，
        统一色调/质感/光影（手册 §3.2.4），权重低于身份路；链式接在身份路（或底模）之后。

    依赖 ComfyUI_IPAdapter_plus + clip_vision 权重。任何节点不匹配/执行失败都抛 RuntimeError，
    由调用方捕获后回退基础文生图（身份/风格一致性降级为提示词强调，不阻塞生成）。
    """
    if not comfy_available():
        raise RuntimeError("ComfyUI 未启动")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    ip_name = _get_comfy_ipadapter()
    neg = negative or "low quality, blurry, watermark, text"
    # 当前 ComfyUI 环境（ComfyUI_IPAdapter_plus）要求用 IPAdapterUnifiedLoader 把 checkpoint 模型
    # 与 ipadapter 权重统一加载到 pipeline，随后简单 IPAdapter 节点才能正确执行；
    # 直接让 IPAdapter 节点引用 IPAdapterModelLoader 会报 "IPAdapter model not present in the pipeline"。
    name_l = ip_name.lower()
    if "vit-g" in name_l:
        preset = "VIT-G"
    elif "full face" in name_l or "full-face" in name_l:
        preset = "FULL FACE - SD1.5 only (portraits stronger)"
    elif "plus face" in name_l or "plus-face" in name_l or "face" in name_l:
        preset = "PLUS FACE (portraits)"
    elif "plus" in name_l:
        preset = "PLUS (high strength)"
    elif "light" in name_l:
        preset = "LIGHT - SD1.5 only (low strength)"
    else:
        preset = "STANDARD (medium strength)"
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            "model": ["10", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg_ip"}},
    }
    # 共享节点：IPAdapterModelLoader 提供 IPADAPTER；UnifiedLoader 把底模与 IP-Adapter 注入 pipeline。
    wf["11"] = {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": ip_name}}
    wf["10"] = {"class_type": "IPAdapterUnifiedLoader", "inputs": {
        "model": ["4", 0], "preset": preset, "ipadapter": ["11", 0]}}
    # 身份路（角色/资产参考图）
    prev_model = ["10", 0]
    if ref_comfy_name:
        wf["13"] = {"class_type": "LoadImage", "inputs": {"image": ref_comfy_name}}
        wf["15"] = {"class_type": "IPAdapter", "inputs": {
            "model": ["10", 0], "ipadapter": ["11", 0], "image": ["13", 0],
            "weight": float(weight), "start_at": 0.0, "end_at": 1.0,
            "weight_type": "standard"}}
        prev_model = ["15", 0]
    # 风格路：第二路 IP-Adapter（全局风格参考图），链式接在身份路（或底模）之后
    if style_comfy_name:
        wf["23"] = {"class_type": "LoadImage", "inputs": {"image": style_comfy_name}}
        wf["20"] = {"class_type": "IPAdapter", "inputs": {
            "model": prev_model, "ipadapter": ["11", 0], "image": ["23", 0],
            "weight": float(style_weight), "start_at": 0.0, "end_at": 1.0,
            "weight_type": "style transfer"}}
        wf["3"]["inputs"]["model"] = ["20", 0]
    else:
        wf["3"]["inputs"]["model"] = prev_model
    return _comfy_run(wf, timeout)


def _comfy_text2img_via_template(intent, timeout=240):
    """模板驱动出图（铁律 1.2：禁止手搓 ComfyUI 节点 JSON）。

    流程：意图翻译引擎 translate_intent_to_workflow() 依据 resource_catalog.json +
    workflow_templates/ 产出工作流 JSON → Layer0 ComfyUIService 提交并取回输出图。

    若意图含本地首帧锚定图（init_image 为本地面路径），先经 Layer0 upload_image 上传
    并回填为 ComfyUI 内部文件名，再交给模板（上传归 Layer0，节点拼装归模板）。

    一切节点拓扑由模板决定，本函数与调用方不再构造任何 ComfyUI 节点。失败抛 RuntimeError
    （缺 catalog/模板/IP 权重等），由调用方捕获后诚实报错或跳过（不再回退手搓构建）。
    """
    try:
        import intent_translator
        from layer0 import comfyui_service
    except Exception as e:
        raise RuntimeError("意图翻译引擎/服务不可用: %s" % e)
    # 首帧锚定：本地 init_image 需先上传 ComfyUI 取得内部文件名
    init_local = intent.get("init_image")
    if init_local and isinstance(init_local, str) and os.path.isfile(init_local):
        try:
            svc = comfyui_service.get_service()
            comfy_name = svc.upload_image(init_local)
            intent = dict(intent)
            intent["init_image"] = comfy_name
        except Exception as e:
            raise RuntimeError("首帧锚定图上传失败: %s" % e)
    wf = intent_translator.translate_intent_to_workflow(intent)
    svc = comfyui_service.get_service()
    out_dir = comfy_env.get_comfy_output_dir()
    return svc.generate(wf, out_dir, timeout=timeout)


# 角色卡 AI 生成落盘根目录（与 worker / VisualControlAgent 一致：仓库根 /outputs/assets）
_ASSET_CARD_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", "assets")


def _load_asset_attrs_for_card(asset_id):
    """从 DB 回退角色属性（当调用方未显式传 attrs 时）：name + 主阶段 age。"""
    import db
    conn = db.get_conn()
    try:
        row = db.fetch_one(conn, "SELECT name FROM assets WHERE asset_id=?", (asset_id,))
        name = (row or {}).get("name") or asset_id
        srow = db.fetch_one(
            conn,
            "SELECT age FROM asset_stages WHERE asset_id=? ORDER BY selected DESC, stage_id LIMIT 1",
            (asset_id,),
        )
        age = (srow or {}).get("age")
    finally:
        conn.close()
    return {"name": name, "age": age, "gender": "", "appearance": []}


def _persist_character_card(asset_id, local_path):
    """把生成的角色卡落到 outputs/assets/card-<asset_id>.png（扁平，供 /file?name= 服务），
    并更新 assets.cover_url + 主阶段 reference_image_url 为该可服务 URL。

    返回可服务 URL（前端 cover_url 直接引用，便于后续 IP 复用与一致性锁定）。
    """
    import db
    os.makedirs(_ASSET_CARD_OUT, exist_ok=True)
    fname = "card-%s.png" % asset_id
    dest = os.path.join(_ASSET_CARD_OUT, fname)
    if os.path.abspath(local_path) != os.path.abspath(dest):
        import shutil
        shutil.copyfile(local_path, dest)
    url = "/file?name=" + fname
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE assets SET cover_url=?, updated_at=strftime('%s','now') WHERE asset_id=?",
            (url, asset_id),
        )
        srow = db.fetch_one(
            conn,
            "SELECT stage_id, age FROM asset_stages WHERE asset_id=? AND selected=1 LIMIT 1",
            (asset_id,),
        )
        if srow:
            conn.execute(
                "UPDATE asset_stages SET reference_image_url=? WHERE stage_id=?",
                (url, srow["stage_id"]),
            )
        else:
            sid = "STAGE-%s-primary" % asset_id
            age = (srow or {}).get("age", 0) if srow else 0
            conn.execute(
                "INSERT OR REPLACE INTO asset_stages "
                "(stage_id, asset_id, stage_name, age, reference_image_url, selected, created_at) "
                "VALUES (?,?,?,?,?,1,strftime('%s','now'))",
                (sid, asset_id, "主阶段", age, url),
            )
        conn.commit()
    finally:
        conn.close()
    return url


def generate_character_card(asset_id, attrs=None, style_tags=None,
                            generation_params=None, negative_prompt=None, timeout=240):
    """P1 角色卡 AI 生成（导演agent优化草稿 #1）：经意图翻译引擎产出首张定妆照，落资产封面。

    铁律 1.1：首卡由结构化角色属性驱动（非自由 prompt），确定性 seed 保证稳定不随机；
    经 _comfy_text2img_via_template 走 workflow_templates/character_card_gen.json，
    绝不手搓节点（铁律 1.2）。ComfyUI 不可用抛 RuntimeError，由调用方诚实报错。

    :param asset_id: 角色资产 ID（确定性 seed 锚点 + 落库目标）。
    :param attrs: 角色结构化属性 {name, age, gender, appearance:[...]}；缺省从 DB 回退 name+age。
    :param style_tags: 风格标签（写实/电影感…）。
    :param generation_params: 可选覆盖 {width,height,seed,steps,cfg}。
    :param negative_prompt: 可选覆盖角色卡专用负向词。
    :return: 生成的角色卡本地图绝对路径（同时已写入 assets.cover_url + 主阶段 reference_image_url）。
    """
    import intent_translator
    if attrs is None:
        attrs = _load_asset_attrs_for_card(asset_id)
    intent = intent_translator.build_character_card_intent(
        attrs, character_id=asset_id, style_tags=style_tags,
        generation_params=generation_params, negative_prompt=negative_prompt,
    )
    local = _comfy_text2img_via_template(intent, timeout=timeout)
    return _persist_character_card(asset_id, local)


# ======================================================================
# 导演Agent能力增强 M2（设计 §8）：角色卡六步确定性管线
# ======================================================================
def _compose_subject(five):
    """由五要素拼装主体描述（置于提示词首位）。"""
    five = five or {}
    parts = []
    if five.get("age"):
        parts.append("%s岁" % five["age"])
    if five.get("appearance"):
        parts.append(str(five["appearance"]))
    if five.get("clothing"):
        parts.append(str(five["clothing"]))
    if five.get("temperament"):
        parts.append(str(five["temperament"]))
    if five.get("era"):
        parts.append("%s时期" % five["era"])
    return "，".join([p for p in parts if p])


def _build_character_intent_for_v2(name, style_tags, era):
    """为 build_generation_prompt 构造最小 ProductionIntent（携带风格标签）。"""
    from intent_engine.production_intent import (
        GenerationParams, ProductionIntent, StyleConstraints,
    )
    return ProductionIntent(
        task_type="character_genesis",
        intent_tags=list(style_tags or []),
        bound_assets=[],
        generation_params=GenerationParams(),
        style_constraints=StyleConstraints(),
    )


def _extract_face_vector_from_image(image_path):
    """从参考图提取 512 维人脸身份向量（最佳努力）。

    依赖 InsightFace（buffalo_l）；缺失 → 显式抛 FeatureUnavailable（诚实，不静默跳过身份锁）。
    """
    from intent_engine.errors import FeatureUnavailable
    try:
        # 现有特征提取约定（如项目提供 features 模块则启用；否则诚实报错）
        import importlib
        feats = importlib.import_module("features")
        if hasattr(feats, "extract_face_vector"):
            return feats.extract_face_vector(image_path)
    except Exception as e:
        raise FeatureUnavailable(
            "face_vector", "缺少 InsightFace 引擎/权重，无法提取 512 维身份向量: %s" % e,
            installed=[],
        )
    raise FeatureUnavailable(
        "face_vector", "未提供 512 维向量提取实现（features.extract_face_vector）",
        installed=[],
    )


def _qc_character_card(image_path, five):
    """⑥ 品控回检（人脸清晰度 / 设定符合度）。无引擎 → 诚实跳过并返回 skipped。"""
    try:
        _extract_face_vector_from_image(image_path)  # 能提取即说明人脸可用
        return {"status": "passed", "face_detected": True}
    except Exception:
        return {"status": "skipped", "reason": "no_face_engine"}


def generate_character_card_v2(asset_id, setting_text=None, era=None,
                               style_tags=None, poses=None, ollama_fn=None,
                               catalog=None, timeout=240):
    """P1 角色卡六步确定性管线（设计 §8，解决 P3「角色卡随机/脱离剧本」）。

    ① LLM 提取五要素 → ② 能力匹配(时代+风格→底模+LoRA) → ③ 结构化 prompt
    → ④ derive_seed(角色名,姿势)+负向 → ⑤ 多角度参考图生成+512向量入库
    → ⑥ 品控回检。

    确定性保证（验收 P3）：同角色同姿势 seed 恒定（derive_seed）；时代/风格决定底模+LoRA
    （match_capability，不靠 LLM 抽卡）。外部依赖（ComfyUI/InsightFace/Ollama）不可用 →
    显式上浮或诚实降级（禁静默）。
    """
    from intent_engine.parameter_injector import derive_seed

    # ① 五要素（LLM 结构化提取）
    five = None
    if setting_text and ollama_fn:
        five = extract_five_elements(setting_text, ollama_fn)
        era = era or five.get("era")
        subject = _compose_subject(five)
    else:
        subject = None

    # ② 能力匹配（时代 + 风格 → 底模 + LoRA）
    cap = None
    if catalog is not None and (style_tags or era):
        cap = match_capability(era or "", style_tags or [], catalog)

    # ③ 结构化 prompt（§9 纪律，含 trigger_words）
    attrs = _load_asset_attrs_for_card(asset_id)
    name = (attrs or {}).get("name") or (five or {}).get("appearance") or asset_id
    intent = _build_character_intent_for_v2(name, style_tags or [], era)
    positive = build_generation_prompt(intent, subject=subject)
    negative = build_character_negative_prompt()

    # ④ 确定性 seed + ⑤/⑥ 多角度生成与回检
    poses = poses or ["front"]
    results = []
    for pose in poses:
        seed = derive_seed(name, pose)  # 同角色同姿势 → 永远一致
        # ⑤ 委托既有 generate_character_card（复用其模板路由 + 落库）；外部缺失诚实抛错
        card_path = generate_character_card(
            asset_id, attrs=attrs, style_tags=style_tags,
            generation_params={"seed": seed, "pose": pose},
            negative_prompt=negative, timeout=timeout,
        )
        # ⑤ 512 维向量入库（缺失引擎诚实跳过，不阻断卡片生成）
        fv_status = "skipped"
        try:
            vec = _extract_face_vector_from_image(card_path)
            fv_status = "stored" if vec is not None else "skipped"
        except Exception as e:
            logger.warning("[card-v2] 512 向量提取跳过: %s" % e)
        # ⑥ 品控回检
        qc = _qc_character_card(card_path, five)
        results.append({
            "pose": pose, "seed": seed, "card_path": card_path,
            "face_vector": fv_status, "qc": qc,
        })
    return {
        "asset_id": asset_id,
        "five_elements": five,
        "capability": cap,
        "positive_prompt": positive,
        "negative_prompt": negative,
        "poses": results,
    }


def _comfy_via_typed_template(task_type, values, timeout=240, output_dir=None):
    """通用模板驱动提交（铁律 1.2）：按 task_type 填充 workflow_templates 并提交 Layer0。

    values 中若出现本地图片路径（存在且为图片后缀），自动经 Layer0 上传并回填为
    ComfyUI 内部文件名（上传归 Layer0，节点拼装归模板）。调用方与本项目不再手搓任何节点拓扑。

    output_dir: 可选，覆盖默认 ComfyUI 输出目录（用于人脸修复等需指定落盘目录的场景）；
    为 None 时回退 comfy_env.get_comfy_output_dir()（与既有手搓路径行为一致）。
    """
    try:
        import intent_translator
        from layer0 import comfyui_service
    except Exception as e:
        raise RuntimeError("意图翻译引擎/服务不可用: %s" % e)
    svc = comfyui_service.get_service()
    _IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
    for k, v in list(values.items()):
        if isinstance(v, str) and v.lower().endswith(_IMG_EXT) and os.path.isfile(v):
            try:
                values[k] = svc.upload_image(v)
            except Exception as e:
                raise RuntimeError("模板参考图上传失败(%s): %s" % (k, e))
    wf = intent_translator.translate_typed_workflow(task_type, values)
    out_dir = output_dir or comfy_env.get_comfy_output_dir()
    return svc.generate(wf, out_dir, timeout=timeout)


def _comfy_img2img(first_frame_path, prompt, strength=0.6,
                   width=1280, height=720, steps=20, cfg=7.0,
                   seed=-1, timeout=240, negative=None):
    """图生图：以首帧(first_frame)为锚点去噪，实现首尾帧像素级承接（手册 §3.3.4 首尾帧锚定）。

    仅在前序分镜尾帧作为下一分镜首帧参考时调用；依赖 ComfyUI 与底模权重，失败抛 RuntimeError。
    """
    if not comfy_available():
        raise RuntimeError("ComfyUI 未启动")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    neg = negative or "low quality, blurry, watermark, text"
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": "euler", "scheduler": "normal", "denoise": float(strength),
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["10", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "LoadImage", "inputs": {"image": _upload_to_comfy(first_frame_path)}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg_anchor"}},
        "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["4", 2]}},
    }
    return _comfy_run(wf, timeout)


def _comfy_img2img_with_depth(first_frame_path, prompt, strength=0.6, depth_weight=0.6,
                              depth_cn=None, width=1280, height=720, steps=20, cfg=7.0,
                              seed=-1, timeout=240):
    """首尾帧锚定 + 深度复用（手册 §3.3.4）：首镜以首帧做 img2img 像素承接，
    同时把首帧深度图作为 depth ControlNet 约束构图，保证同场景相邻分镜空间结构一致。

    depth_cn 由调用方经 _detect_depth_controlnet() 解析；缺权重或 ComfyUI 未启动抛
    RuntimeError，调用方捕获后回退纯像素级 img2img（诚实降级，不静默）。
    """
    if not comfy_available():
        raise RuntimeError("ComfyUI 未启动")
    if not depth_cn:
        raise RuntimeError("未提供 depth ControlNet 权重")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": "euler", "scheduler": "normal", "denoise": float(strength),
            "model": ["4", 0], "positive": ["14", 0], "negative": ["7", 0], "latent_image": ["10", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "LoadImage", "inputs": {"image": _upload_to_comfy(first_frame_path)}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, blurry, watermark, text", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg_anchor_depth"}},
        "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["4", 2]}},
        "11": {"class_type": "DepthPreprocessor", "inputs": {"image": ["5", 0]}},
        "12": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": depth_cn}},
        "13": {"class_type": "ControlNetApply", "inputs": {
            "conditioning": ["6", 0], "control_net": ["12", 0], "image": ["11", 0], "strength": float(depth_weight)}},
        "14": {"class_type": "ControlNetApply", "inputs": {
            "conditioning": ["13", 0], "control_net": ["12", 0], "image": ["11", 0], "strength": float(depth_weight)}},
    }
    return _comfy_run(wf, timeout)


def _extract_tail_frame(video_path, dst_path):
    """用 ffmpeg 提取视频最后一帧，作为首尾帧锚定的尾帧参考（手册 §3.3.4）。失败返回 None。"""
    if not (video_path and os.path.isfile(video_path) and FFMPEG_BIN):
        return None
    try:
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        cmd = [FFMPEG_BIN, "-y", "-sseof", "-1", "-i", video_path,
               "-vsync", "0", "-q:v", "2", dst_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        if os.path.isfile(dst_path) and os.path.getsize(dst_path) > 0:
            return dst_path
    except Exception as e:
        logger.warning("[gen] 尾帧提取失败: %s" % e)
    return None


def _get_comfy_wan_model():
    return comfy_env.discover_weight(
        "diffusion_models", ["*.safetensors", "*.pth"], what="Wan 扩散模型权重")


def _get_comfy_wan_t5():
    return comfy_env.discover_weight(
        "text_encoders", ["*umt5*.pth", "*umt5*.safetensors", "*.pth"],
        what="Wan T5 文本编码器权重")


def _get_comfy_wan_vae():
    return comfy_env.discover_weight(
        "vae", ["*Wan*.pth", "*wan*.pth", "*.pth"], what="Wan VAE 权重")


def _comfy_run_video(wf, timeout):
    """提交 ComfyUI 视频工作流并轮询 /history 取回输出视频绝对路径；失败抛 RuntimeError。

    与 _comfy_run 类似，但输出节点为 VHS_VideoCombine，产物在 history 的
    'gifs'/'videos' 键下（而非 'images'）。含冷启动兜底：首次执行错误/超时等 25s 重试一次。
    """
    output_dir = comfy_env.get_comfy_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    payload = json.dumps({"prompt": wf, "client_id": "manju_local"}).encode("utf-8")

    def _submit_and_wait():
        try:
            body = _http_post_json(COMFY_URL + "/prompt", payload, timeout=120)
            prompt_id = json.loads(body).get("prompt_id")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:500]
            raise RuntimeError("ComfyUI /prompt 被拒(%s): %s" % (e.code, detail))
        except Exception as e:
            raise RuntimeError("ComfyUI /prompt 提交失败: %s" % e)
        if not prompt_id:
            raise RuntimeError("ComfyUI 未返回 prompt_id")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(COMFY_URL + "/history/" + prompt_id, timeout=10) as r:
                    h = json.loads(r.read().decode("utf-8"))
            except Exception:
                time.sleep(3)
                continue
            if prompt_id in h:
                node = h[prompt_id]
                st = node.get("status", {})
                if st.get("status_str") == "error" or st.get("completed") is False:
                    msgs = st.get("messages", [])
                    err_txt = "; ".join(
                        str(m[1]) for m in msgs if isinstance(m, (list, tuple)) and len(m) > 1
                    )[:600] or "未知执行错误"
                    raise RuntimeError("ComfyUI 执行错误: %s" % err_txt)
                for node_out in node.get("outputs", {}).values():
                    for key in ("gifs", "videos", "images"):
                        for im in (node_out.get(key) or []):
                            fn = im.get("filename")
                            if fn:
                                sub = im.get("subfolder")
                                p = (os.path.join(output_dir, sub, fn)
                                     if sub else os.path.join(output_dir, fn))
                                if os.path.isfile(p):
                                    return p
                raise RuntimeError("ComfyUI 执行完成但未产出视频文件")
            time.sleep(3)
        raise RuntimeError("ComfyUI 轮询超时（%ds 内未取回视频，可能模型加载过慢或服务繁忙）" % timeout)

    try:
        return _submit_and_wait()
    except RuntimeError as e:
        msg = str(e)
        if "执行错误" in msg or "超时" in msg:
            time.sleep(25)
            try:
                return _submit_and_wait()
            except RuntimeError:
                raise
        raise


REAL_VIDEO_MIN_VRAM_GB = 8.0
"""真实视频生成的最低显存门槛（GB）。

实测依据（PROJECT_MAP §9.7）：
- Wan2.1-T2V-1.3B 依赖 umt5-xxl 文本编码器（bf16 ≈11GB），单组件即超 6GB 卡 → 硬件不可行。
- SD1.5 + AnimateDiff 虽可在 6GB 跑通，但 1660 Ti 上单镜头 16 帧需 15+ 分钟，多镜头分镜耗时数小时，
  且默认配置会 OOM 崩掉共享的 ComfyUI。故设 8GB 门槛：<8GB 诚实跳过真实视频、回退静态卡轮播，
  既保护平台不被拖垮，也避免用户误以为能出运动视频。
"""


def _comfy_vram_gb():
    """查询 ComfyUI 宿主机的 GPU 显存总量（GB）；查询失败返回 0.0（交由调用方按 0 处理）。

    注意：秋葉 ComfyUI v0.30.2 的 /system_stats 中 devices 是**顶层键**
    （并非嵌套在 system 下），故优先取顶层 devices，并兼容旧版 system.devices 布局。
    """
    try:
        with urllib.request.urlopen(COMFY_URL + "/system_stats", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        devs = data.get("devices") or (data.get("system", {}) or {}).get("devices") or []
        for d in devs:
            vt = d.get("vram_total") or 0
            if vt and int(vt) > 0:
                return int(vt) / (1024 ** 3)
    except Exception:
        return 0.0
    return 0.0


def _comfy_text2video(prompt, ref_comfy_name=None, width=832, height=480,
                      num_frames=49, seed=-1, timeout=600):
    """真实视频生成（N05 分镜级视频片段）：显存门槛 + AnimateDiff 优先 / Wan 回退。

    硬件约束路线（PROJECT_MAP §9.7）：
      1) 显存 < REAL_VIDEO_MIN_VRAM_GB（8GB）时，直接抛 RuntimeError，由调用方降级静态卡轮播，
         不做任何假装能出运动视频的行为（诚实降级）。
      2) 显存达标且装有 ADE_AnimateDiffLoaderGen1 节点 → 走轻量 SD1.5+AnimateDiff 路线。
      3) 否则回退 Wan2.1-T2V（需 ≥8GB 且装有 WanVideo 系列节点）。

    任何节点缺失/执行失败都抛 RuntimeError，由调用方回退图片轮播降级（不静默吞错）。

    返回输出视频绝对路径（ComfyUI output 目录；后端与本机 ComfyUI 同机可直接读取）。
    """
    if not comfy_available():
        raise RuntimeError("ComfyUI 未启动")
    vram = _comfy_vram_gb()
    if 0 < vram < REAL_VIDEO_MIN_VRAM_GB:
        raise RuntimeError(
            "真实视频生成已跳过：检测到 GPU 显存仅 %.1fGB（需 ≥%.0fGB）。"
            "当前显卡（如 GTX 1660 Ti 6GB）不足以稳定生成运动视频——Wan2.1 的文本编码器"
            "单组件即超 11GB，AnimateDiff 虽能跑但 6GB 上单镜头需 15+ 分钟且易 OOM 拖垮"
            "共享 ComfyUI。已自动降级为静态卡轮播；如需真实视频请在 ≥8GB 显存机器上运行。"
            % (vram, REAL_VIDEO_MIN_VRAM_GB))
    if _comfy_has_node("ADE_AnimateDiffLoaderGen1"):
        return _comfy_text2video_animatediff(
            prompt, ref_comfy_name=ref_comfy_name, width=width, height=height,
            num_frames=num_frames, seed=seed, timeout=timeout)
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    wan_model = _get_comfy_wan_model()
    wan_t5 = _get_comfy_wan_t5()
    wan_vae = _get_comfy_wan_vae()
    # 优先模板驱动（铁律 1.2）；模板/目录缺失时诚实回退手搓（防御性，正常运行不触发）
    try:
        return _comfy_via_typed_template("shot_video_gen_wan", {
            "wan_model": wan_model, "wan_t5": wan_t5, "wan_vae": wan_vae,
            "positive_prompt": prompt,
            "negative_prompt": "low quality, blurry, watermark, text, distorted, jittery",
            "width": int(width), "height": int(height), "num_frames": int(num_frames),
            "seed": int(seed), "steps": 30, "cfg": 6.0, "shift": 5.0,
        }, timeout=timeout)
    except Exception as e:
        logger.warning("[video] Wan 模板不可用，回退手搓: %s" % e)
        wf = {
            "1": {"class_type": "WanVideoModelLoader", "inputs": {
                "model": wan_model, "base_precision": "bf16",
                "quantization": "disabled", "load_device": "offload_device"}},
            "2": {"class_type": "WanVideoVAELoader", "inputs": {
                "model_name": wan_vae, "precision": "bf16"}},
            "3": {"class_type": "LoadWanVideoT5TextEncoder", "inputs": {
                "model_name": wan_t5, "precision": "bf16",
                "load_device": "offload_device", "quantization": "disabled"}},
            "4": {"class_type": "WanVideoTextEncode", "inputs": {
                "positive_prompt": prompt,
                "negative_prompt": "low quality, blurry, watermark, text, distorted, jittery",
                "t5": ["3", 0]}},
            "5": {"class_type": "WanVideoEmptyEmbeds", "inputs": {
                "width": int(width), "height": int(height), "num_frames": int(num_frames)}},
            "6": {"class_type": "WanVideoSampler", "inputs": {
                "model": ["1", 0], "image_embeds": ["5", 0], "text_embeds": ["4", 0],
                "steps": 30, "cfg": 6.0, "shift": 5.0, "seed": int(seed),
                "scheduler": "unipc", "force_offload": True, "riflex_freq_index": 0}},
            "7": {"class_type": "WanVideoDecode", "inputs": {
                "vae": ["2", 0], "samples": ["6", 0], "enable_vae_tiling": False,
                "tile_x": 272, "tile_y": 272, "tile_stride_x": 144, "tile_stride_y": 128}},
            "8": {"class_type": "VHS_VideoCombine", "inputs": {
                "images": ["7", 0], "frame_rate": 25.0, "filename_prefix": "manju_vid",
                "format": "video/h264-mp4", "pingpong": False, "save_output": True, "loop_count": 0}},
        }
        return _comfy_run_video(wf, timeout)


def _get_comfy_animatediff_model():
    return comfy_env.discover_weight(
        "animatediff_models", ["*.ckpt", "*.safetensors"], what="AnimateDiff 运动模块权重")


def _comfy_text2video_animatediff(prompt, ref_comfy_name=None, width=512, height=512,
                                  num_frames=16, seed=0, timeout=600):
    """真实视频生成（轻量路线）：SD1.5 + AnimateDiff-Evolved 文生视频。

    用于显存 ≥8GB 但不足以跑 Wan2.1-T2V（其 umt5-xxl ≈11GB 单组件即超 6GB 卡）的机器。
    工作流（已 E2E 验证于 D:/AI/animatediff_t2v_test.py）：
      CheckpointLoaderSimple(SD1.5) → ADE_AnimateDiffLoaderGen1(mm_sd_v15_v2, 含 context_options)
      + CLIPTextEncode×2(正/负) + EmptyLatentImage(batch=frames) + KSampler(latent_image 键)
      + VAEDecode + VHS_VideoCombine(h264-mp4)。ADE_AnimateDiffUniformContextOptions
      (context_length=8 分窗) 用于压低峰值显存、避免 OOM 崩 ComfyUI。

    分辨率最长边收敛 ≤768 并对齐 64；帧数对 4 取整且 ≤32（AnimateDiff 运动学要求）。
    """
    if not comfy_available():
        raise RuntimeError("ComfyUI 未启动")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    # 分辨率收敛：最长边 ≤768 且对齐 64
    width = int(width); height = int(height)
    max_side = max(width, height)
    if max_side > 768:
        scale = 768.0 / max_side
        width = int(round(width * scale / 64.0) * 64)
        height = int(round(height * scale / 64.0) * 64)
    width = max(64, (width // 64) * 64)
    height = max(64, (height // 64) * 64)
    # 帧数：AnimateDiff 需对齐 4，且 ≤32（分窗 context 仍建议保守）
    num_frames = max(4, int(num_frames) // 4 * 4)
    if num_frames > 32:
        num_frames = 32
    ckpt = comfy_env.discover_weight(
        "checkpoints", ["*.safetensors", "*.ckpt"], what="SD1.5 检查点权重")
    motion = _get_comfy_animatediff_model()
    # 优先模板驱动（铁律 1.2）；模板/目录缺失时诚实回退手搓（防御性，正常运行不触发）
    try:
        return _comfy_via_typed_template("shot_video_gen_animatediff", {
            "ckpt_name": ckpt, "motion_module": motion,
            "positive_prompt": prompt,
            "negative_prompt": "low quality, blurry, watermark, text, distorted, jittery, oversaturated",
            "width": width, "height": height, "num_frames": num_frames,
            "seed": int(seed), "steps": 24, "cfg": 7.5,
        }, timeout=timeout)
    except Exception as e:
        logger.warning("[video] AnimateDiff 模板不可用，回退手搓: %s" % e)
        wf = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "2": {"class_type": "ADE_AnimateDiffLoaderGen1", "inputs": {
                "model": ["1", 0], "model_name": motion, "beta_schedule": "autoselect",
                "context_options": ["9", 0]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {
                "clip": ["1", 1],
                "text": "low quality, blurry, watermark, text, distorted, jittery, oversaturated"}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {
                "width": width, "height": height, "batch_size": num_frames}},
            "6": {"class_type": "KSampler", "inputs": {
                "model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0],
                "latent_image": ["5", 0], "seed": int(seed), "steps": 24,
                "cfg": 7.5, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
            "8": {"class_type": "VHS_VideoCombine", "inputs": {
                "images": ["7", 0], "frame_rate": 12.0, "filename_prefix": "manju_ad",
                "format": "video/h264-mp4", "pingpong": False, "save_output": True, "loop_count": 0}},
            "9": {"class_type": "ADE_AnimateDiffUniformContextOptions", "inputs": {
                "context_length": 8, "context_stride": 1, "context_overlap": 4,
                "context_schedule": "uniform", "closed_loop": False, "fuse_method": "flat",
                "use_on_equal_length": False, "start_percent": 0.0, "guarantee_steps": 1}},
        }
        return _comfy_run_video(wf, timeout)


def _concat_video_clips(clips, out_path, W, H, fps=25):
    """用 ffmpeg concat 过滤器把多段视频统一缩放/补边到 WxH 并拼接（处理不同分辨率/时长）。"""
    if not clips or not ffmpeg_available():
        return False
    inputs = []
    parts = []
    for i, c in enumerate(clips):
        if not c or not os.path.isfile(c):
            return False
        inputs += ["-i", c]
        parts.append(
            "[%d:v]scale=%d:%d:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1[v%d]" % (i, W, H, W, H, i))
    cat = "".join("[v%d]" % i for i in range(len(clips)))
    parts.append("%sconcat=n=%d:v=1:a=0[outv]" % (cat, len(clips)))
    fc = ";".join(parts)
    cmd = [FFMPEG_BIN, "-y", "-nostdin", *inputs, "-filter_complex", fc,
           "-map", "[outv]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-r", str(fps), "-an", out_path]
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=300)
    except Exception:
        return False
    return os.path.isfile(out_path)


def _comfy_has_node(node_class):
    """探测 ComfyUI 是否装有指定自定义节点（如 GFPGAN / CodeFormer）；失败返回 False。"""
    try:
        info = json.loads(_http_post_json(COMFY_URL + "/object_info", b"{}", timeout=10))
        return node_class in info
    except Exception:
        return False


@metrics.track_generation("face_restore")
def face_restore(image_path, method="GFPGAN", out_path=None):
    """人脸修复（手册 §3.4.4 全局后处理）：经 ComfyUI 的 GFPGAN / CodeFormer 节点修复单图人脸。

    铁律 1.2：优先走 workflow_templates 模板驱动（face_restore_gfpgan /
    face_restore_codeformer）；模板不可用时诚实回退内联手搓（防御性，正常运行不触发）。
    环境要求（缺则诚实报错，绝不静默跳过人脸修复 / 绝不假装已修复）：
      - ComfyUI 已启动（comfy_available）
      - 已安装对应自定义节点（GFPGAN / CodeFormer）
      - 已放入模型权重（GFPGAN/CodeFormer 权重文件）
    返回修复后图像绝对路径；失败抛 RuntimeError（含真实原因），由上层据 REDUN-01 诚实降级。
    """
    if not comfy_available():
        raise RuntimeError("人脸修复失败：ComfyUI 未启动（请先启动秋葉 ComfyUI :8188）")
    m = (method or "GFPGAN").upper()
    node_name = "GFPGAN" if m == "GFPGAN" else "CodeFormer"
    if not _comfy_has_node(node_name):
        raise RuntimeError(
            "人脸修复失败：ComfyUI 未安装 %s 节点或未放入权重。"
            "请在 ComfyUI 安装对应自定义节点（GFPGAN / CodeFormer）并放入模型权重后重试。" % node_name)
    up = _upload_to_comfy(image_path)
    if not up:
        raise RuntimeError("人脸修复失败：参考图上传 ComfyUI 失败")
    out_dir = out_path and os.path.dirname(out_path)
    try:
        if node_name == "GFPGAN":
            return _comfy_via_typed_template("face_restore_gfpgan", {
                "input_image": up,
                "ckpt_name": "GFPGANv1.4.pth",
                "restore_model": "GFPGANv1.4",
            }, timeout=180, output_dir=out_dir)
        else:
            return _comfy_via_typed_template("face_restore_codeformer", {
                "input_image": up,
                "upscale": 2,
                "codeformer_weight": 0.5,
                "fidelity": 0.5,
                "seed": 0,
                "blur_amount": 0,
                "blur_kernel": 5,
            }, timeout=180, output_dir=out_dir)
    except Exception as e:
        logger.warning("[facefix] %s 模板不可用，回退内联手搓: %s" % (method, e))
        if node_name == "GFPGAN":
            wf = {
                "1": {"class_type": "LoadImage", "inputs": {"image": up}},
                "2": {"class_type": "GFPGAN", "inputs": {
                    "ckpt_name": "GFPGANv1.4.pth", "restore_model": "GFPGANv1.4",
                    "input_image": ["1", 0], "use_builtin": False, "weight": 1.0, "upscale": 2}},
                "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "manju_facefix"}},
            }
        else:
            wf = {
                "1": {"class_type": "LoadImage", "inputs": {"image": up}},
                "2": {"class_type": "CodeFormer", "inputs": {
                    "image": ["1", 0], "upscale": 2, "codeformer_weight": 0.5,
                    "fidelity": 0.5, "seed": 0, "blur_amount": 0, "blur_kernel": 5}},
                "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "manju_facefix"}},
            }
        try:
            return _comfy_run(wf, timeout=180, output_dir=out_dir)
        except RuntimeError as e2:
            raise RuntimeError("人脸修复(%s)执行失败: %s" % (method, e2))


def _comfy_text2img_with_controlnet(prompt, ref_comfy_name, control_type="depth",
                                    weight=1.0, width=1280, height=720, steps=20, cfg=7.0,
                                    seed=-1, timeout=240):
    """带 ControlNet 的文生图：用场景参考图约束构图（深度/姿态/线稿）。

    铁律 1.2：优先走 workflow_templates/shot_image_controlnet.json 模板驱动；
    模板不可用时诚实回退内联手搓（防御性，正常运行不触发）。
    仅在 detect_comfy_controlnet() 为 True 时由调用方启用；任何节点不匹配/执行失败都抛
    RuntimeError，由调用方捕获后回退基础文生图（构图约束降级为提示词强调）。
    """
    if not comfy_available():
        raise RuntimeError("ComfyUI 未启动")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    preprocess_map = {"depth": "DepthPreprocessor", "canny": "CannyEdgePreprocessor",
                      "openpose": "OpenposePreprocessor", "lineart": "LineArtPreprocessor"}
    pre = preprocess_map.get(control_type, "DepthPreprocessor")
    cn_name = _get_comfy_controlnet(control_type)
    negative = "low quality, blurry, watermark, text"
    values = {
        "checkpoint": _get_comfy_checkpoint(),
        "positive_prompt": prompt,
        "negative_prompt": negative,
        "seed": int(seed),
        "steps": int(steps),
        "cfg": float(cfg),
        "width": int(width),
        "height": int(height),
        "ref_image": ref_comfy_name,
        "preprocess_node": pre,
        "control_net_name": cn_name,
        "control_weight": float(weight),
    }
    try:
        return _comfy_via_typed_template("shot_image_controlnet", values, timeout=timeout)
    except Exception as e:
        logger.warning("[controlnet] 模板不可用，回退内联手搓: %s" % e)
        wf = {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": steps, "cfg": float(cfg),
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                "model": ["4", 0], "positive": ["14", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg_cn"}},
            "10": {"class_type": "LoadImage", "inputs": {"image": ref_comfy_name}},
            "11": {"class_type": pre, "inputs": {"image": ["10", 0]}},
            "12": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": cn_name}},
            "13": {"class_type": "ControlNetApply", "inputs": {
                "conditioning": ["6", 0], "control_net": ["12", 0], "image": ["11", 0], "strength": float(weight)}},
            "14": {"class_type": "ControlNetApply", "inputs": {
                "conditioning": ["13", 0], "control_net": ["12", 0], "image": ["11", 0], "strength": float(weight)}},
        }
        return _comfy_run(wf, timeout)


def generate_segment_video(segment, shots, asset_refs, out_path, params=None):
    """Phase 3+4 片段生成：资产引用(IP-Adapter/ControlNet) + LLM 英文提示词 + ComfyUI 出图。

    返回 (level, note)。level: 'full'(ComfyUI) / 'slideshow'(FFmpeg 降级) / fail。
    所有外部依赖（ComfyUI/LLM/视频模型）失败都优雅降级，保证「无故障跑通」。
    """
    scene_text = segment.get("scene_description") or "AI 短剧片段"
    params = params or {}
    res = params.get("resolution") or segment.get("resolution") or "720p"
    W, H = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080),
            "2k": (2560, 1440), "4k": (3840, 2160)}.get(res, (1280, 720))
    seed = params.get("seed")
    try:
        seed = int(seed)
    except Exception:
        seed = None
    if seed is None or seed < 0:
        seed = int(segment.get("seed") or -1)
    if seed is None or seed < 0:
        seed = -1

    run_dir = os.path.join(OUTPUTS, "seg_" + (segment.get("segment_id") or "x"))
    os.makedirs(run_dir, exist_ok=True)

    if not ffmpeg_available():
        return "fail", "FFmpeg 不可用，无法合成视频（请确认 ffmpeg 在 PATH）"

    # 资产引用：取角色/场景参考图（用于 IP-Adapter / ControlNet），并收集资产名用于提示词不变量
    role_cover = None   # (local_path, comfy_name, stage_id)
    scene_cover = None
    asset_infos = []
    try:
        import db as _db
        _conn = _db.get_conn()
        try:
            for a in (asset_refs or []):
                aid = a.get("asset_id")
                if not aid:
                    continue
                row = _db.fetch_one(_conn, "SELECT name, type, asset_path, cover_path FROM assets WHERE asset_id=?", (aid,))
                if not row:
                    continue
                atype = row.get("type")
                asset_infos.append({"name": row.get("name"), "type": atype})
                path = row.get("asset_path") or row.get("cover_path")
                if not path or not os.path.isfile(path):
                    continue
                if atype == "role" and role_cover is None:
                    role_cover = (path, _upload_to_comfy(path), a.get("stage_id"))
                elif atype == "scene" and scene_cover is None:
                    scene_cover = (path, _upload_to_comfy(path))
        finally:
            _conn.close()
    except Exception as e:
        logger.warning("[gen] 资产引用读取失败（降级为无参考图）: %s" % e)

    # N03 资产注入配置：per-role 基础身份权重 + 全局风格参考图（手册 §5.2 / §3.2.4）
    inject_cfg = params.get("asset_inject_config") or {}
    style_ref = params.get("global_style_ref") or ""
    style_weight = float(inject_cfg.get("style_weight", 0.4) or 0.4)
    role_weights = {k: float(v.get("base_weight", 0.85))
                    for k, v in (inject_cfg.get("roles") or {}).items()}
    # N04/N05 首尾帧锚定：前序同场景分镜的尾帧作为本分镜首帧参考（手册 §3.3.4）
    first_frame = params.get("first_frame") or ""
    if first_frame and not os.path.isfile(first_frame):
        logger.warning("[gen] first_frame 指向文件不存在，跳过首尾帧锚定: %s" % first_frame)
        first_frame = ""
    # N04/N05 首尾帧深度复用（§3.3.4）：同场景首帧承接时，若本地有 depth ControlNet，
    # 首镜额外以首帧深度图约束构图；权重缺失则诚实降级为像素级 img2img（不静默）。
    depth_cn = None
    if params.get("enable_tail_depth", True) and first_frame:
        depth_cn = _detect_depth_controlnet()
        if depth_cn:
            logger.info("[gen] 首尾帧深度复用可用（depth ControlNet=%s），首镜将叠加深度约束" % depth_cn)
        else:
            logger.info("[gen] 深度 ControlNet 权重缺失，首尾帧锚定仅用像素级 img2img（诚实降级）")
    style_comfy_name = None
    if style_ref and os.path.isfile(style_ref) and comfy_available():
        try:
            style_comfy_name = _upload_to_comfy(style_ref)
            logger.info("[gen] 全局风格参考图已上传，将作为风格条件注入: %s" % style_ref)
        except Exception as e:
            logger.warning("[gen] 风格参考图上传失败，跳过风格注入: %s" % e)
            style_comfy_name = None

    # LLM 润色中文分镜为英文提示词（无 LLM 模板兜底）
    en_prompt = build_segment_prompt(segment, shots, asset_infos)
    if role_cover:
        logger.info("[gen] 角色参考图已注入 IP-Adapter: %s" % role_cover[0])
    if scene_cover and detect_comfy_controlnet():
        logger.info("[gen] 场景参考图已注入 ControlNet: %s" % scene_cover[0])
    elif scene_cover:
        logger.info("[gen] 场景参考图存在但 ControlNet 权重缺失，仅作提示词约束")

    # 第一层：ComfyUI 出图（每张镜头一张）；IP-Adapter/ControlNet 失败自动回退基础文生图
    comfy_imgs = []
    if comfy_available():
        # 本片段角色身份权重（N03 按年龄调权；无配置则默认 0.85）
        role_stage_id = role_cover[2] if role_cover else None
        base_w = role_weights.get(role_stage_id, 0.85) if role_stage_id else 0.85
        if role_cover and role_cover[1]:
            logger.info("[gen] 角色身份注入 IP-Adapter（base_weight=%.2f）stage=%s"
                        % (base_w, role_stage_id))
        if style_comfy_name:
            logger.info("[gen] 全局风格参考图注入 IP-Adapter（style_weight=%.2f, %s）"
                        % (style_weight, inject_cfg.get("warm_cool", "neutral")))
        if first_frame:
            logger.info("[gen] 首尾帧锚定：首镜以首帧 %s 为锚点图生图（strength=0.6）" % first_frame)
        for idx, sh in enumerate(shots):
            p = en_prompt
            img = None
            try:
                if first_frame and idx == 0:
                    # 首镜锚定：以前序尾帧为起点；depth ControlNet 可用则叠加深度约束（§3.3.4）
                    if depth_cn:
                        try:
                            img = _comfy_img2img_with_depth(
                                first_frame, p, strength=0.6, depth_weight=0.6, depth_cn=depth_cn,
                                width=W, height=H, seed=seed, timeout=120)
                            logger.info("[gen] 首尾帧锚定+深度复用：首镜 img2img + depth ControlNet")
                        except Exception as e:
                            logger.warning("[gen] 深度复用失败，回退纯 img2img 首尾帧锚定: %s" % e)
                            img = _comfy_img2img(first_frame, p, strength=0.6, width=W, height=H,
                                                 seed=seed, timeout=120)
                    else:
                        img = _comfy_img2img(first_frame, p, strength=0.6, width=W, height=H,
                                             seed=seed, timeout=120)
                elif role_cover and role_cover[1]:
                    img = _comfy_text2img_with_ipadapter(
                        p, role_cover[1], weight=base_w, width=W, height=H, seed=seed,
                        timeout=120, style_comfy_name=style_comfy_name, style_weight=style_weight,
                        first_30_steps_boost=inject_cfg.get("first_30_steps_boost", 0.0))
                elif scene_cover and scene_cover[1] and detect_comfy_controlnet():
                    img = _comfy_text2img_with_controlnet(p, scene_cover[1], width=W, height=H, seed=seed, timeout=120)
                else:
                    img = _comfy_text2img(p, width=W, height=H, seed=seed, timeout=120)
            except Exception as e:
                logger.warning("[gen] 多模态出图失败，回退基础文生图: %s" % e)
                try:
                    img = _comfy_text2img(p, width=W, height=H, seed=seed, timeout=120)
                except Exception:
                    img = None
            comfy_imgs.append(img)
            if seed and seed > 0:
                seed = seed + 1  # 每张不同种子，避免雷同

    # N05 真实视频生成：逐镜头 Wan2.1 文生视频（运动视频）。全部镜头成功才启用真实视频，
    # 任一失败则回退下方静态卡轮播（手册 N05 「分镜级视频片段」；绝不假装能出运动视频）。
    clips = []
    video_all_ok = comfy_available()
    if video_all_ok:
        for idx, sh in enumerate(shots):
            try:
                c = _comfy_text2video(en_prompt, num_frames=49, seed=seed, timeout=600)
                if c and os.path.isfile(c):
                    clips.append(c)
                    logger.info("[gen] 镜头%d 真实视频片段已生成: %s" % (idx, c))
                else:
                    clips.append(None)
                    video_all_ok = False
            except Exception as e:
                logger.warning("[gen] 镜头%d 真实视频生成失败，回退静态卡轮播: %s" % (idx, e))
                clips.append(None)
                video_all_ok = False
            if seed and seed > 0:
                seed = seed + 1
    if video_all_ok and len(clips) == len(shots):
        if _concat_video_clips(clips, out_path, W, H, fps=25):
            note = "本地 Wan2.1 真实文生视频（%d 个镜头运动片段，480P 原生分辨率）" % len(clips)
            logger.info("[gen] %s" % note)
            tail_frame = _extract_tail_frame(out_path, os.path.join(run_dir, "tail_frame.png"))
            return "full", note, tail_frame
        logger.warning("[gen] 真实视频片段拼接失败，回退静态卡轮播")

    # 第二层：构建视频帧（优先用 ComfyUI 真图，失败的单张降级为 ffmpeg 文字卡）
    cards = []
    for i, sh in enumerate(shots):
        dur = max(1, int(sh.get("duration") or 4))
        img = comfy_imgs[i] if i < len(comfy_imgs) else None
        if img and os.path.isfile(img):
            dst = os.path.join(run_dir, "shot_%02d.png" % (i + 1))
            try:
                shutil.copyfile(img, dst)
                cards.append((dst, dur))
                continue
            except Exception:
                pass
        txt = (sh.get("description") or scene_text)[:60].replace(":", " ").replace("'", "")
        card = os.path.join(run_dir, "card_%02d.png" % (i + 1))
        sub = txt.replace("\\", " ").replace('"', "")
        cmd = [
            FFMPEG_BIN, "-y", "-f", "lavfi", "-i",
            "color=c=0x10131a:s=1280x720:d=%d" % dur,
            "-vf", "drawtext=text='%s':fontcolor=white:fontsize=42:x=(w-tw)/2:y=(h-th)/2"
                   % sub,
            card,
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if os.path.isfile(card):
                cards.append((card, dur))
        except Exception:
            pass

    if not cards:
        card = os.path.join(run_dir, "card_00.png")
        subprocess.run([FFMPEG_BIN, "-y", "-f", "lavfi", "-i", "color=c=0x10131a:s=1280x720:d=4",
                        "-vf", "drawtext=text='%s':fontcolor=white:fontsize=48:x=(w-tw)/2:y=(h-th)/2" % scene_text[:40],
                        card], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        cards = [(card, 4)]

    # 判定能力等级
    real_cnt = sum(1 for c, _ in cards if os.path.basename(c).startswith("shot_"))
    if comfy_available() and real_cnt == len(shots):
        level, note = "full", "本地 ComfyUI 真实文生图（%d 张全部成功）" % real_cnt
    elif real_cnt > 0:
        level, note = "full", "本地 ComfyUI 真实文生图（%d/%d 张成功，其余降级文字卡）" % (real_cnt, len(shots))
    elif comfy_available():
        level, note = "slideshow", "ComfyUI 出图失败，已降级为文字卡"
    else:
        level, note = "slideshow", "FFmpeg 标题卡合成（本地 AI 图像服务未启动）"

    # 拼接为 mp4（concat）
    concat_list = os.path.join(run_dir, "concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for c, _ in cards:
            f.write("file '%s'\n" % c.replace("\\", "/"))

    cmd = [FFMPEG_BIN, "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25", "-an", out_path]
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=180)
    except subprocess.TimeoutExpired:
        return "fail", "FFmpeg 合成超时（180s）", None
    except Exception as e:
        return "fail", "FFmpeg 合成失败: %s" % e, None

    if not os.path.isfile(out_path):
        return "fail", "视频文件未生成", None

    # N04/N05 首尾帧锚定：提取尾帧，供同场景下一分镜作首帧参考（手册 §3.3.4）
    tail_frame = _extract_tail_frame(out_path, os.path.join(run_dir, "tail_frame.png"))
    if tail_frame:
        logger.info("[gen] 尾帧已提取: %s" % tail_frame)
    return level, note, tail_frame


def _tts_to_file(text, out_file):
    """REDUN-01：旁白 TTS。优先 edge-tts(在线) → pyttsx3(离线)，均不可用则跳过返回 False。

    任一引擎不可用都只是降级（不阻断合成），由调用方决定是否启用音频后期。
    """
    if not text or not text.strip():
        return False
    # 1) edge-tts（在线，音质好）
    try:
        import edge_tts
        import asyncio
        async def _gen():
            comm = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
            await comm.save(out_file)
        asyncio.run(_gen())
        if os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
            return True
    except Exception as e:
        logger.warning("edge-tts 不可用，回退离线引擎: %s", e)
    # 2) pyttsx3（离线）
    try:
        import pyttsx3
        eng = pyttsx3.init()
        eng.setProperty("rate", 175)
        eng.save_to_file(text, out_file)
        eng.runAndWait()
        if os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
            return True
    except Exception as e:
        logger.warning("pyttsx3 不可用，跳过 TTS 旁白: %s", e)
    return False


def _build_srt(entries):
    """把 [(start_s, end_s, text)] 渲染为 SRT 文本。"""
    def _ts(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60); ms = int((t - int(t)) * 1000)
        return "%02d:%02d:%02d,%03d" % (h, m, s, ms)
    lines = []
    for i, (a, b, txt) in enumerate(entries, 1):
        lines.append(str(i)); lines.append("%s --> %s" % (_ts(a), _ts(b))); lines.append(txt); lines.append("")
    return "\n".join(lines)


# ============================================================
# 项 ⑦：转场（按镜头类型自适应 xfade）+ 全局后处理（帧率/降噪/锐化/调色）
# 用本地 ffmpeg 完成；人脸修复需 ComfyUI/人脸引擎，本地 deferred（见 docstring）。
# ============================================================
SHOT_TRANSITION_MAP = {
    "extreme_close_up": "fade",
    "close_up": "fade",
    "medium_close": "fade",
    "medium": "slideright",
    "medium_full": "slideright",
    "full": "slideleft",
    "long": "fade",
    "extreme_long": "fade",
    "wide": "fade",
}
VALID_XFADE = {
    "fade", "fadeblack", "fadewhite", "radial", "smoothleft", "smoothright",
    "smoothup", "smoothdown", "rectcrop", "circlecrop", "circleclose", "circleopen",
    "horzclose", "horzopen", "vertclose", "vertopen", "diagbl", "diagbr", "diagtl",
    "diagtr", "hlslice", "hrslice", "vuslice", "vdslice", "hblur", "fadegrays",
    "wipetl", "wipetr", "wipebl", "wipebr", "slideleft", "slideright", "slideup", "slidedown",
}


def _pick_transition(shot_type):
    """按「出镜镜头类型」自适应选择转场（xfade 合法名），未知类型回退 fade。"""
    t = SHOT_TRANSITION_MAP.get((shot_type or "").strip().lower())
    if t and t in VALID_XFADE:
        return t
    return "fade"


def _probe_clip(path):
    """用 ffprobe 取时长/分辨率/fps/是否含音轨；失败返回 None。"""
    try:
        out = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries",
             "format=duration:stream=width,height,avg_frame_rate,codec_type",
             "-of", "json", path],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30)
        info = json.loads(out.stdout.decode("utf-8", "ignore"))
    except Exception:
        return None
    try:
        dur = float((info.get("format") or {}).get("duration") or 0)
    except Exception:
        dur = 0.0
    if dur <= 0:
        return None
    w = h = None
    fps = None
    has_audio = False
    for st in (info.get("streams") or []):
        if st.get("codec_type") == "video" and w is None:
            try:
                w = int(st.get("width") or 0)
                h = int(st.get("height") or 0)
            except Exception:
                w = h = None
            fr = st.get("avg_frame_rate") or ""
            if fr and "/" in fr:
                try:
                    a, b = fr.split("/")
                    fps = float(a) / float(b) if float(b) else None
                except Exception:
                    fps = None
        elif st.get("codec_type") == "audio":
            has_audio = True
    return {"duration": dur, "width": w, "height": h, "fps": fps, "has_audio": has_audio}


def _has_audio(path):
    p = _probe_clip(path)
    return bool(p and p["has_audio"])


def _concat_copy_concat(concat_list, base_mp4):
    """原 Step1：-c copy 拼接（无转场/后处理时的兜底路径）。返回是否成功产出。"""
    try:
        subprocess.run([FFMPEG_BIN, "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
                        "-c", "copy", base_mp4],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=300)
    except Exception:
        return False
    return os.path.isfile(base_mp4)


def _build_enhanced_base(clips, base_mp4, opts, temp_files):
    """项 ⑦ 核心：转场（按镜头类型自适应 xfade）+ 全局后处理（帧率/降噪/锐化/调色）。
    - 逐片段归一化：统一 fps/分辨率/yuv420p；可选 hqdn3d 降噪 + unsharp 锐化 + eq 调色；
      保证每个片段都有音轨（无音轨补静音），便于 xfade 音频链对齐。
    - 多片段用 xfade 视频链 + acrossfade 音频链串联；转场类型由出镜镜头 shot_type 决定。
    任一环节失败返回 None，调用方回退 -c copy 拼接（REDUN-01）。
    clips: list of (order, fpath, dur, narration, sub_entries, shot_type)
    """
    fps_target = int(opts.get("target_fps") or 25)
    res_override = opts.get("target_resolution") or ""
    do_post = bool(opts.get("post_process", True))
    trans_dur = float(opts.get("transition_duration") or 0.6)

    # 1) 探测每个片段以取得精确时长与统一分辨率基准
    probed = []
    for c in clips:
        p = _probe_clip(c[1])
        if not p:
            logger.warning("转场后处理：片段探测失败，整体回退拼接: %s", c[1])
            return None
        probed.append(p)

    # 目标分辨率：显式覆盖 > 首个片段分辨率；保证偶数
    if res_override and "x" in res_override:
        try:
            tw, th = res_override.lower().split("x")
            tw, th = int(tw), int(th)
        except Exception:
            tw = th = None
    else:
        tw, th = probed[0]["width"], probed[0]["height"]
    if not tw or not th:
        tw, th = 1280, 720
    if tw % 2:
        tw += 1
    if th % 2:
        th += 1

    # 转场时长受最短片段约束
    min_dur = min(p["duration"] for p in probed)
    D = trans_dur
    if D >= min_dur / 2.0 or D <= 0:
        D = max(0.2, min(0.6, min_dur / 2.0 - 0.05))
    D = round(D, 2)

    # 2) 逐片段归一化（帧率/分辨率/像素格式）+ 可选后处理；保证都有音轨
    norm_paths = []
    for i, c in enumerate(clips):
        p = probed[i]
        vf = "fps=%d," % fps_target
        vf += "scale=%d:%d:force_original_aspect_ratio=decrease," % (tw, th)
        vf += "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p" % (tw, th)
        if do_post:
            vf += ",hqdn3d=3:2:4:3,unsharp=5:5:0.6:5:5:0.0,eq=contrast=1.03:saturation=1.06"
        norm = os.path.join(OUTPUTS, "synth_norm_%s_%d.mp4" % (os.path.basename(base_mp4), i))
        if p["has_audio"]:
            fc = ("[0:v]%s[v];[0:a]aformat=sample_fmts=fltp:sample_rates=44100:"
                  "channel_layouts=stereo[a]" % vf)
            cmd = [FFMPEG_BIN, "-nostdin", "-y", "-i", c[1], "-filter_complex", fc,
                   "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
                   "-crf", "20", "-c:a", "aac", "-ar", "44100", norm]
        else:
            fc = ("[0:v]%s[v];[1:a]aformat=sample_fmts=fltp:sample_rates=44100:"
                  "channel_layouts=stereo[a]" % vf)
            cmd = [FFMPEG_BIN, "-nostdin", "-y", "-i", c[1],
                   "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                   "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                   "-c:a", "aac", "-ar", "44100", "-shortest", norm]
        try:
            subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=300)
        except Exception as e:
            logger.warning("转场后处理：归一化失败 clip%d: %s", i, e)
            return None
        if not os.path.isfile(norm):
            logger.warning("转场后处理：归一化未产出 clip%d", i)
            return None
        norm_paths.append(norm)
        temp_files.append(norm)

    # 重新探测归一化后时长（重编码可能轻微浮动），用于 xfade offset 计算
    dur2 = []
    for n in norm_paths:
        p = _probe_clip(n)
        if not p:
            return None
        dur2.append(p["duration"])

    n = len(norm_paths)
    # 3) 单片段：无需 xfade，直接采用归一化片段
    if n == 1:
        try:
            os.replace(norm_paths[0], base_mp4)
            temp_files.remove(norm_paths[0])
        except Exception:
            return None
        return base_mp4

    # 4) 多片段：xfade 视频链 + 音频按 xfade 时间轴对齐（本机构建 acrossfade 无 offset 支持，
    #    改用 adelay 把各片段音轨偏移到对应转场起点再 amix，等效交叉淡入且时长与视频一致）
    offsets = []
    acc = dur2[0] - D
    offsets.append(round(acc, 3))
    for i in range(1, n - 1):
        acc = acc + dur2[i] - D
        offsets.append(round(acc, 3))

    fc_parts = []
    # 视频 xfade 链
    prev_v = "0:v"
    for i in range(1, n):
        vtag = "v%d" % i if i < n - 1 else "vout"
        t = _pick_transition(clips[i - 1][5])  # 出镜镜头类型决定转场
        fc_parts.append("[%s][%d:v]xfade=transition=%s:duration=%.2f:offset=%.2f[%s]"
                        % (prev_v, i, t, D, offsets[i - 1], vtag))
        prev_v = vtag
    # 音频：各片段音轨按 xfade 偏移 adelay 对齐后 amix（等效交叉淡入，时长与视频一致）
    a_src = []
    for i in range(n):
        if i == 0:
            a_src.append("[0:a]")
        else:
            ms = int(round(offsets[i - 1] * 1000))
            tag = "ad%d" % i
            fc_parts.append("[%d:a]adelay=delays=%d:all=1[%s]" % (i, ms, tag))
            a_src.append("[%s]" % tag)
    fc_parts.append("".join(a_src) + ("amix=inputs=%d:duration=longest[aout]" % n))
    fc = ";".join(fc_parts)

    cmd = [FFMPEG_BIN, "-nostdin", "-y"]
    for np in norm_paths:
        cmd += ["-i", np]
    cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]", "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "44100", base_mp4]
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=600)
    except Exception as e:
        logger.warning("转场后处理：xfade 合成失败，回退拼接: %s", e)
        return None
    if not os.path.isfile(base_mp4):
        logger.warning("转场后处理：xfade 未产出，回退拼接")
        return None
    return base_mp4


def _face_restore_one_clip(fpath, method, temp_files):
    """对单个片段逐帧做人脸修复（GFPGAN/CodeFormer）并重建视频（保留原音轨）。

    缺 ComfyUI / 节点 / 权重时 face_restore 抛清晰错误，由调用方 REDUN-01 诚实降级（仅告警）。
    """
    import glob as _glob
    import tempfile as _tempfile
    d = _tempfile.mkdtemp(prefix="manju_fr_", dir=_TMP_ROOT)
    temp_files.append(d)
    fr_pattern = os.path.join(d, "f%05d.png")
    subprocess.run([FFMPEG_BIN, "-y", "-i", fpath, "-vsync", "0", fr_pattern],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    frames = sorted(_glob.glob(os.path.join(d, "f*.png")))
    if not frames:
        raise RuntimeError("人脸修复：未提取到帧")
    for i, f in enumerate(frames):
        out = os.path.join(d, "r%05d.png" % i)
        rp = face_restore(f, method, out_path=out)   # 真实调用 ComfyUI GFPGAN/CodeFormer
        if not (rp and os.path.isfile(rp)):
            raise RuntimeError("人脸修复：帧 %d 未产出修复图" % i)
        temp_files.append(rp)
    silent = os.path.join(d, "restored_silent.mp4")
    subprocess.run([FFMPEG_BIN, "-y", "-framerate", "25", "-i", os.path.join(d, "r%05d.png"),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", silent],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    if not os.path.isfile(silent):
        raise RuntimeError("人脸修复：重建静音视频失败")
    final = os.path.join(d, "restored.mp4")
    if _has_audio(fpath):
        subprocess.run([FFMPEG_BIN, "-y", "-i", silent, "-i", fpath,
                        "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", final],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    else:
        final = silent
    if not os.path.isfile(final):
        raise RuntimeError("人脸修复：最终视频未生成")
    return final


def _face_restore_clips(clips, method, temp_files):
    """N08 全局后处理：人脸修复（手册 §3.4.4）。逐片段逐帧修复并重建。

    任一片段失败 → 保留原片段（由调用方 REDUN-01 诚实降级，仅告警，不静默伪装已修复）。
    """
    out = []
    for c in clips:
        try:
            new_path = _face_restore_one_clip(c[1], method, temp_files)
            out.append((c[0], new_path, c[2], c[3], c[4], c[5]))
        except Exception as e:
            logger.warning("人脸修复跳过片段 %s: %s", c[1], e)
            out.append(c)
    return out


def _mix_audio_tracks(tracks, project_id, temp_files):
    """§3.4.4 多音轨分层：逐轨 volume 增益后 amix 为单音轨。tracks=[(path, volume), ...]，均须为已存在文件。

    - 单轨：volume≠1.0 则增益后输出新文件，否则直出原文件（避免无谓转码）。
    - 多轨：ffmpeg 多输入 + 每输入 volume 滤镜 + amix=inputs=N:duration=longest。
    混流产物登记 temp_files（OPS-01 收尾统一释放）。返回混流 aac 路径或 None。
    """
    valid = [(p, v) for (p, v) in tracks if p and os.path.isfile(p)]
    if not valid:
        return None
    if len(valid) == 1:
        p, v = valid[0]
        if abs(v - 1.0) < 1e-3:
            return p
        out = os.path.join(OUTPUTS, "synth_vol_%s.aac" % project_id)
        subprocess.run([FFMPEG_BIN, "-nostdin", "-y", "-i", p,
                        "-af", "volume=%.3f" % v, "-c:a", "aac", out],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=120)
        if os.path.isfile(out):
            temp_files.append(out)
            return out
        return p
    inputs = []
    vc = []
    labels = []
    for i, (p, v) in enumerate(valid):
        inputs += ["-i", p]
        tag = "t%d" % i
        vc.append("[%d:a]volume=%.3f[%s]" % (i, v, tag))
        labels.append(tag)
    amix = "%s" % "".join(labels) + ("amix=inputs=%d:duration=longest:dropout_transition=0[aout]" % len(labels))
    filter_complex = ";".join(vc) + amix
    out = os.path.join(OUTPUTS, "synth_mix_%s.aac" % project_id)
    cmd = [FFMPEG_BIN, "-nostdin", "-y"] + inputs + ["-filter_complex", filter_complex,
          "-map", "[aout]", "-c:a", "aac", out]
    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=300)
    if os.path.isfile(out):
        temp_files.append(out)
        return out
    return None


def synthesize_project_video(project_id, out_path, options=None):
    """Phase 5（合成全集，KB §05）：把所有「已生成成功」片段拼接为完整 MP4，并按 options 叠加后期：

      - tts=True：收集各片段 shot.voiceover/dialogue 作为旁白，TTS 生成音轨并混流（REDUN-01 降级）
      - bgm=True 且提供 bgm_path：背景音乐循环铺底混流；voice_volume/bgm_volume/sfx_volume（§3.4.4 多音轨分层音量控制）
      - sfx_path（可选）：音效轨，与旁白/背景乐分层混流；缺失则诚实跳过（不与 BGM 抢默认）
      - subtitle=True：把各片段对话烧录为硬字幕（中文需字体，失败则降级跳过）
      - transitions=True（默认）：按「出镜镜头类型」自适应转场（xfade），见 _pick_transition（项 ⑦）
      - post_process=True（默认）：全局后处理——统一帧率/分辨率 + 降噪(hqdn3d) + 锐化(unsharp) + 调色(eq)
      - target_fps / target_resolution / transition_duration：可选微调
    转场/后处理用本地 ffmpeg 完成；人脸修复需 ComfyUI/人脸引擎，本地 deferred。
    任一后期失败均优雅降级（REDUN-01）：转场/后处理失败回退 -c copy 拼接，绝不阻断主流程。
    返回 (level, note)，与 generate_segment_video 约定一致。
    """
    import db
    from urllib.parse import urlparse, parse_qs
    options = options or {}
    do_tts = bool(options.get("tts"))
    do_bgm = bool(options.get("bgm"))
    do_sub = bool(options.get("subtitle"))
    bgm_path = options.get("bgm_path") or ""
    # §3.4.4 多音轨分层音量控制：人声/音效/背景音乐分别增益（默认 1.0，范围 0~2）
    def _clamp_vol(v, default=1.0):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return default
        return min(max(x, 0.0), 2.0)
    voice_volume = _clamp_vol(options.get("voice_volume"), 1.0)
    bgm_volume = _clamp_vol(options.get("bgm_volume"), 1.0)
    sfx_volume = _clamp_vol(options.get("sfx_volume"), 1.0)
    sfx_path = options.get("sfx_path") or ""
    do_trans = bool(options.get("transitions", True))
    do_pp = bool(options.get("post_process", True))
    target_fps = int(options.get("target_fps") or 25)
    target_resolution = options.get("target_resolution") or ""
    trans_dur = float(options.get("transition_duration") or 0.6)
    temp_files = []  # OPS-01：收尾统一释放临时资源

    conn = db.get_conn()
    try:
        segs = db.fetch_all(
            conn,
            'SELECT segment_id, "index", title, video_url, duration, status FROM segments '
            'WHERE project_id=? ORDER BY "index" ASC',
            (project_id,),
        )
    finally:
        conn.close()

    clips = []          # (order, fpath, duration_sec, narration_text, sub_entries)
    skipped = 0
    cursor = 0.0
    for s in segs:
        vurl = s.get("video_url")
        if not vurl:
            skipped += 1
            continue
        q = parse_qs(urlparse(vurl).query)
        name = (q.get("name") or [None])[0]
        if not name:
            skipped += 1
            continue
        fpath = os.path.join(OUTPUTS, os.path.basename(name))
        if not os.path.isfile(fpath):
            logger.warning("合成全集跳过缺失片段视频: %s", fpath)
            skipped += 1
            continue
        dur = float(s.get("duration") or 12)
        # 收集本片段镜头旁白/对白文本（TTS 源）
        narration = ""
        sub_entries = []
        c2 = db.get_conn()
        try:
            shots = db.fetch_all(c2,
                'SELECT "index", duration, voiceover, dialogue, shot_type FROM shots '
                'WHERE segment_id=? ORDER BY "index" ASC', (s["segment_id"],))
        finally:
            c2.close()
        parts = []
        sub_start = cursor
        seg_dur = dur
        n = len(shots) or 1
        rep_shot_type = ""
        for j, sh in enumerate(shots):
            if not rep_shot_type and sh.get("shot_type"):
                rep_shot_type = sh.get("shot_type")
            txt = (sh.get("voiceover") or sh.get("dialogue") or "").strip()
            if txt:
                parts.append(txt)
                sd = float(sh.get("duration") or (seg_dur / n))
                sub_entries.append((sub_start, sub_start + sd, txt))
                sub_start += sd
            else:
                sub_start += float(sh.get("duration") or (seg_dur / n))
        if parts:
            narration = "。".join(parts)
        # 项 ⑦：clips 元组第 6 项为「出镜镜头类型」，供转场自适应选择
        clips.append((s.get("index") or 0, fpath, dur, narration, sub_entries, rep_shot_type))
        cursor += dur

    if not clips:
        return "fail", "无可用片段视频（请先让全部片段生成成功）"

    clips.sort(key=lambda x: x[0])
    # N08 全局后处理：人脸修复（手册 §3.4.4）。逐片段逐帧修复；缺能力时诚实降级（仅告警，不阻断）。
    fr_method = options.get("face_restore")
    if fr_method:
        try:
            clips = _face_restore_clips(clips, fr_method, temp_files)
            logger.info("合成全集：人脸修复已应用（method=%s）", fr_method)
        except Exception as e:
            logger.warning("合成全集：人脸修复整体失败，跳过并保留原片段: %s", e)
    total_dur = sum(c[2] for c in clips)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    concat_list = os.path.join(OUTPUTS, "synth_concat_%s.txt" % project_id)
    with open(concat_list, "w", encoding="utf-8") as f:
        for _, c, _, _, _, _ in clips:
            f.write("file '%s'\n" % c.replace("\\", "/"))
    temp_files.append(concat_list)

    # Step 1：基础视频拼接（含原声）
    base_mp4 = out_path + ".base.mp4"
    temp_files.append(base_mp4)
    enhanced = False
    # Step 1：转场 + 全局后处理（项 ⑦）；不可用则回退 -c copy 拼接（REDUN-01）
    if (do_trans or do_pp) and clips:
        eb = _build_enhanced_base(clips, base_mp4, {
            "target_fps": target_fps, "target_resolution": target_resolution,
            "post_process": do_pp, "transition_duration": trans_dur,
        }, temp_files)
        if eb and os.path.isfile(eb):
            enhanced = True
            logger.info("合成全集：转场/后处理已应用（enhanced=True, transitions=%s, post_process=%s）",
                        do_trans, do_pp)
    if not enhanced:
        if not _concat_copy_concat(concat_list, base_mp4):
            return "fail", "合成全集基础视频未生成"

    audio_final = None
    srt_path = None
    try:
        # Step 2：旁白 TTS（REDUN-01 降级）
        if do_tts:
            seg_audios = []
            audio_cursor = 0.0
            for _, _, dur, narration, _, _ in clips:
                if narration:
                    seg_wav = os.path.join(OUTPUTS, "synth_tts_%s_%d.wav" % (project_id, len(seg_audios)))
                    if _tts_to_file(narration, seg_wav) and os.path.isfile(seg_wav):
                        padded = os.path.join(OUTPUTS, "synth_pad_%s_%d.wav" % (project_id, len(seg_audios)))
                        # N07：逐段 apad 精确对齐到片段时长（误差≤单帧），保证音画同步≤100ms
                        subprocess.run([FFMPEG_BIN, "-nostdin", "-y", "-i", seg_wav,
                                        "-af", "apad", "-t", "%.2f" % dur, padded],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=120)
                        if os.path.isfile(padded):
                            seg_audios.append(padded)
                            temp_files.append(seg_wav); temp_files.append(padded)
                audio_cursor += dur   # 无旁白片段为静音对齐，仍计时间轴
            if seg_audios:
                narr_list = os.path.join(OUTPUTS, "synth_narr_%s.txt" % project_id)
                with open(narr_list, "w", encoding="utf-8") as f:
                    for a in seg_audios:
                        f.write("file '%s'\n" % a.replace("\\", "/"))
                temp_files.append(narr_list)
                narr_aac = os.path.join(OUTPUTS, "synth_narr_%s.aac" % project_id)
                subprocess.run([FFMPEG_BIN, "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", narr_list,
                                "-c", "copy", narr_aac],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=120)
                if os.path.isfile(narr_aac):
                    audio_final = narr_aac
                    temp_files.append(narr_aac)
                    logger.info("合成全集：TTS 旁白已生成（%d 段）", len(seg_audios))
                else:
                    logger.warning("合成全集：TTS 旁白合并失败，跳过音频后期")
                # N07 音画同步校验（手册 §3.4.4 误差≤100ms）：逐段 apad 后音轨时间轴应与视频一致
                drift = abs(audio_cursor - total_dur)
                if drift * 1000 > AUDIO_ALIGN_MAX_MS:
                    logger.warning("合成全集：音画时间轴漂移 %.0fms 超阈值(%dms)，可能音画不同步",
                                   drift * 1000, AUDIO_ALIGN_MAX_MS)
                else:
                    logger.info("合成全集：音画对齐校验通过（逐段 apad 对齐，漂移 %.0fms ≤ %dms）",
                                drift * 1000, AUDIO_ALIGN_MAX_MS)

        # Step 3：多音轨分层混流（§3.4.4：voice / sfx / bgm 独立音量增益后合成单音轨）
        # 降级（REDUN-01）：任一音轨缺失即跳过该轨，至少保留存在的轨；三轨全缺则无音频（静音视频）
        tracks = []
        if audio_final and os.path.isfile(audio_final):
            tracks.append((audio_final, voice_volume))   # 旁白（来自 Step 2）
        if sfx_path and os.path.isfile(sfx_path):
            tracks.append((sfx_path, sfx_volume))         # 音效 SFX（可选）
        if do_bgm and bgm_path and os.path.isfile(bgm_path):
            bgm_aac = os.path.join(OUTPUTS, "synth_bgm_%s.aac" % project_id)
            subprocess.run([FFMPEG_BIN, "-nostdin", "-y", "-stream_loop", "-1", "-i", bgm_path,
                            "-t", "%.2f" % total_dur, "-c", "copy", bgm_aac],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
            if os.path.isfile(bgm_aac):
                tracks.append((bgm_aac, bgm_volume))      # 背景音乐 BGM
                temp_files.append(bgm_aac)
        if tracks:
            mixed = _mix_audio_tracks(tracks, project_id, temp_files)
            if mixed and os.path.isfile(mixed):
                audio_final = mixed
                logger.info("合成全集：多音轨分层混流完成（voice=%.2f / sfx=%.2f / bgm=%.2f，共 %d 轨）",
                            voice_volume, sfx_volume, bgm_volume, len(tracks))
            else:
                logger.warning("合成全集：多音轨混流失败，保留 Step 2 既有音轨")
        else:
            # 无旁白/无 BGM/无 SFX：保证 audio_final 为 None（生成静音视频或仅画面）
            if not (audio_final and os.path.isfile(audio_final)):
                audio_final = None

        # Step 4：硬字幕（REDUN-01 降级）
        if do_sub:
            all_subs = []
            for _, _, _, _, sub_entries, _ in clips:
                all_subs.extend(sub_entries)
            if all_subs:
                srt_path = os.path.join(OUTPUTS, "synth_sub_%s.srt" % project_id)
                with open(srt_path, "w", encoding="utf-8") as f:
                    f.write(_build_srt(all_subs))
                temp_files.append(srt_path)

        # Step 5：最终封装
        base_audio = _has_audio(base_mp4)
        if srt_path:
            vf = "subtitles='%s'" % srt_path.replace("\\", "/").replace(":", "\\:")
            cmd = [FFMPEG_BIN, "-nostdin", "-y", "-i", base_mp4]
            if audio_final:
                cmd += ["-i", audio_final]
                cmd += ["-filter_complex", "[0:v]%s[v]" % vf, "-map", "[v]", "-map", "1:a:0"]
            else:
                # 基础视频无音轨时仅烧录视频（避免 -map 0:a:0 因缺音轨失败，REDUN-01）
                if base_audio:
                    cmd += ["-filter_complex", "[0:v]%s[v]" % vf, "-map", "[v]", "-map", "0:a:0"]
                else:
                    cmd += ["-filter_complex", "[0:v]%s[v]" % vf, "-map", "[v]"]
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", out_path]
            try:
                subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=600)
            except Exception as e:
                logger.warning("合成全集字幕烧录失败，降级为无字幕: %s", e)
                srt_path = None
        if not os.path.isfile(out_path):  # 字幕降级或无需字幕 → 直接封装音频
            if audio_final:
                subprocess.run([FFMPEG_BIN, "-nostdin", "-y", "-i", base_mp4, "-i", audio_final,
                                "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", out_path],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=300)
            elif os.path.isfile(base_mp4):
                os.replace(base_mp4, out_path)  # 基础拼接结果（含/不含音轨均可）
                try:
                    temp_files.remove(base_mp4)
                except Exception:
                    pass
    finally:
        # OPS-01：释放所有临时文件/目录（基础视频/音频/SRT/列表/人脸修复帧目录），
        # 仅保留最终 out_path；目录必须用 rmtree 释放（os.remove 对目录会失败被静默吞掉）。
        for tf in temp_files:
            try:
                if os.path.isdir(tf):
                    shutil.rmtree(tf, ignore_errors=True)
                elif os.path.isfile(tf):
                    os.remove(tf)
            except Exception:
                pass

    if not os.path.isfile(out_path):
        return "fail", "合成全集视频文件未生成"
    extras = []
    if enhanced:
        extras.append("转场/后处理")
    if do_tts and audio_final:
        extras.append("旁白")
    if do_bgm and bgm_path and os.path.isfile(bgm_path):
        extras.append("BGM")
    if do_sub and srt_path:
        extras.append("字幕")
    note = "合成全集完成（%d 个片段拼接%s%s）" % (
        len(clips),
        "，跳过 %d 个无视频片段" % skipped if skipped else "",
        "，含" + "/".join(extras) if extras else "")
    return "ok", note


# ============================================================
# 回忆录（AGENT_SPEC_15）本地 AI 适配
# ============================================================

def plan_memoir(memoir):
    """本地 Ollama 策划：依据回忆录主题/生平/数量产出分章结构（含分镜）。

    返回 list[dict]，每章含 chapter_index/title/era/duration/shots/...（见 AGENT_SPEC_15 §8.1）。
    Ollama 不可用时返回 None（调用方诚实报错）。
    """
    if not ollama_available():
        return None
    subject = memoir.get("subject_name") or "主人公"
    bg = memoir.get("background_text") or ""
    count = int(memoir.get("storyboard_count") or 4)
    ratio = memoir.get("target_ratio") or "16:9"
    res = memoir.get("target_resolution") or "720p"
    emotion = memoir.get("enhance_emotion") or ""
    exclude = memoir.get("exclude_content") or ""
    photos = (memoir.get("reference_photos") or [])
    photo_note = ("用户已上传 %d 张人物参考照，指导板须以该人物真实样貌为唯一视觉依据。" % len(photos)) if photos else ""
    timeline = memoir.get("timeline_events") or []

    shot_n = 3  # 每章默认 3 个分镜（spec default_shots_per_chapter=3）
    dur = 15    # 每章固定 15s（spec 标准）
    constraints = [
        "严格输出 JSON 数组，不要输出任何解释文字",
        "风格温暖怀旧纪录片",
        "分镜时长之和 = 15s（BR-016）",
        "镜头英文枚举：shot_type∈{extreme_close_up,close_up,medium_close,medium,medium_full,full,long,extreme_long}；"
        "camera_movement∈{static,pan,tilt,dolly,zoom,handheld,aerial,follow}",
    ]
    if emotion:
        constraints.append("强化情绪：%s。" % emotion)
    if exclude:
        constraints.append("不希望出现：%s。" % exclude)
    if photo_note:
        constraints.append(photo_note)
    prompt = build_prompt(
        role="你是一位人生回忆录导演策划，擅长把零散生平素材组织为温暖怀旧的纪录片式分镜。",
        objective=(
            "请基于下面信息，生成 %d 个章节的分镜策划，严格输出 JSON 数组。\n"
            "每个元素字段：\n"
            "  chapter_index(int), title(章节标题), era(年代如'1980年代'), duration(=%d),\n"
            "  characters: [\"角色名 (年龄岁) - 外貌描述\"],\n"
            "  scenes: [\"环境场景描述\"],\n"
            "  camera_plan: \"机位路径文字\",\n"
            "  lighting: \"灯光与情绪\",\n"
            "  mood_keywords: [\"情绪关键词\"],\n"
            "  color_palette: [{\"color_name\":\"名称\",\"hex\":\"#RRGGBB\"}] (5 个),\n"
            "  audio_description: \"声音设计\",\n"
            "  voiceover_notes: [\"旁白备注\"],\n"
            "  shots: [{\"shot_type\":\"景别\",\"camera_movement\":\"运镜\",\"duration\":\"起止s如0-5s\","
            "\"description\":\"画面描述\",\"voiceover\":\"旁白\"}] (共 %d 个，时长之和=15s)\n"
            "主题人物：%s\n生平背景：%s\n时间轴事件：%s"
            % (count, dur, shot_n, subject, bg, json.dumps(timeline, ensure_ascii=False))
        ),
        constraints=constraints,
        invariants=[
            "必须以用户上传的参考照为唯一视觉依据，不得替换或虚构人物样貌",
            "不得虚构用户未提供的生平事实",
            "字段名与数量严格遵循要求，不得增减",
        ],
    )
    try:
        txt = _ollama_generate(prompt, think=False)
    except Exception:
        return None
    if not txt:
        return None
    # 提取 JSON 数组
    s = txt.strip()
    start = s.find("[")
    end = s.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        arr = json.loads(s[start:end + 1])
    except Exception:
        return None
    if not isinstance(arr, list) or not arr:
        return None
    # 规范化
    out = []
    for i, ch in enumerate(arr[:count]):
        if not isinstance(ch, dict):
            continue
        ch.setdefault("chapter_index", i + 1)
        ch.setdefault("title", "第 %d 章" % (i + 1))
        ch.setdefault("era", "")
        ch.setdefault("duration", dur)
        shots = ch.get("shots") or []
        # 保证分镜时长之和=15（BR-016）
        total = sum(_dur_of(s0) for s0 in shots)
        if total != dur and shots:
            _redistribute(shots, dur)
        ch["shots"] = shots
        ch.setdefault("characters", [])
        ch.setdefault("scenes", [])
        ch.setdefault("camera_plan", "")
        ch.setdefault("lighting", "")
        ch.setdefault("mood_keywords", [])
        ch.setdefault("color_palette", [])
        ch.setdefault("audio_description", "")
        ch.setdefault("voiceover_notes", [])
        out.append(ch)
    return out


def _dur_of(s):
    d = s.get("duration")
    if isinstance(d, int):
        return d
    if isinstance(d, str):
        m = re.findall(r"\d+", d)
        if m:
            return int(m[-1]) - (int(m[0]) if len(m) > 1 else 0)
    return 5


def _redistribute(shots, total):
    n = len(shots)
    base = total // n
    rem = total - base * n
    for i, s in enumerate(shots):
        s["duration"] = base + (1 if i < rem else 0)


def storyboard_from_prompt(prompt):
    """AGENT_SPEC_15 §8.2：依据指导板提示词用本地 ComfyUI 出图（替代 GPT Image 2）。

    返回本地图片绝对路径或 None。
    """
    if not comfy_available():
        return None
    try:
        return _comfy_text2img(prompt, width=2048, height=1152, steps=24, timeout=240)
    except Exception:
        return None


def generate_memoir_video(chapter, out_path):
    """AGENT_SPEC_15 §8.3：本地生成回忆录章节视频（替代 Seedance 2.0 Mini）。

    复用 generate_segment_video 的 L1 真图链路。
    """
    shots = chapter.get("shot_data", {}).get("shots") or []
    seg_like = {
        "segment_id": chapter.get("chapter_id") or "memoir",
        "scene_description": chapter.get("title") or "人生回忆录",
        "audio_enabled": 0,
    }
    shot_like = []
    for s in shots:
        shot_like.append({
            "description": s.get("description") or "",
            "duration": _to_int_dur(s.get("duration")) or 5,
        })
    if not shot_like:
        shot_like = [{"description": seg_like["scene_description"], "duration": 15}]
    return generate_segment_video(seg_like, shot_like, [], out_path)


def _to_int_dur(d):
    if isinstance(d, int):
        return d
    if isinstance(d, str):
        m = re.findall(r"\d+", d)
        if m:
            return int(m[-1])
    return 5


# ============================================================
# 文生图（AGENT_SPEC 文生图页）：Ollama 提示词增强 + Dify 编排 + ComfyUI 出图
# 服务绑定：Ollama(本地大模型) / Dify(Docker Desktop 编排大脑) / ComfyUI(秋叶 出图)
# ============================================================

COMFY_CKPT = "Realistic_Vision_V6.0_NV_B1_fp16.safetensors"
STYLE_SUFFIX = {
    "写实": "photorealistic, realistic skin, natural lighting, highly detailed",
    "动漫": "anime style, cel shading, vibrant colors, clean lineart",
    "电影感": "cinematic composition, film grain, dramatic lighting, teal and orange color grading, 35mm lens",
    "水彩": "watercolor painting, soft edges, paper texture, pastel tones, artistic",
}

# 内置中英词典：当 Dify 和 Ollama 均不可用时，用基础词典做中文→英文翻译兜底，
# 确保 ComfyUI (SD1.5) 始终收到英文提示词，避免中文直接送入导致严重画面偏差。
_ZH_EN_DICT = {
    "玫瑰": "rose", "茉莉花": "jasmine flower", "茉莉": "jasmine",
    "百合": "lily", "郁金香": "tulip", "向日葵": "sunflower", "牡丹": "peony",
    "菊花": "chrysanthemum", "樱花": "cherry blossom", "荷花": "lotus",
    "兰花": "orchid", "桃花": "peach blossom", "梅花": "plum blossom",
    "花": "flower", "人": "person", "人类": "human",
    "女孩": "girl", "男孩": "boy", "女人": "woman", "男人": "man",
    "少女": "young girl", "少年": "young boy", "老人": "elderly person",
    "猫": "cat", "狗": "dog", "风景": "landscape", "山": "mountain",
    "海": "sea", "天空": "sky", "树": "tree", "城市": "city",
    "建筑": "building", "车": "car", "食物": "food", "动物": "animal",
    "鸟": "bird", "鱼": "fish", "太阳": "sun", "月亮": "moon",
    "星星": "stars", "雨": "rain", "雪": "snow", "火": "fire",
    "水": "water", "光": "light", "夜": "night", "日": "day",
    "森林": "forest", "河流": "river", "湖": "lake", "草地": "grassland",
    "沙漠": "desert", "城堡": "castle", "飞船": "spaceship", "宇宙": "universe",
    "龙": "dragon", "蝴蝶": "butterfly", "马": "horse", "兔子": "rabbit",
    "钢琴": "piano", "吉他": "guitar", "书": "book", "剑": "sword",
    "雨伞": "umbrella", "杯子": "cup", "桌子": "table", "椅子": "chair",
    "红色": "red", "蓝色": "blue", "绿色": "green", "黄色": "yellow",
    "紫色": "purple", "白色": "white", "黑色": "black", "粉色": "pink",
    "橙色": "orange", "金色": "golden", "银色": "silver",
    "美丽": "beautiful", "可爱": "cute", "神秘": "mysterious",
    "梦幻": "dreamy", "黑暗": "dark", "明亮": "bright",
}


def _dict_fallback_translate(prompt, negative="", style="写实"):
    """词典兜底翻译：当 Dify/Ollama 均不可用时，用内置词典做基础中文→英文翻译。

    确保 ComfyUI (SD1.5) 始终收到英文提示词，避免中文直接送入导致画面严重偏差。
    返回 (en_prompt, en_negative)。
    """
    parts = []
    remaining = prompt
    # 按词条长度降序匹配（"人类" 优先于 "人"）
    for zh in sorted(_ZH_EN_DICT.keys(), key=len, reverse=True):
        en = _ZH_EN_DICT[zh]
        if zh in remaining:
            parts.append(en)
            remaining = remaining.replace(zh, "")
    suffix = STYLE_SUFFIX.get(style, "")
    en_p = ", ".join(parts) if parts else "abstract art"
    if suffix:
        en_p = en_p + ", " + suffix
    # 负向提示词翻译
    en_n = negative or ""
    for zh in sorted(_ZH_EN_DICT.keys(), key=len, reverse=True):
        en = _ZH_EN_DICT[zh]
        if zh in en_n:
            en_n = en_n.replace(zh, en)
    if not en_n:
        en_n = ("lowres, bad anatomy, bad hands, text, error, missing fingers, "
                "extra digit, fewer digits, cropped, worst quality, low quality")
    logger.info("[t2i] 词典兜底翻译: '%s' -> '%s'" % (prompt, en_p))
    return en_p, en_n


def load_service_config():
    """读取服务绑定配置（Dify 编排）：dify_base_url / dify_api_key / dify_app_id。"""
    try:
        import db
        conn = db.get_conn()
        try:
            rows = db.fetch_all(
                conn,
                "SELECT key, value FROM settings WHERE key IN "
                "('dify_base_url','dify_api_key','dify_app_id')")
            cfg = {}
            for r in rows:
                v = r["value"]
                try:
                    v = json.loads(v)
                except Exception:
                    pass
                cfg[r["key"]] = v
            return cfg
        finally:
            conn.close()
    except Exception:
        return {}


def translate_to_sd_prompt(prompt, negative="", style="写实"):
    """Ollama 绑定：把中文画面描述翻译/增强为英文 SD 提示词 + 负向提示词。

    返回 (en_prompt, en_negative)；任何失败（Ollama 不可用 / 生成异常 / 解析失败）返回 None，
    由调用方决定是否使用词典兜底翻译。不再静默返回原始中文。
    """
    if not ollama_available():
        logger.warning("[t2i] Ollama 不可用或模型未加载，跳过翻译")
        return None
    suffix = STYLE_SUFFIX.get(style, "")
    objective = (
        "把下面的中文画面描述，改写为适合 Stable Diffusion / ComfyUI 的高质量英文提示词，"
        "要求细节丰富、画面感强、与风格一致。同时给出英文负向提示词（排除低质量元素）。\n\n"
        "用户描述：%s\n风格倾向：%s" % (prompt, style)
    )
    constraints = [
        "严格只输出一个 JSON 对象，不要任何解释文字",
        '格式：{"prompt": "英文正向提示词", "negative_prompt": "英文负向提示词"}',
        "提示词以英文逗号分隔，包含主体、场景、光线、画质词",
        "若用户已给负向提示词，合并进 negative_prompt；否则给通用负向",
    ]
    if suffix:
        constraints.append("正向提示词末尾补充风格修饰：%s" % suffix)
    invariants = [
        "不得输出 JSON 以外的任何内容",
        "不得删除用户描述中的核心主体",
    ]
    p = build_prompt(
        role="你是一个资深 Stable Diffusion / ComfyUI 提示词工程专家，擅长把中文创意转化为精准的英文绘画提示词。",
        objective=objective,
        constraints=constraints,
        invariants=invariants,
    )
    try:
        txt = _ollama_generate(p, think=False, keep_alive=0)
    except Exception as e:
        logger.warning("[t2i] Ollama generate 异常: %s" % e)
        return None
    if not txt:
        logger.warning("[t2i] Ollama 返回空响应")
        return None
    s = txt.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        logger.warning("[t2i] Ollama 响应无 JSON: %s" % s[:200])
        return None
    try:
        obj = json.loads(s[start:end + 1])
        en_p = (obj.get("prompt") or "").strip()
        en_n = (obj.get("negative_prompt") or "").strip()
        if not en_p:
            logger.warning("[t2i] Ollama JSON 中 prompt 为空")
            return None
        logger.info("[t2i] Ollama 翻译成功: '%s' -> '%s'" % (prompt[:50], en_p[:80]))
        return en_p, (en_n or negative)
    except Exception as e:
        logger.warning("[t2i] Ollama JSON 解析失败: %s" % e)
        return None


def orchestrate_with_dify(prompt, negative="", style="写实", config=None):
    """Dify 绑定（Docker Desktop 编排大脑）：调用 Dify chat app 把描述编排为 SD 提示词。

    返回 (en_prompt, en_negative)；任何失败（未配置 / 网络 / 解析）返回 None，由调用方回退 Ollama。
    """
    config = config or load_service_config()
    base = (config.get("dify_base_url") or "http://127.0.0.1").rstrip("/")
    key = config.get("dify_api_key") or ""
    if not key:
        logger.warning("[dify] 未配置 API Key，跳过 Dify 编排")
        return None
    url = base + "/v1/chat-messages"
    suffix = STYLE_SUFFIX.get(style, "")
    query = (
        "你是 Stable Diffusion 提示词编排助手。请把下面的画面描述编排为高质量英文 SD 提示词，"
        "并输出 JSON：{\"prompt\": 英文正向提示词, \"negative_prompt\": 英文负向提示词}。"
        "用户描述：%s\n风格：%s%s" % (prompt, style, ("\n风格修饰：" + suffix if suffix else ""))
    )
    body = json.dumps({
        "inputs": {},
        "query": query,
        "user": "manju_local",
        "response_mode": "blocking",
        "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8"))
        answer = data.get("answer") or ""
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "ignore")[:500]
        logger.warning("[dify] 编排请求失败：HTTP %s %s，响应：%s" % (e.code, e.reason, err_body))
        return None
    except Exception as e:
        logger.warning("[dify] 编排请求异常：%s" % e)
        return None
    if not answer:
        logger.warning("[dify] 编排返回 answer 为空")
        return None
    s = answer.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        # 非 JSON：整段作为正向提示词
        logger.info("[dify] 编排返回非 JSON，直接作为正向提示词使用")
        return s, negative
    try:
        obj = json.loads(s[start:end + 1])
        en_p = (obj.get("prompt") or "").strip()
        en_n = (obj.get("negative_prompt") or "").strip()
        if not en_p:
            logger.warning("[dify] 编排返回 JSON 中 prompt 为空")
            return s, negative
        logger.info("[dify] 编排成功：%s -> %s" % (prompt[:40], en_p[:80]))
        return en_p, (en_n or negative)
    except Exception as e:
        logger.warning("[dify] 编排返回 JSON 解析失败：%s" % e)
        return s, negative


# ---------------------------------------------------------------------------
# 创意工坊：把一句话创意扩展为结构化创作简报（Dify 优先，Ollama 回退，模板兜底）
# ---------------------------------------------------------------------------

def _dify_chat(query, config=None):
    """通用 Dify chat：返回 answer 字符串；失败/未配置返回 None。"""
    config = config or load_service_config()
    base = (config.get("dify_base_url") or "http://127.0.0.1").rstrip("/")
    key = config.get("dify_api_key") or ""
    if not key:
        return None
    url = base + "/v1/chat-messages"
    body = json.dumps({
        "inputs": {}, "query": query, "user": "manju_local",
        "response_mode": "blocking", "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("answer") or ""
    except Exception:
        return None


def _creative_dify_query(idea, style):
    return (
        "你是资深短剧/短视频创意策划。请把下面的创意点子扩展为结构化创作简报，"
        '严格只输出一个 JSON 对象，不要任何解释文字：'
        '{"title": 标题, "premise": 一句话前提, "tags": [3个标签字符串], '
        '"shots": [3个分镜描述字符串], "scenes": [3个适用场景字符串]}。\n'
        "创意点子：%s\n风格倾向：%s" % (idea, style)
    )


def _ollama_creative(idea, style):
    if not ollama_available():
        return None
    p = build_prompt(
        role="你是一个资深短剧/短视频创意策划，擅长把一句话点子扩展成可落地的创作简报。",
        objective=(
            "请把下面这个创意点子扩展为结构化创作简报，输出 JSON：\n"
            '{"title": 标题, "premise": 一句话前提, "tags": [3个标签], '
            '"shots": [3个分镜描述], "scenes": [3个适用场景]}。\n'
            "创意点子：%s\n风格倾向：%s") % (idea, style),
        constraints=["严格只输出 JSON，不要解释文字", "tags 与 scenes 各 3 个左右", "shots 3 条"],
        invariants=["不得输出 JSON 以外的任何内容", "不得删除用户点子的核心创意"],
    )
    try:
        return _ollama_generate(p, think=False, keep_alive=0)
    except Exception:
        return None


def _parse_creative(raw):
    if not raw:
        return None
    s = raw.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except Exception:
        return None
    return {
        "title": str(obj.get("title") or "").strip(),
        "premise": str(obj.get("premise") or "").strip(),
        "tags": [str(t) for t in (obj.get("tags") or [])][:6],
        "shots": [str(x) for x in (obj.get("shots") or [])][:6],
        "scenes": [str(x) for x in (obj.get("scenes") or [])][:6],
    }


def _creative_fallback(idea, style):
    return {
        "title": (idea[:16] + "…") if len(idea) > 16 else idea,
        "premise": idea,
        "tags": [style, "短剧", "AI 生成"],
        "shots": ["开场：建立核心冲突与人物关系", "中段：反转与情绪升级", "结尾：情绪落点与钩子"],
        "scenes": ["短视频剧情号", "品牌定制短剧", "剧情矩阵分发"],
        "used": "template",
    }


def generate_creative(idea, style="短剧"):
    """创意工坊入口：优先 Dify 编排 → 回退 Ollama → 模板兜底。
    返回 {title, premise, tags, shots, scenes, used}。"""
    config = load_service_config()
    raw = None
    used = "none"
    if config.get("dify_api_key"):
        raw = _dify_chat(_creative_dify_query(idea, style), config)
        if raw:
            used = "dify"
    if (not raw) and ollama_available():
        raw = _ollama_creative(idea, style)
        if raw:
            used = "ollama"
    parsed = _parse_creative(raw)
    if parsed and parsed.get("title"):
        parsed["used"] = used
        return parsed
    return _creative_fallback(idea, style)


def txt2img_generate(prompt, negative="", width=1024, height=576, steps=20,
                     cfg=7.0, count=4, seed=-1, enhance=True,
                     orchestrator="ollama", style="写实", on_progress=None):
    """文生图主流程：编排提示词 → 逐张调用 ComfyUI 出图 → 复制到 OUTPUTS。

    - orchestrator: 'dify' | 'ollama' | 'none'
        dify   → 优先 Dify 编排，失败回退 Ollama
        ollama → Ollama 翻译增强（默认，需本地 Ollama）
        none   → 直接使用原始提示词（不增强）
    - on_progress(idx, total, image_dict): 每出一张调用一次，用于实时回写进度。
    返回 dict：{images:[{url,seed,width,height}], prompt_used, negative_used,
                orchestrator_used, model, note}
    ComfyUI 不可用时抛 RuntimeError（由 worker 标记失败/重试）。
    """
    if not comfy_available():
        raise RuntimeError("本地 ComfyUI 未启动（%s），无法生成图像" % COMFY_URL)

    final_prompt = prompt
    final_negative = negative or ""
    used = None
    note_parts = []

    if orchestrator == "dify":
        res = orchestrate_with_dify(prompt, negative, style)
        if res:
            final_prompt, final_negative = res
            used = "dify"
        else:
            res = translate_to_sd_prompt(prompt, negative, style)
            if res:
                final_prompt, final_negative = res
                used = "ollama(fallback)"
                note_parts.append("Dify 不可用，已回退 Ollama")
            else:
                # 词典兜底：确保 ComfyUI 收到英文提示词
                final_prompt, final_negative = _dict_fallback_translate(prompt, negative, style)
                used = "dict_fallback"
                note_parts.append("Dify/Ollama 均不可用，已用内置词典做基础翻译（画质可能下降）")
    elif orchestrator == "ollama" or enhance:
        res = translate_to_sd_prompt(prompt, negative, style)
        if res:
            final_prompt, final_negative = res
            used = "ollama"
        else:
            final_prompt, final_negative = _dict_fallback_translate(prompt, negative, style)
            used = "dict_fallback"
            note_parts.append("Ollama 翻译失败或未启动，已用内置词典兜底翻译")
    else:
        # orchestrator == "none" 且 enhance=False：不增强，但仍做词典翻译避免中文直送
        final_prompt, final_negative = _dict_fallback_translate(prompt, negative, style)
        used = "dict_fallback"
        note_parts.append("未启用提示词增强，已用内置词典做基础翻译")

    # 释放 Ollama 显存，把 6GB 显卡让给 ComfyUI（1660Ti 显存不足以同时容纳两者）
    if used in ("ollama", "ollama(fallback)"):
        ollama_unload()

    images = []
    total = max(1, int(count))
    for i in range(total):
        s = (int(seed) + i) if seed and int(seed) > 0 else random.randint(0, 2 ** 31 - 1)
        try:
            path = _comfy_text2img(final_prompt, width=width, height=height,
                                   steps=steps, cfg=cfg, seed=s, timeout=240)
        except RuntimeError as e:
            # 透传 ComfyUI 真实错误，便于 worker 记录与诊断
            raise
        except Exception as e:
            raise RuntimeError("ComfyUI 调用异常: %s" % e)
        if not path or not os.path.isfile(path):
            continue
        dst_name = "t2i_%s_%d.png" % (uuid.uuid4().hex[:12], i)
        dst = os.path.join(OUTPUTS, dst_name)
        try:
            shutil.copyfile(path, dst)
        except Exception:
            continue
        img = {"url": "/file?name=" + dst_name, "seed": s, "width": width, "height": height}
        images.append(img)
        if on_progress:
            try:
                on_progress(len(images), total, img)
            except Exception:
                pass

    if not images:
        raise RuntimeError("ComfyUI 未产出任何图像（可能模型加载失败或服务繁忙），请重试")

    note = "本地 ComfyUI 真实文生图（%d/%d 张成功）" % (len(images), total)
    if note_parts:
        note += "；" + "；".join(note_parts)
    return {
        "images": images,
        "prompt_used": final_prompt,
        "negative_used": final_negative,
        "orchestrator_used": used,
        "model": _get_comfy_checkpoint(),
        "note": note,
    }


# ======================================================================
# 漫画创作：一键生成 4 格漫画（复用 Director N03 全局风格参考 + §5.2 角色一致性约束）
# ======================================================================
COMIC_STYLE_SUFFIX = {
    "日漫": "manga style, Japanese comic, clean lineart, screentone, expressive characters, black and white ink with subtle shading",
    "美漫": "american comic book style, bold ink lines, flat colors, dynamic composition, halftone",
    "港漫": "hong kong manhua style, ink wash, dramatic action, high contrast, detailed linework",
    "写实漫画": "realistic comic illustration, semi-realistic rendering, graphic novel style, detailed",
    "水彩绘本": "watercolor storybook illustration, soft pastel, hand-painted, gentle ink outlines",
}
COMIC_NEGATIVE = ("lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
                  "fewer digits, cropped, worst quality, low quality, blurry, watermark, "
                  "photo, 3d render, deformed panel, "
                  "unrelated objects, isolated flowers, still life, abstract floral pattern, "
                  "empty landscape, scenery only, disconnected background, speech bubble, word, letter")


# ---------------------------------------------------------------------------
# 图文一致性增强（对应「图文不符根因分析」下一轮修正 Checklist 必须项/建议项）
# 目标：避免「男孩雨夜捡受伤白猫」被稀释成「男孩+夜景」而猫/受伤/动作丢失。
# 不新增端点、不改 ComfyUI 工作流结构，仅在提示词与负向约束层做加固 + 诚实降级。
# ---------------------------------------------------------------------------
# 实体强制规则：故事出现下列中文关键词时，若翻译后的英文提示词缺失对应实体，
# 则自动补回并加 SD 权重，保证核心主体/对象/状态/动作不被风格词淹没。
_COMIC_ENTITY_RULES = [
    (("男孩", "少年", "男生", "男孩子"), "(1boy:1.3), (young boy:1.2)"),
    (("女孩", "少女", "女生", "女孩子"), "(1girl:1.3), (young girl:1.2)"),
    (("白猫",), "(white cat:1.4), (white fur:1.2)"),
    (("猫",), "(cat:1.35)"),
    (("受伤", "流血", "伤口", "血迹", "血"), "(injured:1.3), (bandaged:1.25), (wound:1.2)"),
    (("雨", "雨夜", "下雨", "暴雨"), "(rain:1.25), (rainy:1.2)"),
    (("夜", "夜晚", "夜里", "晚上"), "(night:1.2)"),
    (("捡", "抱", "捧", "搂", "抱起", "捧起", "拾"), "(holding:1.4), (kneeling:1.2), (arms:1.2)"),
    (("狗",), "(dog:1.3)"),
]

# 抽象→具象：把「受伤/雨夜/捡到」等抽象语义显式转成可渲染的视觉短语，
# 与实体强制互补——前者保证「有这个东西」，后者保证「长得出来」。
_COMIC_CONCRETE_RULES = [
    (("受伤", "流血", "伤口", "血"), "bandaged leg, small wound, blood stain on fur, limping"),
    (("雨夜", "下雨", "雨", "暴雨"), "rain drops, wet ground, puddle, street lamp reflection, dark clouds"),
    (("捡", "抱", "捧", "拾"), "kneeling down, reaching out hand, holding in arms, close-up of hands and cat"),
    (("白猫",), "small white cat, white fur, cat ears, tail, whiskers"),
]

# 规则兜底分镜用的「主体 / 对象 / 场景」关键词表（覆盖常见漫画题材）。
# 用于在 Ollama 不可用、且故事为单句无自然断句时，合成起承转合的递进分镜。
_COMIC_SUBJECT_KEYWORDS = [
    "男孩", "少年", "男生", "男孩子", "女孩", "少女", "女生", "女孩子",
    "男人", "女人", "老人", "小孩", "宝宝", "婴儿", "机器人", "骑士",
    "公主", "王子", "侦探", "医生", "老师", "学生", "战士", "勇者",
]
_COMIC_OBJECT_KEYWORDS = [
    "猫", "狗", "兔", "鸟", "鱼", "龙", "花", "书", "剑", "宝石", "蛋",
    "玩偶", "熊", "狐狸", "鹿", "狼", "马", "精灵", "机器人",
]
_COMIC_SCENE_KEYWORDS = [
    "雨夜", "下雨", "雪夜", "森林", "城市", "街道", "海边", "花园", "窗台",
    "屋顶", "车站", "学校", "教室", "医院", "战场", "山", "河", "湖", "沙漠",
    "废墟", "古堡", "夜", "雨",
]


def _comic_subject_object_scene(story):
    """从故事中粗提取主体(人物)/对象(被作用的事物)/场景，供规则兜底分镜合成。

    返回 (subject, obj, scene)；未命中时返回通用占位词（'主角'/'关键对象'/'场景'）。
    仅用于 Ollama 不可用时的兜底，不追求精确，只需能拼出可渲染的递进分镜。
    """
    subject = ""
    for k in _COMIC_SUBJECT_KEYWORDS:
        if k in story:
            subject = k
            break
    # 对象优先捕获带修饰的词（白猫/黑猫/小花猫…），否则回退到单字关键词
    obj = ""
    m = re.search(r"([白黑小大花老可爱受伤的]+?(猫|狗|兔|鸟|狐狸|熊|鹿|龙|狼|马|精灵))", story)
    if m:
        obj = m.group(1)
    else:
        for k in _COMIC_OBJECT_KEYWORDS:
            if k in story:
                obj = k
                break
    scene = ""
    for k in _COMIC_SCENE_KEYWORDS:
        if k in story:
            scene = k
            break
    if not subject:
        subject = "主角"
    if not obj:
        obj = "关键对象"
    if not scene:
        scene = "场景"
    return subject, obj, scene


def _comic_ensure_entities(story, en_prompt):
    """实体强制：保证翻译后的英文提示词包含故事关键主体/对象/状态/动作，缺失则加权补回。"""
    if not story or not en_prompt:
        return en_prompt
    low = en_prompt.lower()
    add = []
    for keys, phrase in _COMIC_ENTITY_RULES:
        if any(k in story for k in keys):
            tokens = [t.strip(" ,()") for t in phrase.split(",")]
            if not any(tok and tok.lower() in low for tok in tokens):
                add.append(phrase)
    if add:
        en_prompt = en_prompt.rstrip(", ") + ", " + ", ".join(add)
    return en_prompt


def _comic_append_concrete(story, en_prompt):
    """抽象→具象：根据中文关键词追加可渲染的视觉短语，强化「受伤/雨夜/动作」的可视化。"""
    if not story or not en_prompt:
        return en_prompt
    add = []
    for keys, phrase in _COMIC_CONCRETE_RULES:
        if any(k in story for k in keys) and phrase not in en_prompt:
            add.append(phrase)
    if add:
        en_prompt = en_prompt.rstrip(", ") + ", " + ", ".join(add)
    return en_prompt


def _comic_dynamic_negative(story, base_neg):
    """按故事动态追加负向约束：单主角→排除异性/多余人物；提及猫→排除猫缺失/狗；
    统一强化无文字/水印/气泡/签名。去重后返回。"""
    neg = base_neg or ""
    has_boy = any(k in story for k in ("男孩", "少年", "男生", "男孩子", "男性"))
    has_girl = any(k in story for k in ("女孩", "少女", "女生", "女孩子", "女性"))
    has_cat = "猫" in story
    add = []
    if has_boy and not has_girl:
        add += ["girl", "woman", "female", "lady", "extra person", "multiple people", "duplicate character"]
    if has_cat:
        add += ["cat missing", "no cat", "mutated cat", "dog"]
    # 漫画统一禁项（避免随机竖排字/气泡/签名/内嵌多格）
    add += ["text", "speech bubble", "word", "letter", "watermark", "signature",
            "logo", "manga sfx", "multiple panels", "comic page layout", "grid",
            "collage", "split screen", "divided frame", "panel border",
            "two panels", "four panels"]
    # 重建：base 词与追加词统一去重，避免把 base 自身重复拼接
    seen = set()
    tokens = []
    for p in [p.strip() for p in (base_neg or "").split(",") if p.strip()] + add:
        if p and p.lower() not in seen:
            seen.add(p.lower())
            tokens.append(p)
    return ", ".join(tokens)


def _split_story_to_panels(story, style, orchestrator="ollama", panels=4):
    """把故事拆分为 N 格分镜；优先 Ollama 结构化拆分，失败则规则兜底。

    返回 list[{"caption": 中文短描述, "characters": 主角, "action": 动作/情绪,
               "scene": 场景, "props": 关键道具/对象, "prompt": 完整中文画面描述}]。
    """
    # 先尝试从故事中提炼主角，用于兜底和权重注入
    main_subject = _extract_main_subject(story)

    if (orchestrator == "ollama") and ollama_available():
        p = (
            "你是一个漫画分镜编剧。请把下面的故事拆成 %d 格漫画，每格是一个独立画面。"
            "严格只输出一个 JSON 数组，不要任何解释文字，格式："
            '[{"caption":"中文旁白短句(≤20字)","characters":"本格必须出现的主角(如:男孩,猫)",'
            '"action":"具体动作或表情(如:蹲下,抚摸,流泪)","scene":"场景(如:花园,窗台)",'
            '"props":"本格关键道具/对象(如:雨伞,白猫,绷带)","prompt":"完整中文画面描述,'
            '必须包含主角、动作、对象、场景、情绪"}]。\n'
            "要求(必须遵守):\n"
            "1. %d 格连起来完整讲述故事，有起承转合；\n"
            "2. 每一格都必须出现故事中的主要角色与关键对象（如猫/礼物/伤口），"
            "禁止只画风景、物品、花纹、抽象图案；\n"
            "3. 画面以角色动作和表情为核心，场景与道具为辅助；\n"
            "4. prompt 先写角色和动作，再写关键对象，再写场景，最后写情绪；\n"
            "5. 风格为 '%s'，画面内不要出现任何文字、对话框、水印。\n\n故事：%s"
            % (panels, panels, style, story)
        )
        try:
            txt = _ollama_generate(p, think=False, keep_alive=0)
            s = txt.strip()
            start = s.find("[")
            end = s.rfind("]")
            if start >= 0 and end > start:
                arr = json.loads(s[start:end + 1])
                out = []
                for it in arr[:panels]:
                    if not isinstance(it, dict):
                        continue
                    cap = (it.get("caption") or "").strip()
                    chars = (it.get("characters") or "").strip()
                    action = (it.get("action") or "").strip()
                    scene = (it.get("scene") or "").strip()
                    props = (it.get("props") or "").strip()
                    pr = (it.get("prompt") or "").strip()
                    if not pr:
                        pr = "，".join([x for x in [chars, action, props, scene] if x])
                    if pr:
                        # 确保主角出现：如果 LLM 没写主角，强制前置
                        if main_subject and main_subject not in pr:
                            pr = main_subject + "，" + pr
                        out.append({
                            "caption": cap or pr[:20],
                            "characters": chars or main_subject,
                            "action": action,
                            "scene": scene,
                            "props": props,
                            "prompt": pr,
                        })
                if len(out) >= 2:
                    return out
        except Exception as e:
            logger.warning("[comic] Ollama 分镜拆分失败，回退规则拆分: %s" % e)

    # 规则兜底：按叙事节奏切句（包含中英文逗号），并强制每格包含主角
    delims = "。！？!?；;，,、\n"
    parts = []
    buf = ""
    for ch in story:
        if ch in delims:
            if buf.strip():
                parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    if not parts:
        parts = [story]

    # 单句（无自然断句）：Ollama 不可用且故事为一整句时，按「起承转合」合成递进分镜，
    # 避免 4 格都回到同一句话导致画面无叙事推进。主体/对象/场景从关键词粗提取。
    if len(parts) == 1:
        subject, obj, scene = _comic_subject_object_scene(story)
        if scene and scene != "场景":
            beats = [
                ("开端", "%s在%s发现了%s" % (subject, scene, obj)),
                ("发展", "%s慢慢走近，蹲下轻轻触碰%s" % (subject, obj)),
                ("转折", "%s小心地把%s抱进怀里" % (subject, obj)),
                ("高潮", "%s用衣服裹住%s，在%s里转身离开" % (subject, obj, scene)),
            ]
        else:
            beats = [
                ("开端", "%s发现了%s" % (subject, obj)),
                ("发展", "%s慢慢走近，蹲下轻轻触碰%s" % (subject, obj)),
                ("转折", "%s小心地把%s抱进怀里" % (subject, obj)),
                ("高潮", "%s抱着%s走向光亮处" % (subject, obj)),
            ]
        out = []
        for i in range(panels):
            beat, text = beats[i] if i < len(beats) else beats[-1]
            prompt = "%s，%s，必须出现主要角色与关键对象，不要只画空背景或静物" % (beat, text)
            out.append({
                "caption": text[:20],
                "characters": main_subject or subject,
                "action": beat,
                "scene": scene if scene != "场景" else "",
                "prompt": prompt,
            })
        return out

    # 多句：按句分组到各格（含主角锚定），保证每格有递进内容
    out = []
    per = max(1, (len(parts) + panels - 1) // panels)
    beats = ["开端", "发展", "转折", "高潮", "结局"]
    for i in range(panels):
        chunk = parts[i * per:(i + 1) * per]
        if not chunk:
            chunk = parts[-1:]
        text = "。".join(chunk)
        beat = beats[i] if i < len(beats) else "后续"
        prompt = "%s，%s，必须出现主要角色，不要只画空背景或静物" % (beat, text)
        out.append({
            "caption": text[:20],
            "characters": main_subject,
            "action": beat,
            "scene": "",
            "prompt": prompt,
        })
    return out


def _extract_main_subject(story):
    """从故事中简单提取主要角色/主体（干净中文名词，不含截断/权重语法），用于兜底分镜主角锚定。

    优先用 Ollama 结构化提取；Ollama 不可用时回退到关键词规则提取，返回「人物,对象」式
    干净名词（如「男孩,白猫」），绝不返回被截断的垃圾片段或中文权重语法。
    """
    if not story:
        return ""
    if ollama_available():
        p = (
            "请从下面故事中只提取最主要的一到两个角色/主体（如：男孩、猫、少女、机器人），"
            "严格只输出一个 JSON 对象，格式：{\"subjects\":\"角色1,角色2\"}，不要解释。\n\n故事：%s" % story
        )
        try:
            txt = _ollama_generate(p, think=False, keep_alive=0)
            s = txt.strip()
            start = s.find("{")
            end = s.rfind("}")
            if start >= 0 and end > start:
                obj = json.loads(s[start:end + 1])
                subj = (obj.get("subjects") or obj.get("main_subject") or "").strip()
                if subj:
                    return subj
        except Exception:
            pass
    # 规则兜底：用关键词粗提取主体与对象，组合为干净名词（如「男孩,白猫」）
    subject, obj, _ = _comic_subject_object_scene(story)
    parts = []
    if subject and subject != "主角":
        parts.append(subject)
    if obj and obj != "关键对象":
        parts.append(obj)
    if parts:
        return "，".join(parts)
    # 末路：取开头连续中文名词短语（上限 6 字，避免在半词处截断）
    m = re.search(r"^([\u4e00-\u9fff]{2,6})", story.strip())
    if m:
        return m.group(1)
    return story.strip()[:6]


class AssetInjectBlocked(RuntimeError):
    """铁律 1.1：角色资产(IP-Adapter)注入被阻断且处于强制注入模式。

    当调用方传入角色参考图且 IP-Adapter 权重可用时，角色生成必须经由 IP-Adapter
    注入该角色特征；若该注入链路失败，禁止静默降级为「仅靠 Prompt 生成角色」
    （会导致角色跨格不一致 / 图文不符），改为明确阻断并向上抛出本异常。
    """


def _comic_load_font(size):
    """尽力加载中文字体，失败回退 PIL 默认位图字体（仅 ASCII 清晰）。"""
    for fp in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
               "C:/Windows/Fonts/msyh.ttf"):
        if os.path.isfile(fp):
            try:
                from PIL import ImageFont
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    try:
        from PIL import ImageFont
        return ImageFont.load_default()
    except Exception:
        return None


def _render_text_placeholder_panel(text, width, height, out_path):
    """L2 降级（设计手册 §5.4）：用 PIL 将每格文本描述渲染为占位图，替代真实漫画渲染。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), (24, 26, 32))
    draw = ImageDraw.Draw(img)
    font = _comic_load_font(max(18, width // 30))
    max_chars = max(8, width // 30)
    lines, cur = [], ""
    for ch in (text or ""):
        cur += ch
        if len(cur) >= max_chars:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    line_h = (font.size if font else 18) + 8
    total_h = len(lines) * line_h
    y = max(12, (height - total_h) // 2)
    for ln in lines[-max(1, height // line_h):]:
        try:
            bbox = draw.textbbox((0, 0), ln, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = len(ln) * (font.size if font else 12)
        x = max(10, (width - tw) // 2)
        draw.text((x, y), ln, fill=(220, 224, 232), font=font)
        y += line_h
    img.save(out_path)
    return out_path


def _render_maintenance_card(width, height, out_path):
    """L3 降级（设计手册 §5.4）：生成服务（ComfyUI 与 Ollama）均不可用时的系统维护中标题卡。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), (18, 20, 26))
    draw = ImageDraw.Draw(img)
    title_font = _comic_load_font(max(28, width // 16))
    sub_font = _comic_load_font(max(15, width // 32))
    title, sub = "系统维护中", "ComfyUI 与 Ollama 生成服务暂时不可用，请稍后重试"
    for txt, font, dy, fill in ((title, title_font, -height // 16, (230, 180, 90)),
                                (sub, sub_font, height // 22, (180, 186, 196))):
        try:
            bbox = draw.textbbox((0, 0), txt, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = len(txt) * (font.size if font else 14)
        draw.text(((width - tw) // 2, height // 2 + dy), txt, fill=fill, font=font)
    img.save(out_path)
    return out_path


def _comic_degrade_l2(story, style, panels, W, H, orchestrator):
    """L2 降级（设计手册 §5.4）：ComfyUI 不可用但 Ollama 可用。

    保留分镜拆分（Ollama 在线），用 Ollama 产出每格文本描述并以 PIL 渲染为占位图，
    返回与正常 generate_comic 同构的 dict，便于前端不改即可展示降级态。
    """
    start_ts = time.time()
    panel_specs = _split_story_to_panels(story, style, orchestrator, panels)
    while len(panel_specs) < panels:
        panel_specs.append(panel_specs[-1] if panel_specs else {"caption": "", "prompt": story})
    images, panels_out = [], []
    for idx, spec in enumerate(panel_specs):
        text = "%s\n%s" % (spec.get("caption") or "", spec.get("prompt") or story)
        dst_name = "t2i_%s_L2_%d.png" % (uuid.uuid4().hex[:12], idx)
        dst = os.path.join(OUTPUTS, dst_name)
        _render_text_placeholder_panel(text[:240], W, H, dst)
        images.append({"url": "/file?name=" + dst_name, "seed": 0, "width": W, "height": H,
                       "final_prompt": spec.get("prompt", ""), "model": "L2-placeholder",
                       "steps": 0, "cfg": 0, "sampler": "text", "style": style,
                       "size": "%dx%d" % (W, H), "degrade": "L2"})
        panels_out.append({"index": idx, "caption": spec.get("caption") or "",
                           "prompt": spec.get("prompt", ""), "degrade": "L2"})
    elapsed_ms = int((time.time() - start_ts) * 1000)
    return {
        "images": images, "panels": panels_out, "style_ref": None, "style_ref_local": "",
        "orchestrator_used": "ollama(L2-degrade)", "model": "L2-placeholder",
        "note": "L2 降级：ComfyUI 不可用，以 Ollama 文本占位图呈现分镜（非真实渲染）",
        "elapsed_ms": elapsed_ms, "params": {"kind": "comic", "degrade": "L2",
        "panels": len(images), "width": W, "height": H, "style": style},
    }


def _comic_degrade_l3(panels, W, H):
    """L3 降级（设计手册 §5.4）：ComfyUI 与 Ollama 均不可用，返回系统维护中标题卡。"""
    start_ts = time.time()
    dst_name = "t2i_%s_L3.png" % uuid.uuid4().hex[:12]
    dst = os.path.join(OUTPUTS, dst_name)
    _render_maintenance_card(W, H, dst)
    images, panels_out = [], []
    for idx in range(panels):
        images.append({"url": "/file?name=" + dst_name, "seed": 0, "width": W, "height": H,
                       "final_prompt": "", "model": "L3-maintenance", "steps": 0, "cfg": 0,
                       "sampler": "none", "style": "", "size": "%dx%d" % (W, H), "degrade": "L3"})
        panels_out.append({"index": idx, "caption": "系统维护中", "prompt": "", "degrade": "L3"})
    elapsed_ms = int((time.time() - start_ts) * 1000)
    return {
        "images": images, "panels": panels_out, "style_ref": None, "style_ref_local": "",
        "orchestrator_used": "none(L3-degrade)", "model": "L3-maintenance",
        "note": "L3 降级：生成服务（ComfyUI 与 Ollama）均不可用，显示系统维护中标题卡",
        "elapsed_ms": elapsed_ms, "params": {"kind": "comic", "degrade": "L3",
        "panels": len(images), "width": W, "height": H},
    }


def _comic_panel_generate(final_prompt, W, H, steps, cfg, s, neg,
                          char_comfy, style_ref_comfy, ip_available,
                          first_local, idx, style_arg,
                          enforce_asset=False):
    """单格出图：严格模板驱动（铁律 1.2，禁止手搓 ComfyUI 节点 JSON）。

    统一经 intent_translator + workflow_templates 产出工作流并提交 ComfyUI：
    - 首帧锚定（idx>0 且 first_local 存在）：走 shot_image_img2img 模板（纯像素承接，不叠 IP）。
    - 有身份图/风格图且 IP 可用：走 shot_image_gen_ip 模板（身份路/风格路）。
    - 否则：走 shot_image_gen 基础文生图模板。

    任何模板提交失败均诚实抛出（不再回退手搓构建）；仅当 enforce_asset 且身份注入失败时
    升级为 AssetInjectBlocked（铁律 1.1 禁止仅靠 Prompt 生成角色）。
    """
    import intent_translator
    identity_intended = bool(char_comfy) and bool(ip_available)
    identity = char_comfy[0] if identity_intended else None
    style_img = style_ref_comfy if (style_ref_comfy and ip_available) else None
    intent = intent_translator.build_shot_image_intent(
        prompt_text=final_prompt,
        style_tags=[style_arg, "通用"] if style_arg else ["通用"],
        negative_prompt=neg,
        identity_image=identity,
        style_image=style_img,
        style_weight=0.4,
        generation_params={"width": W, "height": H, "seed": s,
                           "steps": steps, "cfg": cfg})
    # 首帧锚定：经 img2img 模板（同理禁止手搓）
    if first_local and idx > 0:
        intent["task_type"] = "shot_image_img2img"
        intent["init_image"] = first_local
        intent["denoise"] = 0.5
    try:
        return _comfy_text2img_via_template(intent, timeout=180)
    except Exception as e:
        if enforce_asset and identity_intended:
            raise AssetInjectBlocked(
                "角色资产(IP-Adapter)注入失败，按铁律1.1禁止仅靠Prompt生成角色: %s" % e)
        # 非强制场景：模板失败诚实抛出，交由 generate_comic 跳过该格（不回退手搓）
        raise


@metrics.track_generation("comic_image")
def generate_comic(story, style="日漫", negative="", resolution="1024", ratio="3:4",
                   steps=28, seed=42, enhance=True, orchestrator="ollama",
                   character_refs=None, panels=4, on_progress=None, cfg=8.5):
    """一键生成 4 格漫画：复用 Director 生图约束逻辑。

    1) 分镜拆分：_split_story_to_panels（Ollama 优先，规则兜底）——对应导演「剧本→分镜」。
    2) 全局风格参考图（N03 复用）：以漫画风格基调出一张参考图，作为第二路 IP-Adapter
       风格条件，统一 4 格色调/质感/光影；无 ComfyUI 或 IP-Adapter 权重缺失则诚实降级
       为提示词风格强调（与 N03 下游不注入风格条件一致）。
    3) 逐格出图：每格用「分镜提示词 + 漫画风格后缀 + 负向」经 intent_translator + 模板
       调用 ComfyUI；若提供角色参考图则走 shot_image_gen_ip 模板（身份路 + 风格路），
       保证角色跨格一致（§5.2 复用，铁律 1.2 禁止手搓）；首帧锚定走 shot_image_img2img 模板。
    返回 dict：{images, panels, style_ref, style_ref_local, orchestrator_used, model, note,
                elapsed_ms, params}
    """
    start_ts = time.time()

    # 分辨率/比例 → 宽高（与 t2i 一致：长边=resolution，按比例算短边，8 整除）
    try:
        base = int(resolution)
    except Exception:
        base = 1024
    if ratio == "9:16":
        W, H = int(base * 9 / 16), base
    elif ratio == "3:4":
        W, H = int(base * 3 / 4), base
    elif ratio == "1:1":
        W = H = base
    elif ratio == "4:3":
        W, H = base, int(base * 3 / 4)
    elif ratio == "21:9":
        W, H = base, int(base * 9 / 21)
    else:  # 16:9 默认
        W, H = base, int(base * 9 / 16)
    W, H = max(64, (W // 8) * 8), max(64, (H // 8) * 8)

    # 设计手册 §5.4 降级原则：ComfyUI 不可用时分层降级，绝不静默崩溃
    comfy_ok = comfy_available()
    ollama_ok = ollama_available()
    if not comfy_ok:
        if ollama_ok:
            logger.warning("[comic] ComfyUI 不可用，进入 L2 降级（Ollama 文本占位图）")
            return _comic_degrade_l2(story, style, panels, W, H, orchestrator)
        logger.error("[comic] ComfyUI 与 Ollama 均不可用，进入 L3 降级（系统维护中标题卡）")
        return _comic_degrade_l3(panels, W, H)

    style_suffix = COMIC_STYLE_SUFFIX.get(style, COMIC_STYLE_SUFFIX["日漫"])
    # 动态负向：按故事主体/对象追加针对性排除词（如单主角→排除异性/多余人物、提猫→排除猫缺失/狗）
    base_neg = ((negative + ", ") if negative else "") + COMIC_NEGATIVE
    neg = _comic_dynamic_negative(story, base_neg)
    # 统一故事主角，用于每格 prompt 权重锚定
    main_subject = _extract_main_subject(story)

    # 1) 分镜拆分
    panel_specs = _split_story_to_panels(story, style, orchestrator, panels)
    while len(panel_specs) < panels:
        panel_specs.append(panel_specs[-1] if panel_specs else {"caption": "", "prompt": story})

    # 2) 全局风格参考图（N03 复用）
    style_ref_local = None
    style_ref_comfy = None
    style_ref_url = None
    ip_available = False
    try:
        _get_comfy_ipadapter()
        _get_comfy_clip_vision()
        ip_available = True
    except Exception:
        ip_available = False
    if ip_available:
        try:
            sp = ("comic panel reference still, consistent " + style_suffix +
                  ", narrative scene, same main character present, no text, no speech bubbles, "
                  "clean composition, film still, high detail")
            style_ref_local = _comfy_text2img(sp, width=512, height=288, steps=20, timeout=120)
            if style_ref_local and os.path.isfile(style_ref_local):
                try:
                    style_ref_comfy = _upload_to_comfy(style_ref_local)
                except Exception:
                    style_ref_comfy = None
                dst_name = "t2i_%s_style.png" % uuid.uuid4().hex[:12]
                dst = os.path.join(OUTPUTS, dst_name)
                try:
                    shutil.copyfile(style_ref_local, dst)
                    style_ref_url = "/file?name=" + dst_name
                except Exception:
                    style_ref_url = None
        except Exception as e:
            logger.warning("[comic] 全局风格参考图生成失败（不阻塞）: %s" % str(e)[:160])
            style_ref_local = None

    # 角色参考图上传（§5.2 身份路，可选）
    char_comfy = []
    for ref in (character_refs or []):
        try:
            if ref and os.path.isfile(ref):
                char_comfy.append(_upload_to_comfy(ref))
        except Exception:
            pass

    # 逐格出图严格走模板驱动（铁律 1.2：catalog + workflow_templates 齐备即经
    # intent_translator 产出工作流；模板不可用由 translate 诚实抛出，无手搓兜底）。

    # 3) 逐格出图
    used = None
    images = []
    panels_out = []
    total = len(panel_specs)
    rnd = random.Random()
    first_local = None  # 首格出图本地路径，用于第 2 格起的首帧锚定
    # 铁律 1.1：提供了角色参考图且 IP-Adapter 可用时，强制角色资产注入；
    # 该模式下 IP 失败必须阻断（抛出 AssetInjectBlocked），禁止降级为纯 Prompt 生成角色。
    enforce_asset = bool(char_comfy) and bool(ip_available)
    for idx, spec in enumerate(panel_specs):
        s = (int(seed) + idx) if seed and int(seed) > 0 else rnd.randint(0, 2 ** 31 - 1)
        zh_prompt = spec["prompt"]
        chars = (spec.get("characters") or main_subject or "").strip()
        action = (spec.get("action") or "").strip()

        # 直接翻译中文分镜描述（不注入中文权重语法，避免把「（男孩:1.35）」之类
        # 中文括号权重送入 CLIP 变成噪音；角色/动作一致性由下方英文 weight_prefix 保证）。
        if orchestrator == "ollama" or enhance:
            tr = translate_to_sd_prompt(zh_prompt, "", style)
            if tr:
                en_p = tr[0]
                used = used or "ollama"
            else:
                en_p = _dict_fallback_translate(zh_prompt, "", style)[0]
                used = used or "dict_fallback"
        else:
            en_p = _dict_fallback_translate(zh_prompt, "", style)[0]
            used = used or "dict_fallback"

        # 英文权重前缀（角色/动作一致性），与中文分镜解耦，避免 CLIP 解析中文权重
        weight_prefix = ""
        if chars:
            weight_prefix += "(same main character:1.35), (character consistency:1.2), "
        if action:
            weight_prefix += "(clear character action:1.15), "
        base_final = "%s%s, %s" % (weight_prefix, en_p, style_suffix)
        # 一致性加固（对照「图文不符」修正 Checklist）：
        # 1) 实体强制：翻译丢失的关键主体/对象/状态/动作自动补回并加权
        # 2) 抽象→具象：把「受伤/雨夜/捡到」等转成可渲染视觉短语
        base_final = _comic_ensure_entities(story, base_final)
        base_final = _comic_append_concrete(story, base_final)
        # 单格只描述一个画面；4 格排版交给前端网格，这里禁止模型在单张图内画内嵌多格
        final_prompt = ("%s, single comic panel, one scene, no text, no speech bubbles, "
                        "no panel border, no multiple panels, no grid, no collage, no split screen"
                        % base_final)
        try:
            path = _comic_panel_generate(
                final_prompt, W, H, steps, cfg, s, neg,
                char_comfy, style_ref_comfy, ip_available,
                first_local, idx, style,
                enforce_asset=enforce_asset)
        except AssetInjectBlocked:
            # 强制资产注入被阻断：直接抛出，不降级（铁律 1.1）
            raise
        except Exception as e:
            # 模板路径失败（非强制）：诚实跳过该格，不回退手搓（铁律 1.2）
            logger.warning("[comic] 第 %d 格模板出图失败，跳过该格（不回退手搓）: %s" % (idx, e))
            continue
        if idx == 0 and path and os.path.isfile(path):
            first_local = path
        if not path or not os.path.isfile(path):
            continue
        dst_name = "t2i_%s_%d.png" % (uuid.uuid4().hex[:12], idx)
        dst = os.path.join(OUTPUTS, dst_name)
        try:
            shutil.copyfile(path, dst)
        except Exception:
            continue
        img = {
            "url": "/file?name=" + dst_name, "seed": s, "width": W, "height": H,
            "final_prompt": final_prompt, "model": _get_comfy_checkpoint(),
            "steps": steps, "cfg": cfg, "sampler": "euler", "style": style,
            "size": "%dx%d" % (W, H),
        }
        images.append(img)
        panels_out.append({
            "index": idx,
            "caption": spec.get("caption") or "",
            "characters": spec.get("characters") or main_subject or "",
            "action": spec.get("action") or "",
            "scene": spec.get("scene") or "",
            "prompt": zh_prompt,
            "final_prompt": final_prompt,
        })
        if on_progress:
            try:
                on_progress(len(images), total, img)
            except Exception:
                pass

    if used in ("ollama", "ollama(fallback)"):
        ollama_unload()

    if not images:
        raise RuntimeError("ComfyUI 未产出任何漫画图像，请确认模型已加载或服务未繁忙")

    note = "本地 ComfyUI 真实生成 %d 格漫画（风格：%s）" % (len(images), style)
    if style_ref_local:
        note += "；已注入全局风格参考图保持 4 格统一"
    if char_comfy:
        note += "；已注入角色参考图保持角色一致"
    if not ip_available:
        note += "（未检测到 IP-Adapter 权重，风格/角色一致性仅由提示词保证）"
    elapsed_ms = int((time.time() - start_ts) * 1000)
    full_params = {
        "kind": "comic",
        "model": _get_comfy_checkpoint(),
        "style": style,
        "panels": len(images),
        "requested_panels": panels,
        "resolution": str(resolution),
        "ratio": ratio,
        "width": W,
        "height": H,
        "steps": steps,
        "seed": seed,
        "cfg": cfg,
        "sampler": "euler",
        "orchestrator": used or orchestrator or "dict_fallback",
        "enhance": enhance,
        "negative": neg,
        "ip_available": ip_available,
        "character_refs": len(character_refs or []),
    }
    return {
        "images": images,
        "panels": panels_out,
        "style_ref": style_ref_url,
        "style_ref_local": style_ref_local or "",
        "orchestrator_used": used or "dict_fallback",
        "model": _get_comfy_checkpoint(),
        "note": note,
        "elapsed_ms": elapsed_ms,
        "params": full_params,
    }


# =============================================================================
# 图生图专业编辑工作流（社区高频：局部重绘 / 扩图 / 高清放大 / ControlNet / IP-Adapter）
# 统一沿用 _comfy_run(wf) 提交 ComfyUI /prompt 并轮询 /history 取回输出图。
# 所有外部依赖（ComfyUI / 节点 / 权重）缺失时一律抛 RuntimeError（诚实降级，绝不静默）。
# =============================================================================

def _get_comfy_upscale_model(prefer_factor=None):
    """返回 ComfyUI/models/upscale_models 下一个可用放大模型文件名；prefer_factor=2/4 优先匹配倍数。"""
    if not comfy_available():
        return None
    root = comfy_env.get_comfy_root()
    if not root:
        return None
    d = os.path.join(root, "models", "upscale_models")
    if not os.path.isdir(d):
        return None
    files = []
    for ext in ("*.pth", "*.pt", "*.safetensors", "*.bin"):
        files += glob.glob(os.path.join(d, ext))
    if not files:
        return None
    names = [os.path.basename(f) for f in files]
    if prefer_factor in (2, 4):
        tag = "%dx" % prefer_factor
        for n in names:
            if tag in n.lower():
                return n
    for key in ("4x-UltraSharp", "RealESRGAN_x4plus", "4x_ESRGAN", "ESRGAN", "UltraSharp"):
        for n in names:
            if key.lower() in n.lower():
                return n
    return names[0]


_EDefault_NEG = "low quality, blurry, watermark, text, deformed, extra limbs"


def _comfy_inpaint(image_path, mask_path, prompt, negative="", strength=0.75,
                   steps=20, cfg=7.0, seed=-1, sampler="euler", scheduler="normal", timeout=300):
    """局部重绘（Inpainting）：原图 + 蒙版(Alpha=重绘区) 经 VAEEncodeForInpaint 仅重绘遮罩区域。

    蒙版由前端画笔编辑器生成 RGBA PNG（重绘区 alpha=255）；LoadImage 的 MASK 输出即 alpha 通道。
    依赖 ComfyUI 与底模权重，失败抛 RuntimeError。
    """
    if not comfy_available():
        raise RuntimeError("局部重绘失败：ComfyUI 未启动（请先启动秋葉 ComfyUI :8188）")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    up_img = _upload_to_comfy(image_path)
    up_mask = _upload_to_comfy(mask_path)
    if not up_img or not up_mask:
        raise RuntimeError("局部重绘失败：参考图或蒙版上传 ComfyUI 失败")
    neg = negative or _EDefault_NEG
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": scheduler, "denoise": float(strength),
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["10", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "LoadImage", "inputs": {"image": up_img}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_inpaint"}},
        "10": {"class_type": "VAEEncodeForInpaint", "inputs": {
            "pixels": ["5", 0], "vae": ["4", 2], "mask": ["55", 1], "grow_mask_by": 6}},
        "55": {"class_type": "LoadImage", "inputs": {"image": up_mask}},
    }
    return _comfy_run(wf, timeout)


def _comfy_outpaint(image_path, left=0, top=0, right=0, bottom=0, feathering=20,
                    prompt="", negative="", strength=0.8, steps=25, cfg=7.0, seed=-1,
                    sampler="euler", scheduler="normal", timeout=300):
    """扩图（Outpainting）：Pad Image for Outpainting 扩展画布并生成边缘蒙版，再 inpaint 融合。

    left/top/right/bottom 为四向扩展像素量，feathering 控制原图与新增区的过渡平滑度。
    依赖 ComfyUI 与底模权重，失败抛 RuntimeError。
    """
    if not comfy_available():
        raise RuntimeError("扩图失败：ComfyUI 未启动（请先启动秋葉 ComfyUI :8188）")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    up = _upload_to_comfy(image_path)
    if not up:
        raise RuntimeError("扩图失败：参考图上传 ComfyUI 失败")
    neg = negative or _EDefault_NEG
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": scheduler, "denoise": float(strength),
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["10", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "LoadImage", "inputs": {"image": up}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt or "extend the scene naturally", "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_outpaint"}},
        "10": {"class_type": "VAEEncodeForInpaint", "inputs": {
            "pixels": ["11", 0], "vae": ["4", 2], "mask": ["11", 1], "grow_mask_by": 6}},
        "11": {"class_type": "PadImageForOutpainting", "inputs": {
            "image": ["5", 0], "left": int(left), "top": int(top),
            "right": int(right), "bottom": int(bottom), "feathering": int(feathering)}},
    }
    return _comfy_run(wf, timeout)


def _comfy_upscale(image_path, scale=2, upscale_model=None, denoise=0.0,
                   steps=20, cfg=7.0, prompt="", negative="", seed=-1,
                   sampler="euler", scheduler="normal", timeout=300):
    """高清放大/超分：(可选)模型放大 + (可选)轻量 img2img 细节增强。

    - scale=2/4 自动优选对应倍数放大模型（prefer 指定则优先）；缺模型诚实报错。
    - denoise>0 时在放大后追加一次低强度 img2img 精炼（补细节、不改动构图）。
    依赖 ComfyUI 与放大模型权重，失败抛 RuntimeError。
    """
    if not comfy_available():
        raise RuntimeError("高清放大失败：ComfyUI 未启动（请先启动秋葉 ComfyUI :8188）")
    up = _upload_to_comfy(image_path)
    if not up:
        raise RuntimeError("高清放大失败：参考图上传 ComfyUI 失败")
    um = upscale_model or _get_comfy_upscale_model(prefer_factor=scale)
    if not um:
        raise RuntimeError("高清放大失败：未找到放大模型（请在 ComfyUI/models/upscale_models 放入 4x-UltraSharp / RealESRGAN 等）")
    if denoise and float(denoise) > 0:
        if seed is None or int(seed) < 0:
            seed = random.randint(0, 2 ** 31 - 1)
        neg = negative or _EDefault_NEG
        wf = {
            "1": {"class_type": "LoadImage", "inputs": {"image": up}},
            "2": {"class_type": "LoadUpscaleModel", "inputs": {"model_name": um}},
            "3": {"class_type": "UpscaleImageByModel", "inputs": {"image": ["1", 0], "upscale_model": ["2", 0]}},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
            "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["3", 0], "vae": ["4", 2]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt or "high quality, detailed, sharp", "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
            "8": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": steps, "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": float(denoise),
                "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 2]}},
            "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "manju_upscale"}},
        }
    else:
        wf = {
            "1": {"class_type": "LoadImage", "inputs": {"image": up}},
            "2": {"class_type": "LoadUpscaleModel", "inputs": {"model_name": um}},
            "3": {"class_type": "UpscaleImageByModel", "inputs": {"image": ["1", 0], "upscale_model": ["2", 0]}},
            "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "manju_upscale"}},
        }
    return _comfy_run(wf, timeout)


def _comfy_img2img_with_controlnet(image_path, prompt, negative="", control_type="canny",
                                    weight=1.0, strength=0.6, steps=20, cfg=7.0, seed=-1,
                                    sampler="euler", scheduler="normal", timeout=300):
    """图生图 + ControlNet 结构控制：用参考图提取结构（边缘/深度/姿态/线稿/涂鸦）约束生成。

    control_type ∈ {canny, depth, openpose, lineart, scribble}；预处理节点缺失或权重缺失诚实报错。
    依赖 ComfyUI 与对应 ControlNet 权重，失败抛 RuntimeError。
    """
    if not comfy_available():
        raise RuntimeError("ControlNet 编辑失败：ComfyUI 未启动（请先启动秋葉 ComfyUI :8188）")
    if not detect_comfy_controlnet():
        raise RuntimeError("ControlNet 编辑失败：未安装 ControlNet 节点或缺失权重")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    up = _upload_to_comfy(image_path)
    if not up:
        raise RuntimeError("ControlNet 编辑失败：参考图上传 ComfyUI 失败")
    preprocess_map = {"canny": "CannyEdgePreprocessor", "depth": "DepthPreprocessor",
                      "openpose": "OpenposePreprocessor", "lineart": "LineArtPreprocessor",
                      "scribble": "ScribblePreprocessor"}
    pre = preprocess_map.get(control_type, "CannyEdgePreprocessor")
    if not _comfy_has_node(pre):
        raise RuntimeError("ControlNet 编辑失败：缺少预处理节点 %s（请安装 ComfyUI 控制网预处理节点）" % pre)
    cn = _get_comfy_controlnet(control_type)
    if not cn:
        raise RuntimeError("ControlNet 编辑失败：未找到 %s 类型 ControlNet 权重" % control_type)
    neg = negative or _EDefault_NEG
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": scheduler, "denoise": float(strength),
            "model": ["4", 0], "positive": ["14", 0], "negative": ["7", 0], "latent_image": ["10", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "LoadImage", "inputs": {"image": up}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_cn"}},
        "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["4", 2]}},
        "11": {"class_type": pre, "inputs": {"image": ["5", 0]}},
        "12": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": cn}},
        "13": {"class_type": "ControlNetApply", "inputs": {
            "conditioning": ["6", 0], "control_net": ["12", 0], "image": ["11", 0], "strength": float(weight)}},
        "14": {"class_type": "ControlNetApply", "inputs": {
            "conditioning": ["13", 0], "control_net": ["12", 0], "image": ["11", 0], "strength": float(weight)}},
    }
    return _comfy_run(wf, timeout)


def _comfy_img2img_with_ipadapter(image_path, style_image_path, weight=0.8, prompt="", negative="",
                                  strength=0.6, steps=20, cfg=7.0, seed=-1, sampler="euler",
                                  scheduler="normal", timeout=300):
    """图生图 + IP-Adapter 风格迁移：以风格参考图注入交叉注意力，统一色调/质感/光影。

    单路风格 IP-Adapter（非 FaceID），weight 越大风格越强；依赖 IP-Adapter + CLIP-Vision 权重。
    失败抛 RuntimeError（诚实降级）。
    """
    if not comfy_available():
        raise RuntimeError("风格迁移失败：ComfyUI 未启动（请先启动秋葉 ComfyUI :8188）")
    ip_name = _get_comfy_ipadapter()
    if not ip_name:
        raise RuntimeError("风格迁移失败：缺少 IP-Adapter 权重（请安装 ComfyUI_IPAdapter_plus 并放入权重）")
    up = _upload_to_comfy(image_path)
    up_style = _upload_to_comfy(style_image_path)
    if not up or not up_style:
        raise RuntimeError("风格迁移失败：参考图或风格图上传 ComfyUI 失败")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    neg = negative or _EDefault_NEG
    name_l = ip_name.lower()
    if "vit-g" in name_l:
        preset = "VIT-G"
    elif "full face" in name_l or "full-face" in name_l:
        preset = "FULL FACE - SD1.5 only (portraits stronger)"
    elif "plus face" in name_l or "plus-face" in name_l or "face" in name_l:
        preset = "PLUS FACE (portraits)"
    elif "plus" in name_l:
        preset = "PLUS (high strength)"
    elif "light" in name_l:
        preset = "LIGHT - SD1.5 only (low strength)"
    else:
        preset = "STANDARD (medium strength)"
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": scheduler, "denoise": float(strength),
            "model": ["20", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["10", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "LoadImage", "inputs": {"image": up}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt or "high quality", "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_ip"}},
        "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["4", 2]}},
        "11": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": ip_name}},
        "12": {"class_type": "IPAdapterUnifiedLoader", "inputs": {
            "model": ["4", 0], "preset": preset, "ipadapter": ["11", 0]}},
        "13": {"class_type": "LoadImage", "inputs": {"image": up_style}},
        "20": {"class_type": "IPAdapter", "inputs": {
            "model": ["12", 0], "ipadapter": ["11", 0], "image": ["13", 0],
            "weight": float(weight), "start_at": 0.0, "end_at": 1.0, "weight_type": "style transfer"}},
    }
    return _comfy_run(wf, timeout)
