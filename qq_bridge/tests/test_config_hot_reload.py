# -*- coding: utf-8 -*-
"""qq_bridge 配置文件「热生效」验收（2026-09-07 新增）。

背景：之前 apply_config() 一刀切标 requires_restart=true，但运行时直接读 Config.X
的业务代码（dispatcher.allow / safety / intent.validate_prompt / taskmgr.LOCAL_WIDTH 等）
都是运行时类属性访问——setattr(Config, key, val) 立即生效，无需重启 bridge。

本测试覆盖：
  1) 非 core 参数 PUT → Config 类属性被同步更新 + 在 hot_applied + 不在 requires_restart
  2) core 参数 PUT → Config 类属性不被改 + 在 requires_restart + 不在 hot_applied
  3) RateLimiter（缓存了 Config 的单例）→ 改 Config.GROUP_COOLDOWN 后立即生效
  4) RateLimiter（缓存了 Config 的单例）→ 改 Config.DAILY_QUOTA 后立即生效
  5) _hot_apply 类型转换：int/float/bool/intlist/strlist/text 各分支
  6) 热生效失败兜底：写入 .env 成功但 Config.X 改不动 → 报 errors + 退入 requires_restart
  7) 安全性：core=true 的 ACCESS_TOKEN 改 → 不被热改（仍要求重启）
  8) get_config_view 读到的 value 是热改后的最新值（验证反向读取链路）

不依赖 nonebot / 主项目，单测直接加载 config_api 模块（已有去相对 import）。
"""
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PLUGIN = _REPO / "src" / "plugins" / "qq_bridge"
_PLUGINS_PARENT = _REPO / "src" / "plugins"  # 让 `from qq_bridge.intent import` 能解析
sys.path.insert(0, str(_PLUGINS_PARENT))
sys.path.insert(0, str(_PLUGIN))


def _load():
    import config_api
    from qq_bridge.config import Config

    # 本文件的「世界」是顶级 qq_bridge 副本（_PLUGIN 在 sys.path，全部 import 与断言
    # 都走它）。而 config_api._resolve_runtime_config() 在生产路径优先返回
    # sys.modules['src.plugins.qq_bridge.config'].Config——全量跑时其他测试文件
    # （bg_overrides/channel_official/choice_session 等）已先加载该副本，热生效会
    # setattr 到 src 那份、本文件断言读顶级那份 → 假失败（单跑不现）。
    # 此处把解析器钉到本文件世界的同一份 Config，保证 setattr 与断言同源。
    # 不动 sys.modules——替换模块别名会让先加载的顶级子模块持旧副本，反制造错位。
    config_api._resolve_runtime_config = lambda: Config
    return config_api


@pytest.fixture(autouse=True)
def _reset_config_singletons():
    """每个用例前后还原 Config，避免污染其它测试。

    config.Config 是 class attributes，环境变量会污染；
    这里显式重置为「默认」值，模拟启动一次的效果。
    """
    from qq_bridge.config import Config, _as_bool, _as_float, _as_int, _as_int_set, _as_str_tuple

    def _restore():
        Config.PROMPT_MAX_LEN = _as_int(os.environ.get("PROMPT_MAX_LEN"), 300)
        Config.DAILY_QUOTA = _as_int(os.environ.get("DAILY_QUOTA"), 50)
        Config.GROUP_COOLDOWN = _as_int(os.environ.get("GROUP_COOLDOWN"), 5)
        Config.PRIVATE_COOLDOWN = _as_int(os.environ.get("PRIVATE_COOLDOWN"), 10)
        Config.LOCAL_STEPS = _as_int(os.environ.get("LOCAL_STEPS"), 0)
        Config.STICKER_CHANCE = _as_float(os.environ.get("STICKER_CHANCE"), 0.25)
        Config.KEYWORD_GATE = _as_bool(os.environ.get("KEYWORD_GATE"), True)
        Config.DRAW_KEYWORDS = _as_str_tuple(
            os.environ.get("DRAW_KEYWORDS"), ("生图", "画图", "作画", "生成图片", "draw")
        )
        Config.TEST_KEYWORDS = _as_str_tuple(
            os.environ.get("TEST_KEYWORDS"), ("hi", "hello", "你好", "嗨", "在吗")
        )
        Config.BOT_OWNER_QQ = _as_int_set(os.environ.get("BOT_OWNER_QQ"))
        Config.USER_BLACKLIST = _as_int_set(os.environ.get("USER_BLACKLIST"))
        Config.STRICT_WHITELIST = _as_bool(os.environ.get("STRICT_WHITELIST"), False)
        Config.TEST_REPLY = os.environ.get("TEST_REPLY") or "在呢喵~"

    _restore()
    yield
    _restore()


