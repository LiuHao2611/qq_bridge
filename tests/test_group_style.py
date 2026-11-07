# -*- coding: utf-8 -*-
"""M5 群风格单元测试（仅测 group_style 包；LLM 用假 client，可裸跑）。"""
import sys
sys.path.insert(0, "src/plugins")

import pytest

from qq_bridge.group_style import (
    GroupStyleError, GroupStyleManager, GroupStyleStore, LearnError,
    StyleLearner,
)
from qq_bridge.group_style.learn import _validate
from qq_bridge.group_style.manager import _merge


def _tmp_store(tmp_path):
    return GroupStyleStore(db_path=str(tmp_path / "gs.db"))


def test_store_save_load_roundtrip(tmp_path):
    st = _tmp_store(tmp_path)
    st.save("g:1", atmosphere="沙雕群", memes=["awsl", "典中典"], taboo=["别提工资"])
    s = st.load("g:1")
    assert s["atmosphere"] == "沙雕群"
    assert s["memes"] == ["awsl", "典中典"]
    assert s["taboo"] == ["别提工资"]
    assert s["sample_count"] == 0


def test_store_empty_scope(tmp_path):
    st = _tmp_store(tmp_path)
    s = st.load("g:none")
    assert s["memes"] == [] and s["taboo"] == [] and s["atmosphere"] == ""


def test_store_add_remove_meme_idempotent(tmp_path):
    st = _tmp_store(tmp_path)
    assert st.add_item("g:1", "memes", "梗A") == 1
    assert st.add_item("g:1", "memes", "梗A") == 1  # 去重幂等
    assert st.add_item("g:1", "memes", "梗B") == 2
    assert st.remove_item("g:1", "memes", "梗A") == 1
    assert st.remove_item("g:1", "memes", "梗A") == 1  # 删不存在仍幂等


def test_store_add_item_bad_field(tmp_path):
    st = _tmp_store(tmp_path)
    with pytest.raises(GroupStyleError):
        st.add_item("g:1", "bogus", "x")


def test_store_reset(tmp_path):
    st = _tmp_store(tmp_path)
    st.save("g:1", memes=["a"])
    st.reset("g:1")
    assert st.load("g:1")["memes"] == []


def test_validate_strips_and_caps():
    ok = _validate({"atmosphere": "  轻松  ", "memes": ["a", "b", 3, "", "a"],
                    "taboo": ["x"]})
    assert ok["atmosphere"] == "轻松"
    assert ok["memes"] == ["a", "b", "3"]  # 数字转字符串、去空、去重
    assert ok["taboo"] == ["x"]


def test_validate_rejects_nondict():
    with pytest.raises(LearnError):
        _validate(["not", "dict"])


def test_validate_rejects_bad_atmosphere_type():
    with pytest.raises(LearnError):
        _validate({"atmosphere": 123, "memes": [], "taboo": []})


def test_merge_caps_and_dedup():
    out = _merge(["a", "b"], ["b", "c", "d"], 3)
    assert out == ["a", "b", "c"]  # 截断到 3


class _FakeClient:
    def __init__(self, content):
        self._content = content
        self.calls = 0

    async def chat(self, messages, temperature=None):
        self.calls += 1
        return {"role": "assistant", "content": self._content}


@pytest.mark.anyio
async def test_learner_parse_json(tmp_path):
    client = _FakeClient(
        '```json\n{"atmosphere":"技术讨论","memes":["666","yyds"],"taboo":["广告"]}\n```')
    learner = StyleLearner(client=client)
    style = await learner.learn(["u:说 a", "v:说 b"])
    assert style["atmosphere"] == "技术讨论"
    assert style["memes"] == ["666", "yyds"]
    assert style["taboo"] == ["广告"]
    assert client.calls == 1


@pytest.mark.anyio
async def test_learner_bad_json_raises():
    client = _FakeClient("这不是json")
    learner = StyleLearner(client=client)
    with pytest.raises(LearnError):
        await learner.learn(["a", "b"])


@pytest.mark.anyio
async def test_learner_empty_messages_raises():
    client = _FakeClient("{}")
    learner = StyleLearner(client=client)
    with pytest.raises(LearnError):
        await learner.learn([])


@pytest.mark.anyio
async def test_manager_learn_merges(tmp_path):
    client = _FakeClient(
        '{"atmosphere":"轻松","memes":["新梗1","新梗2"],"taboo":["新忌讳"]}')
    mgr = GroupStyleManager(store=_tmp_store(tmp_path),
                            learner=StyleLearner(client=client))
    # 先手动留一条旧梗
    mgr.add_meme("g:1", "旧梗")
    res = await mgr.learn("g:1", ["x" for _ in range(12)])
    s = mgr.view("g:1")
    assert "旧梗" in s["memes"]          # 旧梗保留
    assert "新梗1" in s["memes"]          # 新梗追加
    assert s["atmosphere"] == "轻松"      # 新氛围覆盖
    assert res["total_memes"] == 3


def test_manager_prompt_none_when_empty(tmp_path):
    mgr = GroupStyleManager(store=_tmp_store(tmp_path))
    assert mgr.prompt("g:1") is None


def test_manager_prompt_text_when_has_content(tmp_path):
    mgr = GroupStyleManager(store=_tmp_store(tmp_path))
    mgr.add_meme("g:1", "梗A")
    text = mgr.prompt("g:1")
    assert text is not None
    assert "梗A" in text
    assert "【群风格" in text


def test_manager_reset(tmp_path):
    mgr = GroupStyleManager(store=_tmp_store(tmp_path))
    mgr.add_meme("g:1", "梗A")
    mgr.reset("g:1")
    assert mgr.prompt("g:1") is None
