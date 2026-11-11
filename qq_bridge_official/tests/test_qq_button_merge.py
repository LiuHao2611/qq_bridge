# -*- coding: utf-8 -*-
"""QQ 官方「图片+按钮合一」+「点按钮报错」回归守门（2026-11-11，prove-it）。

## 两个真实生产问题（都只在真实群点击/出图时暴露）

问题②「点按钮报错——请求第三方失败」：
  * 日志：``official_interaction_ack_failed api=put_interaction
    error=TypeError: Bot.put_interaction() missing 1 required keyword-only
    argument: 'code'``。适配器 1.7.2 的 ``put_interaction(interaction_id, code)``
    的 ``code`` 必填，旧 ACK 只传 interaction_id → 回执没发 → 客户端报错。
  * 次生：按钮回复走主动消息被拒 ``40034105 主动消息失败，无权限``。
    INTERACTION_CREATE 没原消息 msg_id，官方给的是 ``event_id``，应走被动回复。

问题①「图片要和按钮一起发」：
  * 官方 msg_type 互斥：图片 7、markdown+按钮 2，一条消息无法同时带
    file_image + keyboard。唯一官方途径：图片经素材管理 API 上传换
    ``qqbot-media://`` 引用，内嵌进 markdown content，再与 keyboard 同段（2）。
  * 素材三步：POST prepare → PUT presigned_url → POST complete；100 张上限
    用 LRU 淘汰（DELETE /v2/media-images/{id}/delete，误用 POST 会 404/405）；
    失败必须抛 MaterialUploadError 供上层降级回两条，不静默吞。

## 守门设计：行为为主、AST 为辅、三类真实错误注入

* 行为（真执行，非源码字符串）：
  ① 从源文件 AST 抽出真 ``_ack_interaction``，注入严格签名假 bot（``code``
     无默认值），await 它 —— 漏 ``code`` 会当场 TypeError（复现根因），带
     ``code=0`` 才通过；
  ③ 真跑 ``media_asset.ensure_material``（monkeypatch ``_new_request`` /
     ``image_size`` 注入假依赖，不碰 sys.modules）：验三步顺序、字段、md5
     缓存命中不重传、LRU 淘汰发出的是 **DELETE**、complete 失败向上传播。
* AST 结构（只用于无法脱离 nonebot 框架执行的 ``send()`` 接线）：
  ② ``send()`` 认 reply_ref.event_id、向 send_to_* 透传 event_id、空串
     ``or None`` 归一；``qq_interaction`` 写 event_id 进 reply_ref；
  ④ ``_build_merged_card_segment`` markdown+keyboard 同段、用 ensure_material；
  ⑤ ``send()`` 有合并分支 + 失败降级两条分支。
* 容器专项（宿主 skip，真 nonebot payload）：
  ⑥ 真 ``Bot._extract_send_message`` 断 ``content=None / markdown.content 含
     qqbot-media / keyboard 在 / msg_type=2``。

跑法：
  python tests/test_qq_button_merge.py                        # 宿主纯标准库，⑥自动 skip
  docker cp tests/test_qq_button_merge.py <ctr>:/app/qk_test/ && \
    docker exec <ctr> python /app/qk_test/test_qq_button_merge.py   # 容器含⑥
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CH = _REPO / "src/plugins/qq_bridge/channel"
_OFFICIAL = _CH / "official.py"
_QQ_INTERACTION = _CH / "qq_interaction.py"
_MEDIA_ASSET = _CH / "media_asset.py"


def _load(path: Path, name: str):
    """干净直载单文件到唯一模块名（不注入/覆盖任何依赖到 sys.modules）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _extract_fn(src: str, fn_name: str, ns_extra: dict):
    """从源码 AST 抽出单个函数并 exec 到隔离命名空间（真函数体，非复刻逻辑）。

    用于那些顶层依赖 relative-import、无法整文件直载、但函数体本身可在注入
    少量依赖后真实执行的函数（如 _ack_interaction）。
    """
    from typing import Any

    tree = ast.parse(src)
    node = next(
        (n for n in tree.body
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name),
        None,
    )
    assert node is not None, "源码找不到函数 %s" % fn_name
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {
        "Any": Any,
        "jlog": lambda evt, **kw: {"event": evt, **kw},
        "log": _NullLog(),
    }
    ns.update(ns_extra)
    exec(compile(module, "<%s>" % fn_name, "exec"), ns)  # noqa: S102 — 仅测自己的源码
    return ns[fn_name]


