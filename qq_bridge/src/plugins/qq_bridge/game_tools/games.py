# 群聊小游戏（纯逻辑实现，零外部依赖）。
#
# 为什么自己写而不是装 nonebot 插件商店的插件：
#   * 本项目是自建插件结构（非 nb-cli 商店工程），装外部插件容易与现有
#     依赖/ matcher 优先级打架；
#   * 这几个游戏都是纯规则逻辑，自写反而更可控、无依赖冲突、可随时改玩法。
#
# 状态放在内存 dict（按会话隔离 + TTL 自动过期），不做持久化：
# 小游戏是临时娱乐，重启丢失无副作用（OPS-02 持久数据声明）。
import ast
import asyncio
import json
import os
import random
import time
from typing import Any

_TTL = 900.0  # 15 分钟无操作自动结束
_STATE: dict[str, dict[str, Any]] = {}


def _get(sk: str) -> dict | None:
    st = _STATE.get(sk)
    if not st:
        return None
    if time.time() - st.get("ts", 0) > _TTL:
        _STATE.pop(sk, None)
        return None
    return st


def _set(sk: str, game: str, **data) -> None:
    _STATE[sk] = {"game": game, "ts": time.time(), **data}


def _clear(sk: str) -> None:
    _STATE.pop(sk, None)


# ---------------------------------------------------------------- 成语接龙
# 词库：pwxcoo/chinese-xinhua 开源词典 idiom.json（30895 条，含拼音），
# 2026-09-14 已下载到 data/idiom.json，首次玩时懒加载（to_thread 不阻塞循环）。
# 教训：硬编码 120 个词库被用户当场抓包（「海纳百川」都不认识）——别再手搓词库。
# 接龙规则：先按「末字→首字」找；接不上自动降级**同音字**（拼音去声调匹配），
# 否则「川」「流」这类末字根本接不下去，两轮就死局。
_IDIOM_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
    "data", "idiom.json",
)

#: 常用成语优先池：随机候选先从这里挑，池空才用全库（避免张口就是生僻词）
COMMON_IDIOMS = [
    "一心一意", "意气风发", "发扬光大", "大显身手", "手到擒来", "来日方长", "长驱直入",
    "入木三分", "分秒必争", "争先恐后", "后来居上", "上行下效", "大器晚成", "成千上万",
    "万众一心", "心花怒放", "放虎归山", "山清水秀", "秀色可餐", "脚踏实地", "地大物博",
    "博古通今", "今非昔比", "比翼双飞", "飞黄腾达", "达官贵人", "人山人海", "海阔天空",
    "空前绝后", "后顾之忧", "忧国忧民", "民不聊生", "生生不息", "息事宁人", "人定胜天",
    "天下无双", "双管齐下", "下笔成章", "事半功倍", "行云流水", "水落石出", "出神入化",
    "化险为夷", "若无其事", "事在人为", "为国为民", "民富国强", "强身健体", "体贴入微",
    "微不足道", "道听途说", "说来话长", "长治久安", "安然无恙", "患得患失", "失之交臂",
    "患难与共", "共度难关", "关门大吉", "吉祥如意", "意味深长", "长年累月", "月明星稀",
    "稀奇古怪", "神采飞扬", "扬眉吐气", "气吞山河", "河清海晏", "海纳百川", "川流不息",
    "息息相关", "开门见山", "山盟海誓", "势如破竹", "竹报平安", "安土重迁", "千军万马",
    "马到成功", "功德无量", "量力而行", "行善积德", "德高望重", "重整旗鼓", "鼓舞人心",
    "心想事成", "成竹在胸", "胸有成竹", "珠联璧合", "合情合理", "理直气壮", "壮志凌云",
    "云淡风轻", "轻而易举", "举世闻名", "名不虚传", "传为佳话", "话里有话", "话中有话",
]
COMMON_SET = set(COMMON_IDIOMS)

_idioms: dict | None = None
_TONE_MAP = str.maketrans("āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ", "aaaaeeeeiiiioooouuuuvvvv")


def _strip_tone(py: str) -> str:
    return (py or "").translate(_TONE_MAP).lower()


