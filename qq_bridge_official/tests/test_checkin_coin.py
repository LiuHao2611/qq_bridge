# -*- coding: utf-8 -*-
"""签到卡喵币（coins.png）写实金币判据 + 贴图链路回归测试（2026-11-10）。

按功能从 test_checkin_card.py 拆出（守宿主 20KB 门禁）。守三条契约：
  * 防覆盖：coins 是 MANUAL_ASSETS，build() 不覆盖、--check 不改字节；
  * 写实判据：新币 PASS，糊饼/空图/缝隙叠币三类反例全拦；
  * 贴图链路：draw() 不把深色写实币冲淡成发白球面（禁 RGBA 自身当 mask）。

不联网、不碰 Docker、不依赖 git 历史（反例全部本地确定性构造，提交前后恒成立）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import build_checkin_assets as BLD  # noqa: E402
import checkin_coin_gate as GATE  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

COINS = ROOT / "src/plugins/qq_bridge/pic/templates/assets/checkin_gold/coins.png"
GOLD_ICONS_SRC = ROOT / "src/plugins/qq_bridge/pic/templates/checkin_gold_icons.py"
BUILD_SRC = TOOLS / "build_checkin_assets.py"


def test_coins_is_manual_asset_and_build_wont_overwrite():
    """防覆盖契约：coins 登记在 MANUAL_ASSETS，build() 走 continue 跳过、不落 FA 镀金。

    曾有的真实风险：build() 遍历 ICONS 无条件用 FA `coins.svg` 覆盖，一跑就把
    AI 写实金币降级成三枚叠币矢量图标，且 git diff 才会发现。此测试锁死防覆盖。"""
    assert "coins" in GATE.MANUAL_ASSETS, "写实喵币必须登记为手工资产，禁止 build 覆盖"
    assert "coins" in BLD.MANUAL_ASSETS, "build 必须 import 到同一 MANUAL_ASSETS 契约"
    src = BUILD_SRC.read_text(encoding="utf-8")
    # build 循环里必须有 MANUAL_ASSETS 判断 + continue，且覆盖逻辑（g.save）在其后
    loop = src[src.index("for kind, slug in ICONS.items()"):
               src.index("for name, (stem, spec, why) in FONT_SPECS.items()")]
    assert "if kind in MANUAL_ASSETS:" in loop, "build 缺 MANUAL_ASSETS 跳过分支"
    assert "continue" in loop, "build 手工资产分支未 continue，仍会往下覆盖"
    assert loop.index("if kind in MANUAL_ASSETS:") < loop.index("g.save(p"), \
        "防覆盖判断须在 g.save 覆盖动作之前"
    # 写实币字节在跑过 --check（纯读）后不变
    before = COINS.read_bytes()
    r = subprocess.run([sys.executable, str(BUILD_SRC), "--check"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert COINS.read_bytes() == before, "--check 不得改动写实喵币字节"


def test_photo_coin_gate_admits_real_and_blocks_all_counterexamples(tmp_path):
    """写实金币判据四类样本：新币 PASS；缝隙叠币/纯色饼/空图必须各自被拦。

    反例全部本地确定性构造（不读 git HEAD —— 提交后 HEAD 已是新币，旧 FA 反例会失效）：
      * 缝隙叠币：圆盘中心挖空（模拟三枚叠币/非单枚实心）-> core_solid < 0.97 命中；
      * 纯色饼：占比对但 lum_std=0/feather=0 -> 明暗+羽化命中；
      * 空图：四项全挂。
    """
    assert GATE.coin_photo_problems(COINS) == [], GATE.coin_photo_problems(COINS)
    # 判据经 build 脚本转出的入口也必须可用（容器 --check 走这条）
    assert BLD.coin_photo_problems(COINS) == []

    # 1) 缝隙叠币：外环金属 + 中心大孔（core 区大面积透明 -> core_solid 掉到 <0.97）
    gapped = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gapped)
    gd.ellipse((4, 4, 124, 124), fill=(150, 110, 20, 255))
    gd.ellipse((34, 34, 94, 94), fill=(0, 0, 0, 0))       # 中心挖空 = 非单枚实心
    gp = tmp_path / "gapped.png"
    gapped.save(gp, "PNG")
    # 2) 纯色饼
    flat = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(flat).ellipse((2, 2, 126, 126), fill=(201, 150, 20, 255))
    fb = tmp_path / "flat.png"
    flat.save(fb, "PNG")
    # 3) 空图
    empty = tmp_path / "empty.png"
    Image.new("RGBA", (128, 128), (0, 0, 0, 0)).save(empty, "PNG")
    for name, p in (("缝隙叠币", gp), ("纯色饼", fb), ("空图", empty)):
        probs = GATE.coin_photo_problems(p)
        assert probs, "%s 未被写实金币判据拦下（回退/糊饼会漏进交付）" % name


def test_gold_icon_paste_does_not_wash_out_dark_coin():
    """贴图链路回归（2026-11-10 实卡抓到）：draw() 不得把深色写实金币冲淡成发白球面。

    根因：曾 ``paste_alpha(layer,(x,y),layer)`` 把 RGBA 自身当 mask，paste_alpha 对
    非 L mask 会 convert('L') 取亮度并乘进 alpha，深色币(亮度≈0.34)被削到 34%，浅橙底
    透出 -> 发白。本测试对比「正确(无 mask)」与「错误(带 mask)」两路的平均 RGB：
    正确路须保住暖金深色（接近资产本体），错误路须明显发白（证明判据抓得住该 bug）。"""
    from src.plugins.qq_bridge.pic.canvas import Canvas
    from src.plugins.qq_bridge.pic.theme import resolve
    src = Image.open(COINS).convert("RGBA")
    bb = src.getbbox()
    src = src.crop(bb)
    size = 40
    s = size / max(src.size)
    layer = src.resize((round(src.width * s), round(src.height * s)), Image.LANCZOS)
    bg = "#F1E7DC"                        # 当前喵币胶囊浅橙底实测色

    def mean_rgb(img):
        # 只取中心 50% 纯金币面（避开羽化边混入的浅橙底），三者同口径
        w, h = img.size
        c = img.crop((int(w * .25), int(h * .25), int(w * .75), int(h * .75))).convert("RGB")
        px = list(c.getdata())
        return tuple(sum(p[i] for p in px) / len(px) for i in range(3))

    body = mean_rgb(layer)                              # 资产本体中心实金色
    # 正确路：生产 draw() 走无 mask；合成到浅橙底后中心应仍是实金深色
    good_canvas = Canvas(80, 80, bg, theme=resolve("light"))
    good_canvas.paste_alpha(layer, (20, 20))             # 无 mask（生产路径）
    good = mean_rgb(good_canvas.img.crop((20, 20, 20 + layer.width, 20 + layer.height)))
    # 错误路：带 layer 自身当 mask（旧 bug），中心实金色也必须被冲淡发白
    bad_canvas = Canvas(80, 80, bg, theme=resolve("light"))
    bad_canvas.paste_alpha(layer, (20, 20), layer)       # 旧 bug 复现
    bad = mean_rgb(bad_canvas.img.crop((20, 20, 20 + layer.width, 20 + layer.height)))
    # 正确路保住深色：中心实金面与资产本体每通道差 < 35；错误路被冲淡：至少一通道亮 > 35
    assert max(abs(good[i] - body[i]) for i in range(3)) < 35, \
        "draw 无 mask 路应保住写实金币中心实金色，实得画布%s vs 本体%s" % (good, body)
    assert max(bad[i] - good[i] for i in range(3)) > 35, \
        "带 mask 的错误路必须明显发白（证明本测试能抓住该回归），实得%s vs %s" % (bad, good)
    # 生产源码必须无 mask 调用，防有人加回
    src_text = GOLD_ICONS_SRC.read_text(encoding="utf-8")
    assert "paste_alpha(layer, (x0, y0), layer)" not in src_text, \
        "禁止把 RGBA layer 自身当 mask 传回 paste_alpha（会 reintroduce 发白 bug）"
