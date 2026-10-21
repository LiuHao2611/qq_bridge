# -*- coding: utf-8 -*-
"""M6 文件互传 单元测试（2026-10-20）。

覆盖：
  A. pc.py 纯函数：_safe_name 清洗 / inbox_save(防覆盖+超限) / inbox_write_bytes /
     file_read_info（存在/目录/缺失）；
  B. dispatcher：_extract 解析 file 段、normalize 注入 QQEvent.files；
  C. __init__ 路由：/pc get 白名单拒绝·超限拒绝·发文件（mock send_file）；
  D. __init__ 接收：_pc_receive_files（url 直下 / get_file 回退 / 超限跳过 / 空段）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, "src")
import plugins.qq_bridge as qb
from plugins.qq_bridge import pc as pc_mod
from plugins.qq_bridge.dispatcher import _extract, normalize

_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASS.append(name)
        print("  ✓ %s" % name)
    else:
        _FAIL.append(name)
        print("  ✗ %s  %s" % (name, detail))


async def test_pc3() -> None:
    print("[M6 文件互传] A. pc.py 纯函数")
    with tempfile.TemporaryDirectory() as d:
        # A1 _safe_name 清洗
        check("文件名去分隔符", pc_mod._safe_name("a/b\\c..txt") == "a_b_c_.txt"
              or ".." not in pc_mod._safe_name("../../etc/passwd"))
        check("文件名空→file", pc_mod._safe_name("   ") == "file")
        check("文件名限长", len(pc_mod._safe_name("x" * 500)) <= 200)

        # A2 inbox_save 正常 + 防覆盖
        src = os.path.join(d, "src.bin")
        with open(src, "wb") as fh:
            fh.write(b"hello")
        res = pc_mod.inbox_save(src, d, "hello.bin", 10 * 1024 * 1024)
        check("inbox_save 成功", res["ok"] and os.path.isfile(res["path"]), str(res))
        res2 = pc_mod.inbox_save(src, d, "hello.bin", 10 * 1024 * 1024)
        check("inbox_save 防覆盖", res2["ok"] and res2["name"] == "hello(1).bin", str(res2))
        # A3 超限拒绝
        res3 = pc_mod.inbox_save(src, d, "big.bin", 1)
        check("inbox_save 超限拒", (not res3["ok"]) and "超过" in res3["error"], str(res3))
        # A4 源不存在
        res4 = pc_mod.inbox_save(os.path.join(d, "nope"), d, "x.bin", 10 * 1024 * 1024)
        check("inbox_save 源缺失", (not res4["ok"]) and "不存在" in res4["error"], str(res4))

        # A5 inbox_write_bytes
        rb = pc_mod.inbox_write_bytes(b"abc", d, "a.txt", 10 * 1024 * 1024)
        check("write_bytes 成功", rb["ok"] and open(rb["path"], "rb").read() == b"abc", str(rb))
        rb2 = pc_mod.inbox_write_bytes(b"x" * 999, d, "a.txt", 10)
        check("write_bytes 超限拒", (not rb2["ok"]) and "超过" in rb2["error"], str(rb2))

    # A6 file_read_info
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
        tf.write(b"data"); p = tf.name
    fi = pc_mod.file_read_info(p)
    check("file_read_info 存在", fi["exists"] and fi["is_file"] and fi["size"] == 4, str(fi))
    os.remove(p)
    fi2 = pc_mod.file_read_info(p)
    check("file_read_info 缺失", (not fi2["exists"]) and "不存在" in fi2["error"], str(fi2))
    fi3 = pc_mod.file_read_info(os.path.dirname(p))
    check("file_read_info 目录", fi3.get("exists") and (not fi3.get("is_file")), str(fi3))

    print("[M6 文件互传] B. dispatcher 解析")
    # 用 duck typing 假段
    class Seg:
        def __init__(self, t, data):
            self.type = t
            self.data = data
    segs = [Seg("text", {"text": "hi"}),
            Seg("file", {"file": "fid123", "name": "note.txt", "size": 1024, "url": ""})]
    text, at, imgs, atqq, files, voices = _extract(segs)
    check("extract 文本", text == "hi")
    check("extract file 段", len(files) == 1 and files[0]["file"] == "fid123"
          and files[0]["name"] == "note.txt" and files[0]["size"] == 1024, str(files))

    # normalize 注入 files（private 消息）
    class Ev:
        message_type = "private"
        user_id = 10001
        group_id = 0
        self_id = "bot"
        raw_message = ""
        message = segs
        original_message = segs
    qb.Config.BOT_OWNER_QQ = (10001,)
    nev = normalize(Ev(), self_id="bot", trace_id="t")
    check("normalize 注入 files", nev is not None and len(nev.files) == 1
          and nev.files[0]["name"] == "note.txt", str(nev.files if nev else None))

    print("[M6 文件互传] C. /pc get 路由")
    OWNER = 10001
    qb.Config.BOT_OWNER_QQ = (OWNER,)
    qb.Config.PC_ENABLE = True
    qb.Config.PC_DISK_ROOTS = d if False else r"D:\AI"  # 用真实白名单值
    captured: list = []
    async def _cap(ctx, sess, text, *, template="text", title=""):
        captured.append((title, text)); return None
    orig_reply = qb._reply
    qb._reply = _cap
    # mock send_file 不真发（必须是 async，因生产代码 await 它）
    orig_send = qb.send_file
    async def _fake_send_file(ctx, p, at_user=False):  # type: ignore
        return None
    qb.send_file = _fake_send_file

    # 白名单拒绝：路径不在 D:\AI
    captured.clear()
    await qb._handle_pc_command(qb._Ev(OWNER, "/pc get C:\\Windows\\notepad.exe") if hasattr(qb, "_Ev") else _make_ev(OWNER, "/pc get C:\\Windows\\notepad.exe"), "t")
    check("get 白名单拒绝", captured and "不在白名单" in captured[0][1], str(captured))

    # 不存在文件
    captured.clear()
    await _run_get(OWNER, "/pc get D:\\AI\\nope.txt")
    check("get 不存在", captured and "读不到" in captured[0][1], str(captured))

    # 正常发送（造一个真实文件在 D:\AI 下不行，改用 mock _pc_disk_allowed + 真实临时文件）
    # 直接测 _pc_send_file：mock 白名单通过 + 真实临时文件
    tmpf = _tmp_file()
    orig_allowed = qb._pc_disk_allowed
    qb._pc_disk_allowed = lambda p: True  # 测试内放行
    captured.clear()
    await qb._pc_send_file(qb._ctx(_make_ev(OWNER, "x")), None, tmpf)
    check("get 正常发送", captured and "正在把" in captured[0][1], str(captured))
    # 超限
    qb.Config.PC_FILE_MAX_MB = 0  # 0 → max(1,0)=1MB；临时文件很小应放行，这里改用大文件逻辑：造 2MB 文件
    big = _tmp_file(size=2 * 1024 * 1024)
    captured.clear()
    await qb._pc_send_file(qb._ctx(_make_ev(OWNER, "x")), None, big)
    check("get 超限拒", captured and "超过" in captured[0][1], str(captured))
    qb.Config.PC_FILE_MAX_MB = 100
    qb._pc_disk_allowed = orig_allowed
    os.remove(tmpf); os.remove(big)

    # 空参数
    captured.clear()
    await qb._pc_send_file(qb._ctx(_make_ev(OWNER, "x")), None, "")
    check("get 空参数提示", captured and "用法" in captured[0][1], str(captured))

    qb.send_file = orig_send

    print("[M6 文件互传] D. _pc_receive_files")
    # 收件箱改为临时目录（不污染项目 data/pc_inbox）
    _dinbox = tempfile.mkdtemp()
    _orig_inbox = qb._pc_inbox_dir
    qb._pc_inbox_dir = lambda: _dinbox  # type: ignore
    # url 直下成功（mock httpx）
    class _Resp:
        status_code = 200
        content = b"filedata"
    class _Cli:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, u): return _Resp()
    qb.httpx.AsyncClient = lambda **k: _Cli()
    ev = _make_ev(OWNER, "")
    ev.files = [{"file": "", "name": "recv.txt", "size": 8, "url": "http://x/recv.txt"}]
    captured.clear()
    await qb._pc_receive_files(ev, "t")
    check("接收 url 成功", any("收到" in c[1] for c in captured), str(captured))
    # 超限跳过（段内声明的 size 超阈值 → 下载前即跳过，不浪费流量）
    ev2 = _make_ev(OWNER, "")
    ev2.files = [{"file": "", "name": "big.bin", "size": 2 * 1024 * 1024, "url": "http://x/b"}]
    qb.Config.PC_FILE_MAX_MB = 0  # → 实际阈值 max(1,0)=1MB
    captured.clear()
    await qb._pc_receive_files(ev2, "t")
    check("接收 超限跳过", any("超过" in c[1] for c in captured), str(captured))
    qb.Config.PC_FILE_MAX_MB = 100
    # get_file 回退（mock connector）
    class _Bot:
        async def call_api(self, action, **p):
            return {"file": _tmp_file(), "name": "viaid.txt"}
    qb.connector.first_bot = lambda: _Bot()  # type: ignore
    ev3 = _make_ev(OWNER, "")
    ev3.files = [{"file": "fid1", "name": "viaid.txt", "size": 5, "url": ""}]
    captured.clear()
    await qb._pc_receive_files(ev3, "t")
    check("接收 get_file 回退", any("收到" in c[1] for c in captured), str(captured))
    # 空段
    ev4 = _make_ev(OWNER, "")
    ev4.files = []
    captured.clear()
    await qb._pc_receive_files(ev4, "t")
    check("接收 空段提示", captured and "没收到文件段" in captured[0][1], str(captured))

    qb._reply = orig_reply
    qb.httpx.AsyncClient = _orig_async_client  # type: ignore
    qb._pc_inbox_dir = _orig_inbox  # type: ignore
    try:
        os.rmdir(_dinbox)
    except OSError:
        pass


# ---- 测试辅助 ----
def _make_ev(user_id, raw_text, is_group=False):
    class _Ev:
        def __init__(self):
            self.user_id = user_id
            self.raw_text = raw_text
            self.is_group = is_group
            self.group_id = 0 if not is_group else 123
    return _Ev()


async def _run_get(owner, raw):
    await qb._handle_pc_command(_make_ev(owner, raw), "t")


_tmp_paths: list = []


def _tmp_file(size: int = 16) -> str:
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"x" * size)
    _tmp_paths.append(path)
    return path


_orig_async_client = None


if __name__ == "__main__":
    import atexit
    def _cleanup():
        for p in _tmp_paths:
            try: os.remove(p)
            except OSError: pass
    atexit.register(_cleanup)
    _orig_async_client = qb.httpx.AsyncClient  # 备份，测试内替换
    asyncio.run(test_pc3())
    print("\n==== 结果：%d 通过 / %d 失败 ====" % (len(_PASS), len(_FAIL)))
    if _FAIL:
        print("失败项：", _FAIL)
        sys.exit(1)
    print("ALL_OK")
