# -*- coding: utf-8 -*-
"""game_tools/ocr.py —— Windows 本地 OCR（紫卡截图识别，中英双语）。

用 Windows 10/11 自带的 Windows.Media.Ocr 引擎（winrt 绑定），
纯本地、零外部请求、免费无限量。

语言策略：优先 zh-CN（国际服客户端可切换中文界面，中文引擎也能读英文），
失败回退 en-US。两个引擎都建好时**合并输出**（中英界面都覆盖）。

设计：
  * 引擎懒加载、进程内复用（OcrEngine 创建有几十 ms 开销）；
  * import 失败/引擎缺失 → available()=False，调用方降级为文本提示；
  * 阻塞读取走 asyncio.to_thread，不拖事件循环。
"""
from __future__ import annotations

import asyncio
import io
import os
import re

from PIL import Image
from nonebot.log import logger

_ENGINES: list = []  # [(tag, engine), ...]


def _load_engines() -> list:
    """懒加载 OCR 引擎：zh-CN 优先，en-US 兜底。"""
    if _ENGINES:
        return _ENGINES
    try:
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.globalization import Language

        for tag in ("zh-CN", "en-US"):
            try:
                eng = OcrEngine.try_create_from_language(Language(tag))
                if eng is not None:
                    _ENGINES.append((tag, eng))
            except Exception:  # noqa: BLE001
                continue
        if not _ENGINES:
            try:
                eng = OcrEngine.try_create_from_user_profile_languages()
                if eng is not None:
                    _ENGINES.append(("auto", eng))
            except Exception:  # noqa: BLE001
                pass
        if not _ENGINES:
            logger.warning("wf_ocr_no_engine")
            return []
        logger.info(f"wf_ocr_engines_ready tags={[t for t, _ in _ENGINES]}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_ocr_engine_load_failed: {type(e).__name__}: {e}")
    return _ENGINES


def backend() -> str:
    """当前使用的 OCR 后端：``rapid`` / ``winrt`` / ``none``。

    ``WF_OCR_BACKEND`` 可选 ``auto``(默认) / ``rapid`` / ``winrt``：
      * ``auto``  **优先 RapidOCR** —— 实测（2026-11-02 三宝聚卡）RapidOCR 整行输出、
        一次读对；Windows OCR 把中文拆成单字且「三」误读成「二」。RapidOCR 还跨平台，
        是整体进 Docker（Linux）的前提。
      * ``winrt`` 强制 Windows.Media.Ocr（旧行为，保留以便回退对比）。
    """
    want = (os.environ.get("WF_OCR_BACKEND") or "auto").strip().lower()
    from . import ocr_rapid
    rapid_ok = ocr_rapid.available()
    if want == "rapid":
        return "rapid" if rapid_ok else "none"
    if want == "winrt":
        return "winrt" if _load_engines() else ("rapid" if rapid_ok else "none")
    if rapid_ok:
        return "rapid"
    return "winrt" if _load_engines() else "none"


def available() -> bool:
    return backend() != "none"


async def _recognize_one(data: bytes, engine) -> str:
    """单个引擎识别。"""
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    dw = DataWriter(stream)
    dw.write_bytes(data)
    await dw.store_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    return "\n".join(line.text for line in result.lines)


def _preprocess(img: Image.Image) -> Image.Image:
    """放大 2 倍提升小字识别率（紫卡截图字小，且常是 QQ 缩略图）。

    经验：Windows OCR 对小字紫卡（292×285 实测）在 ~2 倍时中文识别最稳，
    过度放大（3~5 倍）反而让细笔画中文（暴/素/毒）错位成别的字，
    故固定 2 倍、仅在长边偏小（<1000px）时放大，避免无谓模糊。
    """
    w, h = img.size
    if max(w, h) < 1000:
        img = img.resize((w * 2, h * 2), Image.LANCZOS)
    return img


def _run_sync(data: bytes) -> str:
    """同步跑 OCR（to_thread 包装，规避事件循环）。多引擎结果合并去重。"""
    import asyncio as _a

    lines, seen = [], set()
    for _tag, eng in _load_engines():
        try:
            txt = _a.run(_recognize_one(data, eng))
        except Exception:  # noqa: BLE001
            continue
        for ln in txt.splitlines():
            s = ln.strip()
            if s and s not in seen:
                seen.add(s)
                lines.append(s)
    return "\n".join(lines)


def _preprocess_gray(img: Image.Image) -> Image.Image:
    """灰度 + 2 倍（部分紫卡字体在灰度下比彩色更稳，与彩色 2x 互补）。"""
    w, h = img.size
    if max(w, h) < 1000:
        img = img.convert("L").convert("RGB").resize((w * 2, h * 2), Image.LANCZOS)
    else:
        img = img.convert("L").convert("RGB")
    return img


