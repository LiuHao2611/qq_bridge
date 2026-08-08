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

logger = logging.getLogger("ai_local")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUTS = os.path.join(BASE, "outputs")
os.makedirs(OUTPUTS, exist_ok=True)

OLLAMA_URL = "http://127.0.0.1:11434"
COMFY_URL = "http://127.0.0.1:8188"
MODEL = os.environ.get("MANJU_OLLAMA_MODEL", "qwen3.5:9b")


def ollama_available():
    """检查 Ollama 可用且目标模型已加载（不仅检查 API 存活，还验证模型存在）。"""
    try:
        models = ollama_list_models()
        if MODEL not in models:
            logger.warning("[ollama] API 存活但模型 %s 未加载（可用模型: %s）"
                           % (MODEL, models or "无"))
            return False
        return True
    except Exception:
        return False


def ollama_list_models():
    """返回 Ollama 本地已加载模型名列表；服务不可用时返回 []（不抛错）。"""
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=3) as r:
            if r.status != 200:
                return []
            data = json.loads(r.read().decode("utf-8", "ignore"))
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def comfy_available():
    try:
        with urllib.request.urlopen(COMFY_URL + "/system_stats", timeout=3) as r:
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
    p = shutil.which("ffmpeg")
    if p:
        return p
    # 个人本地版：FFmpeg 常见安装位置（不依赖 PATH）
    for cand in (
        r"D:\AI\tools\ffmpeg\ffmpeg-8.1.2-essentials_build\bin\ffmpeg.exe",
    ):
        if os.path.isfile(cand):
            return cand
    return "ffmpeg"  # 退回 PATH（若仍缺失会诚实报错）


FFMPEG_BIN = _resolve_ffmpeg()

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


def _ollama_generate(prompt, think=False, keep_alive=None):
    pl = {"model": MODEL, "prompt": prompt, "stream": False, "think": think}
    if keep_alive is not None:
        pl["keep_alive"] = keep_alive
    payload = json.dumps(pl).encode("utf-8")
    body = _http_post_json(OLLAMA_URL + "/api/generate", payload, timeout=180)
    return json.loads(body).get("response", "")


def ollama_chat(prompt, think=False, keep_alive=None):
    """LLM 统一入口（§9 提示词规范）：内部复用 _ollama_generate。

    满足业务对 ai_local.ollama_chat() 的调用约定；所有提示词都经 build_prompt 生成。
    """
    return _ollama_generate(prompt, think=think, keep_alive=keep_alive)


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
    """从 LLM 输出中截取首个 JSON 数组/对象块（容错：忽略解释性文字）。"""
    if not text:
        return None
    s = text.strip()
    start, end = s.find("["), s.rfind("]")
    if start >= 0 and end > start:
        return s[start:end + 1]
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        return s[start:end + 1]
    return None


