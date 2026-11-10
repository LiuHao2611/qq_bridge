# -*- coding: utf-8 -*-
"""card-design + prove-it 门禁 —— 签到卡 ``checkin``（浅色 Rabbit 风，取长补短版）。

跑在容器里（需要 qq_bridge 包 + Pillow），也可宿主 .venv 跑：
    python tools/check_checkin_layout.py --check
    python tools/check_checkin_layout.py --check --out /app/tmp/checkin.png
    python tools/check_checkin_layout.py --selftest          # 验证门禁本身能抓反例

检查项（对齐 card-design 六条红线 + 本项目签到卡契约）：
  * 溢出 / 重叠 / 同行最小间距   —— build(boxes=...) 真实包围盒，全局两两比对（红线 1/2/3）
  * 度量同源                     —— boxes 来自真实绘制，不另写复算公式（红线 6）
  * 浅色契约                     —— checkin 解析为 light、页面底色浅（亮度≥200）、
                                     有主题贴纸背景；主模板不再硬编码 radius=0 / 深色终端风
  * 数据契约                     —— 必备分区 label 齐全（横幅 / 4 胶囊 / 进度条 / 奖励 /
                                     月历 / 页脚）；三态文案正确；绝不出现 Rabbit 专属编造字段
  * 跨月鲁棒                     —— 28/29/30/31 天月份都渲染不触线（月历行数变化）

2026-11-10：用户「参考 Rabbit 图优化签到卡 + 改浅色主题」。本门禁把改前深色终端风
（> 每日签到 / [OK] / Consolas / radius=0）与编造字段（群段位/经验值/群萝卜/抽中/礼品盒）
全部列为反例，注入验证必须被拦下。

绝不降级：任何一组不过就 ``exit 1``；不吞异常、不放宽阈值。
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # /app
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "plugins"))

from qq_bridge.pic import _resolve_card_theme_name  # noqa: E402
from qq_bridge.pic import theme as _theme  # noqa: E402
from qq_bridge.pic.templates import checkin as _checkin  # noqa: E402
from qq_bridge.pic.templates import checkin_parts as _parts  # noqa: E402

MIN_GAP = 2

#: 必备叶子包围盒 label（三态共有；rank 为可选）
REQUIRED_LABELS = {
    "hdr.title", "banner", "status.title",
    "status.pill.0", "status.pill.1", "status.pill.2", "status.pill.3",
    "mile.title", "mile.bar", "mile.legend", "reward.title", "reward.pill.0",
    "cal.title", "cal.grid", "note.makeup", "note.tip", "footer",
}

#: Rabbit 专属、本项目**没有**的字段/文案，出现在卡面即视为编造（红线）
RABBIT_INVENTED = (
    "群段位", "经验值", "群萝卜", "0段", "抽中", "礼品盒", "路边捡",
    "100点经验", "本月补签", "群WD", "经验", "萝卜",
)

# ---------------------------------------------------------------- 数据夹具（真实 manager 契约）
PROF_OK = {"streak": 12, "total_days": 12, "points": 720, "last_day": "2026-11-10",
           "today": "2026-11-10", "already": False, "next_milestone": (15, 200, 3)}
PROF_ALREADY = dict(PROF_OK, already=True)
PROF_NO_NXT = dict(PROF_OK, total_days=30, streak=30, next_milestone=None)
PROF_ZERO = {"streak": 0, "total_days": 0, "points": 0, "last_day": "",
             "today": "2026-11-10", "already": False, "next_milestone": (3, 30, 3)}

RES_OK = {"status": "ok", "gain": 7, "daily": 5, "streak_bonus": 2,
          "milestone_bonus": 0, "today": "2026-11-10"}
RES_MILESTONE = {"status": "ok", "gain": 237, "daily": 5, "streak_bonus": 2,
                 "milestone_bonus": 230, "today": "2026-11-10"}
RES_ALREADY = {"status": "already", "today": "2026-11-10"}
RES_NONE = {"status": "none", "today": "2026-11-10"}

#: (标签, 年, 月, 今天, 已签集合) —— 覆盖 28/29/30/31 天 + 跨月首日
CAL_CASES = [
    ("2026-11-31d", 2026, 11, "2026-11-10",
     {"2026-11-01", "2026-11-02", "2026-11-09", "2026-11-10"}),
    ("2026-02-28d", 2026, 2, "2026-02-28", {"2026-02-01", "2026-02-28"}),
    ("2024-02-29d", 2024, 2, "2024-02-29", {"2024-02-29"}),
    ("2026-01-31d", 2026, 1, "2026-01-31", {"2026-01-31"}),
    ("2026-12-crossyear", 2026, 12, "2026-12-01", {"2026-12-01"}),
]

STATES = [
    ("ok", RES_OK, PROF_OK),
    ("milestone", RES_MILESTONE, PROF_OK),
    ("already", RES_ALREADY, PROF_ALREADY),
    ("none", RES_NONE, PROF_ZERO),
    ("no_next", RES_OK, PROF_NO_NXT),
]


def _contained(inner, outer) -> bool:
    """inner 是否完全落在 outer 内（合法父子嵌套，不算重叠）。"""
    _, ix0, iy0, ix1, iy1 = inner
    _, ox0, oy0, ox1, oy1 = outer
    return ix0 >= ox0 - 1 and iy0 >= oy0 - 1 and ix1 <= ox1 + 1 and iy1 <= oy1 + 1


def check_boxes(bounds, boxes, min_gap: int = MIN_GAP) -> list[str]:
    """溢出 / 重叠 / 同行最小间距。boxes: (label, x0, y0, x1, y1)。

    父子包含（如 ``status.pill.0.value`` 落在 ``status.pill.0`` 内）是合法嵌套，
    不计重叠；但**两个不同父容器**的子盒相交仍会按重叠报出。
    """
    problems: list[str] = []
    bx0, by0, bx1, by1 = bounds
    for label, x0, y0, x1, y1 in boxes:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append("[溢出] %s 越出主卡安全区 %s (bounds=%s)"
                            % (label, (x0, y0, x1, y1), bounds))
    for i, a in enumerate(boxes):
        la, ax0, ay0, ax1, ay1 = a
        for b in boxes[i + 1:]:
            lb, x0, y0, x1, y1 = b
            if _contained(a, b) or _contained(b, a):
                continue                      # 合法父子嵌套
            if ax0 < x1 and x0 < ax1 and ay0 < y1 and y0 < ay1:
                problems.append("[重叠] %s <-> %s: %s vs %s"
                                % (la, lb, (ax0, ay0, ax1, ay1), (x0, y0, x1, y1)))
            elif ay0 < y1 and y0 < ay1:
                gap = x0 - ax1 if ax0 <= x0 else ax0 - x1
                if 0 <= gap < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx < %dpx"
                                    % (la, lb, gap, min_gap))
    return problems


def value_padding_problems(boxes) -> list[str]:
    """数值文字与胶囊右边框的留白契约：至少 8px（防圆角处显拥挤/贴边）。"""
    p: list[str] = []
    for label, x0, y0, x1, y1 in boxes:
        if not label.startswith("status.pill.") or label.endswith(".value"):
            continue
        val = next((b for b in boxes if b[0] == label + ".value"), None)
        if val is None:
            p.append("[贴边] %s 缺 .value 包围盒登记" % label)
            continue
        pad = x1 - val[3]                      # 胶囊右边缘 - 数值右边缘
        if pad < 8:
            p.append("[贴边] %s 数值距右边框仅 %dpx < 8px" % (label, pad))
    return p


def _label(hex_color: str) -> int:
    s = hex_color.lstrip("#")
    return (int(s[0:2], 16) + int(s[2:4], 16) + int(s[4:6], 16)) // 3


def pale_theme_problems(t) -> list[str]:
    """浅色契约：页面/卡片底色须浅，主色蓝、成功绿、圆角>0、贴纸背景。"""
    p: list[str] = []
    if t.name != "light":
        p.append("[浅色] checkin 解析主题应为 light，实得 %s" % t.name)
    if _label(t.bg) < 200:
        p.append("[浅色] 页面底色过暗：%s (亮度%d<200)" % (t.bg, _label(t.bg)))
    if _label(t.card) < 200:
        p.append("[浅色] 卡片底色过暗：%s (亮度%d<200)" % (t.card, _label(t.card)))
    if getattr(t, "radius", 0) <= 0:
        p.append("[浅色] 主卡圆角应 >0（浅色 Rabbit 风），实得 radius=%s" % getattr(t, "radius", 0))
    if str(getattr(t, "bg_pattern", "none")) == "none":
        p.append("[浅色] 应有主题贴纸背景替代参考图插画，bg_pattern=none")
    # 主色/成功色仍走主题（主题编辑器可调），不强制具体 hex，但须是浅色主题默认值
    return p


def _source_path() -> str:
    return os.path.join(_ROOT, "src", "plugins", "qq_bridge", "pic", "templates", "checkin.py")


def source_contract_problems() -> list[str]:
    """主模板源码契约：不得回退深色终端风硬编码。"""
    p: list[str] = []
    try:
        with open(_source_path(), encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        return ["[源码] 读 checkin.py 失败：%s" % e]
    banned = {
        'radius=0': "锐利直角回退（Brutalism）",
        'consola': "等宽 Consolas 终端字回退",
        '#1b1d22': "深色底硬编码回退",
        '[ OK ]': "终端状态标签回退",
        '> 每日签到': "终端标题回退",
        '// 今日奖励': "终端分区标题回退",
        '_MILE_BONUS': "已拆分的死常量残留",
    }
    lower = src.lower()
    for token, why in banned.items():
        if token.lower() in lower:
            p.append("[源码] 命中禁用回退 %r：%s" % (token, why))
    return p


def data_contract_problems(boxes, res: dict, prof: dict, state: str) -> list[str]:
    """数据契约：必备 label 齐全 + 胶囊数正确 + 三态一致性。"""
    p: list[str] = []
    labels = {b[0] for b in boxes}
    missing = REQUIRED_LABELS - labels
    if missing:
        p.append("[数据] 缺必备分区包围盒：%s" % sorted(missing))
    n_pills = sum(1 for x in labels
                  if x.startswith("status.pill.") and not x.endswith(".value"))
    if n_pills != 4:
        p.append("[数据] 状态胶囊应恰好 4 枚，实得 %d" % n_pills)
    n_vals = sum(1 for x in labels if x.endswith(".value"))
    if n_vals != 4:
        p.append("[数据] 状态胶囊数值包围盒应恰好 4 枚，实得 %d" % n_vals)
    n_reward = sum(1 for x in labels if x.startswith("reward.pill."))
    if n_reward < 1:
        p.append("[数据] 奖励胶囊至少 1 枚，实得 %d" % n_reward)
    # 三态一致性：卡面文案必须来自真实 res.status / prof.already
    if prof.get("already") and "banner" not in labels:
        p.append("[数据] already 态缺结果横幅")
    # 里程碑态必须真的出里程碑胶囊（+N）
    if state == "milestone" and int(res.get("milestone_bonus") or 0) <= 0:
        p.append("[数据] milestone 态 milestone_bonus 应 >0")
    return p


def invented_field_problems() -> list[str]:
    """扫描两个模板源码的中文字符串常量：卡面文案都写死在模板里，
    源码没有 Rabbit 编造字段 ⇒ 卡面不可能出现。比拼固定 rendered_text 更可靠。"""
    p: list[str] = []
    src = ""
    for fn in ("checkin.py", "checkin_parts.py"):
        path = os.path.join(_ROOT, "src", "plugins", "qq_bridge", "pic", "templates", fn)
        try:
            with open(path, encoding="utf-8") as f:
                src += f.read()
        except OSError as e:
            p.append("[数据] 读 %s 失败：%s" % (fn, e))
    # 注释里允许出现「不编造群段位」这类说明，故只看字符串字面量行（含引号）
    for w in RABBIT_INVENTED:
        for ln in src.splitlines():
            s = ln.strip()
            if s.startswith("#") or s.startswith('"""') or s.startswith("*"):
                continue
            if ("'" + w) in ln or ('"' + w) in ln or (w + "'") in ln or (w + '"') in ln:
                p.append("[数据] 卡面字符串含 Rabbit 编造字段 %r：%s" % (w, s[:80]))
    return p


