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

import comfy_env  # 秋葉 ComfyUI 根目录 + 权重自动发现（手册 §5.1/§5.2）

logger = logging.getLogger("ai_local")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUTS = os.path.join(BASE, "outputs")
os.makedirs(OUTPUTS, exist_ok=True)

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
        explicit=explicit, what="底模(checkpoint) 权重")


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
        "clip_vision", ["*.safetensors"], what="CLIP-ViT(clip_vision) 权重")


def _get_comfy_controlnet(control_type="depth"):
    """返回 ControlNet 权重文件名（场景构图约束，手册 §3 场景参考图约束）。

    control_type: depth / canny / openpose / lineart。扫描 models/controlnet
    自动挑首个匹配 *<type>* 的文件；都没有 → 清晰报错。
    """
    patterns = ["*%s*.pth" % control_type, "*%s*.safetensors" % control_type,
                "*.pth", "*.safetensors"]
    return comfy_env.discover_weight(
        "controlnet", patterns, what="ControlNet(%s) 权重" % control_type)


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
        info = json.loads(_http_post_json(COMFY_URL + "/object_info", b"{}", timeout=10))
        if not any(k in " ".join(info.keys()) for k in
                   ("ControlNet", "ControlNetLoader", "ControlNetAux")):
            return False
        for cand in (r"D:\AI\ComfyUI-aki-v3\ComfyUI\models\controlnet",
                     os.path.join(BASE, "models", "controlnet")):
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


def _ollama_generate(prompt, think=False, keep_alive=None, model=None, base_url=None):
    target_model = model or MODEL
    url = (base_url or _get_ollama_url()).rstrip("/") + "/api/generate"
    pl = {"model": target_model, "prompt": prompt, "stream": False, "think": think}
    if keep_alive is not None:
        pl["keep_alive"] = keep_alive
    payload = json.dumps(pl).encode("utf-8")
    body = _http_post_json(url, payload, timeout=180)
    return json.loads(body).get("response", "")


def ollama_chat(prompt, think=False, keep_alive=None, purpose="create"):
    """LLM 统一入口（§9 提示词规范）：按 purpose 读取 settings 决定本地/在线。

    purpose: 'create' | 'planning' | 'extract'，对应 Settings.vue 中 modelSettings 的 key。
    当 settings 中该 purpose 配置为 online 时，走 OpenAI 兼容在线 API；否则走本地 Ollama。
    """
    cfg = _get_llm_model_config(purpose)
    if cfg.get("provider") == "online":
        raw, _ = _call_online_llm(prompt, cfg)
        return raw or ""
    return _ollama_generate(prompt, think=think, keep_alive=keep_alive,
                            model=cfg.get("model"), base_url=cfg.get("apiBase"))


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
            raw = ollama_chat(prompt, purpose="create")
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
            raw = ollama_chat(prompt, purpose="planning")
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
                    seed=-1, timeout=240):
    """调用秋叶 ComfyUI /prompt 出图，返回输出图绝对路径；失败抛 RuntimeError。"""
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, blurry, watermark, text", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg"}},
    }
    return _comfy_run(wf, timeout)


def _comfy_text2img_with_ipadapter(prompt, ref_comfy_name, weight=0.85,
                                   width=1280, height=720, steps=20, cfg=7.0,
                                   seed=-1, timeout=240,
                                   style_comfy_name=None, style_weight=0.4):
    """带 IP-Adapter 的文生图：用参考图（角色定妆照）约束角色一致性（手册 §5.2 身份权重注入）。

    身份路（ref_comfy_name）：把角色参考图注入交叉注意力，weight=base_weight（默认 0.85，
    可由 N03 按年龄在 0.7-0.95 调整），实现真人资产「基础 ID + 风格 LoRA」分层控制。
    风格路（可选 style_comfy_name）：把全局风格参考图（N03 生成）作第二路 IP-Adapter 注入，
    统一色调/质感/光影（手册 §3.2.4），权重低于身份路。

    依赖 ComfyUI_IPAdapter_plus + clip_vision 权重。任何节点不匹配/执行失败都抛 RuntimeError，
    由调用方捕获后回退基础文生图（身份/风格一致性降级为提示词强调，不阻塞生成）。
    """
    if not comfy_available():
        raise RuntimeError("ComfyUI 未启动")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    ip_name = _get_comfy_ipadapter()
    cv_name = _get_comfy_clip_vision()
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            "model": ["10", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, blurry, watermark, text", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg_ip"}},
        "10": {"class_type": "IPAdapter", "inputs": {
            "ipadapter": ["11", 0], "clip_vision": ["12", 0],
            "image": ["13", 0], "model": ["4", 0],
            "weight": float(weight),
            "start_at": 0.0, "end_at": 1.0, "weight_type": "standard"}},
        "11": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": ip_name}},
        "12": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": cv_name}},
        "13": {"class_type": "LoadImage", "inputs": {"image": ref_comfy_name}},
    }
    # 风格路：第二路 IP-Adapter（全局风格参考图），链式接在身份路之后
    if style_comfy_name:
        wf["20"] = {"class_type": "IPAdapter", "inputs": {
            "ipadapter": ["11", 0], "clip_vision": ["12", 0],
            "image": ["23", 0], "model": ["10", 0],
            "weight": float(style_weight),
            "start_at": 0.0, "end_at": 1.0, "weight_type": "standard"}}
        wf["23"] = {"class_type": "LoadImage", "inputs": {"image": style_comfy_name}}
        wf["3"]["inputs"]["model"] = ["20", 0]
    return _comfy_run(wf, timeout)


