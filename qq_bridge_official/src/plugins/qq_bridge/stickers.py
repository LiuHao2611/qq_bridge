# -*- coding: utf-8 -*-
"""stickers —— 智能表情包模块（2026-10-19）。

需求：① 自动收集群里的表情包为己用；② 自动区分表情包类型；
      ③ 按人设在合适语境发合适的表情包（本喵生气发阴阳怪气图，不发怒图）；
      ④ 配合 AI 模式的「 marker 自主决定发表情包 + 长回复分段连发」。

设计参考（2026 GitHub 权威项目调研，集百家之长，禁闭门造车）：
  - kusadact/nonebot-plugin-ai-groupmate：VLM 理解图片 → 描述语义 + 视觉特征
    双索引 → RRF 多路召回 → **群内真实使用频率重排** → 发送前相关性审核；
  - jiang068/Aibot：自动抓取群内表情包 → VLM 生成描述 → 入库 → 适时发送，
    **数量上限滚动删除**；
  - Funny1Potato/nonebot-plugin-aigf-master：AI 自主决定发表情包 + 自动收藏；
  - QsQsin/qq_autoChat：**按情绪分类的本地表情包库**，AI 判断氛围选分类；
  - TheD0ubleC/StickerSelector：AI 生成「表情包意图描述」→ 语义匹配选图。

本地兼容取舍（个人机器 / 无向量库 / 不乱花钱）：
  - 索引用 JSON 文件而非向量库；「语义匹配」轻量化为
    「LLM/心情 → 人设映射分类 → 分类内加权随机（使用频率加权 =
    ai-groupmate 频率重排的极简化）」；
  - 分类双轨：Tier A **本地 OCR**（winrt 零成本，只读表情包上的文字，
    零幻觉）+ Tier B **可选 VLM**（需视觉模型，默认关闭，开了才花钱）；
  - 收集只收高置信表情（image sub_type=1 / mface 商城表情 / 小体积 GIF），
    普通照片一律不收——防止把群友自拍塞进表情库。

数据布局（qq_bridge/data/stickers/）：
  - 顶层 *.gif/jpg/png       内置库（人工放的，保持原样）
  - collected/*.xxx          自动收集库
  - index.json               {md5: {file, cat, desc, uses, at, w, h, src}}
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from pathlib import Path

import httpx
from PIL import Image

from .config import Config, get_logger, jlog

log = get_logger()

#: 表情包分类表（VLM/OCR/映射都用这一套；改这里即全链路生效）
STICKER_CATS = (
    "开心", "搞笑", "愤怒", "无语", "阴阳怪气",
    "安慰", "庆祝", "疑问", "爱心", "劳累", "通用",
)

#: OCR 文字 → 分类 的关键词表（Tier A 本地零成本分类；命中即分类）
_OCR_CAT_WORDS: dict[str, tuple[str, ...]] = {
    "阴阳怪气": ("不会吧", "呢呢呢", "羡慕", "厉害死了", "不会有人", "笑死",
                 "就这", "离谱", "装", "哦豁", "急了"),
    "愤怒": ("气死", "愤怒", "滚", "打你", "揍", "杀了", "发火", "火大"),
    "无语": ("无语", "沉默", "呵呵", "麻了", "摆烂", "随便", "行吧", "累了"),
    "开心": ("哈哈", "开心", "笑死", "快乐", "好耶", "喜滋滋", "嘿嘿"),
    "搞笑": ("搞笑", "整活", "沙雕", "抽象", "离谱", "乐子"),
    "安慰": ("抱抱", "安慰", "加油", "辛苦", "心疼", "摸摸头"),
    "庆祝": ("恭喜", "发财", "周年", "过啦", "通过", "生日", "上岸"),
    "疑问": ("为什么", "怎么", "居然", "难道", "疑问"),
    "爱心": ("爱你", "喜欢", "亲亲", "么么", "比心"),
    "劳累": ("困", "累", "躺平", "下班", "想睡", "摆烂"),
}

#: 内置库文件名 → 分类 的默认提示（白馆长是知名阴阳怪气表情包系列）
_BUILTIN_NAME_HINTS: dict[str, str] = {
    "whitevillain": "阴阳怪气",
    "tuhi": "搞笑",
    "penguin": "搞笑",
    "chicken": "搞笑",
}

#: 收集过滤参数
_COLLECT_MAX_BYTES = 2 * 1024 * 1024   # 单张下载上限（防大图/视频冒充）
_COLLECT_MIN_PX = 60                   # 小于此判为小图标，不收
_COLLECT_MAX_PX = 720                  # 大于此判为照片，不收
_COLLECT_PER_GROUP_HOUR = 20           # 每群每小时收集上限（防把硬盘爬满）


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def parse_persona_map(raw: str) -> dict[str, list[str]]:
    """解析人设情绪→分类映射配置文本。

    格式：`生气=阴阳怪气,无语;开心=开心,搞笑;默认=通用`
    （分号或换行分隔条目；等号左边情绪，右边逗号分隔分类）。
    非法行静默跳过。空输入 → 返回 {}（调用方用内置默认）。
    """
    out: dict[str, list[str]] = {}
    for part in re.split(r"[;\n；]", raw or ""):
        part = part.strip()
        if not part or "=" not in part:
            continue
        mood, _, cats = part.partition("=")
        mood = mood.strip()
        cat_list = [c.strip() for c in re.split(r"[,，]", cats) if c.strip()]
        cat_list = [c for c in cat_list if c in STICKER_CATS]
        if mood and cat_list:
            out[mood] = cat_list
    return out


#: 本喵（腹黑阴阳怪气猫管家）的默认情绪→表情分类映射。
#: 核心人设体现：**生气时不发怒图，发阴阳怪气/无语图**——用户原话。
DEFAULT_PERSONA_MAP: dict[str, list[str]] = {
    "生气": ["阴阳怪气", "无语"],
    "愤怒": ["阴阳怪气", "无语"],
    "开心": ["开心", "搞笑"],
    "高兴": ["开心", "搞笑"],
    "无语": ["无语", "阴阳怪气"],
    "安慰": ["安慰", "爱心"],
    "庆祝": ["庆祝", "开心"],
    "疑问": ["疑问"],
    "爱心": ["爱心"],
    "劳累": ["劳累", "安慰"],
    "默认": ["通用", "搞笑"],
}


class StickerEntry:
    """一张表情包的索引条目（内存态）。"""

    __slots__ = ("md5", "file", "cat", "desc", "uses", "at", "w", "h", "src", "builtin")

    def __init__(self, md5: str, file: str, cat: str = "待分类",
                 desc: str = "", uses: int = 0, at: float = 0.0,
                 w: int = 0, h: int = 0, src: str = "", builtin: bool = False):
        self.md5 = md5
        self.file = file
        self.cat = cat
        self.desc = desc
        self.uses = uses
        self.at = at
        self.w = w
        self.h = h
        self.src = src
        self.builtin = builtin

    def to_dict(self) -> dict:
        return {"file": self.file, "cat": self.cat, "desc": self.desc,
                "uses": self.uses, "at": self.at, "w": self.w, "h": self.h,
                "src": self.src, "builtin": self.builtin}

    @classmethod
    def from_dict(cls, md5: str, d: dict) -> "StickerEntry":
        return cls(md5=md5, file=str(d.get("file") or ""),
                   cat=str(d.get("cat") or "待分类"), desc=str(d.get("desc") or ""),
                   uses=int(d.get("uses") or 0), at=float(d.get("at") or 0),
                   w=int(d.get("w") or 0), h=int(d.get("h") or 0),
                   src=str(d.get("src") or ""), builtin=bool(d.get("builtin")))


class StickerLib:
    """表情库：内置库 + 收集库 + 索引 + 分类 + 人设选用。"""

    def __init__(self, base_dir: Path | None = None):
        self.base = Path(base_dir) if base_dir else (
            Path(__file__).resolve().parents[3] / "data" / "stickers")
        self.collected_dir = self.base / "collected"
        self.index_path = self.base / "index.json"
        self._entries: dict[str, StickerEntry] = {}
        # 收集限流：{group_id: [时间戳, ...]}（近 1 小时）
        self._recent_collect: dict[int, list[float]] = {}
        self._load()

    # ---------------- 索引读写 ----------------
    def _load(self) -> None:
        try:
            if self.index_path.exists():
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                items = data.get("items") if isinstance(data, dict) else data
                if isinstance(items, dict):
                    self._entries = {
                        k: StickerEntry.from_dict(k, v)
                        for k, v in items.items() if isinstance(v, dict)
                    }
        except Exception as e:  # noqa: BLE001 — 坏索引不能拖垮启动
            log.warning(jlog("sticker_index_load_failed", error=type(e).__name__))
            self._entries = {}

    def _save(self) -> None:
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.index_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(
                {"items": {k: v.to_dict() for k, v in self._entries.items()}},
                ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.index_path)
        except Exception as e:  # noqa: BLE001 — 落盘失败只记日志（CLOSE-02）
            log.warning(jlog("sticker_index_save_failed", error=type(e).__name__))

    # ---------------- 库视图 ----------------
    def _scan_builtin(self) -> list[StickerEntry]:
        """扫描顶层内置表情（非 collected/ 子目录的图片）。"""
        out: list[StickerEntry] = []
        if not self.base.exists():
            return out
        exts = (".gif", ".png", ".jpg", ".jpeg", ".webp")
        for fp in sorted(self.base.iterdir()):
            if not fp.is_file() or fp.suffix.lower() not in exts:
                continue
            md5 = "b:" + fp.stem.lower()
            e = self._entries.get(md5)
            if e is None:
                # 首次见到：按文件名提示给个初始分类（可被 VLM 精修）
                cat = "通用"
                low = fp.stem.lower()
                for hint, c in _BUILTIN_NAME_HINTS.items():
                    if hint in low:
                        cat = c
                        break
                e = StickerEntry(md5=md5, file=str(fp), cat=cat, builtin=True,
                                 desc="内置库")
                self._entries[md5] = e
            out.append(e)
        return out

    def _scan_collected(self) -> list[StickerEntry]:
        # 注意：不做 collected_dir 存在性捷径——条目按 e.file 存在性逐个校验，
        # 目录没了/换位置都能正确反映（2026-10-19 测试踩坑：捷径让临时目录的
        # 条目全部不可见）。
        out: list[StickerEntry] = []
        for e in self._entries.values():
            if not e.builtin and e.file and Path(e.file).exists():
                out.append(e)
        return out

    def all_entries(self) -> list[StickerEntry]:
        return self._scan_builtin() + self._scan_collected()

    def usable(self) -> list[StickerEntry]:
        """可发送的表情（已分类的；「待分类」暂不参与选用）。"""
        return [e for e in self.all_entries() if e.cat in STICKER_CATS]

    def stats(self) -> dict:
        entries = self.all_entries()
        by_cat: dict[str, int] = {}
        for e in entries:
            by_cat[e.cat] = by_cat.get(e.cat, 0) + 1
        return {
            "total": len(entries),
            "builtin": sum(1 for e in entries if e.builtin),
            "collected": sum(1 for e in entries if not e.builtin),
            "pending": sum(1 for e in entries if e.cat == "待分类"),
            "by_cat": by_cat,
        }

    # ---------------- 选用（人设映射 + 频率加权） ----------------
    def pick(self, mood: str | None = None,
             persona_map: dict[str, list[str]] | None = None) -> StickerEntry | None:
        """按人设选一张表情包。

        流程（QsQsin 情绪分类 + TheD0ubleC 意图匹配 + ai-groupmate 频率重排的
        本地轻量融合）：mood → 人设映射得到候选分类（有序）→ 合并各分类池
        → 使用频率加权随机（uses 越高权重越高，封顶 +100%）。
        任何一级落空都有兜底：映射分类空 → 默认分类 → 通用 → 全库。
        """
        pool_all = self.usable()
        if not pool_all:
            return None
        pmap = persona_map if persona_map is not None else DEFAULT_PERSONA_MAP
        cats: list[str] = []
        if mood:
            cats = list(pmap.get(mood) or [])
        if not cats:
            cats = list(pmap.get("默认") or ["通用"])
        pool: list[StickerEntry] = []
        for c in cats:
            pool.extend([e for e in pool_all if e.cat == c])
        if not pool:
            pool = [e for e in pool_all if e.cat == "通用"] or pool_all
        weights = [1.0 + min(e.uses, 10) * 0.1 for e in pool]
        return random.choices(pool, weights=weights, k=1)[0]

    def record_use(self, entry: StickerEntry | None) -> None:
        if entry is None:
            return
        entry.uses += 1
        self._entries[entry.md5] = entry
        self._save()

    # ---------------- 收集 ----------------
    @staticmethod
    def collect_candidate(seg) -> str | None:
        """判断消息段是否值得收集，返回候选 URL/路径；否则 None。

        高置信才收（防止把群友自拍/图表塞进表情库）：
          ① image 且 sub_type == 1（go-cqhttp/NapCat 约定：表情包）；
          ② mface 商城表情段（有 url/file 才收）；
          ③ 小体积 GIF（≤1MB，动图在群里几乎必是表情包）。
        """
        try:
            stype = str(getattr(seg, "type", "") or "")
            data = getattr(seg, "data", None) or {}
        except Exception:  # noqa: BLE001
            return None
        if stype == "image":
            sub = data.get("sub_type")
            url = data.get("url") or data.get("path") or data.get("file") or ""
            if not url:
                return None
            if str(sub) == "1":
                return str(url)
            # 无 sub_type 的图：只收「小体积 GIF」（群里动图几乎必是表情包）。
            # 扩展名判 file + url 两处——NapCat 的 file 字段有时不带后缀。
            name = (str(data.get("file") or "") + " " + str(url or "")).lower()
            if ".gif" in name:
                try:
                    if int(data.get("file_size") or 0) <= 1024 * 1024:
                        return str(url)
                except (TypeError, ValueError):
                    return str(url)  # 大小未知的 GIF 也先收了（后面尺寸窗兜底）
            return None
        if stype == "mface":
            url = data.get("url") or data.get("file") or data.get("path") or ""
            return str(url) if url else None
        return None

    def _collect_rate_ok(self, group_id: int) -> bool:
        now = time.time()
        recent = [t for t in self._recent_collect.get(group_id, []) if now - t < 3600]
        self._recent_collect[group_id] = recent
        return len(recent) < _COLLECT_PER_GROUP_HOUR

    async def collect_from_event(self, event) -> int:
        """从一条群消息收集表情包，返回新增数量（fire-and-forget 安全）。"""
        if not Config.AI_STICKER_COLLECT:
            return 0
        group_id = int(getattr(event, "group_id", 0) or 0)
        if group_id and not self._collect_rate_ok(group_id):
            return 0
        segs = (getattr(event, "original_message", None)
                or getattr(event, "message", None) or [])
        cands: list[str] = []
        for seg in segs:
            u = self.collect_candidate(seg)
            if u:
                cands.append(u)
        if not cands:
            return 0
        added = 0
        for url in cands[:3]:  # 单条消息最多收 3 张（防刷屏式收集）
            try:
                data = await self._download(url)
                if not data:
                    continue
                if not self._keep(data):
                    continue
                md5 = _md5(data)
                if md5 in self._entries:
                    continue
                if self._over_cap():
                    self._evict()
                fp = self.collected_dir / ("%s.%s" % (md5[:12], self._ext(data)))
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_bytes(data)
                w, h = self._dims(data)
                self._entries[md5] = StickerEntry(
                    md5=md5, file=str(fp), cat="待分类", at=time.time(),
                    w=w, h=h, src="g:%d" % group_id)
                added += 1
            except Exception as e:  # noqa: BLE001 — 单张失败不影响其他
                log.warning(jlog("sticker_collect_failed", error=type(e).__name__,
                                 detail=str(e)[:120]))
        if added:
            self._save()
            self._recent_collect.setdefault(group_id, []).extend([time.time()] * added)
            log.info(jlog("sticker_collected", group=group_id, added=added))
        return added

    async def _download(self, url: str) -> bytes | None:
        """下载候选表情（本地 path 直读；http(s) 走 httpx，带上限）。"""
        try:
            if not (url.startswith("http://") or url.startswith("https://")):
                p = Path(url)
                if p.is_file() and p.stat().st_size <= _COLLECT_MAX_BYTES:
                    return p.read_bytes()
                return None
            async with httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0"}, timeout=15.0, trust_env=False
            ) as cli:
                r = await cli.get(url)
                if r.status_code != 200 or not r.content:
                    return None
                if len(r.content) > _COLLECT_MAX_BYTES:
                    return None
                return r.content
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("sticker_download_failed", error=type(e).__name__))
            return None

    @staticmethod
    def _ext(data: bytes) -> str:
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "gif"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "png"
        if data[:2] == b"\xff\xd8":
            return "jpg"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp"
        return "png"

    @staticmethod
    def _dims(data: bytes) -> tuple[int, int]:
        try:
            import io as _io
            with Image.open(_io.BytesIO(data)) as im:
                return int(im.width), int(im.height)
        except Exception:  # noqa: BLE001
            return 0, 0

    def _keep(self, data: bytes) -> bool:
        """尺寸窗过滤：太小是小图标，太大是照片/截图。"""
        w, h = self._dims(data)
        if not w or not h:
            return False
        return _COLLECT_MIN_PX <= w <= _COLLECT_MAX_PX and _COLLECT_MIN_PX <= h <= _COLLECT_MAX_PX

    def _over_cap(self) -> bool:
        n = sum(1 for e in self._entries.values() if not e.builtin)
        return n >= max(10, int(Config.AI_STICKER_MAX))

    def _evict(self) -> None:
        """滚动删除（Aibot 模式）：淘汰收集库里最旧且使用最少的。"""
        collected = [e for e in self._entries.values() if not e.builtin]
        if not collected:
            return
        victim = min(collected, key=lambda e: (e.uses, e.at))
        try:
            Path(victim.file).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        self._entries.pop(victim.md5, None)
        log.info(jlog("sticker_evicted", file=Path(victim.file).name, uses=victim.uses))

    # ---------------- 分类 ----------------
    def pending_entries(self) -> list[StickerEntry]:
        return [e for e in self._scan_collected() if e.cat == "待分类"]

    @staticmethod
    def _cat_from_text(text: str) -> str | None:
        """OCR 文字 → 分类（Tier A，关键词命中即返回）。"""
        if not text:
            return None
        for cat, words in _OCR_CAT_WORDS.items():
            if any(w in text for w in words):
                return cat
        return None

    async def classify_pending(self, vlm: bool = False, limit: int = 12) -> dict:
        """分类待分类表情。Tier A OCR 永远跑；Tier B VLM 按开关跑。

        返回 {"ocr": n, "vlm": n, "failed": n}。
        """
        pending = self.pending_entries()[:limit]
        if not pending:
            return {"ocr": 0, "vlm": 0, "failed": 0}
        n_ocr = n_vlm = n_fail = 0
        # ---- Tier A：本地 OCR（零成本）----
        still: list[StickerEntry] = []
        for e in pending:
            try:
                data = Path(e.file).read_bytes()
                from .game_tools.ocr import ocr_image
                text = await ocr_image(data)
                cat = self._cat_from_text(text)
                if cat:
                    e.cat = cat
                    e.desc = (text or "")[:60]
                    n_ocr += 1
                else:
                    still.append(e)
            except Exception as ex:  # noqa: BLE001 — OCR 挂了不挡后续
                log.warning(jlog("sticker_ocr_failed", error=type(ex).__name__))
                still.append(e)
        # ---- Tier B：VLM 视觉理解（可选，需视觉模型）----
        if vlm and still and Config.AI_STICKER_VLM:
            n_vlm, n_fail = await self._classify_vlm(still)
        elif still and not Config.AI_STICKER_VLM:
            # 没开 VLM：剩余的统一归「通用」（至少能参与随机发送）
            for e in still:
                e.cat = "通用"
                n_fail += 0  # 不算失败，只是未精分类
        self._save()
        log.info(jlog("sticker_classified", ocr=n_ocr, vlm=n_vlm,
                      remain=sum(1 for e in self._entries.values()
                                 if not e.builtin and e.cat == "待分类")))
        return {"ocr": n_ocr, "vlm": n_vlm, "failed": n_fail}

    async def _classify_vlm(self, entries: list[StickerEntry]) -> tuple[int, int]:
        """Tier B：调视觉大模型批量分类（ai-groupmate/Aibot 同款思路）。

        OpenAI 兼容 chat/completions + image_url（base64 data URL）。
        模型用 AI_STICKER_VLM_MODEL（默认跟 AI_MODEL；不是视觉模型会 400，
        显式记日志、不重试、不崩）。
        """
        import base64 as _b64
        model = Config.AI_STICKER_VLM_MODEL or Config.AI_MODEL
        cat_list = "、".join(STICKER_CATS[:-1])  # 不含"通用"
        n_ok = n_fail = 0
        for batch_start in range(0, len(entries), 4):
            batch = entries[batch_start:batch_start + 4]
            content: list[dict] = [{
                "type": "text",
                "text": ("下面是 QQ 表情包图片，逐张判断情绪分类。"
                         "只输出 JSON 数组，每项 {\"i\": 序号(从0开始), "
                         "\"cat\": \"分类\", \"desc\": \"10字内中文描述\"}。"
                         "分类只能是：%s。" % cat_list),
            }]
            for i, e in enumerate(batch):
                try:
                    raw = Path(e.file).read_bytes()
                    b64 = _b64.b64encode(raw).decode("ascii")
                    mime = "image/gif" if e.file.endswith(".gif") else "image/png"
                    content.append({"type": "image_url",
                                    "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}})
                except Exception:  # noqa: BLE001
                    continue
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.2,
                "max_tokens": 400,
                "stream": False,
            }
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(60.0), trust_env=False
                ) as cli:
                    resp = await cli.post(
                        _chat_url(), json=payload,
                        headers={"Authorization": "Bearer %s" % Config.AI_API_KEY})
                if resp.status_code >= 400:
                    raise RuntimeError("HTTP %d" % resp.status_code)
                j = resp.json()
                text = str(((j or {}).get("choices") or [{}])[0]
                           .get("message", {}).get("content") or "")
                m = re.search(r"\[.*\]", text, re.S)
                arr = json.loads(m.group(0)) if m else []
                for item in arr:
                    idx = int(item.get("i", -1))
                    cat = str(item.get("cat") or "").strip()
                    if 0 <= idx < len(batch) and cat in STICKER_CATS and cat != "通用":
                        batch[idx].cat = cat
                        batch[idx].desc = str(item.get("desc") or "")[:60]
                        n_ok += 1
            except Exception as ex:  # noqa: BLE001 — VLM 失败显式记录，不崩
                log.warning(jlog("sticker_vlm_failed", model=model,
                                 error=type(ex).__name__, detail=str(ex)[:120]))
                n_fail += len(batch)
        return n_ok, n_fail


def _chat_url() -> str:
    """OpenAI 兼容 chat/completions 全端点（与 ai_mode 同款归一化）。"""
    from .ai_mode import _resolve_chat_url
    return _resolve_chat_url(Config.AI_API_BASE)


# 模块级单例
sticker_lib = StickerLib()


async def collect_group_stickers(event) -> int:
    """群消息表情收集入口（fire-and-forget 安全：异常全内吞）。"""
    try:
        return await sticker_lib.collect_from_event(event)
    except Exception as e:  # noqa: BLE001 — 收集是旁路，绝不拖垮消息处理
        log.warning(jlog("sticker_collect_crash", error=type(e).__name__))
        return 0
