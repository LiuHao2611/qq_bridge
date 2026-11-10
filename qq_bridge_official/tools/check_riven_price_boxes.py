# -*- coding: utf-8 -*-
"""紫卡全面分析「市场价格预计 / 词条价差预估」交付闸门。

用户 2026-11-10 反馈「处理下无匹配样本的问题，搞清楚别人是怎么做的，全量对齐」。
旧实现把左盒 big/foot 写死「暂无匹配样本」、左右两框填同一个 ``med``、自造
「样本 n=」、写绝对时间戳，与 Rabbit 参考卡的语义全部偏离。

用法（qq_bridge 根目录，用带 httpx/PIL 的解释器）：
    .venv/Scripts/python.exe tools/check_riven_price_boxes.py --check
    .venv/Scripts/python.exe tools/check_riven_price_boxes.py --selftest
    .venv/Scripts/python.exe tools/check_riven_price_boxes.py --check \
        --fixture tests/fixtures/rabbit_riven_price_reference.json

``--check``：① 逐字比对参考卡基准（外部文件，禁止反改）；② 6 种真实 market
状态跑状态机判定；③ 渲染有/无匹配样本两张卡并写截图 + 版式盒自检。
``--selftest``：把本次真实犯过的错放回去，证明闸门会报错；不依赖 tests/，
因此容器内（镜像不含 tests/）也能直接跑。

缺 fixture 时明确报错，不自动回退在线、不静默跳过。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src.plugins.qq_bridge.game_tools import riven_price  # noqa: E402

DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "rabbit_riven_price_reference.json"
SHOTS = ROOT / "tests"


def _mkt(**kw) -> dict:
    """真实 ``fetch_market_bundle`` 形状的快照（字段含义见 riven_card.py）。"""
    base = {"status": "ok", "n": 27, "med": 380.0, "lo": 330.0, "hi": 400.0,
            "zero": 369.0, "match_tier": "同负词条",
            "rec_neg": {"delta": 17.0}, "exact": {"status": "few", "n": 1},
            "ts": time.strftime("%Y-%m-%d %H:%M")}
    base.update(kw)
    return base


CASES = (
    ("L1 正负面相同精确匹配", _mkt(exact={"status": "ok", "n": 9, "median": 520.0})),
    ("L2 同负词条层级", _mkt()),
    ("L2 无 rank0 零洗样本", _mkt(zero=None)),
    ("L2 溢价为零", _mkt(rec_neg={"delta": 0})),
    ("L3 样本不足", _mkt(status="error", error="同等级有效样本 2 条 < 3")),
    ("L3 市场未获取", None),
)


def _reference_problems(ref: dict, sample: dict) -> list[str]:
    """逐字基准：参考卡夹具不得被按实现反改。"""
    p = []
    for key in ("price_left", "price_right"):
        want = ref[key]
        got = sample.get(key) or {}
        for field, want_val in want.items():
            if str(got.get(field) or "") != want_val:
                p.append("[逐字] %s.%s 应为 %r，实得 %r"
                         % (key, field, want_val, got.get(field)))
    if str(sample.get("observed") or "") != ref["observed"]:
        p.append("[逐字] observed 应为 %r，实得 %r"
                 % (ref["observed"], sample.get("observed")))
    return p


def _state_problems() -> list[str]:
    """真实 market 状态 → 数据层必须满足参考卡语义。"""
    from src.plugins.qq_bridge.game_tools import riven_full as dm
    out = []
    for name, mkt in CASES:
        data = dm.riven_full_data(weapon="绝路", market=mkt)
        probs = riven_price.price_box_problems(data, mkt)
        left, right = data["price_left"], data["price_right"]
        print("  [%s] %-22s 左盒 big=%-12s 零洗=%-6s foot=%-28s ｜ 右盒 %s / %s ｜ 问题 %d"
              % ("OK" if not probs else "FAIL", name, left["big"], left["sub_val"],
                 left["foot"], right["big"], right["sub"], len(probs)))
        for x in probs:
            print("      - " + x)
        out += ["%s: %s" % (name, x) for x in probs]
    return out


def _render_problems() -> list[str]:
    """两种 big 状态都要渲染出图，且真实文字盒无溢出/重叠/间距问题。"""
    import _riven_full_render as R
    dm, tpl, theme = R.setup()
    out = []
    for name, mkt in (("matched", _mkt()), ("no_match", None)):
        data = dm.riven_full_data(weapon="绝路", market=mkt)
        boxes: list = []
        c = tpl.build(theme, data=data, width=655, boxes=boxes)
        res = tpl._check_boxes(boxes, min_gap=2.0, pad=28, width=655, height=1920)
        bad = res["overflow"] + res["overlap"] + res["spacing"]
        shot = SHOTS / ("_shot_riven_price_%s.png" % name)
        shot.write_bytes(c.to_bytes())
        print("  [%s] 渲染 %s → %dx%d, big=%s, 溢出 %d / 重叠 %d / 间距 %d"
              % ("OK" if not bad else "FAIL", shot.name, c.width, c.height,
                 data["price_left"]["big"], len(res["overflow"]),
                 len(res["overlap"]), len(res["spacing"])))
        out += ["渲染 %s: %s" % (name, x) for x in bad]
    return out


def run_check(fixture: Path) -> int:
    if not fixture.is_file():
        print("FAIL：参考卡基准文件不存在：%s（禁止自动回退，用 --fixture 显式指定）"
              % fixture)
        return 2
    ref = json.loads(fixture.read_text(encoding="utf-8"))
    from src.plugins.qq_bridge.game_tools import riven_full as dm
    problems = _reference_problems(ref, dm.reference_sample())
    print("=== 逐字基准（%s）===" % fixture.name)
    print("  [%s] Rabbit 原卡价格盒 %d 处偏差"
          % ("OK" if not problems else "FAIL", len(problems)))
    for x in problems:
        print("      - " + x)
    print("=== 状态机（%d 种真实 market 快照）===" % len(CASES))
    problems += _state_problems()
    print("=== 渲染 + 版式盒 ===")
    problems += _render_problems()
    print("\n紫卡价格盒闸门：%s" % ("PASS（逐字基准 0 偏差 / 状态机 0 问题 / 版式 0 问题）"
                                if not problems else "FAIL %d 项" % len(problems)))
    return 1 if problems else 0


def run_selftest() -> int:
    """把本次真实犯过的错放回去，闸门必须报错（不依赖 tests/）。"""
    errs = []
    good = _mkt()
    from src.plugins.qq_bridge.game_tools import riven_full as dm
    data = dm.riven_full_data(weapon="绝路", market=good)
    if riven_price.price_box_problems(data, good):
        errs.append("正常匹配样本被误报")

    def caught(mut) -> bool:
        d = json.loads(json.dumps(data))
        mut(d)
        return bool(riven_price.price_box_problems(d, good))

    # ① 旧实现：有 27 条样本仍写死「暂无匹配样本」
    if not caught(lambda d: d["price_left"].update(big="暂无匹配样本")):
        errs.append("写死「暂无匹配样本」未被拦住")
    # ② 旧实现：零洗中位价拿 rank8 的 med 冒充（380P）
    if not caught(lambda d: d["price_left"].update(sub_val="380P")):
        errs.append("拿 med 冒充零洗中位价未被拦住")
    # ③ 旧实现：左右两框同一个数
    if not caught(lambda d: d["price_right"].update(big=data["price_left"]["sub_val"])):
        errs.append("两框同值未被拦住")
    # ④ 旧实现：自造「样本 n=27」+ 写死置信度 80%
    if not caught(lambda d: d["price_right"].update(
            sub="来源：wm市场|样本 n=27|置信度80%")):
        errs.append("自造样本字段/写死置信度未被拦住")
    # ⑤ 旧实现：绝对时间戳
    if not caught(lambda d: d.update(observed="最近观测：2026-11-09 18:43")):
        errs.append("绝对时间戳未被拦住")
    # ⑥ 有匹配样本却不标匹配层级
    if not caught(lambda d: d["price_left"].update(foot="暂无匹配样本")):
        errs.append("缺匹配层级标注未被拦住")
    # ⑦ 无样本却编造价格
    empty = dm.riven_full_data(weapon="绝路", market=None)
    if not riven_price.price_box_problems(
            dict(empty, price_left=dict(empty["price_left"], big="999P")), None):
        errs.append("无样本编造价格未被拦住")
    # ⑧ 置信度必须随 n 变化（不是常量）
    a = dm.riven_full_data(weapon="绝路", market=_mkt(n=4))
    b = dm.riven_full_data(weapon="绝路", market=_mkt(n=27))
    if a["price_right"]["sub"] == b["price_right"]["sub"]:
        errs.append("置信度写死、不随样本数变化")
    # ⑨ 失败态不得残留旧快照价格（本次门禁实测抓到的真错）
    fail = dm.riven_full_data(weapon="绝路", market=None)
    if not riven_price.price_box_problems(
            dict(fail, price_left=dict(fail["price_left"], sub_val="369P")), None):
        errs.append("失败态残留旧价格未被拦住")

    print("selftest：%s" % ("PASS 全部注入反例被拦下" if not errs
                          else "FAIL — " + "; ".join(errs)))
    return 1 if errs else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="逐字基准 + 状态机 + 渲染（默认）")
    ap.add_argument("--selftest", action="store_true", help="注入反例验证闸门会报错")
    ap.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="参考卡逐字基准 JSON")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    return run_check(Path(args.fixture))


if __name__ == "__main__":
    raise SystemExit(main())
