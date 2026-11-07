# -*- coding: utf-8 -*-
"""M7 下载编排验收（2026-10-20，download/manager.py）。

覆盖：
  1. classify 链接分类：http/ftp → http；磁力 → magnet；.torrent → torrent；
     视频平台域名 → video；无法识别 → 空串；
  2. submit：http → aria2.addUri；磁力 → paused 添加 + 登记等元数据；
     .torrent → addTorrent base64；视频 → ytdlp 同步下载即 complete；
     无法识别/提交失败 → 显式 DownloadError + 登记 failed；
  3. BT 智能选时序：元数据未到位（files 空）继续等；到位 → 选最大 →
     changeOption select-file → unpause → 移出等待；元数据超时显式失败；
  4. 完成轮询：tellStopped 新 complete → 补文件信息 + on_complete 回调；
     重复轮询不重复通知（gid 去重）；error 状态 → failed + errorMessage；
  5. cancel/pause/unpause：跨 scope 拒绝；终态拒绝；ytdlp 任务禁暂停；
  6. resolve_ref：编号/序号解析（序号按创建倒序）；
  7. 持久化：tmp+replace 原子写，重启恢复（running 任务重纳入等待）；
  8. _gc：终态记录滚动清理（≤100）。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_download_manager.py -v
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.download.aria2 import Aria2Client, Aria2Error  # noqa: E402
from qq_bridge.download.manager import (  # noqa: E402
    KIND_HTTP, KIND_MAGNET, KIND_TORRENT, KIND_VIDEO, DownloadError,
    DownloadManager, classify)
from qq_bridge.download.ytdlp import YtDlpDownloader, YtDlpError  # noqa: E402


class FakeAria2(Aria2Client):
    """内存假 aria2（不打网络，记录调用）。"""

    def __init__(self):
        super().__init__(url="http://fake/jsonrpc", secret="s")
        self.added: list[tuple[str, dict]] = []
        self.files: dict[str, list[dict]] = {}
        self.stopped: list[dict] = []
        self.paused: set[str] = set()
        self.removed: set[str] = set()
        self.options: dict[str, dict] = {}
        self._seq = 0

    async def get_version(self):
        return "1.37.0"

    async def add_uri(self, uris, options=None):
        self._seq += 1
        gid = "g%d" % self._seq
        self.added.append((uris[0] if uris else "", options or {}))
        return gid

    async def add_torrent(self, torrent, options=None):
        self._seq += 1
        gid = "t%d" % self._seq
        return gid

    async def get_files(self, gid):
        return list(self.files.get(gid, []))

    async def tell_status(self, gid, keys=None):
        return {"gid": gid, "status": "active"}

    async def change_option(self, gid, options):
        self.options[gid] = options

    async def pause(self, gid):
        self.paused.add(gid)

    async def unpause(self, gid):
        self.paused.discard(gid)

    async def remove(self, gid):
        self.removed.add(gid)

    async def tell_stopped(self, offset=0, num=50):
        return list(self.stopped)


class FakeYtDlp(YtDlpDownloader):
    def __init__(self, result=None, raises=None):
        super().__init__(dl_dir="D:/Downloads")
        self.result = result or {"title": "测试视频", "filepath": "D:/Downloads/测试视频.mp4",
                                 "duration": 60}
        self.raises = raises

    async def download(self, url):
        if self.raises:
            raise self.raises
        return dict(self.result)


@pytest.fixture()
def mgr(tmp_path):
    notified = []
    m = DownloadManager(store_path=tmp_path / "downloads.json",
                        aria2=FakeAria2(), ytdlp=FakeYtDlp(),
                        on_complete=lambda item: notified.append(item) or _noop())
    m._notified_out = notified  # 测试观察口
    return m


async def _noop():
    return None


# ---------------- 1. classify ----------------
def test_classify():
    assert classify("http://a.com/x.zip") == KIND_HTTP
    assert classify("https://a.com/x.zip?u=1") == KIND_HTTP
    assert classify("ftp://a.com/x") == KIND_HTTP
    assert classify("magnet:?xt=urn:btih:abc") == KIND_MAGNET
    assert classify("D:/dl/movie.torrent") == KIND_TORRENT
    assert classify("https://www.bilibili.com/video/BV1xx") == KIND_VIDEO
    assert classify("https://b23.tv/abc") == KIND_VIDEO
    assert classify("https://www.youtube.com/watch?v=x") == KIND_VIDEO
    assert classify("https://v.douyin.com/abc") == KIND_VIDEO
    assert classify("") == ""
    assert classify("随便一句话") == ""


# ---------------- 2. submit ----------------
@pytest.mark.anyio
async def test_submit_http(mgr):
    item = await mgr.submit("http://a.com/x.zip", "g:1", user_id=42, group_id=1)
    assert item["kind"] == KIND_HTTP and item["status"] == "running"
    assert item["gid"] == "g1"
    assert mgr.aria2.added[0][0] == "http://a.com/x.zip"


@pytest.mark.anyio
async def test_submit_magnet_paused_and_bt_waiting(mgr):
    item = await mgr.submit("magnet:?xt=urn:btih:abc", "g:1")
    assert item["kind"] == KIND_MAGNET and item["status"] == "running"
    # 磁力以 paused 添加（等元数据再选文件）
    assert mgr.aria2.added[0][1] == {"pause": "true"}
    assert item["gid"] in mgr._bt_waiting


@pytest.mark.anyio
async def test_submit_torrent_missing_file(mgr):
    with pytest.raises(DownloadError) as ei:
        await mgr.submit("D:/no/such.torrent", "g:1")
    assert "不存在" in str(ei.value)
    # 失败登记落盘（CLOSE-01 终态）
    failed = [d for d in mgr._items if d["status"] == "failed"]
    assert len(failed) == 1


@pytest.mark.anyio
async def test_submit_torrent_ok(tmp_path):
    tf = tmp_path / "a.torrent"
    tf.write_bytes(b"d4:name3:foo")
    m = DownloadManager(store_path=tmp_path / "d.json", aria2=FakeAria2(),
                        ytdlp=FakeYtDlp())
    item = await m.submit(str(tf), "g:1")
    assert item["kind"] == KIND_TORRENT and item["gid"] == "t1"
    assert item["gid"] in m._bt_waiting


@pytest.mark.anyio
async def test_submit_video_sync_complete(mgr):
    item = await mgr.submit("https://www.bilibili.com/video/BV1xx", "g:1")
    assert item["kind"] == KIND_VIDEO and item["status"] == "complete"
    assert item["name"] == "测试视频"
    # ytdlp 完成即通知（StreamVault Webhook 等价物）
    assert len(mgr._notified_out) == 1


@pytest.mark.anyio
async def test_submit_video_failure_explicit(tmp_path):
    m = DownloadManager(store_path=tmp_path / "d.json", aria2=FakeAria2(),
                        ytdlp=FakeYtDlp(raises=YtDlpError("解析失败：需登录")))
    with pytest.raises(YtDlpError) as ei:
        await m.submit("https://www.bilibili.com/video/BV1xx", "g:1")
    assert "需登录" in str(ei.value)
    assert any(d["status"] == "failed" for d in m._items)


@pytest.mark.anyio
async def test_submit_unknown_url(mgr):
    with pytest.raises(DownloadError):
        await mgr.submit("这不是链接", "g:1")


@pytest.mark.anyio
async def test_submit_aria2_down_explicit(tmp_path):
    class DownAria2(FakeAria2):
        async def add_uri(self, uris, options=None):
            raise Aria2Error("连不上 aria2 RPC（aria2c --enable-rpc …）")

    m = DownloadManager(store_path=tmp_path / "d.json", aria2=DownAria2(),
                        ytdlp=FakeYtDlp())
    with pytest.raises(Aria2Error) as ei:
        await m.submit("http://a.com/x.zip", "g:1")
    assert "--enable-rpc" in str(ei.value)  # 开启指引透出
    assert any(d["status"] == "failed" for d in m._items)


# ---------------- 3. BT 智能选时序 ----------------
@pytest.mark.anyio
async def test_bt_meta_wait_then_select(mgr):
    item = await mgr.submit("magnet:?xt=urn:btih:abc", "g:1")
    gid = item["gid"]
    # 元数据未到位：files 空 → 继续等（不失败不解除等待）
    await mgr._process_bt_waiting()
    assert gid in mgr._bt_waiting
    assert item["status"] == "running"
    # 元数据到位：700MB 正片 + 广告 → 选最大
    mgr.aria2.files[gid] = [
        {"path": "[bt]/ad.nfo", "length": "1024", "selected": "true"},
        {"path": "[bt]/movie.mkv", "length": str(700 * 1048576), "selected": "true"},
    ]
    await mgr._process_bt_waiting()
    assert gid not in mgr._bt_waiting
    assert mgr.aria2.options[gid] == {"select-file": "2"}  # 1 起序号
    assert gid not in mgr.aria2.paused                        # 已 unpause


@pytest.mark.anyio
async def test_bt_meta_timeout_fails(tmp_path):
    m = DownloadManager(store_path=tmp_path / "d.json", aria2=FakeAria2(),
                        ytdlp=FakeYtDlp())
    from qq_bridge.config import Config
    old = Config.DL_BT_META_TIMEOUT
    Config.DL_BT_META_TIMEOUT = 0.0   # 立即超时（测试注入）
    try:
        item = await m.submit("magnet:?xt=urn:btih:abc", "g:1")
        # 制造「早已创建」的事实（绕开同刻时间戳相等导致差值=0 的边界）
        item["created_at"] = time.time() - 10
        await m._process_bt_waiting()
    finally:
        Config.DL_BT_META_TIMEOUT = old
    assert item["status"] == "failed"
    assert "元数据" in item["error"]
    assert item["gid"] not in m._bt_waiting


# ---------------- 4. 完成轮询 ----------------
@pytest.mark.anyio
async def test_poll_completion_notifies_once(mgr):
    item = await mgr.submit("http://a.com/x.zip", "g:1")
    gid = item["gid"]
    # _finish_aria2_item 经 get_files(gid) 取文件详情（与 aria2 真实行为一致）
    mgr.aria2.files[gid] = [{"path": "/dl/x.zip", "selected": "true",
                             "length": "1048576"}]
    mgr.aria2.stopped = [{"gid": gid, "status": "complete",
                          "files": [{"path": "/dl/x.zip", "selected": "true",
                                     "length": "1048576"}]}]
    await mgr._poll_aria2_completions()
    assert item["status"] == "complete"
    assert item["name"] == "x.zip" and item["size"] == 1048576
    assert len(mgr._notified_out) == 1
    # 重复轮询不重复通知（gid 去重）
    await mgr._poll_aria2_completions()
    assert len(mgr._notified_out) == 1


@pytest.mark.anyio
async def test_poll_error_marks_failed(mgr):
    item = await mgr.submit("http://a.com/x.zip", "g:1")
    mgr.aria2.stopped = [{"gid": item["gid"], "status": "error",
                          "errorMessage": "file not found"}]
    await mgr._poll_aria2_completions()
    assert item["status"] == "failed"
    assert "file not found" in item["error"]


@pytest.mark.anyio
async def test_poll_unknown_gid_ignored(mgr):
    mgr.aria2.stopped = [{"gid": "outsider", "status": "complete", "files": []}]
    await mgr._poll_aria2_completions()
    assert mgr._notified_out == []


# ---------------- 5. 取消/暂停 ----------------
@pytest.mark.anyio
async def test_cancel_ok_and_scope_guard(mgr):
    item = await mgr.submit("http://a.com/x.zip", "g:1")
    with pytest.raises(DownloadError):
        await mgr.cancel(item["id"], "g:2")     # 跨 scope 拒绝
    await mgr.cancel(item["id"], "g:1")
    assert item["status"] == "canceled"
    assert item["gid"] in mgr.aria2.removed
    with pytest.raises(DownloadError):
        await mgr.cancel(item["id"], "g:1")     # 终态拒绝


@pytest.mark.anyio
async def test_ytdlp_task_pause_rejected(mgr):
    item = await mgr.submit("https://www.bilibili.com/video/BV1xx", "g:1")
    # ytdlp 同步任务（complete）→ 暂停显式拒绝
    with pytest.raises(DownloadError):
        await mgr.pause(item["id"], "g:1")


@pytest.mark.anyio
async def test_pause_resume_aria2(mgr):
    item = await mgr.submit("http://a.com/x.zip", "g:1")
    await mgr.pause(item["id"], "g:1")
    assert item["gid"] in mgr.aria2.paused
    await mgr.unpause(item["id"], "g:1")
    assert item["gid"] not in mgr.aria2.paused


# ---------------- 6. resolve_ref ----------------
@pytest.mark.anyio
async def test_resolve_ref_by_id_and_index(mgr):
    a = await mgr.submit("http://a.com/1.zip", "g:1")
    b = await mgr.submit("http://a.com/2.zip", "g:1")
    assert mgr.resolve_ref("g:1", a["id"])["id"] == a["id"]
    # 序号按创建倒序：b 是第 1 个
    assert mgr.resolve_ref("g:1", "1")["id"] == b["id"]
    assert mgr.resolve_ref("g:1", "2")["id"] == a["id"]
    assert mgr.resolve_ref("g:1", "999") is None
    assert mgr.resolve_ref("g:2", a["id"]) is None   # 跨 scope 不解析


# ---------------- 7. 持久化 ----------------
def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "downloads.json"
    m1 = DownloadManager(store_path=p, aria2=FakeAria2(), ytdlp=FakeYtDlp())
    m1._items.append(m1._norm({"id": "d1", "scope": "g:1", "url": "http://x",
                               "status": "running", "gid": "g9"}))
    m1._save()
    m2 = DownloadManager(store_path=p, aria2=FakeAria2(), ytdlp=FakeYtDlp())
    assert m2.get("d1")["status"] == "running"


@pytest.mark.anyio
async def test_restart_rearms_bt_waiting(tmp_path):
    p = tmp_path / "downloads.json"
    m1 = DownloadManager(store_path=p, aria2=FakeAria2(), ytdlp=FakeYtDlp())
    item = await m1.submit("magnet:?xt=urn:btih:abc", "g:1")
    # 模拟重启：新实例 + start → running 的 gid 重新纳入等待
    m2 = DownloadManager(store_path=p, aria2=FakeAria2(), ytdlp=FakeYtDlp())
    await m2.start()
    assert item["gid"] in m2._bt_waiting
    await m2.stop()


# ---------------- 8. GC ----------------
def test_gc_keeps_last_100(tmp_path):
    m = DownloadManager(store_path=tmp_path / "d.json", aria2=FakeAria2(),
                        ytdlp=FakeYtDlp())
    for i in range(120):
        m._items.append(m._norm({"id": "d%d" % i, "scope": "g:1",
                                 "status": "complete",
                                 "finished_at": float(i)}))
    m._gc()
    assert len(m._items) == 100
    assert m._items[-1]["id"] == "d119"   # 留最新的
