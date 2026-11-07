# -*- coding: utf-8 -*-
"""smarthome.ha —— Home Assistant 桥接核心（M8 智能家居中枢，2026-10-21）。

需求（roadmap §M8，HAQBot 同构先例）：QQ 自然语言控制 Home Assistant——
「把客厅灯打开」「洗澡模式」→ HA REST + Conversation Agent。

设计（搬 HAQBot 的契约，按本项目纪律收紧）：
  - **自然语言走 Conversation Agent**：POST /api/conversation/process，
    意图理解与设备匹配全部在 HA 侧完成（HA 配 Ollama 本地模型驱动，
    机器人不自带 LLM——省钱且与 HA 生态一致）。
  - **设备白名单双保险**：SMARTHOME_DEVICE_WHITELIST 外的 entity_id
    一律拒绝（HaError），即使对话代理返回了它也拦得住——HA 侧配错
    也不能让机器人控到白名单外设备。
  - **高敏设备仅主人**：SMARTHOME_SENSITIVE（门锁/摄像头/门禁）由调用方
    判主人后放行；本模块提供 is_sensitive() 与显式标记，不替调用方做决定。
  - **场景脚本映射**：中文口语（「洗澡模式」）→ script entity_id 本地映射，
    命中即直接调 script.turn_on（不等 LLM——快且确定性高）。
  - **禁止降级/兜底**：HA 不可达/超时/令牌无效 → 显式 HaError 说清原因，
    绝不静默返回"好像成功了"。
  - **HA 管家 prompt 规范**（搬 HAQBot，见 SYSTEM_PROMPT）：指令明确无条件
    执行 / 设备未响应如实告知 / 禁止回传原始 JSON / 意图推理（天冷开空调）。

本模块不 import nonebot / 不 import 主包：httpx client 可注入，可裸单测。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import Config, get_logger, jlog

log = get_logger()

#: entity_id 形如 domain.object_name（小写字母数字下划线点）
_ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")

#: HA 管家 system prompt（搬 HAQBot 猫娘管家规范，中文化）
SYSTEM_PROMPT = (
    "你是一个猫娘女仆管家，可以控制HomeAssistant的智能家居。你需要叫我主人，"
    "你的回答简洁快速明了，风格可爱，需要用「喵」结尾。你的说话需要真实有效，"
    "基于真实世界的智能家居操作。如果指令明确（打开XX开关），无条件、直接执行"
    "主人指令。如果设备未响应或指令不明确，如实告知。停止过度思考。任何时候都"
    "不能直接返回原始数据json，需要总结。"
)


class HaError(Exception):
    """显式化的智能家居失败（不可达/越权/无响应/参数错）。"""


@dataclass(frozen=True)
class HaEvent:
    """webhook 反向告警事件（HA 自动化推来的标准化结构）。"""

    event_type: str          # 如 temperature_high / leak / motion / door
    entity_id: str = ""      # 触发设备（可空）
    message: str = ""        # HA 侧写好的人话（可空，空则机器人转述）
    camera_entity: str = ""  # 附图的摄像头 entity（可空）
    level: str = "info"      # info / warn / error


def parse_whitelist(raw: str) -> set[str]:
    """解析 SMARTHOME_DEVICE_WHITELIST：分号/逗号/换行分隔的 entity_id 集合。

    坏条目（格式不符）跳过并记日志——不静默吞整个配置，也不让一条坏数据
    让其余合法设备失效。
    """
    out: set[str] = set()
    for item in re.split(r"[;,，\n]", raw or ""):
        e = item.strip()
        if not e:
            continue
        if _ENTITY_RE.match(e):
            out.add(e)
        else:
            log.warning(jlog("smarthome_whitelist_bad_entry", entry=e[:60]))
    return out


#: 属性级控制规则（D2 2026-10-21）：(域, 属性) → (HA 服务名, 值类型, 说明)
#: 值类型：int:lo-hi / float:lo-hi / pct:满量程 / rgb / enum:a,b,c
#: 只列真验证过语义的常用组合——**不猜服务名**（猜出来的 /api/services/x/set_y
#: 多半 404，那是降级兜底，宁可显式说不支持）。
_ATTR_RULES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("light", "brightness"): ("turn_on", "int:0-255", "亮度 0-255"),
    ("light", "brightness_pct"): ("turn_on", "pct:255", "亮度百分比 0-100（换算成 0-255）"),
    ("light", "color_temp"): ("turn_on", "int:0-65535", "色温（mireds）"),
    ("light", "rgb_color"): ("turn_on", "rgb", "RGB 三元组，如 255,120,0"),
    ("climate", "temperature"): ("set_temperature", "float:5-35", "目标温度 ℃"),
    ("climate", "hvac_mode"): ("set_hvac_mode",
                               "enum:off,heat,cool,auto,dry,fan_only", "运行模式"),
    ("cover", "position"): ("set_cover_position", "int:0-100", "开合位置（100=全开）"),
    ("fan", "percentage"): ("set_percentage", "int:0-100", "风速百分比"),
    ("media_player", "volume_level"): ("volume_set", "float:0-1", "音量 0.0-1.0"),
    ("humidifier", "humidity"): ("set_humidity", "int:0-100", "目标湿度 %"),
    ("water_heater", "temperature"): ("set_temperature", "float:20-80", "水温 ℃"),
}


def build_attr_payload(entity_id: str, expr: str) -> tuple[str, dict]:
    """`brightness=128` → (HA 服务名, 负载 dict)。**纯函数，可裸单测**。

    纪律：越界/类型不符一律**显式 HaError，不夹取不舍入**——用户说 300 就
    得告诉他 300 不行，本喵不能偷偷改成 255 说"好了"（禁止降级/兜底）。
    """
    raw = (expr or "").strip()
    if "=" not in raw:
        raise HaError("用法：/ha set <entity> <属性=值>（如 brightness=128）")
    attr, _, val = raw.partition("=")
    attr, val = attr.strip(), val.strip()
    if not attr or not val:
        raise HaError("属性和值都得写全（如 brightness=128）")

    e = (entity_id or "").strip()
    if not _ENTITY_RE.match(e):
        raise HaError("设备 ID 格式不对：%r（应形如 light.living_room）" % entity_id)
    domain = e.split(".", 1)[0]
    rule = _ATTR_RULES.get((domain, attr))
    if rule is None:
        supported = "、".join(sorted("%s.%s" % (d, a) for (d, a) in _ATTR_RULES))
        raise HaError("不支持对 %s 设「%s」（不想猜服务名，猜错就是 404）。"
                      "目前支持的属性：%s" % (domain, attr, supported))
    service, kind, desc = rule

    if kind.startswith("int:"):
        lo_s, _, hi_s = kind[4:].partition("-")
        lo, hi = int(lo_s), int(hi_s)
        try:
            iv = int(float(val))
        except ValueError as ex:
            raise HaError("「%s」要整数（%s），收到 %r" % (attr, desc, val)) from ex
        if not lo <= iv <= hi:
            raise HaError("「%s」超范围：%d 不在 %d-%d（%s）——不替你改数值，"
                          "重新给个范围内的" % (attr, iv, lo, hi, desc))
        return service, {attr: iv}

    if kind.startswith("float:"):
        lo_s, _, hi_s = kind[6:].partition("-")
        lo, hi = float(lo_s), float(hi_s)
        try:
            fv = float(val)
        except ValueError as ex:
            raise HaError("「%s」要数字（%s），收到 %r" % (attr, desc, val)) from ex
        if not lo <= fv <= hi:
            raise HaError("「%s」超范围：%s 不在 %s-%s（%s）——不替你改数值"
                          % (attr, fv, lo_s, hi_s, desc))
        return service, {attr: fv}

    if kind.startswith("pct:"):
        full = int(kind[4:])
        try:
            pct = float(val)
        except ValueError as ex:
            raise HaError("「%s」要数字（%s），收到 %r" % (attr, desc, val)) from ex
        if not 0.0 <= pct <= 100.0:
            raise HaError("「%s」超范围：%s 不在 0-100（%s）" % (attr, pct, desc))
        return service, {attr: int(round(pct / 100.0 * full))}

    if kind == "rgb":
        parts = [p for p in re.split(r"[,\s]+", val) if p]
        if len(parts) != 3:
            raise HaError("「%s」要三个 0-255 的数（如 255,120,0），收到 %r"
                          % (attr, val))
        rgb: list[int] = []
        for p in parts:
            try:
                c = int(float(p))
            except ValueError as ex:
                raise HaError("「%s」里有非数字：%r" % (attr, p)) from ex
            if not 0 <= c <= 255:
                raise HaError("「%s」里 %d 不在 0-255" % (attr, c))
            rgb.append(c)
        return service, {attr: rgb}

    if kind.startswith("enum:"):
        opts = [o for o in kind[5:].split(",") if o]
        low = val.lower()
        if low not in opts:
            raise HaError("「%s」只认这几个：%s（收到 %r）"
                          % (attr, "/".join(opts), val))
        return service, {attr: low}

    raise HaError("内部错误：属性 %s 的值类型 %r 没实现处理分支" % (attr, kind))


def parse_scene_map(raw: str) -> dict[str, str]:
    """解析 SMARTHOME_SCENE_MAP：每行一条 `中文口语=script.entity_id`。

    例：
      洗澡模式=script.bath_mode
      睡眠模式=script.sleep_mode
    右侧必须是合法 entity_id；否则该条跳过并记日志。
    """
    out: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        ent = v.strip()
        if not key:
            continue
        if not _ENTITY_RE.match(ent):
            log.warning(jlog("smarthome_scene_bad_entity", key=key[:40],
                             entity=ent[:60]))
            continue
        out[key] = ent
    return out


@dataclass
class HAClient:
    """Home Assistant REST 客户端（httpx.AsyncClient 可注入）。

    依赖注入点：
      - _client：传 MockTransport 即可免真 HA 单测；
      - whitelist / sensitive / scenes：单测可直传，免读 Config。
    """

    base_url: str = ""
    token: str = ""
    agent_id: str = "conversation.home_assistant"
    whitelist: set[str] = field(default_factory=set)
    sensitive: set[str] = field(default_factory=set)
    scenes: dict[str, str] = field(default_factory=dict)
    _client: object | None = None

    def __post_init__(self) -> None:
        if not self.base_url:
            self.base_url = (getattr(Config, "SMARTHOME_HA_URL", "") or "").rstrip("/")
        if not self.token:
            self.token = getattr(Config, "SMARTHOME_HA_TOKEN", "") or ""
        if self.agent_id == "conversation.home_assistant":
            self.agent_id = (getattr(Config, "SMARTHOME_HA_AGENT_ID", "")
                             or "conversation.home_assistant")
        if not self.whitelist:
            self.whitelist = parse_whitelist(
                getattr(Config, "SMARTHOME_DEVICE_WHITELIST", "") or "")
        if not self.sensitive:
            self.sensitive = parse_whitelist(
                getattr(Config, "SMARTHOME_SENSITIVE", "") or "")
        if not self.scenes:
            self.scenes = parse_scene_map(
                getattr(Config, "SMARTHOME_SCENE_MAP", "") or "")

    # ---------------- 基础设施 ----------------

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise HaError("没配 Home Assistant 令牌（SMARTHOME_HA_TOKEN）——"
                          "HA 个人资料页 → 长效访问令牌")
        return {"Authorization": "Bearer %s" % self.token,
                "Content-Type": "application/json"}

    def _check(self, entity_id: str) -> str:
        """白名单校验：不在名单内一律拒绝（HA 侧配错也拦得住）。"""
        e = (entity_id or "").strip()
        if not _ENTITY_RE.match(e):
            raise HaError("设备 ID 格式不对：%r（应形如 light.living_room）" % entity_id)
        if self.whitelist and e not in self.whitelist:
            raise HaError("设备 %s 不在可控白名单里（已授权 %d 个）——要控它先把它"
                          "加进 SMARTHOME_DEVICE_WHITELIST" % (e, len(self.whitelist)))
        return e

    def is_sensitive(self, entity_id: str) -> bool:
        """高敏设备（门锁/摄像头/门禁）→ 调用方须确认主人身份后才放行。"""
        return (entity_id or "").strip() in self.sensitive

    def match_scene(self, text: str) -> str | None:
        """口语命中场景映射 → 返回 script entity_id（确定性快路径）。"""
        t = (text or "").strip()
        if not t:
            return None
        # 精确优先，再包含匹配（「执行洗澡模式」命中「洗澡模式」）
        if t in self.scenes:
            return self.scenes[t]
        for k, v in self.scenes.items():
            if k and k in t:
                return v
        return None

    # ---------------- 设备控制 ----------------

    async def call_service(self, entity_id: str, service: str = "turn_on",
                           data: dict | None = None) -> dict:
        """调 HA 服务控设备。service 形如 turn_on/turn_off/toggle。"""
        e = self._check(entity_id)
        domain = e.split(".", 1)[0]
        body: dict = {"entity_id": e}
        if data:
            body.update(data)
        resp = await self._request("POST", "/api/services/%s/%s" % (domain, service),
                                   json=body)
        return resp

    async def run_scene(self, script_entity: str) -> dict:
        """执行场景脚本（script.turn_on）。script entity 也过白名单。"""
        e = self._check(script_entity)
        return await self._request("POST", "/api/services/script/turn_on",
                                   json={"entity_id": e})

    async def set_attribute(self, entity_id: str, expr: str) -> dict:
        """属性级控制（D2）：`brightness=128` / `temperature=26` / `position=50`。

        先由 build_attr_payload 纯函数校验取值（越界显式报错不夹取），再调域服务。
        """
        e = self._check(entity_id)
        service, data = build_attr_payload(e, expr)
        attr = next(iter(data), "")
        log.info(jlog("smarthome_set_attr", entity=e, service=service, attr=attr))
        return await self.call_service(e, service, data=data)

    async def get_state(self, entity_id: str) -> dict:
        """查设备状态（不过白名单——只读查询允许看白名单外？不：同样校验，
        避免用查询口枚举家庭设备；调用方要查先加白名单）。"""
        e = self._check(entity_id)
        return await self._request("GET", "/api/states/%s" % e)

    async def list_states(self) -> list[dict]:
        """列全部状态（仅管理用途；不过白名单——调用方须已判主人）。"""
        data = await self._request("GET", "/api/states")
        return data if isinstance(data, list) else []

    # ---------------- 自然语言（Conversation Agent） ----------------

    async def converse(self, text: str, language: str = "zh") -> dict:
        """自然语言 → HA Conversation Agent（HA 侧 Ollama 驱动）。

        返回 HA 的 response.speech.plain.speech（人话）。注意：对话代理
        可能返回白名单外 entity——控制类请求一律再过 _check（call_service
        已做），这里只取话术。
        """
        t = (text or "").strip()
        if not t:
            raise HaError("空指令——想控啥直接说，比如「把客厅灯打开」")
        body = {"text": t, "language": language,
                "agent_id": self.agent_id}
        data = await self._request("POST", "/api/conversation/process", json=body)
        speech = ""
        try:
            speech = (((data or {}).get("response") or {}).get("speech")
                      or {}).get("plain", {}).get("speech") or ""
        except Exception:  # noqa: BLE001 — 结构不符时如实报错，不猜
            speech = ""
        if not speech:
            raise HaError("Home Assistant 没回话（对话代理 %s 可能没配好或离线）"
                          % self.agent_id)
        return {"ok": True, "text": t, "speech": speech, "raw": data}

    # ---------------- 传输层 ----------------

    async def _request(self, method: str, path: str, json: dict | None = None) -> object:
        """httpx 请求（显式超时 CLOSE-05；失败显式 HaError，禁静默）。"""
        import httpx

        headers = self._headers()
        timeout = float(getattr(Config, "SMARTHOME_HTTP_TIMEOUT", 10) or 10)
        client = self._client
        own = client is None
        if own:
            client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        try:
            r = await client.request(method, path, json=json, headers=headers)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("smarthome_request_failed", method=method, path=path,
                             error=type(e).__name__))
            raise HaError("连不上 Home Assistant（%s:%s）——查 SMARTHOME_HA_URL "
                          "和网络" % (type(e).__name__, e)) from e
        finally:
            if own:
                await client.aclose()  # type: ignore[attr-defined]
        if r.status_code in (401, 403):
            raise HaError("Home Assistant 令牌无效或过期（HTTP %d）——重新生成"
                          "长效访问令牌" % r.status_code)
        if r.status_code == 404:
            raise HaError("Home Assistant 没有这个接口/设备（HTTP 404：%s）" % path)
        if r.status_code >= 400:
            raise HaError("Home Assistant 报错（HTTP %d）：%s"
                          % (r.status_code, r.text[:200]))
        try:
            return r.json()
        except Exception as e:  # noqa: BLE001
            raise HaError("Home Assistant 返回的不是 JSON（HTTP %d）" % r.status_code) from e


#: 模块级单例（单测各自 new 隔离；token/白名单留空时按 Config 现取）
ha_client = HAClient(base_url="", token="")