class _NullLog:
    def __getattr__(self, _name):
        return lambda *_a, **_kw: None


# media_asset 顶部 `from ..config ...` 直载会 ImportError → 已内置 logging 兜底，
# 无需注入 config 桩；nonebot.drivers.Request 由 _new_request 收口，测试直接替换它。
ma = _load(_MEDIA_ASSET, "qq_bridge_media_asset_testmerge")


# ---------------------------------------------------------------------------
# ① ACK 必带 code=0（问题②直接根因）——真执行 + 严格签名假 bot
# ---------------------------------------------------------------------------
class _StrictAckBot:
    """put_interaction 的 code 无默认值（与适配器 1.7.2 一致：必填 kw-only）。"""

    def __init__(self):
        self.calls = []

    async def put_interaction(self, *, interaction_id, code):  # noqa: D — code 必须必填
        self.calls.append({"interaction_id": interaction_id, "code": code})


class _Ev:
    id = "INTERACTION_CREATE:abc-123"


def _ack_from(src: str):
    return _extract_fn(src, "_ack_interaction", {})


def test_ack_calls_put_interaction_with_code_zero():
    bot = _StrictAckBot()
    asyncio.run(_ack_from(_QQ_INTERACTION.read_text(encoding="utf-8"))(bot, _Ev()))
    assert len(bot.calls) == 1, "ACK 应恰好调用一次 put_interaction"
    assert bot.calls[0]["interaction_id"] == "INTERACTION_CREATE:abc-123"
    assert bot.calls[0]["code"] == 0, (
        "put_interaction 必须带 code=0（回执成功）；漏 code 会重现 TypeError → "
        "客户端「请求第三方失败」"
    )


def _guard_ack_code(src: str):
    """结构守门：_ack_interaction 内 put_interaction 调用必须含 code=0 关键字。"""
    fn = _extract_fn(src, "_ack_interaction", {})
    bot = _StrictAckBot()
    try:
        asyncio.run(fn(bot, _Ev()))
    except TypeError as e:
        raise AssertionError(
            "ACK 守门：put_interaction 漏 code（会重现 "
            "TypeError: missing keyword-only 'code' → 请求第三方失败）：%s" % e
        )
    assert bot.calls and bot.calls[0].get("code") == 0, \
        "ACK 守门：put_interaction 必须带 code=0"


# ---------------------------------------------------------------------------
# ② event_id 被动回复（问题②次生 40034105）——AST 结构守门
# ---------------------------------------------------------------------------
def _send_calls_blob(src: str) -> str:
    tree = ast.parse(src)
    send = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "send"), None)
    assert send is not None, "找不到 OfficialChannel.send"
    return "\n".join(ast.unparse(n) for n in ast.walk(send) if isinstance(n, ast.Call))


def _send_fn(src: str):
    tree = ast.parse(src)
    send = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "send"), None)
    assert send is not None, "找不到 OfficialChannel.send"
    return send


def _guard_event_id_send(src: str):
    """AST 结构守门（不看引号/空格，避免死抠细节误报）。

    ① send_to_group/send_to_c2c 调用必须带 ``event_id=passive_event_id`` 关键字；
    ② msg_id/event_id 空串必须 ``or None`` 归一（官方二选一）；
    ③ passive_ok 必须读 reply_ref 的 ``event_id``。
    """
    send = _send_fn(src)
    calls = [n for n in ast.walk(send) if isinstance(n, ast.Call)]

    # ① event_id 透传
    sends = [c for c in calls
             if getattr(c.func, "attr", "") in ("send_to_group", "send_to_c2c")]
    assert sends, "send() 未调用 send_to_group/send_to_c2c"
    ok_kw = any(
        kw.arg == "event_id" and isinstance(kw.value, ast.Name)
        and kw.value.id == "passive_event_id"
        for c in sends for kw in c.keywords
    )
    assert ok_kw, \
        "send_to_* 未透传 event_id=passive_event_id（按钮回复仍走主动消息 40034105）"

    # ② `xxx or None` 空串归一（BoolOp Or 的一个分支是 None）
    def _has_or_none(var: str) -> bool:
        for n in ast.walk(send):
            if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or):
                vals = [ast.unparse(v) for v in n.values]
                if var in vals and "None" in vals:
                    return True
        return False

    assert _has_or_none("ref_id"), \
        "msg_id 空串未 `or None` 归一（官方二选一，exclude_none 不排空串）"
    assert _has_or_none("ref_event_id"), \
        "event_id 空串未 `or None` 归一（官方二选一，exclude_none 不排空串）"

    # ③ 读 reply_ref.event_id
    has_get = any(
        isinstance(n.func, ast.Attribute) and n.func.attr == "get"
        and n.args and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == "event_id"
        for n in calls
    )
    assert has_get, \
        "send() 未读 reply_ref.event_id（passive_ok 认不到按钮回复凭据）"


