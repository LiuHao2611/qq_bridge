# -*- coding: utf-8 -*-
"""回忆录导演板（AGENT_SPEC_15）本地后端。

personal_local_only：本地 Ollama(策划) + 秋叶 ComfyUI(指导板出图) + FFmpeg(章节视频)。
对应 spec 路由 /memoirs/:memoirId/chapters/:chapterId/director-board 的数据契约。
云端 GPT Image 2 / Seedance 2.0 Mini 在本地被 ComfyUI / 本地视频合成等价替代（诚实标注于前端模型配置）。
"""
import os
import io
import json
import time
import shutil
import threading
import logging

from fastapi import APIRouter, Request, UploadFile, File

logger = logging.getLogger(__name__)

from db import (get_conn, fetch_one, fetch_all, row_to_dict, uid, SINGLE_USER_ID,
                log_operation)
from common import ApiError, ok
from points_engine import pre_auth, settle, refund, estimate_credits
from ai_local import (plan_memoir, storyboard_from_prompt, generate_memoir_video,
                      ollama_available, comfy_available)

api = APIRouter(prefix="/api/v1")

OUTPUTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
REFS_DIR = os.path.join(OUTPUTS, "refs")
os.makedirs(REFS_DIR, exist_ok=True)

_MEMOIR_JSON = ["reference_photos", "timeline_events"]
_CHAPTER_JSON = ["shot_data", "color_palette", "mood_keywords"]

STORYBOARD_COST = 800  # spec §5 storyboard_credits

_RATIOS = ["16:9", "9:16", "1:1"]
_RESOLS = ["720p", "1080p", "2k"]
_SB_COUNTS = [4, 6, 8, 10]
_PERSONS = ["第一人称", "第三人称"]


def _clean_memoir(row):
    d = row_to_dict(row)
    for c in _MEMOIR_JSON:
        if c in d and isinstance(d[c], str):
            try:
                d[c] = json.loads(d[c])
            except Exception:
                d[c] = []
    # 兼容 NULL
    if not d.get("reference_photos"):
        d["reference_photos"] = []
    if not d.get("timeline_events"):
        d["timeline_events"] = []
    return d


def _clean_chapter(row):
    d = row_to_dict(row)
    for c in _CHAPTER_JSON:
        if c in d and isinstance(d[c], str):
            try:
                d[c] = json.loads(d[c])
            except Exception:
                d[c] = None
    return d


async def _body(req: Request):
    try:
        cl = req.headers.get("content-length")
        if cl in (None, "0"):
            return {}
        return await req.json()
    except Exception:
        raise ApiError("INVALID_PARAMS", "请求体不是合法 JSON")


def _get_memoir(conn, memoir_id):
    m = fetch_one(conn, "SELECT * FROM memoirs WHERE memoir_id=?", (memoir_id,))
    if not m:
        raise ApiError("INVALID_PARAMS", "回忆录不存在")
    return _clean_memoir(m)


def _list_chapters(conn, memoir_id):
    rows = fetch_all(conn, "SELECT * FROM memoir_chapters WHERE memoir_id=? ORDER BY chapter_index ASC", (memoir_id,))
    return [_clean_chapter(r) for r in rows]


