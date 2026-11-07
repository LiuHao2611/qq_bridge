# -*- coding: utf-8 -*-
"""M7 BT 智能选文件验收（2026-10-20，download/btselect.py）。

覆盖（范式 2026-10-20 实读 DownloadBot Aria2.go：selectBigestFile /
selectBigFiles）：
  1. largest：只选最大文件（并列最大全选）；
  2. smart：选 ≥ 平均体积×0.8 的文件；全低于阈值时回落最大文件；
  3. all：全选（用户显式选择）；
  4. 空列表 / 全 0 字节（元数据未给大小）→ 空下标（调用方显式告知，不静默全选）；
  5. to_aria2_select_option：0 起下标 → 1 起序号串（aria2 契约）；
  6. apply_strategy / describe_selection 组合行为。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_download_btselect.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.download.btselect import (  # noqa: E402
    SELECT_ALL, SELECT_LARGEST, SELECT_SMART, apply_strategy,
    describe_selection, select_file_indices, to_aria2_select_option)


def _files(*sizes_mb):
    return [{"path": "/dl/f%d.mkv" % i, "length": str(int(m * 1048576)),
             "selected": "false"} for i, m in enumerate(sizes_mb)]


# ---------------- 1. largest ----------------
def test_largest_picks_biggest():
    files = _files(1, 700, 5, 200)  # 700MB 是正片，其余是广告/样片
    idx = select_file_indices(files, SELECT_LARGEST)
    assert idx == [1]


def test_largest_ties_all_picked():
    files = _files(100, 100, 5)
    assert select_file_indices(files, SELECT_LARGEST) == [0, 1]


# ---------------- 2. smart ----------------
def test_smart_above_avg_80():
    files = _files(1, 2, 100, 90)   # 均值 48.25 → 阈值 38.6 → 选 100/90
    idx = select_file_indices(files, SELECT_SMART)
    assert idx == [2, 3]


def test_smart_falls_back_to_largest_when_all_below():
    files = _files(10, 11, 12)      # 均值 11 → 阈值 8.8 → 全过？不：
    # 阈值 8.8，全部 ≥8.8 → 全选。改构造真正全低于阈值的场景：
    files2 = _files(1, 1, 1000, 1000)  # 均值 500.5 → 阈值 400 → 选两个大的
    idx2 = select_file_indices(files2, SELECT_SMART)
    assert idx2 == [2, 3]
    # 构造「全都小于阈值」不可达（均值×0.8 必 ≤ 最大值）——
    # 但最大值恰低于均值×0.8 在数学上不可能，故回落分支是防御性死代码，
    # 用单文件场景验证不炸：
    single = _files(42)
    assert select_file_indices(single, SELECT_SMART) == [0]


# ---------------- 3. all ----------------
def test_all_selects_everything():
    files = _files(1, 2, 3)
    assert select_file_indices(files, SELECT_ALL) == [0, 1, 2]


# ---------------- 4. 空/零字节显式空 ----------------
def test_empty_files_returns_empty():
    assert select_file_indices([], SELECT_LARGEST) == []
    assert select_file_indices([], SELECT_SMART) == []
    assert select_file_indices([], SELECT_ALL) == []


def test_zero_length_returns_empty_for_smart():
    # 元数据未给大小（全 0 字节）→ 无法智能选，显式空让调用方告知
    files = [{"path": "/dl/a", "length": "0", "selected": "false"},
             {"path": "/dl/b", "length": "0", "selected": "false"}]
    assert select_file_indices(files, SELECT_LARGEST) == []
    assert select_file_indices(files, SELECT_SMART) == []


# ---------------- 5. aria2 序号转换 ----------------
def test_select_option_one_based():
    assert to_aria2_select_option([]) == ""
    assert to_aria2_select_option([0]) == "1"
    assert to_aria2_select_option([1, 3]) == "2,4"     # 排序 + 1 起
    assert to_aria2_select_option([3, 1, 0]) == "1,2,4"


# ---------------- 6. 组合 ----------------
def test_apply_strategy_combo():
    files = _files(1, 700, 5)
    opt, idx = apply_strategy(files, SELECT_LARGEST)
    assert opt == "2" and idx == [1]
    opt2, idx2 = apply_strategy(files, SELECT_ALL)
    assert opt2 == "1,2,3" and idx2 == [0, 1, 2]
    opt3, idx3 = apply_strategy([], SELECT_LARGEST)
    assert opt3 == "" and idx3 == []


def test_describe_selection():
    files = _files(1, 700, 5)
    text = describe_selection(files, [1])
    assert "f1.mkv" in text and "700.0 MB" in text
    assert describe_selection(files, []) == "（没有选中任何文件）"
