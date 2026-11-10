# -*- coding: utf-8 -*-
"""2026-09-11：历史画廊验收（群里「历史」看九宫格缩略图 + 回复编号看原图）。

覆盖：
  1) parse_items：真实接口结构解析（images[] / result.images[] / result.url）、
     无图任务过滤、i2i→改图 与 t2i→生图 的文案归类
  2) abs_url：相对路径拼 MANJU_FILE_BASE
  3) build_grid：拼出 JPEG、尺寸随张数变化、坏图（None）画占位不抛、空列表报错
  4) fetch_recent / download_thumbs：MockTransport 下的正常与失败降级
  5) remember / pick / active_count：编号取值、越界、TTL 过期、用户隔离
  6) 端到端 _handle_reply：`__GALLERY__` 发拼图并记住编号；`__PICK__` 发原图；
     没翻过相册时给明确提示（不误触发生图）

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_gallery.py -v
"""
from __future__ import annotations

import asyncio
import io

import httpx
import pytest
from PIL import Image

import src.plugins.qq_bridge as M
from src.plugins.qq_bridge import gallery
from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.dispatcher import QQEvent
from src.plugins.qq_bridge.session import Session


def _run(coro):
    return asyncio.run(coro)


def _ev(user_id: int = 10001, group_id: int = 20001) -> QQEvent:
    return QQEvent(user_id=user_id, group_id=group_id,
                   message_type="group", raw_text="")


def _png(color=(200, 40, 60), size=(640, 480)) -> bytes:
    img = Image.new("RGB", size, color)
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def _items(n: int, kind: str = "i2i_img2img") -> list[dict]:
    """模拟主项目 /t2i/tasks 原始返回（images 列表 + type）。"""
    return [{"task_id": "t%d" % i, "prompt": "描述%d" % i, "status": "succeeded",
             "created_at": "2026-09-11 01:00:00", "type": kind,
             "images": [{"url": "/file?name=a%d.png" % i}]} for i in range(n)]


def _parsed_items(n: int, kind: str = "i2i_img2img") -> list[dict]:
    """parse_items 产物（含 image_url / kind），给 remember / 命令测试用。"""
    return gallery.parse_items(_payload(_items(n, kind)))