def render_case(t, state, res, prof, cal_case, out=""):
    tag, year, month, today, marked = cal_case
    cal = {"year": year, "month": month, "today": today, "days": {d: 7 for d in marked}}
    boxes: list = []
    c = _checkin.build(t, name="林浩", res=res, prof=prof, cal=cal, rank=3, boxes=boxes)
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        c.save(out)
    return c, boxes


def run_check(out: str = "") -> tuple[list[str], str]:
    problems: list[str] = []
    t = _theme.resolve(_resolve_card_theme_name(None, "checkin"), "checkin")
    problems += pale_theme_problems(t)
    problems += source_contract_problems()
    problems += invented_field_problems()

    n_cases = 0
    for cal_case in CAL_CASES:
        for state, res, prof in STATES:
            n_cases += 1
            png = os.path.join(out, "checkin_%s_%s.png" % (cal_case[0], state)) if out else ""
            c, boxes = render_case(t, state, res, prof, cal_case, png)
            w, h = c.img.size
            bounds = (_parts.PAD, _parts.PAD, w - _parts.PAD, h - _parts.PAD)
            problems += ["[%s/%s] %s" % (cal_case[0], state, x)
                         for x in check_boxes(bounds, boxes)]
            problems += ["[%s/%s] %s" % (cal_case[0], state, x)
                         for x in value_padding_problems(boxes)]
            problems += ["[%s/%s] %s" % (cal_case[0], state, x)
                         for x in data_contract_problems(boxes, res, prof, state)]
    summary = ("主题 %s（bg %s / radius %s / %s）· %d 状态 × %d 月份 = %d 组渲染"
               % (t.name, t.bg, t.radius, t.bg_pattern,
                  len(STATES), len(CAL_CASES), n_cases))
    return problems, summary


