# -*- coding: utf-8 -*-
"""验收：主题编辑器「选择项目图片」（2026-09-24 用户要求）。

覆盖三层：
  1. gallery 契约 —— parse_items 的回收站过滤、fetch_images 的筛选/排序/分页；
  2. 资源入库 —— assets.save_scaled 对「主项目大图」的缩放与转码降级；
  3. 编辑器 REST —— /api/pic/project-images（列表）与 /import（导入，含 SSRF 闸门）。

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_pic_project_images.py -v

说明：所有 HTTP 走 httpx.MockTransport / 假 client，**不依赖主项目真的开着**；
      资源落盘目录 monkeypatch 到 tmp，绝不在 data/themes/assets 里留垃圾。
"""
from __future__ import annotations

import io
import os
import sys

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.plugins.qq_bridge import gallery as gallery_mod  # noqa: E402
from src.plugins.qq_bridge.config import Config  # noqa: E402
from src.plugins.qq_bridge.pic import assets as assets_mod  # noqa: E402


def _png(w: int = 64, h: int = 48) -> bytes:
    """一张真实的小 PNG（供入库/导入用例使用）。"""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (12, 34, 56)).save(buf, format="PNG")
    return buf.getvalue()


def _ts(i: int) -> str:
    """唯一且严格递增的时间戳（i 越大越新），避免同秒导致的排序断言歧义。"""
    from datetime import datetime, timedelta

    return (datetime(2026, 1, 1) + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")


def _task(i: int, kind: str = "t2i_local", deleted: bool = False, prompt: str = "") -> dict:
    return {
        "task_id": "t%03d" % i,
        "prompt": prompt or ("赛博猫 %03d" % i),
        "status": "succeeded",
        "created_at": _ts(i),
        "deleted_at": _ts(9999) if deleted else None,
        "images": [{"url": "/file?name=img_%03d.png" % i}],
        "type": kind,
    }


def _payload(tasks: list[dict], page: int = 1, size: int = 100) -> dict:
    """按主项目的 DESC 分页语义切页返回。"""
    ordered = sorted(tasks, key=lambda t: t["created_at"], reverse=True)
    total = len(ordered)
    start = (page - 1) * size
    return {
        "code": 0,
        "data": {
            "items": ordered[start:start + size],
            "pagination": {
                "page": page, "page_size": size, "total": total,
                "total_pages": max(1, (total + size - 1) // size),
                "has_next": start + size < total, "has_prev": page > 1,
            },
        },
    }


def _transport(tasks: list[dict]) -> httpx.MockTransport:
    """模拟主项目：支持 status/page/size，并**真实实现** type / type_prefix 过滤
    （分类是交给服务端做的，transport 不实现就测不出 total 对不对）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        sel = list(tasks)
        if q.get("type_prefix"):
            sel = [t for t in sel if str(t["type"]).startswith(q["type_prefix"])]
        elif q.get("type"):
            sel = [t for t in sel if t["type"] == q["type"]]
        return httpx.Response(200, json=_payload(
            sel, int(q.get("page") or 1), int(q.get("size") or 100)))

    return httpx.MockTransport(handler)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ============================== 1. gallery 契约 ==============================
def test_parse_items_drops_recycle_bin_when_asked():
    payload = _payload([_task(1), _task(2, deleted=True), _task(3)])
    assert len(gallery_mod.parse_items(payload)) == 3                    # 默认保持既有行为
    kept = gallery_mod.parse_items(payload, drop_deleted=True)
    assert [it["task_id"] for it in kept] == ["t003", "t001"]            # 回收站那条被剔除


def test_parse_items_exposes_abs_thumb_and_kind():
    items = gallery_mod.parse_items(_payload([_task(1)]))
    assert items[0]["thumb_url"].startswith(Config.MANJU_FILE_BASE.rstrip("/"))
    assert items[0]["kind"] == "生图"
    assert gallery_mod.parse_items(_payload([_task(2, kind="i2i_img2img")]))[0]["kind"] == "改图"


def test_fetch_images_filters_sorts_and_paginates():
    tasks = ([_task(i, "t2i_local", prompt="赛博猫 %03d" % i) for i in range(1, 21)]
             + [_task(i, "i2i_img2img", prompt="机械狗 %03d" % i) for i in range(21, 41)]
             + [_task(50, "t2i_local", deleted=True, prompt="已删除的图")])
    res = _run(gallery_mod.fetch_images(page=1, size=12, transport=_transport(tasks)))
    assert res["ok"] and res["error"] == ""
    assert res["total"] == 40, "回收站那张必须被过滤掉：%s" % res["total"]
    assert len(res["items"]) == 12
    assert res["total_pages"] == 4

    # 排序：最新优先 → created_at 单调不增
    ts = [it["created_at"] for it in res["items"]]
    assert ts == sorted(ts, reverse=True), ts

    # 翻页：第二页不与第一页重复
    p2 = _run(gallery_mod.fetch_images(page=2, size=12, transport=_transport(tasks)))
    ids1 = {it["task_id"] for it in res["items"]}
    assert p2["page"] == 2 and len(p2["items"]) == 12
    assert not (ids1 & {it["task_id"] for it in p2["items"]})

    # 升序：最早优先 → 首项不晚于末项
    asc = _run(gallery_mod.fetch_images(page=1, size=10, sort="asc",
                                        transport=_transport(tasks)))
    a = [it["created_at"] for it in asc["items"]]
    assert a == sorted(a), a

    # 关键词：只留命中的，且 total 跟着变
    kw = _run(gallery_mod.fetch_images(page=1, size=40, keyword="机械狗",
                                       transport=_transport(tasks)))
    assert kw["total"] == 20
    assert all("机械狗" in it["prompt"] for it in kw["items"])

    # 分类：图生图只剩「改图」，文生图只剩「生图」（服务端过滤 → total 也是该分类的真实总数）
    i2i = _run(gallery_mod.fetch_images(page=1, size=40, type_filter="i2i",
                                        transport=_transport(tasks)))
    assert i2i["total"] == 20 and all(it["kind"] == "改图" for it in i2i["items"])
    t2i = _run(gallery_mod.fetch_images(page=1, size=40, type_filter="t2i_local",
                                        transport=_transport(tasks)))
    assert t2i["total"] == 20 and all(it["kind"] == "生图" for it in t2i["items"])


def test_fetch_images_scans_multiple_pages_and_reports_capped():
    """超过单页 100 时必须并发翻页扫全（否则只能看到第一页，排序/筛选都是假的）。"""
    tasks = [_task(i) for i in range(1, 251)]
    res = _run(gallery_mod.fetch_images(page=1, size=50, transport=_transport(tasks)))
    assert res["ok"] and res["total"] == 250, res["total"]
    assert res["total_pages"] == 5
    # 升序第 1 页应是最早的 50 张 —— 只能来自"扫到了最后一页"
    asc = _run(gallery_mod.fetch_images(page=1, size=50, sort="asc",
                                        transport=_transport(tasks)))
    first = asc["items"][0]["created_at"]
    assert first == min(t["created_at"] for t in tasks), first


def test_fetch_images_degrades_when_main_project_down():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": 500})
    res = _run(gallery_mod.fetch_images(transport=httpx.MockTransport(handler)))
    assert res["ok"] is False and res["items"] == [] and res["total"] == 0
    assert res["error"], "失败必须带 error（CLOSE-02 禁止静默吞）"


def test_is_project_url_blocks_foreign_host():
    assert gallery_mod.is_project_url("http://127.0.0.1:7788/file?name=a.png") is True
    assert gallery_mod.is_project_url("/file?name=a.png") is False       # 相对地址须先 abs_url
    assert gallery_mod.is_project_url("http://evil.example.com/x.png") is False
    assert gallery_mod.is_project_url("http://169.254.169.254/latest/meta-data") is False


# ============================== 2. 资源入库 ==============================
def test_save_scaled_downsizes_oversized_image(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(assets_mod, "ASSETS_DIR", str(tmp_path / "assets"))
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1600), (200, 30, 90)).save(buf, format="PNG")
    fname = assets_mod.save_scaled(buf.getvalue())
    im = assets_mod.load(fname)
    assert im is not None
    assert max(im.size) <= assets_mod.MAX_DIM, im.size


def test_save_scaled_falls_back_to_jpeg_when_png_too_big(tmp_path, monkeypatch):
    """PNG 压不下去时转 JPEG（主题背景图不需要 alpha），而不是直接拒绝。"""
    from PIL import Image

    monkeypatch.setattr(assets_mod, "ASSETS_DIR", str(tmp_path / "assets"))
    src = Image.effect_noise((512, 512), 120).convert("RGB")   # 噪声：PNG 明显大于 JPEG
    p_png = len(assets_mod._encode(src, "PNG"))
    p_jpg = len(assets_mod._encode(src, "JPEG", quality=90, optimize=True))
    assert p_jpg < p_png, "前提不成立：噪声图 JPEG 应小于 PNG（%d vs %d）" % (p_jpg, p_png)

    raw = assets_mod._encode(src, "PNG")
    fname = assets_mod.save_scaled(raw, max_bytes=(p_png + p_jpg) // 2)
    assert fname.endswith(".jpg"), fname
    assert len(open(os.path.join(str(tmp_path / "assets"), fname), "rb").read()) <= (p_png + p_jpg) // 2


def test_save_scaled_rejects_when_still_too_big(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(assets_mod, "ASSETS_DIR", str(tmp_path / "assets"))
    src = Image.effect_noise((512, 512), 120).convert("RGB")
    with pytest.raises(assets_mod.AssetError, match="超过"):
        assets_mod.save_scaled(assets_mod._encode(src, "PNG"), max_bytes=2048)


def test_save_scaled_rejects_non_image(tmp_path, monkeypatch):
    monkeypatch.setattr(assets_mod, "ASSETS_DIR", str(tmp_path / "assets"))
    with pytest.raises(assets_mod.AssetError):
        assets_mod.save_scaled(b"not-an-image-at-all")


# ============================== 3. 编辑器 REST ==============================
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.plugins.qq_bridge.pic import editor as editor_mod
    from src.plugins.qq_bridge.pic import theme as theme_mod

    monkeypatch.setattr(editor_mod, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(theme_mod, "_ACTIVE_THEME_PATH", str(tmp_path / "a.json"))
    monkeypatch.setattr(assets_mod, "ASSETS_DIR", str(tmp_path / "assets"))
    app = FastAPI()
    editor_mod.register(app)
    return TestClient(app)


def test_route_list_returns_items(client, monkeypatch):
    async def fake(**kw):
        return {"ok": True, "items": [{"task_id": "t1", "prompt": "p", "kind": "生图",
                                       "image_url": "/file?name=a.png",
                                       "thumb_url": "http://127.0.0.1:7788/file?name=a.png",
                                       "created_at": "2026-09-01 00:00:00", "type": "t2i_local"}],
                "total": 1, "page": 1, "page_size": 24, "total_pages": 1,
                "scanned": 1, "capped": False, "error": ""}
    monkeypatch.setattr(gallery_mod, "fetch_images", fake)
    r = client.get("/api/pic/project-images?type=t2i_local&sort=asc&keyword=猫&page=2")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] and len(j["items"]) == 1 and j["total"] == 1


def test_route_list_reports_main_project_unreachable(client, monkeypatch):
    async def fake(**kw):
        return {"ok": False, "items": [], "total": 0, "page": 1, "page_size": 24,
                "total_pages": 0, "scanned": 0, "capped": False, "error": "ConnectError"}
    monkeypatch.setattr(gallery_mod, "fetch_images", fake)
    r = client.get("/api/pic/project-images")
    j = r.json()
    assert j["ok"] is False and "ConnectError" in j["error"] and j["items"] == []


def test_route_import_downloads_and_stores(client, monkeypatch):
    class _Resp:
        status_code = 200
        content = _png()

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    r = client.post("/api/pic/project-images/import",
                    json={"url": "http://127.0.0.1:7788/file?name=abc.png"})
    j = r.json()
    assert j["ok"] is True, j
    assert j["filename"].endswith(".png")
    assert assets_mod.exists(j["filename"])


def test_route_import_rejects_foreign_host(client, monkeypatch):
    called = {"n": 0}

    class _Client:
        def __init__(self, *a, **kw): called["n"] += 1
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return None

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    r = client.post("/api/pic/project-images/import",
                    json={"url": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 400 and "主项目" in r.json()["error"]
    assert called["n"] == 0, "SSRF 闸门必须在发请求之前拦住"


def test_route_import_rejects_non_image_payload(client, monkeypatch):
    class _Resp:
        status_code = 200
        content = b"definitely not a picture"

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    r = client.post("/api/pic/project-images/import",
                    json={"url": "http://127.0.0.1:7788/file?name=x"})
    assert r.status_code == 400 and "不支持的图片格式" in r.json()["error"]


def test_route_import_rejects_missing_url(client):
    r = client.post("/api/pic/project-images/import", json={})
    assert r.status_code == 400