def _split_story_prompt(story):
    """构造分场提示词，本地/在线模型共用。"""
    return build_prompt(
        role="你是一个专业短剧编剧 / 分场导演",
        objective=(
            "把下面长故事拆成若干个「场次」，严格输出 JSON 数组。每个元素字段：\n"
            "  title(场次标题), duration(秒, 取值 5/8/10/12/15), scene_description(场景描述),\n"
            "  characters(出现的中文人名数组), summary(一句话梗概),\n"
            "  shots(3 个分镜数组，每镜含 caption/description/duration/shot_type/camera_movement/refs)\n"
            "故事：\n%s" % story
        ),
        constraints=[
            "严格只输出 JSON 数组，不要任何解释文字",
            "每场 shots 固定 3 个，时长之和 = duration",
            "shot_type∈{wide,medium,close_up,extreme_close_up,full,long}; camera_movement∈{static,pan,tilt,dolly,zoom,follow}",
        ],
        invariants=[
            "不新增故事里未出现过的角色",
            "保留原始情节先后顺序",
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
    """把 LLM 返回的 JSON 数组规范化为标准 segment 结构。"""
    if not isinstance(arr, list) or not arr:
        return None, "解析结果为空或非数组"
    out = []
    for i, seg in enumerate(arr[:30]):
        if not isinstance(seg, dict):
            continue
        dur = int(seg.get("duration") or 10)
        if dur not in (5, 8, 10, 12, 15):
            dur = 10
        shots = seg.get("shots") or []
        if not isinstance(shots, list) or not shots:
            shots = [{"caption": "镜头", "description": seg.get("scene_description", "")[:80],
                      "duration": dur, "shot_type": "medium", "camera_movement": "static", "refs": []}]
        seg["title"] = seg.get("title") or ("场次 %02d" % (i + 1))
        seg["duration"] = dur
        seg["scene_description"] = seg.get("scene_description") or ""
        seg["summary"] = seg.get("summary") or ""
        seg["characters"] = seg.get("characters") or []
        seg["shots"] = shots
        out.append(seg)
    return out, None


# ----------------------------- 资产提取（Phase 1 · 一键从剧本提取） -----------------------------

def _extract_assets_prompt(story):
    return build_prompt(
        role="你是专业短剧资产整理助手，擅长从剧本/故事中识别并归类视觉资产",
        objective=(
            "从下面剧本/故事中提取三类视觉资产，严格只输出一个 JSON 对象，不要任何解释文字：\n"
            "  roles: [{name(中文人名), role_type(主角=protagonist/配角=supporting/反派=antagonist), "
            "           description(50字内外貌/身份/气质), stage_hint(可选年龄阶段：childhood/juvenile/youth/middle_age/old_age)}]\n"
            "  scenes: [{name(场景名), description(50字内环境/氛围/光线)}]\n"
            "  props: [{name(道具名), description(30字内形态/作用)}]\n"
            "剧本/故事：\n%s" % story[:12000]
        ),
        constraints=[
            "严格只输出 JSON，不要 Markdown 代码块、不要解释",
            "角色名必须是剧本中实际出现的人名，不得虚构",
            "场景是地点/环境，不要把它列为道具",
            "道具是具体物品，不要把角色或场景列为道具",
            "每个类别如果为空则返回空数组",
        ],
        invariants=[
            "不新增故事里未出现过的角色、场景或道具",
            "role_type 必须是 protagonist/supporting/antagonist 之一",
            "stage_hint 如提供，必须是 childhood/juvenile/youth/middle_age/old_age 之一",
        ],
    )


def _normalize_extracted_assets(obj):
    """把 LLM 提取结果规范化为标准结构，并去重/截断。"""
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
    model_config = model_config or {}
    provider = model_config.get("provider") or "local"
    model = model_config.get("model") or MODEL
    meta = {"model": model, "source": "llm", "provider": provider}
    prompt = _extract_assets_prompt(story)
    start = time.time()
    raw = None
    try:
        if provider == "local":
            if not ollama_available():
                meta["error"] = "Ollama 服务未启动或目标模型 %s 未加载" % model
                return {"roles": [], "scenes": [], "props": [], "meta": meta}
            raw = ollama_chat(prompt)
        else:
            raw, online_meta = _call_online_llm(prompt, model_config)
            meta.update(online_meta or {})
    except Exception as e:
        meta["error"] = "调用模型异常：%s" % e
        meta["duration_ms"] = int((time.time() - start) * 1000)
        return {"roles": [], "scenes": [], "props": [], "meta": meta}
    meta["duration_ms"] = int((time.time() - start) * 1000)
    if not raw:
        if not meta.get("error"):
            meta["error"] = "模型返回为空"
        return {"roles": [], "scenes": [], "props": [], "meta": meta}
    meta["raw_preview"] = raw[:800]
    block = _extract_json_block(raw)
    if not block:
        meta["error"] = "无法从模型输出中解析 JSON 块"
        return {"roles": [], "scenes": [], "props": [], "meta": meta}
    try:
        obj = json.loads(block)
    except Exception as e:
        meta["error"] = "JSON 解析失败：%s" % e
        return {"roles": [], "scenes": [], "props": [], "meta": meta}
    out = _normalize_extracted_assets(obj)
    out["meta"] = meta
    meta["count"] = len(out["roles"]) + len(out["scenes"]) + len(out["props"])
    return out


def split_story_with_llm(story, model_config=None):
    """Phase 2：用本地 Ollama 或在线模型把长故事拆为场次（含角色/场景/分镜）。

    model_config: dict，可选字段 provider/local/openai-compatible、model、apiBase、apiKey。
    成功返回 {"segments": [...], "meta": {...}}；失败返回 {"segments": None, "meta": {...}}。
    meta 含 model/raw_preview/duration_ms/provider/error，供前端展示推理过程与回退原因。
    """
    model_config = model_config or {}
    provider = model_config.get("provider") or "local"
    model = model_config.get("model") or MODEL
    meta = {"model": model, "source": "llm", "provider": provider}
    prompt = _split_story_prompt(story)
    start = time.time()
    raw = None
    try:
        if provider == "local":
            if not ollama_available():
                meta["error"] = "Ollama 服务未启动或目标模型 %s 未加载" % model
                return {"segments": None, "meta": meta}
            raw = ollama_chat(prompt)
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
        output_dir = r"D:\AI\ComfyUI-aki-v3\ComfyUI\output"
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
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": COMFY_CKPT}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, blurry, watermark, text", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg"}},
    }
    return _comfy_run(wf, timeout)


def _comfy_text2img_with_ipadapter(prompt, ref_comfy_name, weight=0.7,
                                   width=1280, height=720, steps=20, cfg=7.0,
                                   seed=-1, timeout=240):
    """带 IP-Adapter 的文生图：用参考图（角色定妆照）约束角色一致性。

    依赖 ComfyUI_IPAdapter_plus + clip_vision 权重（本机已具备）。任何节点不匹配/执行失败都抛
    RuntimeError，由调用方捕获后回退基础文生图（角色一致性降级为提示词强调，不阻塞生成）。
    """
    if not comfy_available():
        raise RuntimeError("ComfyUI 未启动")
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    wf = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": float(cfg),
            "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            "model": ["10", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": COMFY_CKPT}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, blurry, watermark, text", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg_ip"}},
        "10": {"class_type": "IPAdapter", "inputs": {
            "ipadapter": ["11", 0], "clip_vision": ["12", 0],
            "image": ["13", 0], "model": ["4", 0], "weight": float(weight)}},
        "11": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_name": "ip-adapter_sd15.safetensors"}},
        "12": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "CLIP-ViT-H-14-laion2B-s32B-b4K.safetensors"}},
        "13": {"class_type": "LoadImage", "inputs": {"image": ref_comfy_name}},
    }
    return _comfy_run(wf, timeout)


