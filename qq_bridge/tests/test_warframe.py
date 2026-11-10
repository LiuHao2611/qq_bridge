# -*- coding: utf-8 -*-
"""
test_warframe.py —— 星际战甲（国际服）攻略模块验收（2026-09-16）

覆盖：
  1. 中文别名 → url_name 映射（搬运 TWGBot /wm 用例：充沛→energize、沙皇→kuva_zarr）；
  2. 时间/百分比格式化；
  3. wiki 搜索 / 翻译 / 帮助文本；
  4. game_tools.dispatch 对 wf 系列命令的分发（无网络分支）；
  5. 真实网络冒烟：worldstate（Tenno Tools）与价格（Warframe.Market），
     失败时函数须返回非空字符串（不抛异常、不拖垮消息循环）。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_warframe.py -v
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.qq_bridge.game_tools import warframe  # noqa: E402
from src.plugins.qq_bridge.game_tools import dispatch, _strip_markers  # noqa: E402


# ---------------- 别名映射 ----------------
def test_resolve_alias_chinese():
    assert warframe._resolve_url_name("充沛") == "arcane_energize"
    assert warframe._resolve_url_name("沙皇") == "kuva_zarr"
    assert warframe._resolve_url_name("绝路") == "rubico_prime"
    assert warframe._resolve_url_name("龙骑兵") == "drakgoon"


def test_resolve_alias_en_fallback():
    assert warframe._resolve_url_name("energize") == "arcane_energize"
    assert warframe._resolve_url_name("Kuva Zarr") == "kuva_zarr"  # 空格→下划线
    assert warframe._resolve_url_name("Rubico Prime") == "rubico_prime_set"  # prime 后缀走套装升级


# ---------------- 格式化 ----------------
def test_fmt_dur():
    assert "天" in warframe._fmt_dur(90000)
    assert warframe._fmt_dur(3600) == "1小时0分"
    assert warframe._fmt_dur(90) == "1分钟"
    assert warframe._fmt_dur(-5) == "0分钟"


def test_fmt_pct():
    assert warframe._fmt_pct(0.28) == "28.0%"
    assert warframe._fmt_pct(0.0019) == "0.2%"


# ---------------- 文本功能 ----------------
def test_wiki_text():
    t = warframe.wiki_text("绝路")
    assert "warframe.huijiwiki.com" in t
    assert "绝路" in t
    assert "格式" in warframe.wiki_text("")


def test_translate_text():
    assert "Energize" in warframe.translate_text("充沛")
    assert "灰机" in warframe.translate_text("没见过这个词xyz")


def test_help_text():
    h = warframe.help_text()
    for kw in ("突击", "奸商", "价格", "紫卡", "wiki", "翻译"):
        assert kw in h


# ---------------- dispatch 分发（无网络分支） ----------------
def test_dispatch_wf_help():
    assert "星际战甲" in asyncio.run(dispatch("wf", "", "test"))


def test_dispatch_wf_sub_help():
    assert "星际战甲" in asyncio.run(dispatch("wf", "帮助", "test"))


def test_dispatch_wf_unknown_sub():
    r = asyncio.run(dispatch("wf", "不存在的子命令", "test"))
    assert "未知的星际战甲命令" in r


def test_dispatch_wiki_noarg():
    r = asyncio.run(dispatch("wiki", "", "test"))
    assert "格式" in r


def test_dispatch_price_noarg():
    r = asyncio.run(dispatch("价格", "", "test"))
    assert "格式" in r


def test_dispatch_translate():
    r = asyncio.run(dispatch("翻译", "充沛", "test"))
    assert "Energize" in r


def test_dispatch_wf_price_sub():
    r = asyncio.run(dispatch("wf", "价格 充沛", "test"))
    assert "格式" not in r  # 有参时进入真实查询，不能回"格式"


def test_market_text_slow_wm_is_bounded(monkeypatch):
    """WM 慢/被风控时，market_text 必须在 MARKET_HARD_TIMEOUT 内优雅降级，

    不能空等十几秒把整条命令拖到 NapCat 动作超时（即用户反馈的「返回超时」）。
    """
    import time as _t

    async def _slow(url, params="", timeout=warframe._MARKET_TIMEOUT, retry=warframe._MARKET_RETRY):
        await asyncio.sleep(30)  # 故意远超硬超时，模拟挂起的请求
        return None

    monkeypatch.setattr(warframe, "_market_get", _slow)
    t0 = _t.time()
    r = asyncio.run(warframe.market_text("伯斯顿"))
    el = _t.time() - t0
    assert el < warframe.MARKET_HARD_TIMEOUT + 3, f"市价查询未受硬超时约束，耗时 {el:.1f}s"
    assert r.startswith("Warframe.Market 暂时没响应"), "超时未走优雅降级提示"


def test_price_text_slow_wm_is_bounded(monkeypatch):
    """price_text 同样必须被硬超时约束，不能挂起整条命令。"""
    import time as _t

    async def _slow(url, params="", timeout=warframe._MARKET_TIMEOUT, retry=warframe._MARKET_RETRY):
        await asyncio.sleep(30)
        return None

    monkeypatch.setattr(warframe, "_market_get", _slow)
    t0 = _t.time()
    r = asyncio.run(warframe.price_text("充沛"))
    el = _t.time() - t0
    assert el < warframe.MARKET_HARD_TIMEOUT + 3, f"价格查询未受硬超时约束，耗时 {el:.1f}s"
    assert "Warframe.Market 暂时没响应" in r, "超时未走优雅降级提示"


# ---------------- 翻译（官方中英名 + 多外号互译，离线数据） ----------------
def test_translate_alias_to_full():
    r = warframe.translate_text("咖喱")
    assert "战甲" in r and "Excalibur" in r and "咖喱棒" in r and "圣剑" in r


def test_translate_en_to_zh():
    r = warframe.translate_text("rubico prime")
    assert "主武器" in r and "绝路" in r and "Rubico Prime" in r


def test_translate_official_zh_to_en():
    r = warframe.translate_text("绝路 Prime")
    assert "Rubico Prime" in r and "主武器" in r


def test_translate_warframe_alias():
    r = warframe.translate_text("毒妈")
    assert "Saryn" in r and "战甲" in r


def test_translate_unknown_fallback_wiki():
    r = warframe.translate_text("不存在的词xyz")
    assert "灰机" in r


# ---------------- 紫卡评估（面板 + 倾向 + 洗卡建议，离线数据） ----------------
def test_riven_analysis_rubico():
    r = asyncio.run(warframe.riven_analysis_text("绝路"))
    assert "Rubico Prime" in r and "面板" in r and "倾向" in r and "洗卡建议" in r


def test_riven_analysis_warframe_hint():
    r = asyncio.run(warframe.riven_analysis_text("牛"))
    assert "战甲没有紫卡" in r


def test_riven_analysis_noarg():
    r = asyncio.run(warframe.riven_analysis_text(""))
    assert "格式" in r


def test_dispatch_riven_analysis():
    r = asyncio.run(dispatch("紫卡分析", "绝路", "test"))
    assert "Rubico Prime" in r


def test_dispatch_wf_analysis_sub():
    r = asyncio.run(dispatch("wf", "紫卡分析 绝路", "test"))
    assert "Rubico Prime" in r


# ---------------- 紫卡倾向（DE 44.1.3 后全武器统一 1.2） ----------------
#: 旧口径黑名单：任何对外输出命中即回归失败（2026-10-22 起这些说法全是错的）
_FORBIDDEN_DISPO = (
    "倾向极低：热门武器", "倾向极高：冷门武器", "毕业裂罅卡最值钱",
    "最新倾向", "●●●●○", "●○○○○", "(0.85)", "(0.70)",
)


def test_riven_disposition_card_layout():
    """倾向卡改为「现行统一 1.20 + 改动前对照」，不再有星级/分档点评。"""
    lines = asyncio.run(warframe.riven_disposition_text("绝路")).split("\n")
    assert lines[0] == "@ttl|紫卡倾向（已统一）|book"
    assert "44.1.3" in lines[1] and "移除武器倾向差异" in lines[1]
    m = re.match(r"^@disp\|(.+?)\|(近战|主武器|副武器)\|(\d\.\d\d)\|([^|]*)(?:\|.*)?$",
                 lines[2])
    assert m, lines[2]
    info = warframe._resolve_weapon("绝路")
    assert m.group(1) == warframe._weapon_zh(info["en"])
    assert m.group(3) == "1.20"                 # 现行倍率不随武器变化
    assert "改动前" in m.group(4)               # 历史对照放在面板里
    assert not re.search(r"\|[1-5]\|", lines[2])   # 不再有星级字段
    assert any(ln.startswith("@foot|· 现行规则下") for ln in lines)
    assert any(ln.startswith("@foot|· 洗卡建议：") for ln in lines)
    assert lines[-1].startswith("@page|")


def test_riven_disposition_reference_weapon():
    r = asyncio.run(warframe.riven_disposition_text("天薙刀"))
    assert "@disp|天薙刀|近战|1.20|" in r


def test_riven_disposition_en_name():
    r = asyncio.run(warframe.riven_disposition_text("rubico prime"))
    assert "@disp|绝路 Prime|主武器|1.20|" in r


def test_riven_unified_disposition_and_before_comparison():
    """用户感受：查任何武器，现行倍率都是 1.20，且能看出改动前的涨跌。"""
    for name in ("绝路", "天薙刀", "太始弓"):
        info = warframe._resolve_weapon(name)
        assert info and abs(float(info["omega"]) - warframe.RIVEN_DISPOSITION) < 1e-6
        assert warframe._disp_stars(info) == 4
    assert "改动前 0.70 → 词条约 +71%" == warframe.disp_change_text(0.70)
    assert "-23%" in warframe.disp_change_text(1.55)
    assert warframe.disp_change_text(1.20) == ""      # 本来就是 1.2 不该显示对照
    assert warframe.disp_change_text(None) == ""


def test_riven_disposition_noarg():
    r = asyncio.run(warframe.riven_disposition_text(""))
    assert "格式" in r and "1.20" in r


def test_riven_disposition_warframe_hint():
    r = asyncio.run(warframe.riven_disposition_text("牛"))
    assert "战甲没有紫卡" in r


def test_no_legacy_disposition_wording():
    """守卫：星级点阵 / 热门冷门分档建议 / 「最新倾向」角标不得复活。"""
    card = asyncio.run(warframe.riven_disposition_text("绝路"))
    outputs = {
        "倾向卡": card,
        "倾向卡文本降级": _strip_markers(card),
        "紫卡分析": asyncio.run(warframe.riven_analysis_text("绝路")),
        "模拟开卡": asyncio.run(warframe.sim_riven_text()),
        "帮助文本": warframe.help_text(),
    }
    for label, text in outputs.items():
        for bad in _FORBIDDEN_DISPO:
            assert bad not in text, "%s 命中过期倾向文案：%s" % (label, bad)
    assert "1.20" in _strip_markers(card) and "全武器统一" in _strip_markers(card)
    assert "●" not in _strip_markers(card)


def test_strip_markers_plain_text():
    """纯文本降级不能吐 @ 标记，也不能再画星级点阵。"""
    plain = _strip_markers(
        "@ttl|紫卡倾向（已统一）|book\n"
        "@sub|2026-10-22 起 DE 热修 44.1.3 移除武器倾向差异|24\n"
        "@disp|绝路 Prime|主武器|1.20|改动前 0.70 → 词条约 +71%\n"
        "@foot|· 点评")
    assert "@" not in plain
    assert "[绝路 Prime] 主武器" in plain
    assert "现行倍率 1.20（全武器统一）" in plain
    assert "改动前 0.70" in plain
    assert "●" not in plain


def test_disposition_card_renders():
    """端到端出图：新版 @disp 面板必须能渲染成 PNG。"""
    from src.plugins.qq_bridge.pic import render_sync

    body = asyncio.run(warframe.riven_disposition_text("绝路"))
    png = render_sync("guide", theme_name="light", card="星际战甲·紫卡倾向",
                      body=body, title="", avatar=None, source="DE 官方热修 44.1.3")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 3000


def test_weapon_icon_resolver_fallback():
    """官方图标解析：无图/缺失文件名/下载失败都应静默回落（返回 None，不抛异常）。"""
    from src.plugins.qq_bridge.pic.weapon_icons import icon_cache_path, ensure_icon

    assert icon_cache_path(None) is None
    assert icon_cache_path("") is None
    assert icon_cache_path("no-such-icon-abcdef.png") is None
    assert asyncio.run(ensure_icon("no-such-icon-abcdef.png")) is None


def test_disp_panel_pastes_real_weapon_icon(tmp_path):
    """@disp 携带官方图标时，guide 必须把它贴进面板而非几何图标。"""
    from PIL import Image
    from src.plugins.qq_bridge.pic import render_sync

    _ico = tmp_path / "fake-weapon-icon.png"
    Image.new("RGBA", (64, 64), (180, 30, 200, 255)).save(_ico, format="PNG")
    body = (
        "@ttl|紫卡倾向（已统一）|book\n"
        "@sub|说明|24\n"
        "@disp|测试武器|近战|1.20|改动前 0.70 → 词条约 +71%|" + str(_ico.resolve()) + "\n"
        "@foot|· 点评\n"
        "@page|入口"
    )
    png = render_sync("guide", theme_name="light", card="星际战甲·紫卡倾向",
                      body=body, title="", avatar=None, source="src")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 3000


def test_disp_panel_glyph_fallback_without_icon():
    """@disp 无对照、无图标时仍出图（不崩）。"""
    from src.plugins.qq_bridge.pic import render_sync

    body = ("@ttl|紫卡倾向（已统一）|book\n"
            "@sub|说明|24\n"
            "@disp|测试武器|主武器|1.20|\n"
            "@foot|· 点评\n"
            "@page|入口")
    png = render_sync("guide", theme_name="light", card="星际战甲·紫卡倾向",
                      body=body, title="", avatar=None, source="src")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 3000


def test_dispatch_riven_disposition():
    r = asyncio.run(dispatch("倾向", "绝路", "test"))
    assert "@disp|绝路 Prime|主武器|1.20|" in r


def test_dispatch_wf_disposition_sub():
    r = asyncio.run(dispatch("wf", "倾向 绝路", "test"))
    assert "@disp|绝路 Prime|主武器|1.20|" in r


def test_disp_card_design_gate():
    """prove-it：card-design 门禁必须真的跑通，且能抓到旧口径。"""
    import subprocess

    script = ROOT / "tools" / "check_disp_layout.py"
    assert script.is_file()
    for flag, expect in (("--selftest", "selftest: PASS"), ("--check", "自检 PASS")):
        r = subprocess.run([sys.executable, str(script), flag],
                           cwd=str(ROOT), capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        assert expect in r.stdout


# ---------------- 简体中文本地化（静态表，零网络请求） ----------------
def test_zh_loc_star():
    assert warframe._zh_loc("Venus/V Prime") == "金星/V Prime"
    assert warframe._zh_loc("Kuva Fortress/Rotuma") == "赤毒要塞/Rotuma"
    assert warframe._zh_loc("Zariman/Everview Arc") == "扎里曼号/永视弧域"


def test_zh_mission_tier():
    assert warframe._zh_mission("Void Cascade") == "虚空覆涌"
    assert warframe._zh_mission("Mobile Defense") == "移动防御"
    assert warframe._zh_tier("Axi") == "Axi 后纪"


def test_zh_faction_boss():
    assert warframe._zh_faction("Infestation") == "感染者"
    assert warframe._zh_boss("Mutalist Alad V") == "突变体阿拉德五世"


def test_zh_fissures_text(monkeypatch):
    async def fake(fast=False):
        return {"fissures": {"data": [
            {"tier": "Lith", "location": "Venus/V Prime",
             "missionType": "Survival", "hard": True},
        ]}}
    monkeypatch.setattr(warframe, "_worldstate", fake)
    r = asyncio.run(warframe.fissures_text())
    assert "金星/V Prime" in r and "生存" in r and "Lith 古纪" in r


def test_zh_sortie_text(monkeypatch):
    async def fake():
        return {"sorties": {"data": [{
            "faction": "Infestation", "bossName": "Mutalist Alad V", "end": 0,
            "rewards": [[{"name": "Endo", "chance": 0.2}]],
        }]}}
    monkeypatch.setattr(warframe, "_worldstate", fake)
    r = asyncio.run(warframe.sortie_text())
    assert "感染者" in r and "突变体阿拉德五世" in r and "内融核心" in r


# ---------------- 新口令：倒计时 / 平原全量 / 赏金 / 九重天 / 周本 / 轮换 ----------------
def test_fissures_countdown(monkeypatch):
    now = warframe._now()
    async def fake(fast=False):
        return {"fissures": {"data": [
            {"tier": "Axi", "location": "Void/Ani", "missionType": "Survival",
             "hard": False, "end": now + 300},
        ]}}
    monkeypatch.setattr(warframe, "_worldstate", fake)
    r = asyncio.run(warframe.fissures_text())
    assert "5分" in r and "虚空/Ani" in r


def test_daynight_all(monkeypatch):
    now = warframe._now()
    async def fake(fast=False):
        return {"daynight": {"data": [
            {"id": "cetus", "start": now - 1000, "length": 150 * 60,
             "dayStart": 0, "dayEnd": 60 * 60},
            {"id": "fortuna", "start": now - 1000, "length": 150 * 60,
             "dayStart": 0, "dayEnd": 60 * 60},
            {"id": "earth", "start": now - 1000, "length": 150 * 60,
             "dayStart": 0, "dayEnd": 60 * 60},
        ]}}
    monkeypatch.setattr(warframe, "_worldstate", fake)
    r = asyncio.run(warframe.daynight_text(""))
    assert "夜灵平原（地球）" in r and "奥布山谷（金星）" in r and "地球野外" in r
    assert "后切换" in r


def test_bounties_text(monkeypatch):
    now = warframe._now()
    async def fake_official():
        return {"SyndicateMissions": [
            {"Tag": "CetusSyndicate",
             "Jobs": [
                 {"jobType": "/CetusSyndicate/AttritionBountyExt", "minEnemyLevel": 50, "maxEnemyLevel": 70},
                 {"jobType": "/CetusSyndicate/ReclamationBountyCap", "minEnemyLevel": 100, "maxEnemyLevel": 100},
             ],
             "Expiry": {"$date": {"$numberLong": str(int((now + 600) * 1000))}}},
            {"Tag": "SolarisSyndicate",
             "Jobs": [
                 {"jobType": "/SolarisSyndicate/VenusWetworkJobAssassinate", "minEnemyLevel": 50, "maxEnemyLevel": 70},
                 {"jobType": "/Narmer/SolarisSyndicate/NarmerVenusCullJobAssassinate", "minEnemyLevel": 100, "maxEnemyLevel": 100},
             ]},
            {"Tag": "EntratiSyndicate",
             "Jobs": [
                 {"jobType": "/EntratiSyndicate/DeimosPurifyBounty", "minEnemyLevel": 40, "maxEnemyLevel": 60},
                 {"jobType": "/EntratiSyndicate/DeimosAssassinateBounty", "minEnemyLevel": 100, "maxEnemyLevel": 100},
             ]},
        ]}
    async def fake_wfwiki():
        return None
    monkeypatch.setattr(warframe, "_official_worldstate", fake_official)
    monkeypatch.setattr(warframe, "_wfwiki_html", fake_wfwiki)
    r = asyncio.run(warframe.bounties_text())
    assert "赏金任务一览" in r and "【赏金轮换】" in r and "剩余时间" in r
    assert "【地球赏金】" in r and "捕获他们的领袖" in r and "为了乌努母神" in r
    assert "【金星赏金】" in r and "网络刺杀" in r and "邪主之音 合一众" in r
    assert "【火卫二赏金】" in r and "净化大地" in r and "清净之地" in r
    assert "【深矿赏金】" in r and "花园除草" in r and "130-140" in r
    assert "【扎里曼赏金】" in r and "哈拉科防线" in r and "窃取新力量" in r
    assert "【圣所赏金】" in r and "孽杀" in r and "恶涌" in r
    assert "【六人组赏金】" in r and "地狱净化：科腐者" in r and "能量超载" in r


def test_voidstorms_text(monkeypatch):
    now = warframe._now()
    async def fake(fast=False):
        return {"voidstorms": {"data": [
            {"location": "Earth Proxima", "missionType": "Skirmish", "end": now + 900},
        ]}}
    monkeypatch.setattr(warframe, "_worldstate", fake)
    r = asyncio.run(warframe.voidstorms_text())
    assert "地球比邻星域" in r and "前哨战" in r and "剩15分钟" in r


def test_weekly_text():
    r = warframe.weekly_text()
    assert "执政官狩猎" in r and "深层档案馆" in r


def test_wiki_block_parse():
    html = ('<h2>信条武器轮换</h2><span>刷新 <!-- -->3天 14时</span>'
            '<span class="tabular-nums">5</span>'
            '<li><span>信条·莉薇娅</span></li><li><span>信条·枢密</span></li>')
    r = warframe._wiki_block(html, "信条武器轮换")
    assert r and "3天 14时" in r and "批次 5" in r and "信条·莉薇娅" in r


def test_rotation_fallback(monkeypatch):
    async def none():
        return None
    monkeypatch.setattr(warframe, "_wfwiki", none)
    r = asyncio.run(warframe.rotation_text())
    assert "赤毒武器轮换" in r and "灵化武器" in r


# ---------------- 战甲信息 / MOD / 赋能（离线数据） ----------------
def test_warframe_info_text():
    r = asyncio.run(warframe.warframe_info_text("圣剑"))
    assert "Excalibur" in r and "生命 270" in r and "技能" in r


def test_warframe_info_volt_prime():
    r = asyncio.run(warframe.warframe_info_text("volt prime"))
    assert "Volt Prime" in r and "电能释放" in r


def test_mod_text_zh():
    r = asyncio.run(warframe.mod_text("聚精会神"))
    assert "intensify" in r and "技能强度" in r


def test_mod_text_umbral():
    r = asyncio.run(warframe.mod_text("暗影聚精会神"))
    assert "umbral intensify" in r and "暗影 聚精会神" in r


def test_arcane_text_zh():
    r = asyncio.run(warframe.arcane_text("充沛"))
    assert "arcane energize" in r and "能量" in r


def test_arcane_text_en():
    r = asyncio.run(warframe.arcane_text("arcane avenger"))
    assert "复仇者" in r and "暴击" in r


# ---------------- 别名/黑话兜底（战甲外号 → 资料查询） ----------------
def test_alias_dispatch_zhuma():
    from plugins.qq_bridge.game_tools import _wf_dispatch
    r = asyncio.run(_wf_dispatch("毒妈"))
    assert "Saryn" in r and "毒性孢子" in r


def test_alias_dispatch_gali():
    from plugins.qq_bridge.game_tools import _wf_dispatch
    r = asyncio.run(_wf_dispatch("咖喱棒"))
    assert "Excalibur" in r


def test_alias_exact_prefers_arcane():
    from plugins.qq_bridge.game_tools import _wf_dispatch
    r = asyncio.run(_wf_dispatch("充沛"))
    assert "赋能·充沛" in r and "arcane energize" in r


def test_match_natural_alias():
    from plugins.qq_bridge.game_tools import _match_natural
    assert _match_natural("电男") == ("电男", "")
    assert _match_natural("牛批") is None  # 防误伤：聊天词不触发
    assert _match_natural("奶") is None    # 单字别名不触发


# ---------------- 紫卡截图分析（OCR 解析 + 词条评估） ----------------
def test_riven_parse_en():
    from plugins.qq_bridge.game_tools import riven
    parsed = riven._parse_ocr(
        "Rubico Prime Sati-visitak\n+190.5% Critical Chance\n+120.3% Damage\n-28.7% Zoom")
    assert parsed and parsed["weapon"] == "rubico prime"
    assert parsed["type"] == "Rifle"
    assert len(parsed["stats"]) == 3
    assert parsed["stats"][0]["zh"] == "暴击几率"


def test_riven_parse_zh():
    from plugins.qq_bridge.game_tools import riven
    parsed = riven._parse_ocr(
        "迦伦提恩 Prime 暴怒-暴击\n+210% 近战伤害\n+150% 暴击伤害\n-20% 攻击速度")
    assert parsed and parsed["weapon"] == "galatine prime"
    assert parsed["weapon_zh"] == "迦伦提恩 Prime"
    assert parsed["type"] == "Melee"


def test_riven_parse_faction_x():
    """对 Corpus 的伤害（含「的」+ 空格）→ Damage to Corpus，x 乘数 1.59。"""
    from plugins.qq_bridge.game_tools import riven
    parsed = riven._parse_ocr(
        "执法者 Manti-critaata\n+202.8% 近战伤害\nx1.59 对 Corpus 的伤害\n+222.6% 暴击几率\n-7.9 连击持续时间")
    assert parsed and parsed["weapon"] == "magistar"
    assert parsed["disp"] == 1.35
    en = [s["en"] for s in parsed["stats"]]
    assert "Damage to Corpus" in en or "Damage vs Corpus" in en
    x = [s for s in parsed["stats"] if s["unit"] == "x"][0]
    assert abs(x["value"] - 1.59) < 0.01


def test_riven_eval_rabbit_style():
    """Rabbit 风格：词条行含理论范围、综合评分带字母档、洗卡/负面推荐。"""
    from plugins.qq_bridge.game_tools import riven
    parsed = riven._parse_ocr(
        "Rubico Prime Sati-visitak\n+190.5% Critical Chance\n+120.3% Damage\n+88.8% Multishot\n-28.7% Zoom")
    text = riven._eval(parsed)
    assert "紫卡综合评分" in text and "(" in text
    assert "推荐洗卡" in text and "致命负词条" in text and "推荐负面词条" in text
    assert "倾向" in text


def test_riven_extract_value_guards():
    """% 优先于括号 x2；x 乘数带小数点；连击持续时间为秒单位语义。"""
    from plugins.qq_bridge.game_tools import riven
    assert riven._extract_value("+222.6% 暴击几率（重击时x2）")["value"] == 222.6
    assert riven._extract_value("x1.59 对 Corpus 的伤害")["unit"] == "x"
    assert abs(riven._extract_value("x1.59 对 Corpus 的伤害")["value"] - 1.59) < 0.01
    assert abs(riven._extract_value("xl，59 对Corpus伤害")["value"] - 1.59) < 0.01  # OCR l↔1
    assert riven._extract_value("-7.9秒 连击持续时间")["sign"] == "-"


def test_riven_meta_footer():
    from plugins.qq_bridge.game_tools import _guide_meta
    s, pre, ts = _guide_meta("星际战甲·紫卡评估")
    assert s == "本地 OCR + warframe-items 数据"
    assert pre == "数据构建"


def test_plain_text_ignores_at_segment():
    """NapCat 场景：get_plaintext 可能返回「@9984电男」，必须按 text 段提取纯文本。"""
    from plugins.qq_bridge.game_tools import _plain_text

    class Seg:
        def __init__(self, t, d):
            self.type = t
            self.data = d

    class FakeEvent:
        message = [Seg("at", {"qq": "900000009"}), Seg("text", {"text": "电男"})]
        original_message = [Seg("at", {"qq": "900000009"}), Seg("text", {"text": "电男"})]

        def get_plaintext(self):
            # 模拟 NapCat：at 段未改写，get_plaintext 会带 @
            return "@900000009电男"

    ev = FakeEvent()
    assert _plain_text(ev) == "电男"
    # 退化：无 original_message → 回退 get_plaintext（此时若带 @ 属适配器行为，交由上层兜底）
    ev2 = FakeEvent()
    ev2.original_message = None
    assert _plain_text(ev2) == "电男" or "@" in _plaintext_fallback(ev2)


def _plaintext_fallback(ev):
    """辅助：与 _plain_text 的回退路径一致（仅测试断言用）。"""
    from plugins.qq_bridge.game_tools import _event_segments
    try:
        parts = []
        for seg in _event_segments(ev):
            if seg.type == "text":
                parts.append(str(seg.data.get("text") or ""))
        return "".join(parts).strip()
    except Exception:
        return ""


def test_at_bot_uses_original_message():
    """nonebot 会把 event.message 的 at 段改写为空文本，必须用 original_message。"""
    from plugins.qq_bridge.game_tools import _at_bot, _extract_images

    class Seg:
        def __init__(self, t, d):
            self.type = t
            self.data = d

    class FakeEvent:
        # 模拟 nonebot handler 执行后的 message：at 段已被改写成空文本
        message = [Seg("text", {"text": ""}), Seg("image", {"url": "http://x/img.png"})]
        original_message = [Seg("at", {"qq": "900000009"}),
                            Seg("image", {"url": "http://x/img.png"})]

    class FakeBot:
        self_id = "900000009"

    ev = FakeEvent()
    assert _at_bot(ev, FakeBot()) is True
    assert _extract_images(ev) == ["http://x/img.png"]
    # 退化场景：无 original_message 时回退 message
    ev2 = FakeEvent()
    ev2.original_message = None  # 实例覆盖类属性，模拟无 original_message 的适配器
    assert _extract_images(ev2) == ["http://x/img.png"]


def test_guide_meta_uses_source_utc_time():
    from plugins.qq_bridge.game_tools import _guide_meta
    # 先拉一次 worldstate 填充 section 时间（真实网络）
    asyncio.run(warframe.fissures_text())
    s, pre, ts = _guide_meta("星际战甲·裂隙")
    assert s == "Tenno.Tools 世界状态"
    assert pre == "更新于"
    assert "UTC" in ts  # 数据源 UTC 时间（可能附相对新鲜度后缀），绝不使用本机时间


def test_guide_meta_local_build_time():
    from plugins.qq_bridge.game_tools import _guide_meta
    s, pre, ts = _guide_meta("星际战甲·战甲信息")
    assert s == "warframe-items 官方数据"
    assert pre == "数据构建"
    assert len(ts) >= 10  # MM-DD HH:MM


# ---------------- 市价卡 DSL 复刻（对齐 Rabbit 参考卡，2026-09-29 重写） ----------------
# 统计口径（经真实 WM API + 用户 Rabbit 参考卡逐值比对确认）：
#   · 售价 = statistics_closed[窗口]；收价 = statistics_live[窗口] 中 order_type=buy
#   · 最低/最高 = **整个窗口**的 min(min_price) / max(max_price)（不是最近一档！）
#     参考卡 90天售价 50-110、48时售价 62-77 只可能是全窗口极值
#   · 最新 = 最近一档的 median（closed/live 均有；旧实现用 open_price → live 恒「暂无」）
#   · 非 MOD 前缀「零级」；MOD 按 rank 过滤，前缀 满级 / R{n} / 零级
#   · 出售列表：在线/游戏内整组在前（组内价格升序），离线随后；页码按全部挂单计
def _mk_stats(closed48, closed90, buy48, buy90):
    """构造真实 WM statistics 结构（statistics_closed/Live 各含 48hours/90days 两窗口）。"""
    def C(x):  # closed 档须补 datetime；MOD 档保留 mod_rank
        return [{"datetime": "2026-09-29T06:00:00.000+00:00", **x}]
    return {"payload": {
        "statistics_closed": {"48hours": C(closed48), "90days": C(closed90)},
        "statistics_live": {"48hours": buy48, "90days": buy90},
    }}


def test_market_text_rabbit_style(monkeypatch):
    """market_text 输出须与 Rabbit 参考卡字段级一致（cart 标题/中文名/在线优先/四行统计/喊话/页脚）。"""
    async def fake_market_get(url: str, params: str = "") -> dict | None:
        if "/v2/orders/item/" in url:
            return {"data": [
                {"type": "sell", "platinum": 79, "quantity": 1,
                 "updatedAt": "2026-09-22T10:00:00.000+00:00",
                 "user": {"status": "ingame", "ingameName": "AYMY-DarkBlew",
                          "id": "u_aymy", "avatar": "user/avatar/aymy.png?h1"}},
                {"type": "sell", "platinum": 80, "quantity": 2,
                 "updatedAt": "2026-09-22T09:00:00.000+00:00",
                 "user": {"status": "online", "ingameName": "TennoB",
                          "id": "u_tenno", "avatar": "user/avatar/tenno.png?h2"}},
            ]}
        if "/v2/item/" in url and "/set" in url:
            # set 响应同时供英文名 + 头条块（图标/杜卡德/交易税/稀有度）
            return {"data": {"items": [{
                "slug": "saryn_prime_set",
                "icon": "icons/saryn_prime_set.png", "tradingTax": 1000000,
                "rarity": "Legendary",
                "i18n": {"en": {"name": "Saryn Prime Set"}}}]}}
        if "/v1/items/" in url and "/statistics" in url:
            return _mk_stats(
                {"min_price": 74, "max_price": 77, "median": 75},
                {"min_price": 50, "max_price": 110, "median": 75},
                [{"order_type": "buy", "min_price": 65, "max_price": 75, "median": 70}],
                [{"order_type": "buy", "min_price": 60, "max_price": 72, "median": 64}],
            )
        return None

    monkeypatch.setattr(warframe, "_market_get", fake_market_get)
    # 清掉缓存，避免命中旧缓存
    warframe._MARKET_CACHE.update({"key": "", "ts": 0.0, "data": None})
    warframe._MARKET_NAME_CACHE.update({"key": "", "ts": 0.0, "name": ""})

    r = asyncio.run(warframe.market_text("Saryn Prime 一套", page=1))
    # 头条块：中文名|英文名|分类|杜卡德|交易税|在售|在线|图标路径
    assert ("@wmhdr|Saryn Prime 一套|Saryn Prime|传奇 · 战甲|"
            "不可兑换|1,000,000|2|2|icons/saryn_prime_set.png") in r
    assert "@hdr|卖家|状态|声望|数量|白金价格" in r
    assert "@row|AYMY-DarkBlew|游戏中|0|1|79|u_aymy|user/avatar/aymy.png?h1" in r
    assert "@row|TennoB|在线|0|2|80|u_tenno|user/avatar/tenno.png?h2" in r
    # 价格须按升序（最便宜优先，复刻截图 79→80）
    _rows = [ln for ln in r.split("\n") if ln.startswith("@row|")]
    _prices = [int(ln.split("|")[5]) for ln in _rows]
    assert _prices == sorted(_prices), "市价卡价格未按升序排列"
    # 四行统计：售价=成交(closed)、收价=挂收价(live buy)，非 MOD → 「零级」前缀
    assert "@mstat|48时内零级售价|74|77|75" in r
    assert "@mstat|90天内零级售价|50|110|75" in r
    assert "@mstat|48时内零级收价|65|75|70" in r
    assert "@mstat|90天内零级收价|60|72|64" in r
    # 喊话挑「在线（含游戏内）」最低价，带 RabbitBot 后缀并英文 set 名
    assert "@shout|/w AYMY-DarkBlew Hi! I want to buy: Saryn Prime Set for 79 platinum. (wf.m via qq_bridge)" in r
    # 页脚
    assert "@foot|WARFRAME.MARKET / PC  ·  右键/长按提取文字" in r


def test_market_text_mod_max_rank(monkeypatch):
    """MOD 查询带「满级」时，只过滤出 rank=max 的挂单，统计前缀显示「满级」。"""
    async def fake_market_get(url: str, params: str = "") -> dict | None:
        if "/v2/orders/item/" in url:
            return {"data": [
                # rank 0 便宜，应被过滤掉
                {"type": "sell", "platinum": 2, "quantity": 1, "rank": 0,
                 "user": {"status": "online", "ingameName": "Cheap0"}},
                # rank 10 贵，应保留
                {"type": "sell", "platinum": 120, "quantity": 1, "rank": 10,
                 "user": {"status": "ingame", "ingameName": "MaxA"}},
                {"type": "sell", "platinum": 130, "quantity": 1, "rank": 10,
                 "user": {"status": "online", "ingameName": "MaxB"}},
            ]}
        if "/v2/item/" in url and "/set" in url:
            return {"data": {"items": [{"i18n": {"en": {"name": "Primed Continuity"}}}]}}
        if "/v1/items/" in url and "/statistics" in url:
            return _mk_stats(
                {"mod_rank": 10, "min_price": 110, "max_price": 140, "median": 125},
                {"mod_rank": 10, "min_price": 115, "max_price": 150, "median": 120},
                [{"order_type": "buy", "mod_rank": 10, "min_price": 90, "max_price": 110, "median": 100}],
                [{"order_type": "buy", "mod_rank": 10, "min_price": 80, "max_price": 100, "median": 92}],
            )
        return None

    monkeypatch.setattr(warframe, "_market_get", fake_market_get)
    warframe._MARKET_CACHE.update({"key": "", "ts": 0.0, "data": None})
    warframe._MARKET_NAME_CACHE.update({"key": "", "ts": 0.0, "name": ""})

    r = asyncio.run(warframe.market_text("持久力 满级", page=1))
    rows = [ln for ln in r.split("\n") if ln.startswith("@row|")]
    assert len(rows) == 2, rows
    prices = [int(ln.split("|")[5]) for ln in rows]
    assert prices == [120, 130], prices
    assert r.startswith("@wmhdr|持久力 满级|Primed Continuity|")
    assert "@mstat|48时内满级售价|110|140|125" in r
    assert "@mstat|90天内满级售价|115|150|120" in r
    assert "@mstat|48时内满级收价|90|110|100" in r
    # 喊话挑在线（含游戏内）最低价（MaxA 120，游戏在线）
    assert "@shout|/w MaxA Hi! I want to buy: Primed Continuity for 120 platinum. (wf.m via qq_bridge)" in r


def test_market_text_mod_r0(monkeypatch):
    """MOD 查询带「R0」时，只过滤出 rank=0 的挂单，统计前缀显示「零级」。"""
    async def fake_market_get(url: str, params: str = "") -> dict | None:
        if "/v2/orders/item/" in url:
            return {"data": [
                {"type": "sell", "platinum": 2, "quantity": 1, "rank": 0,
                 "user": {"status": "online", "ingameName": "ZeroA"}},
                {"type": "sell", "platinum": 3, "quantity": 1, "rank": 0,
                 "user": {"status": "offline", "ingameName": "ZeroB"}},
                {"type": "sell", "platinum": 120, "quantity": 1, "rank": 10,
                 "user": {"status": "online", "ingameName": "MaxA"}},
            ]}
        if "/v2/item/" in url and "/set" in url:
            return {"data": {"items": [{"i18n": {"en": {"name": "Primed Continuity"}}}]}}
        if "/v1/items/" in url and "/statistics" in url:
            return _mk_stats(
                {"mod_rank": 0, "min_price": 2, "max_price": 5, "median": 3},
                {"mod_rank": 0, "min_price": 1, "max_price": 6, "median": 3},
                [{"order_type": "buy", "mod_rank": 0, "min_price": 1, "max_price": 2, "median": 1}],
                [{"order_type": "buy", "mod_rank": 0, "min_price": 1, "max_price": 2, "median": 1}],
            )
        return None

    monkeypatch.setattr(warframe, "_market_get", fake_market_get)
    warframe._MARKET_CACHE.update({"key": "", "ts": 0.0, "data": None})
    warframe._MARKET_NAME_CACHE.update({"key": "", "ts": 0.0, "name": ""})

    r = asyncio.run(warframe.market_text("持久力 R0", page=1))
    rows = [ln for ln in r.split("\n") if ln.startswith("@row|")]
    assert len(rows) == 2, rows
    prices = [int(ln.split("|")[5]) for ln in rows]
    assert prices == [2, 3], prices
    assert r.startswith("@wmhdr|持久力 R0|Primed Continuity|")
    assert "@mstat|48时内零级售价|2|5|3" in r
    assert "@mstat|48时内零级收价|1|2|1" in r


def test_market_text_mod_not_mod_with_rank(monkeypatch):
    """对非 MOD 物品使用等级过滤，应返回明确提示而不是混入错误结果。"""
    async def fake_market_get(url: str, params: str = "") -> dict | None:
        if "/v2/orders/item/" in url:
            return {"data": [
                {"type": "sell", "platinum": 79, "quantity": 1,
                 "user": {"status": "online", "ingame_name": "Seller"}},
            ]}
        if "/v2/item/" in url and "/set" in url:
            return {"data": {"items": [{"i18n": {"en": {"name": "Saryn Prime Set"}}}]}}
        if "/v1/items/" in url and "/statistics" in url:
            return {"payload": {"statistics_live": [
                {"order_type": "sell", "min_price": 78, "max_price": 78, "avg_price": 78.0},
            ]}}
        return None

    monkeypatch.setattr(warframe, "_market_get", fake_market_get)
    warframe._MARKET_CACHE.update({"key": "", "ts": 0.0, "data": None})
    warframe._MARKET_NAME_CACHE.update({"key": "", "ts": 0.0, "name": ""})

    r = asyncio.run(warframe.market_text("Saryn Prime 一套 满级", page=1))
    assert "不是 MOD" in r
    assert "无法按等级过滤" in r


def test_market_text_offline_topup(monkeypatch):
    """出售列表：在线/游戏内整组在前（组内价格升序），离线随后；状态照实标注。

    对齐 Rabbit 参考卡：2026-09-29 实测 wukong_prime_set 第 1 页 10 行全为在线/游戏内卖家
    （74~78p）；而纯价格升序会把 67/69/70p 的离线单排到最前 —— 故为「在线整组优先」，
    离线仅在在线之后顺次列出（不再按价格插到前面）。
    """
    async def fake_market_get(url: str, params: str = "") -> dict | None:
        if "/v2/orders/item/" in url:
            return {"data": [
                # 2 个在线（价格偏高）
                {"type": "sell", "platinum": 79, "quantity": 1, "updatedAt": "2026-09-22T10:00:00+00:00",
                 "user": {"status": "ingame", "ingameName": "OnA", "id": "u_ona",
                          "avatar": "user/avatar/ona.png?h3"}},
                {"type": "sell", "platinum": 85, "quantity": 1, "updatedAt": "2026-09-22T10:01:00+00:00",
                 "user": {"status": "online", "ingameName": "OnB", "id": "u_onb",
                          "avatar": "user/avatar/onb.png?h4"}},
                # 3 个离线，价格更低
                {"type": "sell", "platinum": 70, "quantity": 3, "updatedAt": "2026-09-22T10:02:00+00:00",
                 "user": {"status": "offline", "ingameName": "OffA", "id": "u_offa",
                          "avatar": "user/avatar/offa.png?h5"}},
                {"type": "sell", "platinum": 72, "quantity": 2, "updatedAt": "2026-09-22T10:03:00+00:00",
                 "user": {"status": "offline", "ingameName": "OffB", "id": "u_offb",
                          "avatar": "user/avatar/offb.png?h6"}},
                {"type": "sell", "platinum": 90, "quantity": 1, "updatedAt": "2026-09-22T10:04:00+00:00",
                 "user": {"status": "offline", "ingameName": "OffC", "id": "u_offc",
                          "avatar": "user/avatar/offc.png?h7"}},
            ]}
        if "/v2/item/" in url and "/set" in url:
            return {"data": {"items": [{"i18n": {"en": {"name": "Saryn Prime Set"}}}]}}
        if "/v1/items/" in url and "/statistics" in url:
            return _mk_stats(
                {"min_price": 70, "max_price": 90, "median": 80},
                {"min_price": 60, "max_price": 95, "median": 80},
                [{"order_type": "buy", "min_price": 65, "max_price": 75, "median": 70}],
                [{"order_type": "buy", "min_price": 60, "max_price": 72, "median": 64}],
            )
        return None

    monkeypatch.setattr(warframe, "_market_get", fake_market_get)
    warframe._MARKET_CACHE.update({"key": "", "ts": 0.0, "data": None})
    warframe._MARKET_NAME_CACHE.update({"key": "", "ts": 0.0, "name": ""})

    r = asyncio.run(warframe.market_text("Saryn Prime 一套", page=1))
    rows = [ln for ln in r.split("\n") if ln.startswith("@row|")]
    # 第 1 页 5 行：在线组（79,85）在前，离线组（70,72,90）在后
    assert len(rows) == 5, rows
    prices = [int(ln.split("|")[5]) for ln in rows]
    assert prices == [79, 85, 70, 72, 90], prices
    # 状态照实标注（列序：卖家|状态|声望|数量|白金价格|uid|头像）
    assert "@row|OnA|游戏中|0|1|79|u_ona|user/avatar/ona.png?h3" in r
    assert "@row|OffA|离线|0|3|70|u_offa|user/avatar/offa.png?h5" in r
    assert "@row|OffC|离线|0|1|90|u_offc|user/avatar/offc.png?h7" in r
    # 喊话仍挑在线最低价
    assert "@shout|/w OnA Hi! I want to buy: Saryn Prime Set for 79 platinum. (wf.m via qq_bridge)" in r


def test_market_markers_render_through_guide(monkeypatch):
    """跨模块契约：market_text 产出的标记必须能被 guide.build 正确消费。

    守两件真出过问题的事：
      ① @hdr 的「白金价格」必须映射成 price 角色（否则价格变纯文本、没有气泡）；
      ② @row 新列序（卖家|状态|声望|数量|价格|uid|头像）与表头一一对齐。
    最低价 27 应被画成 t.ok 绿色气泡；列角色失配时绿像素≈0，此断言必挂。
    """
    async def fake_market_get(url: str, params: str = "") -> dict | None:
        if "/v2/orders/item/" in url:
            return {"data": [
                {"type": "sell", "platinum": 27, "quantity": 1, "rank": 0,
                 "user": {"status": "online", "ingameName": "Bett_tt",
                          "id": "u1", "reputation": 0}},
                {"type": "sell", "platinum": 40, "quantity": 1, "rank": 0,
                 "user": {"status": "ingame", "ingameName": "KiberPunkRU",
                          "id": "u2", "reputation": 9}},
            ]}
        if "/v2/item/" in url and "/set" in url:
            return {"data": {"items": [{
                "slug": "primed_point_blank",
                "icon": "icons/primed_point_blank.png", "tradingTax": 1000000,
                "rarity": "Legendary",
                "i18n": {"en": {"name": "Primed Point Blank"}}}]}}
        if "/v1/items/" in url and "/statistics" in url:
            return {"payload": {"statistics_closed": {"48hours": [], "90days": []},
                                "statistics_live": {"48hours": [], "90days": []}}}
        return None

    monkeypatch.setattr(warframe, "_market_get", fake_market_get)
    warframe._MARKET_CACHE.update({"key": "", "ts": 0.0, "data": None})
    warframe._MARKET_NAME_CACHE.update({"key": "", "ts": 0.0, "name": ""})
    warframe._MARKET_META_CACHE.clear()

    body = asyncio.run(warframe.market_text("primed_point_blank", page=1))
    assert "@wmhdr|" in body and "@hdr|卖家|状态|声望|数量|白金价格" in body

    from src.plugins.qq_bridge.pic.theme import get as _get_theme, LIGHT
    from src.plugins.qq_bridge.pic.templates import guide

    t = _get_theme(LIGHT)
    c = guide.build(t, body=body, avatars={"u1": _real_png(), "u2": _real_png()},
                    item_icon=_real_png())
    data = c.to_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"

    im = c.img.convert("RGB")
    colors = im.getcolors(maxcolors=im.width * im.height) or []
    _ok = tuple(int(t.ok[i:i + 2], 16) for i in (1, 3, 5))
    green = sum(n for n, (r, g, b) in colors
                if abs(r - _ok[0]) < 40 and abs(g - _ok[1]) < 40 and abs(b - _ok[2]) < 40)
    assert green > 300, f"最低价绿色气泡未绘出（green={green}），价格列角色可能失配"


def test_market_part_uses_subicon(monkeypatch):
    """部件 @wmhdr 必须用 subIcon 部位图（set 响应里同套部件 icon 是同一 hash 主图）。"""
    async def fake_market_get(url: str, params: str = "") -> dict | None:
        if "/v2/orders/item/" in url:
            return {"data": [{"type": "sell", "platinum": 6, "quantity": 1,
                              "user": {"status": "ingame", "ingameName": "Seller",
                                       "id": "u1"}}]}
        if "/v2/item/" in url and "/set" in url:
            return {"data": {"items": [{
                "slug": "wukong_prime_neuroptics_blueprint",
                "tags": ["component", "prime", "warframe", "blueprint"],
                "ducats": 15, "tradingTax": 2000,
                "i18n": {"en": {
                    "name": "Wukong Prime Neuroptics Blueprint",
                    "icon": "items/images/en/wukong_prime_neuroptics.SETROOT.png",
                    "subIcon": "sub_icons/warframe/prime_helmet_128x128.png",
                }},
            }]}}
        if "/v1/items/" in url and "/statistics" in url:
            return {"payload": {"statistics_closed": {"48hours": [], "90days": []},
                                "statistics_live": {"48hours": [], "90days": []}}}
        return None

    monkeypatch.setattr(warframe, "_market_get", fake_market_get)
    warframe._MARKET_CACHE.update({"key": "", "ts": 0.0, "data": None})
    warframe._MARKET_NAME_CACHE.update({"key": "", "ts": 0.0, "name": ""})
    warframe._MARKET_META_CACHE.clear()

    r = asyncio.run(warframe.market_text("wukong_prime_neuroptics_blueprint", page=1))
    assert "sub_icons/warframe/prime_helmet_128x128.png" in r, r
    assert "SETROOT.png" not in r, "部件仍在用套装主图"
    assert "部件 · 战甲 · 蓝图" in r


def test_prime_weapon_part_prefers_wm_prime_subicon(monkeypatch):
    """Prime 武器部件必须优先走 WM 的 prime_* subIcon（金饰图），不能落普通通用图。

    回归背景：2026-11-06 用户反馈「武器部件有 Prime 的，卡片却显示成普通的」。
    旧 `_PART_ART_MAP` 把 prime_barrel/receiver/stock 直接映射到普通
    Barrel/Receiver/Stock.png，导致 Prime 部件与普通部件同图、看不出 Prime。
    """
    assert {"prime_barrel", "prime_receiver", "prime_stock"} <= warframe._PRIME_WM_PREFER
    assert "prime_helmet" not in warframe._PRIME_WM_PREFER   # 战甲部位有专属 Fandom 图
    assert "barrel" not in warframe._PRIME_WM_PREFER          # 普通部件仍走通用图

    async def fake_prime(rel):
        return b"PRIME-WM"

    monkeypatch.setattr(warframe, "_fetch_prime_subicon", fake_prime)
    got = asyncio.run(warframe.fetch_part_art("sub_icons/weapon/prime_barrel_128x128.png"))
    assert got == b"PRIME-WM", "Prime 部件未优先走 WM prime subIcon"


def test_collect_market_item_icon_routing(monkeypatch):
    """套装取官方全图；部件取部位图；MOD/赋能/总图取 WM 商品自身 icon。"""
    # 必须与文件顶部同一包路径，否则 monkeypatch 打不到函数内部引用的 warframe。
    from src.plugins.qq_bridge.game_tools import _collect_market_item_icon

    calls: dict = {}

    async def fake_official(en):
        calls["official"] = en
        return b"OFFICIAL"

    async def fake_wm(rel):
        calls["wm"] = rel
        return b"WM"

    async def fake_part(rel):
        calls["part"] = rel
        return b"PART"

    monkeypatch.setattr(warframe, "fetch_official_art", fake_official)
    monkeypatch.setattr(warframe, "fetch_item_icon", fake_wm)
    monkeypatch.setattr(warframe, "fetch_part_art", fake_part)

    def _hdr(cat: str, en: str = "X") -> str:
        return (f"@wmhdr|测试|{en}|{cat}|0|0|1|1|icons/x.png|\n"
                "@hdr|卖家|状态|声望|数量|白金价格\n")

    # 武器套装 / 战甲套装 → 官方完整图（WM 的方形图会把枪管裁掉、战甲截半身）
    for cat in ("武器 · 套装 · 副武器", "套装 · 战甲"):
        calls.clear()
        assert asyncio.run(_collect_market_item_icon(_hdr(cat))) == b"OFFICIAL", cat
        assert "official" in calls and "wm" not in calls, cat
    # 套装后缀要剥掉：官方图文件名是「Sicarus Prime」，不是「Sicarus Prime Set」
    calls.clear()
    assert asyncio.run(_collect_market_item_icon(
        _hdr("武器 · 套装 · 副武器", "Sicarus Prime Set"))) == b"OFFICIAL"
    assert calls.get("official") == "Sicarus Prime", calls

    # 部件 → Fandom 部位图；总图（蓝图且非部件）→ WM 商品自身 icon，绝不用通用工程图
    calls.clear()
    assert asyncio.run(_collect_market_item_icon(_hdr("部件 · 武器"))) == b"PART"
    assert "part" in calls and "official" not in calls
    calls.clear()
    assert asyncio.run(_collect_market_item_icon(_hdr("蓝图 · 战甲"))) == b"WM"
    assert "wm" in calls and "part" not in calls and "official" not in calls

    # MOD / 赋能 → WM 卡面图
    for cat in ("传奇 · MOD · 主武器 · 霰弹枪", "传奇 · 赋能"):
        calls.clear()
        assert asyncio.run(_collect_market_item_icon(_hdr(cat))) == b"WM", cat
        assert "wm" in calls and "official" not in calls and "part" not in calls, cat

    # subIcon 文件名 → Fandom 通用部位图 映射
    assert warframe._part_art_name(
        "sub_icons/warframe/prime_chassis_128x128.png") == "PrimeChassis.png"
    assert warframe._part_art_name(
        "sub_icons/warframe/prime_helmet_128x128.png") == "PrimeHelmet.png"
    assert warframe._part_art_name(
        "sub_icons/weapon/prime_barrel_128x128.png") == "Barrel.png"
    assert warframe._part_art_name(
        "sub_icons/blueprint_128x128.png") is None   # 通用 BLUEPRINT 工程图不映射
    assert warframe._part_art_name(
        "sub_icons/melee/prime_blade_128x128.png") == "Blade.png"
    assert warframe._part_art_name("unknown/foo.png") is None


class _FakeResp:
    def __init__(self, status, content, ctype="image/png"):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": ctype}


def _real_png() -> bytes:
    from io import BytesIO
    from PIL import Image
    img = Image.new("RGBA", (4, 4), (200, 50, 80, 255))
    buf = BytesIO(); img.save(buf, "PNG"); return buf.getvalue()


def _no_avatar_cache(monkeypatch):
    """绕过磁盘缓存，保证走 mock 网络，测试确定性（不污染/读取 .wm_cache）。"""
    monkeypatch.setattr(warframe, "_avatar_cache_get", lambda *a, **k: warframe._MISS)
    monkeypatch.setattr(warframe, "_avatar_cache_put", lambda *a, **k: None)


def test_fetch_market_avatars_direct_with_cookie(monkeypatch):
    """有 cf_clearance 时先直连 static/assets，cookie 必须透传（过 CF 的关键）。"""
    png = _real_png()
    calls = []

    async def fake_get(url, headers, cookies, timeout):
        calls.append((url, cookies))
        return _FakeResp(200, png)

    monkeypatch.setattr(warframe, "_http_get", fake_get)
    _no_avatar_cache(monkeypatch)
    monkeypatch.setattr(
        warframe, "_load_cf_cookies",
        lambda: [{"name": "cf_clearance", "value": "CLEARED", "domain": ".warframe.market"}])

    out = asyncio.run(warframe.fetch_market_avatars([("u_ok", "user/avatar/aaa.png?h1")]))
    assert out["u_ok"] == png, "真图应返回 PNG bytes"
    assert len(calls) == 1, "有 cookie 且直连成功就不该再打代理"
    assert calls[0][0] == warframe.WM_STATIC + "user/avatar/aaa.png?h1"
    assert calls[0][1].get("cf_clearance") == "CLEARED", "cf_clearance 必须透传到头像请求"


def test_fetch_market_avatars_proxy_fallback_without_cookie(monkeypatch):
    """没有 cf_clearance 时跳过 3.4s 的直连（CF 必然 403），直接走 wsrv 公共图片代理。"""
    png = _real_png()
    calls = []

    async def fake_get(url, headers, cookies, timeout):
        calls.append(url)
        return _FakeResp(200, png)

    monkeypatch.setattr(warframe, "_http_get", fake_get)
    _no_avatar_cache(monkeypatch)
    monkeypatch.setattr(warframe, "_load_cf_cookies", lambda: [])

    out = asyncio.run(warframe.fetch_market_avatars([("u_ok", "user/avatar/aaa.png?h1")]))
    assert out["u_ok"] == png, "代理取到真图也应返回 bytes"
    assert len(calls) == 1, "无 cookie 应只打代理，不该先直连（省 3.4s）"
    assert calls[0].startswith("https://wsrv.nl/?url="), "应走 wsrv 公共图片代理"
    assert "warframe.market" in calls[0]


def test_fetch_market_avatars_rejects_non_image(monkeypatch):
    """代理返回 HTML 错误页（非图片）时必须回落 None，不能把错误页当头像。"""
    async def fake_get(url, headers, cookies, timeout):
        return _FakeResp(200, b"<html>not image</html>", ctype="text/html")

    monkeypatch.setattr(warframe, "_http_get", fake_get)
    _no_avatar_cache(monkeypatch)
    monkeypatch.setattr(warframe, "_load_cf_cookies", lambda: [])

    out = asyncio.run(warframe.fetch_market_avatars([("u_ok", "user/avatar/aaa.png?h1")]))
    assert out["u_ok"] is None


# ---------------- 真实网络冒烟（容错：失败也须返回非空字符串） ----------------
def test_live_worldstate_smoke():
    r = asyncio.run(warframe.sortie_text())
    assert isinstance(r, str) and r.strip()


def test_live_price_smoke():
    r = asyncio.run(warframe.price_text("充沛"))
    assert isinstance(r, str) and r.strip()


def test_live_void_trader_smoke():
    r = asyncio.run(warframe.void_trader_text())
    assert isinstance(r, str) and r.strip()


# ---------------- 请求频率保护（单飞锁：缓存过期并发也只发 1 次 HTTP） ----------------
def test_worldstate_singleflight(monkeypatch):
    calls: list[str] = []

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        await asyncio.sleep(0.05)
        return {"sorties": {"data": [{"faction": "X", "bossName": "B", "end": 0}]}}

    monkeypatch.setattr(warframe, "_fetch", fake_fetch)
    warframe._cache = {"ts": 0.0, "data": None}  # 重置缓存，保证 miss

    async def run():
        await asyncio.gather(
            warframe.sortie_text(), warframe.sortie_text(), warframe.sortie_text(),
            warframe.void_trader_text(),
        )

    asyncio.run(run())
    assert len(calls) == 1, f"并发查询应只发 1 次 HTTP，实际 {len(calls)} 次"


def test_rate_gate_min_interval():
    """全局限速闸：相邻两次外部请求间隔 >= _MIN_INTERVAL。"""
    import time as _t

    warframe._req_gate["ts"] = 0.0

    async def run() -> float:
        await warframe._rate_gate()
        t0 = _t.monotonic()
        await warframe._rate_gate()
        return _t.monotonic() - t0

    dt = asyncio.run(run())
    assert dt >= warframe._MIN_INTERVAL - 0.05


def test_handle_wf_alias_sends_card():
    """回归：@机器人 毒妈 -> 战甲资料卡，绝不抛异常/绝不静默。

    2026-09-16 线上「发了没反应」根因：_download_image 用 os.path 但模块
    未 import os -> NameError 被 nonebot 吞掉 -> 用户零反馈。此用例保证
    _handle 走完整链路（dispatch -> 头像 -> _send_guide -> send_pic）不炸。
    """
    from plugins.qq_bridge.game_tools import _handle
    from plugins.qq_bridge import media

    class Seg:
        def __init__(self, t, d):
            self.type = t
            self.data = d

    class FakeMatcher:
        def stop_propagation(self):
            pass

    class FakeEvent:
        user_id = 900000002
        group_id = 900000001
        channel = "napcat"
        original_message = [Seg("at", {"qq": "900000009"}), Seg("text", {"text": " 毒妈"})]
        message = [Seg("text", {"text": "@900000009毒妈"})]

        def get_plaintext(self):
            return "@900000009毒妈"

        def get_matcher(self):
            return FakeMatcher()

    class FakeBot:
        self_id = "900000009"

        async def send(self, event, msg, **kw):
            raise AssertionError("不应走纯文本降级")

        async def call_api(self, action, **params):
            raise AssertionError("不应真发消息")

    sent = []

    async def fake_send_pic(ctx, data, at_user=False):
        sent.append(len(data))

    orig = media.send_pic
    media.send_pic = fake_send_pic
    try:
        asyncio.run(_handle(FakeBot(), FakeEvent()))
    finally:
        media.send_pic = orig
    assert sent and sent[0] > 1000, "应发出卡片，实际 sent=%s" % sent
