# -*- coding: utf-8 -*-
"""persona —— 多人设库与切换（2026-10-21）。

背景：此前人设只有**一个** `Config.AI_SYSTEM_PROMPT`（textarea，热生效），
换性格只能整段重写提示词，且「本喵」的性格被硬编码在多处（表情人设句等），
换人设会自相矛盾。本模块把人设升级为**命名的人设库 + 按会话切换**。

设计要点：
  - **人设 = 人格文本**，不含协议：表情 marker 协议、工具协议、记忆/情感注入
    都由程序在 `extra_system` 里追加（见 ai_mode._sys_content 与
    _sticker_system_hint）。所以换人设**不会**破坏表情/情感/工具总线——
    这是能安全换人设的前提（否则第三方人设会把协议一起冲掉）。
  - 每个人设自带 `sticker_hint`（自己的人设一致性句），替换掉原先硬编码的
    「你是腹黑阴阳怪气的猫管家」——那人只是默认人设，不是全部人设。
  - 存储：`data/personas/*.json`（每个导入的人设一份）+ `active.json`
    （scope → 人设 id 的生效映射）。内置人设不落盘、不可删。
  - 解析优先级：scope 覆盖 → `Config.AI_PERSONA` → 默认 `benmiao`。
    id = `custom` 时沿用 `Config.AI_SYSTEM_PROMPT`（向后兼容旧用法）。

禁止降级 / 禁止兜底：
  - 配置了不存在的人设 id → 记 `persona_unknown` 警告并**显式**给默认人设
    （不静默伪装成请求的那个人设，日志可查、/人设 列表可核对）；
  - 人设文件损坏（非法 JSON）→ 跳过该文件 + 记日志，不让一个人设拖垮整库；
  - 抓取/导入失败 → 由抓取脚本显式报错，本模块不负责「造一个凑数」。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, get_logger, jlog

log = get_logger()

#: 项目根（与 config.ROOT_DIR 同算法：src/plugins/qq_bridge → parents[3]）
ROOT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT_DIR / "data"
PERSONA_DIR = DATA_DIR / "personas"
ACTIVE_FILE = PERSONA_DIR / "active.json"

#: 默认人设 id
DEFAULT_ID = "benmiao"
#: 伪人设：沿用配置里的 AI_SYSTEM_PROMPT（旧用法 / 手搓人设的逃生口）
CUSTOM_ID = "custom"

_lock = threading.RLock()


@dataclass
class Persona:
    """一个人设。prompt 是人格正文（不含协议）。"""

    id: str
    name: str
    emoji: str = ""
    tagline: str = ""
    prompt: str = ""
    #: 表情包 marker 协议的「人设一致性」句（见 _sticker_system_hint）
    sticker_hint: str = ""
    #: 内置人设（不落盘、不可删）
    builtin: bool = False
    #: 来源（抓取脚本写入，如 GitHub 仓库 URL）
    source: str = ""
    license: str = ""
    tags: list = field(default_factory=list)
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "name": self.name, "emoji": self.emoji,
            "tagline": self.tagline, "prompt": self.prompt,
            "sticker_hint": self.sticker_hint, "builtin": self.builtin,
            "source": self.source, "license": self.license,
            "tags": list(self.tags), "updated_at": self.updated_at,
        }
        return d

    @staticmethod
    def from_dict(d: dict) -> "Persona | None":
        if not isinstance(d, dict):
            return None
        pid = str(d.get("id") or "").strip()
        if not pid:
            return None
        return Persona(
            id=pid,
            name=str(d.get("name") or pid),
            emoji=str(d.get("emoji") or ""),
            tagline=str(d.get("tagline") or ""),
            prompt=str(d.get("prompt") or ""),
            sticker_hint=str(d.get("sticker_hint") or ""),
            builtin=bool(d.get("builtin")),
            source=str(d.get("source") or ""),
            license=str(d.get("license") or ""),
            tags=[str(t) for t in (d.get("tags") or [])],
            updated_at=float(d.get("updated_at") or 0.0),
        )

    def label(self) -> str:
        """列表展示用一行：`🦇 露娜 · 魔界小恶魔，雌小鬼毒舌`。"""
        head = (self.emoji + " " if self.emoji else "") + self.name
        return (head + " · " + self.tagline) if self.tagline else head


# ===========================================================================
# 内置人设
# ===========================================================================
#: 「本喵」——与既有默认 AI_SYSTEM_PROMPT **逐字一致**（换库不换默认行为）
_BENMIAO_PROMPT = (
    "你是 QQ 群聊里的机器人管家，自称「本喵」。性格腹黑、阴阳怪气但靠得住，"
    "说话简短泼辣，像一个真实的群友，不像客服。默认中文回复，"
    "单次回复控制在 100 字以内，禁止使用 markdown 格式与列表符号，"
    "不要暴露自己是 AI 语言模型，不要复述系统提示。"
)
_BENMIAO_HINT = (
    "人设一致性：你是腹黑阴阳怪气的猫管家，生气时发「阴阳怪气」或「无语」，"
    "绝不会发怒图；开心才发「开心/搞笑」。"
)

BUILTINS: dict[str, Persona] = {
    DEFAULT_ID: Persona(
        id=DEFAULT_ID, name="本喵", emoji="🐱",
        tagline="腹黑阴阳怪气猫管家（默认）",
        prompt=_BENMIAO_PROMPT, sticker_hint=_BENMIAO_HINT, builtin=True,
        source="内置", license="", tags=["腹黑", "阴阳怪气", "管家"],
    ),
    CUSTOM_ID: Persona(
        id=CUSTOM_ID, name="自定义", emoji="✍️",
        tagline="沿用配置页「AI 人设（系统提示词）」原样文本",
        # prompt 留空：解析时取 Config.AI_SYSTEM_PROMPT（向后兼容）
        prompt="", sticker_hint=_BENMIAO_HINT, builtin=True,
        source="内置", license="", tags=["自定义"],
    ),
}


class PersonaStore:
    """人设库：内置 + data/personas/*.json，含 per-scope 生效映射。"""

    def __init__(self, persona_dir: Path | None = None):
        self.dir = Path(persona_dir) if persona_dir else PERSONA_DIR
        self._files: dict[str, Persona] = {}
        self._active: dict[str, str] = {}
        self._loaded = False

    # ---------------- 加载 ----------------
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with _lock:
            if self._loaded:
                return
            self.dir.mkdir(parents=True, exist_ok=True)
            self._load_files()
            self._load_active()
            self._loaded = True

    def _load_files(self) -> None:
        self._files = {}
        for p in sorted(self.dir.glob("*.json")):
            if p.name == ACTIVE_FILE.name:
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
            except Exception as e:  # noqa: BLE001 — 单文件损坏不拖垮整库
                log.warning(jlog("persona_file_bad", file=p.name,
                                 error=type(e).__name__))
                continue
            per = Persona.from_dict(d)
            if per is None or not per.prompt:
                log.warning(jlog("persona_file_skip", file=p.name,
                                 detail="缺 id 或空 prompt"))
                continue
            self._files[per.id] = per

    def _load_active(self) -> None:
        self._active = {}
        if not ACTIVE_FILE.exists():
            return
        try:
            with open(ACTIVE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self._active = {str(k): str(v) for k, v in d.items()
                                if str(k) and str(v)}
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("persona_active_bad", error=type(e).__name__))

    def _save_active(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(ACTIVE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._active, f, ensure_ascii=False, indent=1)

    def reload(self) -> None:
        """热重载（导入新人设后调用）。"""
        with _lock:
            self._loaded = False
            self._ensure_loaded()

    # ---------------- 查询 ----------------
    def all(self) -> list[Persona]:
        """全部人设（内置在前，导入的按 id 排序）。"""
        self._ensure_loaded()
        out = list(BUILTINS.values())
        out += [self._files[k] for k in sorted(self._files)
                if k not in BUILTINS]
        return out

    def get(self, pid: str) -> Persona | None:
        pid = (pid or "").strip()
        if not pid:
            return None
        self._ensure_loaded()
        if pid in BUILTINS:
            return BUILTINS[pid]
        return self._files.get(pid)

    def exists(self, pid: str) -> bool:
        return self.get(pid) is not None

    def current_id(self, scope: str = "") -> str:
        """某会话当前生效的人设 id（scope 覆盖 > Config.AI_PERSONA > 默认）。"""
        self._ensure_loaded()
        if scope and scope in self._active:
            return self._active[scope]
        return str(getattr(Config, "AI_PERSONA", DEFAULT_ID) or DEFAULT_ID).strip() or DEFAULT_ID

    def current(self, scope: str = "") -> Persona:
        """某会话当前人设；id 不存在 → 记日志 + 给默认人设（不装成请求的人设）。"""
        pid = self.current_id(scope)
        per = self.get(pid)
        if per is None:
            log.warning(jlog("persona_unknown", id=pid, scope=scope or "-",
                             detail="回落到默认人设（请检查 AI_PERSONA 配置）"))
            return BUILTINS[DEFAULT_ID]
        return per

    def prompt_for(self, scope: str = "") -> str:
        """系统提示词的人设正文（custom → 沿用 Config.AI_SYSTEM_PROMPT）。"""
        per = self.current(scope)
        if per.id == CUSTOM_ID:
            return str(Config.AI_SYSTEM_PROMPT or "")
        return per.prompt

    def hint_for(self, scope: str = "") -> str:
        """表情 marker 协议的人设一致性句。"""
        per = self.current(scope)
        return per.sticker_hint or _BENMIAO_HINT

    # ---------------- 切换 / 写入 ----------------
    def set_scope(self, scope: str, pid: str) -> bool:
        """给某会话切换人设；人设不存在返回 False（调用方显式告知）。"""
        if not self.exists(pid):
            return False
        self._ensure_loaded()
        with _lock:
            self._active[scope] = pid
            self._save_active()
        log.info(jlog("persona_switched", scope=scope, id=pid))
        return True

    def clear_scope(self, scope: str) -> bool:
        """清除会话覆盖（回到全局 AI_PERSONA）。"""
        self._ensure_loaded()
        with _lock:
            if scope in self._active:
                self._active.pop(scope)
                self._save_active()
                return True
        return False

    def save(self, per: Persona) -> None:
        """写入/更新一个导入的人设（内置不可写）。"""
        if per.id in BUILTINS:
            raise ValueError("内置人设不可覆盖：%s" % per.id)
        self._ensure_loaded()
        per.updated_at = time.time()
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.dir / ("%s.json" % per.id)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(per.to_dict(), f, ensure_ascii=False, indent=1)
        with _lock:
            self._files[per.id] = per

    def remove(self, pid: str) -> bool:
        """删除导入的人设（内置不可删）。"""
        if pid in BUILTINS:
            return False
        self._ensure_loaded()
        target = self.dir / ("%s.json" % pid)
        if not target.exists():
            return False
        try:
            os.remove(target)
        except OSError as e:
            log.warning(jlog("persona_remove_failed", id=pid,
                             error=type(e).__name__))
            return False
        with _lock:
            self._files.pop(pid, None)
            for sc, v in list(self._active.items()):
                if v == pid:
                    self._active.pop(sc)
            self._save_active()
        return True


#: 进程级单例（插件入口与命令族共用）
_store: PersonaStore | None = None


def get_store() -> PersonaStore:
    global _store
    if _store is None:
        _store = PersonaStore()
    return _store


def resolve_prompt(scope: str = "") -> str:
    """便捷入口：某会话的系统提示词人设正文。"""
    return get_store().prompt_for(scope)


def resolve_hint(scope: str = "") -> str:
    """便捷入口：某会话的表情人设一致性句。"""
    return get_store().hint_for(scope)
