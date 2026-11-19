# DIY 硬件行情卡（`硬件行情`）

给 QQ 机器人加的一条**只读、可复现**的装机行情口令：一张图说清本期「什么硬件在涨、几涨几跌、涨多少」，
带型号再出一张单款走势卡。

```
硬件行情                 → 总览卡（显卡 / CPU / 内存存储 三分类涨跌 + 全市场涨跌榜）
硬件行情 5090            → 单型号卡（现价 / 环比 / 档案高低点 / 走势 / 天梯跑分）
硬件行情 7800x3d         → 同上（支持 RTX/RX 前缀、i5/i7 写法、大小写混写）
硬件行情 DDR5 32G        → 内存按「整条容量」检索，卡面同时给 元/GB 与整条折算价
装机行情 / 硬件价格 / 电脑硬件 / diy行情   → 同义口令
```

## 1. 数据源调研（2026-11）

| 候选 | 市场 / 币种 | 形态 | 结论 |
|---|---|---|---|
| **x1shang/frocloud-DIYPCmarket** | 中国大陆 DIY / CNY | 手工维护的 Excel → 7 份 CSV，月度，含 GPU/CPU 天梯与品牌表 | **采用**：唯一覆盖「整机三大件 + 人民币 + 涨跌」的档案，且带校验脚本与明确许可 |
| nerdyaf/ram-price-tracker | 美国 / USD | PCPartPicker + Playwright + SQLite + Streamlit | 弃：只做 DDR5，需无头浏览器，口径是美元零售 |
| yachty66/gpu-price-tracker | 美国 / USD | 每卡一个 JSON，GitHub Action 日更 | 弃：只有显卡，美元 |
| HardwareDealsCo/gpu-deals | 美国 / USD | 每日 deals JSON | 弃：促销价，不是市场价 |
| alex-hubbard/gpu_price_tracker | 云 GPU 租赁 / USD/h | Parquet 数据集 | 弃：算力租赁，与装机无关 |

选型只看三条：**人民币真实到手价**、**多品类**、**可离线复现**。frocloud 三条全中，
其余最多中一条。

## 2. 全量搬运（唯一入口）

```bash
# 首次 / 上游更新：从 GitHub 拉 main 的 tarball 并整体替换本地副本
python tools/vendor_frocloud.py --download

# 离线：从已解压目录搬运（必须显式给 commit）
python tools/vendor_frocloud.py --from <dir> --commit <40位sha>

# 校验：逐文件 sha256 + 有无多余/缺失
python tools/vendor_frocloud.py --verify
```

- 目标目录：`src/plugins/qq_bridge/hardware/vendored_frocloud/`（22 文件 / 185 KB，含 7 份 CSV、
  README、AGENTS、DISCLAIMER、LICENSE、LICENSES、THIRD_PARTY_NOTICES、上游 3 个 tools、git 元数据）。
- 清单：`hardware/UPSTREAM.json` —— `repo / commit / commit_date / vendored_at / data_asof /
  license / excluded / files[path,size,sha256]`。
- **`frocloud.xlsx` 不搬**：上游 `AGENTS.md` §0 是绝对红线（xlsx 只准人手工编辑，任何脚本不得
  创建/修改/重写/移动/删除）。本项目只读 `data/*.csv`，不碰设计稿。
- `data/*.csv` 在本仓被根 `.gitignore` 的 `data/` 命中，已在 `.gitignore` 里精确放行该目录
  （只放行这两层，不放行其它 `data/`）。

## 3. 口径（照抄上游，不自行发挥）

| 项 | 规则 |
|---|---|
| 时间 | **月度**快照，不是日内行情；最新一期 2026-10，上游标注数据截止 **2026-10-02** |
| 显卡 / CPU 价格 | 人民币元，全新、个人购买途径（电商最低价，含补贴/大促） |
| 内存 / 固态 / 机械 | **元每 GB**（1GB=1000MB）。整条价 = 每 GB 价 × 规格容量，卡面注明「折算」 |
| 涨跌 | 同标的「最新一期 vs 上一期」百分比；`|环比| < 0.05%` 记持平（与卡面 0.1% 精度一致） |
| 覆盖 | 只统计两期都有报价的标的；面板写「可比数 / 在售数」，不把缺报当成 0 |
| 非标准月份 | GPU 表有一期 `2026-07-下旬`，按上/中/下旬排序权重处理，**不改写成 `2026-07`** |
| 天梯 | 上游 `gpu-tier` / `cpu-tier`；档位为空显示「上游未分级」，备注（如「缩缸/停产」）原样上卡 |

> 为什么不写「当天」：上游是人工月度档案，写「今日」就是编数据。需要日内报价必须换数据源，
> 那是另一次选型，不是改一行文案。

## 4. 代码结构

```
src/plugins/qq_bridge/hardware/
├── __init__.py        口令 matcher（priority=5, block=True）+ 参数解析 + 发图/显式报错
├── dataset.py         只读数据模型：CSV → Instrument/Dataset；月份排序；容量换算
├── market.py          口径层：涨跌统计、榜首、模糊检索、卡面 payload
├── UPSTREAM.json      搬运清单（commit + data_asof + 逐文件 sha256）
└── vendored_frocloud/ 上游全量副本（只读，手改会被 --verify 抓）
src/plugins/qq_bridge/pic/templates/hw_market.py   两个视图的卡片渲染（先算布局再画）
tools/vendor_frocloud.py                           搬运 / 校验
tools/check_hw_market_layout.py                    布局 + 数据 + 口径门禁（含 --selftest）
tests/test_hw_market.py                            23 用例
tests/test_hw_daily.py                             14 用例（ZOL 解析/分词/涨跌/调度，离线）
```

