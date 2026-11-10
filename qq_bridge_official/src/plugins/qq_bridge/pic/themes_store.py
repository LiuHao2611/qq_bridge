# -*- coding: utf-8 -*-
"""pic/themes_store.py —— 用户自定义主题的 JSON 持久化层（2026-09-07 新增）。

设计要点：
* **零依赖**：纯 json + os，无 SQLite/无三方库（自研 pic 包的红线——只引 Pillow）；
* **每主题一文件**：`qq_bridge/data/themes/{name}.json`，便于用户直接编辑/备份/分享；
* **字段强校验**：13 个 Theme 颜色字段缺一不可、必须 #RRGGBB —— 渲染时 Theme 是 frozen dataclass，
  缺字段会在 dataclass 构造时崩溃，事先校验可定位到字段名；
* **名字规范化**：自动 strip + 转小写，文件名做白名单防路径穿越；
* **激活态持久化**：激活主题写在 `Config.PIC_CUSTOM_THEME`（内存热生效）并落盘 .env 同键，
  （2026-09-16：仅内存会导致重启后记忆丢失；一键止血 → deactivate 清空即回 PIC_THEME）。

为什么不用 sqlite：单文件 + 几 KB 体量、读写原子化（tmp → rename）足够；引入 sqlite
反而要管连接/migration/版本，**对一个最多几十条记录的目录得不偿失**（2026-09-07 评估）。
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import fields

from .theme import COLOR_FIELDS, NEON_THEME, LIGHT_THEME, Theme

try:  # 贴纸图标候选（icons 是纯几何自绘，Pillow 为 pic 包硬依赖，运行时必有）
    from .icons import ICONS as _ICON_NAMES
except Exception:  # pragma: no cover - 脱离包环境时降级为空
    _ICON_NAMES = ()

#: 可被用户编辑的**颜色**字段（17 个）。
#: ⚠️ 不能写「除 name 外全部」：Theme 现已含形状字段（int/float），
#: 混入后 HEX 校验必然失败、预设里也会多出整数键，故统一用 theme.COLOR_FIELDS。
THEME_FIELDS: tuple[str, ...] = COLOR_FIELDS

#: 形状/排版字段的可编辑定义（编辑器据此渲染滑块/下拉，后端据此校验）。
#: 设计参考 tweakcn（Apache-2.0，2026 主流 shadcn 主题编辑器）的「颜色 + Radius 分栏」，
#: 把「只能调颜色」扩展为「颜色 + 形状 + 排版」三类。
LAYOUT_SPEC: dict[str, dict] = {
    "radius":     {"label": "卡片圆角", "type": "int",   "min": 0,   "max": 40,  "def": 22,  "unit": "px", "group": "形状"},
    "pad":        {"label": "边距基准", "type": "int",   "min": 8,   "max": 64,  "def": 24,  "unit": "px", "group": "形状"},
    "width":      {"label": "出图宽度", "type": "int",   "min": 360, "max": 1200,"def": 800, "unit": "px", "group": "形状", "step": 8},
    "font_scale": {"label": "字号缩放", "type": "float", "min": 0.8, "max": 1.5, "def": 1.0, "unit": "×",  "group": "排版", "step": 0.05},
    "grad_dir":   {"label": "渐变方向", "type": "enum",  "def": "h", "group": "排版",
                   "options": [{"value": "h", "label": "横向"}, {"value": "v", "label": "纵向"},
                               {"value": "diag", "label": "斜向"}]},
    "border":     {"label": "卡片描边", "type": "int",   "min": 0,   "max": 6,   "def": 2,   "unit": "px", "group": "形状"},
    "shadow":     {"label": "阴影强度", "type": "int",   "min": 0,   "max": 3,   "def": 0,   "unit": "级", "group": "形状"},

    # ---- 装饰（2026-09-07 二次增强：背景平铺图案 / 角落大贴纸 / 双线描边 / 图片资源）----
    "bg_pattern":   {"label": "背景图案", "type": "enum", "def": "none", "group": "装饰",
                     "options": [{"value": "none", "label": "无"}, {"value": "dots", "label": "圆点阵"},
                                 {"value": "grid", "label": "网格线"}, {"value": "diagonal", "label": "斜纹"},
                                 {"value": "stickers", "label": "贴纸平铺"},
                                 {"value": "image", "label": "图片背景"}]},
    "sticker":      {"label": "背景贴纸图标", "type": "enum", "def": "star", "group": "装饰",
                     "options": [{"value": k, "label": k} for k in _ICON_NAMES]},
    "sticker_alpha": {"label": "贴纸透明度", "type": "int", "min": 3, "max": 40, "def": 12, "unit": "%", "group": "装饰"},
    "corner_sticker": {"label": "角落大贴纸", "type": "bool", "def": False, "group": "装饰"},
    "corner_sticker_icon": {"label": "角落贴纸图标", "type": "enum", "def": "star", "group": "装饰",
                     "options": [{"value": k, "label": k} for k in _ICON_NAMES]},
    "double_border": {"label": "双线描边", "type": "bool", "def": False, "group": "装饰"},

    # ---- 图片资源（上传 / 主项目生图生成；主题 JSON 只存文件名）----
    "bg_image":            {"label": "背景图片", "type": "str", "def": "", "group": "背景填充",
                            "hint": "上传或生图生成的背景图（bg_pattern=图片背景 时生效）"},
    "sticker_image":       {"label": "背景贴纸图", "type": "str", "def": "", "group": "装饰",
                            "hint": "背景贴纸平铺用图（优先于贴纸图标）"},
    "corner_sticker_image":{"label": "角落贴纸图", "type": "str", "def": "", "group": "装饰",
                            "hint": "角落大贴纸用图（优先于角落图标）"},
    # ---- 卡片背景填充（纯色/渐变/图片）----
    "card_fill_type": {"label": "卡片底填充", "type": "enum", "def": "solid", "group": "背景填充",
                       "options": [{"value": "solid", "label": "纯色"}, {"value": "gradient", "label": "渐变"},
                                   {"value": "image", "label": "图片"}]},
    "card_gradient_kind": {"label": "渐变类型", "type": "enum", "def": "linear", "group": "背景填充",
                           "options": [{"value": "linear", "label": "线性"}, {"value": "radial", "label": "径向"}]},
    "card_gradient_angle": {"label": "渐变角度", "type": "int", "min": 0, "max": 360, "def": 135, "unit": "°", "group": "背景填充"},
    "card_image": {"label": "卡片背景图", "type": "str", "def": "", "group": "背景填充",
                   "hint": "上传或生图生成的卡片背景图"},
    "card_image_fit": {"label": "图片适配", "type": "enum", "def": "cover", "group": "背景填充",
                       "options": [{"value": "cover", "label": "覆盖裁切"}, {"value": "contain", "label": "等比容纳"},
                                   {"value": "fill", "label": "拉伸填充"}, {"value": "tile", "label": "平铺"}]},
    "card_image_pos": {"label": "图片位置", "type": "enum", "def": "center", "group": "背景填充",
                       "options": [{"value": "center", "label": "居中"}, {"value": "top", "label": "顶部"},
                                   {"value": "bottom", "label": "底部"}, {"value": "left", "label": "左侧"},
                                   {"value": "right", "label": "右侧"}, {"value": "top-left", "label": "左上"},
                                   {"value": "top-right", "label": "右上"}, {"value": "bottom-left", "label": "左下"},
                                   {"value": "bottom-right", "label": "右下"}]},
    "card_image_opacity": {"label": "图片不透明度", "type": "int", "min": 0, "max": 100, "def": 100, "unit": "%", "group": "背景填充"},
    "card_image_overlay": {"label": "图片叠加底色", "type": "bool", "def": True, "group": "背景填充"},
    "card_image_crop_x": {"label": "裁切 X", "type": "int", "min": 0, "max": 100, "def": 0, "unit": "%", "group": "背景填充"},
    "card_image_crop_y": {"label": "裁切 Y", "type": "int", "min": 0, "max": 100, "def": 0, "unit": "%", "group": "背景填充"},
    "card_image_crop_w": {"label": "裁切宽", "type": "int", "min": 1, "max": 100, "def": 100, "unit": "%", "group": "背景填充"},
    "card_image_crop_h": {"label": "裁切高", "type": "int", "min": 1, "max": 100, "def": 100, "unit": "%", "group": "背景填充"},
    "card_alpha": {"label": "卡片底色透明度", "type": "int", "min": 0, "max": 100, "def": 100, "unit": "%", "group": "背景填充",
                   "hint": "低于 100% 时卡片半透明，露出背景图案/背景图"},

    # ---- 页面背景图片增强----
    "bg_image_fit": {"label": "页面背景图适配", "type": "enum", "def": "tile", "group": "背景填充",
                      "options": [{"value": "tile", "label": "平铺"}, {"value": "cover", "label": "覆盖裁切"},
                                  {"value": "contain", "label": "等比容纳"}, {"value": "fill", "label": "拉伸填充"}]},
    "bg_image_pos": {"label": "页面背景图位置", "type": "enum", "def": "center", "group": "背景填充",
                     "options": [{"value": "center", "label": "居中"}, {"value": "top", "label": "顶部"},
                                 {"value": "bottom", "label": "底部"}, {"value": "left", "label": "左侧"},
                                 {"value": "right", "label": "右侧"}]},
    "bg_image_opacity": {"label": "页面背景图不透明度", "type": "int", "min": 0, "max": 100, "def": 100, "unit": "%", "group": "背景填充"},
    "bg_image_overlay": {"label": "页面背景图叠加底色", "type": "bool", "def": False, "group": "背景填充"},
    "bg_image_crop_x": {"label": "页面背景裁切 X", "type": "int", "min": 0, "max": 100, "def": 0, "unit": "%", "group": "背景填充"},
    "bg_image_crop_y": {"label": "页面背景裁切 Y", "type": "int", "min": 0, "max": 100, "def": 0, "unit": "%", "group": "背景填充"},
    "bg_image_crop_w": {"label": "页面背景裁切宽", "type": "int", "min": 1, "max": 100, "def": 100, "unit": "%", "group": "背景填充"},
    "bg_image_crop_h": {"label": "页面背景裁切高", "type": "int", "min": 1, "max": 100, "def": 100, "unit": "%", "group": "背景填充"},
    # ---- 文字样式（Word 式字符样式，角色级；2026-09-15，见 docs/文字样式功能设计.md）----
    "font_family": {"label": "字体", "type": "str_free", "def": "", "group": "字形",
                   "hint": "留空=系统回退链（雅黑→黑体→宋体）"},
    "title_bold": {"label": "标题加粗", "type": "bool", "def": False, "group": "字形"},
    "title_italic": {"label": "标题斜体", "type": "bool", "def": False, "group": "字形"},
    "title_underline": {"label": "标题下划线", "type": "bool", "def": False, "group": "字形"},
    "title_strike": {"label": "标题删除线", "type": "bool", "def": False, "group": "字形"},
    "title_stroke": {"label": "标题描边", "type": "bool", "def": False, "group": "字形"},
    "title_stroke_color": {"label": "标题描边色", "type": "color", "def": "#000000", "group": "字形"},
    "body_bold": {"label": "正文加粗", "type": "bool", "def": False, "group": "字形"},
    "body_italic": {"label": "正文斜体", "type": "bool", "def": False, "group": "字形"},
    "body_underline": {"label": "正文下划线", "type": "bool", "def": False, "group": "字形"},
    "body_strike": {"label": "正文删除线", "type": "bool", "def": False, "group": "字形"},
    "body_stroke": {"label": "正文描边", "type": "bool", "def": False, "group": "字形"},
    "body_stroke_color": {"label": "正文描边色", "type": "color", "def": "#000000", "group": "字形"},
    "sub_bold": {"label": "次要加粗", "type": "bool", "def": False, "group": "字形"},
    "sub_italic": {"label": "次要斜体", "type": "bool", "def": False, "group": "字形"},
    "sub_underline": {"label": "次要下划线", "type": "bool", "def": False, "group": "字形"},
    "sub_strike": {"label": "次要删除线", "type": "bool", "def": False, "group": "字形"},
    "sub_stroke": {"label": "次要描边", "type": "bool", "def": False, "group": "字形"},
    "sub_stroke_color": {"label": "次要描边色", "type": "color", "def": "#000000", "group": "字形"},
    "badge_bold": {"label": "徽章加粗", "type": "bool", "def": False, "group": "字形"},
    "badge_italic": {"label": "徽章斜体", "type": "bool", "def": False, "group": "字形"},
    "badge_underline": {"label": "徽章下划线", "type": "bool", "def": False, "group": "字形"},
    "badge_strike": {"label": "徽章删除线", "type": "bool", "def": False, "group": "字形"},
    "badge_stroke": {"label": "徽章描边", "type": "bool", "def": False, "group": "字形"},
    "badge_stroke_color": {"label": "徽章描边色", "type": "color", "def": "#000000", "group": "字形"},

    # ---- 气泡（2026-09-16：战甲卡技能/集团卡气泡，主题可配）----
    "bubble_fill_alpha":   {"label": "技能气泡底色强度", "type": "int", "min": 0, "max": 60, "def": 13, "unit": "%", "group": "气泡",
                           "hint": "主色混入卡片底色的比例，越大越明显（战甲卡技能气泡）"},
    "bubble_border_alpha": {"label": "技能气泡描边强度", "type": "int", "min": 0, "max": 100, "def": 55, "unit": "%", "group": "气泡"},
    "bubble_radius":       {"label": "气泡圆角", "type": "int", "min": 0, "max": 40, "def": 12, "unit": "px", "group": "气泡"},
    "bubble_border_width": {"label": "气泡描边宽度", "type": "int", "min": 0, "max": 6, "def": 1, "unit": "px", "group": "气泡"},
    "aug_color":           {"label": "集团卡气泡色", "type": "color", "def": "", "group": "气泡",
                           "hint": "集团卡气泡/标题专用色；留空则用主题警告色"},
    "aug_fill_alpha":      {"label": "集团卡气泡底色强度", "type": "int", "min": 0, "max": 60, "def": 13, "unit": "%", "group": "气泡"},
    "aug_border_alpha":    {"label": "集团卡气泡描边强度", "type": "int", "min": 0, "max": 100, "def": 55, "unit": "%", "group": "气泡"},
    "tbl_header_alpha":         {"label": "表头行底色强度", "type": "int", "min": 0, "max": 100, "def": 24, "unit": "%", "group": "表格",
                                 "hint": "价格表/列表 表头行背景色（跟随主色），0 为无色"},
    "tbl_bubble_alpha":         {"label": "列表气泡底色强度", "type": "int", "min": 0, "max": 100, "def": 9, "unit": "%", "group": "表格",
                                 "hint": "价格表/列表 整体列表气泡背景（跟随主色），0 为无色"},
    "tbl_bubble_border_alpha":  {"label": "列表气泡边框强度", "type": "int", "min": 0, "max": 100, "def": 38, "unit": "%", "group": "表格"},
    "tbl_col_line_alpha":       {"label": "列分隔线强度", "type": "int", "min": 0, "max": 100, "def": 26, "unit": "%", "group": "表格",
                                 "hint": "各列之间的竖向分隔线"},
    "tbl_row_line_alpha":       {"label": "行间分隔线强度", "type": "int", "min": 0, "max": 100, "def": 16, "unit": "%", "group": "表格",
                                 "hint": "各行之间的横向分隔线"},

    # ---- 固定元素定位（2026-09-17：标题/来源 拖动调整位置与字号）----
    "title_fixed":  {"label": "固定标题位置", "type": "bool", "def": False, "group": "标题",
                     "hint": "开启后标题为覆盖层，不随正文长短移动，可拖动/调字号"},
    "title_x":      {"label": "标题 X", "type": "int", "min": 0, "max": 1000, "def": 48, "unit": "px", "group": "标题",
                     "hint": "距画布左缘像素；拖动标题时自动写入"},
    "title_y":      {"label": "标题 Y", "type": "int", "min": 0, "max": 1000, "def": 44, "unit": "px", "group": "标题",
                     "hint": "距画布顶部像素；固定后不随正文长短移动"},
    "title_fs":     {"label": "标题字号", "type": "int", "min": 12, "max": 44, "def": 22, "unit": "px", "group": "标题"},
    "source_fixed": {"label": "固定来源位置", "type": "bool", "def": False, "group": "标题",
                     "hint": "开启后尾部来源/分页说明为覆盖层，可拖动/调字号"},
    "source_x":     {"label": "来源 X", "type": "int", "min": 0, "max": 500, "def": 48, "unit": "px", "group": "标题",
                     "hint": "距画布右缘像素（右对齐）；拖动来源时自动写入"},
    "source_y":     {"label": "来源 Y", "type": "int", "min": 0, "max": 300, "def": 52, "unit": "px", "group": "标题",
                     "hint": "距画布底部像素；固定后不随正文长短移动"},
    "source_fs":    {"label": "来源字号", "type": "int", "min": 10, "max": 26, "def": 12, "unit": "px", "group": "标题"},

    # ---- 卡片专属背景覆盖（2026-09-23：每类卡片可单独设背景，互不影响）----
    # 键 = 模板名（fissure/guide/…），值 = 背景参数子集。编辑器在对应卡片页签下
    # 上传背景图时写入这里（不再写全局 bg_image），攻略卡等其它卡片不受影响。
    "bg_overrides": {"label": "卡片专属样式（背景/配色）", "type": "dict", "def": {}, "group": "背景填充",
                     "hint": "每类卡片单独覆盖背景；在对应卡片页签上传背景图即写入，缺失键回落全局背景"},
}

#: 形状字段元组（顺序 = LAYOUT_SPEC 声明顺序）
LAYOUT_FIELDS: tuple[str, ...] = tuple(LAYOUT_SPEC.keys())


def validate_layout(layout: dict | None) -> dict:
    """校验形状字段：**缺失用默认**、越界夹取、枚举非法回落默认。

    与 validate_colors 的"缺一不可"不同——形状字段允许缺失（老主题 JSON 没有），
    这样存量主题零迁移即可继续用。
    """
    src = layout if isinstance(layout, dict) else {}
    out: dict = {}
    for key, spec in LAYOUT_SPEC.items():
        v = src.get(key)
        if spec["type"] == "dict":
            # 卡片专属背景映射：按白名单键清洗（见 _clean_bg_overrides）
            out[key] = _clean_bg_overrides(v)
            continue
        if spec["type"] == "bool":
            out[key] = bool(v) if v is not None else spec["def"]
            continue
        if spec["type"] == "color":
            # 文字描边色等（layout 里的颜色不能用 str 类型——str 按资源文件名白名单校验，
            # #RRGGBB 会被清空；这里按 _HEX 校验，非法回落默认，防注入）
            sv = v if isinstance(v, str) else ""
            out[key] = sv if _HEX.match(sv) else spec.get("def", "#000000")
            continue
        if spec["type"] == "str_free":
            # 自由文本（font_family）：不是资源文件名，仅剥控制字符 + 限长
            sv = str(v) if v is not None else ""
            out[key] = sv.replace("\x00", "").replace("\n", "").replace("\r", "")[:64]
            continue
        if spec["type"] == "str":
            # 文件名引用：非空时按资源白名单校验（uuid.ext），非法回落默认（清空）
            s = v if isinstance(v, str) else ""
            out[key] = s if _asset_re.match(s) else spec.get("def", "")
            continue
        if spec["type"] == "enum":
            allowed = {o["value"] for o in spec.get("options", [])}
            out[key] = v if v in allowed else spec["def"]
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            out[key] = spec["def"]
            continue
        v = max(float(spec["min"]), min(float(spec["max"]), v))
        out[key] = int(round(v)) if spec["type"] == "int" else round(v, 3)
    return out

#: 默认颜色表（neon / light，作为编辑器「预设」按钮一键填色）
PRESETS: dict[str, dict[str, str]] = {
    "neon": {f: getattr(NEON_THEME, f) for f in THEME_FIELDS},
    "light": {f: getattr(LIGHT_THEME, f) for f in THEME_FIELDS},
}

#: 允许的字符（防路径穿越、防文件系统不兼容字）
_SAFE_NAME = re.compile(r"^[a-z0-9_\-\.]{1,64}$")
#: 6 位或 8 位（带 alpha）。**内置 LIGHT 主题的 card_shadow = "#0F172A0D"** 就是 8 位，
#: `canvas.rgb()` 也支持 8 位（取前 6 当 RGB），所以统一放行。
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
#: 资源文件名白名单（与 pic/assets.py 一致）：uuid + 允许扩展名，杜绝路径穿越。
_asset_re = re.compile(r"^[a-f0-9]{32}\.(png|jpe?g|webp)$")

#: 卡片专属背景（bg_overrides）里**允许出现的键**——只有背景装饰相关字段，
#: 其余（颜色/文字样式等）一律剥掉，防止把 bg_overrides 当任意配置注入口。
_BG_OVERRIDE_KEYS = frozenset({
    "bg_pattern", "bg_image",
    "bg_image_fit", "bg_image_pos", "bg_image_opacity", "bg_image_overlay",
    "bg_image_crop_x", "bg_image_crop_y", "bg_image_crop_w", "bg_image_crop_h",
    "elem_pos", "elem_scl",   # 元素微调（2026-09-24 全量自由编辑）：值由 _clean_elem_pos/_scl 二次清洗
})
#: 卡片专属**配色**键（2026-09-24：fissure / netracell 等固定风格卡的语义色覆盖）。
#: 值必须是合法 HEX；渲染端只读这些键，其余一律剥掉（防任意注入口）。
_OVERRIDE_COLOR_KEYS = frozenset({
    "page_color", "card_color", "border_color", "title_color", "text_color",
    "dim_color", "type_color", "name_color", "red_color", "tier_color",
    "node_color", "remain_color", "line_color",
    "bubble_bg", "bubble_line", "bubble_text",
})
#: 卡片专属**整型**参数键（2026-09-24：底色透明度等），0-100 夹取。
_OVERRIDE_INT_KEYS = frozenset({"card_alpha"})
#: 背景图案合法枚举（与 LAYOUT_SPEC["bg_pattern"].options 对齐）
_BG_PATTERNS = frozenset({"none", "dots", "grid", "diagonal", "stickers", "image"})
#: 卡片专属背景的键 = 模板名白名单（小写字母/下划线，防怪键）
_override_key_re = re.compile(r"^[a-z_]{1,32}$")


def _clean_bg_overrides(v) -> dict:
    """清洗卡片专属背景映射：非法键/值剥掉，合法值夹取。任何情况下不抛异常。"""
    if not isinstance(v, dict):
        return {}
    out: dict = {}
    for k, ov in v.items():
        k = str(k or "").strip().lower()
        if not _override_key_re.match(k) or not isinstance(ov, dict):
            continue
        clean: dict = {}
        for bk, bv in ov.items():
            bk = str(bk or "").strip()
            if bk in _OVERRIDE_COLOR_KEYS:
                # 专属配色：只收合法 HEX（非法直接剥掉，渲染端有硬编码默认）
                s = bv if isinstance(bv, str) else ""
                if _HEX.match(s.strip()):
                    clean[bk] = s.strip().upper()
                continue
            if bk in _OVERRIDE_INT_KEYS:
                # 专属整型参数（card_alpha 等）：0-100 夹取，非法剥掉
                try:
                    clean[bk] = max(0, min(100, int(bv)))
                except (TypeError, ValueError):
                    pass
                continue
            if bk not in _BG_OVERRIDE_KEYS:
                continue
            if bk == "bg_image":
                # 背景图：空串（清除）或资产白名单文件名
                s = bv if isinstance(bv, str) else ""
                clean[bk] = s if (s == "" or _asset_re.match(s)) else ""
            elif bk == "bg_pattern":
                clean[bk] = bv if bv in _BG_PATTERNS else "none"
            elif bk == "elem_pos":
                # 元素微调（2026-09-24 全量自由编辑）：{元素键: [dx, dy]} 像素偏移
                ep = _clean_elem_pos(bv)
                if ep:
                    clean[bk] = ep
            elif bk == "elem_scl":
                # 元素微调：{元素键: 缩放百分比}，30~300 夹取
                es = _clean_elem_scl(bv)
                if es:
                    clean[bk] = es
            elif isinstance(bv, bool):
                clean[bk] = bv
            elif bk in ("bg_image_opacity", "bg_image_crop_x", "bg_image_crop_y",
                        "bg_image_crop_w", "bg_image_crop_h"):
                try:
                    clean[bk] = max(0, min(100, int(bv)))
                except (TypeError, ValueError):
                    continue
            else:  # fit / pos：字符串枚举宽松放行（渲染端有默认回落）
                clean[bk] = str(bv or "")[:32]
        if clean:
            out[k] = clean
    return out


#: 元素微调键白名单（元素语义 + 序号，如 title / intro / tier-0 / bub-0-1-2）
_elem_key_re = re.compile(r"^[a-z0-9_\-]{1,40}$")


def _clean_elem_pos(v) -> dict:
    """清洗元素偏移映射：``{元素键: [dx, dy]}``。非法键/值剥掉，坐标 ±4000 夹取。"""
    if not isinstance(v, dict):
        return {}
    out: dict = {}
    for ek, p in v.items():
        ek = str(ek or "").strip()
        if not _elem_key_re.match(ek) or not isinstance(p, (list, tuple)) or len(p) != 2:
            continue
        try:
            dx = max(-4000, min(4000, int(p[0])))
            dy = max(-4000, min(4000, int(p[1])))
        except (TypeError, ValueError):
            continue
        if dx or dy:   # (0,0) 等价未配置，不落盘
            out[ek] = [dx, dy]
    return out


def _clean_elem_scl(v) -> dict:
    """清洗元素缩放映射：``{元素键: 百分比}``。30~300 夹取，100 等价未配置不落盘。"""
    if not isinstance(v, dict):
        return {}
    out: dict = {}
    for ek, s in v.items():
        ek = str(ek or "").strip()
        if not _elem_key_re.match(ek):
            continue
        try:
            n = int(round(float(s)))
        except (TypeError, ValueError):
            continue
        n = max(30, min(300, n))
        if n != 100:
            out[ek] = n
    return out

#: 存储目录：qq_bridge/src/plugins/qq_bridge/pic/themes_store.py → ../../../../data/themes
_HERE = os.path.dirname(os.path.abspath(__file__))
THEMES_DIR: str = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "..", "data", "themes"))


class ThemeStoreError(Exception):
    """主题存储异常（非法名字/非法颜色/IO 错误）。编辑页应捕获并展示。"""


def _safe(name: str) -> str:
    """规范化主题名（小写 + 白名单）。失败抛 ThemeStoreError。"""
    s = (name or "").strip().lower()
    if not _SAFE_NAME.match(s):
        raise ThemeStoreError(
            "主题名「%s」非法：仅允许小写字母/数字/下划线/短横线/点，长度 1-64" % (name or "")
        )
    return s


def validate_colors(colors: dict) -> dict:
    """校验颜色字段，返回**大写规范**的副本。

    2026-09-23 兼容性修复：字段**缺失/为 null 时回落内置默认值**（取 NEON_THEME），
    不再直接报错。原因：`link`（第 17 个颜色字段）是后期新增，存量主题 JSON
    （如 warm.json / cs1.json）里没有它——旧逻辑"缺一不可"会让所有老主题
    在编辑器预览/保存时全部报「颜色字段 link 格式错误：None」。
    与 validate_layout 的「缺失即默认」哲学对齐，存量主题零迁移。
    注意：**存在但格式非法**（非 #RRGGBB(AA)）仍然报错——那才是真正的坏输入。
    """
    if not isinstance(colors, dict):
        raise ThemeStoreError("colors 必须是对象")
    out: dict[str, str] = {}
    for f in THEME_FIELDS:
        v = colors.get(f)
        if v is None:
            # 新增字段在存量主题里缺失 → 用内置默认补齐（保存后文件即自愈完整）
            out[f] = str(getattr(NEON_THEME, f)).upper()
            continue
        if not isinstance(v, str) or not _HEX.match(v.strip()):
            raise ThemeStoreError(
                "颜色字段 %s 格式错误：%r（须为 #RRGGBB，例如 #00E5FF）" % (f, v)
            )
        out[f] = v.strip().upper()
    return out


class ThemeStore:
    """文件系统持久化的主题仓库（**单例**，通过模块级 `theme_store` 引用）。"""

    def __init__(self, dir_: str = THEMES_DIR) -> None:
        self.dir = dir_
        os.makedirs(self.dir, exist_ok=True)

    # ---- 读
    def list(self) -> list[dict]:
        """列出全部主题（按更新时间倒序）。"""
        if not os.path.isdir(self.dir):
            return []
        out: list[dict] = []
        for fn in os.listdir(self.dir):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(self.dir, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and "colors" in d:
                    out.append(d)
            except Exception:  # 损坏文件不致命：跳过
                continue
        out.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return out

    def get(self, name: str) -> dict | None:
        """按名字取主题；不存在返回 None。"""
        try:
            n = _safe(name)
        except ThemeStoreError:
            return None
        p = os.path.join(self.dir, n + ".json")
        if not os.path.isfile(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else None
        except Exception:
            return None

    # ---- 写
    def save(self, name: str, colors: dict, description: str = "",
             layout: dict | None = None) -> dict:
        """保存/更新主题。返回写入后的完整 dict（含时间戳）。

        :param layout: 形状/排版字段（radius/pad/width/font_scale/grad_dir/border/shadow）。
                       省略或字段缺失时按 LAYOUT_SPEC 默认值补齐，老主题零迁移。
        """
        n = _safe(name)
        clean = validate_colors(colors)
        clean_layout = validate_layout(layout)
        p = os.path.join(self.dir, n + ".json")
        now = time.time()
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    old = json.load(f)
                created = old.get("created_at", now)
            except Exception:
                created = now
        else:
            created = now
        data = {
            "name": n,
            "description": (description or "").strip()[:200],
            "colors": clean,
            "layout": clean_layout,
            "created_at": created,
            "updated_at": now,
        }
        # 原子写：先写 tmp 再 replace，避免半截文件
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
        return data

    def delete(self, name: str) -> bool:
        """删除主题。返回是否真删了文件。"""
        try:
            n = _safe(name)
        except ThemeStoreError:
            return False
        p = os.path.join(self.dir, n + ".json")
        if not os.path.isfile(p):
            return False
        os.remove(p)
        return True


#: 模块级单例
theme_store = ThemeStore()