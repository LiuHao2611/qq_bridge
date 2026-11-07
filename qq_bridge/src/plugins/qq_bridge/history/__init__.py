# -*- coding: utf-8 -*-
"""history（对话历史 + 群成员档案，2026-09-07 新增）。

管理页「对话」需要看到：谁在哪个群说了什么、什么时候说的、群主/管理员是谁、
谁在黑名单里。此前项目**完全没有消息落盘**，也从未主动拉取过群/成员元数据，
本模块补齐这两块能力：

  * SQLite 落盘（data/qq_history.db）：messages / profiles / groups 三张表；
  * 档案缓存：把每次消息里带到的昵称/群名片/角色攒进 profiles，页面离线也能看；
  * 头像：不落盘，统一用腾讯官方 qlogo 直链（实时、免鉴权），见 avatar_url()。

设计约束：
  * 同步 sqlite3 + 短 busy_timeout；调用方一律用 `await asyncio.to_thread(...)`
    包一层，避免阻塞 nonebot 事件循环；
  * 每次操作独立连接（to_thread 会跨线程，不能复用带 check_same_thread 的连接）；
  * 任何异常都不得影响消息主流程（历史是旁路，CLOSE-02：失败只记日志）。
"""
from __future__ import annotations

import base64
import contextlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import Config, get_logger, jlog

log = get_logger()

# data/ 目录与表情包库同级（qq_bridge/data/）：直接由 STICKER_DIR 反推最稳，
# 反推失败再按包路径上溯 5 层（history → qq_bridge → plugins → src → qq_bridge）。
def _resolve_data_dir() -> str:
    sticker = str(getattr(Config, "STICKER_DIR", "") or "")
    if sticker:
        parent = os.path.dirname(sticker.rstrip("\\/"))
        if parent:
            return parent
    cur = os.path.abspath(__file__)
    for _ in range(5):
        cur = os.path.dirname(cur)
    return os.path.join(cur, "data")


_DATA_DIR = _resolve_data_dir()
DB_PATH = os.path.join(_DATA_DIR, "qq_history.db")
# 本地图片/视频落盘目录，bridge 通过 /api/qq/file 暴露给前端显示
QQ_FILES_DIR = os.path.join(_DATA_DIR, "qq_files")

# 单个会话最多保留多少条（防止无限膨胀，超出按时间裁剪）
KEEP_PER_PEER = 2000


def _conn() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    # ⚠️ 这里**必须**用 DELETE，不能用 WAL（2026-11-03 实测）。
    # 原因：data/ 目录是 Docker bind-mount，qq_history.db 同时被
    #   ① 容器内 bot 写；② 宿主 manju_agent 后端读（/api/qq/history）。
    # WAL 依赖 -shm 共享内存做跨进程协调，而 bind-mount 两侧的 -shm 语义并不互通；
    # 容器每次 recreate（SQLite 未干净关闭）都会留下陈旧 -shm，之后每次 open 抛
    # "disk I/O error" / "unable to open database file"，日志每分钟刷一条。
    # 本项目其余 5 个库（qq_checkin/qq_memory/...）本来就用 delete 模式，一直正常。
    # 写入量小（聊天记录），busy_timeout=5000 足以覆盖并发读。
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as e:  # 只读挂载等极端情况：不阻断连接，仅告警
        log.warning(f"history_journal_delete_failed: {type(e).__name__}: {e}")
    return conn


@contextlib.contextmanager
def _db():
    """`_conn()` 的 with 版本：**提交/回滚后一定 close()**。

    为什么不能直接 `with _conn() as c:` —— Python 里 `with sqlite3.Connection`
    只包一层事务（退出时 commit/rollback），**不会关闭连接**。本模块有 13 处
    `with _conn() as c:`，等于每操作一次漏一个 fd，长跑必然耗光句柄，还会把
    库长期钉在某个日志模式上（删除/切换 WAL 都需要独占锁）。
    """
    conn = _conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ext_from_url(url: str) -> str:
    """从 URL / 路径 / base64 头推断扩展名，未知则默认 .bin。"""
    s = (url or "").strip()
    if not s:
        return ".bin"
    # base64 图片常见来自文生图 PNG/JPEG；按内容头猜
    if s.startswith("base64://"):
        head = s[9:13]
        if head.startswith("/9j"):
            return ".jpg"
        if head.startswith("iVB"):
            return ".png"
        if head.startswith("R0l"):
            return ".gif"
        if head.startswith("Qk0"):
            return ".bmp"
        if head.startswith("AAAA") or head.startswith("AAAB"):
            return ".webp"
        return ".png"
    # URL / 本地路径
    path_part = s.split("?", 1)[0].split("#", 1)[0]
    ext = os.path.splitext(path_part)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".mp4", ".mov", ".avi", ".mkv"):
        return ext
    # 已知 content-type 映射兜底
    guess, _ = mimetypes.guess_extension(mimetypes.guess_type(path_part)[0] or "")
    return guess or ".bin"


