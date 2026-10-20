# -*- coding: utf-8 -*-
"""download.btselect —— BT 磁力/种子智能选文件（M7，2026-10-20）。

范式搬运（trust but verify，2026-10-20 实读 DownloadBot Aria2.go）：
  DownloadBot 的 `selectBigestFile` / `selectBigFiles`——磁力任务元数据
  到位后（tellStatus 见 bittorrent.info.name），getFiles 拿文件列表：
    - 只选最大文件（观影场景：一部电影 + 一堆广告/样品）；
    - 按大小筛：选 > 平均体积×0.8 的文件（过滤小广告文件）。
  选中后 changeOption `select-file`（**1 起的序号串**）再 unpause。

aria2 侧时序（manager 编排，本模块只做纯函数决策）：
  addUri(磁力) → 元数据等待 → pause → select-file → unpause。

纪律：纯函数零依赖，可裸单测；选不出文件时返回空串并让调用方显式告知，
不静默全选（全选 = 把广告文件也下了，正是本功能要避免的）。
"""
from __future__ import annotations

from ..config import get_logger, jlog

log = get_logger()

#: 选文件策略（Config.DL_BT_SELECT 可配）
SELECT_LARGEST = "largest"   # 只选最大文件
SELECT_SMART = "smart"       # 选 ≥ 平均体积×0.8 的文件
SELECT_ALL = "all"           # 全选（用户显式选择，非默认）
_SELECTS = (SELECT_LARGEST, SELECT_SMART, SELECT_ALL)


def select_file_indices(files: list[dict], strategy: str = SELECT_LARGEST) -> list[int]:
    """按策略挑文件，返回 **0 起的下标列表**（空 = 没什么可挑）。

    files: aria2 getFiles 返回的列表，每项含 path/length/selected。
    strategy: largest | smart | all。
    """
    sizes: list[float] = []
    for f in files:
        try:
            sizes.append(float(f.get("length") or 0))
        except (TypeError, ValueError):
            sizes.append(0.0)
    if not sizes:
        return []
    if strategy == SELECT_ALL:
        return list(range(len(files)))
    if max(sizes) <= 0:
        # 全是 0 字节（元数据未给出长度）→ 无法智能选，显式空让调用方告知
        return []
    if strategy == SELECT_SMART:
        avg = sum(sizes) / len(sizes)
        threshold = avg * 0.8
        picked = [i for i, s in enumerate(sizes) if s >= threshold]
        # 极端情况（全都小于阈值）→ 回落最大文件，绝不返回空导致任务卡死
        if not picked:
            picked = [sizes.index(max(sizes))]
        return picked
    # 默认 largest：并列最大全选
    top = max(sizes)
    return [i for i, s in enumerate(sizes) if s == top]


def to_aria2_select_option(indices: list[int]) -> str:
    """0 起下标 → aria2 `select-file` 值（1 起的序号串，逗号分隔）。"""
    return ",".join(str(i + 1) for i in sorted(indices))


def apply_strategy(files: list[dict], strategy: str = SELECT_LARGEST) -> tuple[str, list[int]]:
    """一步到位：返回 (select-file 值, 选中的下标)。空串 = 没得选。"""
    idx = select_file_indices(files, strategy)
    return to_aria2_select_option(idx), idx


def describe_selection(files: list[dict], indices: list[int]) -> str:
    """人类可懂的选文件说明（日志/通知用）。"""
    if not indices:
        return "（没有选中任何文件）"
    lines = []
    total = 0.0
    for i in indices:
        if 0 <= i < len(files):
            f = files[i]
            name = str(f.get("path") or "").replace("\\", "/").split("/")[-1] or "?"
            try:
                size = float(f.get("length") or 0)
            except (TypeError, ValueError):
                size = 0.0
            total += size
            lines.append("· %s（%.1f MB）" % (name, size / 1048576))
    return "\n".join(lines) + ("\n共 %.1f MB" % (total / 1048576))