# ================ 1) 非 core 参数 PUT → 热生效 ================

def test_non_core_param_is_hot_applied(tmp_path, monkeypatch):
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("PROMPT_MAX_LEN=300\n")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    assert Config.PROMPT_MAX_LEN == 300
    res = config_api.apply_config({"PROMPT_MAX_LEN": "500"})
    assert res["errors"] == {}
    assert "PROMPT_MAX_LEN" in res["hot_applied"]
    assert "PROMPT_MAX_LEN" not in res["requires_restart"]
    # 立即生效
    assert Config.PROMPT_MAX_LEN == 500
    # 持久化到 .env
    assert "PROMPT_MAX_LEN=500" in env.read_text(encoding="utf-8")


def test_group_cooldown_hot_applied(tmp_path, monkeypatch):
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({"GROUP_COOLDOWN": "8"})
    assert "GROUP_COOLDOWN" in res["hot_applied"]
    assert Config.GROUP_COOLDOWN == 8


def test_daily_quota_hot_applied(tmp_path, monkeypatch):
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({"DAILY_QUOTA": "100"})
    assert "DAILY_QUOTA" in res["hot_applied"]
    assert Config.DAILY_QUOTA == 100


def test_draw_keywords_strlist_conversion(tmp_path, monkeypatch):
    """DRAW_KEYWORDS 是 strlist → 应转为 tuple。"""
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({"DRAW_KEYWORDS": "生图,作画,new_kw"})
    assert "DRAW_KEYWORDS" in res["hot_applied"]
    assert Config.DRAW_KEYWORDS == ("生图", "作画", "new_kw")


def test_bot_owner_qq_intlist_conversion(tmp_path, monkeypatch):
    """BOT_OWNER_QQ 是 intlist → 应转为 set。"""
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({"BOT_OWNER_QQ": "111,222,333"})
    assert "BOT_OWNER_QQ" in res["hot_applied"]
    assert Config.BOT_OWNER_QQ == {111, 222, 333}


def test_bool_conversion(tmp_path, monkeypatch):
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    for raw, expected in [("true", True), ("false", False), ("1", True), ("0", False), ("yes", True), ("no", False)]:
        res = config_api.apply_config({"STRICT_WHITELIST": raw})
        assert res["errors"] == {}
        assert Config.STRICT_WHITELIST is expected, f"raw={raw} 应得 {expected}，实得 {Config.STRICT_WHITELIST}"


def test_float_conversion(tmp_path, monkeypatch):
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({"STICKER_CHANCE": "0.5"})
    assert Config.STICKER_CHANCE == 0.5


# ================ 2) core 参数 PUT → 不热生效 + 在 requires_restart ================

def test_core_param_is_not_hot_applied(tmp_path, monkeypatch):
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("IMAGE_CHANNEL=online\n")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    assert Config.IMAGE_CHANNEL == "online"
    res = config_api.apply_config({"IMAGE_CHANNEL": "local"})
    assert res["errors"] == {}
    assert "IMAGE_CHANNEL" in res["requires_restart"]
    assert "IMAGE_CHANNEL" not in res["hot_applied"]
    # 但 .env 仍然写入（重启即生效）
    assert "IMAGE_CHANNEL=local" in env.read_text(encoding="utf-8")


