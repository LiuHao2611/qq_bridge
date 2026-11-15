# -*- coding: utf-8 -*-
"""pic/templates/doupo_style.py —— 斗破四张卡共用的字号 / 字重规范。

2026-11-14 用户要求：「字体问题不只是斗技这一个卡片，所有的斗破游戏卡片都要进行优化」
——把斗技卡验证过的做法推广到人物卡、菜单卡、通用列表卡，并收敛成**单一规范**，
避免四张卡各写一套、过几天又漂移回小字。

为什么需要这一层（两个已踩过的坑）
--------------------------------
1. ``Canvas.text(role="bold")`` **本身不会加粗**：role 只推断 kind，字重由
   ``bold`` 参数 / 主题 ``*_bold`` 开关决定，默认全 False。
2. 容器 vendored ``msyh.ttc`` 只有两个 Regular face（见 fonts.py）。历史上画布层
   靠 1px 同色伪粗描边补，但 **<22px 的小字加描边会把中文笔画缝隙填满**
   （实测「已圆满」糊成一团），且即使拿到真粗体再叠描边也会出双影、发糊。
   所以 ``canvas.py`` 规定 <22px 不伪粗、**真粗体一律不伪粗**。

2026-11-15 修订：``fonts.py::_BUNDLED_BOLD`` 已内置等线 Bold 子集（真粗体 face），
``font_path("bold")`` 优先返回它；容器不再缺真粗体，斗破四张卡的加粗字全部走
同一个字形文件，模糊/重影的根因消除。伪粗描边退化为「连真粗体都加载失败」时的兜底。

因此本模块的规则：
  * 重点文字字号 >= MIN_BOLD_FS(22)，显式 ``bold=True`` + 强调色；
  * 次要文字显式 ``bold=False``，只靠字号与颜色分层，绝不糊；
  * 绘制与度量必须用同一个 role（"bold" / "regular"），否则右对齐会溢出；
  * 换字体文件后 ``pic.render`` 缓存 key 会随字体 size/mtime 变化自动失效。

四张卡：doupo.py（人物）/ doupo_menu.py（菜单）/ doupo_list.py（通用列表）/
doupo_skills.py（斗技，已按同一规范实现）。
"""
from __future__ import annotations

from ..canvas import Canvas

#: 与 canvas.py 的伪粗阈值一致；低于此字号不加粗，只放大。
MIN_BOLD_FS = 22

# ---- 字号阶梯（越大越重点）----
FS_HERO = 30           # 一级：等级 / 境界 / 伤害 / 大额数值
FS_KEY = 24            # 二级：技能名 / 口令 / 商品名 / 价格
FS_BODY = 20           # 正文
FS_SUB = 17            # 说明文字（清晰下限）
FS_LABEL = 16          # 小标签


def fit(text: str, max_w: int, size: int, role: str = "regular") -> str:
    """按最大宽度截断加省略号（度量与绘制同 role）。"""
    text = str(text)
    if max_w <= 0:
        return ""
    if Canvas.text_width(text, size, role) <= max_w:
        return text
    s = text
    while s and Canvas.text_width(s + "…", size, role) > max_w:
        s = s[:-1]
    return (s + "…") if s else ""


def key(c: Canvas, pos, text, *, size: int = FS_KEY, color: str,
        anchor: str = "lm", max_w: int | None = None) -> Canvas:
    """重点文字：放大 + 真粗体（>=22px 才加粗，小字自动降级为清晰常规字）。"""
    bold = int(size) >= MIN_BOLD_FS
    role = "bold" if bold else "regular"
    txt = fit(text, max_w, size, role) if max_w is not None else str(text)
    return c.text(pos, txt, size=int(size), color=color, role=role,
                  bold=bold, anchor=anchor)


def small(c: Canvas, pos, text, *, size: int = FS_SUB, color: str,
          anchor: str = "lm", max_w: int | None = None) -> Canvas:
    """次要文字：显式中等字号、明确不加粗，保证笔画清晰不粘连。"""
    txt = fit(text, max_w, size, "regular") if max_w is not None else str(text)
    return c.text(pos, txt, size=int(size), color=color, role="regular",
                  bold=False, anchor=anchor)


#: 中文避头标点：这些字符不许落在行首（「。」单独占一行是明显排版瑕疵）
HEAD_PUNCT = "，。、；：！？）」』】》%·"
#: 避头预留宽度：15px 字一个全角标点约 15px，留 18 保证回挪后不超原宽
HEAD_RESERVE = 18


def wrap_cjk(text: str, size: int, role: str, max_w: int) -> list[str]:
    """``Canvas.wrap`` + 中文避头，折行后每行仍不超过 ``max_w``。

    做法：先按 ``max_w - HEAD_RESERVE`` 折行（预留一个标点宽），再把行首标点
    回挪到上一行末；回挪前用 ``text_width`` 实测确认不超 ``max_w``，超了就
    放弃回挪（宁可有标点在行首，也不能压到卡片右内边距）。

    为什么不用 ``Canvas.wrap`` 直接折：它按字符宽度累加，标点会自然落到行首
    （实测「。造成 95% 伤害」这种断句）。改 Canvas.wrap 会影响所有卡片，
    所以只在斗破的长描述里用这个包装。
    """
    s = str(text or "")
    if max_w <= HEAD_RESERVE * 2:
        return Canvas.wrap(s, size, role, max_w)
    lines = Canvas.wrap(s, size, role, max_w - HEAD_RESERVE)
    out: list[str] = []
    for ln in lines:
        while out and ln and ln[0] in HEAD_PUNCT:
            cand = out[-1] + ln[0]
            if Canvas.text_width(cand, size, role) > max_w:
                break
            out[-1] = cand
            ln = ln[1:]
        if ln:
            out.append(ln)
    return out