# ----------------------------- 入口：项目 → 回忆录（自动创建） -----------------------------
@api.get("/projects/{project_id}/memoir")
async def get_or_create_memoir(project_id: str):
    conn = get_conn()
    try:
        proj = fetch_one(conn, "SELECT * FROM projects WHERE project_id=?", (project_id,))
        if not proj:
            raise ApiError("INVALID_PARAMS", "项目不存在")
        m = fetch_one(conn, "SELECT * FROM memoirs WHERE project_id=?", (project_id,))
        if not m:
            mid = uid()
            conn.execute(
                """INSERT INTO memoirs (memoir_id, project_id, subject_name, background_text,
                   storyboard_count, target_ratio, target_resolution, enhance_emotion, exclude_content,
                   reference_photos, timeline_events, person_perspective)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mid, project_id, proj.get("name") or "主人公", "", 4, "16:9", "720p", "", "",
                 "[]", "[]", "第一人称"))
            conn.commit()
            m = fetch_one(conn, "SELECT * FROM memoirs WHERE memoir_id=?", (mid,))
        memoir = _clean_memoir(m)
        chapters = _list_chapters(conn, memoir["memoir_id"])
        return ok({"memoir": memoir, "chapters": chapters})
    finally:
        conn.close()


@api.get("/memoirs/{memoir_id}")
async def get_memoir(memoir_id: str):
    conn = get_conn()
    try:
        memoir = _get_memoir(conn, memoir_id)
        chapters = _list_chapters(conn, memoir_id)
        return ok({"memoir": memoir, "chapters": chapters})
    finally:
        conn.close()


# ----------------------------- 回忆录字段更新（spec §3.1 左栏） -----------------------------
@api.patch("/memoirs/{memoir_id}")
async def patch_memoir(memoir_id: str, req: Request):
    b = await _body(req)
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        fields = []; params = []
        enum_checks = {
            "storyboard_count": _SB_COUNTS,
            "target_ratio": _RATIOS,
            "target_resolution": _RESOLS,
            "person_perspective": _PERSONS,
        }
        for k in ("subject_name", "background_text", "storyboard_count",
                  "target_ratio", "target_resolution", "enhance_emotion",
                  "exclude_content", "person_perspective"):
            if k in b:
                v = b[k]
                if k in enum_checks and v not in enum_checks[k]:
                    raise ApiError("INVALID_PARAMS", "%s 必须为 %s" % (k, "/".join(map(str, enum_checks[k]))))
                fields.append("%s=?" % k); params.append(v)
        # §09 MEM-04 人物锚点（JSON）+ 锁定状态
        if "character_anchors" in b and b["character_anchors"] is not None:
            ca = b["character_anchors"]
            if not isinstance(ca, dict):
                raise ApiError("INVALID_PARAMS", "character_anchors 必须为对象")
            fields.append("character_anchors=?"); params.append(json.dumps(ca, ensure_ascii=False))
        if "character_anchors_locked" in b:
            locked = 1 if b["character_anchors_locked"] else 0
            # 锁定校验：锁定时必须满足最小锚点约束（appearance >=3, personality >=2, core_relationship >=5）
            if locked:
                ca = b.get("character_anchors") or {}
                if (len(ca.get("appearance_keywords") or []) < 3
                        or len(ca.get("personality_keywords") or []) < 2
                        or len(ca.get("core_relationship") or "") < 5):
                    raise ApiError("INVALID_PARAMS", "锁定需满足：appearance_keywords≥3, personality_keywords≥2, core_relationship≥5")
            fields.append("character_anchors_locked=?"); params.append(locked)
        if not fields:
            raise ApiError("INVALID_PARAMS", "无可更新字段")
        params.append(memoir_id)
        conn.execute("UPDATE memoirs SET " + ",".join(fields) + ",updated_at=? WHERE memoir_id=?",
                     params + [int(time.time())])
        conn.commit()
        return ok({"memoir": _get_memoir(conn, memoir_id)})
    finally:
        conn.close()


# ----------------------------- 时间轴关键事件（spec §3.2 add-event） -----------------------------
@api.post("/memoirs/{memoir_id}/timeline-events")
async def add_timeline_event(memoir_id: str, req: Request):
    b = await _body(req)
    age = b.get("age")
    desc = (b.get("description") or "").strip()
    if age is None or not desc:
        raise ApiError("INVALID_PARAMS", "age 与 description 必填")
    try:
        age = int(age)
    except Exception:
        raise ApiError("INVALID_PARAMS", "age 必须为整数")
    if age < 0:
        raise ApiError("INVALID_PARAMS", "age 必须 >= 0（BR-约束）")
    conn = get_conn()
    try:
        memoir = _get_memoir(conn, memoir_id)
        events = memoir.get("timeline_events") or []
        events.append({"age": age, "description": desc})
        conn.execute("UPDATE memoirs SET timeline_events=?, updated_at=? WHERE memoir_id=?",
                     (json.dumps(events, ensure_ascii=False), int(time.time()), memoir_id))
        conn.commit()
        return ok({"timeline_events": events})
    finally:
        conn.close()


@api.delete("/memoirs/{memoir_id}/timeline-events/{index}")
async def del_timeline_event(memoir_id: str, index: int):
    conn = get_conn()
    try:
        memoir = _get_memoir(conn, memoir_id)
        events = memoir.get("timeline_events") or []
        if 0 <= index < len(events):
            events.pop(index)
            conn.execute("UPDATE memoirs SET timeline_events=?, updated_at=? WHERE memoir_id=?",
                         (json.dumps(events, ensure_ascii=False), int(time.time()), memoir_id))
            conn.commit()
        return ok({"timeline_events": events})
    finally:
        conn.close()


# ----------------------------- 参考照片（spec §3.2 upload-photo, BR-015） -----------------------------
@api.post("/memoirs/{memoir_id}/reference-photos")
async def upload_photo(memoir_id: str, file: UploadFile = File(...)):
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        data = await file.read()
        if len(data) > 10 * 1024 * 1024:
            raise ApiError("FILE_TOO_LARGE", "参考照最大 10MB（BR-015）")
        ext = os.path.splitext(file.filename or "png")[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            ext = ".png"
        name = "ref_%s%s" % (uid()[:8], ext)
        with open(os.path.join(REFS_DIR, name), "wb") as f:
            f.write(data)
        photo = {"photo_id": uid(), "name": file.filename or name,
                 "url": "/file?name=" + name, "uploaded_at": int(time.time())}
        memoir = _get_memoir(conn, memoir_id)
        photos = memoir.get("reference_photos") or []
        photos.append(photo)
        conn.execute("UPDATE memoirs SET reference_photos=?, updated_at=? WHERE memoir_id=?",
                     (json.dumps(photos, ensure_ascii=False), int(time.time()), memoir_id))
        conn.commit()
        return ok({"photo": photo, "reference_photos": photos})
    finally:
        conn.close()


# ----------------------------- 章节（spec §3.3 chapter switcher） -----------------------------
@api.get("/memoirs/{memoir_id}/chapters")
async def list_chapters(memoir_id: str):
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        return ok({"chapters": _list_chapters(conn, memoir_id)})
    finally:
        conn.close()


@api.post("/memoirs/{memoir_id}/chapters")
async def create_chapter(memoir_id: str, req: Request):
    b = await _body(req)
    conn = get_conn()
    try:
        memoir = _get_memoir(conn, memoir_id)
        existing = _list_chapters(conn, memoir_id)
        if len(existing) >= int(memoir.get("storyboard_count") or 4):
            raise ApiError("MAX_CHAPTERS", "章节数已达故事板数量上限 %s（BR-019）" % memoir.get("storyboard_count"))
        idx = len(existing) + 1
        cid = uid()
        title = b.get("title") or ("第 %02d 章" % idx)
        conn.execute(
            """INSERT INTO memoir_chapters (chapter_id, memoir_id, chapter_index, title, era, emotion,
               storyboard_image_url, storyboard_prompt, shot_data, audio_notes, color_palette, mood_keywords,
               duration, video_url, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, memoir_id, idx, title, "", "", "", "", "{}", "", "[]", "[]", 15, "", "draft"))
        conn.commit()
        return ok({"chapter": _clean_chapter(fetch_one(conn, "SELECT * FROM memoir_chapters WHERE chapter_id=?", (cid,)))})
    finally:
        conn.close()


