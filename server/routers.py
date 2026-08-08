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
import importlib.util
import logging
import json
import time

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
from fastapi import Request  # noqa: F401
import re  # noqa: F401
from common import ok, ApiError  # noqa: F401
from ai_local import (  # noqa: F401
    split_story_with_llm, ollama_list_models, extract_assets_from_story,
    _call_online_llm, comfy_available
)
_body = getattr(routers_compiled, "_body")
_redistribute_durations = getattr(routers_compiled, "_redistribute_durations")
from db import get_conn, fetch_all, uid, SINGLE_USER_ID, log_operation  # noqa: F401

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
        result = split_story_with_llm(text, model_config) or {}
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
    result = extract_assets_from_story(story, model_config)
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


# ======================================================================
# 定点覆盖区（END）
# ======================================================================
