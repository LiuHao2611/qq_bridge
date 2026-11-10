# 签到卡黄金铸造图标

7 枚 128×128 RGBA PNG，签到卡（`checkin.py`）运行时只读贴图，实现「黄金浮雕 / 鎏金手札」风格，
替代旧的 PIL 单色几何图标。加载/缺图契约见 `../../checkin_gold_icons.py`，生成入口见
`../../../../../tools/build_checkin_assets.py`（仓库相对路径：`tools/build_checkin_assets.py`）。

| 文件 | 用途 | FA 6.5.0 Solid 源 |
| --- | --- | --- |
| `clock.png` | 顶部标题闹钟 | `clock` |
| `bolt.png` | 连签次数 | `bolt` |
| `check.png` | 累计签到 | `check` |
| `coins.png` | 当前喵币（替换旧 `diamond` 几何） | `coins` |
| `arrow-up.png` | 距里程碑 | `arrow-up` |
| `star.png` | 群内名次 | `star` |
| `wand-magic-sparkles.png` | 本次奖励胶囊 | `wand-magic-sparkles` |

## 来源与许可

- **图标形状**：Font Awesome Free **6.5.0** Solid，SVG 取自
  `https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.5.0/svgs/solid/`。
  图标属 **CC BY 4.0**（署名许可）；商标与字体部分不在本授权内（本项目只取 SVG 路径形状）。
  项目：https://fontawesome.com/ · 许可：https://creativecommons.org/licenses/by/4.0/
- **镀金加工**：宿主离线用 `svgpathtools + aggdraw + Pillow` 生成金色渐变、顶部高光、深铜描边、
  内阴影与柔和投影（本项目自绘，无第三方版权素材）。运行时**不依赖** SVG/ aggdraw / cairo。

> 署名：Icons by Font Awesome (https://fontawesome.com), licensed CC BY 4.0.
> Modified: gilded / embossed offline into gold-relief PNG for check-in card.

## 复现与校验

```bash
python tools/build_checkin_assets.py            # 重新下载源 SVG 并镀金生成（需联网一次）
python tools/build_checkin_assets.py --check     # 只校验 7 枚 PNG 齐全、尺寸/内容为真
```

缺任意一枚：`checkin_gold_icons.gate(strict=True)` 在 `checkin.build()` 入口直接 `SystemExit`，
**禁止**静默用几何兜底冒充交付（AGENTS.md 规则表「禁止降级开发/兜底」）。
