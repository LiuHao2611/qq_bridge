# -*- coding: utf-8 -*-
"""全部 /api/v1 路由（按 SCHEMA_INDEX §3 契约）。

⚠️ 重建说明（2026-08-08）
原 routers.py 源码在一次编辑中被误清空；运行期唯一被正确编译且长期验证可运行的产物是
`__pycache__/routers_compiled.cpython-313.pyc`（由原始 routers.py 编译，含全部 90+ 端点以及
BR-003 分镜时长重分配等修复；已从 `routers.cpython-313.pyc` 拷出独立留存，避免被主缓存覆盖）。
为在「恢复可编辑性」与「零手抄风险」之间取得平衡，本文件采用「门面 + 定点覆盖」策略：

  1. 从 .pyc 载入已编译模块 `routers_compiled` 并执行，再 `from routers_compiled import *`，
     使 `api`（含全部路由）与所有 handler / helper 原样复用，行为 100% 等价于已验证产物；
  2. 对确需修改的端点（如 split_story，需适配 ai_local 新的 {segments, meta} 契约）
     先删除旧路由、再以新实现重新注册。

未来若要修改其它端点：把该端点的实现从 routers_compiled 平移为明文源码，删除旧路由后
用相同装饰器重新注册即可；不要直接改写 .pyc。任何改动必须同步更新 PROJECT_MAP.md §3.1。

⚠️ 可复现性 / 审计说明（2026-08-14 治理 #260）
- 本 .pyc 已纳入 git 跟踪（`server/_vendor/routers_compiled.cpython-313.pyc`），是 90+ 核心端点的
  权威编译产物；本文件（facade + P0–P4 明文增量）亦须提交 git，二者共同构成可复现的路由层。
- 可读兜底产物（已生成，供审计，非加载）：
  · `server/_vendor/routers_compiled.routes.txt` —— 从 .pyc 反汇编提取的「方法+路径」端点清单（101 条）；
  · `server/_vendor/routers_compiled.disasm.txt` —— 完整 `dis` 反汇编（cpython-313 无现成反编译器，
    此为唯一人类可读的 .pyc 记录）。
- 完整「.pyc → 明文 routers.py」还原：因 cpython-313 缺少可用反编译器（pycdc/decompyle3 均不支持），
  静默全量重写 90+ 端点风险高（易回归，违反无创性铁律）。故保留 facade 架构，待后续「分阶段、带回归测试」
  的明文迁移专项处理；迁移前以本 facade + .pyc + routes.txt 保证 git 可复现与可审计。
"""
import os
import sys
import asyncio
import importlib.util
import logging
import json
import time
import uuid
import shutil
import subprocess
import threading

logger = logging.getLogger("routers")

_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
# 权威编译产物优先从稳定的 _vendor 目录加载（已纳入 Git 版本控制，杜绝 __pycache__ 被清理导致后端崩溃）；
# 若 _vendor 缺失则回退到 __pycache__。
_VENDOR_PYC = os.path.join(_SERVER_DIR, "_vendor", "routers_compiled.cpython-313.pyc")
_CACHE_PYC = os.path.join(_SERVER_DIR, "__pycache__", "routers_compiled.cpython-313.pyc")
_PYC_PATH = _VENDOR_PYC if os.path.exists(_VENDOR_PYC) else _CACHE_PYC

# 1) 载入已编译的权威实现（等价于历史可运行源码；独立留存，避免被主缓存覆盖）
_spec = importlib.util.spec_from_file_location("routers_compiled", _PYC_PATH)
routers_compiled = importlib.util.module_from_spec(_spec)
sys.modules["routers_compiled"] = routers_compiled  # 注册后 `from routers_compiled import *` 才可解析
_spec.loader.exec_module(routers_compiled)

# 2) 复用其中的 api（全部路由）与所有公开符号
#    冻结(PyInstaller)时 `routers_compiled` 由 importlib 按路径加载并注册进 sys.modules，
#    此处改为运行时把公开符号注入全局，避免 `from X import *` 的静态分析报找不到模块。
for _name, _val in vars(routers_compiled).items():
    if not _name.startswith("_"):
        globals()[_name] = _val

# 覆盖实现所需的依赖（来自原模块 / 公共模块）
from fastapi import Request, UploadFile, File, Form  # noqa: F401
import re  # noqa: F401
from common import ok, ApiError  # noqa: F401
from ai_local import (  # noqa: F401
    split_story_with_llm, ollama_list_models, extract_assets_from_story,
    _call_online_llm, comfy_available, _ollama_running_info, generate_comic,
    _comfy_img2img, _comfy_inpaint, _comfy_outpaint, _comfy_upscale,
    _comfy_img2img_with_controlnet, _comfy_img2img_with_ipadapter,
    _get_comfy_upscale_model, reverse_prompt, face_restore, OUTPUTS,
)
_body = getattr(routers_compiled, "_body")
_redistribute_durations = getattr(routers_compiled, "_redistribute_durations")
from db import (  # noqa: F401
    get_conn, fetch_all, uid, SINGLE_USER_ID, log_operation, insert_row,
    count_rows, list_rows, get_by_id, TABLES,
)

# ======================================================================
# 定点覆盖区（START）
# ======================================================================

# --- split_story：适配 ai_local 新的 {segments, meta} 契约 ---
# 先删除旧路由，再以新实现重新注册。注意：api 带 prefix="/api/v1"，
# 已注册路由的 .path 含前缀，故删除时按「前缀 + 相对路径」匹配。
_SPLIT_PATH = "/projects/{project_id}/split-story"
_SPLIT_FULL = (getattr(api, "prefix", "") or "") + _SPLIT_PATH
for _i in range(len(api.routes) - 1, -1, -1):
    if getattr(api.routes[_i], "path", None) == _SPLIT_FULL:
        del api.routes[_i]


@api.post(_SPLIT_PATH)
async def split_story(project_id: str, req: Request):
    """Phase 2 自动分场。

    调用 ai_local.split_story_with_llm（现返回 {"segments", "meta"}）：
      - 成功：meta 含 model / duration_ms / raw_preview / count；
      - 失败/不可用：meta 含 error（如 Ollama 未启动），自动回退正则拆分。
    返回 data={segments, count, source, meta}，供前端底部「推理进度」面板实时展示。
    """
    b = await _body(req)
    text = (b.get("story") or "").strip()
    if not text:
        raise ApiError("INVALID_PARAMS", "story 不能为空")

    model_config = b.get("model_config") or {}
    meta = {}
    try:
        # 关键：用 asyncio.to_thread 把同步 Ollama HTTP 调用丢到工作线程池
        # → FastAPI 事件循环不阻塞，/ollama/status 轮询 / stages-with-poses 等
        #   其他请求在 22 分钟长跑期间依然可以响应（修根因：之前单次 split 把整个 ASGI 卡死）。
        result = await asyncio.to_thread(split_story_with_llm, text, model_config) or {}
        segs = result.get("segments")
        meta = result.get("meta") or {}
        source = meta.get("source") or ("llm" if segs else "regex")
    except Exception as e:  # 与历史行为一致：LLM 调用异常 → 回退正则
        logger.warning("[split] LLM 拆解异常，回退正则: %s", e)
        segs = None
        source = "regex"
        meta = {"source": "regex", "error": "调用异常: %s" % e}

    if segs:
        segments = _normalize_llm_segments(segs)
        source = "llm"
    else:
        segments = _regex_fallback_segments(text)
        source = "regex"
        meta = dict(meta)
        meta["source"] = "regex"
        if not meta.get("error"):
            meta["error"] = "LLM 未返回有效场次，已回退正则拆分"

    # M3（手册 §4.1 / 契约 P2）：拆解完成 → 导演发言「共 N 分镜，其中 M 待绑定资产」
    # + 攒批推送 P2 A 类决策点（单片段时长上限；幂等）。
    try:
        from director.decision_engine import preflight_check
        preflight_check(project_id, "P2")
    except Exception as _e:
        logger.warning("[split] preflight P2 失败（忽略）: %s", _e)
    if segments:
        _n = len(segments)
        _m = sum(1 for s in segments if not s.get("asset_refs"))
        try:
            from realtime import publish_director_notice
            publish_director_notice(project_id, {
                "message": "拆解完成，共 %d 个分镜，其中 %d 个待绑定资产" % (_n, _m),
                "phase": "P2",
            })
        except Exception:
            logger.warning("[split] P2 拆解 notice 推送失败（忽略）")

    return ok({
        "segments": segments,
        "count": len(segments),
        "source": source,
        "meta": meta,
    })


def _normalize_llm_segments(segs):
    """把 LLM 返回的 segments 规范化为与历史口径一致的结构（前 30 场 / 每场前 3 镜）。"""
    segments = []
    for i, seg in enumerate((segs or [])[:30]):
        if not isinstance(seg, dict):
            continue
        shots = seg.get("shots") or []
        norm_shots = []
        for j, sh in enumerate(shots[:3]):
            if not isinstance(sh, dict):
                continue
            sd = sh.get("duration")
            if sd:
                duration = int(sd)
            else:
                duration = int(seg.get("duration") or 10) // max(1, len(shots))
            norm_shots.append({
                "caption": sh.get("caption") or ("镜头%d" % (j + 1)),
                "description": sh.get("description") or (seg.get("scene_description") or "")[:80],
                "duration": int(duration),
                "shot_type": sh.get("shot_type") or "medium",
                "camera_movement": sh.get("camera_movement") or "static",
                "refs": sh.get("refs") or [],
            })
        seg_dur = int(seg.get("duration") or 10)
        _redistribute_durations(seg_dur, norm_shots)
        segments.append({
            "title": seg.get("title") or ("场次 %02d" % (i + 1)),
            "duration": seg_dur,
            "scene_description": seg.get("scene_description") or "",
            "summary": seg.get("summary") or "",
            "characters": seg.get("characters") or [],
            "shots": norm_shots,
        })
    return segments