@api.get("/memoirs/{memoir_id}/chapters/{chapter_id}")
async def get_chapter(memoir_id: str, chapter_id: str):
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        ch = fetch_one(conn, "SELECT * FROM memoir_chapters WHERE chapter_id=?", (chapter_id,))
        if not ch:
            raise ApiError("INVALID_PARAMS", "章节不存在")
        return ok({"chapter": _clean_chapter(ch)})
    finally:
        conn.close()


@api.patch("/memoirs/{memoir_id}/chapters/{chapter_id}")
async def patch_chapter(memoir_id: str, chapter_id: str, req: Request):
    b = await _body(req)
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        ch = fetch_one(conn, "SELECT * FROM memoir_chapters WHERE chapter_id=?", (chapter_id,))
        if not ch:
            raise ApiError("INVALID_PARAMS", "章节不存在")
        fields = []; params = []
        for k in ("title", "era", "emotion", "storyboard_prompt", "duration", "status"):
            if k in b:
                v = b[k]
                if k == "duration":
                    v = int(v)
                fields.append("%s=?" % k); params.append(v)
        for k in ("shot_data", "color_palette", "mood_keywords"):
            if k in b and b[k] is not None:
                fields.append("%s=?" % k); params.append(json.dumps(b[k], ensure_ascii=False))
        # §09 MEM-06/07/08：导演板结构化字段（摄影机路径 / 灯光方案 / 情绪色卡）
        for k in ("shot_table_camera_paths", "lighting_scheme", "emotion_palette"):
            if k in b and b[k] is not None:
                fields.append("%s=?" % k); params.append(json.dumps(b[k], ensure_ascii=False))
        if not fields:
            raise ApiError("INVALID_PARAMS", "无可更新字段")
        params.append(chapter_id)
        conn.execute("UPDATE memoir_chapters SET " + ",".join(fields) + ",updated_at=? WHERE chapter_id=?",
                     params + [int(time.time())])
        conn.commit()
        return ok({"chapter": _clean_chapter(fetch_one(conn, "SELECT * FROM memoir_chapters WHERE chapter_id=?", (chapter_id,)))})
    finally:
        conn.close()