def test_access_token_never_hot_applied(tmp_path, monkeypatch):
    """OneBot Token 是 core=true（启动期一次性绑定），永远要求重启。"""
    config_api = _load()

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({"ONEBOT_V11_ACCESS_TOKEN": "newtoken123"})
    assert res["errors"] == {}
    assert "ONEBOT_V11_ACCESS_TOKEN" in res["requires_restart"]
    assert "ONEBOT_V11_ACCESS_TOKEN" not in res["hot_applied"]


def test_host_port_core(tmp_path, monkeypatch):
    """HOST/PORT 是 NoneBot 启动参数，必须重启。"""
    config_api = _load()

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({"HOST": "0.0.0.0", "PORT": "8081"})
    assert set(res["requires_restart"]) == {"HOST", "PORT"}
    assert not set(res["hot_applied"]) & {"HOST", "PORT"}


# ================ 3) RateLimiter（关键：单例缓存路径）==============

def test_rate_limiter_sees_hot_change(tmp_path, monkeypatch):
    """核心验证：rate_limiter 单例在 Config 改后立即读到新值。

    这是整个热生效修复的「关键防线」——之前 RateLimiter.__init__ 把 Config 拷到
    self.group_cooldown，导致改 Config 没用。现在 property 化后必须立即生效。
    """
    config_api = _load()
    from qq_bridge.config import Config
    from qq_bridge.ratelimit import RateLimiter

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    rl = RateLimiter()  # 走单例路径（实际项目里 rate_limiter 就是这么用）
    assert rl.group_cooldown == Config.GROUP_COOLDOWN == 5

    res = config_api.apply_config({"GROUP_COOLDOWN": "99"})
    assert "GROUP_COOLDOWN" in res["hot_applied"]
    assert Config.GROUP_COOLDOWN == 99

    # RateLimiter 必须立即看到新值（之前是 self.group_cooldown=5 永远不变）
    assert rl.group_cooldown == 99


def test_rate_limiter_daily_quota_sees_hot_change(tmp_path, monkeypatch):
    config_api = _load()
    from qq_bridge.config import Config
    from qq_bridge.ratelimit import RateLimiter

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    rl = RateLimiter()
    assert rl.daily_quota == 50

    config_api.apply_config({"DAILY_QUOTA": "0"})  # 0=不限配额
    assert Config.DAILY_QUOTA == 0
    assert rl.daily_quota == 0


def test_rate_limiter_constructor_override_still_works(tmp_path, monkeypatch):
    """构造参数 override 必须仍工作（向后兼容已有测试与未来单测需要）。"""
    from qq_bridge.ratelimit import RateLimiter

    rl = RateLimiter(private_cooldown=7, group_cooldown=4, daily_quota=10)
    assert rl.private_cooldown == 7
    assert rl.group_cooldown == 4
    assert rl.daily_quota == 10

    # 注：override 不空时返回 override，不读 Config
    rl2 = RateLimiter(group_cooldown=4)
    assert rl2.group_cooldown == 4


# ================ 4) 业务代码即时生效（intent.validate_prompt 等）==============

def test_validate_prompt_sees_hot_change(tmp_path, monkeypatch):
    """改 PROMPT_MAX_LEN 后 intent.validate_prompt 必须立即看到新阈值。"""
    config_api = _load()
    from qq_bridge.intent import validate_prompt, truncate_prompt

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    long_text = "x" * 400
    ok, why = validate_prompt(long_text)
    assert ok is False
    assert "300" in why

    # 改阈值 → 立即生效
    config_api.apply_config({"PROMPT_MAX_LEN": "500"})
    ok2, why2 = validate_prompt(long_text)
    assert ok2 is True, "改后应放行 400 字符，实际 why=%r" % why2
    assert why2 == ""

    # truncate_prompt 也跟着变
    t, trunc = truncate_prompt("x" * 600)
    assert len(t) == 500


