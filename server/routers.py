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
from routers_compiled import *  # noqa: F401,F403

# 覆盖实现所需的依赖（来自原模块 / 公共模块）
from fastapi import Request  # noqa: F401
import re  # noqa: F401
from common import ok, ApiError  # noqa: F401
from ai_local import split_story_with_llm  # noqa: F401
from routers_compiled import _body, _redistribute_durations  # noqa: F401

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

    meta = {}
    try:
        result = split_story_with_llm(text) or {}
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


# ======================================================================
# 定点覆盖区（END）
# ======================================================================
