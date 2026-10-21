# -*- coding: utf-8 -*-
"""group_style/manager —— 群风格门面（2026-10-21）。

组合 store + learn：prompt（注入 system 提示）/ learn（抽取并合并）/ add_meme /
remove_meme / add_taboo / remove_taboo / view / reset。

设计原则（与 memory/emotion 同款零主包依赖）：LLM 延迟注入；能力未启用或
LLM 不可用时 prompt 返回 None（让 caller 跳过注入，不是假注入）；learn 失败
显式上抛（禁止降级/兜底）。
"""
from __future__ import annotations

from .learn import LearnError, style_learner
from .store import GroupStyleError, GroupStyleStore, group_style_store

#: prompt 注入上限（防止风格过长挤占上下文）
_MAX_MEMES_IN_PROMPT = 6
_MAX_TABOO_IN_PROMPT = 4


class GroupStyleManager:
    """群风格门面。"""

    def __init__(self, store: GroupStyleStore | None = None,
                 learner=None) -> None:
        self.store = store if store is not None else group_style_store
        self.learner = learner if learner is not None else style_learner

    # ---------------- 注入 ----------------
    def prompt(self, scope: str) -> str | None:
        """生成注入 system 提示（群风格）。无风格/无内容 → None（caller 跳过）。"""
        try:
            s = self.store.load(scope)
        except GroupStyleError:
            return None  # 读库失败不阻断对话（CLOSE-02 记而不吞由调用方处理）
        if not s:
            return None
        memes = s.get("memes") or []
        taboo = s.get("taboo") or []
        atmo = (s.get("atmosphere") or "").strip()
        if not atmo and not memes and not taboo:
            return None
        lines = ["【群风格·说话方式】"]
        if atmo:
            lines.append("整体氛围：%s" % atmo)
        if memes:
            shown = memes[:_MAX_MEMES_IN_PROMPT]
            lines.append("常用梗/口头禅：%s" % "、".join(shown))
        if taboo:
            shown = taboo[:_MAX_TABOO_IN_PROMPT]
            lines.append("群内忌讳（尽量避开）：%s" % "、".join(shown))
        lines.append("（融入群风格自然说话，但别硬凹梗、别越界到冒犯）")
        return "\n".join(lines)

    # ---------------- 学习 ----------------
    async def learn(self, scope: str, messages: list,
                    max_meme: int = 8, max_taboo: int = 5) -> dict:
        """抽取并合并进 store。返回 {added_memes, added_taboo, atmosphere, total}。"""
        new = await self.learner.learn(messages, max_meme=max_meme,
                                       max_taboo=max_taboo)
        cur = self.store.load(scope)
        # 合并：原 memes/taboo 保留在前，新项追加在后、整体去重、截断上限
        merged_memes = _merge(cur.get("memes") or [], new["memes"], max_meme)
        merged_taboo = _merge(cur.get("taboo") or [], new["taboo"], max_taboo)
        # 氛围：新提取非空则覆盖（更贴近最新群况），否则保留旧
        atmosphere = new["atmosphere"] or cur.get("atmosphere") or ""
        self.store.save(
            scope,
            atmosphere=atmosphere,
            memes=merged_memes,
            taboo=merged_taboo,
            sample_count=cur["sample_count"] + len(messages),
        )
        return {
            "added_memes": len(merged_memes) - len(cur.get("memes") or []),
            "added_taboo": len(merged_taboo) - len(cur.get("taboo") or []),
            "atmosphere": atmosphere,
            "total_memes": len(merged_memes),
            "total_taboo": len(merged_taboo),
        }

    # ---------------- 手动增删 ----------------
    def add_meme(self, scope: str, text: str) -> int:
        return self.store.add_item(scope, "memes", text)

    def remove_meme(self, scope: str, text: str) -> int:
        return self.store.remove_item(scope, "memes", text)

    def add_taboo(self, scope: str, text: str) -> int:
        return self.store.add_item(scope, "taboo", text)

    def remove_taboo(self, scope: str, text: str) -> int:
        return self.store.remove_item(scope, "taboo", text)

    def reset(self, scope: str) -> None:
        self.store.reset(scope)

    def view(self, scope: str) -> dict:
        """返回供 /风格 看 展示的画像（含 updated_at 时间戳）。"""
        return self.store.load(scope)


def _merge(old: list, new: list, cap: int) -> list:
    out = list(old)
    for x in new:
        if x not in out:
            out.append(x)
    return out[:cap]


#: 模块级单例
group_style_manager = GroupStyleManager()
