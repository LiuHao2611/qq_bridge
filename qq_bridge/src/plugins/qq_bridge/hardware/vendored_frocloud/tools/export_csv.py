# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2023-2026 x1shang
"""
frocloud · DIYPCmarket —— export_csv.py

把手工维护的 frocloud.xlsx 导出成 data/*.csv（长表）。

设计原则（对应 todo.txt）：
    * 两行表头解析：第一行年份、第二行月份；表头严格保持"月份"语义
    * 遇到非月份列（`涨幅`、`7月下旬`）必须显式处理，绝不静默丢弃
    * 派生列不进 CSV（`涨幅`、内存的 AVG/MAX/MIN 由脚本按原始价格重算，只做交叉验证）
    * utf-8-sig：Windows 上双击用 Excel 打开不乱码
    * 收尾打印"覆盖报告"：原表里有内容的单元格，要么进了 CSV，
      要么在"显式跳过"清单里 —— 不允许悄悄消失

读值方式：
    * 结构/公式判断 —— openpyxl 普通模式（拿到 '=...' 字符串）
    * 计算后的数值   —— 优先读原工作簿缓存；若这个 xlsx 是脚本产出的
      （没有 Excel 缓存），自动回退读「备份/拆分前原件」

用法：
    python tools/export_csv.py            # 写入 data/*.csv
    python tools/export_csv.py --check    # 只解析，不写文件
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CANDIDATES = [os.path.join(ROOT, "frocloud.xlsx"),
              os.path.join(ROOT, "frocloud-DIYPCmarket", "frocloud.xlsx")]
XLSX = next((p for p in CANDIDATES if os.path.exists(p)), CANDIDATES[0])
DATA = os.path.join(ROOT, "data")

ENCODING = "utf-8-sig"
MONTH_RE = re.compile(r"^\d{1,2}$")

PRICE_HEADER = ["date", "category", "model", "spec", "price_cny", "channel", "note"]
TIER_HEADER = ["tier", "vendor", "platform", "model", "cores", "threads",
               "score_cpumark", "score_single", "note"]


# ============================================================ 读值层
class Src:
    """一个工作簿的两个视图：结构（公式）与数值（缓存结果）

    合并单元格：openpyxl 只有在访问"锚点格"时才给值，其余格返回 None。
    合并格里的空白不代表"数据缺失"，而是"这个标签适用于整段"（例如
    「显卡品牌」的 B3:B6 = 一线）。所以这里统一把非锚点格解析回锚点 ——
    这是**物化标签**，不是拿上一行的值瞎填。真正的空（比如 Intel 区
    整段没写分级）解析后仍然是空。
    """

    def __init__(self, path):
        self.path = path
        self.f = openpyxl.load_workbook(path, data_only=False)
        self.v_path = path
        self.v = openpyxl.load_workbook(path, data_only=True)
        n = sum(1 for ws in self.v.worksheets for row in ws.iter_rows()
                for c in row if isinstance(c.value, (int, float)))
        if n == 0:
            bak = os.path.join(ROOT, "备份", "frocloud-拆分前备份-20261008.xlsx")
            if os.path.exists(bak):
                self.v_path = bak
                self.v = openpyxl.load_workbook(bak, data_only=True)
        self._anchor_cache = {}

    def _anchor(self, sheet, coord):
        """coord 落在某个合并区域内时，返回该区域的锚点坐标，否则返回 coord 本身"""
        key = (sheet, coord)
        if key in self._anchor_cache:
            return self._anchor_cache[key]
        target = coord
        try:
            cell = self.f[sheet][coord]
            for rng in self.f[sheet].merged_cells.ranges:
                if cell.coordinate in rng:
                    target = self.f[sheet].cell(row=rng.min_row, column=rng.min_col).coordinate
                    break
        except Exception:
            pass
        self._anchor_cache[key] = target
        return target

    def in_merge(self, sheet, coord):
        return self._anchor(sheet, coord) != coord

    def has(self, name):
        return name in self.f.sheetnames and name in self.v.sheetnames

    def val(self, sheet, coord):
        """取计算后的值；合并格解析回锚点"""
        return self.v[sheet][self._anchor(sheet, coord)].value

    def raw(self, sheet, coord):
        """取原始内容（公式字符串等）；合并格解析回锚点"""
        return self.f[sheet][self._anchor(sheet, coord)].value

    def is_formula(self, sheet, coord):
        r = self.raw(sheet, coord)
        return isinstance(r, str) and r.startswith("=")

    def max_row(self, sheet):
        return self.f[sheet].max_row

    def max_col(self, sheet):
        return self.f[sheet].max_column

    def txt(self, sheet, coord):
        return norm(self.raw(sheet, coord))

    def num(self, sheet, coord):
        v = self.val(sheet, coord)
        return v if is_num(v) else None


def norm(s):
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def fmt(v, nd=4):
    if v is None:
        return ""
    if is_num(v):
        f = float(v)
        if f.is_integer():
            return str(int(f))
        return str(round(f, nd))
    return norm(v)


def num_cell(src, sheet, coord):
    """该格是不是"原始数字"（非公式、非空、非文本）"""
    if src.is_formula(sheet, coord):
        return None
    v = src.val(sheet, coord)
    return v if is_num(v) else None


# ============================================================ 报告
class Report:
    def __init__(self):
        self.exported = {}
        self.skipped = {}
        self.deriveds = {}

    def mark(self, sheet, coord):
        self.exported[(sheet, coord)] = True

    def skip(self, sheet, coord, why):
        self.skipped.setdefault(sheet, []).append((coord, why))

    def derived(self, sheet, what, how):
        self.deriveds.setdefault(sheet, {})[what] = how

    def dump(self, wb):
        print()
        print("=" * 78)
        print("导出覆盖报告")
        print("=" * 78)
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            total = sum(1 for row in ws.iter_rows() for c in row if c.value not in (None, ""))
            exp = sum(1 for (s, _) in self.exported if s == sheet)
            sk = self.skipped.get(sheet, [])
            print("\n[%s] 非空单元格 %d → 进 CSV %d，显式跳过 %d" % (sheet, total, exp, len(sk)))
            for what, how in list(self.deriveds.get(sheet, {}).items())[:5]:
                print("    · %s：%s" % (what, how))
            if len(self.deriveds.get(sheet, {})) > 5:
                print("    · …… 另有 %d 条同类说明" % (len(self.deriveds[sheet]) - 5))
            seen = set()
            shown = 0
            for coord, why in sk:
                key = re.sub(r"\d+", "#", why)
                if key in seen:
                    continue
                seen.add(key)
                print("    · 跳过 %s：%s" % (coord, why))
                shown += 1
                if shown >= 6:
                    break
            if len(sk) > shown:
                print("    · …… 同类跳过共 %d 条（按原因归类）" % (len(sk) - shown))


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=ENCODING, newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writerow(header)
        w.writerows([["" if v is None else v for v in r] for r in rows])
    return len(rows)


# ============================================================ 两行表头
def two_row_header(src, sheet, special_ok=()):
    year_of, month_of, special = {}, {}, {}
    cur = None
    for c in range(1, src.max_col(sheet) + 1):
        y = src.raw(sheet, "%s1" % get_column_letter(c))
        if is_num(y):
            cur = int(y)
        year_of[c] = cur
        m = src.txt(sheet, "%s2" % get_column_letter(c))
        if MONTH_RE.match(m):
            month_of[c] = int(m)
        elif m:
            if m not in special_ok:
                raise SystemExit("%s：第 2 行出现未映射的表头列 %s=%r —— 请先在 export_csv.py 里显式登记"
                                 % (sheet, get_column_letter(c), m))
            special[c] = m
    return year_of, month_of, special


# ============================================================ 天梯图
def export_gpu_tier(src, rep):
    sheet = "GPU天梯图"
    groups = [
        ("NVIDIA", {"A": "其他产品线", "B": "10系", "C": "16、20系", "D": "30系", "E": "40系", "F": "50系"}),
        ("AMD", {"N": "90系", "O": "7000系", "P": "6000系", "Q": "5000系及以前"}),
        ("intel", {"R": "B系", "S": "A系"}),
    ]
    tier_of, cur = {}, ""
    for r in range(1, src.max_row(sheet) + 1):
        coord = "U%d" % r
        v = src.txt(sheet, coord)
        if v:
            cur = v
            rep.mark(sheet, coord)
        tier_of[r] = cur

    rows = []
    for vendor, cols in groups:
        for letter, series in cols.items():
            for r in range(3, src.max_row(sheet) + 1):
                coord = "%s%d" % (letter, r)
                model = src.txt(sheet, coord)
                if not model:
                    continue          # 只有该列真的写了型号，才算这个列组的行
                vals = {k: src.num(sheet, "%s%d" % (col, r)) for col, k in
                        (("G", "score_timespy"), ("H", "score_portroyal"),
                         ("J", "score_timespy_2"), ("K", "score_steelnomad"))}
                total = src.num(sheet, "I%d" % r)
                for col in ("G", "H", "J", "K", "I"):
                    if src.val(sheet, "%s%d" % (col, r)) is not None:
                        rep.mark(sheet, "%s%d" % (col, r))
                rep.mark(sheet, coord)
                if src.txt(sheet, "U%d" % r):
                    rep.mark(sheet, "U%d" % r)
                rows.append([tier_of.get(r, ""), vendor, series, model,
                             fmt(vals["score_timespy"]), fmt(vals["score_portroyal"]),
                             fmt(vals["score_timespy_2"]), fmt(vals["score_steelnomad"]),
                             fmt(total)])
    rep.derived(sheet, "TOTAL(I列)", "读 Excel 缓存的计算结果（G+H+J+K），不由本脚本重算")
    rows.sort(key=lambda x: (x[0] == "", x[0], x[1], x[3]))
    return (["tier", "vendor", "series", "model", "score_timespy", "score_portroyal",
             "score_timespy_2", "score_steelnomad", "score_total"], rows)


def _export_cpu_like(src, rep, sheet, groups, id_cols, note_col="", tier_col="", tier_label=""):
    tier_of, cur = {}, ""
    if tier_col:
        for r in range(1, src.max_row(sheet) + 1):
            v = src.txt(sheet, "%s%d" % (tier_col, r))
            if v:
                cur = v
                rep.mark(sheet, "%s%d" % (tier_col, r))
            tier_of[r] = cur
    rows = []
    for vendor, cols in groups:
        for letter, platform in cols.items():
            for r in range(3, src.max_row(sheet) + 1):
                coord = "%s%d" % (letter, r)
                model = src.txt(sheet, coord)
                if not model:
                    continue          # 只有该列真的写了型号，才算这个分组/世代的行
                nums = {k: src.num(sheet, "%s%d" % (col, r)) for k, col in id_cols.items()}
                for col in id_cols.values():
                    if src.val(sheet, "%s%d" % (col, r)) is not None:
                        rep.mark(sheet, "%s%d" % (col, r))
                rep.mark(sheet, coord)
                note = ""
                if note_col:
                    note = src.txt(sheet, "%s%d" % (note_col, r))
                    if src.val(sheet, "%s%d" % (note_col, r)) is not None:
                        rep.mark(sheet, "%s%d" % (note_col, r))
                if tier_col and src.val(sheet, "%s%d" % (tier_col, r)) is not None:
                    rep.mark(sheet, "%s%d" % (tier_col, r))
                rows.append([tier_of.get(r, "") if tier_col else "", vendor, platform, model]
                            + [fmt(nums[k]) for k in id_cols] + [note])
    if tier_label:
        rep.derived(sheet, "tier", tier_label)
    rows.sort(key=lambda x: (x[0] == "", x[0], x[1], x[2], x[3]))
    return (TIER_HEADER, rows)


def export_cpu_tier(src, rep):
    return _export_cpu_like(
        src, rep, "桌面端CPU天梯图",
        [("Intel", {"A": "AlderLake 12代 · 2021", "B": "RaptorLake 13代 · 2022",
                    "C": "RaptorLake-R 14代 · 2023", "D": "ArrowLake-S Ultra200 · 2024"}),
         ("AMD", {"K": "ZEN3 5000系 · 2020", "L": "ZEN4 7000系 · 2022", "M": "ZEN5 9000系 · 2024"})],
        {"cores": "F", "threads": "G", "score_cpumark": "H", "score_single": "I"},
        note_col="P", tier_col="O",
        tier_label="O 列的档位按合并块向下填充（旗舰/顶级 → 高端 → 中高端 → 中端 → 入门）")


def export_laptop_cpu_tier(src, rep):
    return _export_cpu_like(
        src, rep, "笔记本CPU天梯图",
        [("Intel", {"A": "300H · Panther", "B": "HX · Ultra200/Plus", "C": "200H · Arrow",
                    "D": "200V · Lunar", "E": "HX · 14代老(i系)", "F": "300 · 404缩核",
                    "G": "Core300 · Walker", "H": "Core200H 马甲"}),
         ("AMD", {"O": "HX9000 · Zen5", "P": "AI Max+ 300", "Q": "HX 7000/8000 · Zen4",
                  "R": "AI 400/300 · Zen5", "S": "锐龙200/8000HS 马甲", "T": "Zen3+ 老"})],
        {"cores": "J", "threads": "K", "score_cpumark": "L", "score_single": "M"},
        note_col="", tier_col="", tier_label="")


# ============================================================ 行情（宽表 → 长表）
def export_prices(src, rep, sheet, category, cols, row_from=3, row_to=None, ignore=None):
    """row_to/ignore：行情表末尾常有"查询小表"（如 CPU行情 的『关于核心与线程』），
    不是价格行，必须显式排除并记录，不能当成价格写进 CSV。"""
    year_of, month_of, special = two_row_header(src, sheet, special_ok=("涨幅", "7月下旬"))
    model_col = cols["model"]
    spec_col = cols.get("spec")
    row_to = row_to or src.max_row(sheet)
    ignore = ignore or []

    for c, label in sorted(special.items()):
        if label == "涨幅":
            rep.derived(sheet, "%s 列「涨幅」" % get_column_letter(c),
                        "派生列：脚本按 (当月价 − 表内历史最低价)/历史最低价 重算做校验，原公式结果不入 CSV")
        else:
            rep.derived(sheet, "%s 列「%s」" % (get_column_letter(c), label),
                        "非月份列：7 月下半月的单次快照，按 date=YYYY-07-下旬 单独成行")

    rows = []
    for r in range(row_from, row_to + 1):
        mcoord = "%s%d" % (get_column_letter(model_col), r)
        model = src.txt(sheet, mcoord)
        if not model:
            continue
        spec = src.txt(sheet, "%s%d" % (get_column_letter(spec_col), r)) if spec_col else ""
        for k, ci in cols.items():
            coord = "%s%d" % (get_column_letter(ci), r)
            if src.val(sheet, coord) is not None:
                rep.mark(sheet, coord)
        for c in range(1, src.max_col(sheet) + 1):
            coord = "%s%d" % (get_column_letter(c), r)
            if c in month_of and c not in special:
                v = num_cell(src, sheet, coord)
                if v is None:
                    if src.is_formula(sheet, coord):
                        rep.skip(sheet, coord, "公式值（派生列），不入 CSV")
                    continue
                rows.append(["%d-%02d" % (year_of[c], month_of[c]), category, model,
                             spec, fmt(v), "", ""])
                rep.mark(sheet, coord)
            elif c in special and special[c] == "7月下旬":
                v = num_cell(src, sheet, coord)
                if v is None:
                    continue
                rows.append(["%d-07-下旬" % (year_of[c] or 0), category, model,
                             spec, fmt(v), "", "7 月下半月快照"])
                rep.mark(sheet, coord)
            elif c in special and special[c] == "涨幅":
                if src.raw(sheet, coord) is not None:
                    rep.mark(sheet, coord)
                    rep.derived(sheet, "涨幅校验 %s" % coord, "原表结果 %s" % src.val(sheet, coord))
    # 表头 + 被排除的查询小表
    for r in (1, 2):
        for c in range(1, src.max_col(sheet) + 1):
            coord = "%s%d" % (get_column_letter(c), r)
            if src.raw(sheet, coord) is not None:
                rep.mark(sheet, coord)
    for lo, hi, why in ignore:
        for r in range(lo, hi + 1):
            for c in range(1, src.max_col(sheet) + 1):
                coord = "%s%d" % (get_column_letter(c), r)
                if src.raw(sheet, coord) is not None:
                    rep.skip(sheet, coord, why)
        rep.derived(sheet, "第 %d–%d 行" % (lo, hi), why)
    rows.sort(key=lambda x: (x[0], x[2]))
    return (PRICE_HEADER, rows)


# ============================================================ 内存 / 存储
MEM_SPEC = {
    4: ("内存 DDR4", "8G×2", "raw"), 5: ("内存 DDR4", "16G×2", "raw"), 6: ("内存 DDR4", "AVG", "avg"),
    7: ("内存 DDR5", "16G×2", "raw"), 8: ("内存 DDR5", "24G×2", "raw"), 9: ("内存 DDR5", "32G×2", "raw"),
    10: ("内存 DDR5", "48G×2", "raw"), 11: ("内存 DDR5", "64G×2", "raw"),
    12: ("内存 DDR5", "MAX", "max"), 13: ("内存 DDR5", "MIN", "min"),
    14: ("固态 PCIE4.0", "1TB", "raw"), 15: ("固态 PCIE4.0", "2TB", "raw"), 16: ("固态 PCIE4.0", "AVG", "avg"),
    17: ("固态 PCIE5.0", "1TB", "raw"), 18: ("固态 PCIE5.0", "2TB", "raw"), 19: ("固态 PCIE5.0", "AVG", "avg"),
    20: ("机械硬盘 HDD", "1TB", "raw"), 21: ("机械硬盘 HDD", "2TB", "raw"),
    22: ("机械硬盘 HDD", "4TB", "raw"), 23: ("机械硬盘 HDD", "AVG", "avg"),
}


def export_memory(src, rep):
    sheet = "内存行情"
    year_of, month_of, special = two_row_header(src, sheet)
    if special:
        raise SystemExit("内存行情第 2 行出现未映射表头：%r" % special)
    rows = []
    for r, (category, spec, kind) in sorted(MEM_SPEC.items()):
        for c, month in sorted(month_of.items()):
            coord = "%s%d" % (get_column_letter(c), r)
            if src.is_formula(sheet, coord):
                rep.derived(sheet, "聚合值 %s" % coord,
                            "原表公式 %s（%s 的 %s），不入 CSV，由 check_csv.py 用同组原始单价重算校验"
                            % (src.raw(sheet, coord), category, kind.upper()))
                continue
            v = num_cell(src, sheet, coord)
            if v is None:
                continue
            rows.append(["%d-%02d" % (year_of[c], month), category,
                         category.split()[0], spec, fmt(v), "", ""])
            rep.mark(sheet, coord)
    for r in range(1, src.max_row(sheet) + 1):
        for c in range(1, src.max_col(sheet) + 1):
            coord = "%s%d" % (get_column_letter(c), r)
            if src.raw(sheet, coord) is not None:
                rep.mark(sheet, coord)
    rep.derived(sheet, "AVG / MAX / MIN", "同组容量的均值、最大值、最小值；CSV 只留原始容量单价，聚合值由 check_csv.py 重算")
    rows.sort(key=lambda x: (x[0], x[1], x[3]))
    return (PRICE_HEADER, rows)


# ============================================================ 显卡品牌
def export_brands(src, rep):
    """显卡品牌：A 列是厂商分区（N/A/I），B 列是品牌分级，C 列是品牌。

    ⚠ B 列的品牌分级是**合并单元格**（B3:B6=一线、B7:B8=次一线、B9:B14=二线…）。
      合并格里的空白不是"数据缺失"，而是"这个分级适用于整段"，必须物化到每一行；
      否则 33 行里会有 25 行 tier 为空。Src.val/raw 已把合并格解析回锚点。
      Intel 区（B32:B37）整段**本来就没写分级**，物化后仍为空 —— 那是真空白，不是 bug。
    """
    sheet = "显卡品牌"
    sections, cur = {}, None
    for r in range(1, src.max_row(sheet) + 1):
        v = src.txt(sheet, "A%d" % r)
        if v in ("N", "A", "I"):
            cur = v
        sections[r] = cur
    group = {"N": "NVIDIA 阵营", "A": "AMD 阵营", "I": "Intel 阵营"}

    tiers = ["入门(丐版)", "低端", "中端", "高端", "旗舰", "超旗舰"]
    header_labels = {"显卡品牌", "品牌分级", "入门(丐版)", "低端", "中端", "高端", "旗舰", "超旗舰"}
    rows = []
    filled_from_merge = 0
    empty_by_design = []
    for r in range(3, src.max_row(sheet) + 1):
        brand = src.txt(sheet, "C%d" % r)
        if not brand:
            continue
        if brand in header_labels or src.txt(sheet, "B%d" % r) == "品牌分级":
            # AMD / Intel 分区各自带一行表头（A='A'/'I', B='品牌分级', C='显卡品牌'），
            # 那不是数据行，必须排除，否则会把后面所有行的对齐顶偏一位。
            rep.skip(sheet, "C%d" % r, "分区表头行（B='品牌分级' / C='%s'），不是数据行" % brand)
            continue
        tier = src.txt(sheet, "B%d" % r)
        if src.in_merge(sheet, "B%d" % r):
            if tier:
                filled_from_merge += 1
            else:
                empty_by_design.append("%s%d:%s" % ("B", r, brand))
        rows.append([group.get(sections[r], ""), tier, brand]
                    + [src.txt(sheet, "%s%d" % (c, r)) for c in "DEFGHI"])
        for c in "ABCDEFGHI":
            coord = "%s%d" % (c, r)
            if src.raw(sheet, coord) is not None:
                rep.mark(sheet, coord)
    for r in (2, 20, 32):
        for c in "ABCDEFGHI":
            coord = "%s%d" % (c, r)
            if src.raw(sheet, coord) is not None:
                rep.mark(sheet, coord)

    # ---- 自检：物化之后 tier 不允许为空（真空白要单独列出来，不能混过去）----
    blank = [row[2] for row in rows if not row[1]]
    if blank:
        rep.derived(sheet, "⚠ tier 为空的行",
                    "以下行的品牌分级在原表里确实没写（所属合并段的锚点就是空的）：%s" % "、".join(blank))
    rep.derived(sheet, "阵营 / 品牌分级",
                "A、B 两列的合并单元格已把标签物化到该段每一行"
                "（品牌分级共补全 %d 行）；%s" % (
                    filled_from_merge,
                    ("Intel 区 B32:B37 原表未写分级，故 %d 行 tier 为空" % len(blank)) if blank else "无空白"))
    return (["vendor_group", "tier", "brand"] + tiers, rows)


# ============================================================ 主流程
def build(export=True, out_dir=None):
    out_dir = out_dir or DATA
    src = Src(XLSX)
    if src.v_path != XLSX:
        print("注意：%s 没有 Excel 缓存值，计算结果改从 %s 读取"
              % (os.path.relpath(XLSX, ROOT), os.path.relpath(src.v_path, ROOT)))
    rep = Report()
    out = {}
    out["gpu-tier.csv"] = export_gpu_tier(src, rep)
    out["cpu-tier.csv"] = export_cpu_tier(src, rep)
    out["laptop-cpu-tier.csv"] = export_laptop_cpu_tier(src, rep)
    out["gpu-prices.csv"] = export_prices(src, rep, "显卡行情", "显卡", {"model": 3, "spec": 2},
                                          row_to=41,
                                          ignore=[(42, src.max_row("显卡行情"), "空行，仅占位")])
    out["cpu-prices.csv"] = export_prices(src, rep, "CPU行情", "CPU", {"model": 2},
                                          row_to=57,
                                          ignore=[(60, src.max_row("CPU行情"),
                                                   "『关于核心与线程』查询小表：型号串与核心/线程数的对照，不是价格；"
                                                   "内容已抄进「说明」表的字段字典")])
    out["memory-prices.csv"] = export_memory(src, rep)
    out["gpu-brands.csv"] = export_brands(src, rep)
    if export:
        for name, (header, rows) in out.items():
            n = write_csv(os.path.join(out_dir, name), header, rows)
            print("  %-22s %4d 行  %s" % (name, n, ", ".join(header)))
    rep.dump(src.f)
    return out, rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只解析，不写文件")
    ap.add_argument("--out-dir", default=None,
                    help="输出目录（默认 data/）。CI 会导出到临时目录再和仓库里的对比，"
                         "这样校验过程不会碰到工作区里的任何文件。")
    args = ap.parse_args()
    print("源工作簿：%s" % os.path.relpath(XLSX, ROOT))
    print("导出目录：%s（编码 %s）" % (args.out_dir or os.path.relpath(DATA, ROOT), ENCODING))
    print()
    out, _ = build(export=not args.check, out_dir=args.out_dir)
    if not args.check:
        print("\n完成：%d 个 CSV 已写入 %s" % (len(out), args.out_dir or os.path.relpath(DATA, ROOT)))


if __name__ == "__main__":
    main()