def test_send_passes_event_id_as_passive_reply():
    _guard_event_id_send(_OFFICIAL.read_text(encoding="utf-8"))


def _guard_event_id_written(src: str):
    assert 'getattr(event, "event_id"' in src, "未取交互事件 event_id"
    assert '"event_id": event_id' in src, "未把 event_id 写进 reply_ref"
    assert '"ts": time.time()' in src, \
        "reply_ref.ts 未设当前时间（passive_ok 会 False → 走主动消息 40034105）"


def test_interaction_writes_event_id_to_reply_ref():
    _guard_event_id_written(_QQ_INTERACTION.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# ③ 素材 API 三步 + md5 缓存 + LRU DELETE ——真执行（注入假依赖，不碰 sys.modules）
# ---------------------------------------------------------------------------
class _FakeURL:
    def __init__(self, path=""):
        self.path = path

    def joinpath(self, *parts):
        return _FakeURL(self.path + "/" + "/".join(str(p) for p in parts))

    def __str__(self):
        return "https://api.sgroup.qq.com" + self.path


class _FakeAdapter:
    def __init__(self):
        self.api_base = _FakeURL()

    def get_api_base(self):
        return self.api_base


class _FakeRequest:
    def __init__(self, method, url, **kw):
        self.method = method
        self.url = str(url)
        self.kw = kw


class _FakeMaterialBot:
    """按 (method,url) 分派 prepare/complete/delete，记录顺序；PUT 单独记。"""

    def __init__(self, complete_ok: bool = True):
        self.adapter = _FakeAdapter()
        self.order: list = []
        self.complete_ok = complete_ok
        self.deleted: list = []
        self.failed_delete: bool = False

    async def _request(self, req):
        if req.method == "POST" and "media-images/prepare" in req.url:
            self.order.append("prepare")
            return {"upload_id": "up_1", "presigned_url": "https://cos.example/put"}
        if req.method == "POST" and "media-images/complete" in req.url:
            self.order.append("complete")
            if not self.complete_ok:
                raise RuntimeError("complete rejected")
            return {"url": "qqbot-media://image/v1/TOKEN123", "image_id": "img_1"}
        if req.method == "DELETE" and "/delete" in req.url:
            self.order.append("delete")
            self.deleted.append((req.method, req.url))
            return {}
        raise AssertionError(
            "unexpected request: %s %s（DELETE 误用 POST/其它方法会走到这里）"
            % (req.method, req.url))

    async def put_upload_part(self, *, presigned_url: str, data: bytes):
        self.order.append("put")
        assert data, "put 收到空字节"


def _wire_fake(bot):
    """用上下文管理替换 _new_request / image_size，退出即恢复，零残留。"""
    class _Wire:
        def __enter__(self):
            self._req = ma._new_request
            self._size = ma.image_size
            ma._new_request = lambda method, url, **kw: _FakeRequest(method, url, **kw)
            ma.image_size = lambda data: (6, 4)  # 解除 PIL 依赖，宿主/容器同跑
            ma.reset_store()
            return bot

        def __exit__(self, *_):
            ma._new_request = self._req
            ma.image_size = self._size
            ma.reset_store()

    return _Wire()


def test_material_api_sequence_and_cache():
    data = b"\x89PNG\r\n\x1a\n-fake-bytes-for-sequence-test-"
    with _wire_fake(_FakeMaterialBot()) as bot:
        r1 = asyncio.run(ma.ensure_material(bot, data))
        assert bot.order == ["prepare", "put", "complete"], \
            "素材 API 顺序错（应 prepare→PUT→complete）：%s" % bot.order
        assert r1["url"] == "qqbot-media://image/v1/TOKEN123"
        assert (r1["width"], r1["height"]) == (6, 4), "图片尺寸未写入素材引用"
        assert r1["cached"] is False
        before = list(bot.order)
        r2 = asyncio.run(ma.ensure_material(bot, data))  # 同一份字节
        assert r2.get("cached") is True, "md5 缓存未命中"
        assert bot.order == before, "缓存命中却重复上传：%s" % bot.order


def test_lru_eviction_uses_delete_method():
    """LRU 超容量淘汰必须发 DELETE /.../delete（误用 POST 会 404/405 释放不掉配额）。"""
    bot = _FakeMaterialBot()
    with _wire_fake(bot):
        ma._store = ma.MaterialStore(max_items=1)
        try:
            asyncio.run(ma.ensure_material(bot, b"png-alpha-unique"))
            asyncio.run(ma.ensure_material(bot, b"png-beta-unique"))
            assert bot.deleted, "超容量未触发 LRU 淘汰"
            method, url = bot.deleted[0]
            assert method == "DELETE", "LRU 淘汰误用 %s（官方要求 DELETE）" % method
            assert "/media-images/" in url and url.endswith("/delete"), \
                "LRU 淘汰 URL 错：%s" % url
        finally:
            ma._store = ma.MaterialStore()


def test_injection_material_failure_propagates():
    """complete 被拒必须抛 MaterialUploadError（供上层降级），不能静默返假 url。"""
    bot = _FakeMaterialBot(complete_ok=False)
    with _wire_fake(bot):
        try:
            asyncio.run(ma.ensure_material(bot, b"png-fail-unique"))
            raise AssertionError("素材守门未报错：complete 失败却没抛 MaterialUploadError")
        except ma.MaterialUploadError:
            pass


# ---------------------------------------------------------------------------
# ④ markdown 内嵌语法 + 合一接线 ——行为 + AST
# ---------------------------------------------------------------------------
def test_markdown_image_syntax():
    s = ma.markdown_image("qqbot-media://image/v1/T", 800, 1019)
    assert s == "![card #800px #1019px](qqbot-media://image/v1/T)", \
        "内嵌语法不符官方 ![alt #宽px #高px](url)：%s" % s


def _guard_merge_wiring(src: str):
    tree = ast.parse(src)
    fn = next(
        (n for n in tree.body
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_build_merged_card_segment"),
        None,
    )
    assert fn is not None, "找不到 _build_merged_card_segment"
    blob = "\n".join(ast.unparse(n) for n in ast.walk(fn))
    assert "ensure_material" in blob, "合一未用素材上传（图片进不了 markdown）"
    assert "markdown_image" in blob, "合一未用 markdown 图片语法"
    calls = [ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Call)]
    assert any(c.startswith("_QQSegment.markdown") for c in calls), \
        "合一消息缺 markdown 段（图片+按钮要同源一条）"
    assert any(c.startswith("_QQSegment.keyboard") for c in calls), \
        "合一消息缺 keyboard 段"


def test_merge_segment_wiring():
    _guard_merge_wiring(_OFFICIAL.read_text(encoding="utf-8"))


def test_send_has_merge_and_fallback_branches():
    blob = _send_calls_blob(_OFFICIAL.read_text(encoding="utf-8"))
    src = _OFFICIAL.read_text(encoding="utf-8")
    assert "_build_merged_card_segment" in blob, "send() 未接图片+按钮合一"
    assert "official_merge_fallback_two_messages" in src, \
        "send() 缺素材失败降级回两条的分支（会静默吞错/丢图）"
    assert '"merged_card"' in src, "合并段未登记 merged_card kind"


def test_merge_segment_has_markdown_and_keyboard_same_message():
    """容器真 nonebot：断最终 payload 是 markdown(含图)+keyboard 同段 msg_type=2。"""
    try:
        from nonebot.adapters.qq import Bot as QQBot
        from nonebot.adapters.qq import Message as QQMessage
        from nonebot.adapters.qq import MessageSegment as QQSegment
    except ImportError:
        print("[skip] nonebot 不在宿主，真 payload 项在容器内跑")
        return
    inv_path = _CH / "command_inventory.py"
    kb_path = _CH / "qq_keyboard.py"
    for p in (inv_path, kb_path):
        if str(p.parent) not in sys.path:
            sys.path.insert(0, str(p.parent))
    if "command_inventory" not in sys.modules:
        _load(inv_path, "command_inventory")
    kbmod = _load(kb_path, "qq_keyboard_testmerge")

    kb_model, _ = kbmod.make_keyboard("赏金")
    seg = QQMessage()
    seg += QQSegment.markdown(ma.markdown_image("qqbot-media://image/v1/T", 800, 1019))
    seg += QQSegment.keyboard(kb_model)
    assert [s.type for s in seg] == ["markdown", "keyboard"], \
        "合一消息必须 markdown+keyboard 同段"
    kwargs = QQBot._extract_send_message(seg, escape_text=False)
    assert kwargs.get("content") is None, "传 markdown 后 content 必须全空"
    assert "qqbot-media://" in (kwargs["markdown"].content or ""), \
        "markdown.content 未内嵌 qqbot-media 图片引用"
    assert kwargs.get("keyboard") is not None, "合一消息缺按钮"


# ---------------------------------------------------------------------------
# ⑤ 三类真实错误注入：把这次犯过的错放回去，守门必须分别 FAIL（prove-it 强制）
# ---------------------------------------------------------------------------
def test_injection_guards():
    ack_src = _QQ_INTERACTION.read_text(encoding="utf-8")

    # 注入 A：ACK 漏 code（= 请求第三方失败根因，严格签名 bot 会 TypeError）
    bad_ack = ack_src.replace("interaction_id=iid, code=0", "interaction_id=iid", 1)
    assert bad_ack != ack_src, "注入 A 没找到锚点"
    try:
        _guard_ack_code(bad_ack)
        raise AssertionError("ACK 守门未报错（漏 code 没被拦 → 请求第三方失败会重现）")
    except AssertionError as e:
        assert "code" in str(e), "ACK 守门报错位置不对：%s" % e
    _guard_ack_code(ack_src)  # 恢复必须过

    # 注入 B：send() 不透传 event_id（= 按钮回复走主动消息 40034105）
    off_src = _OFFICIAL.read_text(encoding="utf-8")
    bad_send = off_src.replace("event_id=passive_event_id", "event_id=None")
    try:
        _guard_event_id_send(bad_send)
        raise AssertionError("event_id 守门未报错（不透传没被拦 → 40034105 会重现）")
    except AssertionError as e:
        assert "event_id" in str(e), "event_id 守门报错位置不对：%s" % e
    _guard_event_id_send(off_src)

    # 注入 C：LRU 淘汰把 DELETE 误写成 POST（官方会 404/405，配额释放不掉）
    ma_src = _MEDIA_ASSET.read_text(encoding="utf-8")

    def _guard_delete_method(msrc: str):
        tree = ast.parse(msrc)
        fn = next((n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                   and n.name == "_delete"), None)
        assert fn is not None, "找不到 _delete"
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_new_request":
                method = n.args[0] if n.args else None
                assert isinstance(method, ast.Constant) and method.value == "DELETE", \
                    "素材 LRU 淘汰必须用 DELETE（误用 POST 会 404/405 释放不掉配额）"
                return
        raise AssertionError("_delete 未找到 _new_request 调用")

    bad_ma = ma_src.replace('_new_request("DELETE"', '_new_request("POST"', 1)
    assert bad_ma != ma_src, "注入 C 没找到锚点"
    try:
        _guard_delete_method(bad_ma)
        raise AssertionError("DELETE 守门未报错（误用 POST 没被拦）")
    except AssertionError as e:
        assert "DELETE" in str(e), "DELETE 守门报错位置不对：%s" % e
    _guard_delete_method(ma_src)


if __name__ == "__main__":
    import traceback

    tests = [
        test_ack_calls_put_interaction_with_code_zero,
        test_send_passes_event_id_as_passive_reply,
        test_interaction_writes_event_id_to_reply_ref,
        test_material_api_sequence_and_cache,
        test_lru_eviction_uses_delete_method,
        test_injection_material_failure_propagates,
        test_markdown_image_syntax,
        test_merge_segment_wiring,
        test_send_has_merge_and_fallback_branches,
        test_merge_segment_has_markdown_and_keyboard_same_message,
        test_injection_guards,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print("PASS  %s" % t.__name__)
            passed += 1
        except Exception as e:
            print("FAIL  %s -> %s: %s" % (t.__name__, type(e).__name__, e))
            traceback.print_exc()
            failed += 1
    print("\n==== 结果: %d PASS / %d FAIL ====" % (passed, failed))
    sys.exit(1 if failed else 0)