## 4.1 两个通道

| 通道 | 事件类型 | matcher |
|---|---|---|
| NapCat（OneBot V11） | `MessageEvent` | `on_regex(priority=5, block=True)` |
| QQ 官方 Bot（nonebot-adapter-qq） | `GroupMessageCreateEvent` / `C2CMessage*` | `on_message(priority=5, block=True, rule=…)` + adapter 守卫，复用 `game_tools.official_bridge.OfficialLikeEvent` |

官方事件注入不到 OneBot 类型的 handler（TypeMisMatch），**两条通道必须各注册一个 matcher**；
官方副本 `qq_bridge_official/` 是独立部署目录（不入 git），改动需同步过去再重建容器。


## 3.1 每日行情（默认视图，2026-11-19 新增）

```
硬件行情                → 今日卡（ZOL 当日参考价，涨跌 = 今日 vs 上一份快照）
硬件行情 月报           → frocloud 月度档案（显卡/CPU/内存存储三分类，历史涨跌）
硬件行情 5070           → 单款今日价 + 本地日线 + 天梯
```

**源：ZOL 中关村在线公开系列页**（`detail.zol.com.cn/vga/s<系列ID>/`），不需要登录、无浏览器。
当前跟踪 12 个有系列页的显卡：RTX 5050 / 5060 / 5060 Ti 8G·16G / 5070 / 5070 Ti / 5080 /
5090 D / 5090 D v2 / RX 9070 / 9070 XT / 9070 GRE。每个系列取当日**样本最低价**并记录样本数。

**为什么 CPU / 内存没有日更**（实测，不是偷懒）：
- ZOL 的 CPU 列表/家族页只公开十来个「热门」型号，把 frocloud 当前 24 个在售型号拿去匹配
  只命中 3 个（9600X / 9700X / 9800X3D）；只报 3 款会让「几涨几跌」变成误导。
- 京东 `p.3.cn` 价格接口在本机与容器均不可达；京东/拼多多/闲鱼的完整采集都要登录态浏览器
  （账号风控 + 封号风险，与本项目账号安全红线冲突）。
- 所以 CPU 与内存存储继续走 frocloud 月度（`硬件行情 月报`），卡面固定写「CPU / 内存存储：
  ZOL 公开列表无稳定日价」。

**调度**：每天 **10:10（北京时间）** 自动采一次。理由：早间电商调价已完成、页面报价稳定；
避开 0–8 点维护与 0 点大促抖动。可用 `HW_DAILY_AT=HH:MM` 覆盖；服务重启时若当天还没采且已过点，
启动 45 秒后补采一次。用户手动问且快照超过 12 小时 → 当场重采（最多一次，避免刷 ZOL）。

**存储**：`data/hardware_daily/YYYY-MM-DD.json`（原子写、保留 180 天）。涨跌只和**上一份快照**比，
第一天是「首日基线」，卡面不画空榜，改画「今日在售」价格表。

**口径**：ZOL 页面的「参考报价」，不是某家店实时成交价；卡面页脚写「每日 10:10 自动抓取 · 仅供参考」，
门禁禁止出现「实时 / 秒杀 / 成交价」这类词。

## 5. 验收

```bash
python tools/vendor_frocloud.py --verify
python tools/check_hw_market_layout.py --check --out tmp/hw.png   # 含今日卡（hw_daily_gate）
python tools/hw_daily_gate.py
python tools/check_hw_market_layout.py --selftest
python -m pytest tests/test_hw_market.py -q
```

门禁查八类：盒越界、盒重叠、同房间距、必备盒缺失、面板内文字溢出、**自己重读 CSV 重算涨跌家数与榜首**、
口径词（必须有「月度/非日内」，禁止「今日/实时行情」）、内存单位（元/GB + 整条折算）。
`--selftest` 注入六类真实错误，全部必须被抓：页脚盒丢失、面板重叠、家数篡改、
口径写成今日、画布高度少算 40px、面板压窄致文字溢出。

## 6. 上游更新流程

1. `python tools/vendor_frocloud.py --download`（自动读 main 的 commit 与 README 截止日）
2. `python tools/vendor_frocloud.py --verify`
3. `python tools/check_hw_market_layout.py --check`
4. `python -m pytest tests/test_hw_market.py -q`
5. 改 `tests/test_hw_market.py` 里「本期真实值」三条断言（数据变了就要有人看一眼）

## 7. 许可

上游为混合许可：**数据与文档 CC BY 4.0，`tools/` 脚本 MIT**。本项目保留
`LICENSE` / `LICENSES/CC-BY-4.0.txt` / `LICENSES/MIT.txt` / `THIRD_PARTY_NOTICES.md`，
卡面页脚标注来源 `frocloud-DIYPCmarket`。上游明确声明其为个人整理的参考数据，
不构成投资/购买建议。