# ----------------------------- MEM-03 事实锚点（用户对 AI 的硬约束） -----------------------------
_ANCHOR_TYPES = ["event", "relation", "date", "location", "other"]


@api.get("/memoirs/{memoir_id}/anchors")
async def list_anchors(memoir_id: str):
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        rows = fetch_all(conn,
            "SELECT * FROM memoir_fact_anchors WHERE memoir_id=? AND deleted_at IS NULL ORDER BY created_at ASC",
            (memoir_id,))
        return ok({"anchors": [row_to_dict(r) for r in rows]})
    finally:
        conn.close()


@api.post("/memoirs/{memoir_id}/anchors")
async def create_anchor(memoir_id: str, req: Request):
    b = await _body(req)
    anchor_type = b.get("anchor_type")
    content = (b.get("content") or "").strip()
    if anchor_type not in _ANCHOR_TYPES:
        raise ApiError("INVALID_PARAMS", "anchor_type 必须为 %s" % "/".join(_ANCHOR_TYPES))
    if len(content) < 2 or len(content) > 200:
        raise ApiError("INVALID_PARAMS", "content 长度需在 2~200 之间")
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        aid = uid()
        is_locked = 1 if b.get("is_locked", True) else 0
        conn.execute(
            """INSERT INTO memoir_fact_anchors (id, memoir_id, anchor_type, content, is_locked)
               VALUES (?,?,?,?,?)""",
            (aid, memoir_id, anchor_type, content, is_locked))
        conn.commit()
        row = fetch_one(conn, "SELECT * FROM memoir_fact_anchors WHERE id=?", (aid,))
        return ok({"anchor": row_to_dict(row)})
    finally:
        conn.close()


@api.patch("/memoirs/{memoir_id}/anchors/{anchor_id}")
async def update_anchor(memoir_id: str, anchor_id: str, req: Request):
    b = await _body(req)
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        row = fetch_one(conn, "SELECT * FROM memoir_fact_anchors WHERE id=? AND deleted_at IS NULL", (anchor_id,))
        if not row:
            raise ApiError("INVALID_PARAMS", "锚点不存在")
        fields = []; params = []
        if "content" in b:
            c = (b["content"] or "").strip()
            if len(c) < 2 or len(c) > 200:
                raise ApiError("INVALID_PARAMS", "content 长度需在 2~200 之间")
            fields.append("content=?"); params.append(c)
        if "is_locked" in b:
            fields.append("is_locked=?"); params.append(1 if b["is_locked"] else 0)
        if not fields:
            raise ApiError("INVALID_PARAMS", "无可更新字段")
        params.append(anchor_id)
        conn.execute("UPDATE memoir_fact_anchors SET " + ",".join(fields) + ",updated_at=? WHERE id=?",
                     params + [int(time.time())])
        conn.commit()
        row = fetch_one(conn, "SELECT * FROM memoir_fact_anchors WHERE id=?", (anchor_id,))
        return ok({"anchor": row_to_dict(row)})
    finally:
        conn.close()


