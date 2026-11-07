# -*- coding: utf-8 -*-
"""QQ 机器人图形化管理后台的配置读写接口（设计文档 §管理后台）。

职责：
  - 暴露 GET  /api/qq/config  → 返回全部可配置参数（按分组 + 常规/核心标记 + 当前值）
  - 暴露 PUT  /api/qq/config  → 接收 {updates:{key:value}}，写回 .env（保留注释与顺序），
                                返回哪些 key 需要重启 bridge 才生效
  - 配置数据全部存于 qq_bridge/.env（本目录内，不碰主项目），红线合规。

铁律：
  - 所有密钥（ONEBOT_V11_ACCESS_TOKEN 等）只经 HTTP 在「管理页」展示，绝不落日志（LOG-04）。
  - 本模块不 import nonebot，可被单测直接导入（与 config.py 一致）。
  - qq_bridge 与主项目物理隔离：本文件只读/写 qq_bridge/.env，不 import 任何 server/ 模块。
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

# ---- 运行时真实默认值来源（2026-09-05 修复「有默认值但页面显示空」）----
# 背景：.env 未显式配置某些 key 时，原先直接回退 SCHEMA 的 default 字符串，
# 而 SCHEMA 里 TEST_REPLY / GROUP_JOIN_REPLY / GROUP_WELCOME_REPLY /
# LOCAL_NEGATIVE_PROMPT / MANJU_FILE_BASE 的 default 写死为 ""，导致前端输入框
# 显示空白——但 config.py 里这些字段有真实生效的默认文案（或派生值），
# 用户误以为"参数有默认值却没展示"。
# 现状：改为优先取 config.Config 的运行时真实值（单一事实来源 = config.py），
# 取不到才回退 SCHEMA default。config.py 自身不 import nonebot，导入它是安全的，
# 不会破坏本模块的 nonebot 隔离链（与 config.py 一致的可单测性）。
# 包内相对导入（正常插件加载路径）。包路径与单模块路径会产生两个不同的
# Config 类对象（Python class 按所在 module namespace 生成），单测/热生效
# 必须保持一致——否则 setattr 到 _RuntimeConfig 不影响 qq_bridge.ratelimit
# 用 `from ..config import Config` 加载的另一份。
#
# 关键时序陷阱：config_api 模块加载时，业务模块（qq_bridge.ratelimit 等）尚未
# import——sys.modules['qq_bridge.config'] 还不存在，会 fallback 到单模块 'config'
# 拿到一份独立的 Config 类，与业务模块后续 import 的那份不是同一个对象。
#
# 解法：模块级 _RuntimeConfig 仍按既有路径拿（供 get_config_view 启动期默认值用），
# 热生效改值的 _hot_apply() 改为运行时动态解析，永远拿到业务模块实际用的那份。
try:
    from .config import Config as _RuntimeConfig
except Exception:
    try:
        import sys as _sys
        mod = _sys.modules.get("qq_bridge.config") or _sys.modules.get("config")
        if mod is not None and hasattr(mod, "Config"):
            _RuntimeConfig = mod.Config
        else:
            from config import Config as _RuntimeConfig
    except Exception:
        _RuntimeConfig = None


def _resolve_runtime_config():
    """运行时动态拿 Config 类——保证与业务模块加载的那份是同一个对象。

    真实生产路径（NoneBot `load_plugins("src/plugins")`）：
      sys.modules['src.plugins.qq_bridge.config'].Config   ← 唯一活对象
    单测场景：
      sys.modules['qq_bridge.config'].Config                ← 部分早期 import
      sys.modules['config'].Config                          ← 旧 fallback
    历史 bug：之前只查 'qq_bridge.config' / 'config' 两个 key，
    生产环境 NoneBot 加载的 'src.plugins.qq_bridge.config' 全 miss，
    抛 RuntimeError「Config 类不可用」，导致所有非核心参数热修改失败，
    配置回退到"需重启"分支。2026-09-07 用户实测 LOCAL_NEGATIVE_PROMPT
    修改失败时定位到这条铁律。

    修法：多 key 顺序查找 + importlib 兜底动态加载 + sys.modules 兜底扫描。
    """
    import sys as _sys

    # 1) 已知常见 sys.modules key（NoneBot 真实 / 单测 / 老 fallback）
    for name in (
        "src.plugins.qq_bridge.config",  # NoneBot load_plugins('src/plugins') 的真实路径
        "qq_bridge.config",               # 包内绝对路径（部分早期代码用）
        "config",                         # 旧单模块 fallback
    ):
        mod = _sys.modules.get(name)
        if mod is not None and hasattr(mod, "Config"):
            return mod.Config

    # 2) importlib 兜底（当前进程未加载则主动 import；NoneBot 已加载但
    #    sys.modules 因 hash 没匹配上面 key 时也能命中）
    try:
        import importlib
        for mod_name in (
            "src.plugins.qq_bridge.config",
            "qq_bridge.config",
            "config",
        ):
            try:
                return importlib.import_module(mod_name).Config
            except Exception:
                continue
    except Exception:
        pass

    # 3) sys.modules 全扫找 Config 类——只为兜底诊断，
    #    任何带 Config 属性的 module 都尝试一次（只信任包含本项目字段的）
    for mod in list(_sys.modules.values()):
        if not mod or not hasattr(mod, "Config"):
            continue
        cls = getattr(mod, "Config", None)
        if cls is None or not hasattr(cls, "LOCAL_NEGATIVE_PROMPT"):
            continue  # 不是本项目的 Config 类，跳过
        return cls

    raise RuntimeError("Config 类不可用（sys.modules 无 qq_bridge.config/config，也无 importlib 兜底命中）")

# 不依赖 .config 的相对导入，便于单测直接加载本模块（隔离 nonebot 导入链）。
# config.py 的 ROOT_DIR 同样是 parents[3]，这里自算一份保持一致。
ROOT_DIR = Path(__file__).resolve().parents[3]
ENV_PATH = ROOT_DIR / ".env"

# ---------------------------------------------------------------------------
# 配置 schema：单一事实来源。
# 每条：(group, key, label, type, options, core, desc, default, placeholder, secret)
#   type: bool | int | float | text | textarea | select | intlist | strlist | secret
#         （secret = 密码框掩码输入，如 AI_API_KEY；后端按 text 直通，禁落日志 LOG-04）
#   core: True=核心参数（启动期一次性绑定——NoneBot 驱动/端口/主项目 API/OneBot Token，
#                     需重启 bridge 才生效；前端保存时二次确认 + 红色标识，改动风险高）
#         False=常规参数（运行时类属性——限流/配额/文案/白名单/风格/默认尺寸/超时等，
#                       setattr(Config, key, val) 立即生效，无需重启）
#   apply_config() 按 core 拆分返回：
#     - requires_restart = [core=true 的 key]   ← 老字段，前端兼容
#     - hot_applied = [core=false 的 key]      ← 新增字段，前端可读「已热生效」
# ---------------------------------------------------------------------------
SCHEMA: list[tuple] = [
    # ---- 核心·身份与权限 ----
    # 登录账号：真正的登录来源是容器环境变量 ACCOUNT（docker-compose.napcat.yml）。
    # 2026-10-29 Docker 化后，start_all.ps1 已不再从这里读账号，改此项不会切换 NapCat 登录的号。
    ("核心·身份与权限", "BOT_UIN", "机器人登录 QQ 号", "text", None, True,
     "⚠️ 仅供参考：NapCat 实际登录的账号由 docker-compose.napcat.yml 的 ACCOUNT 环境变量决定，"
     "改这里不会切换登录号（换号需改 compose 后重建容器并用新号扫码）。"
     "账号安全铁律：禁止内置默认号（原默认值 3858741376 已于 2026-09-16 被封，已清空）",
     "", "", False),
    ("核心·身份与权限", "ONEBOT_V11_ACCESS_TOKEN", "OneBot 鉴权 Token", "text", None, True,
     "NapCat 客户端须与此一致；泄露等于他人可控制你的机器人", "", "8ebfc30d5a726914", True),
    # 2026-09-07：以下权限类参数由 core=true 改为 core=false——dispatcher.allow / safety
    # 都是运行时直接读 Config.X（类属性），setattr(Config, key, val) 立即生效，无需重启。
    ("核心·身份与权限", "BOT_OWNER_QQ", "主人 QQ（白名单）", "intlist", None, False,
     "仅这些 QQ 可触发生图/管理；逗号分隔（热生效）", "", "1743124119", False),
    ("核心·身份与权限", "STRICT_WHITELIST", "严格白名单模式", "bool", None, False,
     "开启后未列入白名单的私聊/群一律静默（热生效）", "false", "", False),
    ("核心·身份与权限", "GROUP_WHITELIST", "监听群（白名单）", "intlist", None, False,
     "仅这些群内机器人会响应；逗号分隔（热生效）", "", "1075851417", False),
    ("核心·身份与权限", "PRIVATE_WHITELIST", "私聊白名单", "intlist", None, False,
     "允许私聊触发的 QQ；空=仅主人（热生效）", "", "", False),
    ("核心·身份与权限", "USER_BLACKLIST", "用户黑名单", "intlist", None, False,
     "这些 QQ 永久静默（含主人之外，热生效）", "", "", False),
    ("核心·身份与权限", "SAFETY_ENABLE", "内容安全过滤", "bool", None, False,
     "开启内置敏感词 + 额外词表过滤（热生效）", "true", "", False),
    ("核心·身份与权限", "SAFETY_EXTRA_WORDS", "额外敏感词", "text", None, False,
     "逗号分隔，与内置词表合并（热生效）", "", "", False),
    # ---- 核心·服务连接 ----
    ("核心·服务连接", "DRIVER", "NoneBot 驱动", "select",
     [{"value": "~fastapi", "label": "~fastapi（默认·推荐）"},
      {"value": "~quart", "label": "~quart（备用）"}], True,
     "ASGI 驱动（改错将导致机器人无法启动，需重启）", "~fastapi", "", False),
    ("核心·服务连接", "HOST", "监听地址", "select",
     [{"value": "127.0.0.1", "label": "127.0.0.1（仅本机，NapCat 与 bridge 同机）"},
      {"value": "0.0.0.0", "label": "0.0.0.0（监听所有网卡，跨机部署用）"}], True,
     "OneBot WS 服务端监听地址。127.0.0.1=仅本机（NapCat 与 bridge 同机）；0.0.0.0=监听所有网卡（跨机部署时改）", "0.0.0.0", "", False),
    ("核心·服务连接", "PORT", "监听端口", "int", None, True,
     "OneBot WS 服务端端口（NapCat 连此）", "8080", "", False),
    ("核心·服务连接", "MANJU_API_BASE", "主项目 API 地址", "text", None, True,
     "能力层调用的主项目后端 /api/v1", "http://127.0.0.1:7788/api/v1", "", False),
    ("核心·服务连接", "MANJU_FILE_BASE", "主项目文件地址", "text", None, True,
     "图片/视频静态资源根（留空=从 API 地址派生）", "", "", False),
    # ---- 核心·生图通道 ----
    ("核心·生图通道", "IMAGE_CHANNEL", "生图通道", "select",
     [{"value": "online", "label": "online（在线云端·并发·推荐）"},
      {"value": "local", "label": "local（本地 ComfyUI·独占·串行）"}], True,
     "online=本地 ComfyUI 串行；local=云端并发", "online", "", False),
    ("核心·生图通道", "LOCAL_TEMPLATE_ID", "本地模板", "select", None, True,
     "本地通道使用的 ComfyUI 工作流模板（管理页自动从主项目已建模板下拉选，无需手填 ID；留空=未启用本地通道）", "", "", False),
    # ---- 常规·生图参数 ----
    ("常规·生图参数", "DEFAULT_WIDTH", "在线出图宽", "int", None, False,
     "在线通道默认出图宽度（本地通道另配「本地出图宽」）", "1024", "", False),
    ("常规·生图参数", "DEFAULT_HEIGHT", "在线出图高", "int", None, False,
     "在线通道默认出图高度", "1024", "", False),
    ("常规·生图参数", "DEFAULT_COUNT", "在线出图张数", "int", None, False,
     "在线通道每次生成的图片张数", "1", "", False),
    ("常规·生图参数", "DEFAULT_NEGATIVE_PROMPT", "在线负面词", "textarea", None, False,
     "在线通道负面提示词（本地通道未单独配置时复用此值）", "低质量,模糊,水印,文字,变形,多余肢体,low quality,blurry,watermark,deformed", "", False),
    ("常规·生图参数", "DEFAULT_STRENGTH", "在线重绘幅度", "float", None, False,
     "在线图生图重绘幅度 0~1，越大越背离原图", "0.65", "", False),
    ("常规·生图参数", "DEFAULT_VIDEO_MODEL", "默认视频模型", "text", None, False,
     "视频生成默认模型（留空=主项目后端默认）", "", "", False),
    ("常规·生图参数", "LOCAL_WIDTH", "本地出图宽", "int", None, False,
     "0=跟随模板尺寸", "0", "", False),
    ("常规·生图参数", "LOCAL_HEIGHT", "本地出图高", "int", None, False,
     "0=跟随模板尺寸", "0", "", False),
    ("常规·生图参数", "LOCAL_STEPS", "采样步数", "int", None, False,
     "0=跟随模板值（蒸馏类模型千万别硬填 20）", "0", "", False),
    ("常规·生图参数", "LOCAL_CFG", "CFG 值", "float", None, False,
     "0=跟随模板值（蒸馏类模型别硬填 7.0）", "0.0", "", False),
    ("常规·生图参数", "LOCAL_STYLE", "画面风格", "select",
     [{"value": "写实", "label": "写实"},
      {"value": "动漫", "label": "动漫"},
      {"value": "插画", "label": "插画"},
      {"value": "油画", "label": "油画"},
      {"value": "水彩", "label": "水彩"},
      {"value": "3D渲染", "label": "3D 渲染"},
      {"value": "概念", "label": "概念"},
      {"value": "漫画", "label": "漫画"},
      {"value": "黑白", "label": "黑白"},
      {"value": "赛博朋克", "label": "赛博朋克"},
      {"value": "古风", "label": "古风"}], False,
     "本地出图风格标签（选中即写入提示词）", "写实", "", False),
    ("常规·生图参数", "LOCAL_ENHANCE", "提示词润色", "bool", None, False,
     "true=走 Ollama 翻译（需本机 Ollama）；false=原文直出", "false", "", False),
    ("常规·生图参数", "LOCAL_ORCHESTRATOR", "提示词编排", "select",
     [{"value": "none", "label": "none（原文直出·推荐）"},
      {"value": "ollama", "label": "ollama（本机 Ollama 翻译·需先启动）"},
      {"value": "dify", "label": "dify（Dify 编排·需配 Dify 服务）"}], False,
     "none=原文直出；ollama=本机 Ollama 翻译提示词；dify=Dify 编排（需配 Dify 服务）", "none", "", False),
    ("常规·生图参数", "LOCAL_QUEUE_TIMEOUT", "本地排队超时(秒)", "int", None, False,
     "提交前等平台其它本地任务跑完的最长时长", "900", "", False),
    ("常规·生图参数", "LOCAL_QUEUE_INTERVAL", "排队轮询间隔(秒)", "float", None, False,
     "轮询平台本地任务空闲的间隔", "3.0", "", False),
    ("常规·生图参数", "LOCAL_NEGATIVE_PROMPT", "本地负面词", "textarea", None, False,
     "本地出图负面提示词（空=复用通用负面词）", "", "", False),
    ("常规·生图参数", "LOCAL_STRENGTH", "本地重绘幅度", "float", None, False,
     "图生图重绘幅度 0~1，越大越背离原图", "0.65", "", False),
    # ---- 常规·限流与配额 ----
    ("常规·限流与配额", "PRIVATE_COOLDOWN", "私聊冷却(秒)", "int", None, False,
     "同一用户私聊最小间隔", "10", "", False),
    ("常规·限流与配额", "GROUP_COOLDOWN", "群冷却(秒)", "int", None, False,
     "同一用户群聊最小间隔", "5", "", False),
    ("常规·限流与配额", "PROMPT_MAX_LEN", "提示词最大长度", "int", None, False,
     "超过截断", "300", "", False),
    ("常规·限流与配额", "DAILY_QUOTA", "每日配额", "int", None, False,
     "每用户每日生图上限", "50", "", False),
    ("常规·限流与配额", "STICKER_CHANCE", "表情包概率", "float", None, False,
     "每次对话发图概率 0~1", "0.25", "", False),
    ("常规·限流与配额", "STICKER_COOLDOWN", "表情包冷却(秒)", "int", None, False,
     "同用户相邻两次表情包最小间隔", "60", "", False),
    # ---- 常规·关键词与话术 ----
    ("常规·关键词与话术", "KEYWORD_GATE", "关键词门禁", "bool", None, False,
     "开启后仅命中关键词的消息受理，其余静默", "true", "", False),
    ("常规·关键词与话术", "DRAW_KEYWORDS", "生图触发词", "strlist", None, False,
     "逗号分隔，作为前缀命中，其后内容作提示词", "生图,画图,作画,生成图片,draw", "", False),
    ("常规·关键词与话术", "TEST_KEYWORDS", "测试触发词", "strlist", None, False,
     "整句等于其一回固定文案，不生图", "hi,hello,你好,嗨,在吗", "", False),
    ("常规·关键词与话术", "TEST_REPLY", "测试回复语", "textarea", None, False,
     "问候类消息的回复文案", "", "", False),
    ("常规·关键词与话术", "GROUP_JOIN_REPLY", "入群欢迎语", "textarea", None, False,
     "被拉进群时自动发送", "", "", False),
    ("常规·关键词与话术", "GROUP_WELCOME_REPLY", "新人欢迎语", "textarea", None, False,
     "群内新人加入时发送", "", "", False),
    ("常规·关键词与话术", "STICKER_DIR", "表情包目录", "text", None, False,
     "表情包库目录（留空=内置 data/stickers），机器人对话随机发图用", "", "", False),
    ("常规·关键词与话术", "CUSTOM_MAID_ALIASES", "自定义触发口令", "textarea", None, False,
     "每行一条：`口令 => /命令`（整句触发）；口令以 ! 结尾 → 前缀触发（口令后接描述）。命令须为机器人标准斜杠命令（/help /status /task /recent /queue /cancel /promote /clear /monitor /mtasks /mlisten /gallery /video /upscale /outpaint /again /ping /hw /quota /theme /mode）。例：`开饭 => /help`、`画个猫! => /task`。保存即热生效；全部口令清单见「口令清单」（GET /api/qq/intents）", "", "", False),
    ("常规·关键词与话术", "MAID_ALIASES", "内置女仆暗号（覆盖）", "textarea", None, False,
     "留空 = 使用代码内置默认暗号；填写后以本配置为准（整体覆盖内置）。格式同自定义口令：每行 `口令 => /命令`，口令以 ! 结尾 → 前缀触发。管理页「触发口令」清单可直接增删改内置口令，保存后自动写回本字段，保存即热生效", "", "", False),
    ("常规·关键词与话术", "CUSTOM_MAID_ALIASES", "自定义触发口令", "textarea", None, False,
     "每行一条：`口令 => /命令`（整句触发）；口令以 ! 结尾 → 前缀触发（口令后接描述）。命令须为机器人标准斜杠命令（/help /status /task /recent /queue /cancel /promote /clear /monitor /mtasks /mlisten /gallery /video /upscale /outpaint /again /ping /hw /quota /theme /mode）。例：`开饭 => /help`、`画个猫! => /task`。保存即热生效；全部口令清单见「口令清单」（GET /api/qq/intents）", "", "", False),
    # ---- 常规·AI 模式（2026-10-19：在线大模型陪聊，OpenAI 兼容协议）----
    # 标准三件套 = 2026 GitHub 权威项目共识（调研清单见 config.py AI_* 注释）：
    # base_url + api_key + model，任何兼容服务商改三值即接入；全部 core=false
    # （ai_mode.py 运行时读 Config 类属性，setattr 立即生效，无需重启 bridge）。
    ("常规·AI 模式", "AI_MODE_ENABLE", "AI 模式总开关", "bool", None, False,
     "开启后 /ai on 才生效（群/私聊大模型陪聊）；关闭时 /ai 命令提示功能未启用（热生效）",
     "true", "", False),
    ("常规·AI 模式", "AI_API_BASE", "API 地址（base_url）", "text", None, False,
     "OpenAI 兼容 base 地址，填到「版本根」即可。预设：OpenAI https://api.openai.com/v1 ｜ "
     "DeepSeek https://api.deepseek.com/v1 ｜ Moonshot https://api.moonshot.cn/v1 ｜ "
     "阿里百炼 https://dashscope.aliyuncs.com/compatible-mode/v1 ｜ "
     "火山方舟 https://ark.cn-beijing.volces.com/api/v3 ｜ 智谱 https://open.bigmodel.cn/api/paas/v4。"
     "带 /chat/completions 或 /responses 等端点后缀也会自动归一化，不会拼出怪 URL（热生效）",
     "https://api.openai.com/v1", "https://api.openai.com/v1", False),
    ("常规·AI 模式", "AI_API_KEY", "API Key", "secret", None, False,
     "所用服务商的 API Key。仅经本页与 .env 读写，绝不落日志（LOG-04）；未配置时开启 AI 模式会提示来本页填（热生效）",
     "", "sk-...", True),
    ("常规·AI 模式", "AI_MODEL", "模型名", "text", None, False,
     "预设：gpt-4o-mini（默认）｜ deepseek-chat ｜ moonshot-v1-8k ｜ qwen-plus ｜ glm-4.6，以所用服务商文档为准（热生效）",
     "gpt-4o-mini", "gpt-4o-mini", False),
    ("常规·AI 模式", "AI_SYSTEM_PROMPT", "AI 人设（系统提示词）", "textarea", None, False,
     "留空 = 用内置「本喵」腹黑猫管家人设（热生效）。本项仅在「当前人设 = custom」时作为"
     "手搓人设生效；否则以人设库为准（/人设 列表看全部、/人设 用 <id> 切换）", "", "", False),
    ("常规·AI 模式", "AI_PERSONA", "当前人设（人设库 id）", "text", None, False,
     "人设库 id：benmiao=内置本喵｜custom=沿用上面手搓的系统提示词｜其余为导入的人设"
     "（如 luna 露娜、michiyo 三千代）。发「/人设 列表」看全部可选；会话级切换用"
     "「/人设 用 <id>」（热生效，不用重启）", "benmiao", "benmiao", False),
    ("常规·AI 模式", "AI_TEMPERATURE", "采样温度", "float", None, False,
     "0~2，越高越发散活泼；群聊陪聊推荐 0.7（热生效）", "0.7", "", False),
    ("常规·AI 模式", "AI_MAX_TOKENS", "单次最大 token", "int", None, False,
     "限制单次回复长度；100 字中文约 150~200 token（热生效）", "512", "", False),
    ("常规·AI 模式", "AI_TIMEOUT", "调用超时(秒)", "float", None, False,
     "在线 API 通常 5~20s 出整句；超时不重试、显式提示（热生效）", "60.0", "", False),
    ("常规·AI 模式", "AI_HISTORY_TURNS", "上下文轮数", "int", None, False,
     "每个会话携带的最近对话轮数（user+assistant 各算 1 条）（热生效）", "10", "", False),
    ("常规·AI 模式", "AI_HISTORY_TTL", "上下文 TTL(秒)", "int", None, False,
     "会话历史超时自动清，避免无限增长（热生效）", "1800", "", False),
    ("常规·AI 模式", "AI_COOLDOWN", "同用户冷却(秒)", "float", None, False,
     "同一用户两次 AI 回复最小间隔，防群聊刷屏打爆接口与额度（热生效）", "3.0", "", False),
    ("常规·AI 模式", "AI_MAX_REPLY_LEN", "回复最大字数", "int", None, False,
     "超长截断加省略后缀（QQ 单条消息上限 + 防刷屏）（热生效）", "800", "", False),
    ("常规·AI 模式", "AI_MAX_RETRIES", "网络重试次数", "int", None, False,
     "仅对连接失败 / 429 / 5xx 指数退避重试（1s/2s+抖动，总尝试封顶 3 次）；4xx 与超时不重试（热生效）",
     "2", "", False),
    # ---- 智能表情包 + 分段连发（2026-10-19）----
    ("常规·AI 模式", "AI_STICKER_SMART", "智能表情包", "bool", None, False,
     "开=按人设语境选表情包（AI 模式由大模型自主决定发不发+发什么；关闭=纯随机）（热生效）",
     "true", "", False),
    ("常规·AI 模式", "AI_STICKER_COLLECT", "自动收集群表情", "bool", None, False,
     "开=自动把群里的表情包（商城表情/带 sub_type 图/小 GIF）收进 data/stickers/collected 为己用；"
     "普通照片不收（热生效）", "true", "", False),
    ("常规·AI 模式", "AI_STICKER_MAX", "表情库容量上限", "int", None, False,
     "收集库超过此数量滚动淘汰最旧最少用的；内置库不受限（热生效）", "300", "", False),
    ("常规·AI 模式", "AI_STICKER_MAP", "人设情绪→表情映射", "textarea", None, False,
     "格式：`情绪=分类1,分类2;情绪=分类`（分号或换行分隔）。本喵默认：生气=阴阳怪气,无语"
     "（腹黑人设生气不发怒图发阴阳怪气图）。可用分类：开心/搞笑/愤怒/无语/阴阳怪气/安慰/庆祝/疑问/爱心/劳累/通用（热生效）",
     "", "", False),
    ("常规·AI 模式", "AI_STICKER_VLM", "VLM 视觉分类表情包", "bool", None, False,
     "开=调视觉大模型给收集的表情包精分类（模型需支持图片输入，如 doubao-vision；"
     "会产生少量调用费用）；关=只用本地 OCR 读字分类，未分类的归「通用」（热生效）", "false", "", False),
    ("常规·AI 模式", "AI_STICKER_VLM_MODEL", "视觉分类模型", "text", None, False,
     "VLM 视觉分类用的模型名（留空=跟 AI 模型一致）。须支持图片输入，否则分类调用会 400 并记日志（热生效）",
     "", "", False),
    # ---- 连发 + 错别字（2026-10-19：MaiMBot 真源码搬运）----
    ("常规·AI 模式", "AI_BURST_ENABLE", "连发模式", "bool", None, False,
     "开=回复走「错别字注入+概率拆条」管线，偶尔拆成几条连着发（像真人）；关=单条直发（热生效）",
     "true", "", False),
    ("常规·AI 模式", "AI_BURST_CHANCE", "连发意愿", "float", None, False,
     "0~1，进入拆条管线的概率（MaiMBot 原版无此闸门，全靠拆条器自身概率；"
     "这是按「偶尔连发」加的意识闸，默认 0.5）（热生效）", "0.5", "", False),
    ("常规·AI 模式", "AI_BURST_MAX_PARTS", "连发最多几条", "int", None, False,
     "单次连发最大气泡数，超出合并尾部（MaiMBot 原版 >4 直接放弃花哨）（热生效）",
     "4", "", False),
    ("常规·AI 模式", "AI_TYPING_CN_TIME", "中文打字速度(秒/字)", "float", None, False,
     "每条气泡发送前按长度「打字」：中文每秒打几个字的倒数（MaiMBot 线上 0.2）（热生效）",
     "0.2", "", False),
    ("常规·AI 模式", "AI_TYPING_EN_TIME", "英文打字速度(秒/字)", "float", None, False,
     "非中文字符的打字计时（MaiMBot 线上 0.1）（热生效）", "0.1", "", False),
    ("常规·AI 模式", "AI_TYPING_MAX", "单条打字延迟封顶(秒)", "float", None, False,
     "再长的气泡也只「打」这么久（MaiMBot 线上封顶 10s，防长回复干等）（热生效）",
     "10.0", "", False),
    ("常规·AI 模式", "AI_BURST_STICKER_CHANCE", "连发附表情包概率", "float", None, False,
     "0~1，连发时在文字流末尾附一张表情包的概率——表情包是流里独立一条"
     "（MaiMBot 的 is_emoji 队列消息同款行为）（热生效）", "0.35", "", False),
    ("常规·AI 模式", "AI_TYPO_ENABLE", "错别字注入", "bool", None, False,
     "开=回复按 MaiMBot 同款算法注入少量同音错别字（更像真人手误）；关=字字正确（热生效）",
     "true", "", False),
    ("常规·AI 模式", "AI_TYPO_ERROR_RATE", "错别字·单字替换率", "float", None, False,
     "单字同音替换概率（MaiMBot 线上 0.03，很低调不跳戏）（热生效）", "0.03", "", False),
    ("常规·AI 模式", "AI_TYPO_TONE_RATE", "错别字·声调错误率", "float", None, False,
     "同音字里选错声调的比例（MaiMBot 线上 0.2）（热生效）", "0.2", "", False),
    ("常规·AI 模式", "AI_TYPO_WORD_RATE", "错别字·整词替换率", "float", None, False,
     "整词替换成同音词的概率（MaiMBot 线上 0.02，极低频出梗）（热生效）",
     "0.02", "", False),
    # ---- 主动发言（2026-10-19）----
    ("常规·AI 模式", "AI_PROACTIVE_ENABLE", "AI 主动发言", "bool", None, False,
     "开=AI 模式群里安静下来时会自己起话头/跟进话题（像个活群友）；关=只回复不主动（热生效）",
     "true", "", False),
    ("常规·AI 模式", "AI_PROACTIVE_MIN_INTERVAL", "主动发言最小间隔(秒)", "int", None, False,
     "同一群两次主动发言的最小间隔（热生效）", "1800", "", False),
    ("常规·AI 模式", "AI_PROACTIVE_IDLE_FACTOR", "沉默阈值系数", "float", None, False,
     "沉默阈值=群平均发言间隔×此系数（自适应：热闹群等更久，冷清群更快暖场）（热生效）",
     "6.0", "", False),
    ("常规·AI 模式", "AI_PROACTIVE_IDLE_MIN", "沉默阈值下限(秒)", "int", None, False,
     "再热闹的群至少安静这么久才考虑主动（热生效）", "300", "", False),
    ("常规·AI 模式", "AI_PROACTIVE_IDLE_MAX", "沉默阈值上限(秒)", "int", None, False,
     "再冷清的群也不会等超过这么久（热生效）", "7200", "", False),
    ("常规·AI 模式", "AI_PROACTIVE_DAILY_MAX", "每日主动上限(次)", "int", None, False,
     "每群每天最多主动发言几次（防刷屏）（热生效）", "2", "", False),
    ("常规·AI 模式", "AI_PROACTIVE_HOURS", "活跃时段", "text", None, False,
     "只在这个时段内主动发言，格式「起-止」24小时制，支持跨零点如 22-2；凌晨不打扰（热生效）",
     "8-23", "8-23", False),
    ("常规·AI 模式", "AI_PROACTIVE_CHANCE", "主动意愿", "float", None, False,
     "0~1，基础主动发言概率（再乘社交能量系数——说太多会累，累了就安静）（热生效）",
     "0.6", "", False),
    ("常规·AI 模式", "AI_PROACTIVE_ENERGY_COST", "每次主动耗能", "float", None, False,
     "每次主动发言消耗的社交能量（恢复 2 点/分钟），形成「话多→累了→安静→恢复」周期（热生效）",
     "25.0", "", False),
    ("常规·AI 模式", "AI_PROACTIVE_GREET", "早午晚安问候", "bool", None, False,
     "开=每天早/午/晚在 AI 模式群里问候一次（仅群里安静超 10 分钟才发，不打断聊天）（热生效）",
     "true", "", False),
    # ---- 工具总线（2026-10-20 M2：Function Calling + 提醒定时任务）----
    ("工具总线", "AI_TOOLS_ENABLE", "AI 工具总线总开关", "bool", None, False,
     "开=AI 对话时大模型可调用已注册工具（提醒/队列查询/宿主机概览等）；"
     "关=退回纯文字对话。当前模型不支持 Function Calling 时报错会如实提示关本开关或换模型，"
     "绝不偷偷剥掉 tools 重试（禁止兜底）（热生效）",
     "true", "", False),
    ("工具总线", "AI_FC_MAX_ROUNDS", "单次对话最大工具回合", "int", None, False,
     "一次处理里 LLM 回合上限（含工具结果回填后的续问）；耗尽显式告知用户分步说，不硬撑（热生效）",
     "8", "", False),
    ("工具总线", "AI_TOOL_TIMEOUT", "单工具执行超时(秒)", "float", None, False,
     "单个工具执行的显式超时；超时即失败并告知模型/用户，不自动重试（热生效）",
     "30.0", "", False),
    ("工具总线", "AI_TOOL_RESULT_MAX_CHARS", "工具结果截断(字)", "int", None, False,
     "单个工具结果写回上下文的字符上限，超长截断（防打爆上下文窗口）（热生效）",
     "6000", "", False),
    ("工具总线", "AI_TOOL_DAILY_BUDGET", "每会话每日工具上限", "int", None, False,
     "每个群/私聊每日工具调用总上限；0=不限。超限显式告知（限流不是功能降级）（热生效）",
     "0", "", False),
    ("工具总线", "REMIND_TICK", "提醒调度间隔(秒)", "float", None, False,
     "提醒到点扫描间隔；1 秒兼顾精度与空转开销（重启 bridge 生效）",
     "1.0", "", False),
    ("工具总线", "REMIND_EXPIRE_SECONDS", "提醒过期时间(秒)", "int", None, False,
     "到点后超过这么久才被发现（进程休眠/停机）→ 标记过期不补发——迟到几分钟的提醒比没有更烦（热生效）",
     "300", "", False),
    ("工具总线", "REMIND_MAX_PER_SCOPE", "每会话提醒上限(条)", "int", None, False,
     "每个群/私聊同时持有的待触发提醒数量上限，防刷屏（热生效）",
     "20", "", False),
    # ---- 宿主机管家（2026-10-20 M6）----
    ("宿主机管家", "PC_ENABLE", "宿主机管家总开关", "bool", None, False,
     "开=开放 /pc 命令族与自然语言直通车（电脑咋样/截个图/硬件温度）；关=全部静默（热生效）",
     "true", "", False),
    ("宿主机管家", "PC_OWNER_ONLY", "仅主人可用", "bool", None, False,
     "**安全硬规则**：开=仅私聊+主人（推荐，群聊调用会泄露主人硬件配置）；"
     "关=私聊白名单 QQ 也可用查询类，高危操作仍限主人（热生效）", "true", "", False),
    ("宿主机管家", "PC_KILL_CONFIRM", "高危操作二次确认", "bool", None, False,
     "开=kill/删除/电源类操作需回复「确认」才执行（30 秒有效）；关=直接执行，不推荐（热生效）",
     "true", "", False),
    ("宿主机管家", "PC_DISK_ROOTS", "目录浏览白名单", "text", None, False,
     "/pc disk 可浏览的目录（分号分隔，防遍历整盘），如 D:\\AI;D:\\Downloads（热生效）",
     r"D:\AI", r"D:\AI", False),
    ("宿主机管家", "PC_SHOT_MAX_MB", "截屏回传上限(MB)", "int", None, False,
     "截图经 base64 回传的大小上限（NapCat WS 单帧 16MB，base64 实传建议 ≤8）（热生效）",
     "8", "", False),
    ("宿主机管家", "PC_FILE_MAX_MB", "文件互传上限(MB)", "int", None, False,
     "QQ→宿主 单文件落盘上限（超限拒绝，防撑爆）；PC→QQ 经本地路径直发不受 16MB WS 帧限制，但仍受此上限（热生效）",
     "100", "", False),
    ("宿主机管家", "PC_FILE_UPLOAD_DIR", "QQ文件落盘目录", "text", None, False,
     "QQ 发来的文件保存到此目录；留空=项目 data/pc_inbox（始终落在项目内，不散到系统盘）；需为 PC_DISK_ROOTS 内或独立目录（热生效）",
     "", "", False),
    ("宿主机管家", "PC_SCREENSHOT_METHOD", "截图方法", "select", "dxgi|mss", False,
     "截图引擎：dxgi=DXGI Desktop Duplication（捕获桌面合成器输出，GPU 全屏内容不再黑屏，本机唯一推荐）；"
     "mss=传统 mss（已知 GPU 全屏内容会黑屏，仅在 DXGI 不可用且明确需要时选用）。"
     "WGC 在本机 Win10 21H2 因系统限制不可用，故统一走 DXGI。选中方法失败即如实报错，无降级/回退（热生效）",
     "dxgi", "dxgi", False),
    # ---- 下载媒体中心（2026-10-20 M7）----
    ("下载媒体", "DL_ENABLE", "下载媒体中心总开关", "bool", None, False,
     "开=开放 /dl 命令族与 FC 下载工具（aria2/yt-dlp/订阅）；关=全部静默（热生效）",
     "true", "", False),
    ("下载媒体", "DL_ARIA2_RPC", "aria2 RPC 端点", "text", None, False,
     "本机 aria2 的 JSON-RPC 地址（默认 http）。改了 aria2 --rpc-listen-port 就同步改这里（热生效）",
     "http://127.0.0.1:6800/jsonrpc", "http://127.0.0.1:6800/jsonrpc", False),
    ("下载媒体", "DL_ARIA2_SECRET", "aria2 RPC 密钥", "secret", None, False,
     "与 aria2 启动参数 --rpc-secret 一致；aria2 没设 secret 就留空。禁落日志（热生效）",
     "", "", False),
    ("下载媒体", "DL_ARIA2_TIMEOUT", "aria2 RPC 超时(秒)", "float", None, False,
     "单次 RPC 调用超时；超时即失败并如实告知，不自动重试超过 3 次（热生效）",
     "15.0", "", False),
    ("下载媒体", "DL_DIR", "下载目录", "text", None, False,
     "aria2 与 yt-dlp 的下载落地目录；「/dl 发我」也从这里取文件回传。建议加入 PC_DISK_ROOTS 白名单（热生效）",
     r"D:\Downloads", r"D:\Downloads", False),
    ("下载媒体", "DL_BT_SELECT", "BT 选文件策略", "select", "largest|smart|all", False,
     "磁力/种子智能选文件（DownloadBot 范式）：largest=只选最大文件（观影场景，过滤广告样品）；"
     "smart=选≥平均体积×0.8 的文件；all=全选（显式选择）。选中方法拿不到文件大小时如实说明并全选（热生效）",
     "largest", "largest", False),
    ("下载媒体", "DL_BT_META_TIMEOUT", "BT 元数据等待(秒)", "float", None, False,
     "磁力任务等待元数据（文件列表）的上限；超时显式失败，不无限等（热生效）",
     "180.0", "", False),
    ("下载媒体", "DL_YTDLP_FORMAT", "yt-dlp 格式", "text", None, False,
     "yt-dlp 格式选择表达式；默认 bv*+ba/b（最佳视频+最佳音轨合并，无音轨回落单文件）。合并需 ffmpeg（热生效）",
     "bv*+ba/b", "bv*+ba/b", False),
    ("下载媒体", "DL_YTDLP_TIMEOUT", "yt-dlp 超时(秒)", "float", None, False,
     "yt-dlp socket 超时（解析/下载共用）；超时显式告知（热生效）",
     "120.0", "", False),
    ("下载媒体", "DL_POLL_INTERVAL", "完成检测间隔(秒)", "float", None, False,
     "轮询 aria2 tellStopped 检测下载完成的间隔；本地服务 5s 足够，无需 WS 长连接（热生效）",
     "5.0", "", False),
    ("下载媒体", "DL_NOTIFY", "完成自动通知", "bool", None, False,
     "开=下载完成自动推送到发起会话（大文件发本地链接、小文件直发）；关=只登记不推送（热生效）",
     "true", "", False),
    ("下载媒体", "DL_SUB_ENABLE", "订阅监控开关", "bool", None, False,
     "开=按间隔检查订阅源（B站收藏夹/UP、抖音作品等经 yt-dlp flat playlist）更新并自动下载（热生效）",
     "true", "", False),
    ("下载媒体", "DL_SUB_INTERVAL", "订阅检查间隔(秒)", "float", None, False,
     "每个订阅的检查周期，默认 1 小时；yt-dlp 请求有成本，部分平台频访会风控（热生效）",
     "3600.0", "", False),
    ("下载媒体", "DL_SUB_AUTO_DOWNLOAD", "订阅更新自动下载", "bool", None, False,
     "开=订阅发现新条目就自动下载；关=只在会话里通知「有新条目」不自动下（热生效）",
     "true", "", False),
    ("下载媒体", "DL_SUB_MAX_PER_SCOPE", "每会话订阅上限(个)", "int", None, False,
     "每个群/私聊最多几个订阅源（防刷屏）（热生效）",
     "5", "", False),
    # ---- 运维面与通知中枢（2026-10-20 M9）----
    ("运维面", "OPS_ENABLE", "运维面总开关", "bool", None, False,
     "开=开放 /ops 命令族（git 状态/SSH 白名单执行/WoL 唤醒/frp 状态）与通知中枢；关=全部静默（热生效）",
     "true", "", False),
    ("运维面", "OPS_GIT_REPO", "开发机仓库路径", "text", None, False,
     "git 状态查询的目标仓库（默认 manju_agent）。只跑只读子命令白名单（status/diff/log/branch…），"
     "写操作一律拒绝（热生效）",
     r"D:\AI\manju_agent", r"D:\AI\manju_agent", False),
    ("运维面", "OPS_WATCH_ENABLE", "仓库巡检开关", "bool", None, False,
     "开=定时巡检仓库（脏改动/未推送从无到有才推通知——没变化静默，防刷屏）（重启生效）",
     "true", "", False),
    ("运维面", "OPS_WATCH_INTERVAL", "巡检间隔(秒)", "float", None, False,
     "每轮巡检间隔，默认 30 分钟（个人开发机够用；LLM 转述有成本）（重启生效）",
     "1800.0", "", False),
    ("运维面", "OPS_WATCH_CHECKS", "巡检自检命令", "text", None, False,
     "分号分隔的只读/无副作用命令（如 `python -m pytest tests/ -q`）；退出码非零 → 通知中枢告警。"
     "**严禁配写操作命令**（重启生效）",
     "", "", False),
    ("运维面", "OPS_WATCH_CHECK_TIMEOUT", "自检命令超时(秒)", "float", None, False,
     "单条自检命令显式超时（CLOSE-05）；超时按失败告警，绝不挂死巡检循环（重启生效）",
     "120.0", "", False),
    ("运维面", "OPS_SSH_HOSTS", "SSH 主机白名单", "text", None, False,
     "`别名:user@host:port` 分号分隔，如 `nas:admin@192.168.1.5:22`。只连这里的主机，拒绝任意地址（热生效）",
     "", "nas:admin@192.168.1.5:22", False),
    ("运维面", "OPS_SSH_CMD_WHITELIST", "SSH 命令白名单", "text", None, False,
     "分号分隔的命令前缀（如 `df -h;systemctl status;docker ps`）；空=默认只读运维表。"
     "命中才执行，另有一层危险模式双保险（rm/poweroff/git push 等直接拒）（热生效）",
     "", "df -h;systemctl status;docker ps", False),
    ("运维面", "OPS_SSH_KEY", "SSH 私钥路径", "text", None, False,
     "paramiko 用的私钥文件（空=SSH agent/默认 ~/.ssh）。不支持密码鉴权——密码禁落配置（LOG-04）（热生效）",
     "", "", False),
    ("运维面", "OPS_SSH_TIMEOUT", "SSH 超时(秒)", "float", None, False,
     "远程命令执行超时；超时显式失败，不重试（远程执行重试=重复副作用，禁止）（热生效）",
     "30.0", "", False),
    ("运维面", "OPS_WOL_HOSTS", "WoL 唤醒目标", "text", None, False,
     "`别名:MAC[:广播地址[:端口]]` 分号分隔，如 `客厅:AA:BB:CC:DD:EE:FF;书房:11:22:33:44:55:66:192.168.1.255`。"
     "跨网段唤醒要把广播地址改成目标子网的（热生效）",
     "", "客厅:AA:BB:CC:DD:EE:FF", False),
    ("运维面", "OPS_FRP_ADMIN", "frp admin API", "text", None, False,
     "frpc 的 admin 地址（frpc.toml 的 adminAddr:adminPort，默认 http://127.0.0.1:7400）。"
     "没配/不通时诚实告知缺什么，不返回假在线状态（热生效）",
     "", "http://127.0.0.1:7400", False),
    ("运维面", "OPS_FRP_TIMEOUT", "frp 查询超时(秒)", "float", None, False,
     "frpc admin API 查询显式超时（CLOSE-05）；超时如实报错不返回假在线（热生效）",
     "10.0", "", False),
    ("运维面", "OPS_NOTIFY_ENABLE", "通知中枢开关", "bool", None, False,
     "开=机器事件（仓库脏改动/自检失败等）经 LLM 人格化转述推送到目标；关=只记日志（热生效）",
     "true", "", False),
    ("运维面", "OPS_NOTIFY_TARGET", "通知推送目标", "text", None, False,
     "QQ 号=私聊主人 / 群号=群推送；空=只记日志不发。**强烈建议私聊**（群推送需谨慎）（热生效）",
     "", "123456789", False),
    ("运维面", "OPS_NOTIFY_MIN_LEVEL", "最低推送级别", "select", "info|warn|error", False,
     "info=全部推送；warn=只推警告以上；error=只推错误。低于该级别的事件只记日志（热生效）",
     "info", "info", False),
    ("运维面", "OPS_NOTIFY_COOLDOWN", "同类事件冷却(秒)", "float", None, False,
     "同kind+title 的事件冷却期内合并计数、到点带计数补发——5 分钟一条才是人话（热生效）",
     "300.0", "", False),
    # ---- 智能家居中枢（2026-10-21 M8，HAQBot 同构）----
    ("智能家居", "SMARTHOME_ENABLE", "智能家居总开关", "bool", None, False,
     "开=开放 /ha 命令族（控设备/场景/查状态/看摄像头）与 webhook 反向告警；关=全部静默（热生效）",
     "false", "", False),
    ("智能家居", "SMARTHOME_HA_URL", "Home Assistant 地址", "text", None, False,
     "HA 的 REST 地址，如 http://192.168.1.10:8123（热生效）",
     "", "http://homeassistant.local:8123", False),
    ("智能家居", "SMARTHOME_HA_TOKEN", "HA 长效访问令牌", "secret", None, False,
     "HA 个人资料页 → Long-Lived Access Tokens 生成（LOG-04 禁入日志）（热生效）",
     "", "", False),
    ("智能家居", "SMARTHOME_HA_AGENT_ID", "HA 对话代理 ID", "text", None, False,
     "自然语言控设备走的 conversation 代理（HA 侧配 Ollama 本地模型驱动；机器人不自带 LLM）（热生效）",
     "conversation.home_assistant", "conversation.home_assistant", False),
    ("智能家居", "SMARTHOME_DEVICE_WHITELIST", "可控设备白名单", "text", None, False,
     "分号/逗号分隔的 entity_id 白名单（light.living;switch.fan）。空=全禁——白名单外一律拒绝，"
     "HA 侧配错也拦得住（热生效）",
     "", "light.living_room;switch.fan;script.bath_mode", False),
    ("智能家居", "SMARTHOME_SENSITIVE", "高敏设备(仅主人)", "text", None, False,
     "门锁/摄像头/门禁等高敏 entity_id（分号分隔）——命中后仅主人可操控，群友一律拒（热生效）",
     "", "lock.front_door;camera.living", False),
    ("智能家居", "SMARTHOME_SCENE_MAP", "场景脚本映射", "textarea", None, False,
     "每行一条「中文口语=script.entity_id」（如 洗澡模式=script.bath_mode）——命中即直接执行，"
     "不等 LLM，快且确定性高（热生效）",
     "", "洗澡模式=script.bath_mode\n睡眠模式=script.sleep_mode", False),
    ("智能家居", "SMARTHOME_WEBHOOK_PORT", "告警接收端口", "int", None, False,
     "HA 自动化 rest_command  POST 本机该端口触发 QQ 群告警（摄像头事件附画面）（重启生效）",
     "8090", "", False),
    ("智能家居", "SMARTHOME_WEBHOOK_TOKEN", "告警校验令牌", "secret", None, False,
     "HA 侧 webhook 调用须带相同 token（?token= 或 x-webhook-token 头）——防局域网扫描乱触发（热生效）",
     "", "", False),
    ("智能家居", "SMARTHOME_WEBHOOK_HOST", "告警监听地址", "text", None, False,
     "接收器监听地址：HA 在局域网另一台机器上必须 0.0.0.0（默认）；HA 与机器人同机可收紧到 127.0.0.1。"
     "**监听 0.0.0.0 时务必配告警校验令牌**（重启生效）",
     "0.0.0.0", "127.0.0.1", False),
    ("智能家居", "SMARTHOME_HTTP_TIMEOUT", "HA 调用超时(秒)", "float", None, False,
     "HA REST 调用显式超时（CLOSE-05）；超时=显式报错，不静默（热生效）",
     "10.0", "", False),
    ("智能家居", "SMARTHOME_ALERT_COOLDOWN", "同类告警冷却(秒)", "float", None, False,
     "同设备+同事件类型的告警冷却期内合并计数（人体感应/门磁会连发）——0=每条都推（热生效）",
     "60.0", "", False),
    # ---- 记忆系统（2026-10-20 M1）----
    ("记忆系统", "MEMORY_ENABLE", "记忆系统总开关", "bool", None, False,
     "开=AI 对话前注入相关记忆（画像/长期事件/群档案）、对话后异步抽取记忆；关=纯对话零记忆（热生效）",
     "true", "", False),
    ("记忆系统", "MEMORY_DB", "记忆库路径", "text", None, False,
     "SQLite 记忆库位置；空=默认 data/qq_memory.db（与对话历史同目录）（重启生效）",
     "", "", False),
    ("记忆系统", "MEMORY_EXTRACT", "对话后异步抽取", "bool", None, False,
     "开=每轮 AI 对话后在后台抽记忆（关于用户的事实/值得记的事件）；抽取失败只记日志，不阻对话（热生效）",
     "true", "", False),
    ("记忆系统", "MEMORY_RECALL_LIMIT", "记忆注入条数", "int", None, False,
     "每次对话注入 system 提示的相关记忆条数（FTS 检索 + 时间衰减取 TopN）（热生效）",
     "5", "", False),
    ("记忆系统", "MEMORY_DECAY_DAYS", "记忆衰减半衰期(天)", "float", None, False,
     "时间衰减半衰期：越老的记忆检索权重越低（30 天前的记忆权重减半）（热生效）",
     "30.0", "", False),
    ("记忆系统", "MEMORY_KEEP_DAYS", "记忆保留(天)", "float", None, False,
     "超过这个天数的记忆滚动清理（软删，可恢复）；另有总数上限 5000 条防膨胀（OPS-02）（热生效）",
     "180.0", "", False),
    # ---- 情感 + 日程 + 关系（2026-10-20 M3）----
    ("情感关系", "EMO_ENABLE", "情感系统总开关", "bool", None, False,
     "开=对话注入「当前情绪+对这个人的亲疏+他的安排」；关=纯对话无情感维度（热生效）",
     "true", "", False),
    ("情感关系", "EMOTION_DB", "情感库路径", "text", None, False,
     "SQLite 情感库位置；空=默认 data/qq_emotion.db（重启生效）",
     "", "", False),
    ("情感关系", "EMO_DECAY_HALFLIFE", "情感衰减半衰期(秒)", "float", None, False,
     "情绪多久衰减一半回到平静（valence→0、arousal→0.5）。默认 600 秒——聊完一会儿就平静下来（热生效）",
     "600.0", "", False),
    ("情感关系", "EMO_FAVOR_ENABLE", "好感度开关", "bool", None, False,
     "开=记录对每个用户的亲疏并注入提示（语气由模型决定，代码不硬编码「对谁说什么」）（热生效）",
     "true", "", False),
    ("情感关系", "EMO_FAVOR_STEP", "单轮好感度上限", "int", None, False,
     "一次对话最多加减多少好感度（防暴涨暴跌）（热生效）",
     "5", "", False),
    ("情感关系", "EMO_SCHEDULE_EXTRACT", "日程抽取开关", "bool", None, False,
     "开=从对话里记下用户的未来安排（面试/考试/出差…），到点主动关心一句（热生效）",
     "true", "", False),
    ("情感关系", "EMO_SCHEDULE_WINDOW", "到期关心提前量(秒)", "float", None, False,
     "提前多久算「快到了」并关心一句（默认 1 小时）（热生效）",
     "3600.0", "", False),
    ("情感关系", "EMOTION_SCHEDULE_KEEP_DAYS", "日程保留(天)", "float", None, False,
     "已完成/过期的日程超此天数滚动清理（OPS-02）（热生效）",
     "30.0", "", False),
    ("情感关系", "EMO_AUTO_JUDGE", "情感自动判定", "bool", None, False,
     "开=每轮复用 AI 自表达的 [表情:分类] marker（免费信号）自动更新本喵心情；不认识的分类静默跳过（不伪造）。关=只在 /情感 手动调（热生效）",
     "true", "", False),
    # ---- 多模态 + 语音（2026-10-20 M4）----
    ("多模态语音", "MM_ENABLE", "多模态总开关", "bool", None, False,
     "开=开放 /看图 命令族与图片/语音消息处理；关=图片语音一律不处理（热生效）",
     "true", "", False),
    ("多模态语音", "AI_VISION_MODEL", "视觉模型", "text", None, False,
     "图理解/视频抽帧用的模型（必须支持图片输入，如 gpt-4o-mini、qwen-vl-max）。"
     "**不配就不能看图**——文本模型看不了图，本喵不拿它凑（热生效）",
     "", "gpt-4o-mini", False),
    ("多模态语音", "AI_VISION_FRAMES", "视频抽帧数", "int", None, False,
     "视频理解抽几帧（1~6，ffmpeg 抽帧后逐帧送视觉模型）（热生效）",
     "3", "", False),
    ("多模态语音", "MM_AUTO_LOOK", "收图自动看图", "bool", None, False,
     "开=收到图片消息自动调用视觉模型理解并回复描述；关=只在 /看图 时看（热生效）",
     "true", "", False),
    ("多模态语音", "AI_ASR_MODEL", "语音识别模型", "text", None, False,
     "Whisper 兼容模型名（如 whisper-1）。**不配就听不懂语音**——本机没装本地 ASR，"
     "不凭空听（热生效）",
     "", "whisper-1", False),
    ("多模态语音", "AI_ASR_TIMEOUT", "语音识别超时(秒)", "float", None, False,
     "ASR 请求超时；超时显式失败不重试（热生效）",
     "60.0", "", False),
    ("多模态语音", "AI_TTS_MODEL", "语音合成模型", "text", None, False,
     "OpenAI TTS 兼容模型名（如 tts-1）。**不配就不会说话**——本机没装本地 TTS（热生效）",
     "", "tts-1", False),
    ("多模态语音", "AI_TTS_VOICE", "合成音色", "text", None, False,
     "随服务商支持的音色名（alloy / nova / shimmer …）（热生效）",
     "alloy", "", False),
    ("多模态语音", "AI_TTS_TIMEOUT", "语音合成超时(秒)", "float", None, False,
     "TTS 请求超时；超时显式失败不重试（热生效）",
     "60.0", "", False),
    ("多模态语音", "AI_TTS_MAX_CHARS", "合成字数上限", "int", None, False,
     "单次语音合成的最大字数——超了改发文字，别烧钱（热生效）",
     "500", "", False),
    ("多模态语音", "MM_ASR_PRIVATE", "主人私聊语音听写", "bool", None, False,
     "开=主人私聊发来的语音自动听写成文字并按普通消息处理；关=忽略语音（热生效）",
     "true", "", False),
    # ---- 群风格学习（2026-10-21 M5）----
    ("群风格学习", "GROUP_STYLE_ENABLE", "群风格总开关", "bool", None, False,
     "开=允许学/用群风格（群梗/口头禅/氛围注入人设）；关=不学不用（热生效）",
     "true", "", False),
    ("群风格学习", "GROUP_STYLE_AUTO_LEARN", "群内自动学习", "bool", None, False,
     "开=proactive 循环里每群每天至多自动学一次风格（受预算闸约束）；关=只手动 /风格 学（热生效）",
     "true", "", False),
    ("群风格学习", "GROUP_STYLE_MAX_MEMES", "梗上限", "int", None, False,
     "单次学习抽取的常用梗/口头禅条数上限（热生效）",
     "8", "", False),
    ("群风格学习", "GROUP_STYLE_MAX_TABOO", "忌讳上限", "int", None, False,
     "单次学习抽取的群内忌讳条数上限（热生效）",
     "5", "", False),
    ("群风格学习", "GROUP_STYLE_DB", "群风格库路径", "text", None, False,
     "留空=默认 data/qq_group_style.db（热生效）",
     "", "", False),
    # ---- AI 调用预算（2026-10-21 M5：影子·自适应）----
    ("AI 调用预算", "BUDGET_ENABLE", "预算总开关", "bool", None, False,
     "开=统计并约束每日 AI 调用（proactive/multimodal 硬闸门、reactive 永远放行只提示）；"
     "关=不限（热生效）",
     "false", "", False),
    ("AI 调用预算", "BUDGET_SHADOW", "影子模式", "bool", None, False,
     "开=只记账不限流（dry-run，观察用量而不限制）；关=真正限流（热生效）",
     "true", "", False),
    ("AI 调用预算", "BUDGET_GLOBAL_DAILY_CALLS", "全局每日上限", "int", None, False,
     "全会话每日 AI 调用总上限（0=不限）；超了非必需调用被拒（热生效）",
     "300", "", False),
    ("AI 调用预算", "BUDGET_PER_SCOPE_DAILY_CALLS", "单会话每日上限", "int", None, False,
     "单个群/私聊每日 AI 调用上限（0=不限）；超了该会话非必需调用被拒（热生效）",
     "100", "", False),
    ("AI 调用预算", "BUDGET_WARN_RATIO", "预警水位", "float", None, False,
     "用量达到上限的比例即进入 warn 水位（热生效）",
     "0.8", "", False),
    ("AI 调用预算", "BUDGET_EST_TOKEN_PER_CALL", "估算Token/次", "int", None, False,
     "每次调用计入账目的估算 token（仅做总额估算，非精确计量）（热生效）",
     "800", "", False),
    ("AI 调用预算", "BUDGET_DB", "预算库路径", "text", None, False,
     "留空=默认 data/qq_budget.db（热生效）",
     "", "", False),
    # ---- 常规·超时与风控 ----
    ("常规·超时与风控", "MANJU_HTTP_TIMEOUT", "HTTP 默认超时(秒)", "float", None, False,
     "外部调用默认超时", "30.0", "", False),
    ("常规·超时与风控", "MANJU_HTTP_TIMEOUT_IMG", "图片超时(秒)", "float", None, False,
     "生图超时", "120.0", "", False),
    ("常规·超时与风控", "MANJU_HTTP_TIMEOUT_VIDEO", "视频超时(秒)", "float", None, False,
     "生视频超时", "600.0", "", False),
    ("常规·超时与风控", "TASK_TIMEOUT", "任务超时判失败(秒)", "int", None, False,
     "【决定成败】单任务从提交到结束的最长等待时间，超过即判 failed（QQ 端会收到失败提示）。"
     "本地 ComfyUI 大模板/高清放大/批量任务建议 3600~7200；在线任务通常 600~1200 足够。"
     "注意：下发给主项目时会被夹到 ≤18000（平台 /t2i/edit 上限），填更大也按 18000 下发；"
     "「任务运行过久仅告警」是另一个参数（MONITOR_TASK_TIMEOUT），不影响成败", "600", "", False),
    ("常规·超时与风控", "POLL_INTERVAL", "轮询间隔(秒)", "float", None, False,
     "进度轮询间隔", "3.0", "", False),
    ("常规·超时与风控", "REPLY_DELAY_MIN", "回传延迟下限(秒)", "float", None, False,
     "回传消息随机延迟，模拟人工", "1.0", "", False),
    ("常规·超时与风控", "REPLY_DELAY_MAX", "回传延迟上限(秒)", "float", None, False,
     "回传消息随机延迟，模拟人工", "3.0", "", False),
    ("常规·超时与风控", "REPLY_TIMEOUT", "单条消息处理超时(秒)", "float", None, False,
     "单条消息处理超过此秒数即兜底超时提示，防 NapCat 抖动/慢调用/模型卡住时「没反应」", "60.0", "", False),
    ("常规·超时与风控", "SESSION_TTL", "会话 TTL(秒)", "int", None, False,
     "半开对话存活时长", "1800", "", False),
    ("常规·超时与风控", "CHOICE_TIMEOUT", "反问选择超时(秒)", "int", None, False,
     "生图通道反问「在线/本地」后等待用户选择的时长", "60", "", False),
    ("常规·超时与风控", "VIDEO_MAX_BYTES", "视频大小阈值(字节)", "int", None, False,
     "超过则改发下载链接 + 封面", "104857600", "", False),
    ("常规·超时与风控", "LOG_LEVEL", "日志级别", "select",
     [{"value": "DEBUG", "label": "DEBUG（最详细·调试用）"},
      {"value": "INFO", "label": "INFO（默认·日常运行）"},
      {"value": "WARNING", "label": "WARNING（仅警告与错误）"},
      {"value": "ERROR", "label": "ERROR（仅错误）"}], False,
     "日志详细程度", "INFO", "", False),
    # ---- 常规·图片渲染（2026-09-07 新增：机器人自绘菜单图/进度条图/配额图）----
    # 全部 core=false：渲染时实时读 Config 类属性 → 改完立即生效，无需重启 bridge。
    ("常规·图片渲染", "PIC_ENABLE", "自绘图片", "bool", None, False,
     "开启后 /help、/status、/quota 发排版图片；关闭则一律回落纯文本（出问题时可一键止血）", "true", "", False),
    ("常规·图片渲染", "PIC_THEME", "图片主题", "select",
     [{"value": "neon", "label": "neon（深色霓虹·青紫渐变）"},
      {"value": "light", "label": "light（极简白底·蓝色）"},
      {"value": "auto", "label": "auto（夜间深色·白天浅色）"}], False,
     "默认配色；用户可用「换深色 / 换浅色」临时切换（仅影响自己）", "neon", "", False),
    ("常规·图片渲染", "PIC_CUSTOM_THEME", "激活的自定义主题", "text", None, False,
     "当前激活的自定义主题名（主题编辑器「应用」写入并持久化到 .env，重启不丢）。留空=未激活走「图片主题」；填主题名=激活（与编辑器「应用」等价）", "", "", False),
    ("常规·图片渲染", "PIC_MAX_WIDTH", "图片宽度(px)", "int", None, False,
     "出图宽度。800 兼顾清晰度与体积（实测 < 60KB）", "800", "", False),
    ("常规·图片渲染", "PIC_CACHE_TTL", "图片缓存(秒)", "int", None, False,
     "同模板+同主题+同参数复用产物；0=不过期（改文案后请手动清或调小）", "300", "", False),
    ("常规·图片渲染", "PIC_CACHE_MAX", "缓存张数上限", "int", None, False,
     "超过按 LRU 淘汰，防止撑爆磁盘", "200", "", False),
    # ---- 常规·菜单与帮助（2026-09-13 新增：菜单分主人/一般，文案与条目可自定义）----
    # 全部 core=false：渲染时实时读 Config 类属性 → 改完立即生效，无需重启 bridge。
    # 文案留空=用内置默认（见 pic/templates/menu.py 的 DEFAULT_*）。
    ("常规·菜单与帮助", "PIC_MENU_TITLE", "菜单标题", "text", None, False,
     "帮助菜单图的大标题。留空=「本喵能干啥」", "", "本喵能干啥", False),
    ("常规·菜单与帮助", "PIC_MENU_OWNER_TITLE", "主人菜单标题", "text", None, False,
     "主人专属菜单的大标题。留空=跟随「菜单标题」", "", "", False),
    ("常规·菜单与帮助", "PIC_MENU_SUBTITLE", "菜单副标题", "text", None, False,
     "一般菜单的副标题。留空=内置默认", "", "直接说人话就行，别老打斜杠", False),
    ("常规·菜单与帮助", "PIC_MENU_FOOTER", "菜单页脚", "text", None, False,
     "一般菜单的页脚小字。留空=内置默认", "", "闲聊免开尊口，要图直说就完事儿喵~", False),
    ("常规·菜单与帮助", "PIC_MENU_OWNER_SUBTITLE", "主人菜单副标题", "text", None, False,
     "主人菜单的副标题。留空=内置默认", "", "主人菜单 · 说人话就行，别老打斜杠", False),
    ("常规·菜单与帮助", "PIC_MENU_OWNER_FOOTER", "主人菜单页脚", "text", None, False,
     "主人菜单的页脚小字。留空=内置默认", "", "这些都是主人专属活儿，别人使唤不动本喵~", False),
    ("常规·菜单与帮助", "PIC_MENU_SHOW_OWNER", "主人可见专属菜单", "bool", None, False,
     "开启：主人能看到「主人专属」那一段（主项目任务/生图监听/监控/插队/清空队列）；"
     "关闭：主人也只看到一般菜单", "true", "", False),
    ("常规·菜单与帮助", "PIC_MENU_ITEMS", "一般菜单条目(高级)", "textarea", None, False,
     "留空=用内置默认。格式：`图标|标题|描述`，多条用 `;` 分隔，分组用 `#分组名`。"
     "可用图标：circle/square/round_square/triangle/diamond/star/bolt/clock/arrow_up/"
     "arrow_right/check/cross/question/image/wand/pause", "", "wand|生图|发一句话;#任务;clock|我的任务|查进度", False),
    ("常规·菜单与帮助", "PIC_MENU_OWNER_ITEMS", "主人菜单条目(高级)", "textarea", None, False,
     "留空=用内置默认（主项目任务/生图监听/监控告警/插队/清空队列）。格式同上", "", "bolt|主项目任务|看后台在跑啥", False),
    # ---- 常规·视频解析（2026-09-13 新增：群聊/私聊自动解析平台视频链接）----
    # 全部 core=false：运行时读 Config 类属性 → 改完立即生效。
    ("常规·视频解析", "VIDEO_PARSE_ENABLED", "视频链接解析(总开关)", "bool", None, False,
     "开启后群聊/私聊出现平台视频链接（B站/小红书/快手/抖音）自动回解析卡片", "false", "", False),
    ("常规·视频解析", "VIDEO_PARSE_GROUP_AUTO", "群内自动解析", "bool", None, False,
     "群消息出现平台链接即回复卡片；关闭则群里完全不解析", "true", "", False),
    ("常规·视频解析", "VIDEO_PARSE_OWNERS", "私聊解析白名单", "strlist", None, False,
     "私聊仅这些 QQ 号触发解析（逗号分隔）；留空=所有私聊都解析", "", "", False),
    # ---- 常规·任务队列（2026-09-08 新增：QQ 侧持久有界队列）----
    # 全部 core=false：消费者实时读 Config 类属性 → 改完立即生效，无需重启 bridge。
    # 用户拍板值（2026-09-08）：QUEUE_MAX=20；平台忙时单次退避 5 分钟、共 3 次，
    # 3 次用完仍忙 → 通知用户并弹出队列。
    ("常规·任务队列", "QUEUE_MAX", "队列上限(条)", "int", None, False,
     "QQ 侧排队任务上限（有界队列）。超过则拒绝新任务并告知当前排队数，防止无限堆积", "20", "", False),
    ("常规·任务队列", "QUEUE_BUSY_BACKOFF", "平台忙退避(秒)", "float", None, False,
     "队首任务遇平台忙(409 IMG_BUSY)时，每次退避的秒数（300=5分钟）。退避期间**不弹出队**，保持队首位置", "300.0", "", False),
    ("常规·任务队列", "QUEUE_BUSY_RETRY", "平台忙重试次数", "int", None, False,
     "退避重试次数上限。用完后仍忙 → 通知用户「平台持续繁忙」并弹出队列（3 次 × 5 分钟 = 15 分钟）", "3", "", False),
    ("常规·任务队列", "QUEUE_POLL_INTERVAL", "队列轮询(秒)", "float", None, False,
     "队空时消费者检查新任务的间隔", "2.0", "", False),
    ("常规·任务队列", "QUEUE_IDEMPOTENT_TTL", "幂等窗口(秒)", "int", None, False,
     "同一用户+同一提示词+同一参数在此窗口内重复下发 → 返回原任务，不入第二条（防手抖重复扣额度）；0=关闭", "60", "", False),
    # ---- 监控告警（默认全关，避免打扰；仅主人可用）----
    ("常规·任务队列", "MONITOR_ENABLE", "监控告警", "bool", None, False,
     "开启后任务失败/超时/平台持续繁忙时主动私聊告警给主人（默认关闭，避免打扰）", "false", "", False),
    ("常规·任务队列", "MONITOR_INTERVAL", "监控轮询(秒)", "float", None, False,
     "检查活跃任务 / 新失败任务的间隔", "30.0", "", False),
    ("常规·任务队列", "MONITOR_TASK_TIMEOUT", "任务运行过久仅告警(秒)", "int", None, False,
     "【仅告警】单个任务超过此秒数仍未完成 → 仅私聊告警主人，不会把任务标为失败。"
     "想延长真正超时请改「常规·超时与风控」里的「任务超时判失败(秒)」", "600", "", False),
    ("常规·任务队列", "MONITOR_BUSY_THRESHOLD", "连续繁忙告警阈值", "int", None, False,
     "连续 N 次遇到平台忙(IMG_BUSY) → 告警「平台持续繁忙」", "3", "", False),
    # ---- 监听主项目生图任务（2026-09-13：从推理监控页 /tasks 获取，QQ 直接看后台在画啥）----
    ("常规·任务队列", "TASK_MONITOR_BASE", "推理监控API地址", "text", None, False,
     "主项目「推理监控」页面所用的统一任务 API 根地址（/tasks/unified?type=image）。"
     "默认 8099 代理，与页面同源；也可填 7788 的 /api/v1 直连", "http://127.0.0.1:8099/api/v1", "", False),
    ("常规·任务队列", "TASK_LISTEN_ENABLE", "监听主项目生图", "bool", None, False,
     "开启后主项目开始生图/生图完成时私聊提醒主人（含提示词，完成补发结果图）；默认关闭避免刷屏（仅主人可开）", "false", "", False),
    ("常规·任务队列", "TASK_LISTEN_INTERVAL", "监听轮询(秒)", "float", None, False,
     "检查主项目生图任务变化的间隔", "15.0", "", False),
    ("常规·任务队列", "TASK_LISTEN_LIMIT", "查询展示条数", "int", None, False,
     "「主项目任务」查询最多展示多少条（避免刷屏）", "10", "", False),
    # ---- QQ 官方机器人通道（2026-09-12 新增：双轨并行，与 NapCat 互不干扰）----
    # 说明：ENABLE / APPID / SECRET 三项在 nonebot.init() 时读取，**改完必须重启 bridge**；
    #      其余项运行时读 Config 类属性，热生效。
    ("官方机器人", "OFFICIAL_ENABLE", "启用官方通道", "bool", None, True,
     "开启后同一进程内同时跑 NapCat 与官方机器人。关闭时 NapCat 零影响（重启后生效）", "false", "", False),
    ("官方机器人", "OFFICIAL_APPID", "AppID", "text", None, True,
     "QQ 开放平台创建机器人后获得；与 Secret 同时填写才会注册官方适配器（重启后生效）", "", "1020xxxxx", False),
    ("官方机器人", "OFFICIAL_SECRET", "AppSecret", "text", None, True,
     "机器人密钥。仅写入 .env，**页面不回显原值**（重启后生效）", "", "", True),
    ("官方机器人", "OFFICIAL_SANDBOX", "沙箱环境", "bool", None, True,
     "沙箱不受消息频控（官方 FAQ Q19），联调阶段建议开启（重启后生效）", "true", "", False),
    ("官方机器人", "OFFICIAL_USE_WEBSOCKET", "WebSocket 接入", "bool", None, True,
     "开启=bridge 主动连官方网关，不需要公网服务器；关闭=Webhook 需 HTTPS 公网回调", "true", "", False),
    ("官方机器人", "OFFICIAL_INTENT", "事件订阅 Intent", "textarea", None, True,
     "JSON 格式。c2c_group_at_messages=群@与私聊消息；group_members=群成员变更（重启后生效）",
     '{"c2c_group_at_messages": true, "group_members": true}', "", False),
    ("官方机器人", "OFFICIAL_DAILY_GROUP_LIMIT", "单群每日主动消息上限", "int", None, False,
     "平台硬限 1000 条/群/天，这里自限留余量；超限自动降级为提示，避免撞 40034100", "900", "", False),
    ("官方机器人", "OFFICIAL_PASSIVE_MAX_AGE", "被动回复最长等待(秒)", "int", None, False,
     "超过则改走主动消息（平台硬限：被动回复 5 分钟内有效、每条最多回 5 次）", "240", "", False),
]


def _key_to_meta() -> dict:
    meta = {}
    for group, key, label, typ, options, core, desc, default, placeholder, secret in SCHEMA:
        meta[key] = {
            "group": group, "label": label, "type": typ, "options": options,
            "core": core, "desc": desc, "default": default, "placeholder": placeholder,
            "secret": secret,
        }
    return meta


_META = _key_to_meta()


def _fetch_template_options() -> list:
    """从主项目 /api/v1/t2i/templates 拉模板列表，给 LOCAL_TEMPLATE_ID 注入下拉选项。

    设计要点：
      - 仅在管理页请求时同步拉一次（4s 超时，失败容错），不阻塞 schema 主流程；
      - 返回 [{value, label}] 格式，label = "id — name  [category]"，
        便于前端 select 同时展示 id 与名称，用户一眼能看出选了哪个；
      - scene=t2i 过滤：QQ 桥本地生图通道 = 文生图流程，不取含 LoadImage 的 i2i 模板；
      - 主项目未启动 / 接口异常 → 返回空 list（前端 select 退化为「(主项目模板不可达)」提示）。

    端点契约（仅读自主项目 server/routers.py 2783 与 response 实测，2026-09-07 修正）：
      - 真实端点：/api/v1/t2i/templates（不是 /api/v1/templates；后者在主项目里不存在，
        会被中间件 fallthrough 到某个空响应，是历史踩坑）；
      - 真实响应：{"code":0,"data":{"templates":[{id,name,category,mode,scene,...}]},"message":"ok",...}
      - 字段名：id（不是 template_id），name，category。
    """
    try:
        base = (_RuntimeConfig.MANJU_API_BASE if _RuntimeConfig is not None else "http://127.0.0.1:7788/api/v1").rsplit("/api/v1", 1)[0]
        # scene=t2i：本地通道 = 文生图流程，过滤掉含 LoadImage 的 i2i 模板
        url = base + "/api/v1/t2i/templates?size=200&scene=t2i"
        with urllib.request.urlopen(url, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        # 真实响应：data.templates[]；老 key (list/items) 不再 fallback（避免历史坑死灰复燃）
        items = (data.get("data") or {}).get("templates") or []
        out = []
        for it in items:
            tid = it.get("id") or ""
            if not tid:
                continue
            name = it.get("name") or ""
            cat = it.get("category") or ""
            label = f"{tid} — {name}" if name else tid
            if cat:
                label += f"  [{cat}]"
            out.append({"value": tid, "label": label})
        return out
    except Exception:
        return []


def _dynamic_options_for(key: str):
    """schema options 为 None 但需要动态注入的字段（如 LOCAL_TEMPLATE_ID 拉主项目模板）。"""
    if key == "LOCAL_TEMPLATE_ID":
        opts = _fetch_template_options()
        # 即使主项目不可达也要返回非空（带占位提示），让前端 select 始终可见
        return opts if opts else [{"value": "", "label": "(主项目不可达，请确认 7788 已启动)"}]
    return None


def _read_env_raw() -> dict:
    """解析 .env 为 {KEY: RAW_VALUE_STRING}，保留其余行结构由 caller 处理。"""
    raw = {}
    if not ENV_PATH.exists():
        return raw
    text = ENV_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if m:
            raw[m.group(1)] = m.group(2).strip()
    return raw


def _to_display(typ: str, raw: str) -> object:
    """把 .env 原始字符串转成前端展示值（字符串为主，bool 转 true/false）。"""
    if typ == "bool":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return raw


def _validate_and_format(typ: str, value: object) -> str:
    """校验前端传来的展示值，返回可写入 .env 的字符串；非法则抛 ValueError。"""
    s = "" if value is None else str(value).strip()
    if typ == "bool":
        low = s.lower()
        if low not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            raise ValueError("布尔值须为 true/false")
        return "true" if low in ("true", "1", "yes", "on") else "false"
    if typ == "int":
        try:
            int(s)
        except ValueError:
            raise ValueError("须为整数")
        return s
    if typ == "float":
        try:
            float(s)
        except ValueError:
            raise ValueError("须为数字")
        return s
    if typ == "intlist":
        for part in s.replace("，", ",").split(","):
            part = part.strip()
            if part and not part.isdigit():
                raise ValueError("须为逗号分隔的 QQ 号（纯数字）")
        return s.replace("，", ",")
    if typ == "select":
        return s  # 前端已约束在 options 内
    return s


def _runtime_default(key: str, typ: str):
    """取 config.Config 中该 key 的运行时真实生效值（.env 未显式配置时的实际默认值）。

    返回 None 表示 config.py 里没有该 key，调用方应回退 SCHEMA default。
    按前端展示类型规整为字符串：bool→"true"/"false"，序列→逗号分隔，其余→str。
    """
    if _RuntimeConfig is None:
        return None
    v = getattr(_RuntimeConfig, key, None)
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (tuple, list)):
        return ",".join(str(x) for x in v)
    if isinstance(v, set):
        return ",".join(str(x) for x in sorted(v))
    return str(v)


def get_config_view() -> dict:
    """GET /api/qq/config：返回分组后的参数视图。

    返回结构：
      { groups: [ { name, core, items: [ {key,label,type,options,core,desc,value,placeholder,secret} ] } ] }
    value 取 .env 当前值，缺省则用 schema default（与 config.py 启动默认值对齐）。
    """
    raw = _read_env_raw()
    # 按 schema 顺序聚合分组
    groups_map: dict[str, dict] = {}
    order: list[str] = []
    for group, key, label, typ, options, core, desc, default, placeholder, secret in SCHEMA:
        if group not in groups_map:
            groups_map[group] = {"name": group, "core": core, "items": []}
            order.append(group)
        cur = raw.get(key)
        if cur is None:
            # 优先 config.py 运行时真实生效值（单一事实来源），取不到才用 schema default
            cur = _runtime_default(key, typ)
            if cur is None:
                cur = default
        groups_map[group]["items"].append({
            "key": key,
            "label": label,
            "type": typ,
            "options": options if options is not None else _dynamic_options_for(key),
            "core": core,
            "desc": desc,
            "value": _to_display(typ, cur),
            "placeholder": placeholder,
            "secret": secret,
        })
    return {"groups": [groups_map[g] for g in order]}


def apply_config(updates: dict) -> dict:
    """PUT /api/qq/config：把 updates 写回 .env（保留注释与顺序），返回生效情况。

    updates: {key: display_string}
    返回：{
      saved:           [所有成功保存的 key（含 core+非 core）],
      requires_restart:[core=true 的 key（需重启 bridge 才生效）],
      hot_applied:     [core=false 的 key（已写入 .env 并 setattr Config.X 立即生效）],
      errors:          {key: msg},
    }
    """
    if not isinstance(updates, dict):
        raise ValueError("updates 须为对象")

    errors: dict[str, str] = {}
    formatted: dict[str, str] = {}
    for key, value in updates.items():
        meta = _META.get(key)
        if not meta:
            errors[key] = "未知参数"
            continue
        try:
            formatted[key] = _validate_and_format(meta["type"], value)
        except ValueError as e:
            errors[key] = str(e)

    if errors:
        # 有错则整体不写，避免半更新
        return {"saved": [], "requires_restart": [], "hot_applied": [], "errors": errors}

    # 写回 .env（先持久化，无论是否热生效都先落盘——保证重启后也生效）
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    seen = set()
    out = []
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if m and m.group(1) in formatted:
            k = m.group(1)
            out.append("%s=%s" % (k, formatted[k]))
            seen.add(k)
        else:
            out.append(line)
    for k, v in formatted.items():
        if k not in seen:
            out.append("%s=%s" % (k, v))

    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")

    # 激活主题双通道同步（2026-09-24）：配置页也是 PIC_CUSTOM_THEME 的合法写入口，
    # 必须与 data/pic_active_theme.json 保持一致——否则 .env 被覆盖成空时，
    # 兜底文件会"复活"一个用户已经在配置页取消掉的主题（反向也同理）。
    if "PIC_CUSTOM_THEME" in formatted:
        try:
            from .pic.theme import write_active_theme

            write_active_theme(formatted["PIC_CUSTOM_THEME"])
        except Exception:  # noqa: BLE001 — 兜底文件写失败绝不能影响配置保存主流程
            pass

    # 拆分 core / 热生效
    requires_restart: list[str] = []
    hot_applied: list[str] = []
    for k, raw_str in formatted.items():
        meta = _META[k]
        if meta["core"]:
            requires_restart.append(k)
            continue
        try:
            _hot_apply(k, raw_str, meta["type"])
            hot_applied.append(k)
        except Exception as e:  # noqa: BLE001 — 单 key 失败不应影响其它
            errors[k] = "已写 .env，但热生效失败（需重启）：%s" % e
            # 关键：失败不应让前端误以为生效。仍算 requires_restart（重启即可生效）
            requires_restart.append(k)

    saved = list(formatted.keys())
    return {
        "saved": saved,
        "requires_restart": requires_restart,
        "hot_applied": hot_applied,
        "errors": errors,
    }


def _hot_apply(key: str, raw_str: str, typ: str) -> None:
    """把字符串按 schema type 转换并 setattr 到 Config 类属性，让运行时立即读到新值。

    适用：core=false 的「运行时类属性」参数。core=true 的（NoneBot 驱动/端口等）
    在 bot 启动时一次性绑定，重启前 setattr 也无效——故 core=true 走 requires_restart。

    失败抛 ValueError，apply_config 会捕获并报「已写 .env，需重启」。
    """
    cfg_cls = _resolve_runtime_config()  # 与业务模块同一份 Config 类

    s = (raw_str or "").strip()

    if typ == "bool":
        # 与 _validate_and_format 对齐：true/1/yes/on → True
        new_val = s.lower() in ("true", "1", "yes", "on")
    elif typ == "int":
        new_val = int(s)
    elif typ == "float":
        new_val = float(s)
    elif typ == "intlist":
        # .env 字符串形如 "1,2,3"（中英逗号都接）；set 形式存
        nums = []
        for p in s.replace("，", ",").split(","):
            p = p.strip()
            if not p:
                continue
            try:
                nums.append(int(p))
            except ValueError as e:
                raise ValueError("intlist 含非数字 %r" % p) from e
        new_val = set(nums)
    elif typ == "strlist":
        parts = [p.strip() for p in s.replace("，", ",").split(",") if p.strip()]
        new_val = tuple(parts) if parts else ()
    elif typ in ("text", "textarea", "select", "secret"):
        # strlist 与 text 的区别：strlist 是 tuple/text 是 str。
        # secret（如 AI_API_KEY）与 text 同族：明文字符串直通 setattr——
        # 少了它，配置页改 Key 会落 requires_restart，用户骂「保存了不生效」
        # （2026-10-19 实测：AI_API_KEY 首次保存即踩此坑）。
        # 注意：此处绝不打印 new_val（LOG-04 密钥禁入日志）。
        new_val = s
    else:
        # 未知 type：保守用 str，不热生效（让 require_restart 兜底）
        raise ValueError("未知 type=%r，无法热生效" % typ)

    # 关键：setattr 类属性（非实例属性），下次任意位置访问 Config.X 都拿新值。
    setattr(cfg_cls, key, new_val)
    # 同步打日志（与现有 config_routes_registered 风格一致）
    try:
        from .config import get_logger, jlog
        # LOG-04：secret 类（AI_API_KEY 等）**禁入日志**——只记 key 名与类型，
        # 值打掩码。此前无 secret+core=false 参数故未暴露；AI_API_KEY 是第一个，
        # 2026-10-19 补此防线（密钥进日志 = 泄露，等同把钥匙插在门上）。
        _logged = "***" if typ == "secret" else str(new_val)[:120]
        get_logger().info(jlog("config_hot_applied", key=key, type=typ, new=_logged))
    except Exception:
        pass


# =============================================================================
# /api/qq/templates 代理（独立 REST 端点，2026-09-07 新增）
# -----------------------------------------------------------------------------
# 背景：管理页「机器人配置」里 LOCAL_TEMPLATE_ID 要下拉选主项目的 ComfyUI 工作流模板，
# 但配置接口的 schema 字段在启动时就已经定型，没法在 get_config_view 里再异步去拉远端。
# 单独暴露一个轻量代理：前端 select 打开时调一次（4s 超时），拿到 [{value,label}] 即可。
# 失败容错：主项目未启动 / 接口异常 → 200 + 空 list + error 字段，前端 select 退化为提示。
# 复用 _fetch_template_options：与 get_config_view 内联版同源，避免两处实现漂移。
# =============================================================================
async def proxy_templates() -> dict:
    """GET /api/qq/templates：从主项目后端拉模板列表，给 LOCAL_TEMPLATE_ID 下拉用。

    返回 {ok: bool, items: [{value,label}], error: str}。
    包装为 async：用 asyncio.to_thread 调同步 urllib.request，避免阻塞 nonebot 事件循环。
    """
    import asyncio
    try:
        items = await asyncio.to_thread(_fetch_template_options)
        # 过滤掉占位提示项（仅在主项目不可达时会出现），让 ok 与 items 语义自洽
        if items and not items[0].get("value"):
            items = []
        return {"ok": True, "items": items, "error": ""}
    except Exception as e:  # noqa: BLE001 — 代理端点容错优先，never raise
        return {"ok": False, "items": [], "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
