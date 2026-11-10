# B站解析：使用官方库 bilibili-api-python（处理 WBI 签名，零逆向），最稳。
# 视频下载：get_download_url 拿 dash 流（音视频分离），httpx 下载后 ffmpeg 合并为 mp4。
from __future__ import annotations

import asyncio
import os
import tempfile

import httpx
from nonebot.log import logger

from .base import BaseParser, Platform, handle, ParseException


def _wan(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return str(n)
    return f"{n / 10000:.1f}万" if n > 10000 else str(n)


# QQ 群视频文件大小上限（保守取 45MB：大文件 NapCat 发送易超时失败）
VIDEO_MAX_BYTES = 45 * 1024 * 1024

_DL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


class BilibiliParser(BaseParser):
    platform = Platform(name="bilibili", display_name="B站")

    @handle("b23.tv", r"b23\.tv/[a-zA-Z0-9]+")
    @handle("bilibili", r"bilibili\.com/video/(?P<bvid>BV[0-9A-Za-z]+)")
    @handle("bilibili_av", r"bilibili\.com/video/av(?P<aid>\d+)")
    async def _parse(self, searched):
        # 短链先重定向到精确页
        if "b23.tv" in searched.group(0):
            real = await self.get_redirect_url("https://" + searched.group(0))
            keyword, m = self.search_url(real)
            return await self.parse(keyword, m)

        bvid = searched.groupdict().get("bvid")
        aid = searched.groupdict().get("aid")
        return await self._fetch(bvid=bvid, aid=aid)

    async def _fetch(self, bvid=None, aid=None):
        from bilibili_api import video as bili_video

        v = bili_video.Video(bvid=bvid, aid=(int(aid) if aid else None))
        info = await v.get_info()

        owner = info.get("owner", {})
        stat = info.get("stat", {}) or {}
        stats = {
            "播放": _wan(stat.get("view")),
            "点赞": _wan(stat.get("like")),
            "投币": _wan(stat.get("coin")),
            "收藏": _wan(stat.get("favorite")),
            "弹幕": _wan(stat.get("danmaku")),
        }
        bv = info.get("bvid") or ("av" + str(info.get("aid")))

        return self.result(
            title=info.get("title"),
            text=info.get("desc"),
            author_name=owner.get("name"),
            author_id=str(owner.get("mid")) if owner.get("mid") else None,
            author_avatar=owner.get("face"),
            cover_url=info.get("pic"),
            duration=info.get("duration"),
            url=f"https://www.bilibili.com/video/{bv}",
            category=info.get("tname"),
            stats=stats,
            extra={
                "bvid": info.get("bvid"),
                "aid": info.get("aid"),
                "pubdate": info.get("pubdate"),
            },
        )

    async def download_video(self, result) -> str | None:
        extra = result.extra or {}
        return await self._download_video(bvid=extra.get("bvid"), aid=extra.get("aid"))

    async def _download_video(self, bvid=None, aid=None) -> str | None:
        """下载 dash 流（音视频分离）并 ffmpeg 合并为 mp4，返回本地路径；失败返回 None。"""
        from bilibili_api import video as bili_video

        try:
            v = bili_video.Video(bvid=bvid, aid=(int(aid) if aid else None))
            dl = await v.get_download_url(0)
        except Exception as e:  # noqa: BLE001
            logger.warning("bili_get_download_url_failed: %s", e)
            return None

        if not isinstance(dl, dict):
            return None
        dash = dl.get("dash")
        if not dash:
            logger.warning("bili_no_dash")
            return None
        videos = dash.get("video") or []
        audios = dash.get("audio") or []
        if not videos:
            return None

        # 选清晰度：清晰度 id 越大越清晰，但单文件不超过上限
        videos = sorted(videos, key=lambda x: x.get("id", 0), reverse=True)
        chosen = next((c for c in videos if (c.get("size") or 0) <= VIDEO_MAX_BYTES), videos[-1])
        audio = max(audios, key=lambda x: x.get("bandwidth", 0)) if audios else None
        v_url = chosen.get("base_url")
        a_url = audio.get("base_url") if audio else None
        if not v_url:
            return None

        tmp = tempfile.mkdtemp(prefix="bili_")
        v_path = os.path.join(tmp, "v.m4s")
        a_path = os.path.join(tmp, "a.m4s")
        out_path = os.path.join(tmp, f"{(bvid or aid or 'video')}.mp4")

        try:
            async with httpx.AsyncClient(
                headers=_DL_HEADERS, follow_redirects=True, timeout=60.0
            ) as client:
                async with client.stream("GET", v_url) as r:
                    r.raise_for_status()
                    with open(v_path, "wb") as f:
                        async for chunk in r.aiter_bytes(65536):
                            f.write(chunk)
                if a_url:
                    async with client.stream("GET", a_url) as r:
                        r.raise_for_status()
                        with open(a_path, "wb") as f:
                            async for chunk in r.aiter_bytes(65536):
                                f.write(chunk)
        except Exception as e:  # noqa: BLE001
            logger.warning("bili_download_failed: %s", e)
            return None

        cmd = ["ffmpeg", "-y", "-i", v_path]
        if a_url:
            cmd += ["-i", a_path]
        cmd += ["-c", "copy", "-movflags", "+faststart", out_path]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=120.0)
        except Exception as e:  # noqa: BLE001
            logger.warning("bili_mux_failed: %s", e)
            return None
        if proc.returncode != 0 or not os.path.exists(out_path):
            return None
        return out_path