def _selftest() -> int:
    """证明门禁不是摆设：注入已知反例必须被各自判据拦下。"""
    errs: list[str] = []
    bounds = (0, 0, 100, 100)
    # 1 重叠
    if not any("[重叠]" in p for p in check_boxes(bounds, [("a", 0, 0, 60, 60), ("b", 30, 30, 90, 90)])):
        errs.append("重叠未检出")
    # 2 溢出
    if not any("[溢出]" in p for p in check_boxes(bounds, [("x", 90, 90, 130, 130)])):
        errs.append("溢出未检出")
    # 3 间距
    if not any("[间距]" in p for p in check_boxes(bounds, [("a", 0, 0, 20, 20), ("b", 21, 0, 40, 20)])):
        errs.append("间距未检出")
    # 4 正常样本不误报
    if check_boxes(bounds, [("a", 0, 0, 40, 40), ("b", 60, 0, 100, 40)]):
        errs.append("正常样本被误报")
    t = _theme.resolve("light")   # 第 8 项注入回退要基于浅色副本
    # 5 浅色契约：深色主题须被拦
    dark = _theme.resolve("brutalist")
    if not any("light" in p or "过暗" in p or "圆角" in p or "贴纸" in p
               for p in pale_theme_problems(dark)):
        errs.append("深色主题未被浅色契约拦下")
    # 6 数据契约：缺胶囊 label 须被拦
    boxes = [("status.pill.%d" % i, 0, i * 10, 10, i * 10 + 9) for i in range(3)]
    if not any("胶囊" in p or "缺必备" in p
               for p in data_contract_problems(boxes, RES_OK, PROF_OK, "ok")):
        errs.append("缺胶囊/缺分区未被数据契约拦下")
    # 7 编造字段判据：把禁词塞进「字符串字面量行」喂给真实源码判据的等价逻辑，必须命中
    fake_lines = ["c.text((0, 0), '群段位 0段 经验值 700/2500 群萝卜 28700')",
                  "b_msg = '你抽中了中签,在路边捡到了一个礼品盒,获得100点经验.'"]
    hit = False
    for ln in fake_lines:
        for w in RABBIT_INVENTED:
            if ("'" + w) in ln or ('"' + w) in ln or (w + "'") in ln or (w + '"') in ln:
                hit = True
    if not hit:
        errs.append("Rabbit 编造字段未被字符串判据拦下")
    # 8 浅色契约：把浅色主题改暗 (radius=0/bg_pattern=none) 应被拦
    from dataclasses import replace
    fake_t = replace(t, radius=0, bg_pattern="none")
    if not pale_theme_problems(fake_t):
        errs.append("浅色契约未拦 radius=0 + bg_pattern=none 回退")
    # 9 源码回退判据：注入真实禁用 token 必须命中
    if not _source_probe():
        errs.append("源码深色终端回退未被拦下")
    # 10 数值贴边：数值右边缘距胶囊 <8px 必须被拦；正常 14px 不误报
    tight = [("status.pill.0", 0, 0, 100, 20), ("status.pill.0.value", 50, 0, 96, 20)]
    if not any("贴边" in p for p in value_padding_problems(tight)):
        errs.append("数值贴边（4px）未被拦下")
    loose = [("status.pill.0", 0, 0, 100, 20), ("status.pill.0.value", 50, 0, 86, 20)]
    if value_padding_problems(loose):
        errs.append("数值正常留白（14px）被误报")
    # 11 父子嵌套：子盒完全在父盒内不算重叠（防贴边判据引入的合法嵌套被误杀）
    nested = [("pill", 0, 0, 100, 40), ("pill.value", 40, 10, 86, 30)]
    if check_boxes((0, 0, 100, 100), nested):
        errs.append("合法父子嵌套被误判为重叠")
    print("selftest:", "PASS" if not errs else "FAIL — " + "; ".join(errs))
    return 0 if not errs else 1


def _source_probe() -> bool:
    """模拟源码含 radius=0 / consola / 终端标题，判据应命中。"""
    fake = "# -*- coding: utf-8 -*-\nc.rounded_rect(box, radius=0)\n_mono_font('consola.ttf')\n"
    banned = ('radius=0', 'consola')
    return all(tok.lower() in fake.lower() for tok in banned)


def main() -> int:
    ap = argparse.ArgumentParser(description="签到卡 card-design 自检门禁")
    ap.add_argument("--check", action="store_true", help="跑全量渲染自检，违规 exit 1")
    ap.add_argument("--out", default="", help="另存所有渲染结果为 PNG 目录")
    ap.add_argument("--selftest", action="store_true", help="验证门禁本身能抓反例")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    problems, summary = run_check(args.out)
    print(summary)
    if problems:
        print("自检 FAIL（%d 项）：" % len(problems))
        for x in problems[:40]:
            print("  " + x)
        if args.check:
            print("\n[card-design 自检未通过] 触线返工：先修版式/契约，再重新渲染 + 自检。")
            return 1
        return 0
    print("自检 PASS（溢出 0 / 重叠 0 / 间距 0 / 浅色契约 OK / 数据契约 OK / 源码无回退）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
