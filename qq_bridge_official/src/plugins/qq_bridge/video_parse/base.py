# 视频链接解析核心（BaseParser 架构）。
# 架构搬运自 fllesser/nonebot-plugin-parser (MIT, https://github.com/fllesser/nonebot-plugin-parser)：
#   - @handle(keyword, pattern) 装饰器把"URL 关键字 -> 正则"注册到解析器类；
#   - BaseParser.__init_subclass__ 自动收集子类与其 handler，并在 search_url() 里统一调度；
#   - 统一 result 模型 result() 收集平台/标题/UP/封面/分区等。
# 相对上游做了"少量兼容当前项目"的精简：去掉 Alconna / uninfo / downloader / 媒体下载，
# 只保留"元数据解析 + 统一结果"，发送由本项目的 on_message matcher 接管。
from __future__ import annotations

import re
from re import Match, Pattern, compile
from abc import ABC
from typing import Any, ClassVar, TypeVar, cast, final
from dataclasses import dataclass, field

from httpx import AsyncClient

# 各平台网页通常需要像样 UA，否则被风控拦截。
COMMON_HEADER: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
COMMON_TIMEOUT = 12.0


@dataclass(slots=True)
class Platform:
    name: str
    display_name: str


@dataclass(slots=True)
class VideoParseResult:
    """统一解析结果（只承载元数据，不下载媒体文件）。"""

    platform: str
    platform_display: str
    title: str | None = None
    text: str | None = None
    author_name: str | None = None
    author_id: str | None = None
    author_avatar: str | None = None
    cover_url: str | None = None
    duration: int | None = None
    url: str | None = None
    category: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    video_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ParseException(Exception):
    """解析失败（URL 不匹配 / 接口异常等）。"""


T = TypeVar("T", bound="BaseParser")
HandlerFunc = type[Any]  # 占位，真正签名见下
KeyPatterns = list[tuple[str, Pattern[str]]]
_KEY_PATTERNS = "_key_patterns"


def handle(keyword: str, pattern: str):
    """注册一个 URL 形态处理器：keyword 用于快速预筛，pattern 用于精确匹配。"""

    def decorator(func):
        if not hasattr(func, _KEY_PATTERNS):
            setattr(func, _KEY_PATTERNS, [])
        patterns: KeyPatterns = getattr(func, _KEY_PATTERNS)
        patterns.append((keyword, compile(pattern)))
        return func

    return decorator


HandlerFunc = Any  # type: ignore[assignment]


class BaseParser(ABC):
    platform: ClassVar[Platform]
    _registry: ClassVar[list[type["BaseParser"]]] = []

    def __init__(self):
        self.headers = dict(COMMON_HEADER)
        self.timeout = COMMON_TIMEOUT

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if ABC in cls.__bases__:
            return
        # 仅注册"定义了 platform 的具体解析器"，跳过 BaseParser/MetaParser 等中间基类
        if not hasattr(cls, "platform") or getattr(cls, "platform", None) is None:
            return
        BaseParser._registry.append(cls)
        cls._handlers = {}
        cls._key_patterns = []
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name, None)
            if callable(attr) and hasattr(attr, _KEY_PATTERNS):
                for keyword, pattern in getattr(attr, _KEY_PATTERNS):
                    cls._handlers[keyword] = attr
                    cls._key_patterns.append((keyword, pattern))
        # 关键字长的优先（如 v.douyin 先于 douyin）
        cls._key_patterns.sort(key=lambda x: -len(x[0]))

    @classmethod
    def get_parser_classes(cls) -> list[type["BaseParser"]]:
        return cls._registry

    @classmethod
    def search_url(cls, url: str) -> tuple[str, Match[str]]:
        """在 url 中查找首个能命中的平台正则，返回 (keyword, match)。"""
        for pcls in BaseParser._registry:
            for keyword, pattern in pcls._key_patterns:
                if keyword not in url:
                    continue
                if searched := pattern.search(url):
                    return keyword, searched
        raise ParseException(f"无法匹配 {url}")

    @final
    async def parse(self, keyword: str, searched: Match[str]) -> VideoParseResult:
        return await self._handlers[keyword](self, searched)

    @classmethod
    def result(cls, **kwargs: Any) -> VideoParseResult:
        return VideoParseResult(
            platform=cls.platform.name,
            platform_display=cls.platform.display_name,
            **kwargs,
        )

    @staticmethod
    async def get_redirect_url(url: str, headers: dict[str, str] | None = None) -> str:
        """取单次 302 重定向后的真实地址（抖音/快手短链常用）。"""
        async with AsyncClient(
            headers=headers or COMMON_HEADER,
            verify=False,
            follow_redirects=False,
            timeout=COMMON_TIMEOUT,
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                resp.raise_for_status()
            return resp.headers.get("Location", url)

    @staticmethod
    async def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
        """GET 页面并返回文本（用于 og:meta 提取）。"""
        async with AsyncClient(
            headers=headers or COMMON_HEADER,
            verify=False,
            follow_redirects=True,
            timeout=COMMON_TIMEOUT,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def download_video(self, result: "VideoParseResult") -> str | None:
        """可选：下载视频文件并返回本地路径；默认不下载（只发元数据卡片）。

        各平台子类按需重写：能拿到直链就下载，拿不到返回 None（降级为只发文字卡）。
        """
        return None

    @classmethod
    def get_parser_by_name(cls, name: str) -> "type[BaseParser] | None":
        """按 platform.name 查找已注册的解析器类。"""
        for pcls in cls._registry:
            if getattr(pcls.platform, "name", None) == name:
                return pcls
        return None


def fmt_duration(seconds: int | None) -> str | None:
    if not seconds:
        return None
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
