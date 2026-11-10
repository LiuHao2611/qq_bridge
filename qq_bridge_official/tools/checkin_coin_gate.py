# -*- coding: utf-8 -*-
"""签到卡喵币（coins.png）写实金币判据 + 手工资产契约（2026-11-10）。

按功能从 build_checkin_assets.py 拆出（守宿主 20KB 门禁）：本模块只依赖 Pillow，
是「写实金币是不是真的、build 该不该覆盖它」的**唯一判据来源**，供
  * ``build_checkin_assets.py`` 的 build()/check() 调用；
  * ``tests/test_checkin_coin.py`` 直接单测 + 反例注入；
容器 ``--check`` 也走这里（只依赖 Pillow，不拖累生成链依赖）。

用户诉求：「喵币不对，我要真实画质真实质感、史诗电影级别的一枚正面喵币、黄金铸造的」。
coins.png 从 FA 三枚叠币矢量图标升级为 AI 生成电影级写实单枚正面金币，随代码入库，
**不由 build() 复现**，故登记为手工资产 + 独立写实判据（旧稀疏图标占比阈值不适用）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

#: 手工/AI 资产：build() 不得用 FA 矢量镀金覆盖（否则写实金币被降级成三枚叠币图标）。
#: 这些图不由 build 复现，随代码入库；--check 用写实特征校验其为真（coin_photo_problems）。
MANUAL_ASSETS = frozenset({"coins"})

#: 写实金币判据阈值 —— 由 .tmp/coin_candidates/probe_metrics.py 对真实样本实测标定：
#:   OLD(FA三枚叠币) frac=.564 core=.907 lum=69.2 feather=.077
#:   NEW(AI写实币)   frac=.802 core=1.000 lum=48.4 feather=.055
#:   FLAT(纯色饼)    frac=.748 core=1.000 lum= 0.0 feather=0
#:   EMPTY(空)       frac=0
#: 写实币是**单枚实心正圆**（core≈1，排除叠币缝隙）+ 真实明暗层次（lum 中区间，
#: 排除纯色饼与过曝）+ 抠图羽化边（feather>0，排除硬切）。写死阈值不写死哈希/像素，
#: 换一张同质量写实币仍 PASS，旧 FA 币/糊饼/空图必 FAIL。
_COIN = {"frac": (0.70, 0.90), "core": 0.97, "lum": (18.0, 100.0), "feather": (0.01, 0.20)}
OUT_PNG = 128                     # 与 build_checkin_assets.OUT_PNG 一致（gold 图标统一尺寸）


def coin_metrics(im: Image.Image) -> dict[str, float]:
    """写实金币的四个可解释图像特征（只依赖 Pillow，供判据与测试共用）。"""
    im = im.convert("RGBA")
    a = im.getchannel("A")
    av = a.tobytes()
    W, H = im.size
    n = W * H
    frac = sum(1 for v in av if v > 16) / n
    feather = sum(1 for v in av if 32 < v < 224) / n
    core = [a.getpixel((x, y))
            for y in range(int(H * .3), int(H * .7))
            for x in range(int(W * .3), int(W * .7))]
    core_solid = sum(1 for v in core if v > 200) / len(core)
    gray = im.convert("L")
    opaque = [g for g, al in zip(gray.tobytes(), av) if al > 16]
    mean = sum(opaque) / len(opaque) if opaque else 0.0
    lum_std = (sum((g - mean) ** 2 for g in opaque) / len(opaque)) ** .5 if opaque else 0.0
    return {"frac": frac, "core": core_solid, "lum": lum_std, "feather": feather}


def coin_photo_problems(p: Path, out_png: int = OUT_PNG) -> list[str]:
    """写实金币内容判据：单枚实心正圆 + 真实金属明暗 + 羽化透明边。

    ``p`` 为 coins.png 路径。缺文件/损坏/纯色饼/旧 FA 矢量叠币/空图全都必须报错。
    """
    if not p.is_file():
        return ["写实金币缺真实 PNG：%s（AI 资产，随代码入库，build 不生成）" % p]
    try:
        im = Image.open(p).convert("RGBA")
    except Exception as e:
        return ["写实金币损坏 %s: %s" % (p, e)]
    if im.size != (out_png, out_png):
        return ["写实金币尺寸 %s 应为 %dx%d，实得 %s" % (p, out_png, out_png, im.size)]
    m = coin_metrics(im)
    p: list[str] = []
    lo, hi = _COIN["frac"]
    if not lo < m["frac"] < hi:
        p.append("写实金币非透明占比 %.3f 不在 (%.2f,%.2f)（空/糊饼/非单枚）" % (m["frac"], lo, hi))
    if m["core"] < _COIN["core"]:
        p.append("写实金币中心实心率 %.3f < %.2f（疑似 FA 叠币/有缝隙，非单枚实心盘）"
                 % (m["core"], _COIN["core"]))
    llo, lhi = _COIN["lum"]
    if not llo < m["lum"] < lhi:
        p.append("写实金币明暗层次 std=%.1f 不在 (%.0f,%.0f)（纯色饼/过曝，非真实金属光影）"
                 % (m["lum"], llo, lhi))
    flo, fhi = _COIN["feather"]
    if not flo < m["feather"] < fhi:
        p.append("写实金币透明羽化边占比 %.4f 不在 (%.2f,%.2f)（硬切边/无抗锯齿）"
                 % (m["feather"], flo, fhi))
    return p