# ================ 5) get_config_view 反向读取 ================

def test_get_config_view_returns_hot_value(tmp_path, monkeypatch):
    """GET /api/qq/config 必须返回热改后的最新值，不能漏。"""
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("PROMPT_MAX_LEN=300\n")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    # 改 Config
    config_api.apply_config({"PROMPT_MAX_LEN": "888"})
    assert Config.PROMPT_MAX_LEN == 888

    # GET view 必须反映新值
    view = config_api.get_config_view()
    items = {it["key"]: it["value"] for g in view["groups"] for it in g["items"]}
    assert items["PROMPT_MAX_LEN"] == "888"  # 字符串展示


# ================ 6) 异常路径：热生效失败不丢持久化 ================

def test_hot_apply_failure_still_persists_to_env(tmp_path, monkeypatch):
    """若 _hot_apply 抛异常，.env 仍写入（重启即生效），错误信息回传。"""
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    def boom(key, raw_str, typ):
        raise RuntimeError("simulated hot apply failure")
    monkeypatch.setattr(config_api, "_hot_apply", boom)

    res = config_api.apply_config({"PROMPT_MAX_LEN": "400"})
    # 关键：.env 已写入（重启即生效）
    assert "PROMPT_MAX_LEN=400" in env.read_text(encoding="utf-8")
    # 错误已报
    assert "PROMPT_MAX_LEN" in res["errors"]
    # 失败时归入 requires_restart（让用户重启解决）
    assert "PROMPT_MAX_LEN" in res["requires_restart"]
    assert "PROMPT_MAX_LEN" not in res["hot_applied"]
    # Config 类属性不应被改（_hot_apply 抛了）
    assert Config.PROMPT_MAX_LEN == 300  # 仍是原值


# ================ 7) 混合 PUT：core + 非 core 一次提交 ================

def test_mixed_core_and_hot_in_one_put(tmp_path, monkeypatch):
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({
        "IMAGE_CHANNEL": "local",     # core=true
        "PROMPT_MAX_LEN": "600",      # core=false
        "GROUP_COOLDOWN": "10",       # core=false
        "ONEBOT_V11_ACCESS_TOKEN": "t",  # core=true
    })
    assert res["errors"] == {}
    assert set(res["requires_restart"]) == {"IMAGE_CHANNEL", "ONEBOT_V11_ACCESS_TOKEN"}
    assert set(res["hot_applied"]) == {"PROMPT_MAX_LEN", "GROUP_COOLDOWN"}
    # 热生效部分已生效
    assert Config.PROMPT_MAX_LEN == 600
    assert Config.GROUP_COOLDOWN == 10
    # core 部分 Config 不被改（仍原值）
    assert Config.IMAGE_CHANNEL == "online"
    # 持久化全有
    text = env.read_text(encoding="utf-8")
    assert "IMAGE_CHANNEL=local" in text
    assert "PROMPT_MAX_LEN=600" in text
    assert "GROUP_COOLDOWN=10" in text
    assert "ONEBOT_V11_ACCESS_TOKEN=t" in text


# ================ 8) 校验失败：错值不会写 ================

def test_invalid_value_blocks_whole_put(tmp_path, monkeypatch):
    """任何 key 校验失败 → 整体不写 .env + 不热改 + errors 非空。"""
    config_api = _load()
    from qq_bridge.config import Config

    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({
        "PROMPT_MAX_LEN": "abc",  # 非整数
        "GROUP_COOLDOWN": "8",
    })
    assert "PROMPT_MAX_LEN" in res["errors"]
    assert res["saved"] == []
    assert res["hot_applied"] == []
    assert res["requires_restart"] == []
    # Config 不被改
    assert Config.PROMPT_MAX_LEN == 300
    assert Config.GROUP_COOLDOWN == 5
    # .env 不写
    assert env.read_text(encoding="utf-8") == ""