def _regex_fallback_segments(text):
    """历史正则回退：按段落切分，识别说话人，构造 3 个固定模板分镜（时长 4/3/3=10）。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r?\n", text) if len(p.strip()) >= 10]
    if not paragraphs:
        paragraphs = [text]
    segments = []
    for i, para in enumerate(paragraphs[:30]):
        chars = list(set(re.findall(r'[\u4e00-\u9fa5]{2,4}(?=说|道|问|答|笑|哭|喊|叫)', para)))[:5]
        sentences = re.split(r'[。！？]', para)
        summary = (sentences[0] + (sentences[1] if len(sentences) > 1 else "")).strip()[:120]
        shots = [
            {
                "caption": "开场",
                "description": summary,
                "duration": 4,
                "shot_type": "wide",
                "camera_angle": "eye_level",
                "camera_movement": "static",
                "focal_length": "35mm",
                "refs": [],
            },
            {
                "caption": "推进",
                "description": para[80:160] or "继续发展",
                "duration": 3,
                "shot_type": "medium",
                "camera_angle": "eye_level",
                "camera_movement": "static",
                "focal_length": "50mm",
                "refs": [],
            },
            {
                "caption": "收尾",
                "description": para[160:240] or "场景结束",
                "duration": 3,
                "shot_type": "close_up",
                "camera_angle": "low_angle",
                "camera_movement": "static",
                "focal_length": "85mm",
                "refs": [],
            },
        ]
        max_shots = max(1, min(3, len(para) // 40))
        seg = {
            "title": "场次 %02d" % (i + 1),
            "duration": 10,
            "scene_description": para[:240],
            "summary": summary or para[:120],
            "characters": chars,
            "shots": shots[:max_shots],
        }
        _redistribute_durations(seg["duration"], seg["shots"])
        segments.append(seg)
    return segments


@api.get("/models/local")
async def list_local_models():
    """返回本地 Ollama 已加载模型列表，供「AI 自动分场」模型选择器使用。"""
    return ok({"models": ollama_list_models()})


@api.get("/ollama/status")
async def ollama_status():
    """返回 Ollama 当前已加载模型的 GPU/CPU 分布，供前端日志展示（避免误以为模型在 CPU 跑）。

    数据来自 ai_local._ollama_running_info（查询 /api/ps，按 size_vram/size 估算 GPU 占比）。
    """
    return ok(_ollama_running_info())


# --- settings: 修复 /settings 被旧实现占用为 Dify 配置的问题，改为读写 settings 表 ---
_SETTINGS_PATH = "/settings"
_SETTINGS_FULL = (getattr(api, "prefix", "") or "") + _SETTINGS_PATH
for _i in range(len(api.routes) - 1, -1, -1):
    _rp = getattr(api.routes[_i], "path", None)
    if _rp in (_SETTINGS_FULL, _SETTINGS_PATH):
        del api.routes[_i]


def _parse_setting_value(v):
    try:
        return json.loads(v)
    except Exception:
        return v


# 服务端内部配置键，不应作为用户设置暴露给设置页（如 Dify、内部开关等）
_INTERNAL_SETTING_PREFIXES = ("dify_",)


def _is_user_setting_key(k):
    return isinstance(k, str) and not k.startswith(_INTERNAL_SETTING_PREFIXES)


@api.get(_SETTINGS_PATH)
async def get_settings():
    """返回全部用户设置（key/value，value 自动 JSON 反序列化），过滤服务端内部配置。"""
    conn = get_conn()
    try:
        rows = fetch_all(conn, "SELECT key, value FROM settings")
        return ok({"settings": {r["key"]: _parse_setting_value(r["value"]) for r in rows if _is_user_setting_key(r["key"])}})
    finally:
        conn.close()


@api.patch(_SETTINGS_PATH)
async def update_settings(req: Request):
    """增量更新用户设置；请求体为 key/value 字典，value 会被 JSON 序列化存储。"""
    b = await _body(req)
    if not isinstance(b, dict):
        raise ApiError("INVALID_PARAMS", "请求体必须是对象")
    conn = get_conn()
    try:
        now = int(time.time())
        for k, v in b.items():
            if not _is_user_setting_key(k):
                continue
            try:
                stored = json.dumps(v, ensure_ascii=False)
            except Exception:
                stored = str(v)
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (k, stored, now),
            )
        conn.commit()
        rows = fetch_all(conn, "SELECT key, value FROM settings")
        return ok({"settings": {r["key"]: _parse_setting_value(r["value"]) for r in rows if _is_user_setting_key(r["key"])}})
    finally:
        conn.close()


# --- 模型连接测试（Settings.vue 中「测试连接」按钮调用） ---
@api.post("/models/test")
async def test_model_connection(req: Request):
    """测试给定 AI 模型配置的连通性。

    body: { kind: 'create'|'planning'|'video'|'img', provider: 'local'|'online',
            local?: {baseUrl, model}, online?: {baseUrl, apiKey, model} }
    返回 data: { ok: bool, message: str }
    """
    b = await _body(req)
    kind = b.get("kind") or "create"
    provider = b.get("provider") or "local"
    local_cfg = b.get("local") or {}
    online_cfg = b.get("online") or {}

    if provider == "local":
        base_url = (local_cfg.get("baseUrl") or "").strip()
        model = (local_cfg.get("model") or "").strip()
        if not base_url:
            return ok({"ok": False, "message": "未填写本地服务地址"})
        if kind in ("create", "planning"):
            try:
                models = ollama_list_models(base_url)
                if not models:
                    return ok({"ok": False, "message": "无法从 %s 获取 Ollama 模型列表" % base_url})
                if model and model not in models:
                    return ok({"ok": False, "message": "目标模型 %s 未加载（可用：%s）" % (model, ", ".join(models[:10]))})
                return ok({"ok": True, "message": "Ollama 可用，已加载模型：%s" % ", ".join(models[:10])})
            except Exception as e:
                return ok({"ok": False, "message": "连接 Ollama 失败：%s" % e})
        else:  # video / img
            try:
                ok_flag = comfy_available(base_url)
                return ok({"ok": ok_flag, "message": "ComfyUI 连接%s" % ("成功" if ok_flag else "失败")})
            except Exception as e:
                return ok({"ok": False, "message": "连接 ComfyUI 失败：%s" % e})
    else:
        api_base = (online_cfg.get("baseUrl") or "").strip()
        api_key = online_cfg.get("apiKey") or ""
        model = (online_cfg.get("model") or "").strip()
        if not api_base:
            return ok({"ok": False, "message": "未填写在线 API 地址"})
        if kind in ("create", "planning"):
            if not model:
                return ok({"ok": False, "message": "未填写在线模型名"})
            raw, meta = _call_online_llm("hi", {"apiBase": api_base, "apiKey": api_key, "model": model})
            if raw is None:
                return ok({"ok": False, "message": meta.get("error") or "在线模型无响应"})
            return ok({"ok": True, "message": "在线 LLM 响应正常（模型：%s）" % model})
        else:
            # 视频/图像在线 API：先探测 /models 端点或基础连通性
            import urllib.request
            probe_url = api_base.rstrip("/") + "/models"
            headers = {}
            if api_key:
                headers["Authorization"] = "Bearer " + api_key
            try:
                r = urllib.request.Request(probe_url, headers=headers, method="GET")
                with urllib.request.urlopen(r, timeout=10) as resp:
                    return ok({"ok": resp.status < 400, "message": "在线服务可访问（状态：%s）" % resp.status})
            except Exception as e:
                # 很多视频/图像服务没有 /models，仅验证地址格式也算"配置有效"
                return ok({"ok": api_base.startswith(("http://", "https://")),
                           "message": "在线地址已记录；实际生成时会按该地址调用（本次探测：%s）" % e})


# --- 资产初始化：从剧本一键提取 + 批量创建（Phase 1 闭环） ---

def _build_project_story_text(conn, project_id):
    """把项目下已导入的场次/分镜文本合并为供 LLM 分析的剧本文本。"""
    segs = fetch_all(conn, 'SELECT segment_id, title, scene_description FROM segments WHERE project_id=? ORDER BY "index"', (project_id,))
    parts = []
    for s in segs:
        header = "# %s" % (s.get("title") or "片段")
        body = (s.get("scene_description") or "").strip()
        seg_text = "\n".join([x for x in [header, body] if x])
        shots = fetch_all(conn, 'SELECT description, voiceover, dialogue FROM shots WHERE segment_id=? ORDER BY "index"', (s.get("segment_id"),))
        shot_texts = []
        for sh in shots:
            txt = " ".join([sh.get("description") or "", sh.get("voiceover") or "", sh.get("dialogue") or ""]).strip()
            if txt:
                shot_texts.append(txt)
        if shot_texts:
            seg_text += "\n" + "\n".join(shot_texts)
        if seg_text.strip():
            parts.append(seg_text)
    return "\n\n".join(parts)


_EXTRACT_PATH = "/projects/{project_id}/extract-assets"
@api.post(_EXTRACT_PATH)
async def extract_assets(project_id: str, req: Request):
    """从项目已有场次/分镜或用户提供的 story 中提取角色/场景/道具候选。

    请求体：{story?: string, model_config?: {...}}
    返回：{roles, scenes, props, meta}
    """
    b = await _body(req)
    story = (b.get("story") or "").strip()
    if not story:
        conn = get_conn()
        try:
            story = _build_project_story_text(conn, project_id)
        finally:
            conn.close()
    if not story:
        raise ApiError("NO_STORY", "项目中暂无场次/分镜，请在请求体提供 story 文本")
    model_config = b.get("model_config") or {}
    # M3（手册 §3.2.3）：进入 P1 资产初始化即攒批推送 A 类决策点（视觉风格/真人参考），
    # 幂等：已 pending 复用、已 resolved 跳过（不重复问）。
    try:
        from director.decision_engine import preflight_check
        preflight_check(project_id, "P1")
    except Exception as _e:
        logger.warning("[extract-assets] preflight P1 失败（忽略）: %s", _e)
    # 同上：用 asyncio.to_thread 包装同步 Ollama 调用，避免事件循环被 extract 卡死（修根因）
    result = await asyncio.to_thread(extract_assets_from_story, story, model_config)
    # 即使 LLM 失败也把 meta.error 返回给前端，前端可提示用户
    return ok(result)


_BATCH_ASSET_PATH = "/projects/{project_id}/assets/batch"
@api.post(_BATCH_ASSET_PATH)
async def batch_create_project_assets(project_id: str, req: Request):
    """批量创建本集资产（is_global=0）。

    请求体：{assets: [{name, type, role_type?, description?, tags?}]}
    幂等：同一 project_id 下同名资产（忽略大小写）会被跳过。
    返回：{created:[...], skipped:[...], count}
    """
    b = await _body(req)
    assets = b.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ApiError("INVALID_PARAMS", "assets 必须是数组")

    valid_types = {"role", "scene", "prop"}
    valid_role_types = {"protagonist", "supporting", "antagonist"}
    clean = []
    for a in assets:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "").strip()[:128]
        t = str(a.get("type") or "").strip().lower()
        if not name or t not in valid_types:
            continue
        rt = str(a.get("role_type") or "").strip().lower()
        entry = {
            "name": name,
            "type": t,
            "role_type": rt if t == "role" and rt in valid_role_types else None,
            "description": str(a.get("description") or "").strip()[:512],
            "tags": [],
        }
        tags = a.get("tags")
        if isinstance(tags, list):
            entry["tags"] = [str(x).strip()[:32] for x in tags if str(x).strip()][:10]
        clean.append(entry)
    if not clean:
        raise ApiError("INVALID_PARAMS", "没有有效的资产可创建")

    conn = get_conn()
    created = []
    skipped = []
    try:
        existing_rows = fetch_all(conn, "SELECT name FROM assets WHERE project_id=? AND is_global=0", (project_id,))
        existing = {r["name"].lower() for r in existing_rows if r.get("name")}
        now = int(time.time())
        for entry in clean:
            if entry["name"].lower() in existing:
                skipped.append(entry["name"])
                continue
            aid = uid()
            params = {
                "asset_id": aid,
                "user_id": SINGLE_USER_ID,
                "name": entry["name"],
                "type": entry["type"],
                "description": entry["description"],
                "tags": json.dumps(entry["tags"], ensure_ascii=False) if entry["tags"] else None,
                "is_global": 0,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            }
            if entry["role_type"]:
                params["role_type"] = entry["role_type"]
            cols = list(params.keys())
            placeholders = ",".join("?" * len(cols))
            conn.execute("INSERT INTO assets (%s) VALUES (%s)" % (",".join(cols), placeholders), tuple(params.values()))
            created.append({
                "asset_id": aid,
                "name": entry["name"],
                "type": entry["type"],
                "role_type": entry["role_type"],
                "description": entry["description"],
            })
            existing.add(entry["name"].lower())
            log_operation(conn, "asset_create", object_type="asset", object_id=aid,
                          object_name=entry["name"], diff={"type": entry["type"], "source": "batch_extract"})
        conn.commit()
    except Exception as e:
        logger.exception("[assets/batch] 批量创建资产失败")
        raise ApiError("BATCH_CREATE_FAILED", "批量创建资产失败：%s" % e)
    finally:
        conn.close()
    return ok({"created": created, "skipped": skipped, "count": len(created)})


# --- F1：删除资产/阶段后级联清理悬空引用（根因修复） ---
# 引用以 asset_id（可选 stage_id）为键「全局」清理，与所属项目无关：
#   - segment_asset_refs：DELETE 匹配行（归一化表）
#   - shots.asset_refs：逐镜头解析 JSON 文本列，剔除悬空引用（渲染 @chip 的权威来源）
# 设计为「附加端点」，不改动编译模块中的 delete_asset / del_stage，零回归风险；
# 任何删除资产/阶段的调用方在成功后显式调用本端点即可保证引用一致性（INV-1）。
_PRUNE_PATH = "/projects/{project_id}/prune-refs"
@api.post(_PRUNE_PATH)
async def prune_asset_refs(project_id: str, req: Request):
    """删除资产/阶段后级联清理悬空 @引用（F1）。

    请求体：{ asset_id: str (必填), stage_id?: str }
      - 删资产：清理全部 asset_id 匹配的引用（不论 stage_id）
      - 删阶段：仅清理 asset_id + stage_id 同时匹配的引用
    返回：{ cleaned_segments, cleaned_shots }
    """
    b = await _body(req)
    asset_id = b.get("asset_id")
    stage_id = b.get("stage_id") or None
    if not asset_id:
        raise ApiError("INVALID_PARAMS", "asset_id 必填")
    conn = get_conn()
    try:
        if stage_id:
            conn.execute(
                "DELETE FROM segment_asset_refs WHERE asset_id=? AND stage_id=?",
                (asset_id, stage_id),
            )
        else:
            conn.execute("DELETE FROM segment_asset_refs WHERE asset_id=?", (asset_id,))
        rows = fetch_all(conn,
            "SELECT shot_id, segment_id, asset_refs FROM shots "
            "WHERE asset_refs IS NOT NULL AND asset_refs <> ''")
        cleaned_shots = 0
        cleaned_segments = set()
        for r in rows:
            raw = r.get("asset_refs")
            if not raw:
                continue
            try:
                arr = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
            except Exception:
                continue
            if not isinstance(arr, list):
                continue
            if stage_id:
                new = [x for x in arr
                       if not (isinstance(x, dict) and x.get("asset_id") == asset_id
                               and (x.get("stage_id") or None) == stage_id)]
            else:
                new = [x for x in arr
                       if not (isinstance(x, dict) and x.get("asset_id") == asset_id)]
            if len(new) != len(arr):
                conn.execute("UPDATE shots SET asset_refs=? WHERE shot_id=?",
                             (json.dumps(new, ensure_ascii=False), r["shot_id"]))
                cleaned_shots += 1
                if r.get("segment_id"):
                    cleaned_segments.add(r["segment_id"])
        conn.commit()
    finally:
        conn.close()
    return ok({"cleaned_segments": len(cleaned_segments), "cleaned_shots": cleaned_shots})


# --- 漫画创作：一键生成 4 格漫画（复用 Director N03 全局风格参考 + §5.2 角色一致性约束） ---
import base64 as _b64  # noqa: E402
import uuid as _uuid    # noqa: E402

_COMIC_TMP = os.path.join(os.path.dirname(_SERVER_DIR), ".tmp", "comic_refs")


def _decode_dataurl_to_png(dataurl):
    """把前端传来的 data URL（可选角色参考图）解码为本地 PNG（落 D 盘 .tmp，FS-03 合规）。"""
    if not dataurl or not isinstance(dataurl, str) or "base64," not in dataurl:
        return None
    try:
        _hdr, b64 = dataurl.split("base64,", 1)
        raw = _b64.b64decode(b64)
        os.makedirs(_COMIC_TMP, exist_ok=True)
        p = os.path.join(_COMIC_TMP, "ref_%s.png" % _uuid.uuid4().hex[:12])
        with open(p, "wb") as f:
            f.write(raw)
        return p
    except Exception as e:
        logger.warning("[comic] 角色参考图解码失败: %s" % e)
        return None


@api.post("/t2i/comic")
async def t2i_comic(req: Request):
    """一键生成 4 格漫画：复用导演 Agent 生图约束逻辑。

    复用点：
      - 分镜拆分：Ollama 结构化拆分（与导演「剧本→分镜」同源），失败规则兜底；
      - 全局风格参考图（N03）：以漫画风格基调出一张参考图作为第二路 IP-Adapter 风格条件，
        统一 4 格色调/质感/光影；
      - 角色一致性（§5.2）：若提供角色参考图，走 IP-Adapter 身份路 + 风格路，跨格一致。
    单次请求完整生成全部 4 格（无分步交互），长任务经 asyncio.to_thread 不阻塞事件循环。
    """
    b = await _body(req)
    story = (b.get("story") or "").strip()
    if not story:
        raise ApiError("INVALID_PARAMS", "story 不能为空")
    char_refs = []
    for item in (b.get("character_refs") or []):
        p = _decode_dataurl_to_png(item)
        if p:
            char_refs.append(p)
    try:
        result = await asyncio.to_thread(
            generate_comic,
            story,
            b.get("style") or "日漫",
            b.get("negative") or "",
            b.get("resolution") or "1024",
            b.get("ratio") or "3:4",
            int(b.get("steps") or 28),
            int(b.get("seed") or 42),
            bool(b.get("enhance", True)),
            b.get("orchestrator") or "ollama",
            char_refs,
            int(b.get("panels") or 4),
            None,  # on_progress
            float(b.get("cfg") or 8.5),
        )
    except ApiError:
        raise
    except Exception as e:
        raise ApiError("GEN_FAILED", "漫画生成失败：%s" % str(e)[:200])

    # 持久化到 image_tasks，使漫画生成历史可跨会话保留、可分页查询
    try:
        conn = get_conn()
        try:
            insert_row(
                conn,
                TABLES["image_tasks"],
                {
                    "task_id": uid(),
                    "prompt": story,
                    "negative_prompt": (b.get("negative") or "") + ", " + "低质量, 无关物体, 文字",
                    "params": json.dumps(
                        {
                            "kind": "comic",
                            "style": b.get("style") or "日漫",
                            "panels": int(b.get("panels") or 4),
                            "resolution": b.get("resolution") or "1024",
                            "ratio": b.get("ratio") or "3:4",
                            "steps": int(b.get("steps") or 28),
                            "seed": int(b.get("seed") or 42),
                            "cfg": float(b.get("cfg") or 8.5),
                        },
                        ensure_ascii=False,
                    ),
                    "status": "succeeded",
                    "progress": 100,
                    "result": json.dumps(result, ensure_ascii=False),
                    "error_message": "",
                    "pre_auth_id": "",
                    "cost_credits": 0,
                    "priority": 0,
                    "retry_count": 0,
                    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[comic] 保存 image_tasks 历史失败（不影响本次返回）: %s" % e)

    return ok(result)


# --- 文生图/漫画历史列表：补充 params / result 原始对象，供漫画历史页正确识别 ---
_T2I_LIST_PATH = "/t2i/tasks"
_T2I_LIST_FULL = (getattr(api, "prefix", "") or "") + _T2I_LIST_PATH
for _i in range(len(api.routes) - 1, -1, -1):
    if getattr(api.routes[_i], "path", None) == _T2I_LIST_FULL:
        del api.routes[_i]


@api.get(_T2I_LIST_PATH)
async def t2i_tasks_list(page: int = 1, size: int = 60, status: str = ""):
    """返回 image_tasks 分页列表，包含 params / result 解析后的完整对象。

    兼容原实现字段（images / orchestrator_used / prompt_used），同时补充
    params（生成参数）、result（原始结果对象），让漫画历史页可以按 kind 过滤。
    """
    conn = get_conn()
    try:
        where = "1=1"
        params = []
        if status:
            where += " AND status=?"
            params.append(status)
        total = count_rows(conn, TABLES["image_tasks"], where, tuple(params))
        items_db = list_rows(
            conn, TABLES["image_tasks"], where=where, params=tuple(params),
            order_by="created_at DESC", limit=size, offset=(page - 1) * size,
        )
        items = []
        for row in items_db:
            parsed_params = {}
            try:
                parsed_params = json.loads(row.get("params") or "{}")
            except Exception:
                parsed_params = {}
            parsed_result = {}
            try:
                parsed_result = json.loads(row.get("result") or "{}")
            except Exception:
                parsed_result = {}
            images = parsed_result.get("images") or []
            prompt_used = parsed_result.get("prompt_used")
            item = {
                "task_id": row.get("task_id"),
                "prompt": row.get("prompt") or "",
                "status": row.get("status"),
                "progress": row.get("progress") or 0,
                "created_at": row.get("created_at"),
                "finished_at": row.get("finished_at"),
                "images": images,
                "orchestrator_used": parsed_result.get("orchestrator_used"),
                "prompt_used": prompt_used,
                "params": parsed_params,
                "result": parsed_result,
            }
            items.append(item)
        return ok({
            "items": items,
            "pagination": {
                "page": page, "page_size": size, "total": total,
                "total_pages": max(1, (total + size - 1) // size),
                "has_next": page * size < total, "has_prev": page > 1,
            },
        })
    finally:
        conn.close()


# --- 文生图/漫画任务详情：补充 params / result 原始对象（保持与列表一致） ---
_T2I_DETAIL_PATH = "/t2i/tasks/{task_id}"
_T2I_DETAIL_FULL = (getattr(api, "prefix", "") or "") + _T2I_DETAIL_PATH
for _i in range(len(api.routes) - 1, -1, -1):
    if getattr(api.routes[_i], "path", None) == _T2I_DETAIL_FULL:
        del api.routes[_i]


@api.get(_T2I_DETAIL_PATH)
async def t2i_task_detail(task_id: str):
    """返回单个 image_tasks 记录，包含解析后的 params / result。"""
    conn = get_conn()
    try:
        row = get_by_id(conn, TABLES["image_tasks"], task_id, pk="task_id")
        if not row:
            raise ApiError("NOT_FOUND", "task_id 不存在")
        parsed_params = {}
        try:
            parsed_params = json.loads(row.get("params") or "{}")
        except Exception:
            parsed_params = {}
        parsed_result = {}
        try:
            parsed_result = json.loads(row.get("result") or "{}")
        except Exception:
            parsed_result = {}
        return ok({"task": {
            "task_id": row.get("task_id"),
            "status": row.get("status"),
            "progress": row.get("progress") or 0,
            "prompt": row.get("prompt") or "",
            "negative_prompt": row.get("negative_prompt") or "",
            "params": parsed_params,
            "error_message": row.get("error_message") or "",
            "result": parsed_result,
            "created_at": row.get("created_at"),
            "finished_at": row.get("finished_at"),
        }})
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 图生图专业编辑：统一多模式编辑入口 + 提示词反推
# ----------------------------------------------------------------------
_EDIT_TMP = os.path.join(_SERVER_DIR, ".tmp", "edit_uploads")
os.makedirs(_EDIT_TMP, exist_ok=True)

_EDIT_MODES = ("img2img", "inpaint", "outpaint", "upscale", "facefix", "style", "controlnet")


def _save_upload(upl, suffix):
    """把上传文件落到 D:/AI 下的临时目录（遵守 C 盘零写入），返回本地路径；无上传返回 None。"""
    if not upl:
        return None
    fn = "%s_%s" % (uuid.uuid4().hex[:12], suffix)
    p = os.path.join(_EDIT_TMP, fn)
    with open(p, "wb") as f:
        f.write(upl.file.read() if hasattr(upl, "file") else upl.read())
    return p


@api.post("/t2i/edit")
async def t2i_edit(
    image: UploadFile = File(...),
    mask: UploadFile = None,
    ref: UploadFile = None,
    mode: str = Form("img2img"),
    prompt: str = Form(""),
    negative: str = Form(""),
    strength: float = Form(0.65),
    steps: int = Form(20),
    cfg: float = Form(7.0),
    seed: int = Form(-1),
    sampler: str = Form("euler"),
    scheduler: str = Form("normal"),
    scale: int = Form(2),
    upscale_model: str = Form(""),
    denoise: float = Form(0.0),
    left: int = Form(0),
    top: int = Form(0),
    right: int = Form(0),
    bottom: int = Form(0),
    feathering: int = Form(20),
    control_type: str = Form("canny"),
    control_weight: float = Form(1.0),
    ip_weight: float = Form(0.8),
    method: str = Form("GFPGAN"),
):
    """图生图专业编辑统一入口：根据 mode 分发到对应 ComfyUI 工作流。

    mode ∈ {img2img 图生图重绘, inpaint 局部重绘, outpaint 扩图, upscale 高清放大,
            facefix 面部修复, style 风格迁移(IP-Adapter), controlnet ControlNet 结构控制}。
    image 必填；inpaint 必须带 mask；style 必须带 ref（风格参考图）。
    结果复制到 server/outputs 并经 /file?name= 服务；同时写入 image_tasks(kind=edit) 历史。
    """
    img_path = _save_upload(image, "img.png")
    mask_path = _save_upload(mask, "mask.png")
    ref_path = _save_upload(ref, "ref.png")
    if mode not in _EDIT_MODES:
        raise ApiError("INVALID_PARAMS", "未知编辑模式: %s" % mode)
    if mode == "inpaint" and not mask_path:
        raise ApiError("INVALID_PARAMS", "局部重绘必须提供蒙版（mask）")
    if mode == "style" and not ref_path:
        raise ApiError("INVALID_PARAMS", "风格迁移必须提供风格参考图（ref）")
    try:
        if mode == "img2img":
            out = await asyncio.to_thread(
                _comfy_img2img, img_path, prompt, float(strength),
                1280, 720, int(steps), float(cfg), int(seed))
        elif mode == "inpaint":
            out = await asyncio.to_thread(
                _comfy_inpaint, img_path, mask_path, prompt, negative,
                float(strength), int(steps), float(cfg), int(seed), sampler, scheduler)
        elif mode == "outpaint":
            out = await asyncio.to_thread(
                _comfy_outpaint, img_path, int(left), int(top), int(right), int(bottom), int(feathering),
                prompt, negative, float(strength), int(steps), float(cfg), int(seed), sampler, scheduler)
        elif mode == "upscale":
            out = await asyncio.to_thread(
                _comfy_upscale, img_path, int(scale), upscale_model or None,
                float(denoise), int(steps), float(cfg), prompt, negative, int(seed), sampler, scheduler)
        elif mode == "facefix":
            out = await asyncio.to_thread(face_restore, img_path, method)
        elif mode == "style":
            out = await asyncio.to_thread(
                _comfy_img2img_with_ipadapter, img_path, ref_path, float(ip_weight),
                prompt, negative, float(strength), int(steps), float(cfg), int(seed), sampler, scheduler)
        else:  # controlnet
            out = await asyncio.to_thread(
                _comfy_img2img_with_controlnet, img_path, prompt, negative,
                control_type, float(control_weight), float(strength), int(steps), float(cfg), int(seed), sampler, scheduler)
    except ApiError:
        raise
    except Exception as e:
        raise ApiError("GEN_FAILED", "图片编辑失败：%s" % str(e)[:300])
    # 复制结果到 OUTPUTS 并生成 /file?name= URL
    try:
        dst_name = "edit_%s.png" % uuid.uuid4().hex[:12]
        dst = os.path.join(OUTPUTS, dst_name)
        shutil.copyfile(out, dst)
        url = "/file?name=" + dst_name
    except Exception as e:
        raise ApiError("GEN_FAILED", "编辑结果保存失败：%s" % str(e)[:200])
    # 持久化历史 image_tasks(kind=edit)，供编辑页历史列表按 kind 过滤
    try:
        conn = get_conn()
        try:
            insert_row(conn, TABLES["image_tasks"], {
                "task_id": uid(),
                "prompt": prompt or mode,
                "negative_prompt": negative,
                "params": json.dumps(
                    {"kind": "edit", "mode": mode, "strength": strength, "steps": steps,
                     "cfg": cfg, "seed": seed, "control_type": control_type, "scale": scale},
                    ensure_ascii=False),
                "status": "succeeded", "progress": 100,
                "result": json.dumps({"url": url, "mode": mode}, ensure_ascii=False),
                "error_message": "", "pre_auth_id": "", "cost_credits": 0,
                "priority": 0, "retry_count": 0,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[edit] 保存 image_tasks 历史失败（不影响本次返回）: %s" % e)
    return ok({"url": url, "mode": mode, "local_path": out})


@api.post("/t2i/reverse")
async def t2i_reverse(image: UploadFile = File(...)):
    """提示词反推：上传参考图，返回结构化提示词（主体/构图/风格/正负向）。"""
    p = _save_upload(image, "rev.png")
    try:
        res = await asyncio.to_thread(reverse_prompt, p)
    except Exception as e:
        raise ApiError("GEN_FAILED", "提示词反推失败：%s" % str(e)[:200])
    return ok(res)


# ======================================================================
# 定点覆盖区（END）
# ======================================================================

# ======================================================================
# Director Agent Core 端点（full 版接线：POST /projects/{id}/director-run）
# ======================================================================
import threading  # noqa: E402
from realtime import publish as ws_publish  # noqa: E402
from director.scheduler import GlobalScheduler  # noqa: E402

# 进程内运行态表（骨架阶段：内存态，重启即丢；full 版可落 director_runs 表）
_DIRECTOR_RUNS = {}
_DIRECTOR_LOCK = threading.Lock()


def _director_on_progress(project_id, run_id, node_id, status, progress, info):
    """on_progress 回调：把节点进度经现有 WS 网关推送（§6 进度透出 / REDUN-01）。"""
    ws_publish("director:%s" % project_id, "progress", {
        "run_id": run_id, "node": node_id, "status": status,
        "progress": progress, "info": info,
    })
    # DirectorOrb v2：同一频道推送 orb_dag_state（悬浮球「正在执行」呼吸态数据源）
    ws_publish("director:%s" % project_id, "orb_dag_state", {
        "run_id": run_id, "node": node_id, "status": status,
        "progress": progress, "info": info,
    })


@api.post("/projects/{project_id}/director-run")
async def director_run(project_id: str, req: Request):
    """导演一站式生成入口（N01→N09 全链路编排）。

    请求体（均可选）：
      - story: 覆盖用剧本文本；缺省则从本项目已导入场次/分镜聚合（_build_project_story_text）
      - model_config: 透传给 ai_local（Ollama/ComfyUI/在线 本地或在线切换）
      - start_node: 断点续跑起点（默认 N01）
    返回：{ run_id, status: 'running', trace_id }；进度经 WS 频道 director:<project_id> 推送。
    """
    b = await _body(req)
    # 剧本：优先用请求体，否则聚合项目已导入场次/分镜
    story = (b.get("story") or "").strip()
    if not story:
        conn = get_conn()
        try:
            story = _build_project_story_text(conn, project_id)
        finally:
            conn.close()
    # 透传前端 Director 参数配置面板下发的全部生产参数（手册 §3.2/§3.3/§3.4）：
    # resolution/aspect_ratio/seed_strategy/enable_real_asset(N03)、enable_tail_frame/
    # priority_level(N04)、face_restore/enable_quality_check/target_*(N06/N08)、
    # tts/bgm/subtitle/transitions/post_process(N06/N08 合成)。
    # 禁止低配：原实现仅透传 story+model_config，导致全局参数被丢弃、N03/N04/N06 拿不到配置。
    params = dict(b) if isinstance(b, dict) else {}
    params["story"] = story
    if b.get("model_config"):
        params["model_config"] = b["model_config"]

    run_id = uid()
    trace_id = "DR-%s" % run_id  # 与 run_id 同源，便于关联

    def _worker():
        scheduler = GlobalScheduler()
        try:
            result = scheduler.run(
                project_id, user_id=SINGLE_USER_ID, params=params,
                start_node=b.get("start_node") or "N01",
                trace_id=trace_id,
                on_progress=lambda node_id, status, progress, info:
                    _director_on_progress(project_id, run_id, node_id, status, progress, info),
            )
            snap = result.to_dict()
            with _DIRECTOR_LOCK:
                _DIRECTOR_RUNS[run_id] = {
                    "status": "done" if result.ok else "failed",
                    "project_id": project_id, "trace_id": trace_id,
                    "final_output": result.final_output,
                    "snapshot": snap, "created_at": int(time.time()),
                }
            ws_publish("director:%s" % project_id, "completed", {
                "run_id": run_id, "ok": result.ok,
                "final_output": result.final_output,
                "node_status": snap.get("node_status"),
            })
        except Exception as e:  # 兜底：任何未捕获异常都收敛为 error，不静默吞（CLOSE-02）
            logger.exception("[director-run] 未捕获异常 run_id=%s", run_id)
            with _DIRECTOR_LOCK:
                _DIRECTOR_RUNS[run_id] = {
                    "status": "error", "project_id": project_id,
                    "trace_id": trace_id, "error": str(e),
                    "created_at": int(time.time()),
                }
            ws_publish("director:%s" % project_id, "error", {"run_id": run_id, "error": str(e)})

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    with _DIRECTOR_LOCK:
        _DIRECTOR_RUNS[run_id] = {
            "status": "running", "project_id": project_id,
            "trace_id": trace_id, "created_at": int(time.time()),
        }
    return ok({"run_id": run_id, "status": "running", "trace_id": trace_id})


@api.get("/projects/{project_id}/director-run")
async def director_runs_list(project_id: str):
    """列出该项目下的导演运行记录（内存态）。"""
    with _DIRECTOR_LOCK:
        items = [
            {"run_id": k, **v} for k, v in _DIRECTOR_RUNS.items()
            if v.get("project_id") == project_id
        ]
    return ok({"runs": items})


@api.get("/projects/{project_id}/director-run/{run_id}")
async def director_run_status(project_id: str, run_id: str):
    """查询单次导演运行的进度/结果。"""
    with _DIRECTOR_LOCK:
        r = _DIRECTOR_RUNS.get(run_id)
    if not r or r.get("project_id") != project_id:
        raise ApiError("NOT_FOUND", "run_id 不存在或不属于该项目")
    return ok(r)


# ======================================================================
# 真人资产 512 维身份特征向量：提取 / 状态查询（手册 §5.1 / §5.3）
# Layer 3 资产调用接口需向前端暴露「是否已提取 512 维系向量 + 触发预提取」，
# 以便用户确认真人资产化权限（缺引擎/无脸 → 诚实报错，不静默跳过身份锁定）。
# ======================================================================
FACE_VECTOR_DIM = 512
_OUTPUTS = os.path.join(os.path.dirname(_SERVER_DIR), "outputs")


def _resolve_asset_local_file(c):
    """把资产参考图 URL/路径解析为本地可读文件；找不到返回 None。"""
    if not c:
        return None
    if os.path.isfile(c):
        return c
    name = None
    if "name=" in c:
        from urllib.parse import parse_qs, urlparse
        name = (parse_qs(urlparse(c).query).get("name") or [None])[0]
    else:
        name = os.path.basename(c)
    if name:
        for base in (_OUTPUTS, os.path.join(_OUTPUTS, "assets"),
                     os.path.join(_OUTPUTS, "assets", "originals"),
                     # upload-image 等端点以 server/ 为 BASE 写入 server/outputs/（.pyc 行为）
                     os.path.join(_SERVER_DIR, "outputs"),
                     os.path.join(_SERVER_DIR, ".uploads")):
            p = os.path.join(base, os.path.basename(name))
            if os.path.isfile(p):
                return p
    return None


def _resolve_stage_ref_images(stage_id):
    """收集某阶段(含其姿势)的本地参考图路径列表，供 insightface 提取 512 维向量。"""
    conn = get_conn()
    try:
        rows = fetch_all(conn,
            "SELECT s.reference_image_url, a.asset_path, a.cover_path "
            "FROM asset_stages s JOIN assets a ON a.asset_id=s.asset_id "
            "WHERE s.stage_id=?", (stage_id,))
        s = rows[0] if rows else None
        urls = []
        if s:
            urls += [s.get("reference_image_url"), s.get("asset_path"), s.get("cover_path")]
        poses = fetch_all(conn, "SELECT reference_image_url FROM asset_poses WHERE stage_id=?", (stage_id,))
        for p in poses:
            urls.append(p.get("reference_image_url"))
    finally:
        conn.close()
    out = []
    for u in urls:
        fp = _resolve_asset_local_file(u)
        if fp:
            out.append(fp)
    return out


# ----------------------------------------------------------------------
# 导演 Agent 管家统筹 · 决策点 API（M2：手册 §3.2.4）
# ----------------------------------------------------------------------

@api.get("/projects/{project_id}/decisions")
async def list_project_decisions(project_id: str, status: str = None):
    """列出项目决策点（A 类待确认 / B 类自主）。status 可选 pending|resolved|skipped 过滤。"""
    from director.decision_engine import list_decisions
    decisions = list_decisions(project_id, status=status)
    return ok({
        "project_id": project_id,
        "decisions": [d.to_dict() for d in decisions],
        "count": len(decisions),
    })


@api.post("/projects/{project_id}/decisions/{decision_id}/resolve")
async def resolve_project_decision(project_id: str, decision_id: str, req: Request):
    """提交用户对某决策点的答案（手册 §3.2.4）。body: { answer: str }。"""
    from director.decision_engine import resolve_decision
    b = await _body(req)
    answer = (b.get("answer") if isinstance(b, dict) else None)
    if answer is None:
        raise ApiError("40001", "缺少 answer 字段")
    dp = resolve_decision(project_id, decision_id, str(answer))
    return ok({"decision": dp.to_dict()})


# ----------------------------------------------------------------------
# M2 资源注册表 · 能力维度声明 API（手册 §2.2 / §2.6）
# ----------------------------------------------------------------------

@api.get("/resource/catalog")
async def resource_catalog():
    """M2 资源注册表：模型/节点/LoRA 清单 + 各自声明实现的能力维度 + 首尾帧分级。

    在线（ComfyUI 可达）经 ResourceCatalog.refresh() 实时探测并标注；
    离线（ComfyUI 不可达）尽力从磁盘探测，仍无则回退到「已声明实现」清单，
    保证前端资源清单区始终可展示（不阻断；online 字段如实标注）。
    """
    from capabilities import all_dimensions, declared_implementations
    from intent_engine.resource_catalog import ResourceCatalog, annotate_catalog
    online = True
    cat = None
    try:
        rc = ResourceCatalog()
        await rc.refresh()
        cat = rc.catalog
    except Exception as e:
        online = False
        logger.warning("[resource/catalog] ComfyUI 探测失败，回退离线目录：%s", e)
        cat = {
            "checkpoints": [], "loras": [], "ip_adapters": [], "controlnets": [],
            "vaes": [], "video_models": [], "node_families": {}, "object_info": {},
        }
    # 离线也尝试磁盘探测（comfy_env 扫描）；标注 capabilities 后返回
    try:
        annotate_catalog(cat)
    except Exception as e:
        logger.warning("[resource/catalog] annotate 失败：%s", e)
    return ok({
        "online": online,
        "capability_dimensions": all_dimensions(),
        "models": (cat.get("checkpoints") or []) + (cat.get("video_models") or []),
        "ip_adapters": cat.get("ip_adapters") or [],
        "controlnets": cat.get("controlnets") or [],
        "loras": cat.get("loras") or [],
        "node_families": cat.get("node_families") or {},
        "declared_implementations": declared_implementations(),
    })


@api.get("/assets/{asset_id}/face-vectors")
async def get_asset_face_vectors(asset_id: str):
    """批量返回该角色资产下每个阶段的 512 维向量状态（前端一次拉取渲染徽标）。"""
    conn = get_conn()
    try:
        rows = fetch_all(conn,
            "SELECT stage_id, face_vector FROM asset_stages WHERE asset_id=?", (asset_id,))
    finally:
        conn.close()
    vectors = {}
    for r in rows:
        has = bool(r.get("face_vector"))
        vectors[r["stage_id"]] = {"has_vector": has, "dim": FACE_VECTOR_DIM if has else 0}
    return ok({"asset_id": asset_id, "vectors": vectors})


@api.get("/assets/{asset_id}/stages/{stage_id}/face-vector")
async def get_stage_face_vector(asset_id: str, stage_id: str):
    """查询单个阶段的 512 维向量状态。"""
    try:
        import features
        vec = features.load_face_vector_by_stage(stage_id)
    except Exception:
        vec = None
    return ok({"stage_id": stage_id, "has_vector": vec is not None,
               "dim": FACE_VECTOR_DIM if vec is not None else 0})


@api.post("/assets/{asset_id}/stages/{stage_id}/face-vector/extract")
async def extract_stage_face_vector(asset_id: str, stage_id: str):
    """预提取并落库某阶段的 512 维身份特征向量（手册 §5.1）。

    缺引擎(insightface/onnxruntime)/无脸 → 诚实抛 FeatureUnavailable 错误码，
    由前端清晰告知用户需放入 buffalo_l 权重，禁止静默跳过身份锁定。
    """
    imgs = _resolve_stage_ref_images(stage_id)
    if not imgs:
        raise ApiError("FACE_NO_IMAGE",
                       "该阶段无可解析的参考图（请先在资产编辑中上传参考照）")
    try:
        import features
        vec = features.extract_face_vector(imgs)   # 多图取均值；无脸/缺引擎 → 抛错
        stored = features.store_face_vector(stage_id, vec)
    except Exception as e:
        code = getattr(e, "code", "FACE_EXTRACT_FAILED")
        raise ApiError(code, str(e))
    if not stored:
        raise ApiError("FACE_STORE_FAILED", "512 维向量提取成功但落库失败")
    return ok({"stage_id": stage_id, "has_vector": True, "dim": FACE_VECTOR_DIM})


@api.post("/assets/{asset_id}/generate-card")
async def generate_character_card_endpoint(asset_id: str, req: Request):
    """P1 角色卡 AI 生成（导演agent优化草稿 #1）：经意图翻译引擎产出首张定妆照。

    请求体（均可选）：
      {"attrs": {"name","age","gender","appearance":[...]},
       "style_tags": ["写实","电影感"],
       "generation_params": {"width":512,"height":768,"steps":28,"cfg":8.5},
       "negative_prompt": "..."}
    铁律 1.1/1.2：走 intent_engine/templates/character_card_gen.json，确定性 seed，绝不手搓节点。
    ComfyUI 不可用 → 诚实抛 COMFY_UNAVAILABLE（调用方清晰告知用户）。
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    attrs = body.get("attrs")
    style_tags = body.get("style_tags")
    generation_params = body.get("generation_params")
    negative_prompt = body.get("negative_prompt")
    try:
        import ai_local
        path = ai_local.generate_character_card(
            asset_id, attrs=attrs, style_tags=style_tags,
            generation_params=generation_params, negative_prompt=negative_prompt)
    except Exception as e:
        code = getattr(e, "code", "CHARACTER_CARD_FAILED")
        raise ApiError(code, str(e))
    return ok({"asset_id": asset_id, "cover_url": path})