def _comfy_img2img(first_frame_path, prompt, strength=0.6,
                   width=1280, height=720, steps=20, cfg=7.0,
                   seed=-1, timeout=240):
    """图生图：以首帧(first_frame)为锚点去噪，实现首尾帧像素级承接（手册 §3.3.4 首尾帧锚定）。

    仅在前序分镜尾帧作为下一分镜首帧参考时调用；依赖 ComfyUI 与底模权重，失败抛 RuntimeError。
    """
    if not comfy_available():
        raise RuntimeError("ComfyUI 未启动")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": "euler", "scheduler": "normal", "denoise": float(strength),
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["10", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "LoadImage", "inputs": {"image": _upload_to_comfy(first_frame_path)}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, blurry, watermark, text", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg_anchor"}},
        "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["4", 2]}},
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


def _comfy_text2video(prompt, ref_comfy_name=None, width=1280, height=720,
                      frames=49, seed=-1, timeout=600):
    """视频生成（探测到视频模型时由调用方启用）。

    视频模型节点族差异大且需具体工作流；为「无故障跑通」优先，默认返回 None，
    由调用方回退图片轮播降级。若后续装好 Seedance/Wan，可在此接入对应工作流。
    """
    return None


def _comfy_has_node(node_class):
    """探测 ComfyUI 是否装有指定自定义节点（如 GFPGAN / CodeFormer）；失败返回 False。"""
    try:
        info = json.loads(_http_post_json(COMFY_URL + "/object_info", b"{}", timeout=10))
        return node_class in info
    except Exception:
        return False


def face_restore(image_path, method="GFPGAN", out_path=None):
    """人脸修复（手册 §3.4.4 全局后处理）：经 ComfyUI 的 GFPGAN / CodeFormer 节点修复单图人脸。

    环境要求（缺则诚实报错，绝不静默跳过人脸修复 / 绝不假装已修复）：
      - ComfyUI 已启动（comfy_available）
      - 已安装对应自定义节点（ComfyUI-GBLP/ComfyUI_GFPGAN 或对应 CodeFormer 节点）
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
        return _comfy_run(wf, timeout=180, output_dir=out_path and os.path.dirname(out_path))
    except RuntimeError as e:
        raise RuntimeError("人脸修复(%s)执行失败: %s" % (method, e))


def _comfy_text2img_with_controlnet(prompt, ref_comfy_name, control_type="depth",
                                    weight=1.0, width=1280, height=720, steps=20, cfg=7.0,
                                    seed=-1, timeout=240):
    """带 ControlNet 的文生图：用场景参考图约束构图（深度/姿态/线稿）。

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
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            "model": ["4", 0], "positive": ["14", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _get_comfy_checkpoint()}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, blurry, watermark, text", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg_cn"}},
        "10": {"class_type": "LoadImage", "inputs": {"image": ref_comfy_name}},
        "11": {"class_type": pre, "inputs": {"image": ["10", 0]}},
        "12": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": _get_comfy_controlnet(control_type)}},
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
                    # 首镜锚定：以前序尾帧为起点去噪，保证像素级承接（手册 §3.3.4）
                    img = _comfy_img2img(first_frame, p, strength=0.6, width=W, height=H,
                                         seed=seed, timeout=120)
                elif role_cover and role_cover[1]:
                    img = _comfy_text2img_with_ipadapter(
                        p, role_cover[1], weight=base_w, width=W, height=H, seed=seed,
                        timeout=120, style_comfy_name=style_comfy_name, style_weight=style_weight)
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
    d = _tempfile.mkdtemp(prefix="manju_fr_")
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


def synthesize_project_video(project_id, out_path, options=None):
    """Phase 5（合成全集，KB §05）：把所有「已生成成功」片段拼接为完整 MP4，并按 options 叠加后期：

      - tts=True：收集各片段 shot.voiceover/dialogue 作为旁白，TTS 生成音轨并混流（REDUN-01 降级）
      - bgm=True 且提供 bgm_path：背景音乐循环铺底混流
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

        # Step 3：背景音乐（循环铺底，REDUN-01 降级）
        if do_bgm and bgm_path and os.path.isfile(bgm_path):
            bgm_aac = os.path.join(OUTPUTS, "synth_bgm_%s.aac" % project_id)
            subprocess.run([FFMPEG_BIN, "-nostdin", "-y", "-stream_loop", "-1", "-i", bgm_path,
                            "-t", "%.2f" % total_dur, "-c", "copy", bgm_aac],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
            if os.path.isfile(bgm_aac):
                if audio_final:
                    mixed = os.path.join(OUTPUTS, "synth_mix_%s.aac" % project_id)
                    subprocess.run([FFMPEG_BIN, "-nostdin", "-y", "-i", audio_final, "-i", bgm_aac,
                                    "-filter_complex", "amix=inputs=2:duration=longest:dropout_transition=0",
                                    mixed],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=120)
                    if os.path.isfile(mixed):
                        temp_files.append(bgm_aac); temp_files.append(mixed)
                        audio_final = mixed
                    else:
                        temp_files.append(bgm_aac)
                        logger.warning("合成全集：BGM 混流失败，仅保留旁白音轨")
                else:
                    audio_final = bgm_aac
                    temp_files.append(bgm_aac)

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
        # OPS-01：释放所有临时文件（基础视频/音频/SRT/列表），仅保留最终 out_path
        for tf in temp_files:
            try:
                if os.path.isfile(tf):
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