def _payload(items) -> dict:
    return {"data": {"items": items}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每个用例：清画廊缓存 + 去掉回复延迟（否则每条 _say 睡好几秒）。"""
    gallery.reset()
    monkeypatch.setattr(Config, "REPLY_DELAY_MIN", 0.0)
    monkeypatch.setattr(Config, "REPLY_DELAY_MAX", 0.0)
    yield
    gallery.reset()


class _Rec:
    def __init__(self) -> None:
        self.pics: list[bytes] = []
        self.texts: list[str] = []
        self.images: list[str] = []

    async def pic(self, ctx, data, at_user=False):
        self.pics.append(data)

    async def text(self, ctx, text, at_user=False):
        self.texts.append(str(text))

    async def image(self, ctx, file, at_user=False):
        self.images.append(str(file))


@pytest.fixture
def rec(monkeypatch):
    r = _Rec()
    monkeypatch.setattr(M, "send_pic", r.pic)
    monkeypatch.setattr(M, "send_text", r.text)
    monkeypatch.setattr(M, "send_image", r.image)
    monkeypatch.setattr(M, "maybe_send_sticker", lambda ctx: asyncio.sleep(0))
    # render 强制失败 → 让 _reply 回落 send_text，这样所有说明/提示文本都进
    # rec.texts（便于断言），而不被"渲染成图"藏起来（不影响 send_pic/send_image 直发字节）
    async def _boom(*a, **k):
        raise RuntimeError("pic render disabled in test")

    monkeypatch.setattr(M.pic_mod, "render", _boom)
    return r


# ============================== parse_items ==============================

def test_parse_items_reads_images_and_classifies_kind():
    items = gallery.parse_items(_payload(_items(2, "i2i_img2img")))
    assert len(items) == 2
    assert items[0]["image_url"] == "/file?name=a0.png"
    assert items[0]["kind"] == "改图"
    assert items[0]["task_id"] == "t0"
    assert items[0]["prompt"] == "描述0"
    t2i = gallery.parse_items(_payload(_items(1, "t2i_local")))
    assert t2i[0]["kind"] == "生图"


def test_parse_items_falls_back_to_result_images_and_url():
    raw = [
        {"task_id": "r1", "prompt": "p", "images": [],
         "result": {"images": [{"url": "/file?name=r1.png"}]}},
        {"task_id": "r2", "prompt": "p", "images": [],
         "result": {"url": "/file?name=r2.png"}},
    ]
    items = gallery.parse_items(_payload(raw))
    assert [i["image_url"] for i in items] == ["/file?name=r1.png", "/file?name=r2.png"]


def test_parse_items_skips_imageless_and_bad_rows():
    raw = [
        {"task_id": "ok", "images": [{"url": "/file?name=ok.png"}]},
        {"task_id": "noimg", "images": []},           # 无图 → 过滤
        {"task_id": "emptyurl", "images": [{"url": ""}]},
        "not-a-dict",                                  # 脏数据 → 跳过
    ]
    items = gallery.parse_items(_payload(raw))
    assert [i["task_id"] for i in items] == ["ok"]


def test_parse_items_tolerates_bad_payload():
    assert gallery.parse_items({}) == []
    assert gallery.parse_items({"data": {"items": "oops"}}) == []


# ================================ abs_url ================================

def test_abs_url_joins_file_base(monkeypatch):
    monkeypatch.setattr(Config, "MANJU_FILE_BASE", "http://127.0.0.1:7788")
    assert gallery.abs_url("/file?name=a.png") == "http://127.0.0.1:7788/file?name=a.png"


def test_abs_url_keeps_absolute_and_empty():
    assert gallery.abs_url("http://x/y.png") == "http://x/y.png"
    assert gallery.abs_url("") == ""


# =============================== build_grid ==============================

def test_build_grid_returns_jpeg_with_expected_size():
    data = gallery.build_grid([_png()] * 9)
    assert data[:2] == b"\xff\xd8", "应为 JPEG"
    img = Image.open(io.BytesIO(data))
    expect_w = gallery.PAD * 2 + 3 * gallery.CELL + 2 * gallery.GAP
    assert img.size == (expect_w, expect_w), img.size


def test_build_grid_rows_grow_with_count():
    four = Image.open(io.BytesIO(gallery.build_grid([_png()] * 4)))
    one = Image.open(io.BytesIO(gallery.build_grid([_png()] * 1)))
    assert four.height > one.height, "4 张应占 2 行，比 1 行高"


def test_build_grid_tolerates_broken_images():
    """下载失败（None）或坏 bytes → 画占位格，整页仍出得来。"""
    data = gallery.build_grid([_png(), None, b"not-an-image", _png()])
    assert data[:2] == b"\xff\xd8"


def test_build_grid_rejects_empty():
    with pytest.raises(ValueError):
        gallery.build_grid([])


# ========================= fetch / download（Mock） =========================

def test_fetch_recent_parses_response():
    def h(req):
        return httpx.Response(200, json={
            "data": {"items": _items(3), "pagination": {"total": 3}}})

    items, err, total = _run(gallery.fetch_recent(transport=httpx.MockTransport(h)))
    assert err == ""
    assert len(items) == 3
    assert total == 3


def test_fetch_recent_reports_http_error():
    def h(req):
        return httpx.Response(500, json={"detail": "boom"})

    items, err, total = _run(gallery.fetch_recent(transport=httpx.MockTransport(h)))
    assert items == []
    assert err == "HTTP 500"
    assert total == 0


def test_fetch_recent_sorts_by_created_at_desc(monkeypatch):
    """本地兜底排序：最新日期排最前（即便主项目顺序异常也不乱）。"""
    raw = [
        {"task_id": "old", "images": [{"url": "/a1.png"}], "created_at": "2026-09-01 10:00:00"},
        {"task_id": "new", "images": [{"url": "/a2.png"}], "created_at": "2026-09-10 18:00:00"},
        {"task_id": "mid", "images": [{"url": "/a3.png"}], "created_at": "2026-09-05 12:00:00"},
        {"task_id": "no_date", "images": [{"url": "/a4.png"}], "created_at": ""},
    ]

    def h(req):
        return httpx.Response(200, json={
            "data": {"items": raw, "pagination": {"total": 4}}})

    items, err, total = _run(gallery.fetch_recent(transport=httpx.MockTransport(h)))
    assert [i["task_id"] for i in items] == ["new", "mid", "old", "no_date"]


def test_fetch_recent_sorts_mixed_iso_and_unix_timestamp(monkeypatch):
    """真实现网根因：created_at 混存 ISO 字符串与 unix 时间戳，必须按数值排。

    时间戳 1789085123（≈9/11）比 ISO「2026-09-06」更晚，应排在最前；
    若按字符串比较 '1'<'2' 会把时间戳排到 ISO 之后（用户遇到的"没按最新排"）。
    """
    raw = [
        {"task_id": "iso_old", "images": [{"url": "/a1.png"}], "created_at": "2026-09-06 17:42:07"},
        {"task_id": "ts_new", "images": [{"url": "/a2.png"}], "created_at": 1789085123},
        {"task_id": "ts_mid", "images": [{"url": "/a3.png"}], "created_at": 1789080401},
    ]

    def h(req):
        return httpx.Response(200, json={
            "data": {"items": raw, "pagination": {"total": 3}}})

    items, err, total = _run(gallery.fetch_recent(transport=httpx.MockTransport(h)))
    assert [i["task_id"] for i in items] == ["ts_new", "ts_mid", "iso_old"]


def test_fetch_recent_builds_type_filter_url(monkeypatch):
    """分类筛选：i2i→type_prefix=i2i，t2i_local→type=t2i_local，全部不加。"""
    seen = {}

    def h(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json={
            "data": {"items": _items(1), "pagination": {"total": 1}}})

    _run(gallery.fetch_recent(type_filter="i2i", transport=httpx.MockTransport(h)))
    assert "type_prefix=i2i" in seen["url"], seen["url"]
    _run(gallery.fetch_recent(type_filter="t2i_local", transport=httpx.MockTransport(h)))
    assert "type=t2i_local" in seen["url"], seen["url"]
    _run(gallery.fetch_recent(transport=httpx.MockTransport(h)))
    assert "type" not in seen["url"].split("?")[1], seen["url"]


def test_parse_gallery_arg_classifies_and_reads_page():
    assert gallery.parse_gallery_arg("") == ("", 1)
    assert gallery.parse_gallery_arg("2") == ("", 2)
    assert gallery.parse_gallery_arg("文生图") == ("t2i_local", 1)
    assert gallery.parse_gallery_arg("图生图") == ("i2i", 1)
    assert gallery.parse_gallery_arg("历史 文生图 3") == ("t2i_local", 3)
    assert gallery.parse_gallery_arg("图生图 第2页") == ("i2i", 2)
    # 先判图生图整词，避免「图生图」被「生图」误判成文生图
    assert gallery.parse_gallery_arg("图生图") != ("t2i_local", 1)
    assert gallery.parse_gallery_arg("i2i") == ("i2i", 1)
    assert gallery.parse_gallery_arg("t2i") == ("t2i_local", 1)


def test_download_thumbs_marks_failed_as_none():
    def h(req):
        if "bad" in str(req.url):
            return httpx.Response(404)
        return httpx.Response(200, content=_png())

    out = _run(gallery.download_thumbs(
        ["http://x/good.png", "http://x/bad.png"],
        transport=httpx.MockTransport(h)))
    assert out[0] is not None
    assert out[1] is None, "失败项应为 None（占位），不能抛"


# ============================ remember / pick ============================

def test_remember_then_pick_by_number():
    gallery.remember(10001, _items(3))
    assert gallery.pick(10001, 1)["task_id"] == "t0"
    assert gallery.pick(10001, 3)["task_id"] == "t2"
    assert gallery.active_count(10001) == 3


def test_pick_out_of_range_and_unknown_user():
    gallery.remember(10001, _items(2))
    assert gallery.pick(10001, 0) is None
    assert gallery.pick(10001, 3) is None
    assert gallery.pick(99999, 1) is None
    assert gallery.active_count(99999) == 0


def test_pick_expires_after_ttl(monkeypatch):
    gallery.remember(10001, _items(2))
    monkeypatch.setattr(gallery, "VIEW_TTL", -1.0)  # 立刻过期
    assert gallery.pick(10001, 1) is None
    assert gallery.active_count(10001) == 0, "过期后不应还能点编号"


def test_pick_ignores_garbage_input():
    gallery.remember(10001, _items(2))
    assert gallery.pick(10001, "abc") is None
    assert gallery.pick("bad-uid", 1) is None


# ============================== 端到端命令 ==============================

def test_gallery_command_sends_grid_and_remembers(rec, monkeypatch):
    items = _parsed_items(9)

    async def _fake_fetch(page=1, size=None, type_filter="", transport=None):
        return (items, "", 99)

    async def _fake_build(its, cols=None):
        return b"JPEGBYTES"

    monkeypatch.setattr(gallery, "fetch_recent", _fake_fetch)
    monkeypatch.setattr(gallery, "build_page", _fake_build)

    _run(M._handle_reply(_ev(), Session(10001), "__GALLERY__", 0, 50))

    assert b"JPEGBYTES" in rec.pics, "应先发九宫格拼图：%r" % rec.pics
    assert gallery.active_count(10001) == 9, "发完要记住编号，供回复选原图"


def test_gallery_command_filters_by_class(rec, monkeypatch):
    """`历史 文生图` / `历史 图生图` 把分类传给 fetch_recent，并记住分类供翻页。"""
    seen = {}

    async def _fake_fetch(page=1, size=None, type_filter="", transport=None):
        seen["cls"] = type_filter
        return (_parsed_items(3), "", 3)

    async def _fake_build(its, cols=None):
        return b"JPEGBYTES"

    monkeypatch.setattr(gallery, "fetch_recent", _fake_fetch)
    monkeypatch.setattr(gallery, "build_page", _fake_build)

    _run(M._handle_reply(_ev(), Session(10001), "__GALLERY__@@文生图", 0, 50))
    assert seen["cls"] == "t2i_local"
    _run(M._handle_reply(_ev(), Session(10001), "__GALLERY__@@图生图 2", 0, 50))
    assert seen["cls"] == "i2i"


def test_gallery_context_next_page_keeps_class(rec, monkeypatch):
    """画廊上下文里说「下一页」沿用上次分类（文生图），页码 +1。"""
    calls = []

    async def _fake_fetch(page=1, size=None, type_filter="", transport=None):
        calls.append((page, type_filter))
        return (_parsed_items(3), "", 30)

    async def _fake_build(its, cols=None):
        return b"JPEGBYTES"

    monkeypatch.setattr(gallery, "fetch_recent", _fake_fetch)
    monkeypatch.setattr(gallery, "build_page", _fake_build)

    _run(M._handle_reply(_ev(), Session(10001), "__GALLERY__@@文生图", 0, 50))
    _run(M._handle_reply(_ev(), Session(10001), "__GALLERY__@@下一页", 0, 50))
    assert calls == [(1, "t2i_local"), (2, "t2i_local")], calls


def test_gallery_command_empty_history_tells_user(rec, monkeypatch):
    async def _fake_fetch(page=1, size=None, type_filter="", transport=None):
        return ([], "", 0)

    monkeypatch.setattr(gallery, "fetch_recent", _fake_fetch)
    _run(M._handle_reply(_ev(), Session(10001), "__GALLERY__", 0, 50))
    assert rec.pics == [], "没有历史就不该发拼图"
    assert gallery.active_count(10001) == 0


def test_pick_command_sends_original_image(rec, monkeypatch):
    monkeypatch.setattr(Config, "MANJU_FILE_BASE", "http://127.0.0.1:7788")
    gallery.remember(10001, _parsed_items(3))
    _run(M._handle_reply(_ev(), Session(10001), "__PICK__@@2", 0, 50))
    assert rec.images == ["http://127.0.0.1:7788/file?name=a1.png"], rec.images


def test_pick_without_view_shows_hint(rec):
    """没翻过相册时回复编号 → 明确提示，绝不静默、也不当成生图提示词。"""
    _run(M._handle_reply(_ev(), Session(10001), "__PICK__@@1", 0, 50))
    assert rec.images == []
    joined = " ".join(rec.texts)
    assert "没翻过相册" in joined or any("没翻过相册" in str(t) for t in rec.texts)
