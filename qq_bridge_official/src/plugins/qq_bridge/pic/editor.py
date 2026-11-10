# -*- coding: utf-8 -*-
"""pic/editor.py —— 自定义主题编辑器的 REST + HTML 页面挂载（2026-09-07 新增）。

按"研发准则"先检索 2026 主流设计（详见 docs/pic_render_plan.md），落地三件共识：
  1. **实时预览**（改色即看效果）—— 编辑器每次输入 debounce 350ms fetch preview
  2. **WCAG 对比度提示**（防止用户配出"看得见但读不清"的色）—— 在页面上算
  3. **一键导出 JSON**（与主流 Color Toolkit / shadcn theme builder 一致）

API 设计：
  GET    /api/pic/themes                列表 + 内置预设 + 字段定义
  GET    /api/pic/themes/{name}         详情
  PUT    /api/pic/themes/{name}         保存/创建（colors + description）
  DELETE /api/pic/themes/{name}         删除
  POST   /api/pic/themes/{name}/activate 激活（写 Config.PIC_CUSTOM_THEME）
  POST   /api/pic/themes/deactivate     取消激活
  POST   /api/pic/themes/preview        临时颜色实时预览（返回 PNG bytes）
  GET    /pic/theme-editor              HTML 编辑器页面
  GET    /api/pic/project-images        主项目文生图/图生图成图（过滤回收站，可筛选排序分页）
  POST   /api/pic/project-images/import 把主项目某张图导入主题资源库

激活态写 Config.PIC_CUSTOM_THEME（内存热生效）并**落盘 .env 同键**（2026-09-16 修复：
此前只写内存，重启后 Config 从 .env 重读 → 主题记忆丢失；deactivate 清空内存与 .env 即回 PIC_THEME。）
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path

from fastapi import Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from . import assets as _assets
from . import fonts as _fonts_mod
from . import gen_sticker
from . import icons as _icons
from .theme import Theme
from .themes_store import (
    LAYOUT_FIELDS,
    LAYOUT_SPEC,
    PRESETS,
    ThemeStoreError,
    THEME_FIELDS,
    theme_store,
    validate_layout,
)
from . import TEMPLATE_LABELS

#: 注意：模块级**不能** `from . import pic`——`pic.editor` 在 pic 包内，
#: `.` 指的是 pic 包本身，里面没有名为 `pic` 的子模块，
#: Python 会抛 ImportError；而 `from .. import pic` 也会在 pic/__init__
#: 加载中途触发循环。**正解是用 sys.modules[__package__] 拿到当前包对象**，
#: 因为 pic 包一定已加载到 sys.modules（否则我们这个 editor 模块也不会被加载）。
_pic_pkg = __import__("sys").modules[__package__]


#: .env 路径（qq_bridge/.env）。**提为模块级常量**：单测可 monkeypatch 到临时目录，
#: 否则跑一次测试就会把「激活的自定义主题」写进生产 .env（踩过的坑，2026-09-24）。
ENV_PATH = Path(__file__).resolve().parents[4] / ".env"

_PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "editor_page.html")
#: 内置静态资源目录（Coloris 拾色器，MIT 许可，随包分发不依赖 CDN）
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


def _log_persist_fail(e: Exception) -> None:
    """持久化失败必须记日志（CLOSE-02 禁止静默吞异常），不阻断热生效。"""
    try:
        from ..config import get_logger, jlog
        get_logger().warning(jlog("theme_active_persist_failed", error=str(e)))
    except Exception:  # noqa: BLE001
        pass


def _persist_active_theme(name: str) -> bool:
    """把「当前激活的自定义主题」落盘，**双通道**：.env 的 PIC_CUSTOM_THEME + data/pic_active_theme.json。

    2026-09-16 修复：此前 activate 只 setattr Config.PIC_CUSTOM_THEME（内存态），
    服务重启后 Config 从 .env 重新初始化 → 自定义主题记忆丢失、回落默认。
    这里与 config_api.apply_config 的 .env 写回算法一致（保留注释与顺序、
    已存在则原位替换、否则追加），保证下次启动自动恢复同一主题。

    2026-09-24 加固（用户实测「重启后主题变回默认」）：.env 单一通道太脆——
    dotenv 未加载 / 键被别的写 .env 通道覆盖成空串 / .env 写失败，任一命中都会丢。
    因此同时写 `data/pic_active_theme.json`（theme.write_active_theme），读取侧
    `theme._config_theme()` 在 env 通道拿不到值时回落到它。**任一通道写成功即算
    持久化成功**，不再出现"点了应用、重启却没了"。

    失败不阻断热生效：记日志并返回 False，调用方在响应里带 persisted 字段。
    """
    import re as _re

    env_path = ENV_PATH
    env_ok = False
    try:
        lines = (env_path.read_text(encoding="utf-8").splitlines()
                 if env_path.exists() else [])
    except Exception as e:  # noqa: BLE001
        _log_persist_fail(e)
        lines = []
    else:
        out, seen = [], False
        for line in lines:
            if _re.match(r"^\s*PIC_CUSTOM_THEME\s*=", line):
                out.append("PIC_CUSTOM_THEME=%s" % name)
                seen = True
            else:
                out.append(line)
        if not seen:
            out.append("PIC_CUSTOM_THEME=%s" % name)
        try:
            env_path.write_text("\r\n".join(out) + "\r\n",
                                encoding="utf-8", newline="")
            env_ok = True
        except Exception as e:  # noqa: BLE001
            _log_persist_fail(e)

    # 通道二：pic 自己的持久文件（env 失效时它就是唯一救命稻草）
    try:
        from .theme import write_active_theme

        file_ok = bool(write_active_theme(name))
    except Exception as e:  # noqa: BLE001
        _log_persist_fail(e)
        file_ok = False
    if not (env_ok or file_ok):
        _log_persist_fail(RuntimeError("两个持久化通道都失败：name=%s" % name))
    return bool(env_ok or file_ok)


def register(app) -> None:
    """挂载全部主题编辑器路由（在 FastAPI app 上 add_api_route）。"""

    # ---- 列表（含内置预设、字段定义、形状参数定义、可预览模板）----
    async def _themes_list():
        return {
            "ok": True,
            "themes": theme_store.list(),
            "presets": list(PRESETS.keys()),
            # 预设的**具体色值**（前端预设墙要画缩略色点，光有名字不够）
            "preset_colors": PRESETS,
            # 颜色字段带上分组，前端按「背景/文字/主色/进度/语义」分区渲染，
            # 17 个平铺一行一个色块是上一版"太不人性化"的主因（2026-09-07 重构）。
            "fields": [
                {"key": f, "label": _LABELS.get(f, f), "group": _GROUPS.get(f, "其他")}
                for f in THEME_FIELDS
            ],
            "layout_spec": LAYOUT_SPEC,
            "layout_fields": list(LAYOUT_FIELDS),
            "templates": list(_pic_pkg.list_templates()),
            "template_labels": TEMPLATE_LABELS,
            "active": _active_name(),
        }

    async def _theme_get(name: str):
        d = theme_store.get(name)
        if not d:
            return _err("主题不存在：" + name, 404)
        return {"ok": True, "theme": d}

    # ---- 保存/创建（强校验 colors）----
    async def _theme_save(name: str, req: Request):
        try:
            body = await req.json()
        except Exception:
            return _err("请求体须为 JSON")
        colors = body.get("colors") if isinstance(body, dict) else None
        layout = body.get("layout") if isinstance(body, dict) else None
        description = (body.get("description") or "") if isinstance(body, dict) else ""
        try:
            d = theme_store.save(name, colors or {}, description, layout=layout)
            return {"ok": True, "theme": d}
        except ThemeStoreError as e:
            return _err(str(e))

    async def _theme_delete(name: str):
        if theme_store.delete(name):
            return {"ok": True}
        return _err("主题不存在或删除失败", 404)

    # ---- 激活/取消（写 Config.PIC_CUSTOM_THEME，热生效）----
    async def _theme_activate(name: str):
        if not theme_store.get(name):
            return _err("主题不存在：" + name, 404)
        from ..config import Config

        Config.PIC_CUSTOM_THEME = name
        persisted = _persist_active_theme(name)
        return {"ok": True, "active": name, "persisted": persisted}

    async def _theme_deactivate():
        from ..config import Config

        Config.PIC_CUSTOM_THEME = ""
        persisted = _persist_active_theme("")
        return {"ok": True, "active": "", "persisted": persisted}

    # ---- 实时预览：拿**临时 colors**直接渲染（不写盘）----
    async def _theme_preview(req: Request):
        try:
            body = await req.json()
        except Exception:
            return _err("请求体须为 JSON")
        try:
            t, template, kw = _parse_preview(body)
            data = _pic_pkg.render_raw(template, t, **kw)
        except ThemeStoreError as e:
            return _err(str(e))
        except _pic_pkg.PicRenderError as e:
            return _err(str(e))
        return Response(content=data, media_type="image/png")

    def _parse_preview(body) -> tuple[Theme, str, dict]:
        """把前端预览请求解析成 Theme + 模板名 + 参数（preview / preview/interactive 共用）。"""
        if not isinstance(body, dict):
            body = {}
        colors = body.get("colors") or {}
        layout = body.get("layout") or {}
        template = (body.get("template") or "text").strip().lower()
        text_body = body.get("body") or "预览主题"
        title = body.get("title")
        params = body.get("params")
        from .themes_store import validate_colors

        clean = validate_colors(colors)
        kw = dict(params) if isinstance(params, dict) else {}
        # 不同模板需要不同参数（进度图要 percent、配额图要 used/quota），
        # 前端只传了文案时这里补一组"有代表性的示例值"，保证每个模板都能出图。
        if template == "text":
            kw.setdefault("body", text_body)
            kw.setdefault("title", title)
        elif template == "progress":
            kw.setdefault("percent", 62)
            kw.setdefault("elapsed", 95)
            kw.setdefault("kind", "t2i")
            kw.setdefault("prompt", text_body or "一只赛博朋克风格的猫")
            kw.setdefault("trace_id", "preview01")
        elif template == "quota":
            kw.setdefault("used", 18)
            kw.setdefault("quota", 50)
            kw.setdefault("user_label", title or "本喵的配额")
        elif template == "guide":
            # 战甲卡预览示例：技能/集团卡气泡 + 市场表格 + 来源小字（2026-09-17）
            kw.setdefault("body", (
                "【技能】\n"
                "· 1 剧毒孢子：对敌人施加毒素孢子，扩散传播\n"
                "· 4 瘴气：引爆全场毒素，造成爆发伤害\n"
                "【集团卡】\n"
                "· 补充能量：技能命中时队友获得能量回复\n"
                "@wmhdr|绝路 Prime 一套|Rubico Prime Set|武器 · 套装|不可兑换|1,000,000|1265|68|\n"
                "@hdr|卖家|状态|声望|数量|白金价格\n"
                "@row|TennoA|游戏在线|12|1|60|u1|\n"
                "@row|TennoB|在线|3|2|55|u2|\n"
                "@shout|/w TennoA Hi! I want to buy: Rubico Prime Set for 60 platinum. (wf.m via RabbitBot)\n"
                "@page|页码:1/86 使用\"wm 绝路 Prime 一套 -2\"查看下一页.\n"
                "@foot|[PC] Warframe Rabbit *善用右键/长按提取文字\n"
            ))
            kw.setdefault("title", "战甲资料预览")
            kw.setdefault("source", "worldstate.wf.wiki 世界状态 · 更新于 2026-09-17 12:00 UTC")
        elif template == "fissure":
            # 虚空裂隙卡预览示例（2026-09-24：此前无示例参数 → 预览只剩标题，没东西可点）
            kw.setdefault("title", title or "虚空裂隙")
            kw.setdefault("intro", "矿工的日常：虚空裂隙速览")
            kw.setdefault("groups", [
                {"label": "前纪遗物(T2)", "rows": [
                    {"bubble": "Grineer · 普通", "node": "Tessellate / 土星", "remain": "23分钟"},
                    {"bubble": "Corpus · 钢铁", "node": "Hydron / 谷神星", "remain": "48分钟"},
                ]},
                {"label": "后纪遗物(T3)", "rows": [
                    {"bubble": "Orokin · 普通", "node": "Unda / 火星", "remain": "1小时12分"},
                ]},
            ])
            kw.setdefault("footers", ["* 输入\"裂隙 钢铁\"查看钢铁裂隙"])
        elif template == "netracell":
            # 科研任务卡预览示例（2026-09-24：此前无示例参数 → 预览正文空白）
            kw.setdefault("title", title or "本周科研任务")
            kw.setdefault("sections", [{
                "highlight": "深层",
                "missions": [
                    {"type": "刺杀", "name": "Vor 上尉", "risks": ["情报减少"]},
                    {"type": "防御", "name": "Hydron / 谷神星", "risks": ["敌人更强"]},
                    {"type": "捕获", "name": "Korrudo / 火星", "risks": []},
                ],
                "extras": ["额外奖励：真空 Mod 30分钟", "额外奖励：虚空风暴加成",
                           "绩效任务：完成一次枢纽捕获"],
            }])
            kw.setdefault("remain", "6天12小时49分")
        return Theme(name="preview", **clean, **validate_layout(layout)), template, kw

    # ---- 交互式预览：返回 PNG + 可点击区域（支持在预览图上直接编辑）----
    async def _theme_preview_interactive(req: Request):
        try:
            body = await req.json()
        except Exception:
            return _err("请求体须为 JSON")
        try:
            t, template, kw = _parse_preview(body)
            fn = _pic_pkg._TEMPLATES.get(template)
            if fn is None:
                return _err("未知模板：" + template)
            c = fn(t, **kw)
            data = c.to_bytes()
            regions = getattr(c, "regions", []) or []
        except ThemeStoreError as e:
            return _err(str(e))
        except _pic_pkg.PicRenderError as e:
            return _err(str(e))
        return JSONResponse({
            "ok": True,
            "png": base64.b64encode(data).decode("utf-8"),
            "regions": regions,
            "meta": {"w": c.width, "h": c.height, "template": template},
        })

    # ---- 从图片提取主色（2026-09-07 新增：照着一张图配色）----
    # 用 Pillow 自带的中位切分量化（median cut），不引第三方库
    # （pic 包红线：只依赖 Pillow）。思路参考 Color Thief（MIT）。
    async def _theme_extract(req: Request):
        try:
            body = await req.json()
        except Exception:
            return _err("请求体须为 JSON")
        raw = (body.get("image") or "") if isinstance(body, dict) else ""
        try:
            n = int((body or {}).get("count") or 8)
        except (TypeError, ValueError):
            n = 8
        n = max(3, min(12, n))
        if "," in raw:
            raw = raw.split(",", 1)[1]
        if not raw.strip():
            return _err("缺少 image（base64 DataURL）")
        try:
            import base64
            import io

            from PIL import Image

            im = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")
            im.thumbnail((180, 180))
            q = im.quantize(colors=n, method=Image.MEDIANCUT)
            pal = q.getpalette() or []
            items = sorted(q.getcolors() or [], reverse=True)
            out = []
            for cnt, idx in items[:n]:
                r, g, b = pal[idx * 3: idx * 3 + 3]
                out.append({"hex": "#%02X%02X%02X" % (r, g, b), "weight": int(cnt)})
            return {"ok": True, "colors": out, "count": len(out)}
        except Exception as e:  # noqa: BLE001
            return _err("图片解析失败：%s: %s" % (type(e).__name__, str(e)[:120]))

    # ---- 卡片主题绑定（每张业务卡片可独立配置主题，2026-09-17）----
    def _card_catalog() -> list[str]:
        """已知业务卡片清单：GUIDE_TITLES 的标题值去重 + _send_guide 直传的标题。"""
        out: list[str] = []
        try:
            from ..game_tools import GUIDE_TITLES
            seen = set(out)
            for v in GUIDE_TITLES.values():
                if v not in seen:
                    seen.add(v)
                    out.append(v)
        except Exception:  # game_tools 拉 nonebot 链，独立环境拿不到就用手工清单
            pass
        for extra in ("星际战甲·紫卡评估", "星际战甲·资料", "星际战甲·模拟开卡",
                      "角色图鉴", "游戏工具箱"):
            if extra not in out:
                out.append(extra)
        return out

    def _card_binds() -> dict:
        try:
            from .theme import _CARD_THEMES_PATH
            with open(_CARD_THEMES_PATH, encoding="utf-8") as f:
                d = json.load(f)
            b = d.get("binds") if isinstance(d, dict) else None
            return dict(b) if isinstance(b, dict) else {}
        except Exception:
            return {}

    def _card_themes_get():
        from .theme import _CARD_THEMES_PATH, card_theme as _ct
        themes = ["neon", "light", "auto"] + [t["name"] for t in theme_store.list()]
        default = _active_name() or ""
        try:
            from ..config import Config
            if not default:
                default = str(getattr(Config, "PIC_THEME", "neon") or "neon")
        except Exception:
            default = default or "neon"
        return {
            "ok": True,
            "default": default,
            "binds": _card_binds(),
            "cards": _card_catalog(),
            "themes": themes,
            "path": _CARD_THEMES_PATH,
        }

    async def _card_themes_save(req: Request):
        try:
            body = await req.json()
        except Exception:
            return _err("请求体须为 JSON")
        binds = body.get("binds") if isinstance(body, dict) else None
        if not isinstance(binds, dict):
            return _err("binds 必须是对象")
        clean: dict[str, str] = {}
        for k, v in binds.items():
            k = str(k).strip()[:40]
            v = str(v or "").strip().lower()
            if k and v:
                clean[k] = v
        from .theme import _CARD_THEMES_PATH
        os.makedirs(os.path.dirname(_CARD_THEMES_PATH), exist_ok=True)
        data = {"updated_at": time.time(), "binds": clean}
        tmp = _CARD_THEMES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _CARD_THEMES_PATH)
        return {"ok": True, "binds": clean}

    # ---- 主项目图片（文生图 / 图生图）→ 主题背景/贴纸（2026-09-24 用户要求）----
    # 只经 HTTP 单向调主项目（铁律），绝不 import 主项目模块；复用 gallery 的解析契约。
    async def _project_images(req: Request):
        """`GET /api/pic/project-images?type=&keyword=&sort=&page=&size=`

        列出主项目已成功的文生图 / 图生图，**自动过滤回收站图片**，支持分类筛选、
        关键词筛选、时间排序、分页。主项目不可达时返回 `ok:false` + error（弹窗提示，
        不抛 500，避免整页 JS 报错）。
        """
        try:
            from ..gallery import fetch_images
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "画廊模块不可用：%s" % e, "items": [], "total": 0}

        q = dict(req.query_params)

        def _int(k: str, default: int) -> int:
            try:
                return int(q.get(k) or default)
            except (TypeError, ValueError):
                return default

        res = await fetch_images(
            page=_int("page", 1), size=_int("size", 24),
            type_filter=(q.get("type") or "").strip(),
            keyword=(q.get("keyword") or "").strip(),
            sort=(q.get("sort") or "desc").strip(),
        )
        if res.get("error"):
            # 主项目不可达：给前端一句人话，别让它自己猜
            res["error"] = "取不到主项目图片（%s），请确认主项目 7788 已启动" % res["error"]
        return res

    async def _project_images_import(req: Request):
        """`POST /api/pic/project-images/import  {url}` → 下载并存进主题资源库。

        :param url: 主项目图片地址（`/file?name=xxx` 或绝对地址）。
        :return: `{ok, filename}` —— filename 与上传接口同格式，前端直接当资源用。
        """
        try:
            body = await req.json()
        except Exception:
            return _err("请求体须为 JSON")
        url = (body.get("url") or "").strip() if isinstance(body, dict) else ""
        if not url:
            return _err("缺少 url")
        try:
            from ..gallery import abs_url, is_project_url

            full = abs_url(url)
            if not is_project_url(full):
                return _err("只允许导入主项目自己的图片地址")
            import httpx

            # 显式超时（CLOSE-04 / REDUN-02：绝不依赖 httpx 默认）
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
                resp = await cli.get(full)
            if resp.status_code != 200 or not resp.content:
                return _err("下载失败：HTTP %d" % resp.status_code)
            fname = _assets.save_scaled(resp.content)
        except _assets.AssetError as e:
            return _err(str(e))
        except Exception as e:  # noqa: BLE001
            return _err("下载失败：%s: %s" % (type(e).__name__, str(e)[:120]))
        return {"ok": True, "filename": fname}

    # ---- HTML 编辑器页面 ----
    async def _editor_page():
        try:
            html_text = open(_PAGE_PATH, encoding="utf-8").read()
        except FileNotFoundError:
            return _err("editor_page.html 未找到", 500)
        return HTMLResponse(html_text)

    app.add_api_route("/api/pic/themes", _themes_list, methods=["GET"])
    # 静态资源（内置 Coloris 拾色器，MIT）：本地托管，断网/内网也能用
    if os.path.isdir(_STATIC_DIR):
        try:
            app.mount("/pic/static", StaticFiles(directory=_STATIC_DIR), name="pic_static")
        except Exception:  # 挂载失败不影响编辑器其余功能（页面会回退原生拾色器）
            pass

    # ---- 装饰资源（背景图 / 贴纸图）：上传（合规校验）/ 取图 / 删除 ----
    async def _asset_upload(file: UploadFile = File(...)):
        data = await file.read()
        try:
            fname = _assets.save(data)
        except _assets.AssetError as e:
            return _err(str(e))
        return {"ok": True, "filename": fname}

    async def _asset_get(filename: str):
        p = _assets.path_of(filename)
        if not p:
            return _err("资源不存在", 404)
        return FileResponse(p)

    async def _asset_delete(filename: str):
        if _assets.delete(filename):
            return {"ok": True}
        return _err("资源不存在", 404)

    # ---- 字体族列表（2026-09-15 文字样式功能；cjk 字体排前）----
    def _font_list():
        try:
            fams = _fonts_mod.scan_families()
        except Exception as e:  # 扫描失败不影响编辑器其余功能
            return _err("字体扫描失败：" + str(e))
        return JSONResponse({"ok": True, "fonts": [
            {"family": r["family"], "cjk": r["cjk"],
             "has_bold": r["bold"] is not None, "has_italic": r["italic"] is not None}
            for r in fams
        ]})

    # ---- 图标预览（缩略图网格用）----
    def _icon_list():
        return {"ok": True, "icons": list(_icons.ICONS)}

    def _icon_preview(name: str):
        kind = (name or "").strip().lower()
        if kind not in _icons._SHAPES:
            return _err("图标不存在：" + name, 404)
        img = _icons.layer(kind, 64, "#00E5FF", alpha=200)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Response(content=buf.read(), media_type="image/png")

    # ---- 调主项目生图接口生成贴纸/背景图 ----
    async def _theme_gen_sticker(name: str, req: Request):
        if not theme_store.get(name):
            return _err("主题不存在：" + name, 404)
        try:
            body = await req.json()
        except Exception:
            return _err("请求体须为 JSON")
        prompt = (body.get("prompt") or "").strip() if isinstance(body, dict) else ""
        target = (body.get("target") or "sticker") if isinstance(body, dict) else "sticker"
        tpl = (body.get("tpl") or "").strip().lower() if isinstance(body, dict) else ""
        try:
            fname = await gen_sticker.generate(prompt, target)
        except gen_sticker.GenStickerError as e:
            return _err(str(e))
        # 自动回写主题字段，省去用户再手动绑；并联动打开对应开关
        field = {"bg": "bg_image", "corner": "corner_sticker_image",
                 "sticker": "sticker_image"}.get(target)
        d = theme_store.get(name)
        if d and field:
            lay = dict(d.get("layout") or {})
            if target == "bg" and tpl:
                # 2026-09-23 卡片专属背景：生图写入 bg_overrides[tpl]，不影响其它卡片
                ov = dict(lay.get("bg_overrides") or {})
                cur = dict(ov.get(tpl) or {})
                cur["bg_image"] = fname
                cur["bg_pattern"] = "image"
                ov[tpl] = cur
                lay["bg_overrides"] = ov
            else:
                lay[field] = fname
                if target == "bg":
                    lay["bg_pattern"] = "image"
                elif target == "sticker":
                    lay["bg_pattern"] = "stickers"
                elif target == "corner":
                    lay["corner_sticker"] = True
            theme_store.save(name, d.get("colors") or {}, d.get("description", "") or "",
                             layout=lay)
        return {"ok": True, "filename": fname, "field": field}

    app.add_api_route("/api/pic/themes/preview", _theme_preview, methods=["POST"])
    app.add_api_route("/api/pic/themes/preview/interactive", _theme_preview_interactive, methods=["POST"])
    app.add_api_route("/api/pic/themes/extract-colors", _theme_extract, methods=["POST"])
    app.add_api_route("/api/pic/themes/deactivate", _theme_deactivate, methods=["POST"])
    app.add_api_route("/api/pic/themes/assets", _asset_upload, methods=["POST"])
    app.add_api_route("/api/pic/themes/assets/{filename}", _asset_get, methods=["GET"])
    app.add_api_route("/api/pic/themes/assets/{filename}", _asset_delete, methods=["DELETE"])
    app.add_api_route("/api/pic/themes/icons", _icon_list, methods=["GET"])
    app.add_api_route("/api/pic/themes/fonts", _font_list, methods=["GET"])
    app.add_api_route("/api/pic/themes/icons/{name}", _icon_preview, methods=["GET"])
    app.add_api_route("/api/pic/themes/{name}", _theme_get, methods=["GET"])
    app.add_api_route("/api/pic/themes/{name}", _theme_save, methods=["PUT"])
    app.add_api_route("/api/pic/themes/{name}", _theme_delete, methods=["DELETE"])
    app.add_api_route("/api/pic/themes/{name}/activate", _theme_activate, methods=["POST"])
    app.add_api_route("/api/pic/themes/{name}/generate-sticker", _theme_gen_sticker, methods=["POST"])
    app.add_api_route("/api/pic/card-themes", _card_themes_get, methods=["GET"])
    app.add_api_route("/api/pic/card-themes", _card_themes_save, methods=["POST"])
    app.add_api_route("/api/pic/project-images", _project_images, methods=["GET"])
    app.add_api_route("/api/pic/project-images/import", _project_images_import, methods=["POST"])
    app.add_api_route("/pic/theme-editor", _editor_page, methods=["GET"])


def _active_name() -> str:
    """当前激活的自定义主题名（含 data/pic_active_theme.json 兜底，见 theme.active_custom_theme）。"""
    try:
        from .theme import active_custom_theme

        return active_custom_theme()
    except Exception:
        return ""


#: 字段显示名（中文，编辑器右上角说明用）。缺字段有 default。
_LABELS: dict[str, str] = {
    "bg": "页面底色",
    "card": "卡片底色",
    "card_border": "卡片边框",
    "card_shadow": "卡片阴影",
    "text": "正文",
    "text_sub": "次级文字",
    "text_weak": "弱文字",
    "accent": "主色",
    "accent2": "辅色",
    "accent_soft": "徽章底",
    "track": "进度条轨道",
    "fill_start": "填充渐变起",
    "fill_end": "填充渐变末",
    "ok": "成功",
    "warn": "警告",
    "err": "危险",
    "divider": "分割线",
}


#: 颜色字段分组（编辑器左侧分区渲染，避免 17 个色块平铺找不到）。
#: 语义分组 > 字母序，符合 tweakcn / VSCode 主题编辑器的惯例。
_GROUPS: dict[str, str] = {
    "bg": "背景", "card": "背景", "card_border": "背景", "card_shadow": "背景",
    "divider": "背景",
    "text": "文字", "text_sub": "文字", "text_weak": "文字",
    "accent": "主色", "accent2": "主色", "accent_soft": "主色",
    "track": "进度条", "fill_start": "进度条", "fill_end": "进度条",
    "ok": "语义色", "warn": "语义色", "err": "语义色",
}