def _preprocess_photo(img: Image.Image) -> Image.Image:
    """中等宽度（500~2000px）再放大，典型手机拍屏/长截图。

    为什么按**宽度**判定：手机截图常是 1080×2376 这类竖长图，若用 max(size) 判定
    永远 >1600、一次都不放大（实测「+65.3%」原尺寸读成「6，3」，丢小数点）。
    宽度才是决定文字像素高度的量。
    """
    w, h = img.size
    scale = 2.0 if w < 1400 else 1.4
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def _preprocess_small(img: Image.Image) -> Image.Image:
    """超小图专用：长边直接放到 ~1000px。

    为什么需要单独一档：``_preprocess`` 的 2x 对 292×285 这类卡面最稳，但 QQ 里
    传来的**裁切小图**（实测 180×127 的冰凇卡）在 360px 时中文引擎把 ``101.4``
    读成 ``10L4``（小数点丢成字母 L）；同一张图放到 ~1080px 就读对了。
    大图不启用这一档——``_preprocess`` 的注释里记着过度放大反而让细笔画中文错位。
    """
    w, h = img.size
    scale = max(2, min(6, round(1000 / max(w, h))))
    return img.resize((w * scale, h * scale), Image.LANCZOS)


async def ocr_image(data: bytes) -> str:
    """图片字节 → OCR 文本（多引擎 + 多预处理融合）。失败返回空串。

    为什么多预处理：Windows OCR 对紫卡字体偶发漏读整行（同一张图不同次跑
    结果不一致，词行时有时无）。彩色 2x 与灰度 2x 互补能显著提升词条行覆盖，
    代价是 2 倍识别耗时——紫卡分析是按需低频操作，可接受。
    """
    _b = backend()
    if _b == "none":
        return ""
    if _b == "rapid":
        from . import ocr_rapid
        return await asyncio.to_thread(ocr_rapid.ocr_image, data)
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001
        return ""
    lines: set[str] = set()
    preps = [_preprocess(img), _preprocess_gray(img)]
    # 中等宽度补一档放大：1080 宽的拍屏原来完全没被放大，小数点整行丢失（实测）。
    if 500 <= img.size[0] < 2000:
        preps.append(_preprocess_photo(img))
    # 极小图（缩略图/裁切卡面）多跑一档更大放大：多一档只是**增加**候选行，
    # 由 riven.py 的逻辑行聚合并按「位数多者优先」取优，不会覆盖已读对的结果。
    if max(img.size) < 500:
        preps.append(_preprocess_small(img))
    for prep in preps:
        buf = io.BytesIO()
        prep.save(buf, "PNG")
        try:
            for ln in (await asyncio.to_thread(_run_sync, buf.getvalue())).splitlines():
                s = ln.strip()
                if s:
                    lines.add(s)
        except Exception as e:  # noqa: BLE001 — 某变体失败不影响其他变体
            logger.warning(f"wf_ocr_variant_failed: {type(e).__name__}: {e}")
    return "\n".join(sorted(lines))


def _boxes_from_result(result, scale: float) -> list:
    """从单次识别结果抽取行级包围盒，坐标按 scale 还原到原图像素。

    winrt 的 OcrLine 不直接带 bounding_rect，包围盒在每行的 OcrWord 上，
    因此行盒子由各 word 盒子取并集得到（更稳，含单词间距）。
    """
    out = []
    for line in getattr(result, "lines", []) or []:
        xs, ys, xe, ye = [], [], [], []
        for w in getattr(line, "words", []) or []:
            r = getattr(w, "bounding_rect", None)
            if r is None:
                continue
            xs.append(r.x); ys.append(r.y)
            xe.append(r.x + r.width); ye.append(r.y + r.height)
        if not xs:
            continue
        x = min(xs); y = min(ys)
        w = max(xe) - x; h = max(ye) - y
        out.append((line.text.strip(),
                    int(x / scale), int(y / scale),
                    int(w / scale), int(h / scale)))
    return out


async def _recognize_boxes(data: bytes, engine) -> list:
    """单次引擎识别，返回 (text, x, y, w, h) 行级包围盒（预处理后图像素坐标）。

    缩放还原（÷ _preprocess 倍数）统一在 ocr_image_boxes 内完成。
    """
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    dw = DataWriter(stream)
    dw.write_bytes(data)
    await dw.store_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    return _boxes_from_result(result, 1.0)


