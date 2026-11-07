"""验证 PIC_CUSTOM_THEME 链路：resolve(None) 应读到编辑器激活的自定义主题"""
from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.pic.theme import _config_theme, resolve


def test_custom_theme_resolve_chain():
    """激活自定义主题后，render(theme_name=None) 应走 PIC_CUSTOM_THEME 而非 fallback 到 neon"""
    print(f"\nConfig.PIC_CUSTOM_THEME = {repr(getattr(Config, 'PIC_CUSTOM_THEME', '<NOT SET>'))}")
    print(f"Config.PIC_THEME = {repr(getattr(Config, 'PIC_THEME', '<NOT SET>'))}")
    print(f"_config_theme() = {repr(_config_theme())}")

    t = resolve(None)
    print(f"resolve(None) -> name={t.name!r} bg={t.bg!r}")

    # 如果 cs1 已激活（通过 API POST /activate），这里应返回 cs1
    # 否则说明 _config_theme 没读到 PIC_CUSTOM_THEME
    custom = getattr(Config, 'PIC_CUSTOM_THEME', '') or ''
    if custom:
        assert t.name == custom, f"激活了 {custom} 但 resolve 返回 {t.name}"
        print(f"PASS: 自定义主题 {custom} 生效")
    else:
        # 未激活任何自定义主题，应回落到 PIC_THEME 或 neon
        print(f"INFO: 无自定义主题激活，当前回落到 {t.name}")
