# -*- coding: utf-8 -*-
"""pic/theme.py —— 双主题（深色霓虹 / 极简白底）+ 热切换。

设计要点（2026-09-07，用户决策「两套都要，做热切换」）：
* 主题 = **纯数据**（frozen dataclass 色板），渲染时按名字取，不含任何业务判断；
* **热切换靠「渲染时读取」而非「启动时绑定」**：`resolve(None)` 每次都重新读
  `Config.PIC_THEME`，所以管理页改配置 `setattr(Config, 'PIC_THEME', 'light')`
  后**立即生效，无需重启 bridge**（与项目现有 config_api._hot_apply 机制一致）。
* `auto` = 按时段自动（19:00~07:00 用深色，其余用浅色），契合 QQ 夜间使用习惯。
* 本模块**不 import nonebot、不 import 上级包**，可独立单测；Config 由调用方传入。

色板取值参考 2026 主流深色仪表盘配色（青+洋红霓虹渐变）与 Notion/Linear 极简风。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, fields, field
from datetime import datetime

#: 主题名常量
NEON = "neon"
LIGHT = "light"
AUTO = "auto"
THEME_NAMES = (NEON, LIGHT, AUTO, "brutalist")


@dataclass(frozen=True)
class Theme:
    """一套色板。所有颜色为 '#RRGGBB' 字符串，渲染时再转 RGB 元组。"""

    name: str
    # 背景与卡片
    bg: str
    card: str
    card_border: str
    card_shadow: str
    # 文字
    text: str
    text_sub: str
    text_weak: str
    # 主色（accent2 用于渐变尾端）
    accent: str
    accent2: str
    accent_soft: str  # 主色浅底（标签/徽章背景）
    # 进度条
    track: str
    fill_start: str
    fill_end: str
    # 语义色
    ok: str
    warn: str
    err: str
    # 分割线
    divider: str
    # 链接/昵称色（Warframe 市价卡卖家名）
    link: str = "#8B5CF6"

    # ---- 形状与排版（2026-09-07 新增：用户要求「只能调颜色太单调」）----
    # 全部**带默认值**：老主题 JSON 里没有这些字段时自动用内置值，向后兼容，
    # 不需要给存量主题做数据迁移（themes_store 校验层同样按"缺失即默认"处理）。
    radius: int = 22        # 卡片圆角（小控件按 0.36 比例派生）
    pad: int = 24           # 边距基准：卡片外边距=pad，内容内边距=pad*2
    width: int = 800        # 出图宽度（QQ 里 800 兼容性最好）
    font_scale: float = 1.0  # 字号缩放（0.8 紧凑 ~ 1.5 大字号）
    grad_dir: str = "h"     # 渐变方向：h 横向 / v 纵向 / diag 斜向
    border: int = 2         # 卡片描边宽度（0=无描边）
    shadow: int = 0         # 阴影强度 0~3（0=扁平）

    # ---- 装饰（2026-09-07 二次增强，对标 Warframe Rabbit 式出图：背景平铺图案 +
    #      角落大贴纸 + 双线描边。全部零依赖，icons 自绘几何图标即可当贴纸）----
    bg_pattern: str = "none"    # none / dots / grid / diagonal / stickers / image
    sticker: str = "star"       # 背景平铺贴纸图标（icons.ICONS 里选）
    sticker_alpha: int = 12     # 贴纸不透明度 3~40（%，越低越像水印）
    corner_sticker: bool = False  # 右下角大贴纸（旋转 + 半透明，压在最上层）
    double_border: bool = False   # 双线描边（外圈粗 + 内圈细，更精致）

    # ---- 图片资源（qq_bridge 本地存储，主题 JSON 只存文件名引用）----
    # 与几何图标解耦：背景平铺、角落大贴纸可各自用「上传/主项目生图」的图片，
    # 不再被迫共用同一个 sticker 图标（2026-09-07 用户增强）。
    bg_image: str = ""             # 背景图文件名（bg_pattern="image" 时使用）
    sticker_image: str = ""        # 背景平铺贴纸图文件名（bg_pattern="stickers" 优先于图标）
    corner_sticker_image: str = "" # 角落大贴纸图文件名（优先于角落图标）
    corner_sticker_icon: str = "star"  # 角落大贴纸用的图标（与背景贴纸解耦）

    # ---- 卡片背景填充（2026-09-14：不要每个部位都纯色，支持渐变/图片/裁切/适配）----
    card_fill_type: str = "solid"        # solid / gradient / image
    card_gradient_kind: str = "linear"  # linear / radial
    card_gradient_angle: int = 135      # 0~360
    card_image: str = ""                # 图片资源文件名
    card_image_fit: str = "cover"       # cover / contain / fill / tile
    card_image_pos: str = "center"      # 9-point 位置
    card_image_opacity: int = 100       # 0~100
    card_image_overlay: bool = True     # 是否叠加 card 底色
    card_image_crop_x: int = 0          # 裁切区域 %
    card_image_crop_y: int = 0
    card_image_crop_w: int = 100
    card_image_crop_h: int = 100
    card_alpha: int = 100               # 卡片底色不透明度 %（<100 露出背景图案/背景图）

    # ---- 文字样式（2026-09-15：Word 式字符样式，角色级 title/body/sub/badge）----
    font_family: str = ""               # 字体族名；空=系统回退链
    title_bold: bool = False
    title_italic: bool = False
    title_underline: bool = False
    title_strike: bool = False
    title_stroke: bool = False
    title_stroke_color: str = "#000000"
    body_bold: bool = False
    body_italic: bool = False
    body_underline: bool = False
    body_strike: bool = False
    body_stroke: bool = False
    body_stroke_color: str = "#000000"
    sub_bold: bool = False
    sub_italic: bool = False
    sub_underline: bool = False
    sub_strike: bool = False
    sub_stroke: bool = False
    sub_stroke_color: str = "#000000"
    badge_bold: bool = False
    badge_italic: bool = False
    badge_underline: bool = False
    badge_strike: bool = False
    badge_stroke: bool = False
    badge_stroke_color: str = "#000000"

    # ---- 页面背景图片增强（2026-09-14：等比缩放/框选部位/不透明度）----
    bg_image_fit: str = "tile"          # tile / cover / contain / fill
    bg_image_pos: str = "center"
    bg_image_opacity: int = 100
    bg_image_overlay: bool = False
    bg_image_crop_x: int = 0
    bg_image_crop_y: int = 0
    bg_image_crop_w: int = 100
    bg_image_crop_h: int = 100

    # ---- 气泡（2026-09-16：战甲卡技能/集团卡气泡；全部带默认值，主题可配）----
    bubble_fill_alpha: int = 13    # 技能气泡底色强度 %（主色混入卡片底色）
    bubble_border_alpha: int = 55  # 技能气泡描边强度 %
    bubble_radius: int = 12        # 气泡圆角
    bubble_border_width: int = 1   # 气泡描边宽度
    aug_color: str = ""            # 集团卡气泡/标题专用色；空=用警告色 warn
    aug_fill_alpha: int = 13       # 集团卡气泡底色强度 %
    aug_border_alpha: int = 55     # 集团卡气泡描边强度 %

    # ---- 固定元素定位（2026-09-17：卡片首部标题 / 尾部来源 可拖动调整，
    #      不随正文长短被挤出；百分比相对画布，字号独立控制）----
    tbl_header_alpha: int = 24   # 价格表/列表 表头行底色强度 %（跟随主色）
    tbl_bubble_alpha: int = 9    # 列表气泡底色强度 %（跟随主色）
    tbl_bubble_border_alpha: int = 38  # 列表气泡边框强度 %
    tbl_col_line_alpha: int = 26 # 列表列分隔线强度 %
    tbl_row_line_alpha: int = 16 # 列表行间分隔线强度 %
    title_fixed: bool = False    # 固定标题位置（覆盖层，不参与正文布局流）
    title_x: int = 48            # 标题左缘 px（距画布左）
    title_y: int = 44            # 标题顶部 px（距画布顶）
    title_fs: int = 22           # 标题字号 px
    source_fixed: bool = False   # 固定来源位置（覆盖层）
    source_x: int = 48           # 来源右缘 距画布右 px（右对齐）
    source_y: int = 52           # 来源顶部 距画布底 px
    source_fs: int = 12          # 来源字号 px

    # ---- 卡片专属背景覆盖（2026-09-23：每类模板可单独设背景，互不影响）----
    # 键 = 模板名（fissure/guide/menu…），值 = 背景参数子集（bg_pattern/bg_image/bg_image_*…）。
    # 渲染时 decor.background(tpl=...) 按模板名合并覆盖，缺失键回落主题级全局背景。
    # **仅显式配置才生效**：固定风格卡（裂隙/科研）无覆盖时保持原有观感（无创原则）。
    bg_overrides: dict = field(default_factory=dict)

    # ---- 派生量（只读属性，供 widgets/templates 使用）----
    @property
    def r_ctrl(self) -> int:
        """小控件圆角：按卡片圆角 36% 派生（内置值 22→8，符合圆角分级规范）。"""
        return max(0, int(round(self.radius * 0.36)))

    @property
    def pad_in(self) -> int:
        """内容内边距（卡片内的左右留白）= pad * 2（内置 24→48）。"""
        return int(self.pad * 2)

    def fs(self, base: int) -> int:
        """按 font_scale 缩放字号（最小 10px，避免缩到看不清）。"""
        return max(10, int(round(base * float(self.font_scale or 1.0))))

    def horizontal(self) -> bool:
        """渐变是否横向（仅有 icon/进度条类用到；'diag' 也按横向处理再旋转）。"""
        return str(self.grad_dir or "h").lower() in ("h", "diag", "horizontal")


#: 可编辑字段分组。**形状字段必须显式列出**：dataclass 里它们带默认值排在最末，
#: 若用「除 name 外全部」推导会把形状字段混进颜色校验（HEX 正则必然报错）。
LAYOUT_FIELDS: tuple[str, ...] = (
    "radius", "pad", "width", "font_scale", "grad_dir", "border", "shadow",
    "bg_pattern", "sticker", "sticker_alpha", "corner_sticker", "double_border",
    "bg_image", "sticker_image", "corner_sticker_image", "corner_sticker_icon",
    # ---- 卡片背景填充（纯色/渐变/图片 + 图片裁切/适配/不透明度）----
    "card_fill_type", "card_gradient_kind", "card_gradient_angle", "card_image",
    "card_image_fit", "card_image_pos", "card_image_opacity", "card_image_overlay",
    "card_image_crop_x", "card_image_crop_y", "card_image_crop_w", "card_image_crop_h",
    "card_alpha",
    # ---- 页面背景图片增强（适配/裁切/不透明度）----
    "bg_image_fit", "bg_image_pos", "bg_image_opacity", "bg_image_overlay",
    "bg_image_crop_x", "bg_image_crop_y", "bg_image_crop_w", "bg_image_crop_h",
    # ---- 文字样式（角色级，2026-09-15）----
    "font_family",
    *[f"{r}_{k}" for r in ("title", "body", "sub", "badge")
      for k in ("bold", "italic", "underline", "strike", "stroke", "stroke_color")],
    # ---- 气泡（2026-09-16：战甲卡技能/集团卡气泡）----
    "bubble_fill_alpha", "bubble_border_alpha", "bubble_radius",
    "bubble_border_width", "aug_color", "aug_fill_alpha", "aug_border_alpha",
    # ---- 表格（2026-09-17：表头/列表/分隔线 强度不再写死）----
    "tbl_header_alpha", "tbl_bubble_alpha", "tbl_bubble_border_alpha",
    "tbl_col_line_alpha", "tbl_row_line_alpha",
    # ---- 固定元素定位（2026-09-17：标题/来源 拖动调整）----
    "title_fixed", "title_x", "title_y", "title_fs",
    "source_fixed", "source_x", "source_y", "source_fs",
    "bg_overrides",
)
COLOR_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(Theme) if f.name != "name" and f.name not in LAYOUT_FIELDS
)


NEON_THEME = Theme(
    name=NEON,
    bg="#0B0E17",
    card="#141A2B",
    card_border="#232C45",
    card_shadow="#000000",
    text="#EAF0FF",
    text_sub="#A7B4CE",
    text_weak="#6E7C99",
    accent="#00E5FF",
    accent2="#FF4FD8",
    accent_soft="#0E2A38",
    track="#1E2740",
    fill_start="#00E5FF",
    fill_end="#FF4FD8",
    ok="#3DDC97",
    warn="#FFB020",
    err="#FF5C7A",
    link="#A78BFA",
    divider="#232C45",
    # 深色霓虹：圆点阵背景 + 闪电角落贴纸（克制的点缀）
    bg_pattern="dots",
    sticker="bolt",
    corner_sticker=True,
    double_border=False,
)

LIGHT_THEME = Theme(
    name=LIGHT,
    bg="#FFFFFF",
    card="#F5F7FA",
    card_border="#E3E8EF",
    card_shadow="#0F172A0D",
    text="#1A1A1A",
    text_sub="#4B5563",
    text_weak="#9CA3AF",
    accent="#2563EB",
    accent2="#60A5FA",
    # 浅色主题下图标徽章底色必须比卡片(#F5F7FA)明显更深，否则徽章几乎看不见
    # （原 #E8F0FE 与卡片色差仅 24，低于可辨识阈值，2026-09-07 实测踩到）
    accent_soft="#D7E4FC",
    track="#E5E7EB",
    fill_start="#2563EB",
    fill_end="#60A5FA",
    ok="#16A34A",
    warn="#D97706",
    err="#DC2626",
    link="#8B5CF6",
    divider="#E3E8EF",
    # 浅色主题对标 Warframe Rabbit 出图：贴纸平铺背景 + 双线描边（精致卡片感）
    bg_pattern="stickers",
    sticker="wand",
    corner_sticker=True,
    double_border=True,
)

BRUTALIST_THEME = Theme(
    name="brutalist",
    bg="#1b1d22",          # 深灰底（贴近用户指定的 #1f2228 粗野主义）
    card="#23262d",        # 面板略亮，分层
    card_border="#3a3f48", # 锐利细边
    card_shadow="#000000",
    text="#ffffff",        # 纯白字
    text_sub="#aab0b8",    # 灰
    text_weak="#6b7178",   # 更暗
    accent="#ffffff",      # 主色=白（冷峻）；等宽数字即"强调"
    accent2="#9aa0a6",
    accent_soft="#2c313a",
    track="#2a2e36",       # 轨道/进度底
    fill_start="#ffffff",
    fill_end="#ffffff",
    ok="#4ade80",          # 终端绿（仅用于 +N / 成功，克制）
    warn="#e8b04b",
    err="#ff5c7a",
    link="#6ea8fe",
    divider="#2f343c",
    radius=0,              # 锐利直角（粗野主义核心）
    border=1,
    shadow=0,
    bg_pattern="none",     # 干净无花哨水印
    corner_sticker=False,
    double_border=False,
    width=800,
    font_scale=1.0,
)

_THEMES: dict[str, Theme] = {NEON: NEON_THEME, LIGHT: LIGHT_THEME, "brutalist": BRUTALIST_THEME}

# auto 模式：>=19 点或 <7 点用深色（夜间霓虹），其余用浅色
_AUTO_DARK_FROM_HOUR = 19
_AUTO_DARK_TO_HOUR = 7


def get(name: str) -> Theme:
    """按名字取主题；未知名字一律回落深色（不抛异常）。"""
    return _THEMES.get((name or "").strip().lower(), NEON_THEME)


def resolve_auto(now: datetime | None = None) -> Theme:
    """auto 主题解析：夜间深色、白天浅色。"""
    h = (now or datetime.now()).hour
    if h >= _AUTO_DARK_FROM_HOUR or h < _AUTO_DARK_TO_HOUR:
        return NEON_THEME
    return LIGHT_THEME


#: 卡片主题绑定存储：data/pic_card_themes.json（{binds: {卡片名: 主题名}}）。
#: 每张业务卡片（市价/裂隙/战甲资料…）可单独绑定主题；空 = 跟随全局。
_CARD_THEMES_PATH: str = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "data", "pic_card_themes.json"))


def card_theme(card: str | None) -> str:
    """卡片主题绑定：card → 主题名；无绑定返回 ''（跟随全局）。

    每次渲染读盘（文件几 KB），改绑定即刻生效，无需重启/清缓存。
    """
    if not card:
        return ""
    try:
        with open(_CARD_THEMES_PATH, encoding="utf-8") as f:
            d = json.load(f)
        binds = d.get("binds") if isinstance(d, dict) else None
        if isinstance(binds, dict):
            return str(binds.get(card) or "").strip().lower()
    except Exception:
        pass
    return ""


#: 激活态兜底持久化：data/pic_active_theme.json（{"name", "updated_at"}）。
#: 为什么还要单独一个文件（2026-09-24 修复「重启后主题变回默认」）：
#:   PIC_CUSTOM_THEME 只存在 .env 里，而 .env 有三类失效场景——① 进程启动时
#:   dotenv 没加载（换启动方式/cwd）；② 键被其它写 .env 的通道覆盖成空串；
#:   ③ .env 写失败（占用/只读）。任一条命中都会让「应用过的主题」丢掉。
#: 因此**双写双通道**：编辑器「应用/取消」同时写 .env 与本文件；解析时 env 通道
#: 拿不到值就回到本文件。路径与 _CARD_THEMES_PATH 同源，由 pic 包自己读写。
_ACTIVE_THEME_PATH: str = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..",
    "data", "pic_active_theme.json"))


def read_active_theme() -> str:
    """读兜底文件记录的激活主题名；不存在/损坏/非法一律返回 ''（不抛）。"""
    try:
        with open(_ACTIVE_THEME_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            v = d.get("name")
            # 只认字符串：手改坏/类型不对一律当没配，绝不把 123 / {} 当主题名
            return str(v).strip().lower() if isinstance(v, str) else ""
    except Exception:
        pass
    return ""


def write_active_theme(name: str) -> bool:
    """写兜底文件（空串 = 清除激活态）。成功 True；失败返回 False，绝不抛。

    与 _CARD_THEMES_PATH 同样用「临时文件 + os.replace」原子替换，
    避免半截写入把激活态写成空。
    """
    try:
        os.makedirs(os.path.dirname(_ACTIVE_THEME_PATH), exist_ok=True)
        data = {"name": str(name or "").strip().lower(), "updated_at": time.time()}
        tmp = _ACTIVE_THEME_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _ACTIVE_THEME_PATH)
        return True
    except Exception:
        return False


def active_custom_theme() -> str:
    """当前激活的**自定义**主题名（编辑器「应用」写入的那个）；空串 = 未激活。

    与 `_config_theme()` 的区别：本函数**不回落** PIC_THEME（那是全局默认主题，
    不是"自定义激活态"），供编辑器列表/绑定页显示与判断用。
    """
    try:
        from ..config import Config

        custom = str(getattr(Config, "PIC_CUSTOM_THEME", "") or "").strip().lower()
        if custom:
            return custom
    except Exception:
        pass
    # 仅在 env 通道**完全没有**这个键时兜底：键存在但为空 = 用户主动取消，必须尊重
    if "PIC_CUSTOM_THEME" not in os.environ:
        return read_active_theme()
    return ""


def resolve(name: str | None = None, card: str | None = None, now: datetime | None = None) -> Theme:
    """解析主题。

    解析优先级：
      1. 显式传入的 `name`（会话级 sess.pic_theme 会走这里）
      2. **卡片级绑定**（`card` 参数 + 未显式传 name 时；如「星际战甲·市价」→ cs1）
      3. 否则读 `Config.PIC_CUSTOM_THEME`（用户在主题编辑器里**激活**的自定义主题）
      4. 否则读 `Config.PIC_THEME`（全局默认）
      5. 内置 neon / light / auto 命中直接返回
      6. 自定义主题（`pic/themes_store.py` 文件系统）命中返回
      7. 全部失败回落 neon

    :param name: 主题名（neon/light/auto/任意自定义名）。**传 None 时从 Config 读**，
                 这是热切换的关键：每次渲染都重新读，改配置即刻生效。
    :param card: 业务卡片名（如 "星际战甲·市价"）。**仅当 name 为空时生效**——
                 用户显式为会话选的主题优先于卡片绑定。
    :param now:  仅 auto 模式使用，便于测试注入固定时间。
    """
    # 卡片绑定优先于全局配置，但次于显式传入的 name（会话级临时选择）
    if card and not name:
        _bound = card_theme(card)
        if _bound:
            name = _bound
    if not name:
        name = _config_theme()
    name = (name or NEON).strip().lower()
    if name == AUTO:
        return resolve_auto(now)
    if name in _THEMES:
        return _THEMES[name]
    # 自定义主题（themes_store.py，单文件存储）
    custom = _load_custom(name)
    if custom:
        return Theme(name=name, **custom)
    return NEON_THEME


def _load_custom(name: str) -> dict | None:
    """从文件系统加载用户自定义主题的**颜色 + 形状**字段，合并成一个 dict。

    返回 None 表示主题不存在（→ 回落 neon）。形状字段缺失时不塞进 dict，
    交给 Theme 的默认值兜底，老主题无需迁移。

    主题包**不强制**依赖 themes_store（独立单测时拿不到 Config/QQ 包时也能跑），
    所以这里 try 包住，IO 失败一律返回 None。
    """
    try:
        from .themes_store import theme_store

        d = theme_store.get(name)
        if not d:
            return None
        out: dict = {}
        c = d.get("colors")
        if isinstance(c, dict):
            out.update(c)
        lay = d.get("layout")
        if isinstance(lay, dict):
            out.update(lay)
        return out or None
    except Exception:
        return None


def _config_theme() -> str:
    """运行时读取全局主题（**优先级：PIC_CUSTOM_THEME > PIC_THEME**）。

    自定义主题被用户在编辑器点「应用」时，会写 `Config.PIC_CUSTOM_THEME = "{name}"`；
    此后所有未指定主题名的渲染都走用户的自定义色板。
    关掉自定义主题 = `Config.PIC_CUSTOM_THEME = ""`，自然回落 `PIC_THEME`。

    相对导入在独立单测场景下会失败（上级包 __init__ 会拉 nonebot），
    因此全部包在 try 里，失败即用默认值，保证 pic 包永远可用。
    """
    try:
        from ..config import Config

        custom = str(getattr(Config, "PIC_CUSTOM_THEME", "") or "").strip()
        if custom:
            return custom.lower()
        # 兜底：env 通道没有这个键（.env 未加载 / 键被改写丢）→ 用 pic 自己的
        # 持久文件恢复。键存在但为空 = 用户主动取消，不覆盖（尊重配置页的显式清空）。
        if "PIC_CUSTOM_THEME" not in os.environ:
            saved = read_active_theme()
            if saved:
                return saved
        return str(getattr(Config, "PIC_THEME", "") or "").strip().lower()
    except Exception:
        return ""


def rgb(value: str) -> tuple[int, int, int]:
    """'#RRGGBB' → (r, g, b)。支持 8 位 #RRGGBBAA（忽略 alpha）。"""
    s = (value or "").strip().lstrip("#")
    if len(s) >= 6:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    return (0, 0, 0)


def rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    r, g, b = rgb(value)
    return (r, g, b, alpha)
