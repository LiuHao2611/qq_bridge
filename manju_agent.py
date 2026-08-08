#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
漫剧自动生成 Agent（底层工作流引擎）

输入: 一段剧本 / 或一句话主题(极简模式) / 或上传 docx 分镜脚本
输出: 拆分镜头 -> Ollama 写 SD 提示词(可预览/编辑) -> ComfyUI 出图/出动画 -> FFmpeg 合成带字幕视频

能力:
  - 底模可切换(默认 MeinaMix 动漫漫剧风；可换 Realistic_Vision 写实模型)
  - 提示词预览/编辑(出图前先看到英文 prompt，解决图文不符)
  - IP-Adapter 角色一致性(上传角色参考图 -> 每镜脸一致)
  - AnimateDiff 真动画(每镜生成几秒动画，不再是 PPT)
  - 出图前自动让出 Ollama 显存(1660Ti 6GB 跑 AnimateDiff 必需)

引擎: Ollama(qwen3.5:9b) + ComfyUI(秋叶版 8188) + FFmpeg
仅依赖 Python 标准库 + 可选自定义节点(AnimateDiff/IP-Adapter/VideoHelperSuite)。
"""
import sys, os, re, json, time, urllib.request, urllib.parse, subprocess, threading, shutil, zipfile

OLLAMA_URL = "http://localhost:11434/api/generate"
# 1660Ti 仅 6GB 显存: 9B LLM(5.6GB) 与 2GB 底模无法同时在显存共存。
# 因此做「分阶段让卡」:
#  - 规划(拆镜)阶段 ComfyUI 通常未启动(用户手动开)，GPU 空闲 -> Ollama 用 GPU(-1) 大幅加速；
#  - 出图阶段 Ollama 先 unload_ollama() 释放显存，整块 GPU 让给 ComfyUI。
# 若 ComfyUI 正在占用 GPU(8188 在线)，则回退 CPU(OLLAMA_NUM_GPU) 避免 OOM。
OLLAMA_MODEL = "qwen3.5:9b"
OLLAMA_NUM_GPU = 0  # ComfyUI 占用 GPU 时的回退值


def _comfy_up():
    """探测 ComfyUI(8188) 是否在线，决定 Ollama 能否占用 GPU。"""
    try:
        import socket
        s = socket.create_connection(("127.0.0.1", 8188), timeout=1.5)
        s.close()
        return True
    except Exception:
        return False


def _ollama_num_gpu():
    return -1 if not _comfy_up() else OLLAMA_NUM_GPU
COMFY_URL  = "http://127.0.0.1:8188"

# 秋叶 ComfyUI 的真实模型目录(嵌套了一层 ComfyUI/)
COMFY_ROOT = r"D:\AI\ComfyUI-aki-v3\ComfyUI"
MODELS_DIR = os.path.join(COMFY_ROOT, "models")

# 默认底模: 动漫模型 MeinaMix(2GB, 6GB 显存可跑动画)；写实模型 Realistic_Vision 仍在下拉可选
CKPT = "Realistic_Vision_V6.0_NV_B1_fp16.safetensors"

# 动画/一致性相关文件名(下载后放到对应目录，缺省则自动回退)
MOTION_MODEL = "mm_sd_v15_v2.ckpt"          # AnimateDiff 运动模块 -> models/animatediff_models/
IPADAPTER_MODEL = "ip-adapter_sd15.safetensors"  # -> models/ipadapter/
CLIP_VISION = "CLIP-ViT-H-14-laion2B-s32B-b4K.safetensors"  # -> models/clip_vision/

W, H = 768, 432  # 16:9 视频分辨率(出图)；动画建议降到 512x512 更稳
BASE = os.environ.get("MANJU_APP_ROOT") or os.path.dirname(os.path.abspath(__file__))


# ----------------------------- 模型可用性探测 -----------------------------
def _list_files(folder, exts):
    if not os.path.isdir(folder):
        return []
    out = []
    for f in os.listdir(folder):
        if any(f.lower().endswith(e) for e in exts):
            out.append(f)
    return sorted(out)

def list_checkpoints():
    return _list_files(os.path.join(MODELS_DIR, "checkpoints"), (".safetensors", ".ckpt"))

def list_motion_models():
    return _list_files(os.path.join(MODELS_DIR, "animatediff_models"), (".safetensors", ".ckpt", ".pt"))

def list_ipadapter_models():
    return _list_files(os.path.join(MODELS_DIR, "ipadapter"), (".safetensors", ".ckpt", ".bin"))

def list_clip_vision():
    return _list_files(os.path.join(MODELS_DIR, "clip_vision"), (".safetensors", ".ckpt"))


# ----------------------------- Ollama 调用 -----------------------------
SYS_PROMPT = """你是一个AI漫剧分镜助手。用户会给你一段剧情描述(可能是一段中文梗概，或按行写好的场景，或详细分镜脚本)。
请把它拆分成若干个镜头(scene)，为每个镜头生成 Stable Diffusion(SD1.5)英文生图提示词，并一并给出导演创意指导所需的元数据。
输出严格 JSON，不要 markdown，结构如下:
{
  "title": "剧集标题",
  "scenes": [
    {"scene_id": 1, "caption": "该镜头的字幕(简短中文，可含台词)", "description": "中文画面描述",
     "prompt": "英文 positive prompt, comma separated tags", "negative_prompt": "英文 negative prompt", "duration": 4}
  ],
  "characters": [{"name":"角色名","relation":"与主角关系/身份","age":"青年/中年/老年","notes":"外貌与性格备注(一句话)"}],
  "palette": [{"name":"主色/辅色等","color":"#RRGGBB"}],
  "story": {"setting":"故事背景/年代/地点(一句话)","timeline":"关键时间线或事件(一句话)","notes":"基调/类型(一句话)"},
  "anchors": ["必须贯穿全片的硬设定1(人名/地点/关键道具)","..."]
}
要求:
1. prompt 用逗号分隔的英文标签(25~45 个标签词即可)，突出: 人物(外貌/衣着/表情)、场景、动作、镜头语言、光线、风格(anime illustration, 2d animation, cel shading)。
2. negative_prompt 用英文，至少包含: 'realistic, photo, 3d render, lowres, bad anatomy, extra limbs, mutated hands, worst quality, blurry, watermark, text, deformed'。
3. duration 为 3-6 的整数秒；镜头数 3-6 个(若剧本已明确列出镜头则尽量保留)。
4. 若剧本是详细分镜脚本，优先从「【镜头N】」「镜头N」或分镜表提取「场景描述」和「动作/台词」，忽略摄影指导、音效、情绪标记、隐喻解读、制作备忘等制作备注。
5. 必须保留原剧本中的人物姓名、关键道具、场景地点、情绪基调，不要擅自替换。
6. characters 提取有名字或有明确特征的主要角色 3-6 个，保留原姓名；palette 提供 4 个十六进制色值(含#)贴合基调；anchors 列出 3-5 条全片须保持一致的硬设定。
"""

EXPAND_PROMPT = """你是一个短视频/漫剧编剧。用户只给了一个主题或一句话，
请把它扩写成一段适合 AI 生图的剧情梗概(中文，3-6 个场景，每场景一两句话描述画面与情绪)。
只输出正文，不要解释、不要英文、不要分镜参数。"""

ENRICH_PROMPT = (
    "你是一个为『国产AI漫剧 / 动态漫画(donghua / animated webtoon)』写 Stable Diffusion(SD1.5)提示词的工程师。\n"
    "请为以下短剧镜头生成英文生图提示词。\n"
    "要求:\n"
    "1. 逗号分隔的英文标签;\n"
    "2. 风格必须是 2D 动画/动漫插画风(anime illustration, 2d animation, vibrant colors, clean lineart, cel shading);\n"
    "3. 必须包含并突出: 人物(姓名/年龄/外貌/发型/衣着/表情/肢体动作)、场景地点、镜头语言(景别/运镜)、光线氛围、情绪;\n"
    "4. positive prompt 控制在 60~120 个标签词以内, 信息密度高, 避免空洞形容词;\n"
    "5. negative_prompt 用英文名, 至少包含: 'realistic, photo, 3d render, lowres, bad anatomy, extra limbs, mutated hands, worst quality, blurry, watermark, text, deformed';\n"
    "6. 只输出 JSON 数组, 不要 markdown, 不要解释。每个元素字段: scene_id(整数), prompt(英文 positive), negative_prompt(英文 negative)。\n\n"
)


def _ollama_raw(prompt, temperature=0.6, maxtoken=4096, timeout=600, num_gpu=None):
    """通用 Ollama 文本生成，关闭思考链，返回纯文本。num_gpu=None 时按 GPU 占用情况自动选择。"""
    if num_gpu is None:
        num_gpu = _ollama_num_gpu()
    body = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature, "num_predict": maxtoken,
                    "num_gpu": num_gpu},
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    return resp.get("response", "").strip()


def _strip_md(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text.strip()


def _ollama_stream(prompt, on_chunk=None, temperature=0.6, maxtoken=1500, timeout=600, num_gpu=None):
    """流式 Ollama 文本生成，关闭思考链，逐块回调 on_chunk(text)，最终返回完整文本。"""
    if num_gpu is None:
        num_gpu = _ollama_num_gpu()
    body = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": True,
        "think": False,
        "options": {"temperature": temperature, "num_predict": maxtoken, "num_gpu": num_gpu},
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    full = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            piece = obj.get("response", "")
            if piece:
                full.append(piece)
                if on_chunk:
                    try:
                        on_chunk("".join(full))
                    except Exception:
                        pass
            if obj.get("done"):
                break
    return "".join(full).strip()


def call_ollama(script, maxtoken=1500, on_chunk=None):
    """调用 Ollama 把剧情拆成结构化镜头(JSON)，含导演元数据(合并输出，减少调用次数)。"""
    return json.loads(_strip_md(_ollama_stream(
        SYS_PROMPT + "\n\n剧情:\n" + script, temperature=0.6, maxtoken=maxtoken, on_chunk=on_chunk)))


def expand_theme(theme, on_chunk=None):
    return _ollama_stream(EXPAND_PROMPT + "\n\n主题: " + theme, temperature=0.85, maxtoken=1200, on_chunk=on_chunk)


def unload_ollama():
    """出图前让 Ollama 释放资源(1660Ti 6GB 显存紧张；CPU 模式主要释放 RAM)。失败静默。"""
    try:
        body = {"model": OLLAMA_MODEL, "prompt": " ", "stream": False,
                "think": False, "keep_alive": 0}
        req = urllib.request.Request(
            OLLAMA_URL, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass


# ----------------------------- docx 解析 -----------------------------
def extract_docx(path):
    """用标准库从 docx 中提取段落文本和表格（保持文档顺序）。"""
    import xml.etree.ElementTree as ET
    NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(f"{NS}body")
    paragraphs, tables = [], []
    for child in body:
        if child.tag == f"{NS}p":
            texts = [t.text for t in child.iter(f"{NS}t") if t.text]
            paragraphs.append("".join(texts))
        elif child.tag == f"{NS}tbl":
            rows = []
            for tr in child.iter(f"{NS}tr"):
                row = []
                for tc in tr.iter(f"{NS}tc"):
                    cell_texts = [t.text for t in tc.iter(f"{NS}t") if t.text]
                    row.append("".join(cell_texts))
                rows.append(row)
            tables.append(rows)
    return paragraphs, tables


def _extract_title(paragraphs):
    for p in paragraphs[:10]:
        p = p.strip()
        if p.startswith("《") and "》" in p:
            return p
        if "第" in p and "集" in p and len(p) < 40:
            return p
    return "漫剧"


def parse_scenes_from_tables(tables):
    """从分镜表中提取镜头：镜头 / 场景描述 / 动作台词 / 时长。"""
    for table in tables:
        if not table or len(table) < 2:
            continue
        header = [c.strip() for c in table[0]]
        has_scene = any("镜头" in h for h in header)
        has_desc = any("场景" in h or "描述" in h for h in header)
        if not (has_scene and has_desc):
            continue
        idx_scene = next((i for i, h in enumerate(header) if "镜头" in h), None)
        idx_desc = next((i for i, h in enumerate(header) if "场景" in h or "描述" in h), None)
        idx_action = next((i for i, h in enumerate(header) if "动作" in h or "台词" in h or "对白" in h), None)
        idx_dur = next((i for i, h in enumerate(header) if "时长" in h or "时间" in h), None)
        scenes = []
        for row in table[1:]:
            if not row:
                continue
            first = row[0].strip() if row[0] else ""
            if not first or first.lower() in ("镜头", "scene", "镜号"):
                continue
            desc = row[idx_desc].strip() if idx_desc is not None and idx_desc < len(row) else ""
            action = row[idx_action].strip() if idx_action is not None and idx_action < len(row) else ""
            dur_s = row[idx_dur].strip() if idx_dur is not None and idx_dur < len(row) else "4s"
            m = re.search(r"\d+", dur_s)
            dur = int(m.group()) if m else 4
            caption = action[:160] if action else desc[:160]
            if not desc and not action:
                continue
            scenes.append({
                "scene_id": len(scenes) + 1,
                "caption": caption,
                "description": desc,
                "duration": max(1, min(60, dur)),
            })
        if scenes:
            return scenes
    return None


def parse_scenes_from_text(text):
    """当没有标准分镜表时，尝试从 【镜头N】 段落中提取。"""
    pattern = re.compile(r"【镜头\s*(\d+)】(.+?)(?=【镜头\s*\d+】|$)", re.S)
    matches = pattern.findall(text)
    if not matches:
        return None
    scenes = []
    for num_str, block in matches:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        desc = ""
        action = ""
        dur = 4
        for i, line in enumerate(lines):
            if line.startswith("场景描述"):
                desc = lines[i + 1] if i + 1 < len(lines) else ""
            elif line.startswith("动作/台词") or line.startswith("动作"):
                action = lines[i + 1] if i + 1 < len(lines) else ""
            elif "时长" in line:
                m = re.search(r"\d+", line)
                if m:
                    dur = int(m.group())
        if not desc:
            desc = " ".join(lines[:3])
        caption = action[:160] if action else desc[:160]
        scenes.append({
            "scene_id": int(num_str),
            "caption": caption,
            "description": desc,
            "duration": max(1, min(60, dur)),
        })
    return scenes if scenes else None


def enrich_scenes_with_prompts(scenes, title="", on_progress=None, batch_size=1):
    """逐镜调用 Ollama 为每个镜头生成英文 SD 提示词。

    采用逐镜生成（默认 batch_size=1）而非批量：
    - 进度可见：前端轮询能看到 1/16、2/16… 逐步推进，不会卡在某一整批；
    - 稳定：单镜返回单个 JSON 对象，无批量 scene_id 匹配歧义，避免后半段提示词退化；
    - 容错：单镜失败仅该镜回退，不影响其他镜。
    """
    if not scenes:
        return scenes
    neg_base = "realistic, photo, 3d render, lowres, bad anatomy, extra limbs, mutated hands, worst quality, blurry, watermark, text, deformed"
    total = len(scenes)
    base_rules = (
        "你是一个为『国产AI漫剧 / 动态漫画』写 Stable Diffusion(SD1.5)提示词的工程师。\n"
        "要求: 逗号分隔的英文标签; 风格必须是 2D 动画/动漫插画风(anime illustration, 2d animation, vibrant colors, clean lineart, cel shading);\n"
        "必须包含并突出: 人物(姓名/年龄/外貌/发型/衣着/表情/肢体动作)、场景地点、镜头语言(景别/运镜)、光线氛围、情绪;\n"
        "positive prompt 控制在 60~120 个标签词以内; negative_prompt 至少包含: '" + neg_base + "';\n"
        "只输出这一个镜头的 JSON 对象, 不要 markdown, 不要解释。字段: scene_id(整数), prompt(英文 positive), negative_prompt(英文 negative)。\n\n"
    )

    def gen_single(sc):
        prompt = base_rules + (f"剧集: {title}\n" if title else "")
        prompt += f"镜头{sc['scene_id']} 描述: {sc.get('description','')} | 动作/台词: {sc.get('caption','')}\n"
        prompt += "只输出这一个镜头的 JSON 对象。"
        text = _ollama_raw(prompt, temperature=0.6, maxtoken=1024, timeout=300)
        return json.loads(_strip_md(text))

    processed = 0
    for sc in scenes:
        ok = False
        try:
            item = gen_single(sc)
            if isinstance(item, dict) and item.get("prompt"):
                sc["prompt"] = item.get("prompt")
                sc["negative_prompt"] = item.get("negative_prompt", neg_base) or neg_base
                ok = True
        except Exception:
            ok = False
        if not ok:
            # 兜底：风格前缀 + 原描述，保证有可渲染提示词且不致整段空白
            desc = sc.get("description", "") or sc.get("caption", "")
            sc["prompt"] = "anime illustration, 2d animation, vibrant colors, clean lineart, cel shading, " + desc
            sc["negative_prompt"] = neg_base
        processed += 1
        if on_progress:
            try:
                on_progress(processed, total)
            except Exception:
                pass
    return scenes


# ----------------------------- ComfyUI 交互 -----------------------------
def upload_to_comfyui(path):
    """把本地图片上传到 ComfyUI 的 input 目录，返回 LoadImage 可用的文件名。"""
    with open(path, "rb") as f:
        data = f.read()
    fn = os.path.basename(path)
    boundary = "----WebKitFormBoundaryManjuAI"
    crlf = b"\r\n"
    body = (b"--" + boundary.encode() + crlf +
            ('Content-Disposition: form-data; name="image"; filename="%s"' % fn).encode() + crlf +
            b"Content-Type: application/octet-stream" + crlf + crlf +
            data + crlf +
            b"--" + boundary.encode() + b"--" + crlf)
    req = urllib.request.Request(
        COMFY_URL + "/upload/image", data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())
    return resp.get("name") or fn


def build_workflow(prompt, neg, seed, prefix, ref_image=None, duration=4,
                   ckpt=CKPT, animated=False, motion_model=MOTION_MODEL,
                   ip_ref=None, width=W, height=H):
    """构造 ComfyUI 工作流。
    - ref_image:  静态 img2img 参考图(整体构图/风格参考)
    - ip_ref:     IP-Adapter 角色参考图(脸/角色一致性)
    - animated:   启用 AnimateDiff 生成动画片段(输出经 VHS_VideoCombine 存 mp4)
    返回 (wf_dict, output_node_id, output_kind)  output_kind in {"image","video"}
    """
    wf = {
        "3":  {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "6":  {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["3", 1]}},
        "7":  {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["3", 1]}},
    }
    model_ref = ["3", 0]  # 当前模型来源

    FRAMES = 16 if animated else 1  # AnimateDiff 需要多帧 latent 才能产生运动

    # AnimateDiff: 把运动模块注入模型
    if animated:
        wf["ad"] = {"class_type": "ADE_AnimateDiffLoaderGen1",
                    "inputs": {"model": model_ref, "model_name": motion_model,
                               "beta_schedule": "autoselect"}}
        model_ref = ["ad", 0]

    # IP-Adapter: 角色一致性(用上传的角色参考图)。
    # 必须用 UnifiedLoader 同时载入 ipadapter+clipvision，再交给 IPAdapter 注入。
    # 注意: 该自定义节点注册的 class_type 是 "IPAdapter"(非 "IPAdapterSimple")。
    if ip_ref:
        wf["ip_load"] = {"class_type": "IPAdapterUnifiedLoader", "inputs": {
            "model": model_ref, "preset": "STANDARD (medium strength)"}}
        wf["ip_img"] = {"class_type": "LoadImage", "inputs": {"image": ip_ref}}
        wf["ip_apply"] = {"class_type": "IPAdapter", "inputs": {
            "model": ["ip_load", 0], "ipadapter": ["ip_load", 1],
            "image": ["ip_img", 0], "weight": 0.6,
            "start_at": 0.0, "end_at": 1.0, "weight_type": "standard"}}
        model_ref = ["ip_apply", 0]

    # latent 来源：动画强制 txt2img 多帧(暂不支持动画+img2img)；否则按 img2img/文生图
    if animated:
        wf["5"] = {"class_type": "EmptyLatentImage",
                   "inputs": {"width": width, "height": height, "batch_size": FRAMES}}
        latent_ref = ["5", 0]
    elif ref_image:
        # img2img: 参考图 -> 缩放 -> VAE 编码 -> 低噪声重绘
        wf["load"] = {"class_type": "LoadImage", "inputs": {"image": ref_image}}
        wf["scale"] = {"class_type": "ImageScale", "inputs": {
            "image": ["load", 0], "width": width, "height": height,
            "upscale_method": "bilinear", "crop": "disabled"}}
        wf["encode"] = {"class_type": "VAEEncode", "inputs": {
            "pixels": ["scale", 0], "vae": ["3", 2]}}
        latent_ref = ["encode", 0]
    else:
        wf["5"] = {"class_type": "EmptyLatentImage",
                   "inputs": {"width": W, "height": H, "batch_size": 1}}
        latent_ref = ["5", 0]

    wf["10"] = {"class_type": "KSampler", "inputs": {
        "model": model_ref, "positive": ["6", 0], "negative": ["7", 0],
        "latent_image": latent_ref, "seed": seed, "steps": 20, "cfg": 7.0,
        "sampler_name": "euler", "scheduler": "normal",
        "denoise": 0.55 if (ref_image and not animated) else 1.0}}
    wf["11"] = {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 2]}}

    if animated:
        # 动画: 多帧 -> 合成 mp4(由 VideoHelperSuite 保存)
        fps = max(4, round(FRAMES / max(1, int(duration))))  # 16 帧 / 目标时长 ≈ 帧率
        wf["save"] = {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["11", 0], "frame_rate": fps, "loop_count": 0,
            "filename_prefix": prefix, "format": "video/h264-mp4",
            "pingpong": False, "save_output": True}}
        return wf, "save", "video"
    else:
        wf["9"] = {"class_type": "SaveImage",
                   "inputs": {"images": ["11", 0], "filename_prefix": prefix}}
        return wf, "9", "image"


def generate_media(wf, client_id="manju"):
    """提交工作流并等待完成，返回 (filename, subfolder, type, kind)。"""
    payload = json.dumps({"prompt": wf, "client_id": client_id}).encode()
    req = urllib.request.Request(
        COMFY_URL + "/prompt", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        pid = json.loads(resp.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError("ComfyUI /prompt 拒绝(%s): %s" % (e.code, body))
    for _ in range(200):
        try:
            h = json.loads(urllib.request.urlopen(
                COMFY_URL + "/history/" + pid, timeout=10).read())
        except Exception:
            time.sleep(3); continue
        if pid in h:
            node = h[pid]
            if node.get("status", {}).get("status_str") == "error":
                raise RuntimeError("ComfyUI 出错: " + str(node["status"].get("messages")))
            out = node["outputs"]
            # 找到输出节点(9=image / save=video)
            if "save" in out and out["save"].get("gifs"):
                g = out["save"]["gifs"][0]
                return g["filename"], g.get("subfolder", ""), g.get("type", "output"), "video"
            if "9" in out and out["9"].get("images"):
                im = out["9"]["images"][0]
                return im["filename"], im.get("subfolder", ""), im.get("type", "output"), "image"
            # 兜底
            for nid, val in out.items():
                if isinstance(val, dict) and val.get("images"):
                    im = val["images"][0]
                    return im["filename"], im.get("subfolder", ""), im.get("type", "output"), "image"
        time.sleep(3)
    raise TimeoutError("出图/出动画超时")


def download_file(filename, subfolder, ftype, outpath):
    qs = "filename=" + urllib.parse.quote(filename) + "&subfolder=" + \
         urllib.parse.quote(subfolder) + "&type=" + ftype
    with urllib.request.urlopen(COMFY_URL + "/view?" + qs, timeout=60) as r:
        data = r.read()
    with open(outpath, "wb") as f:
        f.write(data)


def fmt_time(s):
    h = int(s // 3600); m = int((s % 3600) // 60); sec = int(s % 60)
    ms = int(round((s - int(s)) * 1000))
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


META_PROMPT = """你是一个AI短剧导演助理。根据剧名和分镜信息，生成导演创意指导所需的元数据。
只输出严格 JSON，不要 markdown，不要解释。结构如下:
{
  "characters": [{"name":"角色名","relation":"与主角关系/身份","age":"青年/中年/老年","notes":"外貌与性格备注(一句话)"}],
  "palette": [{"name":"主色/辅色等","color":"#RRGGBB"}],
  "story": {"setting":"故事背景/年代/地点(一句话)","timeline":"关键时间线或事件(一句话)"},
  "anchors": ["必须贯穿全片的硬设定1(人名/地点/关键道具)","..."]
}
要求:
1. characters 从剧本中提取有名字或有明确特征的主要角色，3-6 个，保留原姓名；若确实无名，用「主角/配角A」等概括。
2. palette 提供 4 个十六进制色值(含 #)，贴合剧情基调(暖/冷/悬疑等)，并给中文名称。
3. anchors 列出 3-5 条全片必须保持一致的硬设定(关键人物名、地点、道具、关系)，用于避免前后矛盾。
"""

def _clean_meta(raw):
    """把 Ollama 返回的元数据原始 dict 规整为面板可用结构(补 id/补色)。"""
    data = raw or {}
    chars = data.get("characters") or []
    palette = data.get("palette") or []
    story = data.get("story") or {}
    anchors = data.get("anchors") or []
    clean_chars = []
    for i, c in enumerate(chars):
        if not isinstance(c, dict):
            continue
        clean_chars.append({
            "id": "c%d" % (i + 1),
            "name": str(c.get("name") or ("人物%d" % (i + 1))),
            "relation": str(c.get("relation") or "配角"),
            "age": str(c.get("age") or "青年"),
            "notes": str(c.get("notes") or ""),
            "refs": [],
        })
    clean_palette = []
    for p in palette:
        if not isinstance(p, dict):
            continue
        col = str(p.get("color") or "")
        if not col.startswith("#") or len(col) not in (7, 4):
            continue
        clean_palette.append({"name": str(p.get("name") or col), "color": col})
    if len(clean_palette) < 4:
        # 兜底补色，保证面板有东西展示
        defaults = [("#3a5bd9", "主色"), ("#d9a23a", "辅色1"), ("#c23ad9", "辅色2"), ("#e8c4a2", "肤色")]
        while len(clean_palette) < 4:
            c, n = defaults[len(clean_palette)]
            clean_palette.append({"name": n, "color": c})
    return {
        "characters": clean_chars,
        "palette": clean_palette[:6],
        "story": {
            "setting": str(story.get("setting") or ""),
            "timeline": str(story.get("timeline") or ""),
            "notes": str(story.get("notes") or ""),
        },
        "anchors": [str(a) for a in anchors if a][:8],
    }


def plan_meta(title, scenes, timeout=300):
    """兜底：当主路径(call_ollama 合并输出)未能给出元数据时，单独抽取一次。"""
    scene_hint = "\n".join(
        "- 镜头%d: %s | %s" % (i + 1, (s.get("caption") or "")[:80], (s.get("description") or "")[:120])
        for i, s in enumerate(scenes[:12]))
    prompt = META_PROMPT + "\n\n剧名: %s\n分镜摘要:\n%s\n" % (title, scene_hint)
    try:
        text = _ollama_raw(prompt, temperature=0.5, maxtoken=1200, timeout=timeout)
        data = json.loads(_strip_md(text))
    except Exception:
        data = {}
    return _clean_meta(data)


# ----------------------------- 视频合成 -----------------------------
def make_video(scenes, media_paths, outdir, is_video=False, fps=24, codec="libx264", fmt="mp4"):
    """把每镜的静帧(或动画片段)按 duration 拼接、烧入中文字幕，输出指定格式视频。"""
    clips = []
    for i, (sc, imp) in enumerate(zip(scenes, media_paths), 1):
        clip = os.path.join(outdir, f"clip_{i:02d}.mp4")
        dur = int(sc.get("duration", 4))
        if is_video:
            # 动画片段已含运动，直接按目标时长重编码统一参数
            subprocess.run(["ffmpeg", "-y", "-i", imp,
                            "-t", str(dur), "-r", str(fps), "-pix_fmt", "yuv420p",
                            "-vf", f"scale={W}:{H}", "-c:v", codec,
                            "-preset", "veryfast", clip], check=True)
        else:
            subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", imp,
                            "-t", str(dur), "-r", str(fps), "-pix_fmt", "yuv420p",
                            "-vf", f"scale={W}:{H}", "-c:v", codec,
                            "-preset", "veryfast", clip], check=True)
        clips.append(clip)
    listf = os.path.join(outdir, "_list.txt")
    with open(listf, "w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{os.path.basename(c)}'\n")
    raw = os.path.join(outdir, "_raw.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                    "-c", "copy", raw], check=True, cwd=outdir)
    srt = os.path.join(outdir, "subs.srt")
    t = 0.0; lines = []
    for i, sc in enumerate(scenes, 1):
        d = int(sc.get("duration", 4))
        lines.append(str(i))
        lines.append(fmt_time(t) + " --> " + fmt_time(t + d))
        lines.append(sc.get("caption", ""))
        lines.append("")
        t += d
    with open(srt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    ext = "mov" if fmt == "mov" else "mp4"
    final = os.path.join(outdir, "final." + ext)
    subprocess.run(["ffmpeg", "-y", "-i", raw,
                    "-vf", r"subtitles=subs.srt:force_style='FontName=Microsoft YaHei,FontSize=28'",
                    "-c:v", codec, "-pix_fmt", "yuv420p", "-c:a", "copy", final],
                   check=True, cwd=outdir)
    return final


# ----------------------------- 两阶段入口 -----------------------------
def plan_script(text="", mode="full", script_text=None, script_path=None, on_progress=None, on_chunk=None):
    """阶段一: 剧本解析 + 镜头拆分 + 提示词生成(不调用 ComfyUI)。
    返回 (title, scenes, meta)。scenes 元素含 prompt/negative_prompt；meta 含人物/色彩/故事/锚点。
    on_chunk 可接收 Ollama 流式输出，用于前端实时展示生成进度。"""
    has_doc_text = bool(script_text and script_text.strip())
    has_doc_path = bool(script_path and os.path.isfile(script_path))
    if has_doc_path or has_doc_text:
        mode = "full"

    if has_doc_path:
        script = ""
    elif has_doc_text:
        script = script_text
    elif text and text.strip():
        # 短主题(<120字且<3换行)先扩写成剧情梗概，避免 Ollama 直接解析失败；
        # 详细剧本/分镜脚本直接送 call_ollama(已合并元数据输出)，省一次推理。
        if len(text.strip()) < 120 and text.count("\n") < 3:
            script = expand_theme(text, on_chunk=on_chunk)
        else:
            script = text
    else:
        raise ValueError("请提供主题/剧本，或上传剧本文件")

    title = "漫剧"
    scenes = None
    if has_doc_path and script_path.lower().endswith(".docx"):
        paragraphs, tables = extract_docx(script_path)
        title = _extract_title(paragraphs) or title
        scenes = parse_scenes_from_tables(tables)
        if scenes is None:
            script = "\n".join(paragraphs)
            scenes = parse_scenes_from_text(script)
    if scenes is None and has_doc_text:
        scenes = parse_scenes_from_text(script_text)
    meta = None
    if scenes is not None:
        scenes = enrich_scenes_with_prompts(scenes, title, on_progress=on_progress)
        meta = plan_meta(title, scenes)
    else:
        data = call_ollama(script, on_chunk=on_chunk)
        title = data.get("title", title)
        scenes = data.get("scenes") or []
        # 主路径已合并输出元数据；若缺失则兜底单独抽一次
        raw_meta = {k: data.get(k) for k in ("characters", "palette", "story", "anchors")}
        if raw_meta.get("characters") or raw_meta.get("palette") or raw_meta.get("anchors"):
            meta = _clean_meta(raw_meta)
        else:
            meta = plan_meta(title, scenes)
    for i, sc in enumerate(scenes, 1):
        sc.setdefault("scene_id", i)
        sc.setdefault("caption", "")
        sc.setdefault("negative_prompt", "")
        sc.setdefault("prompt", sc.get("description", ""))
        sc.setdefault("duration", 4)
    return title, scenes, meta


def render_video(title, scenes, outdir=None, ref_files=None,
                 ckpt=CKPT, animated=False, use_ip=False,
                 width=W, height=H, fps=24, codec="libx264", fmt="mp4",
                 on_progress=None, on_start=None):
    """阶段二: 根据(可编辑过的) scenes 出图/出动画并合成视频。
    use_ip=True 时把上传的参考图当作 IP-Adapter 角色参考(需 IP-Adapter 模型就位)。"""
    def prog(stage, msg, cur=0, tot=0):
        if on_progress:
            try: on_progress(stage, msg, cur, tot)
            except Exception: pass

    if outdir is None:
        outdir = os.path.join(BASE, "outputs", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(outdir, exist_ok=True)
    if on_start:
        try: on_start(outdir)
        except Exception: pass

    # 能力可用性检查(缺模型直接报错，避免烧半天显存才发现)
    if animated and MOTION_MODEL not in list_motion_models():
        raise RuntimeError(
            f"未找到 AnimateDiff 运动模块 {MOTION_MODEL}，请下载到 "
            f"{os.path.join(MODELS_DIR, 'animatediff_models')} 后重试。")
    if use_ip and not (IPADAPTER_MODEL in list_ipadapter_models()
                       and CLIP_VISION in list_clip_vision()):
        raise RuntimeError(
            f"未找到 IP-Adapter 模型/CLIP-ViT，请下载到 "
            f"{os.path.join(MODELS_DIR, 'ipadapter')} 与 clip_vision 后重试。")
    if ckpt not in list_checkpoints():
        # 容错：支持传无扩展名/大小写不同的真实底模名
        norm = lambda x: x.lower().removesuffix(".safetensors").removesuffix(".ckpt")
        target = norm(ckpt)
        m = [c for c in list_checkpoints() if norm(c) == target]
        if m:
            ckpt = m[0]
        else:
            raise RuntimeError(
                f"未找到底模 {ckpt}，可选：{list_checkpoints()}")

    # 出图前让 Ollama 释放显存(1660Ti 6GB 跑 AnimateDiff 必需)
    unload_ollama()

    # 上传参考图
    comfy_refs = []
    if ref_files:
        for p in ref_files:
            try:
                comfy_refs.append(upload_to_comfyui(p))
                refdest = os.path.join(outdir, "ref")
                os.makedirs(refdest, exist_ok=True)
                try: shutil.copy(p, os.path.join(refdest, os.path.basename(p)))
                except Exception: pass
                try: os.remove(p)
                except Exception: pass
            except Exception:
                continue

    manifest = {"title": title, "model": ckpt, "animated": animated,
                "use_ip": use_ip, "refs": comfy_refs, "scenes": []}

    media_paths = []
    any_video = False
    prog("image", f"开始出图，共 {len(scenes)} 镜", 0, len(scenes))
    for i, sc in enumerate(scenes, 1):
        ref = comfy_refs[(i - 1) % len(comfy_refs)] if comfy_refs else None
        # IP-Adapter 用上传的角色参考图；静态 img2img 也用同一参考(若开启 ip 则不重复)
        ip_ref = ref if (use_ip and ref) else None
        img2img_ref = ref if (not use_ip and ref) else None
        wf, out_node, kind = build_workflow(
            sc.get("prompt", ""), sc.get("negative_prompt", ""),
            (int(time.time()) % 100000) + i * 100, f"manju_{i:02d}",
            ref_image=img2img_ref, duration=int(sc.get("duration", 4)),
            ckpt=ckpt, animated=animated, ip_ref=ip_ref,
            width=width, height=height)
        fn, sf, tp, got = generate_media(wf)
        is_video = (got == "video")
        any_video = any_video or is_video
        ext = ".mp4" if is_video else ".png"
        outp = os.path.join(outdir, f"scene_{i:02d}{ext}")
        download_file(fn, sf, tp, outp)
        wfj = os.path.join(outdir, f"scene_{i:02d}_workflow.json")
        json.dump(wf, open(wfj, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        media_paths.append(outp)
        manifest["scenes"].append({**sc, "media": os.path.basename(outp),
                                   "kind": kind, "workflow": os.path.basename(wfj)})
        prog("image", f"镜头 {i}/{len(scenes)} 完成", i, len(scenes))

    prog("video", "正在合成视频、烧入中文字幕…", len(scenes), len(scenes))
    final = make_video(scenes, media_paths, outdir, is_video=any_video,
                       fps=fps, codec=codec, fmt=fmt)
    manifest["video"] = os.path.basename(final)
    prog("done", "完成！", len(scenes), len(scenes))
    json.dump(manifest, open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return outdir, manifest


def run_agent(text="", mode="full", script_text=None, script_path=None, ref_files=None,
              ckpt=CKPT, animated=False, use_ip=False, on_progress=None, on_start=None):
    """统一入口(向后兼容): 一次性 解析->出图->视频。"""
    def prog(stage, msg, cur=0, tot=0):
        if on_progress:
            try: on_progress(stage, msg, cur, tot)
            except Exception: pass

    prog("plan", "正在解析剧本、生成提示词…")
    title, scenes, meta = plan_script(text, mode, script_text, script_path)
    prog("plan", f"《{title}》共 {len(scenes)} 个镜头，开始渲染", 0, len(scenes))
    if on_start:
        try: on_start(os.path.join(BASE, "outputs", "_pending"))
        except Exception: pass
    outdir, manifest = render_video(title, scenes, ref_files=ref_files,
                                    ckpt=ckpt, animated=animated, use_ip=use_ip)
    prog("done", "完成！", len(scenes), len(scenes))
    return outdir, manifest


def main():
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        script = open(sys.argv[1], encoding="utf-8").read()
        mode = "full"
    else:
        sample = os.path.join(BASE, "sample_script.txt")
        script = open(sample, encoding="utf-8").read() if os.path.exists(sample) else "一个女孩在雨中等公交车。"
        mode = "full"

    def cb(stage, msg, cur, tot):
        print(f"[{stage}] {msg}  ({cur}/{tot})")

    outdir, manifest = run_agent(script, mode=mode, on_progress=cb)
    print(">> 输出目录: " + outdir)
    print(">> 视频:     " + manifest["video"])


if __name__ == "__main__":
    main()
