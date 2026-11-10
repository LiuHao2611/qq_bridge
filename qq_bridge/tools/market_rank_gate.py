# -*- coding: utf-8 -*-
"""MOD「等级」列交付闸门（2026-11-10，从 check_market_currency_layout.py 按功能拆出）。

主门禁 check_market_currency_layout.py 是存量文件，超 20KB 会撞代码文件上限，
故把等级列的渲染核对 + 反例自检拆到本模块（<20KB），主文件只留两行入口调用。

契约定稿（用户「遇到有等级的物品时应该展示物品等级」+ 选「加等级列 + 全部等级混排」）：
  * MOD 表格 6 列：卖家|等级|状态|声望|数量|白金价格，行尾仍 uid/头像两列；
  * 每行等级胶囊登记 ``tbl.row.rank:<标记>``（零级/R1/满级/—），表头登记 ``tbl.hdr.rank``；
  * 等级标记必须真实混排（不能全是一种），数量 = 数据行数；
  * 非 MOD（5 列）卡不得混入任何等级盒 —— 向后兼容旧卡 / editor 预览 / 夹具。

高度/列宽/配色由 market_currency.table_ratios / rank_colors 单一来源提供，本模块只读。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402
for _p in (str(ROOT), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import render_wm_card as R  # noqa: E402
from src.plugins.qq_bridge.pic import theme  # noqa: E402
from src.plugins.qq_bridge.pic.templates import guide  # noqa: E402
# 刻意不 import 主门禁文件：check_boxes / _currency_problems / _icon_slot_problems /
# MIN_GAP 一律由主文件在调用时参数注入，保证依赖单向（主→本模块），杜绝循环 import。

CARD = "星际战甲·市价"
SHOTS = ROOT / "tests"
MIXED = {"零级", "R1", "满级"}
RANKED_ROWS = 10
RANKED_SHOT = "_shot_market_currency_ranked.png"


def _render_ranked(theme_name: str = "light"):
    """渲染 MOD 6 列等级样例（Hammer Shot 零级/R1/满级混排），返回 (Canvas, boxes)。"""
    body = R._body_ranked().replace("|不可兑换|1,000,000|", "|不可兑换|1,000,000|")
    t = theme.get(theme_name)
    boxes: list = []
    c = guide.build(t, body=body, boxes=boxes,
                    avatars={f"u{i}": R._avatar(i) for i in range(1, 11)},
                    item_icon=R._icon(),
                    source="数据源：Warframe.Market · 市价卡布局验收")
    return c, boxes


def rank_problems(boxes: list, ranked_rows: int, mixed_labels: set[str]) -> list[str]:
    """等级列契约：表头 1 个、每行 1 枚胶囊、多等级混排；非 MOD 不得混入。

    ``ranked_rows``：>0 = 应含等级列（校验行数/混排）；=0 = 应完全无等级盒（兼容）。
    """
    names = [b[0] for b in boxes]
    rank_boxes = [b for b in boxes if b[0].startswith("tbl.row.rank:")]
    n_hdr, n_row = names.count("tbl.hdr.rank"), len(rank_boxes)
    p: list[str] = []
    if ranked_rows <= 0:
        if n_hdr or n_row:
            p.append("[等级] 非 MOD 5 列卡不应出现等级列：hdr=%d row=%d" % (n_hdr, n_row))
        return p
    if n_hdr != 1:
        p.append("[等级] 表头「等级」标签应恰好 1 个，实际 %d" % n_hdr)
    if n_row != ranked_rows:
        p.append("[等级] 每行应各 1 个等级胶囊：应 %d / 实 %d" % (ranked_rows, n_row))
    seen = {b[0].split(":", 1)[1] for b in rank_boxes}
    missing = mixed_labels - seen
    if missing:
        p.append("[等级] 应为多等级混排，缺标记 %s（实得 %s）"
                 % (sorted(missing), sorted(seen)))
    return p


def check_ranked(shots_dir: Path, check_boxes, min_gap: int,
                 currency_problems, icon_slot_problems) -> tuple[list[str], str]:
    """跑等级卡的真实出图 + 排版/图标/等级契约，返回 (问题列表, 打印行)。
    主文件把纯函数注入进来，避免本模块反向 import 造成循环依赖。
    """
    problems: list[str] = []
    rc, rboxes = _render_ranked("light")
    (shots_dir / RANKED_SHOT).write_bytes(rc.to_bytes())
    layout = check_boxes((16, 16, rc.width - 16, rc.height - 16), rboxes, min_gap)
    cur = currency_problems(rboxes, False) + icon_slot_problems(rboxes, 1.0)
    rranks = rank_problems(rboxes, RANKED_ROWS, MIXED)
    line = ("[%s] %s → %dx%d, boxes=%d, 排版问题=%d, 图标问题=%d, 等级问题=%d"
            % ("OK" if not (layout or cur or rranks) else "FAIL", RANKED_SHOT,
               rc.width, rc.height, len(rboxes), len(layout), len(cur), len(rranks)))
    problems += ["%s: %s" % (RANKED_SHOT, x) for x in layout + cur + rranks]
    return problems, line


def selftest_rank(plain_boxes: list) -> list[str]:
    """注入等级列的真实反例，证明 rank_problems 不是摆设。返回错误列表。"""
    errs: list[str] = []
    _, rboxes = _render_ranked("light")
    if rank_problems(rboxes, RANKED_ROWS, MIXED):
        errs.append("正常等级卡被误报")
    no_hdr = [b for b in rboxes if b[0] != "tbl.hdr.rank"]
    if not rank_problems(no_hdr, RANKED_ROWS, MIXED):
        errs.append("漏「等级」表头未被拦住")
    one_less = list(rboxes)
    one_less.remove(next(b for b in one_less if b[0].startswith("tbl.row.rank:")))
    if not rank_problems(one_less, RANKED_ROWS, MIXED):
        errs.append("某行漏等级胶囊未被拦住")
    single = [b for b in rboxes if not b[0].startswith("tbl.row.rank:")] + [
        ("tbl.row.rank:零级",) + b[1:] for b in rboxes if b[0].startswith("tbl.row.rank:")]
    if not rank_problems(single, RANKED_ROWS, MIXED):
        errs.append("等级不再混排（全变零级）未被拦住")
    if not rank_problems(plain_boxes + [("tbl.hdr.rank", 300, 100, 330, 120)], 0, set()):
        errs.append("非 MOD 卡混入等级列未被拦住")
    return errs