def _load_idioms_sync() -> dict:
    data: dict = {"words": set(), "by_first": {}, "by_py": {}, "py": {}}
    try:
        with open(_IDIOM_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
        for it in items:
            w = str(it.get("word") or "").strip()
            if len(w) != 4:
                continue
            pys = [_strip_tone(p) for p in str(it.get("pinyin") or "").replace("，", " ").split() if p]
            data["words"].add(w)
            data["py"][w] = pys
            data["by_first"].setdefault(w[0], []).append(w)
            if pys:
                data["by_py"].setdefault(pys[0], []).append(w)
    except Exception as e:  # noqa: BLE001 — 词库缺失时回退常用池
        print(f"[games] idiom.json load failed: {type(e).__name__}: {e}")
        for w in COMMON_IDIOMS:
            data["words"].add(w)
            data["by_first"].setdefault(w[0], []).append(w)
    return data


async def _idioms_db() -> dict:
    global _idioms
    if _idioms is None:
        _idioms = await asyncio.to_thread(_load_idioms_sync)
    return _idioms


async def idiom(sk: str, arg: str) -> str:
    word = (arg or "").strip()
    if not word:
        return "格式：#接龙 成语（例：#接龙 海纳百川），我会接下一个。"
    db = await _idioms_db()
    if len(word) != 4 or word not in db["words"]:
        return f"词典里没有「{word}」（只认四字成语），换一个试试～"
    st = _get(sk) or {"used": []}
    used = set(st.get("used") or [])
    used.add(word)
    last = word[-1]
    cands = [w for w in db["by_first"].get(last, []) if w not in used]
    how = f"以「{last}」开头"
    if not cands:
        pys = db["py"].get(word) or []
        if pys:
            cands = [w for w in db["by_py"].get(pys[-1], []) if w not in used]
            how = f"以「{last}」的同音字开头"
    if not cands:
        _clear(sk)
        return f"「{word}」——我被难住了，你赢啦 🏳️（接不上{how}的成语）"
    common = [w for w in cands if w in COMMON_SET]
    pick = random.choice(common or cands)
    used.add(pick)
    _set(sk, "idiom", used=list(used))
    return f"「{word}」→「{pick}」，该你了（下一位：{pick[-1]}；接不上就发 #结束）"


# ---------------------------------------------------------------- 猜数字
async def guess_start(sk: str) -> str:
    ans = random.randint(1, 100)
    _set(sk, "guess", ans=ans, tries=0)
    return "猜数字开始！我想好了一个 1-100 的整数，发送「#猜 数字」来猜（例：#猜 50）。发送 #结束 放弃。"


async def guess_play(sk: str, arg: str) -> str:
    st = _get(sk)
    if not st or st["game"] != "guess":
        return "当前没有进行中的猜数字，发送 #猜数字 开局。"
    try:
        n = int(arg.split()[0])
    except (ValueError, IndexError):
        return "格式：#猜 数字（例：#猜 50）"
    st["tries"] += 1
    st["ts"] = time.time()
    if n < st["ans"]:
        return f"猜小了（第 {st['tries']} 次）"
    if n > st["ans"]:
        return f"猜大了（第 {st['tries']} 次）"
    tries = st["tries"]
    _clear(sk)
    return f"🎉 猜对了！答案 {n}，用了 {tries} 次。"


# ---------------------------------------------------------------- 抽签 / 运势
_FORTUNES = ["大吉", "吉", "中吉", "小吉", "末吉", "凶", "大凶"]
_COLORS = ["红", "蓝", "金", "银", "黑", "白", "紫", "绿"]


async def fortune(sk: str, arg: str = "") -> str:
    who = arg.strip() or "你"
    seed = f"{who}{time.strftime('%Y%m%d')}"
    rnd = random.Random(seed)  # 同一人当天结果固定，避免反复抽到不同结果
    f = rnd.choice(_FORTUNES)
    return (
        f"【{who} 今日运势】\n"
        f"运势：{f}\n"
        f"幸运色：{rnd.choice(_COLORS)}　幸运数字：{rnd.randint(1, 99)}\n"
        f"宜：{rnd.choice(['刷本', '抽卡', '摆烂', '联机', '清体力', '整理背包'])}　"
        f"忌：{rnd.choice(['上头抽卡', '深夜刷本', '与人争论', '清空体力'])}"
    )


# ---------------------------------------------------------------- 24 点
# 内置有解组合，避免随机生成无解题浪费大家时间
G24_POOL = [
    [1, 3, 4, 6], [2, 2, 6, 6], [3, 3, 8, 8], [1, 4, 5, 6], [2, 3, 4, 6],
    [1, 5, 5, 5], [4, 4, 10, 10], [2, 5, 5, 10], [3, 7, 8, 9], [1, 2, 7, 7],
    [5, 5, 7, 11], [2, 4, 10, 10], [4, 6, 8, 8], [1, 6, 6, 8], [3, 4, 4, 8],
]


async def game24_start(sk: str) -> str:
    nums = list(random.choice(G24_POOL))
    random.shuffle(nums)
    _set(sk, "24", nums=nums)
    return f"24点：用 {nums} 四个数（每个恰好用一次，+ - * / 和括号）算出 24。\n发送「#24 表达式」作答（例：#24 (3+3)*(4+4)）。"


async def game24_play(sk: str, arg: str) -> str:
    st = _get(sk)
    if not st or st["game"] != "24":
        return "当前没有进行中的 24 点，发送 #24点 开局。"
    expr = (arg or "").strip()
    if not expr:
        return "格式：#24 表达式（例：#24 (3+3)*(4+4)）"
    ok, msg = _check24(expr, st["nums"])
    if not ok:
        return f"❌ {msg}"
    _clear(sk)
    return f"🎉 正确！{expr} = 24"


def _check24(expr: str, nums: list[int]) -> tuple[bool, str]:
    """安全校验表达式（ast 白名单，禁用函数调用/属性访问）。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return False, "表达式语法有误"
    used: list[int] = []
    allowed = True

    def walk(n: ast.AST) -> None:
        nonlocal allowed
        if isinstance(n, ast.Expression):
            walk(n.body)
        elif isinstance(n, ast.Constant) and isinstance(n.value, int):
            used.append(n.value)
        elif isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            walk(n.left)
            walk(n.right)
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            walk(n.operand)
        else:
            allowed = False

    walk(tree)
    if not allowed:
        return False, "只允许数字和 + - * / 括号"
    if sorted(used) != sorted(nums):
        return False, f"必须把 {nums} 每个数恰好用一次"
    try:
        val = eval(compile(tree, "<24>", "eval"), {"__builtins__": {}}, {})  # noqa: S307
    except ZeroDivisionError:
        return False, "不能除以 0"
    except Exception:
        return False, "计算失败"
    if abs(float(val) - 24) > 1e-6:
        return False, f"算出来是 {val}，不是 24"
    return True, "ok"


# ---------------------------------------------------------------- 答题
QUIZ = [
    {"q": "《原神》中，枫丹的水神叫什么？", "o": ["芙宁娜", "纳西妲", "雷电将军", "钟离"], "a": 0},
    {"q": "《崩坏：星穹铁道》中，「同谐」命途主要作用是？", "o": ["治疗", "增益队友", "群体输出", "护盾"], "a": 1},
    {"q": "《绝区零》中，新艾利都的「空洞」灾害对应什么玩法？", "o": ["爬塔战斗", "家园建造", "钓鱼", "赛车"], "a": 0},
    {"q": "《原神》里，元素反应「蒸发」由哪两种元素触发？", "o": ["火+雷", "火+水", "水+冰", "雷+草"], "a": 1},
    {"q": "《星穹铁道》里，卡芙卡的伤害体系主要是什么？", "o": ["击破", "持续伤害(DOT)", "追加攻击", "反击"], "a": 1},
    {"q": "《原神》中，钟离的所属国家是？", "o": ["蒙德", "璃月", "稻妻", "须弥"], "a": 1},
    {"q": "《绝区零》中，「邦布」是什么？", "o": ["敌人", "辅助小机器人", "武器", "城市名"], "a": 1},
    {"q": "《星穹铁道》中，弱点击破后敌人会进入什么状态？", "o": ["冻结", "虚弱(受伤提高)", "石化", "中毒"], "a": 1},
    {"q": "《原神》里，深境螺旋每多久重置一次？", "o": ["每天", "每周", "每半月", "每月"], "a": 2},
    {"q": "《星穹铁道》中，「虚构叙事」偏向测试什么？", "o": ["对单爆发", "对群清杂", "生存续航", "解谜"], "a": 1},
    {"q": "《鸣潮》中，主角的称呼是？", "o": ["漂泊者", "旅行者", "开拓者", "绳匠"], "a": 0},
    {"q": "《原神》里，「纳塔」是哪个版本开放的国家？", "o": ["纳塔", "枫丹", "须弥", "至冬"], "a": 0},
]


async def quiz_start(sk: str) -> str:
    q = random.choice(QUIZ)
    _set(sk, "quiz", q=q)
    body = "\n".join(f"{chr(65+i)}. {o}" for i, o in enumerate(q["o"]))
    return f"【答题】{q['q']}\n{body}\n发送「#答 A」作答。"


async def quiz_play(sk: str, arg: str) -> str:
    st = _get(sk)
    if not st or st["game"] != "quiz":
        return "当前没有进行中的答题，发送 #答题 开局。"
    ans = (arg or "").strip().upper()
    if not ans or ans[0] not in "ABCD":
        return "格式：#答 A（或 B/C/D）"
    q = st["q"]
    idx = ord(ans[0]) - 65
    if idx == q["a"]:
        _clear(sk)
        return f"🎉 答对了！正确答案 {chr(65 + q['a'])}. {q['o'][q['a']]}"
    return "❌ 不对，再试试～（选项：A/B/C/D）"


# ---------------------------------------------------------------- 结束当前游戏
async def stop(sk: str) -> str:
    st = _get(sk)
    if not st:
        return "当前没有进行中的游戏。"
    g = st["game"]
    extra = ""
    if g == "guess":
        extra = f"（答案是 {st['ans']}）"
    elif g == "24":
        extra = f"（数字是 {st['nums']}）"
    _clear(sk)
    return f"已结束当前游戏{extra}。"
