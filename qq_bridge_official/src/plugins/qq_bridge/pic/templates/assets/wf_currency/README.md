# 市价卡货币图标 · 真实游戏内图标资产

三枚 PNG 是 Warframe **游戏内货币图标**的官方转录（Wiki Text Icons，64×64 RGBA），
统一裁透明边后 **等比 contain** 进 128×128 RGBA 透明画布（不拉伸变形）。
它们是**源码资产**，随代码交付；`qq_bridge/.gitignore` 末尾对这三张精确放行，
绕开主项目根 `.gitignore` 的全局 `*.png`。

| 文件 | 货币 | 来源 | 结论 |
|---|---|---|---|
| `platinum.png` | 白金 Platinum | 灰机 WARFRAME 中文维基 `File:Platinum64.png` → [直链](https://huiji-public.huijistatic.com/warframe/uploads/1/15/Platinum64.png) | 游戏内银色圆环＋蓝宝石白金图标 |
| `credits.png` | 现金 Credits | 灰机 WARFRAME 中文维基 `File:Credits64.png` → [直链](https://huiji-public.huijistatic.com/warframe/uploads/b/b1/Credits64.png) | 游戏内三张 Corpus 信用点卡 |
| `ducats.png` | 杜卡德 Ducats | 灰机 WARFRAME 中文维基 `File:PrimeBucks.png`（重定向 `OrokinDucats.png`）→ [直链](https://huiji-public.huijistatic.com/warframe/uploads/d/d5/OrokinDucats.png) | 游戏内金色 Orokin 纹章 |

## 为什么用灰机镜像

- 官方 `wiki.warframe.com/w/Special:FilePath/*`、`/images/*`、`warframe.fandom.com`、
  `warframe-archive.fandom.com`、`static.wikia.nocookie.net` 及多种 CORS 代理，
  在本机均被 Cloudflare 403 / 超时拦截，拿不到二进制 PNG。
- 灰机 MediaWiki API 可达，返回的是**同源 Wiki Text Icons**：
  `https://warframe.huijiwiki.com/api.php?action=query&titles=File%3APlatinum64.png&prop=imageinfo&iiprop=url&format=json`
- DE 官方 Public Export（`content.warframe.com/PublicExport`）与游戏客户端同源，
  `Lotus/Interface/Icons/StoreIcons/Currency/` 下有 `Ducat.png` 与
  `CorpusCreditCardLow.png`，但 **manifest 20154 条记录里没有独立白金货币图标**
  （`platinum` 命中的 803 条全是 Relic Projection，另 2 条是 Specter 道具）。
  因此 DE 导出只作为灰机失败时的降级源与交叉验证：`Ducat.png` 与灰机杜卡德肉眼一致。

结论：**三枚全部是真实游戏内图标，没有几何占位、没有重绘等价物**。

## 取图记录（2026-11-09）

- `https://content.warframe.com/PublicExport/index_en.txt.lzma` → 200
- `.../Manifest/ExportManifest.json!00_y10Y6QId+-io-dCB3GMEmg` → 200（3.8MB / 20154 条）
- `https://wiki.warframe.com/images/*` → 403（Cloudflare 挑战页）
- `https://warframe.fandom.com/*`、`static.wikia.nocookie.net` → 超时 / 403
- `https://warframe.huijiwiki.com/api.php?...` → 200（浏览器/WebFetch/curl 可达；
  **Python httpx 被 567 拦截**，工具内显式改用 curl 直连，不静默吞错）
- 三枚 CDN PNG → 200 `image/png`，64×64 RGBA

## 维护

```bash
# 联网补齐三枚（httpx 被灰机 CDN 567 拦截时会显式改道 curl）
python tools/fetch_wf_currency_icons.py

# 只校验本地资产：缺图 / 尺寸不对即 exit 1，可直接当交付闸门
python tools/fetch_wf_currency_icons.py --check
```

替换某一枚时直接覆盖同名 PNG 即可，`pic/currency_icons.py` 每次渲染重新读取，
无需改代码、无需重启。资产缺失时 `currency_icons.draw()` 会显式 `log.warning`
并回落几何图标，`currency_icons.gate(strict=True)` / `--check` 会把缺图拦在交付前
（对齐 AGENTS.md §18「几何方块只允许作异常兜底，缺图一律不算交付」）。
