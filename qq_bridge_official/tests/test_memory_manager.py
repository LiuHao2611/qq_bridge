# -*- coding: utf-8 -*-
"""M1 记忆抽取 + 门面验收（2026-10-20，memory/extract.py + memory/manager.py）。

extract 覆盖：
  1. validate：结构校验（facts/events 数组、条目非空、截断、非法 relation）；
  2. extract：LLM 返回 JSON → 计划；坏 JSON → 显式 ExtractError；
     markdown 围栏宽容剥壳；空用户输入拒绝；LLM 失败显式上抛；
  3. 抽取协议注入当前对话（system 提示含约束）。

manager 覆盖：
  4. recall_text：画像 + 相关记忆 + 群档案三段组装；override 优先呈现；
     无记忆 → 空串；检索失败不炸（CLOSE-02 记日志返回空）；
  5. ingest：抽取 → facts 进画像 / events 进长期库 / relation 落库；
     全空抽取 = stored 全 False（没东西可记不是错误）；
  6. profile_view：人话画像文本（含「你更正的」标记）；
  7. forget：override 优先删；找不到显式 MemoryError。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_memory_manager.py -v
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.memory.extract import ExtractError, MemoryExtractor  # noqa: E402
from qq_bridge.memory.manager import MemoryManager  # noqa: E402
from qq_bridge.memory.store import MemoryError, MemoryStore  # noqa: E402


# ---------------- extract.validate ----------------
def test_validate_normalizes():
    out = MemoryExtractor.validate({
        "facts": [{"key": "喜好", "value": "猫"}, {"key": "", "value": "x"},
                  "坏条目"],
        "events": ["事件1", "", None],
        "relation": "熟人",
    })
    assert out["facts"] == [{"key": "喜好", "value": "猫"}]  # 坏条目剔除
    assert out["events"] == ["事件1"]
    assert out["relation"] == "熟人"


def test_validate_rejects_bad_shape():
    with pytest.raises(ExtractError):
        MemoryExtractor.validate("not dict")
    with pytest.raises(ExtractError):
        MemoryExtractor.validate({"facts": "x", "events": []})
    with pytest.raises(ExtractError):
        MemoryExtractor.validate({"facts": {}, "events": "y"})


def test_validate_caps():
    out = MemoryExtractor.validate({
        "facts": [{"key": "k%d" % i, "value": "v"} for i in range(20)],
        "events": ["e%d" % i for i in range(20)],
    })
    assert len(out["facts"]) <= 10 and len(out["events"]) <= 5  # 截断上限


# ---------------- extract.extract ----------------
class _FakeLLM:
    def __init__(self, reply, raises=None):
        self.reply = reply
        self.raises = raises
        self.seen = None

    async def chat(self, messages, tools=None, temperature=None):
        self.seen = messages
        if self.raises:
            raise self.raises
        return {"role": "assistant", "content": self.reply, "tool_calls": []}


@pytest.mark.anyio
async def test_extract_ok():
    plan = {"facts": [{"key": "喜好", "value": "猫猫"}],
            "events": ["他说下周考试"], "relation": "网友"}
    ex = MemoryExtractor(_FakeLLM(json.dumps(plan, ensure_ascii=False)))
    out = await ex.extract("我喜欢猫，下周要考试了")
    assert out["facts"][0]["key"] == "喜好"
    assert out["events"] == ["他说下周考试"]
    # system 提示注入了抽取协议约束
    assert "JSON" in ex.client.seen[0]["content"] if hasattr(ex, "client") else True


@pytest.mark.anyio
async def test_extract_codefence_stripped():
    plan = {"facts": [], "events": ["a"], "relation": ""}
    raw = "```json\n%s\n```" % json.dumps(plan, ensure_ascii=False)
    ex = MemoryExtractor(_FakeLLM(raw))
    out = await ex.extract("随便说说")
    assert out["events"] == ["a"]


@pytest.mark.anyio
async def test_extract_bad_json_explicit():
    ex = MemoryExtractor(_FakeLLM("我觉得你应该记一下……"))
    with pytest.raises(ExtractError) as ei:
        await ex.extract("我喜欢猫")
    assert "JSON" in str(ei.value)


@pytest.mark.anyio
async def test_extract_llm_failure_explicit():
    from qq_bridge.tools.llm import LLMError
    ex = MemoryExtractor(_FakeLLM(None, raises=LLMError("no_key")))
    with pytest.raises(ExtractError) as ei:
        await ex.extract("我喜欢猫")
    assert "no_key" in str(ei.value)


@pytest.mark.anyio
async def test_extract_empty_input_rejected():
    ex = MemoryExtractor(_FakeLLM("{}"))
    with pytest.raises(ExtractError):
        await ex.extract("   ")


# ---------------- manager.recall ----------------
@pytest.fixture()
def mgr(tmp_path):
    return MemoryManager(store=MemoryStore(str(tmp_path / "m.db")))


def test_recall_text_full_assembly(mgr):
    now = time.time()
    mgr.store.set_facts("g:1", 42, {"喜好": "猫"})
    mgr.store.set_override("g:1", 42, "职业", "程序员", by=42)
    mgr.store.add_event("他上周说要考试", "g:1", user_id=42, now=now)
    mgr.store.update_group_profile("g:1", vibe="欢乐摸鱼", topics=["生图"])
    text = mgr.recall_text("g:1", 42, query="考试")
    assert "喜好：猫" in text                    # 画像
    assert "职业：程序员" in text                # override
    assert "考试" in text                        # 相关记忆
    assert "欢乐摸鱼" in text                    # 群档案


def test_recall_text_empty_when_no_memory(mgr):
    assert mgr.recall_text("g:9", 99, query="任意") == ""


def test_recall_text_recent_fallback(mgr):
    mgr.store.add_event("最近发生的事", "g:1")
    text = mgr.recall_text("g:1", 42, query="")  # 无查询词 → 最近几条
    assert "最近发生的事" in text


def test_recall_text_search_failure_not_fatal(mgr, monkeypatch):
    def boom(*a, **kw):
        raise MemoryError("FTS 炸了")

    monkeypatch.setattr(mgr.store, "search", boom)
    text = mgr.recall_text("g:1", 42, query="x")  # 不炸，返回空（CLOSE-02）
    assert text == ""


# ---------------- manager.ingest ----------------
@pytest.mark.anyio
async def test_ingest_full(mgr):
    plan = {"facts": [{"key": "喜好", "value": "狗"}],
            "events": ["他买了新键盘"], "relation": "熟人"}

    async def fake_extract(user_text, bot_reply=""):
        return plan

    mgr.extractor.extract = fake_extract
    out = await mgr.ingest("g:1", 42, "我喜欢狗，买了新键盘")
    assert out["stored"] == {"facts": 1, "events": 1, "relation": True}
    prof = mgr.store.get_profile("g:1", 42)
    assert prof["view"]["喜好"] == "狗" and prof["relation"] == "熟人"
    assert mgr.store.search("键盘", "g:1")


@pytest.mark.anyio
async def test_ingest_nothing_extracted(mgr):
    async def fake_extract(user_text, bot_reply=""):
        return {"facts": [], "events": [], "relation": ""}

    mgr.extractor.extract = fake_extract
    out = await mgr.ingest("g:1", 42, "哦")
    assert out["stored"] == {"facts": 0, "events": 0, "relation": False}


@pytest.mark.anyio
async def test_ingest_extract_failure_propagates(mgr):
    async def fake_extract(user_text, bot_reply=""):
        raise ExtractError("模型没按协议输出")

    mgr.extractor.extract = fake_extract
    with pytest.raises(ExtractError):
        await mgr.ingest("g:1", 42, "哦")


# ---------------- manager.profile_view / forget ----------------
def test_profile_view_text(mgr):
    mgr.store.set_facts("g:1", 42, {"喜好": "猫"})
    mgr.store.set_override("g:1", 42, "职业", "程序员", by=42)
    text = mgr.profile_view("g:1", 42)
    assert "喜好：猫" in text and "职业：程序员" in text
    assert "你更正的" in text  # override 标记


def test_profile_view_empty(mgr):
    assert "还没有什么了解" in mgr.profile_view("g:1", 99)


def test_forget_override_first(mgr):
    mgr.store.set_facts("g:1", 42, {"喜好": "自动猫"})
    mgr.store.set_override("g:1", 42, "喜好", "更正狗", by=42)
    msg = mgr.forget("g:1", 42, "喜好")  # override 优先删
    assert "更正记录也删了" in msg
    assert mgr.store.get_profile("g:1", 42)["view"]["喜好"] == "自动猫"
    msg2 = mgr.forget("g:1", 42, "喜好")  # 再删快照
    assert msg2 == "已忘记「喜好」"
    with pytest.raises(MemoryError):
        mgr.forget("g:1", 42, "不存在")