#: http 图片下载落盘的大小上限（QQ 聊天图片一般 < 20MB）
_HTTP_DL_MAX_BYTES = 20 * 1024 * 1024


def _download_http_image(url: str) -> str:
    """下载 http(s) 图片到本地并返回 /api/qq/file 地址。

    背景：QQ 聊天图片的 gchat.qpic.cn 链接是**临时的**，过一段时间就 403，
    历史页会剩下一堆裂图/[图片] 占位。落盘后长期可看；下载失败退回原链接。
    """
    try:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = resp.read(_HTTP_DL_MAX_BYTES + 1)
        if not data or len(data) > _HTTP_DL_MAX_BYTES:
            return url
        os.makedirs(QQ_FILES_DIR, exist_ok=True)
        name = f"{uuid.uuid4().hex}{_ext_from_url(url)}"
        with open(os.path.join(QQ_FILES_DIR, name), "wb") as f:
            f.write(data)
        return f"/api/qq/file?name={name}"
    except Exception:  # noqa: BLE001 — 旁路能力，失败不影响主流程
        return url


def _persist_media(url_or_path: str, *, public_base: str = "", download_http: bool = False) -> str:
    """把图片/视频 URL 或本地文件持久化为前端可访问的 URL。

    * http(s) 直链：默认原样返回；download_http=True 时下载落盘（防 CDN 过期）；
    * base64:// 或本地文件：保存到 data/qq_files/，返回 /api/qq/file?name=xxx.ext；
    * 其它：原样返回（可能无效，前端自行处理）。
    """
    s = (url_or_path or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://")):
        return _download_http_image(s) if download_http else s
    try:
        os.makedirs(QQ_FILES_DIR, exist_ok=True)
        ext = _ext_from_url(s)
        name = f"{uuid.uuid4().hex}{ext}"
        dest = os.path.join(QQ_FILES_DIR, name)
        if s.startswith("base64://"):
            data = base64.b64decode(s[9:])
            with open(dest, "wb") as f:
                f.write(data)
        elif os.path.isfile(s):
            shutil.copy2(s, dest)
        else:
            # 非本地文件、非 URL、非 base64：可能是 NapCat 缓存 ID，直接返回原串
            return s
        return f"/api/qq/file?name={name}"
    except Exception as e:
        log.warning(jlog("persist_media_failed", src=s[:80], error=str(e)[:120]))
        return s


def _json_card_text(raw: str) -> str:
    """QQ 小程序/图文 JSON 卡片 → 可读文本（标题 + 业务链接）。

    背景：手机 QQ 分享 B站/抖音等会发 json 卡片，此前只存「[卡片消息]」占位，
    管理页完全看不出内容。这里提取 prompt / title / desc 和卡内业务链接
    （过滤 QQ 图床域名），让历史记录跟 QQ 里看到的一样可读。
    """
    try:
        obj = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(obj, dict):
        return ""
    prompt = str(obj.get("prompt") or "").strip()
    meta = obj.get("meta") if isinstance(obj.get("meta"), dict) else {}
    title = ""
    desc = ""
    for node in meta.values():
        if isinstance(node, dict):
            title = title or str(node.get("title") or "").strip()
            desc = desc or str(node.get("desc") or "").strip()
    parts: list[str] = []
    for p in (prompt, title if title != prompt else "", desc):
        p = str(p or "").strip()
        if p and p not in parts:
            parts.append(p)
    urls: list[str] = []

    def _walk(o: Any) -> None:
        if isinstance(o, str):
            urls.extend(re.findall(r"https?://[^\s\"'<>\\]+", o))
        elif isinstance(o, dict):
            for v in o.values():
                _walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                _walk(v)

    _walk(obj)
    uniq = list(dict.fromkeys(urls))
    # QQ 图床（小程序配图）不是业务链接，过滤掉
    biz = [u for u in uniq if not re.search(r"(gtimg|qpic\.cn|ugcimg)", u)]
    for u in (biz or uniq)[:2]:
        parts.append(u)
    return " ".join(parts).strip()


def _xml_card_text(raw: str) -> str:
    """XML 卡片（老版分享）→ 可读文本（标题 + 链接）。"""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    urls = list(dict.fromkeys(re.findall(r"https?://[^\s\"'<>]+", raw)))
    biz = [u for u in urls if not re.search(r"(gtimg|qpic\.cn|ugcimg)", u)]
    parts = [p for p in (title, *(biz or urls)[:2]) if p]
    return " ".join(parts).strip()


def _segment_summary(st: str, data: dict) -> str:
    """非 text/image 消息段转成可读的占位文本，避免气泡里空无一物。"""
    if st == "at":
        qq = data.get("qq") or data.get("id") or "?"
        return f"[@{qq}]"
    if st == "face":
        return "[表情]"
    if st == "mface":
        return data.get("summary") or "[大表情]"
    if st == "reply":
        return "[回复]"
    if st == "video":
        return "[视频]"
    if st == "record":
        return "[语音]"
    if st == "file":
        return f"[文件:{data.get('name') or ''}]"
    if st == "forward":
        return "[合并转发]"
    if st == "json":
        return _json_card_text(str(data.get("data") or "")) or "[卡片消息]"
    if st == "xml":
        return _xml_card_text(str(data.get("data") or "")) or "[XML消息]"
    if st == "markdown":
        return "[Markdown]"
    return f"[{st}]"


def init_db() -> None:
    """建表（幂等）。插件 register() 时调用一次。"""
    try:
        with _db() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         REAL    NOT NULL,
                    self_id    TEXT    DEFAULT '',
                    scene      TEXT    NOT NULL,
                    peer_id    INTEGER NOT NULL,
                    user_id    INTEGER NOT NULL,
                    nickname   TEXT    DEFAULT '',
                    card       TEXT    DEFAULT '',
                    role       TEXT    DEFAULT '',
                    direction  TEXT    NOT NULL,
                    kind       TEXT    DEFAULT 'text',
                    text       TEXT    DEFAULT '',
                    payload    TEXT    DEFAULT '',
                    trace_id   TEXT    DEFAULT '',
                    message_id INTEGER DEFAULT 0
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_msg_peer_ts ON messages(scene, peer_id, ts)")
            # 兼容旧库：messages 表可能已有数据但无 message_id 列（撤回功能需要），增量补列
            try:
                _cols = [r[1] for r in c.execute("PRAGMA table_info(messages)").fetchall()]
                if "message_id" not in _cols:
                    c.execute("ALTER TABLE messages ADD COLUMN message_id INTEGER DEFAULT 0")
            except Exception:  # noqa: BLE001 — 旧库兼容，绝不影响主流程
                pass
            c.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts)")
            # 回补 NapCat 历史要按 message_id 去重，给它建个索引（2026-10-28）
            c.execute("CREATE INDEX IF NOT EXISTS idx_msg_mid ON messages(scene, peer_id, message_id)")
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    peer_id    INTEGER NOT NULL,
                    user_id    INTEGER NOT NULL,
                    nickname   TEXT DEFAULT '',
                    card       TEXT DEFAULT '',
                    role       TEXT DEFAULT '',
                    updated_at REAL,
                    PRIMARY KEY (peer_id, user_id)
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    group_id     INTEGER PRIMARY KEY,
                    name         TEXT DEFAULT '',
                    member_count INTEGER DEFAULT 0,
                    owner_id     INTEGER DEFAULT 0,
                    updated_at   REAL
                )
                """
            )
    except Exception as e:  # 历史库建不起来也不能拖垮机器人
        log.error(jlog("history_init_failed", error=str(e)[:200]))


# ----------------------------------------------------------------------------
# 头像 / 档案
# ----------------------------------------------------------------------------
def avatar_url(uin: Any, size: int = 100) -> str:
    """QQ 头像直链（腾讯官方 qlogo，免鉴权、实时更新）。

    s=100 头像 / s=640 高清；uin 非法时返回空串（前端显示占位）。
    """
    try:
        n = int(uin or 0)
    except (TypeError, ValueError):
        return ""
    return "https://q1.qlogo.cn/g?b=qq&nk=%d&s=%d" % (n, size) if n > 0 else ""


def group_avatar_url(group_id: Any, size: int = 100) -> str:
    """群头像直链。"""
    try:
        n = int(group_id or 0)
    except (TypeError, ValueError):
        return ""
    return "https://p.qlogo.cn/gh/%d/%d/%d" % (n, n, size) if n > 0 else ""


def upsert_profile(peer_id: int, user_id: int, nickname: str = "",
                   card: str = "", role: str = "") -> None:
    """缓存成员档案（昵称/群名片/角色）。空值不覆盖已有值。"""
    if not user_id:
        return
    try:
        with _db() as c:
            row = c.execute(
                "SELECT nickname, card, role FROM profiles WHERE peer_id=? AND user_id=?",
                (int(peer_id or 0), int(user_id)),
            ).fetchone()
            if row:
                nick, old_card, old_role = row
                c.execute(
                    "UPDATE profiles SET nickname=?, card=?, role=?, updated_at=? "
                    "WHERE peer_id=? AND user_id=?",
                    (
                        nickname or nick or "",
                        card or old_card or "",
                        role or old_role or "",
                        time.time(),
                        int(peer_id or 0),
                        int(user_id),
                    ),
                )
            else:
                c.execute(
                    "INSERT INTO profiles(peer_id, user_id, nickname, card, role, updated_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (int(peer_id or 0), int(user_id), nickname or "", card or "",
                     role or "", time.time()),
                )
    except Exception as e:
        log.warning(jlog("history_profile_failed", error=str(e)[:120]))


def list_profiles(peer_id: int) -> dict:
    """某会话（群/私聊）已知成员档案 → {user_id: {nickname,card,role,avatar}}。"""
    out: dict = {}
    try:
        with _db() as c:
            for uid, nick, card, role in c.execute(
                "SELECT user_id, nickname, card, role FROM profiles WHERE peer_id=?",
                (int(peer_id or 0),),
            ):
                out[int(uid)] = {
                    "nickname": nick or "",
                    "card": card or "",
                    "role": role or "",
                    "avatar": avatar_url(uid),
                }
    except Exception as e:
        log.warning(jlog("history_profiles_failed", error=str(e)[:120]))
    return out


def upsert_group(group_id: int, name: str = "", member_count: int = 0, owner_id: int = 0) -> None:
    """缓存群资料（群名/成员数/群主）。空值不覆盖。"""
    if not group_id:
        return
    try:
        with _db() as c:
            row = c.execute(
                "SELECT name, member_count, owner_id FROM groups WHERE group_id=?",
                (int(group_id),),
            ).fetchone()
        if row:
            old_name, old_cnt, old_owner = row
            name = name or old_name or ""
            member_count = member_count or old_cnt or 0
            owner_id = owner_id or old_owner or 0
        with _db() as c:
            c.execute(
                "INSERT INTO groups(group_id, name, member_count, owner_id, updated_at) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(group_id) DO UPDATE SET name=?, member_count=?, owner_id=?, updated_at=?",
                (int(group_id), name, int(member_count or 0), int(owner_id or 0), time.time(),
                 name, int(member_count or 0), int(owner_id or 0), time.time()),
            )
    except Exception as e:
        log.warning(jlog("history_group_failed", error=str(e)[:120]))


def list_groups() -> list:
    """已缓存的群资料列表。"""
    out = []
    try:
        with _db() as c:
            for gid, name, cnt, owner in c.execute(
                "SELECT group_id, name, member_count, owner_id FROM groups ORDER BY updated_at DESC"
            ):
                out.append({
                    "group_id": int(gid),
                    "name": name or "",
                    "member_count": int(cnt or 0),
                    "owner_id": int(owner or 0),
                    "avatar": group_avatar_url(gid),
                })
    except Exception as e:
        log.warning(jlog("history_groups_failed", error=str(e)[:120]))
    return out


# ----------------------------------------------------------------------------
# 消息读写
# ----------------------------------------------------------------------------
def _trim(c: sqlite3.Connection, scene: str, peer_id: int) -> None:
    """单个会话只留最近 KEEP_PER_PEER 条，防止库无限膨胀。"""
    try:
        row = c.execute(
            "SELECT COUNT(1) FROM messages WHERE scene=? AND peer_id=?", (scene, peer_id)
        ).fetchone()
        if row and row[0] > KEEP_PER_PEER:
            keep = c.execute(
                "SELECT id FROM messages WHERE scene=? AND peer_id=? "
                "ORDER BY ts DESC LIMIT ?",
                (scene, peer_id, KEEP_PER_PEER),
            ).fetchall()
            if keep:
                min_id = min(r[0] for r in keep)
                c.execute(
                    "DELETE FROM messages WHERE scene=? AND peer_id=? AND id<?",
                    (scene, peer_id, min_id),
                )
    except Exception:
        pass


def add_message(*, ts: float, self_id: str, scene: str, peer_id: int, user_id: int,
                nickname: str = "", card: str = "", role: str = "", direction: str = "in",
                kind: str = "text", text: str = "", payload: Any = None,
                trace_id: str = "", message_id: int = 0) -> None:
    """写一条消息（入站 in / 出站 out），并顺带更新成员档案。"""
    try:
        p = json.dumps(payload, ensure_ascii=False) if payload not in (None, "", []) else ""
        with _db() as c:
            c.execute(
                "INSERT INTO messages(ts, self_id, scene, peer_id, user_id, nickname, card,"
                " role, direction, kind, text, payload, trace_id, message_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (float(ts), str(self_id or ""), scene, int(peer_id or 0), int(user_id or 0),
                 nickname or "", card or "", role or "", direction, kind or "text",
                 text or "", p, trace_id or "", int(message_id or 0)),
            )
            _trim(c, scene, peer_id)
    except Exception as e:
        log.warning(jlog("history_add_failed", error=str(e)[:120]))

    # 档案顺便更新（群成员的角色/名片靠这里慢慢攒起来）
    if direction == "in" and user_id:
        upsert_profile(peer_id, user_id, nickname, card, role)


def list_conversations(limit: int = 50) -> list:
    """会话列表（按最后一条消息倒序）。群用群名/群号，私聊用昵称。"""
    out = []
    try:
        with _db() as c:
            rows = c.execute(
                "SELECT scene, peer_id, MAX(ts) AS last_ts, COUNT(1) AS cnt "
                "FROM messages GROUP BY scene, peer_id ORDER BY last_ts DESC LIMIT ?",
                (int(limit or 50),),
            ).fetchall()
            for scene, peer_id, last_ts, cnt in rows:
                last = c.execute(
                    "SELECT text, kind, direction, user_id, nickname, card FROM messages "
                    "WHERE scene=? AND peer_id=? ORDER BY ts DESC LIMIT 1",
                    (scene, peer_id),
                ).fetchone()
                gid = int(peer_id) if scene == "group" else 0
                name = ""
                if gid:
                    g = c.execute(
                        "SELECT name FROM groups WHERE group_id=?", (gid,)
                    ).fetchone()
                    name = (g[0] if g and g[0] else "") or ("群 %d" % gid)
                else:
                    p = c.execute(
                        "SELECT nickname, card FROM profiles WHERE peer_id=? AND user_id=?",
                        (peer_id, peer_id),
                    ).fetchone()
                    name = ((p[1] or p[0]) if p else "") or ("QQ %d" % int(peer_id))
                out.append({
                    "scene": scene,
                    "peer_id": int(peer_id),
                    "name": name,
                    "last_ts": float(last_ts or 0),
                    "count": int(cnt or 0),
                    "last_text": (last[0] or "") if last else "",
                    "last_kind": (last[1] or "text") if last else "text",
                    "last_direction": (last[2] or "in") if last else "in",
                    "last_user_id": int(last[3] or 0) if last else 0,
                    "avatar": group_avatar_url(gid) if gid else avatar_url(peer_id),
                })
    except Exception as e:
        log.warning(jlog("history_conversations_failed", error=str(e)[:120]))
    return out


def count_messages(scene: str, peer_id: int) -> int:
    """某会话的消息总数（分页元信息用）。"""
    try:
        with _db() as c:
            row = c.execute(
                "SELECT COUNT(1) FROM messages WHERE scene=? AND peer_id=?",
                (scene, int(peer_id or 0)),
            ).fetchone()
            return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def list_messages_page(scene: str, peer_id: int, limit: int = 60,
                       before_ts: float | None = None) -> dict:
    """游标分页读消息（2026-09-07 优化，参考业界「后端分页」做法）。

    要点：
      * 先按 ts **倒序**取 limit+1 条（多取一条用来判断 has_more），再翻转成正序，
        既走得到 ts 索引，又符合"最早的在前"的阅读顺序；
      * 返回 has_more / next_before_ts / total，前端不用自己算还有没有更多；
      * limit 做钳制（1~200），防止恶意/误传参数拖垮查询。
    """
    try:
        limit = max(1, min(int(limit or 60), 200))
    except (TypeError, ValueError):
        limit = 60
    rows = []
    try:
        sql = ("SELECT id, message_id, ts, user_id, nickname, card, role, direction, kind, text, payload,"
               " trace_id FROM messages WHERE scene=? AND peer_id=?")
        args: list = [scene, int(peer_id)]
        if before_ts:
            sql += " AND ts < ?"
            args.append(float(before_ts))
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit + 1)
        with _db() as c:
            rows = list(c.execute(sql, args).fetchall())
    except Exception as e:
        log.warning(jlog("history_page_failed", error=str(e)[:120]))

    has_more = len(rows) > limit
    rows = rows[:limit]
    out = [_row_to_message(r) for r in reversed(rows)]
    return {
        "messages": out,
        "has_more": has_more,
        "next_before_ts": (out[0]["ts"] if out else None),
        "total": count_messages(scene, peer_id),
    }


def _row_to_message(r) -> dict:
    """行 → 消息 dict（含 id，前端据此做分页去重）。"""
    mid, msg_id, ts, uid, nick, card, role, direction, kind, text, payload, trace_id = r
    imgs: list = []
    if payload:
        try:
            pj = json.loads(payload)
            if isinstance(pj, list):
                imgs = pj
            elif isinstance(pj, dict):
                imgs = pj.get("images") or []
        except Exception:
            imgs = []
    return {
        "id": int(mid or 0),
        "message_id": int(msg_id or 0),
        "ts": float(ts),
        "user_id": int(uid or 0),
        "nickname": nick or "",
        "card": card or "",
        "role": role or "",
        "direction": direction,
        "kind": kind or "text",
        "text": text or "",
        "images": imgs,
        "avatar": avatar_url(uid),
        "trace_id": trace_id or "",
    }


def list_messages(scene: str, peer_id: int, limit: int = 200,
                  before_ts: float | None = None) -> list:
    """某会话的消息（正序，默认最近 limit 条；before_ts 用于向上翻页）。"""
    out = []
    try:
        sql = ("SELECT id, message_id, ts, user_id, nickname, card, role, direction, kind, text, payload,"
               " trace_id FROM messages WHERE scene=? AND peer_id=?")
        args: list = [scene, int(peer_id)]
        if before_ts:
            sql += " AND ts < ?"
            args.append(float(before_ts))
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit or 200))
        with _db() as c:
            rows = list(c.execute(sql, args).fetchall())
        for r in reversed(rows):
            out.append(_row_to_message(r))
    except Exception as e:
        log.warning(jlog("history_list_failed", error=str(e)[:120]))
    return out


def record_incoming(event: Any, trace_id: str = "", self_id: str = "") -> None:
    """记录一条入站消息（群/私聊都记，含未 @机器人 被丢弃的，保证历史完整）。

    注意：这里**不走** dispatcher.normalize()——它会把未 @ 的群消息丢掉，
    而历史要能看到群里所有人在说什么（页面按 QQ 的样子展示）。
    """
    mt = str(getattr(event, "message_type", "") or "")
    if mt not in ("group", "private"):
        return
    uid = int(getattr(event, "user_id", 0) or 0)
    gid = int(getattr(event, "group_id", 0) or 0)
    scene = "group" if mt == "group" else "private"
    peer_id = gid if scene == "group" else uid

    segs = getattr(event, "original_message", None) or getattr(event, "message", None) or []
    texts: list = []
    images: list = []
    has_video = False
    for seg in segs or []:
        st = str(getattr(seg, "type", "") or "")
        data = getattr(seg, "data", None) or {}
        if st == "text":
            texts.append(str(data.get("text") or ""))
        elif st == "image":
            u = data.get("url") or data.get("file") or ""
            if u:
                # 统一落盘：QQ 图片 CDN 链接会过期，下载到本地历史才能长期可看
                images.append(_persist_media(u, download_http=True))
        elif st == "video":
            has_video = True
            u = data.get("url") or data.get("file") or ""
            if u:
                images.append(_persist_media(u))
        else:
            summary = _segment_summary(st, data)
            if summary:
                texts.append(summary)
    text = "".join(texts).strip()

    sender = getattr(event, "sender", None)
    nickname = str(getattr(sender, "nickname", "") or "")
    card = str(getattr(sender, "card", "") or "")
    role = str(getattr(sender, "role", "") or "")

    kind = "video" if has_video else ("image" if images else "text")
    add_message(
        ts=time.time(), self_id=self_id, scene=scene, peer_id=peer_id, user_id=uid,
        nickname=nickname, card=card, role=role, direction="in", kind=kind,
        text=text, payload=images or None, trace_id=trace_id,
        message_id=int(getattr(event, "message_id", 0) or 0),
    )
    if scene == "group":
        gname = str(getattr(event, "group_name", "") or "")
        if gname:
            upsert_group(gid, name=gname)


def record_outgoing(ctx: dict, raw_message: str, self_id: str = "", message_id: int = 0) -> None:
    """记录一条出站消息（机器人自己发的）。

    把 OneBot 消息串拆成文本 / 图片 / 视频 / 其它段：
      * 图片/视频：base64 或本地文件会保存到 data/qq_files/，返回 /api/qq/file 可访问 URL；
      * @/表情/回复 等非文本段：转成占位文本，避免气泡空无一物；
      * 文本段：保留原样。
    """
    gid = int((ctx or {}).get("group_id") or 0)
    uid = int((ctx or {}).get("user_id") or 0)
    scene = "group" if gid else "private"
    peer_id = gid if gid else uid
    msg = str(raw_message or "")

    # 按 CQ 码切分，保留原始文本段顺序
    parts = re.split(r"(\[CQ:[^\]]+\])", msg)
    texts: list = []
    images: list = []
    has_video = False
    for part in parts:
        if not part:
            continue
        if not part.startswith("[CQ:"):
            texts.append(part)
            continue
        m = re.match(r"\[CQ:([^,\]]+)(?:,([^\]]*))?\]", part)
        if not m:
            continue
        st = m.group(1)
        params = {}
        if m.group(2):
            for kv in m.group(2).split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    params[k.strip()] = v.strip()
        if st == "image":
            u = params.get("url") or params.get("file") or ""
            if u:
                images.append(_persist_media(u, download_http=True))
        elif st == "video":
            has_video = True
            u = params.get("url") or params.get("file") or ""
            if u:
                images.append(_persist_media(u))
        else:
            summary = _segment_summary(st, params)
            if summary:
                texts.append(summary)
    text = "".join(texts).strip()

    kind = "video" if has_video else ("image" if images else "text")

    self_uid = 0
    try:
        self_uid = int(self_id or 0)
    except (TypeError, ValueError):
        self_uid = 0

    add_message(
        ts=time.time(), self_id=str(self_id or ""), scene=scene, peer_id=peer_id,
        user_id=self_uid, nickname="", card="", role="", direction="out", kind=kind,
        text=text, payload=images or None, message_id=int(message_id or 0),
    )


def _raw_message_parts(segments: Any) -> tuple[str, list, bool]:
    """OneBot 原始 message 段（get_*_msg_history 返回的那种）→ (文本, 图片[], 是否视频)。

    与 record_incoming / record_outgoing 的口径保持一致：文本原样、图片落成前端可访问
    的 URL、@/表情/卡片等转占位文本。补齐历史时**不下载** CDN 图片（download_http=False），
    避免一次回补几十张图拖慢接口，直接存原图直链即可正常显示。
    """
    texts: list[str] = []
    images: list[str] = []
    has_video = False
    for seg in segments or []:
        if isinstance(seg, str):
            texts.append(seg)
            continue
        if not isinstance(seg, dict):
            continue
        st = str(seg.get("type") or "")
        data = seg.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        if st == "text":
            texts.append(str(data.get("text") or ""))
        elif st == "image":
            u = str(data.get("url") or data.get("file") or "")
            if u:
                images.append(_persist_media(u))
        elif st == "video":
            has_video = True
            u = str(data.get("url") or data.get("file") or "")
            if u:
                images.append(_persist_media(u))
        elif st == "file":
            fname = str(data.get("name") or data.get("file") or "")
            if fname:
                texts.append(f"[文件:{fname}]")
        else:
            summary = _segment_summary(st, data)
            if summary:
                texts.append(summary)
    return "".join(texts).strip(), images, has_video


def existing_message_ids(scene: str, peer_id: int, ids: list) -> set:
    """给定 message_id 列表，返回其中**已存在**的那些（回补历史时去重用）。"""
    out: set = set()
    clean = []
    for i in ids or ():
        try:
            v = int(i or 0)
        except (TypeError, ValueError):
            continue
        if v:
            clean.append(v)
    if not clean:
        return out
    try:
        with _db() as c:
            for k in range(0, len(clean), 400):        # SQLite 变量上限 999，分批
                chunk = clean[k:k + 400]
                sql = ("SELECT DISTINCT message_id FROM messages WHERE scene=? AND peer_id=?"
                       " AND message_id IN (%s)" % ",".join("?" * len(chunk)))
                for (mid,) in c.execute(sql, [scene, int(peer_id)] + chunk):
                    out.add(int(mid or 0))
    except Exception as e:
        log.warning(jlog("history_existing_ids_failed", error=str(e)[:120]))
    return out


def import_raw_messages(scene: str, peer_id: int, self_id: str, raws: Any) -> dict:
    """把 NapCat 的 get_group_msg_history / get_friend_msg_history 结果补进本地库。

    为什么需要它：本地库只记「机器人在线期间收到的消息」——机器人重启、被风控掉线、
    或者插件重启那段时间的聊天全是空白，页面看起来就像「消息停在昨天」。
    这里按 message_id 去重后回补，机器人自己发的（user_id == self_id）记 out。

    返回 {fetched, imported, skipped}。异常只记日志，绝不影响调用方。
    """
    items = [r for r in (raws or []) if isinstance(r, dict)]
    result = {"fetched": len(items), "imported": 0, "skipped": 0}
    if not items:
        return result
    try:
        self_uid = int(self_id or 0)
    except (TypeError, ValueError):
        self_uid = 0

    ids = []
    for r in items:
        try:
            ids.append(int(r.get("message_id") or 0))
        except (TypeError, ValueError):
            continue
    exists = existing_message_ids(scene, peer_id, ids)

    # 由旧到新写入，保证 ts 顺序自然（NapCat 一般已是正序，这里再兜一次稳定排序）
    def _ts(r):
        try:
            return float(r.get("time") or r.get("timestamp") or 0)
        except (TypeError, ValueError):
            return 0.0

    for r in sorted(items, key=_ts):
        try:
            mid = int(r.get("message_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid and mid in exists:
            result["skipped"] += 1
            continue
        ts = _ts(r) or time.time()
        sender = r.get("sender") or {}
        if not isinstance(sender, dict):
            sender = {}
        uid = int(sender.get("user_id") or r.get("user_id") or 0)
        text, images, has_video = _raw_message_parts(r.get("message") or r.get("message_chain") or [])
        if not text and not images and r.get("raw_message"):
            # 极少数实现只回 raw_message（CQ 码串），拆出来当纯文本用
            text = str(r.get("raw_message") or "").strip()
        kind = "video" if has_video else ("image" if images else "text")
        direction = "out" if (self_uid and uid == self_uid) else "in"
        add_message(
            ts=ts, self_id=str(self_id or ""), scene=scene, peer_id=int(peer_id),
            user_id=uid,
            nickname=str(sender.get("nickname") or ""),
            card=str(sender.get("card") or ""),
            role=str(sender.get("role") or ""),
            direction=direction, kind=kind, text=text,
            payload=images or None, trace_id="", message_id=mid,
        )
        result["imported"] += 1
    return result


def clear_all_history() -> dict:
    """清空所有历史消息、会话档案、群缓存（对话页「清空所有记录」用）。

    返回 {ok, deleted_messages, deleted_profiles, deleted_groups}。
    """
    try:
        with _db() as c:
            msg = c.execute("SELECT COUNT(1) FROM messages").fetchone()[0] or 0
            prof = c.execute("SELECT COUNT(1) FROM profiles").fetchone()[0] or 0
            grp = c.execute("SELECT COUNT(1) FROM groups").fetchone()[0] or 0
            c.execute("DELETE FROM messages")
            c.execute("DELETE FROM profiles")
            c.execute("DELETE FROM groups")
            c.execute("VACUUM")
            return {"ok": True, "deleted_messages": msg, "deleted_profiles": prof, "deleted_groups": grp}
    except Exception as e:
        log.warning(jlog("history_clear_failed", error=str(e)[:120]))
        return {"ok": False, "error": str(e)}