def _run_sync_boxes(data: bytes) -> list:
    """同步跑多引擎、合并行包围盒（按 文本+行号 去重）。"""
    import asyncio as _a

    seen = set()
    merged = []
    for _tag, eng in _load_engines():
        try:
            boxes = _a.run(_recognize_boxes(data, eng))
        except Exception:  # noqa: BLE001
            continue
        for (t, x, y, w, h) in boxes:
            s = t.strip()
            if not s:
                continue
            key = (s, int(y / 4))
            if key in seen:
                continue
            seen.add(key)
            merged.append((s, x, y, w, h))
    merged.sort(key=lambda b: (b[2], b[1]))
    return merged


async def ocr_rank(data: bytes) -> str:
    """紫卡等级：裁右上角 → 2x → OCR → 返回 0~20 的等级数字（读不到返回 ""）。

    为什么单独裁：等级在游戏内**固定**渲染在卡面右上角、字号最小，
    全图 2x / 灰度 2x 都常整行漏读（实测 292×285 卡面 "18" 全图读不出，
    裁右上角后稳定读出）。等级位置对**所有**紫卡一致，属通用布局规则，
    不是针对某个武器的 hack。
    """
    if not available():
        return ""
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001
        return ""
    w, h = img.size
    # 多裁切变体逐个尝试（等级徽章在卡面右上，但不同卡面构图/边框占比不同）：
    #   宽松裁 (0.62→右, 0→30%H)@2x —— Ocucor 类实测可读
    #   紧裁   (0.70→右, 0→20%H)@4x —— Nukor 类实测可读（宽松裁读不出）
    # 命中任一即返回；裁后交给 ocr_image（长边<1000 再放 2x）。
    txt = ""
    for box, scale in (((int(w * 0.62), 0, w, int(h * 0.30)), 2),
                       ((int(w * 0.70), 0, w, int(h * 0.20)), 4)):
        crop = img.crop(box)
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        try:
            txt = await ocr_image(buf.getvalue())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"wf_ocr_rank_failed: {type(e).__name__}: {e}")
            continue
        if txt and re.search(r"\d", txt):
            break
    if not txt:
        return ""
    # 等级徽章数字 ≤16（紫卡自身等级 / MR 需求均不超 16）。
    # 实测坑：徽章左侧边框碎片常被 OCR 粘成前导 1（"8"→"18"，与 Rabbit 参考卡比对确认），
    # 故优先取 ≤16 的候选；若只有 >16 的候选，剥掉前导 1 再试。
    cands = [int(m.group()) for m in re.finditer(r"\d{1,2}", txt) if 0 <= int(m.group()) <= 16]
    if cands:
        return str(cands[0])
    for m in re.finditer(r"\d{1,3}", txt):
        s = m.group()
        if len(s) >= 2 and s.startswith("1") and s[1:].isdigit() and 0 <= int(s[1:]) <= 16:
            return s[1:]
    return ""


async def ocr_image_boxes(data: bytes) -> list:
    """图片字节 → 行级包围盒 [(text, x, y, w, h)]，坐标为**原始图**像素。

    用途：紫卡"布局感知"识别——诅咒（负）词条在游戏内永远渲染为红色，
    且固定排在最后一行。拿到盒子后可对词条行做颜色采样，直接判定负词条，
    而不依赖脆弱的"负号 OCR"。盒子坐标已按 _preprocess 的 2 倍放大还原。

    返回空列表表示 OCR 不可用或识别为空。
    """
    _b = backend()
    if _b == "none":
        return []
    if _b == "rapid":
        from . import ocr_rapid
        return await asyncio.to_thread(ocr_rapid.ocr_image_boxes, data)
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001
        return []
    orig_w = img.size[0]
    # 与 ocr_image 用**同一套预处理档位**：否则 ocr_image 读到的更准读数（如放大档
    # 才读出的「6 + 5．3」）不在盒子里，行聚合只能看到低档的「6，3」而被丢弃。
    preps = [_preprocess(img)]
    if 500 <= img.size[0] < 2000:
        preps.append(_preprocess_photo(img))
    if max(img.size) < 500:
        preps.append(_preprocess_small(img))
    merged: dict[tuple[str, int], tuple] = {}
    for proc in preps:
        scale = proc.width / max(1, orig_w)
        buf = io.BytesIO()
        proc.save(buf, "PNG")
        try:
            boxes = await asyncio.to_thread(_run_sync_boxes, buf.getvalue())
        except Exception as e:  # noqa: BLE001 — 某一档失败不影响其他档
            logger.warning(f"wf_ocr_boxes_failed: {type(e).__name__}: {e}")
            continue
        for (t, x, y, w, h) in boxes:
            ox, oy = int(x / scale), int(y / scale)
            ow, oh = int(w / scale), int(h / scale)
            merged.setdefault((t.strip(), int(oy / 4)), (t, ox, oy, ow, oh))
    return sorted(merged.values(), key=lambda b: (b[2], b[1]))