@api.delete("/memoirs/{memoir_id}/anchors/{anchor_id}")
async def delete_anchor(memoir_id: str, anchor_id: str):
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        conn.execute("UPDATE memoir_fact_anchors SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                     (int(time.time()), anchor_id))
        conn.commit()
        return ok({"deleted": True})
    finally:
        conn.close()


# ----------------------------- MEM-13 章节排序与拖拽（原子重排） -----------------------------
@api.post("/memoirs/{memoir_id}/chapters/reorder")
async def reorder_chapters(memoir_id: str, req: Request):
    b = await _body(req)
    ids = b.get("chapter_ids")
    if not isinstance(ids, list) or not ids:
        raise ApiError("INVALID_PARAMS", "chapter_ids 必填且为非空数组")
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        existing = _list_chapters(conn, memoir_id)
        exist_ids = {c["chapter_id"] for c in existing}
        if set(ids) != exist_ids:
            raise ApiError("INVALID_PARAMS", "chapter_ids 必须与现有章节集合完全一致（原子重排，REDUN-04）")
        # 事务原子：失败整体回滚
        for i, cid in enumerate(ids):
            conn.execute("UPDATE memoir_chapters SET chapter_index=?, updated_at=? WHERE chapter_id=?",
                         (i + 1, int(time.time()), cid))
        conn.commit()
        return ok({"chapters": _list_chapters(conn, memoir_id)})
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ----------------------------- MEM-09 声音旁白 TTS（本地 edge-tts 替代，诚实标注） -----------------------------
@api.post("/memoirs/{memoir_id}/chapters/{chapter_id}/narrations")
async def create_narration(memoir_id: str, chapter_id: str, req: Request):
    """§09 MEM-09 / AI-02：提交旁白 TTS。本地优先 edge-tts；缺失则存储文本并诚实标注 local_fallback。"""
    b = await _body(req)
    text = (b.get("text") or "").strip()
    if not text:
        raise ApiError("INVALID_PARAMS", "text 必填")
    if len(text) > 5000:
        raise ApiError("INVALID_PARAMS", "text 长度上限 5000")
    voice_provider = b.get("voice_provider", "edge_tts")
    voice_id = b.get("voice_id", "zh-CN-XiaoxiaoNeural")
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        ch = fetch_one(conn, "SELECT * FROM memoir_chapters WHERE chapter_id=?", (chapter_id,))
        if not ch:
            raise ApiError("INVALID_PARAMS", "章节不存在")
        audio_url = None
        local_fallback = False
        try:
            import edge_tts
            import asyncio
            name = "nar_%s.mp3" % chapter_id[:8]
            out_path = os.path.join(OUTPUTS, name)
            comm = edge_tts.Communicate(text, voice_id)
            # 同步执行（本地，单章体量小）
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(comm.save(out_path))
            finally:
                loop.close()
            if os.path.isfile(out_path):
                audio_url = "/file?name=" + name
        except ImportError:
            local_fallback = True
        except Exception as e:
            # AI-02 降级：生成失败仅存文本，标注本地降级（不抛错中断）
            local_fallback = True
            logger.warning("[memoir][tts] 本地 TTS 生成失败，仅存储文本：%s" % str(e)[:120])
        notes = {
            "text": text,
            "voice_provider": voice_provider,
            "voice_id": voice_id,
            "audio_url": audio_url,
            "local_fallback": local_fallback,
        }
        conn.execute("UPDATE memoir_chapters SET audio_notes=?, updated_at=? WHERE chapter_id=?",
                     (json.dumps(notes, ensure_ascii=False), int(time.time()), chapter_id))
        conn.commit()
        return ok({"narration": notes, "audio_url": audio_url})
    finally:
        conn.close()


# ----------------------------- AI 工作流（spec §8） -----------------------------
@api.post("/memoirs/{memoir_id}/planning")
async def planning(memoir_id: str, req: Request):
    """§8.1 策划（本地 Ollama 替代 Gemini）。"""
    conn = get_conn()
    try:
        memoir = _get_memoir(conn, memoir_id)
        chapters = plan_memoir(memoir)
        if not chapters:
            raise ApiError("AI_UNAVAILABLE", "本地 Ollama 不可用或策划失败（请确认 Ollama 已启动 qwen3.5:9b）")
        # 重建章节
        conn.execute("DELETE FROM memoir_chapters WHERE memoir_id=?", (memoir_id,))
        for ch in chapters:
            cid = uid()
            shot_data = {
                "characters": ch.get("characters", []),
                "scenes": ch.get("scenes", []),
                "camera_plan": ch.get("camera_plan", ""),
                "lighting": ch.get("lighting", ""),
                "mood_keywords": ch.get("mood_keywords", []),
                "shots": ch.get("shots", []),
                "audio_description": ch.get("audio_description", ""),
                "voiceover_notes": ch.get("voiceover_notes", []),
                "color_palette": ch.get("color_palette", []),
            }
            conn.execute(
                """INSERT INTO memoir_chapters (chapter_id, memoir_id, chapter_index, title, era, emotion,
                   storyboard_prompt, shot_data, color_palette, mood_keywords, duration, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, memoir_id, int(ch.get("chapter_index", 1)), ch.get("title", "第 %d 章" % ch.get("chapter_index", 1)),
                 ch.get("era", ""), ch.get("emotion", ""),
                 ch.get("storyboard_prompt", "") or "",
                 json.dumps(shot_data, ensure_ascii=False),
                 json.dumps(ch.get("color_palette", []), ensure_ascii=False),
                 json.dumps(ch.get("mood_keywords", []), ensure_ascii=False),
                 int(ch.get("duration", 15)), "planned"))
        conn.commit()
        return ok({"chapters": _list_chapters(conn, memoir_id), "status": "planned"})
    finally:
        conn.close()


def _build_storyboard_prompt(chapter, memoir):
    sd = chapter.get("shot_data") or {}
    title = chapter.get("title") or ""
    era = chapter.get("era") or ""
    chars = "；".join([str(c) for c in (sd.get("characters") or [])])
    scenes = "；".join([str(s) for s in (sd.get("scenes") or [])])
    shots_txt = ""
    for i, s in enumerate(sd.get("shots") or []):
        shots_txt += "镜头%d：%s；" % (i + 1, s.get("description", ""))
    prompt = (
        "一张2048x1152分辨率、16:9比例的专业电影导演创意指导板，黑色哑光背景，极细金色分割线，米白色正文，"
        "高密度专业排版，纪录片档质感，顶部包边，包含章节标题、角色设定、场景描述、分镜表格、情绪关键词、"
        "色彩参考、声音备注、图片信息、页码等模块，视觉风格复古、温暖、细腻，具有80年代胶片质感。\n"
        "章节标题：%s\n年代：%s\n角色：%s\n场景：%s\n分镜：%s"
        % (title, era, chars, scenes, shots_txt)
    )
    return prompt


def _run_chapter_task(task_id, kind, memoir_id, chapter_id, payload):
    conn = get_conn()
    try:
        conn.execute("UPDATE memoir_tasks SET status='running' WHERE task_id=?", (task_id,))
        conn.commit()
        try:
            if kind == "storyboard":
                prompt = payload.get("prompt") or ""
                img = storyboard_from_prompt(prompt)
                if not img or not os.path.isfile(img):
                    raise RuntimeError("ComfyUI 出图失败（本地未启动或超时）")
                ext = os.path.splitext(img)[1] or ".png"
                name = "board_%s%s" % (chapter_id[:8], ext)
                dst = os.path.join(OUTPUTS, name)
                shutil.copyfile(img, dst)
                url = "/file?name=" + name
                conn.execute("UPDATE memoir_chapters SET storyboard_image_url=?, status='board_ready' WHERE chapter_id=?",
                             (url, chapter_id))
                result = url
                note = "本地 ComfyUI 指导板出图"
                log_operation(conn, "memoir_storyboard", "chapter", chapter_id, cost_credits=STORYBOARD_COST)
            elif kind == "video":
                out_name = "memoir_vid_%s.mp4" % chapter_id[:8]
                out_path = os.path.join(OUTPUTS, out_name)
                level, note = generate_memoir_video(payload.get("chapter") or {}, out_path)
                if level == "fail":
                    raise RuntimeError(note)
                url = "/file?name=" + out_name
                conn.execute("UPDATE memoir_chapters SET video_url=?, status='video_ready' WHERE chapter_id=?",
                             (url, chapter_id))
                result = url
                log_operation(conn, "memoir_video", "chapter", chapter_id, cost_credits=payload.get("cost", 0))
            else:
                raise RuntimeError("未知任务类型")
            conn.execute("UPDATE memoir_tasks SET status='succeeded', result_url=?, finished_at=? WHERE task_id=?",
                         (result, int(time.time()), task_id))
            # 实扣积分
            try:
                t = fetch_one(conn, "SELECT pre_auth_id FROM memoir_tasks WHERE task_id=?", (task_id,))
                if t and t["pre_auth_id"]:
                    settle(t["pre_auth_id"])
            except Exception:
                pass
            conn.commit()
        except Exception as e:
            conn.execute("UPDATE memoir_tasks SET status='failed', error_message=? WHERE task_id=?",
                         (str(e)[:300], task_id))
            try:
                t = fetch_one(conn, "SELECT pre_auth_id FROM memoir_tasks WHERE task_id=?", (task_id,))
                if t and t["pre_auth_id"]:
                    refund(t["pre_auth_id"], reason=str(e)[:100])
            except Exception:
                pass
            conn.commit()
    finally:
        conn.close()


def _safe_run(task_id, kind, memoir_id, chapter_id, payload):
    """CLOSE-01 兜底：_run_chapter_task 外层再包一层，任何意外异常都标记 failed 并退还冻结金，
    绝不遗留永久 queued。"""
    try:
        _run_chapter_task(task_id, kind, memoir_id, chapter_id, payload)
    except Exception as e:
        conn = get_conn()
        try:
            conn.execute("UPDATE memoir_tasks SET status='failed', error_message=? WHERE task_id=?",
                         ("线程异常：" + str(e)[:200], task_id))
            try:
                t = fetch_one(conn, "SELECT pre_auth_id FROM memoir_tasks WHERE task_id=?", (task_id,))
                if t and t["pre_auth_id"]:
                    refund(t["pre_auth_id"], reason=str(e)[:100])
            except Exception:
                pass
            conn.commit()
        finally:
            conn.close()


@api.post("/memoirs/{memoir_id}/chapters/{chapter_id}/storyboard")
async def regen_storyboard(memoir_id: str, chapter_id: str, req: Request):
    """§8.2 指导板出图（本地 ComfyUI 替代 GPT Image 2）。需 pre_auth。"""
    b = await _body(req)
    pre_auth_id = b.get("pre_auth_id")
    if not pre_auth_id:
        raise ApiError("MISSING_FIELD", "pre_auth_id 必填（BR-004）")
    conn = get_conn()
    tid = None
    try:
        _get_memoir(conn, memoir_id)
        ch = fetch_one(conn, "SELECT * FROM memoir_chapters WHERE chapter_id=?", (chapter_id,))
        if not ch:
            raise ApiError("INVALID_PARAMS", "章节不存在")
        chapter = _clean_chapter(ch)
        memoir = _get_memoir(conn, memoir_id)
        prompt = chapter.get("storyboard_prompt") or _build_storyboard_prompt(chapter, memoir)
        tid = uid()
        conn.execute(
            """INSERT INTO memoir_tasks (task_id, memoir_id, chapter_id, kind, status, pre_auth_id, cost_credits)
               VALUES (?,?,?,?,?,?,?)""",
            (tid, memoir_id, chapter_id, "storyboard", "queued", pre_auth_id, STORYBOARD_COST))
        conn.execute("UPDATE memoir_chapters SET status='board_generating', last_task_id=? WHERE chapter_id=?",
                     (tid, chapter_id))
        conn.commit()
        try:
            threading.Thread(target=_safe_run, args=(tid, "storyboard", memoir_id, chapter_id,
                                {"prompt": prompt}), daemon=True).start()
        except Exception as e:
            # CLOSE-01+04：线程启动失败 → 删除任务行并退还冻结金，闭环收尾
            try:
                c2 = get_conn()
                c2.execute("DELETE FROM memoir_tasks WHERE task_id=?", (tid,))
                refund(pre_auth_id, reason="线程启动失败")
                c2.commit(); c2.close()
            except Exception:
                pass
            raise ApiError("INTERNAL_ERROR", "生成线程启动失败: %s" % str(e)[:120])
        return ok({"task_id": tid, "status": "queued"})
    except Exception:
        # CLOSE-04：任务未真正开始即失败 → 退还冻结金（线程内失败由 _safe_run 自行处理）
        try:
            refund(pre_auth_id, reason="指导板提交失败")
        except Exception:
            pass
        raise
    finally:
        conn.close()


@api.post("/memoirs/{memoir_id}/chapters/{chapter_id}/video")
async def regen_video(memoir_id: str, chapter_id: str, req: Request):
    """§8.3 章节视频（本地合成替代 Seedance 2.0 Mini）。需 pre_auth。"""
    b = await _body(req)
    pre_auth_id = b.get("pre_auth_id")
    if not pre_auth_id:
        raise ApiError("MISSING_FIELD", "pre_auth_id 必填（BR-004）")
    conn = get_conn()
    tid = None
    try:
        _get_memoir(conn, memoir_id)
        ch = fetch_one(conn, "SELECT * FROM memoir_chapters WHERE chapter_id=?", (chapter_id,))
        if not ch:
            raise ApiError("INVALID_PARAMS", "章节不存在")
        chapter = _clean_chapter(ch)
        # 预检 BR-021（本地宽松：至少需指导板图 + 至少1张参考照）
        memoir = _get_memoir(conn, memoir_id)
        photos = len(memoir.get("reference_photos") or [])
        if photos < 1:
            raise ApiError("ERR_INSUFFICIENT_PHOTOS", "请至少上传 1 张人物参考照（BR-015）")
        if not chapter.get("storyboard_image_url"):
            raise ApiError("BR_STORYBOARD_MISSING", "请先生成指导板（BR-021）")
        est = estimate_credits({"duration": int(chapter.get("duration") or 15),
                                "resolution": memoir.get("target_resolution") or "720p",
                                "audio": False, "model_id": "seedance-2.0-mini"})
        cost = est["estimated_credits"]
        bal = fetch_one(conn, "SELECT points_balance FROM users WHERE user_id=?", (SINGLE_USER_ID,))["points_balance"]
        if bal < cost:
            raise ApiError("INSUFFICIENT_POINTS", "积分不足：余额 %d < 预估 %d" % (bal, cost))
        tid = uid()
        conn.execute(
            """INSERT INTO memoir_tasks (task_id, memoir_id, chapter_id, kind, status, pre_auth_id, cost_credits)
               VALUES (?,?,?,?,?,?,?)""",
            (tid, memoir_id, chapter_id, "video", "queued", pre_auth_id, cost))
        conn.execute("UPDATE memoir_chapters SET status='video_generating', last_task_id=? WHERE chapter_id=?",
                     (tid, chapter_id))
        conn.commit()
        try:
            threading.Thread(target=_safe_run, args=(tid, "video", memoir_id, chapter_id,
                                {"chapter": chapter, "cost": cost}), daemon=True).start()
        except Exception as e:
            # CLOSE-01+04：线程启动失败 → 删除任务行并退还冻结金，闭环收尾
            try:
                c2 = get_conn()
                c2.execute("DELETE FROM memoir_tasks WHERE task_id=?", (tid,))
                refund(pre_auth_id, reason="线程启动失败")
                c2.commit(); c2.close()
            except Exception:
                pass
            raise ApiError("INTERNAL_ERROR", "生成线程启动失败: %s" % str(e)[:120])
        return ok({"task_id": tid, "status": "queued", "estimated_credits": cost})
    except Exception:
        # CLOSE-04：任务未真正开始即失败 → 退还冻结金（线程内失败由 _safe_run 自行处理）
        try:
            refund(pre_auth_id, reason="视频提交失败")
        except Exception:
            pass
        raise
    finally:
        conn.close()


@api.get("/memoirs/{memoir_id}/chapters/{chapter_id}/task")
async def chapter_task(memoir_id: str, chapter_id: str):
    """轮询当前章节的最新生成任务（替代 WS task 事件）。"""
    conn = get_conn()
    try:
        _get_memoir(conn, memoir_id)
        t = fetch_one(conn,
            "SELECT * FROM memoir_tasks WHERE chapter_id=? ORDER BY created_at DESC LIMIT 1", (chapter_id,))
        return ok({"task": row_to_dict(t) if t else None})
    finally:
        conn.close()


# ----------------------------- 预检（spec §3.5 BR-021） -----------------------------
@api.get("/memoirs/{memoir_id}/pre-check")
async def pre_check(memoir_id: str):
    conn = get_conn()
    try:
        memoir = _get_memoir(conn, memoir_id)
        chapters = _list_chapters(conn, memoir_id)
        target = int(memoir.get("storyboard_count") or 4)
        photos = len(memoir.get("reference_photos") or []) >= 1
        planning = sum(1 for c in chapters if c.get("status") in ("planned", "board_ready", "video_ready")) == target
        storyboard = sum(1 for c in chapters if c.get("storyboard_image_url")) == target and target > 0
        video = sum(1 for c in chapters if c.get("video_url")) == target and target > 0
        return ok({"photos": photos, "planning": planning, "storyboard": storyboard, "video": video,
                   "chapter_count": len(chapters), "target": target})
    finally:
        conn.close()


# ----------------------------- 导出交付包（spec §3.1 export-package） -----------------------------
@api.post("/memoirs/{memoir_id}/export-package")
async def export_package(memoir_id: str, req: Request):
    import zipfile
    conn = get_conn()
    try:
        memoir = _get_memoir(conn, memoir_id)
        chapters = _list_chapters(conn, memoir_id)
        name = "memoir_pkg_%s.zip" % memoir_id[:8]
        path = os.path.join(OUTPUTS, name)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            meta = {
                "subject_name": memoir.get("subject_name"),
                "target_ratio": memoir.get("target_ratio"),
                "target_resolution": memoir.get("target_resolution"),
                "storyboard_count": memoir.get("storyboard_count"),
                "person_perspective": memoir.get("person_perspective"),
                "chapters": [{
                    "chapter_index": c.get("chapter_index"), "title": c.get("title"),
                    "era": c.get("era"), "emotion": c.get("emotion"),
                    "storyboard_image_url": c.get("storyboard_image_url"),
                    "video_url": c.get("video_url"),
                    "shot_data": c.get("shot_data"),
                } for c in chapters],
            }
            z.writestr("metadata.json", json.dumps(meta, ensure_ascii=False, indent=2))
            # 打包指导板图
            for c in chapters:
                url = c.get("storyboard_image_url")
                if url and url.startswith("/file?name="):
                    bn = url.split("name=")[-1]
                    fp = os.path.join(OUTPUTS, os.path.basename(bn))
                    if os.path.isfile(fp):
                        z.write(fp, "storyboards/%s_%s%s" % (c.get("chapter_index"), os.path.basename(bn),
                                                             os.path.splitext(fp)[1]))
        return ok({"download_url": "/file?name=" + name, "expires_at": int(time.time()) + 86400})
    finally:
        conn.close()
