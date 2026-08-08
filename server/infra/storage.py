# -*- coding: utf-8 -*-
"""Layer 0 · Storage Service（执行手册 §2.1 Layer 0: Storage Service）。

抽象：对象存储（中间帧 / 成片）、关系型数据库（任务元数据）、分布式缓存（任务状态）。
本地实现（LocalStorageService）：中间产物落本地 outputs 目录；按手册 §7.2 声明 TTL
与清理者，遵守 OPS-02（持久数据必须声明 TTL 与清理者）。

TTL 约定（手册 §7 + OPS-02 平台既定生命周期，本地对齐）：
  分镜中间帧 / 尾帧参考图   保留 7 天（对应手册 7 天）
  失败任务残留               保留 7 天
  成片                       长期保留（用户产物）
"""
import os
import time
import threading
from abc import ABC, abstractmethod
from typing import Dict, List, Optional


INTERMEDIATE_TTL_DAYS = 7
FAILED_TTL_DAYS = 7


class StorageService(ABC):
    """存储服务抽象。"""

    @abstractmethod
    def put_intermediate(self, name: str, data: bytes, ttl_days: int = INTERMEDIATE_TTL_DAYS) -> str:
        raise NotImplementedError

    @abstractmethod
    def put_final(self, name: str, data: bytes) -> str:
        raise NotImplementedError

    @abstractmethod
    def get(self, name: str) -> Optional[bytes]:
        raise NotImplementedError

    @abstractmethod
    def cleanup_expired(self) -> int:
        """OPS-03：幂等、分批、带锁的过期清理；返回清理数量。"""
        raise NotImplementedError


class LocalStorageService(StorageService):
    """本地实现：单盘目录 + 元数据 sidecar 记录 TTL。"""

    def __init__(self, root: Optional[str] = None):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._root = root or os.path.join(base, "outputs")
        self._meta_dir = os.path.join(self._root, ".meta")
        os.makedirs(self._root, exist_ok=True)
        os.makedirs(self._meta_dir, exist_ok=True)
        self._lock = threading.Lock()

    def _meta_path(self, name: str) -> str:
        return os.path.join(self._meta_dir, name.replace("/", "_") + ".ttl.json")

    def _write_meta(self, name: str, expires_at: float):
        try:
            with open(self._meta_path(name), "w", encoding="utf-8") as f:
                f.write('{"name":%s,"expires_at":%d}' % (__import__("json").dumps(name), int(expires_at)))
        except Exception:
            pass

    def put_intermediate(self, name: str, data: bytes, ttl_days: int = INTERMEDIATE_TTL_DAYS) -> str:
        path = os.path.join(self._root, name)
        with open(path, "wb") as f:
            f.write(data)
        self._write_meta(name, time.time() + ttl_days * 86400)
        return path

    def put_final(self, name: str, data: bytes) -> str:
        path = os.path.join(self._root, name)
        with open(path, "wb") as f:
            f.write(data)
        # 成片无 TTL（长期保留），仅记元信息不含过期
        return path

    def get(self, name: str) -> Optional[bytes]:
        path = os.path.join(self._root, name)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def cleanup_expired(self) -> int:
        """OPS-03：幂等 + 分批(≤500) + 带锁 的过期中间产物清理。"""
        now = time.time()
        removed = 0
        with self._lock:
            try:
                names = os.listdir(self._meta_dir)
            except Exception:
                return 0
            batch = names[:500]
            for fn in batch:
                try:
                    with open(os.path.join(self._meta_dir, fn), "r", encoding="utf-8") as f:
                        meta = __import__("json").load(f)
                    if meta.get("expires_at", 0) <= now:
                        name = meta.get("name")
                        p = os.path.join(self._root, name)
                        if os.path.exists(p):
                            os.remove(p)
                            removed += 1
                        os.remove(os.path.join(self._meta_dir, fn))
                except Exception:
                    continue
        return removed


default_storage = LocalStorageService()
