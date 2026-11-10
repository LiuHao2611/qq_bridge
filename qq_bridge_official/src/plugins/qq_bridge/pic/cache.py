# -*- coding: utf-8 -*-
"""pic/cache.py —— 渲染结果磁盘缓存（TTL + LRU + 惰性删除）。

为什么需要：菜单图/配额图内容稳定（同一主题+同一份数据），每次都重绘浪费 CPU；
而 `/status` 又可能在短时间内被反复问，缓存能显著降低重复渲染。

策略（2026 主流做法：TTL + LRU + 惰性删除三件套，自实现不引第三方）：
  * **Key** = sha256(模板名 | 主题名 | 参数 JSON)，参数顺序无关（sort_keys）；
  * **TTL**：条目超过 ttl 秒视为过期，访问时惰性删除（不额外起定时任务）；
  * **LRU**：文件数超过 max_files 时，按「最近访问时间」升序淘汰到上限；
  * **索引**：`index.json` 记录 {创建时间, 最近访问, 体积}。
    坑：Windows NTFS 的 atime 更新有延迟（默认 1h），**不能依赖文件系统 atime**，
    所以自己在索引里维护 last。

并发：本模块在 `asyncio.to_thread` 中被调用，可能有并发 → 用 RLock 保护索引。
失败容忍：索引损坏/文件缺失一律当作未命中，**绝不让缓存问题影响出图**（降级优先）。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIR = _ROOT / "data" / "pic_cache"
DEFAULT_TTL = 300          # 5 分钟
DEFAULT_MAX_FILES = 200    # 最多缓存 200 张
_PURGE_BATCH = 500         # OPS-03：分批上限


def make_key(template: str, theme: str, params: dict | None = None) -> str:
    """生成缓存 key（同参数同 key，参数顺序无关）。"""
    raw = "%s|%s|%s" % (
        template,
        theme,
        json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class PicCache:
    def __init__(
        self,
        directory: str | os.PathLike | None = None,
        ttl: int = DEFAULT_TTL,
        max_files: int = DEFAULT_MAX_FILES,
    ) -> None:
        self.dir = Path(directory or DEFAULT_DIR)
        self.ttl = int(ttl)
        self.max_files = max(1, int(max_files))
        self._lock = threading.RLock()
        self._index: dict[str, dict] = {}
        self._loaded = False

    # ---------------- 索引 ----------------
    @property
    def index_path(self) -> Path:
        return self.dir / "index.json"

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            if self.index_path.is_file():
                self._index = json.loads(self.index_path.read_text(encoding="utf-8"))
                if not isinstance(self._index, dict):
                    self._index = {}
        except Exception:
            self._index = {}  # 索引损坏：当作空缓存，不影响出图
        self._loaded = True

    def _flush(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._index, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.index_path)
        except Exception:
            pass  # 写索引失败不致命：下次会重建

    # ---------------- 读写 ----------------
    def get(self, key: str) -> bytes | None:
        """命中返回 PNG bytes；未命中/过期返回 None（并惰性清理）。"""
        with self._lock:
            self._ensure_loaded()
            item = self._index.get(key)
            if not item:
                return None
            now = time.time()
            if self.ttl > 0 and now - float(item.get("c", 0)) > self.ttl:
                self._drop(key)
                return None
            path = self.dir / str(item.get("f") or (key + ".png"))
            try:
                data = path.read_bytes()
            except Exception:
                self._drop(key)
                return None
            item["l"] = now
            self._flush()
            return data

    def put(self, key: str, data: bytes) -> None:
        """写入缓存；写后检查容量（超限按 LRU 淘汰）。"""
        if not data:
            return
        with self._lock:
            self._ensure_loaded()
            now = time.time()
            fname = key + ".png"
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                (self.dir / fname).write_bytes(data)
            except Exception:
                return  # 写盘失败：宁可不缓存，也不影响出图
            self._index[key] = {"f": fname, "c": now, "l": now, "s": len(data)}
            self._flush()
            if len(self._index) > self.max_files:
                self.purge()

    def purge(self) -> int:
        """惰性清理：删过期 + LRU 淘汰到上限。返回删除条目数。"""
        with self._lock:
            self._ensure_loaded()
            now = time.time()
            removed = 0

            # 1) 过期（OPS-03 分批）
            if self.ttl > 0:
                for key in list(self._index.keys())[:_PURGE_BATCH]:
                    if now - float(self._index[key].get("c", 0)) > self.ttl:
                        self._drop(key)
                        removed += 1

            # 2) LRU 淘汰
            if len(self._index) > self.max_files:
                ordered = sorted(self._index.items(), key=lambda kv: float(kv[1].get("l", 0)))
                for key, _ in ordered[: max(0, len(self._index) - self.max_files)]:
                    self._drop(key)
                    removed += 1

            if removed:
                self._flush()
            return removed

    def clear(self) -> int:
        """清空全部缓存。返回删除条目数。"""
        with self._lock:
            self._ensure_loaded()
            n = len(self._index)
            for key in list(self._index.keys()):
                self._drop(key)
            self._flush()
            return n

    def stats(self) -> dict:
        with self._lock:
            self._ensure_loaded()
            total = sum(int(v.get("s") or 0) for v in self._index.values())
            return {
                "count": len(self._index),
                "bytes": total,
                "dir": str(self.dir),
                "ttl": self.ttl,
                "max_files": self.max_files,
            }

    # ---------------- 内部 ----------------
    def _drop(self, key: str) -> None:
        item = self._index.pop(key, None)
        if not item:
            return
        try:
            (self.dir / str(item.get("f") or (key + ".png"))).unlink(missing_ok=True)
        except Exception:
            pass


#: 模块级默认缓存实例（业务层直接 `from ..pic.cache import pic_cache` 使用）
pic_cache = PicCache()