# ----------------------------------------------------------------------
# P1 角色卡增强管线（M3 · 六步确定性管线）：产出定妆照并推送 translator/insight 到悬浮球
# 手册 §4.2「从剧本生成角色」入口后端落地；项目作用域，project_id 用于 WS 频道范围。
# ----------------------------------------------------------------------

@api.post("/projects/{project_id}/assets/{asset_id}/generate-card-v2")
async def generate_character_card_v2_endpoint(project_id: str, asset_id: str, req: Request):
    """六步确定性角色卡生成（设计 §8）：LLM 五要素 → 能力匹配 → 结构化 prompt
    → 确定性 seed → 多角度参考图 + 512 向量入库 → 品控回检。

    请求体（均可选）：{"style_tags":["写实","电影感"], "era":"民国",
                       "poses":["front","side"], "setting_text":"角色设定原文"}
    project_id 仅用于把 translator/insight 事件推到正确的悬浮球频道（director/translator:{pid}）。
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    style_tags = body.get("style_tags")
    era = body.get("era")
    poses = body.get("poses")
    setting_text = body.get("setting_text")
    # M3（手册 §3.2.3）：角色卡生成属 P1 资产初始化，攒批推送 A 类决策点（幂等）。
    try:
        from director.decision_engine import preflight_check
        preflight_check(project_id, "P1")
    except Exception as _e:
        logger.warning("[card-v2] preflight P1 失败（忽略）: %s", _e)
    try:
        import ai_local
        # M3 §4.1：防御式构建资源目录（离线注入静态 resource_catalog.json，不触网），
        # 传给六步管线以触发真实的「导演选型」可视化推送（catalog 构建失败则跳过，不影响出图）。
        catalog = None
        try:
            import json as _json
            from intent_engine.resource_catalog import ResourceCatalog
            _cat_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resource_catalog.json")
            with open(_cat_path, "r", encoding="utf-8") as _f:
                _cat = _json.load(_f)
            catalog = ResourceCatalog(catalog=_cat)  # 离线注入：不触网，仅静态目录
        except Exception as _ce:
            logger.warning("[card-v2] 资源目录构建失败（选型可视化将跳过）: %s", _ce)
        res = ai_local.generate_character_card_v2(
            asset_id, setting_text=setting_text, era=era,
            style_tags=style_tags, poses=poses, catalog=catalog, project_id=project_id)
    except Exception as e:
        code = getattr(e, "code", "CHARACTER_CARD_V2_FAILED")
        raise ApiError(code, str(e))
    return ok({"asset_id": asset_id, "project_id": project_id, "result": res})


# ======================================================================
# P1 M1 角色身份系统（身份卡）：GET/PATCH /assets/{id}/identity-card
# 无创性：仅读写 assets 新增的 4 列 + 主阶段 face_vector，不影响既有资产行为。
# ======================================================================

@api.get("/assets/{asset_id}/identity-card")
async def get_identity_card(asset_id: str):
    """读取角色身份卡（含默认一致句兜底 + 主阶段 512 维向量状态）。"""
    import features
    conn = get_conn()
    try:
        card = features.get_identity_card(conn, asset_id)
    finally:
        conn.close()
    if not card:
        raise ApiError("ASSET_NOT_FOUND", "角色资产不存在: %s" % asset_id)
    return ok(card)


@api.patch("/assets/{asset_id}/identity-card")
async def patch_identity_card(asset_id: str, req: Request):
    """编辑身份卡字段（服装/发型/体型/一致句），持久化。"""
    import features
    try:
        body = await req.json()
    except Exception:
        body = {}
    allowed = ("outfit", "hairstyle", "body_type", "consistency_sentence")
    data = {k: body[k] for k in allowed if k in body}
    if not data:
        raise ApiError("EMPTY_PATCH", "未提供任何身份卡字段")
    conn = get_conn()
    try:
        row = fetch_one(conn, "SELECT asset_id FROM assets WHERE asset_id=?", (asset_id,))
        if not row:
            raise ApiError("ASSET_NOT_FOUND", "角色资产不存在: %s" % asset_id)
        features.save_identity_card(conn, asset_id, data)
    finally:
        conn.close()
    return ok({"asset_id": asset_id, "updated": list(data.keys())})


# ======================================================================
# P1 M2 Lock 锁定阶段（标准肖像 + 参考图集）：POST generate / GET / regenerate
# 无创性：仅读写 asset_reference_set 表，不影响既有资产行为。
# 红线：ComfyUI / insightface 缺失时诚实抛错，绝不静默降级成"无锁脸"。
# ======================================================================

@api.post("/assets/{asset_id}/reference-set/generate")
async def generate_reference_set_endpoint(asset_id: str):
    """一键生成角色参考图集：标准肖像（正面中性）+ 4 张参考图
    （front/three_quarter/expression/full_body，光线一致），每张提取 512 维向量落库。

    依赖缺失（ComfyUI 离线 / IP-Adapter 权重缺失 / insightface 缺失）时诚实抛错，
    绝不以"无锁脸"空图冒充成功。
    """
    import features
    conn = get_conn()
    try:
        row = fetch_one(conn, "SELECT asset_id FROM assets WHERE asset_id=?", (asset_id,))
    finally:
        conn.close()
    if not row:
        raise ApiError("ASSET_NOT_FOUND", "角色资产不存在: %s" % asset_id)
    try:
        # 同步阻塞的 ComfyUI 调用放入线程，避免阻塞事件循环
        portrait = await asyncio.to_thread(features.generate_standard_portrait, asset_id)
        refset = await asyncio.to_thread(features.generate_reference_set, asset_id)
    except Exception as e:
        code = getattr(e, "code", "REFERENCE_SET_GEN_FAILED")
        raise ApiError(code, str(e))
    return ok({
        "asset_id": asset_id,
        "standard_portrait": portrait,
        "reference_set": refset,
    })


@api.get("/assets/{asset_id}/reference-set")
async def get_reference_set_endpoint(asset_id: str):
    """读取角色参考图集（标准肖像 + 4 张参考图 + 每张 512 维向量状态）。"""
    import features
    conn = get_conn()
    try:
        items = features.get_reference_set(conn, asset_id)
    finally:
        conn.close()
    if not items:
        raise ApiError("REFERENCE_SET_EMPTY", "该角色尚未生成参考图集: %s" % asset_id)
    return ok({"asset_id": asset_id, "items": items})


@api.post("/assets/{asset_id}/reference-set/{ref_kind}/regenerate")
async def regenerate_reference_endpoint(asset_id: str, ref_kind: str):
    """重生成单张参考图 / 标准肖像（按 ref_kind 幂等覆盖）。"""
    import features
    conn = get_conn()
    try:
        row = fetch_one(conn, "SELECT asset_id FROM assets WHERE asset_id=?", (asset_id,))
    finally:
        conn.close()
    if not row:
        raise ApiError("ASSET_NOT_FOUND", "角色资产不存在: %s" % asset_id)
    try:
        item = await asyncio.to_thread(features.regenerate_one, asset_id, ref_kind)
    except ValueError as e:
        raise ApiError("BAD_REF_KIND", str(e))
    except Exception as e:
        code = getattr(e, "code", "REFERENCE_REGEN_FAILED")
        raise ApiError(code, str(e))
    return ok({"asset_id": asset_id, "item": item})


# ======================================================================
# P1 M3/M5 权重前置诚实探测：GET /consistency/readiness
# 返回四杠杆所需权重（ipadapter / clip_vision / controlnet / insightface）就绪状态；
# 缺失时列出缺失项 + 错误明细，供设置页红色"缺失"提示与生成时诚实报错。
# ======================================================================

@api.get("/consistency/readiness")
async def consistency_readiness_endpoint():
    """探测 Lock-Then-Animate 四杠杆所需权重是否就绪（诚实，绝不假装可用）。"""
    from capabilities.consistency import ConsistencyCapability
    cap = ConsistencyCapability()
    try:
        readiness = cap.probe_readiness()
    except Exception as e:
        raise ApiError("READINESS_PROBE_FAILED", str(e))
    return ok(readiness)


# ======================================================================
# P1 M4 复用阶段：角色卡跨项目深拷贝导入
# ----------------------------------------------------------------------
# 红线：禁止浅拷贝引用 —— 必须实体复制 512 维向量(asset_stages.face_vector)
#       + 身份卡(assets 四列) + 参考图集(asset_reference_set)，不可仅指向源。
# 同名幂等：同一 project 下已存在同名 role 资产则跳过，返回既有（不产生重复）。
# ======================================================================

def import_role_card(source_asset_id, target_project_id):
    """P1 M4 角色卡跨项目深拷贝（输入→处理→输出）。

    处理1：同名幂等（目标项目已有同名 role → 返回既有，skipped）
    处理2：复制 assets 行（新 uid() 主键，is_global=0）
    处理3：深拷贝 asset_stages(含 face_vector) / asset_reference_set(含 face_vector)
           —— 全部实体复制，主键重生成，杜绝浅拷贝 / 共享引用（红线）
    输出：{asset_id, name, imported, skipped}
    红线：非 role 资产 → ApiError(NOT_ROLE_ASSET)；源不存在 → ApiError(ASSET_NOT_FOUND)
    """
    conn = get_conn()
    try:
        # 源角色资产（含身份卡四列）；跨项目复用，不限 project
        src = fetch_one(conn,
            "SELECT asset_id, name, type, role_type, description, cover_url, tags, "
            "outfit, hairstyle, body_type, consistency_sentence "
            "FROM assets WHERE asset_id=?", (source_asset_id,))
        if not src:
            raise ApiError("ASSET_NOT_FOUND", "源资产不存在: %s" % source_asset_id)
        if (src.get("type") or "") != "role":
            raise ApiError("NOT_ROLE_ASSET", "仅支持角色卡导入，源资产类型=%s" % src.get("type"))
        name = src.get("name") or source_asset_id

        # 同名幂等：同一 project 下已存在同名 role 资产则跳过
        exist = fetch_one(conn,
            "SELECT asset_id, name FROM assets WHERE project_id=? AND is_global=0 "
            "AND type='role' AND name=?", (target_project_id, name))
        if exist:
            return {"asset_id": exist["asset_id"], "name": exist["name"],
                    "imported": False, "skipped": True}

        new_id = uid()
        now = int(time.time())
        params = {
            "asset_id": new_id, "user_id": SINGLE_USER_ID, "name": name, "type": "role",
            "description": src.get("description") or "", "cover_url": src.get("cover_url") or "",
            "tags": src.get("tags"), "is_global": 0, "project_id": target_project_id,
            "created_at": now, "updated_at": now,
            "outfit": src.get("outfit") or "", "hairstyle": src.get("hairstyle") or "",
            "body_type": src.get("body_type") or "",
            "consistency_sentence": src.get("consistency_sentence") or "",
        }
        if src.get("role_type"):
            params["role_type"] = src["role_type"]
        cols = list(params.keys())
        conn.execute("INSERT INTO assets (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                     tuple(params.values()))

        # 深拷贝 asset_stages（含 512 维向量 face_vector 实体复制）
        stages = fetch_all(conn,
            "SELECT stage_id, stage_name, age, reference_image_url, face_vector, selected "
            "FROM asset_stages WHERE asset_id=?", (source_asset_id,))
        for st in stages:
            conn.execute(
                "INSERT INTO asset_stages (stage_id, asset_id, stage_name, age, "
                "reference_image_url, face_vector, selected, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (uid(), new_id, st.get("stage_name"), st.get("age") or 0,
                 st.get("reference_image_url") or "", st.get("face_vector"),
                 st.get("selected") or 0, now))

        # 深拷贝 asset_reference_set（参考图集 + 每张向量实体复制）
        refs = fetch_all(conn,
            "SELECT ref_kind, image_url, face_vector FROM asset_reference_set WHERE asset_id=?",
            (source_asset_id,))
        for rf in refs:
            conn.execute(
                "INSERT INTO asset_reference_set (ref_id, asset_id, ref_kind, image_url, face_vector, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (uid(), new_id, rf.get("ref_kind"), rf.get("image_url") or "",
                 rf.get("face_vector"), now))

        # 深拷贝独立 identity_card 表（手册 M4 copy_identity_card，实体复制非共享引用）
        ic = fetch_one(conn,
            "SELECT face_vector, outfit, hairstyle, body_type, consistency_sentence "
            "FROM identity_card WHERE asset_id=?", (source_asset_id,))
        if ic:
            conn.execute(
                "INSERT INTO identity_card (asset_id, face_vector, outfit, hairstyle, body_type, consistency_sentence) "
                "VALUES (?,?,?,?,?,?)",
                (new_id, ic.get("face_vector"), ic.get("outfit") or "",
                 ic.get("hairstyle") or "", ic.get("body_type") or "",
                 ic.get("consistency_sentence") or ""))

        # 深拷贝独立 standard_portrait 表
        sp = fetch_one(conn,
            "SELECT image_path, created_at FROM standard_portrait WHERE asset_id=?",
            (source_asset_id,))
        if sp:
            conn.execute(
                "INSERT INTO standard_portrait (asset_id, image_path, created_at) VALUES (?,?,?)",
                (new_id, sp.get("image_path") or "", sp.get("created_at")))

        conn.commit()
        log_operation(conn, "asset_import", object_type="asset", object_id=new_id,
                      object_name=name, diff={"type": "role", "source_asset_id": source_asset_id})
        return {"asset_id": new_id, "name": name, "imported": True, "skipped": False}
    except ApiError:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("[assets/import] 角色卡深拷贝失败")
        raise ApiError("ROLE_IMPORT_FAILED", "角色卡导入失败：%s" % e)
    finally:
        conn.close()


@api.post("/projects/{project_id}/assets/import")
async def import_role_asset_endpoint(project_id: str, req: Request):
    """深拷贝一个历史角色卡到本项目（含 512 维向量 + 身份卡 + 参考图集）。

    请求体：{ asset_id: <源角色资产 id> }
    返回：{ asset:{asset_id,name,type}, imported:bool, skipped:bool }
    """
    b = await _body(req)
    src_id = str(b.get("asset_id") or "").strip()
    if not src_id:
        raise ApiError("INVALID_PARAMS", "缺少 asset_id")
    result = import_role_card(src_id, project_id)
    return ok({
        "asset": {"asset_id": result["asset_id"], "name": result["name"], "type": "role"},
        "imported": result["imported"], "skipped": result["skipped"],
    })


@api.get("/projects/{project_id}/assets/importable-roles")
async def importable_roles_endpoint(project_id: str):
    """列出可被导入的历史角色卡（其他项目 / 全局库中的 role 资产，排除本项目）。"""
    conn = get_conn()
    try:
        rows = fetch_all(conn,
            "SELECT a.asset_id, a.name, a.role_type, a.cover_url, a.project_id, "
            "p.name AS project_name "
            "FROM assets a LEFT JOIN projects p ON a.project_id=p.project_id "
            "WHERE a.type='role' AND a.project_id<>? AND a.is_global IN (0,1) "
            "ORDER BY a.created_at DESC LIMIT 200", (project_id,))
        # 排除本项目已有同名（幂等提示）
        exist_rows = fetch_all(conn,
            "SELECT name FROM assets WHERE project_id=? AND is_global=0 AND type='role'",
            (project_id,))
        exist_names = {r["name"] for r in exist_rows if r.get("name")}
        items = []
        for r in rows:
            nm = r.get("name") or r["asset_id"]
            items.append({
                "asset_id": r["asset_id"], "name": nm,
                "role_type": r.get("role_type"), "cover_url": r.get("cover_url") or "",
                "project_id": r.get("project_id"), "project_name": r.get("project_name") or "全局库",
                "already_imported": nm in exist_names,
            })
    finally:
        conn.close()
    return ok({"items": items})


# ======================================================================
# DirectorOrb v2（悬浮球化身）路由挂载
# ----------------------------------------------------------------------
# 设计（方案 §二）：悬浮球 = 导演 Agent 的前台人格化化身，只聚合既有数据，
# 不新增独立检测。全部接口挂在既有 api（prefix=/api/v1）下，受
# module_flags.director_orb_v2 灰度开关保护（默认关闭）。
# ======================================================================
from orb.routes import register_orb_routes  # noqa: E402
register_orb_routes(api)


# ======================================================================
# LoRA 训练模块端点（手册 §8 / 设计思路 §8）
# ----------------------------------------------------------------------
# 信封 {code,data,message,request_id,timestamp}；列表 {items,pagination}。
# 引擎缺失 / 打标缺失 / ComfyUI 离线均诚实抛错（ApiError），禁止静默失败（手册 §3.1/§11.4）。
# ======================================================================
from typing import List  # noqa: E402
from lora import (  # noqa: E402
    engine as _lora_engine, dataset as _lora_dataset,
    register as _lora_register, task_runner as _lora_runner,
    preset as _lora_preset, captioner as _lora_captioner,
)
from lora.engine import FeatureUnavailable as _LoraFeatureUnavailable  # noqa: E402


@api.get("/lora/presets")
async def lora_presets():
    """前端初始化：预设清单 + 训练引擎可用性（引擎缺失时前端据此提示安装）。"""
    return ok({"presets": _lora_preset.list_presets(), "engine": _lora_engine.detect_engine()})


@api.post("/lora/train")
async def lora_create_train(request: Request):
    """创建训练任务（手册 §5.1 / §8）。

    入参：name, lora_type(character|style), base_model, trigger_word,
          dataset_dir(或 asset_id), preset, project_id(可选), seed(可选)。
    角色 LoRA 必须提供 trigger_word；数据集不足显式报错（不静默放行）。
    """
    body = await request.json()
    name = (body.get("name") or "").strip()
    lora_type = body.get("lora_type")
    if lora_type not in ("character", "style"):
        raise ApiError("INVALID_PARAMS", "lora_type 必须为 character 或 style")
    if not name:
        raise ApiError("MISSING_FIELD", "缺少 name")
    trigger_word = (body.get("trigger_word") or "").strip()
    if lora_type == "character" and not trigger_word:
        raise ApiError("MISSING_FIELD", "角色 LoRA 必须提供 trigger_word（LoRA 激活开关）")
    preset_key = body.get("preset") or _lora_preset.DEFAULT_PRESET
    asset_id = body.get("asset_id")
    dataset_dir = body.get("dataset_dir")

    if asset_id:
        dataset_dir = _lora_dataset.new_dataset_dir()
        try:
            _lora_dataset.import_from_assets(asset_id, dataset_dir)
        except Exception as e:
            raise ApiError("INVALID_PARAMS", "从资产库导入参考图失败：%s" % str(e)[:160])
    if not dataset_dir or not os.path.isdir(dataset_dir):
        raise ApiError("MISSING_FIELD", "缺少 dataset_dir（或 asset_id 导入失败）")
    vd = _lora_dataset.validate_dataset(dataset_dir, lora_type)
    if not vd["ok"]:
        raise ApiError("INVALID_PARAMS", vd["message"])

    trace_id = uid()
    task = {
        "id": uid(), "project_id": body.get("project_id"),
        "name": name, "lora_type": lora_type,
        "base_model": body.get("base_model") or "",
        "trigger_word": trigger_word, "dataset_dir": dataset_dir,
        "caption_status": "none",
        "params": json.dumps({"preset": preset_key, "seed": body.get("seed", 42)}),
        "status": "queued", "progress": 0, "current_step": 0, "total_steps": 0,
        "loss_log": "[]", "associated_asset_id": asset_id, "trace_id": trace_id,
    }
    conn = get_conn()
    try:
        insert_row(conn, "lora_train_tasks", task)
        conn.commit()
    finally:
        conn.close()
    # 诚实告知引擎可用性（缺失时任务仍入队，由 Worker 执行时显式失败并提示安装路径）
    eng = _lora_engine.detect_engine()
    return ok({"task": task, "engine": eng})


@api.get("/lora/train")
async def lora_list_train(page: int = 1, size: int = 20):
    conn = get_conn()
    try:
        total = count_rows(conn, "lora_train_tasks")
        rows = list_rows(conn, "lora_train_tasks", order_by="created_at DESC",
                         limit=size, offset=(page - 1) * size)
    finally:
        conn.close()
    return ok(paginate([dict(r) for r in rows], total, page, size))


@api.get("/lora/train/{task_id}")
async def lora_get_train(task_id: str):
    conn = get_conn()
    try:
        row = get_by_id(conn, "lora_train_tasks", task_id)
    finally:
        conn.close()
    if not row:
        raise ApiError("NOT_FOUND", "训练任务不存在")
    return ok(dict(row))


@api.post("/lora/train/{task_id}/cancel")
async def lora_cancel_train(task_id: str):
    _lora_runner.cancel_training(task_id)
    return ok({"task_id": task_id, "status": "cancelled"})


@api.post("/lora/caption")
async def lora_caption(request: Request):
    """对数据集自动打标（手册 §4.2 / §8）。引擎/打标脚本缺失诚实报错。"""
    body = await request.json()
    dataset_dir = body.get("dataset_dir")
    lora_type = body.get("lora_type")
    trigger_word = body.get("trigger_word") or ""
    if not dataset_dir:
        raise ApiError("MISSING_FIELD", "缺少 dataset_dir")
    if lora_type not in ("character", "style"):
        raise ApiError("INVALID_PARAMS", "lora_type 必须为 character 或 style")
    try:
        res = _lora_captioner.caption_dataset(dataset_dir, lora_type, trigger_word)
    except _LoraFeatureUnavailable as e:
        raise ApiError("AI_PROVIDER_TIMEOUT", e.reason)
    return ok(res)


@api.post("/lora/dataset/upload")
async def lora_upload_dataset(files: List[UploadFile] = File(...), lora_type: str = Form("style")):
    """上传图片构建数据集（手册 §4.1）。校验格式 + 数量，返回 dataset_dir 与校验结果。"""
    if lora_type not in ("character", "style"):
        raise ApiError("INVALID_PARAMS", "lora_type 必须为 character 或 style")
    ds = _lora_dataset.new_dataset_dir()
    saved = [(f.filename, await f.read()) for f in files]
    try:
        res = _lora_dataset.upload_dataset(saved, ds)
    except ValueError as e:
        raise ApiError("INVALID_PARAMS", str(e))
    vd = _lora_dataset.validate_dataset(ds, lora_type)
    return ok({"dataset_dir": ds, "count": res["count"],
               "rejected": res.get("rejected", []), "validation": vd})


@api.get("/lora/models")
async def lora_list_models(page: int = 1, size: int = 50):
    conn = get_conn()
    try:
        total = count_rows(conn, "lora_models")
        rows = list_rows(conn, "lora_models", order_by="created_at DESC",
                         limit=size, offset=(page - 1) * size)
    finally:
        conn.close()
    return ok(paginate([dict(r) for r in rows], total, page, size))


@api.post("/lora/models/{model_id}/register")
async def lora_register_model(model_id: str):
    """手动（重）注册训练 LoRA 到 catalog（手册 §8）。"""
    try:
        row = _lora_register.manual_register(model_id)
    except ValueError as e:
        raise ApiError("NOT_FOUND", str(e))
    return ok(dict(row))


@api.post("/lora/models/{model_id}/enable")
async def lora_enable_model(model_id: str):
    _lora_register.set_model_status(model_id, "active")
    return ok({"model_id": model_id, "status": "active"})


@api.post("/lora/models/{model_id}/disable")
async def lora_disable_model(model_id: str):
    _lora_register.set_model_status(model_id, "disabled")
    return ok({"model_id": model_id, "status": "disabled"})


@api.delete("/lora/models/{model_id}")
async def lora_delete_model(model_id: str):
    try:
        _lora_register.delete_model(model_id)
    except ValueError as e:
        raise ApiError("NOT_FOUND", str(e))
    return ok({"model_id": model_id, "deleted": True})


@api.post("/lora/models/{model_id}/validate")
async def lora_validate_model(model_id: str):
    """生成「有 LoRA vs 无 LoRA」验证对比图（手册 §9）。依赖本地 ComfyUI + LoRA 可被加载。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM lora_models WHERE id=?", (model_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise ApiError("NOT_FOUND", "LoRA 模型不存在")
    try:
        urls = _lora_runner.generate_validation_pair(dict(row))
    except _LoraFeatureUnavailable as e:
        raise ApiError("AI_PROVIDER_TIMEOUT", e.reason)
    except Exception as e:
        raise ApiError("INTERNAL_ERROR", "验证生成失败：%s" % str(e)[:200])
    return ok(urls)


