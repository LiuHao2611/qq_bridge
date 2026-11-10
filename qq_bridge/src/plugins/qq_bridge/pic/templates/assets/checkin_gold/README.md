# 签到卡黄金铸造图标

7 枚 128×128 RGBA PNG，签到卡（`checkin.py`）运行时只读贴图，实现「黄金浮雕 / 鎏金手札」风格，
替代旧的 PIL 单色几何图标。加载/缺图契约见 `../../checkin_gold_icons.py`，生成入口见
`../../../../../tools/build_checkin_assets.py`（仓库相对路径：`tools/build_checkin_assets.py`）。

| 文件 | 用途 | 来源 |
| --- | --- | --- |
| `clock.png` | 顶部标题闹钟 | FA 6.5.0 `clock` 镀金 |
| `bolt.png` | 连签次数 | FA 6.5.0 `bolt` 镀金 |
| `check.png` | 累计签到 | FA 6.5.0 `check` 镀金 |
| `coins.png` | 当前喵币（**电影级写实单枚金币**） | **本项目 AI 生成 + 圆形抠图 + 暖金调色**（见下） |
| `arrow-up.png` | 距里程碑 | FA 6.5.0 `arrow-up` 镀金 |
| `star.png` | 群内名次 | FA 6.5.0 `star` 镀金 |
| `wand-magic-sparkles.png` | 本次奖励胶囊 | FA 6.5.0 `wand-magic-sparkles` 镀金 |

## 来源与许可

- **6 枚 FA 图标形状**：Font Awesome Free **6.5.0** Solid，SVG 取自
  `https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.5.0/svgs/solid/`。
  图标属 **CC BY 4.0**（署名许可）；商标与字体部分不在本授权内（本项目只取 SVG 路径形状）。
  项目：https://fontawesome.com/ · 许可：https://creativecommons.org/licenses/by/4.0/
- **6 枚镀金加工**：宿主离线用 `svgpathtools + aggdraw + Pillow` 生成金色渐变、顶部高光、深铜描边、
  内阴影与柔和投影（本项目自绘，无第三方版权素材）。
- **`coins.png`（喵币）**：用户 2026-11-10 要求「真实画质真实质感、史诗电影级的一枚正面喵币、
  黄金铸造」，弃用原 FA `coins` 三枚叠币矢量图标。改用 AI 文生图生成**单枚正面猫首浮雕金币**
  电影级布光素材，经本项目圆形自适应 alpha 抠图（去右下角水印、圆形羽化边、暖金提亮）得到
  128×128 RGBA。**本项目自制资产**，无第三方图标版权素材；生成底模为腾讯混元（WorkBuddy
  ImageGen）。该图**不由 `build()` 复现**，登记在 `MANUAL_ASSETS`，随代码入库，缺图须从版本库恢复。

> 署名（仅 6 枚 FA 图标）：Icons by Font Awesome (https://fontawesome.com), licensed CC BY 4.0.
> Modified: gilded / embossed offline into gold-relief PNG for check-in card.
> `coins.png` 不在上述 FA 授权范围，为本项目 AI 生成并加工的原创资产。

## 复现与校验

```bash
python tools/build_checkin_assets.py            # 重新下载 6 枚 FA 源 SVG 并镀金生成；coins.png 跳过不覆盖
python tools/build_checkin_assets.py --check     # 校验 7 枚 PNG 齐全、尺寸/内容为真（coins 走写实金币判据）
```

- `build()` 对 `MANUAL_ASSETS`（= `coins`）**绝不覆盖**：缺图直接 `SystemExit` 提示从版本库恢复，
  禁止静默回落 FA 矢量金币（AGENTS.md「禁止降级开发/兜底」）；即使误跑 build，写实币字节不变。
- `--check` 对 coins 走独立**写实金币判据**（唯一来源 `tools/checkin_coin_gate.py::coin_photo_problems()`，
  按功能从 build 拆出守 20KB；单枚实心正圆 core_solid≥0.97 + 真实金属明暗 lum_std∈(18,100)
  + 抠图羽化边 feather∈(0.01,0.20) + 占比 frac∈(0.70,0.90)），缝隙叠币/纯色饼/空图三类
  本地确定性反例全部拦下；阈值由四类样本实测标定（`.tmp/coin_candidates/probe_metrics.py`）。
- 缺任意一枚：`checkin_gold_icons.gate(strict=True)` 在 `checkin.build()` 入口直接 `SystemExit`，
  **禁止**静默用几何兜底冒充交付。