def _comfy_text2video(prompt, ref_comfy_name=None, width=1280, height=720,
                      frames=49, seed=-1, timeout=600):
    """视频生成（探测到视频模型时由调用方启用）。

    视频模型节点族差异大且需具体工作流；为「无故障跑通」优先，默认返回 None，
    由调用方回退图片轮播降级。若后续装好 Seedance/Wan，可在此接入对应工作流。
    """
    return None


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
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": COMFY_CKPT}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, blurry, watermark, text", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "manju_seg_cn"}},
        "10": {"class_type": "LoadImage", "inputs": {"image": ref_comfy_name}},
        "11": {"class_type": pre, "inputs": {"image": ["10", 0]}},
        "12": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "control_v11p_sd15_%s.pth" % control_type}},
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
    role_cover = None   # (local_path, comfy_name)
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
                    role_cover = (path, _upload_to_comfy(path))
                elif atype == "scene" and scene_cover is None:
                    scene_cover = (path, _upload_to_comfy(path))
        finally:
            _conn.close()
    except Exception as e:
        logger.warning("[gen] 资产引用读取失败（降级为无参考图）: %s" % e)

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
        for _ in shots:
            p = en_prompt
            img = None
            try:
                if role_cover and role_cover[1]:
                    img = _comfy_text2img_with_ipadapter(p, role_cover[1], width=W, height=H, seed=seed, timeout=120)
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
        return "fail", "FFmpeg 合成超时（180s）"
    except Exception as e:
        return "fail", "FFmpeg 合成失败: %s" % e

    if not os.path.isfile(out_path):
        return "fail", "视频文件未生成"
    return level, note


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


