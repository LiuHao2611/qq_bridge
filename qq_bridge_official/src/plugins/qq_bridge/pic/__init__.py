# -*- coding: utf-8 -*-
"""pic —— 机器人自绘图片能力（菜单图 / 进度条图 / 配额图）。

用法（业务层）：
    from ..pic import render, PicRenderError
    try:
        data = await render("progress", percent=42, elapsed=83, kind="t2i")
        await send_pic(ctx, data)
    except PicRenderError:
        await _say(ctx, 纯文本兜底)

铁律：
  * 只依赖 Pillow（2026-09-07 选型，Python 图像领域主流），不引浏览器/渲染引擎；
  * 全部代码、字体、缓存都在 qq_bridge/ 内，**不 import 主项目任何模块**；
  * 绘制是同步 CPU 操作 → `render()` 内部用 `asyncio.to_thread`，**不阻塞事件循环**；
  * 渲染失败抛 PicRenderError，由调用方降级为纯文本（CLOSE-02 禁止静默失败）。

主题：`neon` / `light` / `auto`，运行时读 Config.PIC_THEME → **改配置即热生效**。
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import canvas as _canvas  # noqa: F401
from . import icons as _icons  # noqa: F401
from . import widgets as _widgets  # noqa: F401
from . import fonts as _fonts  # noqa: F401
from . import theme as _theme_pkg  # noqa: F401
from . import card_binds  # 卡片主题内置默认绑定（配置优先，随代码交付）
from .cache import PicCache, make_key, pic_cache
from .canvas import Canvas
from .templates import (menu as _menu, progress as _progress, quota as _quota, text as _text, guide as _guide, hw as _hw, riven as _riven, riven_analysis as _riven_analysis, riven_recommend as _riven_recommend, netracell as _netracell, fissure as _fissure, bounty as _bounty, tenet as _tenet, coda as _coda, incarnon as _incarnon, arbitration_table as _arbitration_table, xiuxian as _xiuxian, xiuxian_menu as _xiuxian_menu, xiuxian_help as _xiuxian_help, doupo as _doupo, doupo_menu as _doupo_menu, doupo_list as _doupo_list, aimenu as _aimenu, checkin as _checkin, plain as _plain, alerts as _alerts, task_alert as _task_alert, archon as _archon, riven_trend as _riven_trend, riven_fusion as _riven_fusion, riven_full as _riven_full)  # noqa: F401
from .theme import AUTO, LIGHT, NEON, Theme, resolve as resolve_theme

__all__ = [
    "Canvas", "canvas", "fonts", "icons", "widgets", "theme",
    "Theme", "NEON", "LIGHT", "AUTO", "resolve_theme",
    "render", "render_sync", "render_raw", "list_templates", "PicRenderError",
    "pic_cache", "PicCache", "make_key",
]


class PicRenderError(Exception):
    """渲染失败（未知模板 / 绘制异常）。调用方必须捕获并降级为纯文本。"""


_log = logging.getLogger("qq_bridge.pic")


def _resolve_card_theme_name(theme_name, card):
    """计算最终 theme_name：显式 > 配置绑定 > 内置默认（card_binds）> 原值。

    内置默认绑定随代码交付（绕过 ``data/`` 被 gitignore 导致的不交付问题）；
    配置（``theme.card_theme``）优先，用户可随时改回深色。
    """
    return card_binds.resolve_card_theme(theme_name, card, _theme_pkg.card_theme)


#: 模板注册表：新增模板请在此登记
_TEMPLATES = {
    "menu": _menu.build,
    "progress": _progress.build,
    "quota": _quota.build,
    "text": _text.build,  # 短回复文本卡（替代纯文本确认）
    "guide": _guide.build,  # 攻略长图卡（游戏工具箱：头像 + 分区标题 + 长正文）
    "hw": _hw.build,  # 本地硬件占用图（环形色块：绿=空闲/黄=中等/红=高占用）
    "riven": _riven.build,  # 紫卡样式卡（模拟开卡，还原游戏内紫卡观感）
    "riven_analysis": _riven_analysis.build,  # 紫卡分析卡（逐像素复刻 Rabbit 参考卡）
    "riven_recommend": _riven_recommend.build,  # 紫卡洗卡词条推荐卡（击杀效率 + 市场挂牌分层双维度）
    "netracell": _netracell.build,  # 科研任务专属卡（浅色白卡+蓝色气泡，照 Rabbit）
    "fissure": _fissure.build,  # 虚空裂隙专属卡（速览/普通/钢铁，蓝气泡+橙倒计时）
    "bounty": _bounty.build,  # 赏金任务一览卡（Rabbit 版式：轮换 pills + 图标 + 立绘）
    "tenet": _tenet.build,  # 信条武器卡：单列粉紫 Rabbit 版式（改动前原版，真实武器图标缺图 raise）
    "coda": _coda.build,    # 终幕武器卡：800×998 参考图两列版式；与 tenet 完全独立，禁止共用 build
    "incarnon": _incarnon.build,  # 灵化战甲及武器周常卡（Rabbit 版式：战甲3卡+武器5卡+9×5轮回表）
    "arbitration_table": _arbitration_table.build,  # 仲裁时间表卡（Rabbit 版式：5列循环班次表）
    "xiuxian": _xiuxian.build,  # 文字修仙角色状态卡（境界进度条 + 四宫格数值）
    "xiuxian_menu": _xiuxian_menu.build,  # 文字修仙口令菜单卡（分组 + 两列清单）
    "xiuxian_help": _xiuxian_help.build,  # 文字修仙玩法细则卡（单列折行要点）
    "doupo": _doupo.build,  # 斗破人物状态卡（境界进度条 + 六维 + 气血/斗气）
    "doupo_menu": _doupo_menu.build,  # 斗破口令菜单卡（分组 + 两列清单）
    "doupo_list": _doupo_list.build,  # 斗破通用列表卡（斗技/异火/宝库/背包/排行）
    "aimenu": _aimenu.build,  # 指令总览双列加长图（覆盖全部命令族，一张图）
    "checkin": _checkin.build,  # 每日签到卡（连续/累计/喵币 + 里程碑进度 + 本月日历）
    "plain": _plain.build,  # 平原状态和时间卡（Rabbit 版式：白卡+四组状态/倒计时胶囊）
    "alerts": _alerts.build,  # 警报任务卡（Rabbit 版式：派系胶囊+节点+倒计时胶囊+奖励/现金）
    "task_alert": _task_alert.build,  # 任务订阅命中卡（单条裂隙任务，card-design 度量优先版式）
    "archon": _archon.build,  # 本周执刑官猎杀卡（Rabbit 参考图逐像素底图 + 动态信息重绘）
    "riven_trend": _riven_trend.build,  # 紫卡趋势 / 词条价差卡（WFM 真实挂牌 + 本地采样）
    "riven_fusion": _riven_fusion.build,  # 紫卡融合卡（Riven Fusion 机制 + 费用表 + 真实中位价）
    "riven_full": _riven_full.build,  # 紫卡全面分析卡（655×1920 像素级复刻 Rabbit 完整分析卡）
}

#: 模板显示名（编辑器预览 tab 用中文，避免英文 key 直接暴露给用户）
TEMPLATE_LABELS: dict[str, str] = {
    "menu": "菜单",
    "progress": "进度条",
    "quota": "配额",
    "text": "文本卡",
    "guide": "攻略卡",
    "hw": "硬件卡",
    "riven": "紫卡卡",
    "riven_analysis": "紫卡分析卡",
    "riven_recommend": "紫卡推荐卡",
    "fissure": "虚空裂隙卡",
    "netracell": "科研任务卡",
    "bounty": "赏金任务卡",
    "tenet": "信条武器卡",
    "coda": "终幕武器卡",
    "incarnon": "灵化周常卡",
    "arbitration_table": "仲裁时间表卡",
    "xiuxian": "修仙状态卡",
    "xiuxian_menu": "修仙菜单卡",
    "xiuxian_help": "修仙玩法卡",
    "doupo": "斗破人物卡",
    "doupo_menu": "斗破菜单卡",
    "doupo_list": "斗破列表卡",
    "aimenu": "指令总览卡",
    "checkin": "签到卡",
    "plain": "平原状态卡",
    "alerts": "警报任务卡",
    "task_alert": "任务订阅提醒卡",
    "archon": "执刑官猎杀卡",
    "riven_trend": "紫卡趋势卡",
    "riven_fusion": "紫卡融合卡",
    "riven_full": "紫卡全面分析卡",
}


def list_templates() -> tuple:
    return tuple(sorted(_TEMPLATES.keys()))


def _config() -> dict:
    """读 Config 的图片相关配置；取不到时用默认值（保证 pic 可独立运行/单测）。"""
    try:
        from ..config import Config

        return {
            "enable": bool(getattr(Config, "PIC_ENABLE", True)),
            "theme": str(getattr(Config, "PIC_THEME", "") or ""),
            "width": int(getattr(Config, "PIC_MAX_WIDTH", 800) or 800),
            "ttl": int(getattr(Config, "PIC_CACHE_TTL", 300) or 300),
            "max_files": int(getattr(Config, "PIC_CACHE_MAX", 200) or 200),
        }
    except Exception:
        return {"enable": True, "theme": "", "width": 800, "ttl": 300, "max_files": 200}


def enabled() -> bool:
    """自绘图片总开关（关闭时调用方应直接走纯文本）。"""
    return _config()["enable"]


def render_sync(template: str, theme_name: str | None = None, card: str | None = None, **params) -> bytes:
    """同步渲染（**纯 CPU，勿在事件循环里直接调用**，请用 `render()`）。

    :param card: 业务卡片名（如 "星际战甲·市价"），命中卡片级主题绑定。
    """
    name = (template or "").strip().lower()
    fn = _TEMPLATES.get(name)
    if fn is None:
        raise PicRenderError("未知模板：%s（可用：%s）" % (template, ", ".join(list_templates())))
    cfg = _config()
    # 同 render()：传 None 让 resolve() 走完整链路（卡片绑定 > PIC_CUSTOM_THEME > PIC_THEME > 内置）
    t = resolve_theme(_resolve_card_theme_name(theme_name, card), card)
    params.setdefault("width", cfg["width"])
    try:
        c = fn(t, **params)
        return c.to_bytes()
    except PicRenderError:
        raise
    except Exception as e:  # 不吞异常：包一层语义错误，便于调用方统一降级
        raise PicRenderError("%s 渲染失败：%s: %s" % (name, type(e).__name__, e)) from e


def render_raw(template: str, theme_obj: Theme, **params) -> bytes:
    """**绕过主题解析**，直接用传入的 Theme 对象渲染（用于编辑器实时预览临时主题）。

    与 `render()` 的区别：
      * `render()` 按 name 走 resolve（neon/light/auto/自定义名）→ 命中 `themes_store`；
      * `render_raw()` 拿用户**正在拖动拾色器但还没保存**的临时 Theme 渲染一张预览图，
        不会去读磁盘上的主题文件（编辑中途保存会很频繁，每次都写盘无意义）。
    """
    name = (template or "").strip().lower()
    fn = _TEMPLATES.get(name)
    if fn is None:
        raise PicRenderError("未知模板：%s（可用：%s）" % (template, ", ".join(list_templates())))
    try:
        c = fn(theme_obj, **params)
        return c.to_bytes()
    except PicRenderError:
        raise
    except Exception as e:
        raise PicRenderError("%s 渲染失败：%s: %s" % (name, type(e).__name__, e)) from e


async def render(template: str, theme_name: str | None = None, card: str | None = None, use_cache: bool = True, **params) -> bytes:
    """异步渲染（内部 to_thread，不阻塞事件循环）。带磁盘缓存。

    :param template: 模板名 menu / progress / quota
    :param theme_name: 主题名；None=读 Config.PIC_THEME（支持 auto）
    :param card: 业务卡片名（如 "星际战甲·市价"），命中卡片级主题绑定
    :param use_cache: 是否走缓存（同模板+同主题+同参数复用产物）
    """
    cfg = _config()
    # 解析后的完整主题对象：缓存 key 必须包含它的"全部字段签名"，
    # 否则同名主题改了颜色/装饰后（声名不变）会命中旧缓存 → 用户看到的是旧样式（2026-09-07 修复）。
    # 关键：theme_name 为空时必须传 None（而非 cfg["theme"]），让 resolve() 走完整链路：
    #   卡片绑定 > PIC_CUSTOM_THEME（编辑器「应用」激活的自定义主题）> PIC_THEME > 内置
    # 若传 cfg["theme"] 会短路自定义主题（2026-09-07 修复）。
    # 内置默认绑定（card_binds）在「未显式指定 + 配置未绑定」时兜底（如市价卡浅色版）。
    t_obj = resolve_theme(_resolve_card_theme_name(theme_name, card), card)
    resolved = t_obj.name
    try:
        import hashlib, json as _json
        from dataclasses import asdict

        sig = hashlib.sha256(
            _json.dumps(asdict(t_obj), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:8]
    except Exception:
        sig = ""
    key = make_key((template or "").strip().lower() + "|c=" + str(card or ""),
                   resolved + ("@" + sig if sig else ""), params)

    _t0 = time.perf_counter()
    if use_cache:
        pic_cache.ttl = cfg["ttl"]
        pic_cache.max_files = cfg["max_files"]
        hit = await asyncio.to_thread(pic_cache.get, key)
        if hit:
            _log.info("pic_render template=%s cache=hit render_ms=%.1f bytes=%d",
                      (template or "").strip().lower(), (time.perf_counter() - _t0) * 1000, len(hit))
            return hit

    _tr = time.perf_counter()
    data = await asyncio.to_thread(render_sync, template, theme_name, card, **params)
    render_ms = (time.perf_counter() - _tr) * 1000

    if use_cache and data:
        await asyncio.to_thread(pic_cache.put, key, data)
    _log.info("pic_render template=%s cache=miss render_ms=%.1f bytes=%d total_ms=%.1f",
              (template or "").strip().lower(), render_ms, len(data or b""),
              (time.perf_counter() - _t0) * 1000)
    return data
