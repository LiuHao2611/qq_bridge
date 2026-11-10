# 签到卡分层风格字体

4 款子集 TTF，让签到卡「该粗则粗、不同字用不同字体」（用户 2026-11-10 诉求）。
`checkin.py` 经 `../../checkin_font.py` 的 `route(size, role)` 分流，绘制与度量走**同一个**
ImageFont（避免 `canvas.py` 里「绘制读 font_family、度量读 role」的决策分裂）；本卡专用，
不改全局 `fonts.py/canvas.py`。

| 文件 | 逻辑角色 | 用途 | Google Fonts 家族 |
| --- | --- | --- | --- |
| `display.ttf` | 毛笔板书 | 主标题「签到」（size≥36） | Ma Shan Zheng |
| `section.ttf` | 硬笔行楷 | 4 个分区标题（size=20） | Long Cang |
| `heavy.ttf` | 圆润粗体 | `role="bold"` 数值/强调（伪粗描边加字重） | ZCOOL KuaiLe |
| `body.ttf` | 圆润正文 | 其余正文/标签/月历/页脚 | ZCOOL KuaiLe |

## 来源与许可

4 款均取自 **Google Fonts**（https://fonts.google.com/），许可 **SIL Open Font License 1.1**
（https://openfontlicense.org/）。许可证全文见各家族上游仓库；OFL 允许嵌入、再分发与子集化，
**唯独禁止单独出售字体本身**，本项目仅以子集形式嵌入卡片渲染，符合许可。

## 子集策略（体积与字形安全）

- 字体原文件 0.6~5.9MB，**全量入库约 20MB**（GB2312 主字集，按 GB2312 子集只缩 95%~99%，
  实测无效）。故只保留「卡面固定文案字符集」——从 `checkin.py`/`checkin_parts.py` 字符串字面量
  `ast` 抽取（约 380~404 字），4 款合计 <700KB。
- **动态昵称**（如「林浩 的签到小屋」）含子集外字时，`checkin_font.resolve()` 整串回退
  vendored 雅黑 `msyh.ttc`（已随 `COPY src` 进镜像），**绝不逐字混排**（否则 shaping/getlength
  累加与整串度量不一致、包围盒错位）。
- 风格字体普遍缺 `✓▶★☆●→` 等符号，`·` 仅站酷快乐体有：卡面相应位置改几何绘制或纯文本
  「已达成/下一档」，杜绝豆腐框。生成时按角色字集校验零缺字。

## 复现与校验

```bash
python tools/build_checkin_assets.py            # 联网取 TTF -> 按固定字集子集化（UA 强制返回 ttf）
python tools/build_checkin_assets.py --check     # 校验 4 款齐全、按角色字集零缺字
```

缺任意一款：`checkin_font.gate(strict=True)` 在 `checkin.build()` 入口直接 `SystemExit`。
