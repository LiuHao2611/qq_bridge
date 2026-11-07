# -*- coding: utf-8 -*-
"""单图诊断：对一张紫卡截图跑完整 OCR + 解析管线，打印每阶段产物。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))                      # 让 src.group_switch 可导入
sys.path.insert(0, str(ROOT / "src" / "plugins"))  # 让 qq_bridge.game_tools 可导入

IMG = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"D:\WorkBuddyData\blobs\20\20001f659ecd389551af1eb42066d1ea9cc68a007882c5bed54dfe072b30eb52.png")

from qq_bridge.game_tools import riven, ocr as ocr_mod


async def main():
    data = IMG.read_bytes()
    print("== OCR available:", ocr_mod.available())
    text = await ocr_mod.ocr_image(data)
    print("\n== RAW OCR TEXT (ocr_image) ==")
    print(repr(text))
    print("\n== LINES ==")
    for i, ln in enumerate(text.splitlines()):
        print(f"  {i:02d} | {ln!r}")

    boxes = await ocr_mod.ocr_image_boxes(data)
    print(f"\n== BOXES ({len(boxes)}) ==")
    for (t, x, y, w, h) in boxes:
        print(f"  y={y:4d} x={x:4d} w={w:4d} h={h:3d} | {t!r}")

    rk = await ocr_mod.ocr_rank(data)
    print("\n== RANK crop OCR:", repr(rk))

    parsed = riven._parse_ocr(text, boxes=boxes, img_data=data)
    print("\n== PARSED ==")
    print(parsed)

    if not parsed:
        print("\n== DIAGNOSE ==")
        print(riven._ocr_diagnose(text, boxes))

    # 单独看武器名模糊命中
    disp = riven._RIVEN["disp"] or {}
    zh = riven._RIVEN["zh"] or {}
    riven._load_riven_data()
    fw = __import__("qq_bridge.game_tools.warframe", fromlist=["_load_translate_data"])
    fw._load_translate_data()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    wk = riven._fuzzy_weapon(lines, disp, zh)
    print("\n== _fuzzy_weapon result:", wk)
    print("== disp weapons count:", len(disp))


if __name__ == "__main__":
    asyncio.run(main())