# ======================================================================
# 一键重构前端（FS-01/FS-03 合规）：执行 scripts/build_frontend.sh
# ======================================================================
# 设计要点：
#   - 复用已验证的 build_frontend.sh（其内已显式 export TMPDIR/TEMP/TMP 全部指向
#     D:\AI\manju_agent\.tmp\vite，遵守 C 盘零写入纪律）；仅调用，不手抄命令。
#   - 构建属长耗时同步操作（~15s），用 asyncio.to_thread 丢到工作线程，避免阻塞事件循环。
#   - 线程锁防并发构建互相踩踏（vite 同时跑两次会写同一 dist 且 esbuild 锁冲突）。
#   - 跨平台进程树清理：Windows 用 taskkill /T，POSIX 用 os.killpg，避免超时残留 node 孤儿。
#   - 完整日志落 D 盘 .srvlogs，接口仅返回尾部摘要，防止响应体过大。
_REBUILD_LOCK = threading.Lock()
_REBUILD_BASH = "C:/Program Files/Git/bin/bash.exe"


@api.post("/system/rebuild-frontend")
async def system_rebuild_frontend():
    """一键重构前端：执行 scripts/build_frontend.sh，复用其 TMPDIR 环境（C 盘零写入合规）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # D:/AI/manju_agent
    script = os.path.join(root, "scripts", "build_frontend.sh")
    if not os.path.isfile(script):
        raise ApiError("NOT_FOUND", "构建脚本不存在: %s" % script)

    bash_exe = _REBUILD_BASH if os.path.isfile(_REBUILD_BASH) else "bash"
    log_dir = os.path.join(root, "server", ".srvlogs")
    log_path = os.path.join(log_dir, "rebuild_frontend.log")

    def _kill_tree(pid):
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=15)
            else:
                import signal as _sig
                os.killpg(os.getpgid(pid), _sig.SIGKILL)
        except Exception:
            try:
                os.kill(pid, 9)
            except Exception:
                pass

    def _run():
        if not _REBUILD_LOCK.acquire(blocking=False):
            return {"success": False, "exit_code": -1,
                    "busy": True, "output": "已有前端构建任务在执行，请稍后再试。"}
        try:
            os.makedirs(log_dir, exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as lf:
                proc = subprocess.Popen(
                    [bash_exe, script],
                    cwd=root,
                    env=dict(os.environ),
                    stdout=lf, stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=(sys.platform != "win32"),
                )
                try:
                    proc.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    _kill_tree(proc.pid)
                    return {"success": False, "exit_code": -1, "busy": False,
                            "output": "前端构建超时（>300s），已终止构建进程。"}
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                    tail = lf.read()[-4000:]
            except Exception:
                tail = ""
            return {
                "success": proc.returncode == 0,
                "exit_code": proc.returncode,
                "busy": False,
                "output": tail,
                "log_path": log_path,
            }
        except Exception as e:
            return {"success": False, "exit_code": -1, "busy": False,
                    "output": "前端构建异常：%s" % str(e)[:400]}
        finally:
            _REBUILD_LOCK.release()

    result = await asyncio.to_thread(_run)
    return ok(result)


# ======================================================================
# P3 M1 Skills 技能库（编排层可插拔）
# ----------------------------------------------------------------------
# 衔接（手册 §0.3）：技能执行 = fill_template → 组装 P0 M4 ProductionIntent
#                   → 调 P0 M3 translator.translate（真调，非返回固定文本）。
# 红线（§5）：禁空壳、禁 intent_template 不符 ProductionIntent 结构、禁 hash() 生成 ID。
# ======================================================================
from skills import (  # noqa: E402
    load_skills as _sk_load_skills,
    get_skill as _sk_get_skill,
    fill_template as _sk_fill_template,
    run_skill as _sk_run_skill,
    create_skill as _sk_create_skill,
    update_skill as _sk_update_skill,
    delete_skill as _sk_delete_skill,
)


@api.get("/skills")
async def list_skills_endpoint(req: Request):
    """列出技能库：内置技能（builtin/*.json）+ 我的技能（skills 表）。"""
    skills = _sk_load_skills(SINGLE_USER_ID)
    return ok({"skills": skills})


@api.post("/skills")
async def create_skill_endpoint(req: Request):
    """保存自定义技能。入参：name, intent_template(dict, 须符合 ProductionIntent 结构), params(可选)。"""
    b = await _body(req)
    name = str(b.get("name") or "").strip()
    intent_template = b.get("intent_template")
    if not name or not isinstance(intent_template, dict):
        raise ApiError("INVALID_PARAMS", "name 与 intent_template(dict) 必填")
    if not intent_template.get("task_type"):
        raise ApiError("INVALID_PARAMS", "intent_template.task_type 必填")
    sid = _sk_create_skill(name, intent_template, b.get("params") or {}, SINGLE_USER_ID)
    return ok({"skill_id": sid})


@api.get("/skills/{skill_id}")
async def get_skill_endpoint(skill_id: str):
    s = _sk_get_skill(skill_id)
    if not s:
        raise ApiError("SKILL_NOT_FOUND", "技能不存在: %s" % skill_id)
    return ok({"skill": s})


@api.put("/skills/{skill_id}")
async def update_skill_endpoint(skill_id: str, req: Request):
    b = await _body(req)
    ok_ = _sk_update_skill(
        skill_id,
        name=b.get("name"),
        intent_template=b.get("intent_template"),
        params=b.get("params"),
    )
    if not ok_:
        raise ApiError("SKILL_NOT_FOUND", "技能不存在: %s" % skill_id)
    return ok({"updated": True})


@api.delete("/skills/{skill_id}")
async def delete_skill_endpoint(skill_id: str):
    ok_ = _sk_delete_skill(skill_id)
    if not ok_:
        raise ApiError("SKILL_NOT_FOUND", "技能不存在: %s" % skill_id)
    return ok({"deleted": True})


@api.post("/skills/{skill_id}/run")
async def run_skill_endpoint(skill_id: str, req: Request):
    """拖拽执行技能：填占位符 → 组装 ProductionIntent → 真调 P0 translator → 返回 workflow。

    入参：user_input(用户输入/运镜描述), context{subject_asset_id, style_lighting, ...}
    输出：translate 出的 ComfyUI workflow dict（由 ComfyUI 服务实际出图）。
    """
    b = await _body(req)
    user_input = b.get("user_input")
    context = b.get("context") or {}
    if not isinstance(context, dict):
        context = {}
    workflow = await _sk_run_skill(skill_id, user_input, context)
    return ok({"workflow": workflow})


# ======================================================================
# P3 M2 用户自定义模板 + 可视化编辑器（编排层可插拔）
# ----------------------------------------------------------------------
# 衔接（手册 §0.3）：自定义模板注册进 P0 M3 task_type→template_id 映射
#   = 写 intent_engine/templates/user_custom/{task_type}.json（同名覆盖系统模板，director 自动选用）。
# 红线（§5）：禁原样存 JSON 不解析；禁 hash() 生成 ID。
# ======================================================================
from features.user_templates import (  # noqa: E402
    import_workflow as _tmpl_import,
    register_user_template as _tmpl_register,
    list_user_templates as _tmpl_list,
    test_user_template as _tmpl_test,
)


@api.post("/templates/import")
async def template_import_endpoint(req: Request):
    """导入 ComfyUI workflow → 占位符识别 → 语义模板 → 注册进 P0 映射（user_custom 同名覆盖）。

    入参：task_type(须为合法 task_type), comfy_json(ComfyUI workflow {node_id:{class_type,inputs}})
    输出：{registered, template_id, path, usable_by_director, detected_placeholders}
    """
    b = await _body(req)
    task_type = str(b.get("task_type") or "").strip()
    comfy_json = b.get("comfy_json")
    if not task_type or not isinstance(comfy_json, dict):
        raise ApiError("INVALID_PARAMS", "task_type 与 comfy_json(dict) 必填")
    semantic = _tmpl_import(comfy_json)
    try:
        result = _tmpl_register(task_type, semantic)
    except ValueError as e:
        raise ApiError("INVALID_TASK_TYPE", str(e))
    result["detected_placeholders"] = semantic["detected_placeholders"]
    return ok(result)


@api.get("/user-templates")
async def template_list_endpoint(req: Request):
    """列出已注册的用户自定义模板（user_custom）。

    注意：/templates 及其 /templates/{id} 子树已被 routers_compiled 中的旧「项目模板」
    子系统占用（GET/PATCH/DELETE /templates/{id}、POST /templates/{id}/apply），
    为避免冲突与破坏既有业务，P3 M2 用户自定义语义模板列表使用顶层 /user-templates。
    """
    return ok({"templates": _tmpl_list()})


@api.post("/templates/{template_id}/test")
async def template_test_endpoint(template_id: str):
    """验证模板已注册进 P0 映射且 director 可选用（TemplateEngine 可加载）。"""
    try:
        res = _tmpl_test(template_id)
    except Exception as e:
        raise ApiError("TEMPLATE_TEST_FAILED", "模板不可用：%s" % e)
    return ok(res)


# ======================================================================
# P3 M3 长期记忆 / 资产沉淀（跨项目复用，编排层可插拔）
# ----------------------------------------------------------------------
# 衔接（手册 §0.3）：角色卡复用 P1 M4 import_role_card 深拷贝；风格锚点/ LoRA 复用 P0 表。
# 红线（§5）：禁浅拷贝引用；禁 hash() 生成 ID（用 db.uid）。
# ======================================================================
from features.memory_store import (  # noqa: E402
    remember_project as _mem_remember,
    recommend_for_new_project as _mem_recommend,
    import_to_project as _mem_import,
)


@api.post("/projects/{project_id}/remember")
async def remember_project_endpoint(project_id: str):
    """沉淀当前项目资产（角色卡/风格锚点/LoRA）到 memory 表。"""
    n = _mem_remember(project_id, SINGLE_USER_ID)
    return ok({"remembered": n})


@api.get("/memory/recommend")
async def memory_recommend_endpoint(req: Request):
    """新项目一键复用：推荐历史角色卡/风格锚点/LoRA。"""
    items = _mem_recommend(SINGLE_USER_ID)
    return ok({"items": items})


@api.post("/projects/{project_id}/import-memory")
async def import_memory_endpoint(project_id: str, req: Request):
    """从历史导入选中记忆项到目标项目（按类型分派：角色卡深拷贝 / 风格锚点复制 / LoRA 注册）。"""
    b = await _body(req)
    mids = b.get("memory_ids")
    if not isinstance(mids, list):
        raise ApiError("INVALID_PARAMS", "memory_ids 必须是数组")
    result = _mem_import(project_id, mids)
    return ok(result)


# ======================================================================
# P4 M1 新模型零代码接入（手册 §2 M1）
# ----------------------------------------------------------------------
# 衔接（手册 §0.3）：节点自动发现复用 P0 M2 三层推导（detect_capability_dimensions /
#   _frame_anchor_grade / _annotate_entry）；模板自动匹配查 P0 M3
#   TemplateEngine.discover_templates()（task_type→template 映射）。
# 红线（§5）：禁假零代码（必须节点发现 + 模板匹配）；禁全新族假装已接入（诚实引导 P3 M2）。
# ======================================================================
from features.model_plug import (  # noqa: E402
    detect_new_models as _mdetect,
    discover_node_family as _mdiscover,
    match_template as _mmatch,
    register_model as _mregister,
    list_model_declarations as _mlist,
)
from intent_engine.resource_catalog import ResourceCatalog as _ModelPlugRC  # noqa: E402


def _mp_refresh_object_info():
    """同步刷新 ComfyUI object_info（在线推导用；失败返回 None，走注册表兜底）。"""
    rc = _ModelPlugRC()
    try:
        _loop = asyncio.new_event_loop()
        try:
            _loop.run_until_complete(rc.refresh())
        finally:
            _loop.close()
    except Exception as e:
        logger.warning("[M1] 在线 object_info 探测失败（走注册表兜底）：%s", e)
        return None
    return rc


# 覆盖（手册 §0.3 / 项目约定：与 split_story 同策略）：routers_compiled 权威 .pyc 中
# 已冻结了一份 P4 M1 实现（含内联 model_plug 函数）。为恢复「可编辑性 + 单测覆盖」，
# 先删除 .pyc 中的 4 个 P4 M1 路由，再以本文件源码版本（import features.model_plug）重新注册。
# 注意：/models/local 与 /models/test 是 Ollama 模型选择器/连通性测试路由，属不同功能，不删除。
_MP_PATHS = {
    (getattr(api, "prefix", "") or "") + p for p in (
        "/models", "/models/scan", "/models/register", "/models/{name}/discover",
    )
}
for _i in range(len(api.routes) - 1, -1, -1):
    if getattr(api.routes[_i], "path", None) in _MP_PATHS:
        del api.routes[_i]


@api.get("/models/scan")
async def models_scan_endpoint(req: Request):
    """扫描 ComfyUI 模型目录，返回尚未声明的「新模型」清单（零代码接入第一步）。

    离线/ComfyUI 未安装：返回空清单（诚实告知需先启动 ComfyUI，不静默假装无新模型）。
    """
    try:
        new = _mdetect()
    except Exception as e:
        raise ApiError("MODEL_SCAN_FAILED", "模型扫描失败：%s" % e)
    return ok({"models": new, "count": len(new)})


@api.get("/models/{name}/discover")
async def models_discover_endpoint(name: str):
    """自动发现某模型节点族 + 能力维度（复用 P0 M2 三层推导）+ 模板匹配。

    name 为模型文件名（如 wan-3.0）。在线时真正跑 detect_capability_dimensions；
    离线时走注册表兜底（仍用 _frame_anchor_grade 层2 签名推导）。
    """
    object_info = None
    try:
        rc = await asyncio.to_thread(_mp_refresh_object_info)
        if rc is not None:
            object_info = rc.object_info
    except Exception as e:
        logger.warning("[M1] 节点自动发现在线推导失败（注册表兜底）：%s", e)
    result = _mdiscover(name, object_info)
    result["template"] = _mmatch(result["node_family"], result["type"])
    return ok(result)


@api.post("/models/register")
async def models_register_endpoint(req: Request):
    """合并自动发现 + 用户补声明 → 写 catalog 热加载（无需重启即生效）。"""
    b = await _body(req)
    name = str(b.get("name") or "").strip()
    if not name:
        raise ApiError("INVALID_PARAMS", "name 必填")
    try:
        rec = _mregister(b)
    except ValueError as e:
        raise ApiError("INVALID_PARAMS", str(e))
    except Exception as e:
        raise ApiError("MODEL_REGISTER_FAILED", "模型注册失败：%s" % e)
    return ok(rec)


@api.get("/models")
async def models_list_endpoint(req: Request):
    """列出已声明模型（热加载视图）。?status=active 可按状态过滤。"""
    status = req.query_params.get("status")
    models = _mlist(status)
    return ok({"models": models, "count": len(models)})


# ======================================================================
# P4 M2 批量生产 / 流水线调度（手册 §2 M2）
# ----------------------------------------------------------------------
# 复用（手册 §0.3）：依赖判定复用 P2 M2 首尾帧链（has_dependency）。
# 红线（§5）：GPU_MAX_CONCURRENT 保持 1（视频显存占用高，禁止开并发导致 OOM）。
# ======================================================================
from features.task_scheduler import (  # noqa: E402
    schedule_tasks as _schedule_tasks,
    has_dependency as _has_dependency,
    GPU_MAX_CONCURRENT as _GPU_MAX,
)
from db import row_to_dict as _row_to_dict  # noqa: E402


# 覆盖（与 M1 同策略）：若权威 .pyc 已冻结 M2/M3 路由，先删后以源码重注。
_MP2_PATHS = {
    (getattr(api, "prefix", "") or "") + p for p in (
        "/tasks/queue", "/tasks/{id}/priority", "/resource/clear-comfy-cache",
    )
}
for _i in range(len(api.routes) - 1, -1, -1):
    if getattr(api.routes[_i], "path", None) in _MP2_PATHS:
        del api.routes[_i]


@api.get("/tasks/queue")
async def tasks_queue_endpoint(req: Request):
    """M2 §4 队列视图：返回按 优先级 + 依赖 排序的生成任务队列（含 schedule_status）。

    schedule_status='ready'（无首尾帧依赖，可立即调度）/ 'waiting'（有首尾帧依赖，串行等待上游）。
    """
    conn = get_conn()
    try:
        rows = fetch_all(conn,
            "SELECT * FROM generation_tasks WHERE status IN ('queued','retrying','running') "
            "ORDER BY priority DESC, created_at ASC")
    finally:
        conn.close()
    tasks = [_row_to_dict(r) for r in rows]
    scheduled = _schedule_tasks(tasks)
    return ok({
        "tasks": scheduled,
        "gpu_max_concurrent": _GPU_MAX,
        "count": len(scheduled),
    })


@api.post("/tasks/{id}/priority")
async def tasks_priority_endpoint(id: str, req: Request):
    """M2 §4 调整任务优先级（VIP 高优先）；worker 下次轮询即按其重排消费。

    请求体：{"priority": <int>}
    """
    b = await _body(req)
    priority = b.get("priority")
    if not isinstance(priority, int):
        raise ApiError("INVALID_PARAMS", "priority 必须为整数")
    conn = get_conn()
    try:
        n = conn.execute(
            "UPDATE generation_tasks SET priority=? WHERE task_id=?", (priority, id)).rowcount
        conn.commit()
    finally:
        conn.close()
    if not n:
        raise ApiError("TASK_NOT_FOUND", "任务不存在：%s" % id)
    return ok({"task_id": id, "priority": priority})


@api.post("/resource/clear-comfy-cache")
async def clear_comfy_cache_endpoint(req: Request):
    """M3 §4.3 手动清理 ComfyUI 显存缓存（优雅 free memory，不杀进程）。

    worker 空闲时也会自动释放（release_vram_if_idle）；此端点供用户手动触发。
    """
    try:
        from worker import release_vram_if_idle
        result = release_vram_if_idle()
    except Exception as e:
        raise ApiError("CLEAR_COMFY_FAILED", "清理失败：%s" % e)
    return ok({"result": result})


# ======================================================================
# 路由序修正（P4 M2/M3 收口）：编译基线 routers_compiled 先注册了通用详情路由
# GET /tasks/{task_id}，会抢先匹配字面路由 GET /tasks/queue（task_id='queue' → 任务不存在）。
# 这里把 P4 的固定字面路由 /tasks/queue 移到所有 /tasks/{...} 可变路由之前，
# 保证「精确字面匹配优先于路径参数匹配」（FastAPI 按注册序分发）。
# 不删除 /tasks/{task_id}（P0-P3 既有能力，保持非破坏性）。
# ======================================================================
def _p4_fix_route_precedence():
    """把字面路由 /tasks/queue 移到通用详情路由 /tasks/{task_id} 之前。

    注：APIRouter 带 prefix='/api/v1'，route.path 含前缀（如 '/api/v1/tasks/queue'），
    故匹配用 endswith/包含而非精确相等。仅调整顺序，不删除 /tasks/{task_id}（P0-P3 既有能力）。
    """
    q = None
    q_i = None
    first_var = None
    for i, r in enumerate(api.routes):
        p = getattr(r, "path", None) or ""
        if p.endswith("/tasks/queue"):
            q, q_i = r, i
        elif "/tasks/{" in p and first_var is None:
            first_var = i
    if q is not None and first_var is not None and q_i > first_var:
        api.routes.pop(q_i)
        api.routes.insert(first_var, q)


_p4_fix_route_precedence()