def synthesize_project_video(project_id, out_path, options=None):
    """Phase 5（合成全集，KB §05）：把所有「已生成成功」片段拼接为完整 MP4，并按 options 叠加后期：

      - tts=True：收集各片段 shot.voiceover/dialogue 作为旁白，TTS 生成音轨并混流（REDUN-01 降级）
      - bgm=True 且提供 bgm_path：背景音乐循环铺底混流
      - subtitle=True：把各片段对话烧录为硬字幕（中文需字体，失败则降级跳过）
    默认仅视频拼接（-c copy）。任一后期失败均优雅降级（REDUN-01），绝不阻断主流程。
    返回 (level, note)，与 generate_segment_video 约定一致。
    """
    import db
    from urllib.parse import urlparse, parse_qs
    options = options or {}
    do_tts = bool(options.get("tts"))
    do_bgm = bool(options.get("bgm"))
    do_sub = bool(options.get("subtitle"))
    bgm_path = options.get("bgm_path") or ""
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
                'SELECT "index", duration, voiceover, dialogue FROM shots '
                'WHERE segment_id=? ORDER BY "index" ASC', (s["segment_id"],))
        finally:
            c2.close()
        parts = []
        sub_start = cursor
        seg_dur = dur
        n = len(shots) or 1
        for j, sh in enumerate(shots):
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
        clips.append((s.get("index") or 0, fpath, dur, narration, sub_entries))
        cursor += dur

    if not clips:
        return "fail", "无可用片段视频（请先让全部片段生成成功）"

    clips.sort(key=lambda x: x[0])
    total_dur = sum(c[2] for c in clips)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    concat_list = os.path.join(OUTPUTS, "synth_concat_%s.txt" % project_id)
    with open(concat_list, "w", encoding="utf-8") as f:
        for _, c, _, _, _ in clips:
            f.write("file '%s'\n" % c.replace("\\", "/"))
    temp_files.append(concat_list)

    # Step 1：基础视频拼接（含原声）
    base_mp4 = out_path + ".base.mp4"
    temp_files.append(base_mp4)
    try:
        subprocess.run([FFMPEG_BIN, "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
                        "-c", "copy", base_mp4],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=300)
    except subprocess.TimeoutExpired:
        return "fail", "FFmpeg 合成全集超时（300s）"
    except Exception as e:
        return "fail", "FFmpeg 合成全集失败: %s" % e
    if not os.path.isfile(base_mp4):
        return "fail", "合成全集基础视频未生成"

    audio_final = None
    srt_path = None
    try:
        # Step 2：旁白 TTS（REDUN-01 降级）
        if do_tts:
            seg_audios = []
            cc = 0.0
            for _, _, dur, narration, _ in clips:
                if narration:
                    seg_wav = os.path.join(OUTPUTS, "synth_tts_%s_%d.wav" % (project_id, len(seg_audios)))
                    if _tts_to_file(narration, seg_wav) and os.path.isfile(seg_wav):
                        padded = os.path.join(OUTPUTS, "synth_pad_%s_%d.wav" % (project_id, len(seg_audios)))
                        subprocess.run([FFMPEG_BIN, "-nostdin", "-y", "-i", seg_wav,
                                        "-af", "apad", "-t", "%.2f" % dur, padded],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=120)
                        if os.path.isfile(padded):
                            seg_audios.append(padded)
                            temp_files.append(seg_wav); temp_files.append(padded)
                cc += dur
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
            for _, _, _, _, sub_entries in clips:
                all_subs.extend(sub_entries)
            if all_subs:
                srt_path = os.path.join(OUTPUTS, "synth_sub_%s.srt" % project_id)
                with open(srt_path, "w", encoding="utf-8") as f:
                    f.write(_build_srt(all_subs))
                temp_files.append(srt_path)

        # Step 5：最终封装
        need_reencode = bool(srt_path)  # 字幕烧录必须重编码
        if srt_path:
            vf = "subtitles='%s'" % srt_path.replace("\\", "/").replace(":", "\\:")
            cmd = [FFMPEG_BIN, "-nostdin", "-y", "-i", base_mp4]
            if audio_final:
                cmd += ["-i", audio_final]
                cmd += ["-filter_complex", "[0:v]%s[v]" % vf, "-map", "[v]", "-map", "1:a:0"]
            else:
                cmd += ["-filter_complex", "[0:v]%s[v]" % vf, "-map", "[v]", "-map", "0:a:0"]
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
            else:
                os.replace(base_mp4, out_path)  # 仅视频拼接：直接采用基础拼接结果
                temp_files.remove(base_mp4)
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
        "model": COMFY_CKPT,
        "note": note,
    }
