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
            b.get("ratio") or "16:9",
            int(b.get("steps") or 20),
            int(b.get("seed") or -1),
            bool(b.get("enhance", True)),
            b.get("orchestrator") or "ollama",
            char_refs,
            int(b.get("panels") or 4),
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
                            "ratio": b.get("ratio") or "16:9",
                            "steps": int(b.get("steps") or 20),
                            "seed": int(b.get("seed") or -1),
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
        vec = features.load_face_vector(stage_id)
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
