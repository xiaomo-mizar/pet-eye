# -*- coding: utf-8 -*-
"""让桌宠的话不那么同质化：策略轮盘、禁用词、长度/句式、素材、去重打分。"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# --------------------------------------------------------------------------
# 策略卡
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Strategy:
    id: str
    label: str
    hint: str
    weight: float = 1.0
    cooldown: int = 3
    daily_cap: int = 0  # 0 = 不限
    needs_music: bool = False
    needs_long_work: bool = False
    needs_question: bool = False
    needs_anchor: bool = True
    material: str = ""


STRATEGIES: tuple[Strategy, ...] = (
    Strategy(
        "observe",
        "具体观察",
        "点出屏幕里一个具体细节（文件名、报错、按钮、数字、页面标题都行），只观察，不评价。",
        weight=18,
        cooldown=2,
        needs_anchor=True,
    ),
    Strategy(
        "care",
        "陪伴关心",
        "温柔陪伴，但要具体（眼睛、手腕、喝水、坐姿），不要只说“记得休息”。",
        weight=14,
        cooldown=3,
        needs_anchor=False,
    ),
    Strategy(
        "tease",
        "轻微吐槽",
        "损友式轻轻吐槽，不冒犯、不攻击；像熟人开玩笑。",
        weight=12,
        cooldown=3,
        needs_anchor=True,
    ),
    Strategy(
        "ask",
        "轻提问",
        "问一个轻松、具体的小问题；不要问“为什么/进度到哪了”。",
        weight=10,
        cooldown=3,
        needs_anchor=False,
        material="ask",
    ),
    Strategy(
        "fact",
        "冷知识",
        "讲一句鲸鱼/海洋/程序员冷知识，短、有趣，不要上课。",
        weight=8,
        cooldown=4,
        needs_anchor=False,
        material="fact",
    ),
    Strategy(
        "praise",
        "具体夸夸",
        "夸一个具体的动作或细节，不要空夸“真棒/厉害”。",
        weight=7,
        cooldown=4,
        needs_anchor=True,
    ),
    Strategy(
        "story",
        "小画面",
        "一句小故事/小画面/文艺比喻，不要堆形容词，不要鸡汤。",
        weight=6,
        cooldown=5,
        needs_anchor=False,
        material="story",
    ),
    Strategy(
        "tsundere",
        "傲娇",
        "先嘴硬再关心，嘴硬是装的，别真的凶。",
        weight=6,
        cooldown=5,
        needs_anchor=False,
    ),
    Strategy(
        "jealous",
        "吃醋",
        "轻微吃醋（对游戏、工作或歌），可爱但不压迫。",
        weight=5,
        cooldown=5,
        needs_anchor=False,
    ),
    Strategy(
        "guess",
        "猜心思",
        "猜主人此刻在想什么/下一步要干嘛；猜错也没关系。",
        weight=5,
        cooldown=5,
        needs_anchor=False,
    ),
    Strategy(
        "sing",
        "哼歌词",
        "用「」引用 1~2 句正在听的歌的真实歌词（总共不超过 20 字），再加半句自己的话；优先引用真实歌词，只有完全记不清时才用「啦啦啦」代替，不要编。",
        weight=10,
        cooldown=4,
        needs_music=True,
        needs_anchor=False,
    ),
    Strategy(
        "remind",
        "提醒休息",
        "结合连续使用时长提醒休息/喝水/起身；一天最多 2 次。",
        weight=2,
        cooldown=8,
        daily_cap=2,
        needs_long_work=True,
        needs_anchor=False,
    ),
    Strategy(
        "callback",
        "追问上一句",
        "追问上一轮自己问过的问题，或接着上一轮的话题往下聊。",
        weight=5,
        cooldown=2,
        needs_question=True,
        needs_anchor=False,
    ),
)

STRATEGY_BY_ID = {s.id: s for s in STRATEGIES}
# 表情包不是文字策略，只用于统计/反馈显示
STRATEGY_BY_ID["meme"] = Strategy("meme", "表情包", "发一张表情包", weight=0, needs_anchor=False)

PATTERNS = ("陈述", "提问", "感叹", "耳语")

# 这些词一旦在最近几句反复出现，就临时禁用
_COMMON_PET_WORDS = (
    "本鱼",
    "陪着",
    "陪你",
    "慢慢",
    "累了",
    "休息",
    "乖乖",
    "别太",
    "加油",
    "尾巴",
    "白饭",
    "哦",
    "呀",
    "嘛",
    "😊",
    "🐟",
    "🌙",
)

# 这几个单字语气词太常用，硬禁会把整批候选打回
_SOFT_PARTICLES = frozenset({"哦", "呀", "嘛"})

# --------------------------------------------------------------------------
# 素材库
# --------------------------------------------------------------------------

MATERIALS: dict[str, tuple[str, ...]] = {
    "fact": (
        "蓝鲸的舌头比一头大象还重，心脏有小汽车那么大。",
        "抹香鲸能潜到两千米深，憋气一个多小时。",
        "鲸鱼睡觉只关半个脑子，另一半负责记得浮上来换气。",
        "座头鲸的歌每年都会换新调子，像在发新专辑。",
        "虎鲸其实是海豚科，不是鲸。",
        "鲸落：一头鲸沉下去，能养活深海一片生态几十年。",
        "海豚会给自己起名字，用独特的哨声互相称呼。",
        "世界上第一只“bug”真的是一只飞蛾，粘在继电器上。",
        "Python 的名字来自喜剧团体 Monty Python，不是蛇。",
        "0.1 + 0.2 在浮点数里不等于 0.3，计算机也会犯小迷糊。",
        "git blame 不是骂人，它只是帮你找出这行是谁写的。",
        "键盘上的 Ctrl+Z 是 undo，鲸鱼没有 undo，所以它很谨慎。",
    ),
    "ask": (
        "如果今天能提前一小时收工，你最想干嘛？",
        "你最近单曲循环的是哪一首？",
        "如果本鱼能变成一种饮料，你觉得会是什么味道？",
        "明天早上想吃什么，本鱼提前帮你想好。",
        "你桌面上最舍不得删的文件是哪个？",
        "如果现在能瞬移，你想去哪儿待十分钟？",
        "你最近有没有哪件小事，其实做得挺得意的？",
        "如果给今天起个标题，你会写什么？",
        "你现在最想听到谁的声音？",
        "如果手边突然多一杯热饮，你选茶还是牛奶？",
    ),
    "story": (
        "深海里有条鲸，把月光当成浮游生物，追了一整夜。",
        "小肥鱼把尾巴泡进茶杯，说这样就能喝到自己的味道。",
        "路灯替晚归的人留了一小圈暖黄，像没关掉的对话。",
        "雨落在窗上，把整座城市擦成一张没写完的草稿。",
        "鲸鱼经过港口时会放慢速度，怕吵醒睡着的船。",
        "本鱼把今天的好消息折成小船，放在你的键盘旁边。",
        "凌晨四点的风很轻，像有人替你把门带上。",
        "海面下三米，光变成碎银子，鱼群从银子里穿过去。",
        "一只小螃蟹横着走，只为把落日看得久一点。",
        "杯子里的热气往上飘，飘到一半就舍不得走了。",
    ),
    "food": (
        "大白饭是碳水的温柔，配什么都像回家。",
        "刚出锅的米饭会冒白气，像一小朵会散的云。",
        "本鱼可以为了半碗白饭，原谅今天所有报错。",
        "饭要一口一口吃，bug 要一行一行改，都是这个道理。",
        "如果今天很累，就吃热一点的，胃会替你先缓过来。",
        "白饭配酱油是极简，配红烧肉是庆典。",
        "鲸鱼一顿能吃很多磷虾，本鱼一顿能吃掉一碗白饭。",
        "别空着肚子改代码，饿的时候连变量名都会变凶。",
    ),
}


def pick_material(kind: str, recent_ids: list[str], rng: random.Random) -> tuple[str, str]:
    items = MATERIALS.get(kind) or ()
    if not items:
        return "", ""
    choices = [(i, text) for i, text in enumerate(items) if f"{kind}:{i}" not in recent_ids]
    if not choices:
        choices = list(enumerate(items))
    index, text = rng.choice(choices)
    return text, f"{kind}:{index}"


# --------------------------------------------------------------------------
# 文本工具
# --------------------------------------------------------------------------


def normalize(text: str) -> str:
    return re.sub(r"[\s，。！？；：、,.!?;:…—～~“”‘’「」『』（）()\[\]【】]+", "", text or "")


def ngrams(text: str, n: int = 3) -> set[str]:
    s = normalize(text)
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def similarity(a: str, b: str) -> float:
    ga, gb = ngrams(a), ngrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / max(1, len(ga | gb))


def max_similarity(line: str, recent: list[str]) -> float:
    return max((similarity(line, x) for x in recent if x), default=0.0)


def banned_words(recent_lines: list[str]) -> list[str]:
    window = [x for x in recent_lines if x][-6:]
    banned: list[str] = []
    for word in _COMMON_PET_WORDS:
        # 单字语气词一禁就误伤一大片（“哦/呀/嘛”），只在提示词里劝，不硬禁
        if word in _SOFT_PARTICLES:
            continue
        hits = sum(1 for line in window if word in line)
        if hits >= 2:
            banned.append(word)
    return banned[:6]


def banned_openings(recent_lines: list[str]) -> list[str]:
    openings: list[str] = []
    for line in [x for x in recent_lines if x][-5:]:
        head = line.strip()[:3]
        if len(head) >= 2 and head not in openings:
            openings.append(head)
    if sum(1 for line in recent_lines[-4:] if line.startswith(("本鱼", "宝宝", "主人"))) >= 2:
        for word in ("本鱼", "宝宝", "主人"):
            if word not in openings:
                openings.append(word)
    return openings[:5]


def choose_length_bucket(recent_lines: list[str], rng: random.Random) -> tuple[int, int]:
    lengths = [len(x) for x in recent_lines[-3:] if x]
    avg = sum(lengths) / len(lengths) if lengths else 30.0
    if avg >= 36:
        return 20, 28
    if avg <= 26:
        return 34, 48
    return rng.choice([(20, 28), (26, 35), (36, 50)])


def choose_pattern(recent_patterns: list[str], rng: random.Random) -> str:
    recent = [p for p in recent_patterns[-2:] if p]
    choices = [p for p in PATTERNS if p not in recent] or list(PATTERNS)
    return rng.choice(choices)


def pattern_ok(line: str, pattern: str) -> bool:
    if pattern == "提问":
        return line.rstrip("」』”’ ").endswith(("？", "?"))
    if pattern == "感叹":
        return line.rstrip("」』”’ ").endswith(("！", "!"))
    if pattern == "耳语":
        return ("…" in line) or ("~" in line) or ("～" in line)
    return True


def screen_digest(prev_hash: str, cur_hash: str, threshold: int = 4) -> tuple[str, bool]:
    if not prev_hash or not cur_hash:
        return "", False
    try:
        diff = int(prev_hash, 16) ^ int(cur_hash, 16)
    except ValueError:
        return "", False
    if diff.bit_count() <= threshold:
        return "", False

    counts = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    for index in range(64):
        if (diff >> (63 - index)) & 1:
            row, col = divmod(index, 8)
            counts[min(2, row // 3)][min(2, col // 3)] += 1

    names = (("左上", "上中", "右上"), ("左中", "中间", "右中"), ("左下", "下中", "右下"))
    best = (0, 1, 1)
    for r in range(3):
        for c in range(3):
            if counts[r][c] > best[0]:
                best = (counts[r][c], r, c)
    if best[0] >= 4:
        return f"{names[best[1]][best[2]]}变化明显", True
    return "整屏变化分散", True


# --------------------------------------------------------------------------
# 计划
# --------------------------------------------------------------------------


@dataclass
class TurnPlan:
    strategies: list[Strategy]
    bucket: tuple[int, int]
    pattern: str
    material: str = ""
    material_id: str = ""
    banned_words: list[str] = field(default_factory=list)
    banned_openings: list[str] = field(default_factory=list)
    callback: str = ""
    music_title: str = ""
    music_artist: str = ""
    digest: str = ""
    changed: bool = False
    same_app_min: float = 0.0
    stale_min: float = 0.0
    similarity_threshold: float = 0.55
    anchor_tokens: set[str] = field(default_factory=set)

    @property
    def strategy_ids(self) -> list[str]:
        return [s.id for s in self.strategies]

    def strategy(self, sid: str) -> Strategy:
        return STRATEGY_BY_ID.get(sid) or self.strategies[0]


def _today(ts: float) -> str:
    return datetime.fromtimestamp(ts).date().isoformat()


def _eligible(
    strategy: Strategy,
    *,
    now: float,
    recent_ids: list[str],
    recent_meta: list[dict],
    feedback: dict,
    music: bool,
    long_work: bool,
    has_question: bool,
    stale_min: float,
) -> bool:
    if strategy.needs_music and not music:
        return False
    if strategy.needs_long_work and not long_work:
        return False
    if strategy.needs_question and not has_question:
        return False
    if strategy.cooldown:
        recent = recent_ids[-strategy.cooldown :]
        if strategy.id in recent:
            return False
    if strategy.daily_cap:
        today = _today(now)
        used = sum(
            1
            for item in recent_meta
            if item.get("strategy") == strategy.id and _today(float(item.get("at") or 0)) == today
        )
        if used >= strategy.daily_cap:
            return False
    # 同一屏超过 10 分钟：不再观察/吐槽具体画面，改聊别的
    if stale_min >= 10 and strategy.id in {"observe", "praise", "tease", "guess"}:
        return False
    return True


def _weight(strategy: Strategy, feedback: dict) -> float:
    stat = feedback.get(strategy.id) or {}
    likes = int(stat.get("like") or 0)
    dislikes = int(stat.get("dislike") or 0)
    factor = 1.0 + 0.2 * likes - 0.3 * dislikes
    return max(0.2, min(3.0, factor)) * strategy.weight


def _weighted_pick(
    pool: list[Strategy], feedback: dict, rng: random.Random
) -> Strategy | None:
    if not pool:
        return None
    weights = [_weight(s, feedback) for s in pool]
    total = sum(weights)
    if total <= 0:
        return rng.choice(pool)
    r = rng.uniform(0, total)
    for strategy, weight in zip(pool, weights):
        r -= weight
        if r <= 0:
            return strategy
    return pool[-1]


def _anchor_tokens(scene: Any) -> set[str]:
    texts: list[str] = []
    for value in (
        getattr(scene, "window_title", ""),
        getattr(scene, "app", ""),
        getattr(scene, "category", ""),
        getattr(scene, "game", ""),
    ):
        if value:
            texts.append(str(value))
    media = getattr(scene, "media", None)
    if media:
        texts.append(getattr(media, "title", "") or "")
        texts.append(getattr(media, "artist", "") or "")
    tokens: set[str] = set()
    for text in texts:
        for chunk in re.split(r"[\s\-—_|/\\·:：,，。.!?！？()（）\[\]【】]+", text):
            chunk = chunk.strip()
            if len(chunk) < 2:
                continue
            if len(chunk) <= 4:
                tokens.add(chunk)
            else:
                tokens.update(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return tokens


def plan_turn(
    scene: Any,
    *,
    now: float,
    recent_lines: list[str],
    recent_meta: list[dict],
    feedback: dict,
    pending_question: str = "",
    pending_at: float = 0.0,
    recent_materials: list[str] | None = None,
    same_app_min: float = 0.0,
    stale_min: float = 0.0,
    digest: str = "",
    changed: bool = False,
    candidates: int = 3,
    music_lyrics: bool = True,
    material_bank: bool = True,
    callback_enabled: bool = True,
    similarity_threshold: float = 0.55,
    rng: random.Random | None = None,
) -> TurnPlan:
    rng = rng or random.Random()
    hour = datetime.fromtimestamp(now).hour
    media = getattr(scene, "media", None)
    music = bool(
        media
        and getattr(media, "kind", "") == "music"
        and getattr(media, "playing", None) is not False
    )
    long_work = same_app_min >= 45 or hour >= 23 or hour < 5
    has_question = bool(
        callback_enabled
        and pending_question
        and pending_at
        and now - pending_at <= 900
    )

    recent_ids = [str(item.get("strategy") or "") for item in recent_meta][-12:]
    pool = [
        s
        for s in STRATEGIES
        if not (s.id == "sing" and not music_lyrics)
        and _eligible(
            s,
            now=now,
            recent_ids=recent_ids,
            recent_meta=recent_meta,
            feedback=feedback,
            music=music,
            long_work=long_work,
            has_question=has_question,
            stale_min=stale_min,
        )
    ]
    if not pool:
        pool = [STRATEGY_BY_ID["care"]]

    chosen: list[Strategy] = []
    # 在听歌时，让“哼歌词”有较高概率进入候选
    if music and music_lyrics and candidates >= 2 and rng.random() < 0.65:
        sing = STRATEGY_BY_ID["sing"]
        if sing in pool:
            chosen.append(sing)
            pool = [s for s in pool if s.id != "sing"]
    # 有上一轮问题时，偶尔把“追问”塞进来
    if has_question and callback_enabled and rng.random() < 0.5:
        callback = STRATEGY_BY_ID["callback"]
        if callback in pool:
            chosen.append(callback)
            pool = [s for s in pool if s.id != "callback"]

    while len(chosen) < candidates and pool:
        pick = _weighted_pick(pool, feedback, rng)
        if pick is None:
            break
        chosen.append(pick)
        pool = [s for s in pool if s.id != pick.id]
    if not chosen:
        chosen = [STRATEGY_BY_ID["care"]]
    # 去重并保持顺序
    seen: set[str] = set()
    chosen = [s for s in chosen if not (s.id in seen or seen.add(s.id))]

    pattern = choose_pattern([str(m.get("pattern") or "") for m in recent_meta], rng)
    if any(s.id in {"ask", "callback"} for s in chosen) and rng.random() < 0.7:
        pattern = "提问"
    if any(s.id == "sing" for s in chosen) and rng.random() < 0.5:
        pattern = "耳语"

    material = ""
    material_id = ""
    if material_bank:
        for strategy in chosen:
            if strategy.material:
                material, material_id = pick_material(strategy.material, recent_materials or [], rng)
                if material:
                    break

    music_title = getattr(media, "title", "") if media else ""
    music_artist = getattr(media, "artist", "") if media else ""

    return TurnPlan(
        strategies=chosen,
        bucket=choose_length_bucket(recent_lines, rng),
        pattern=pattern,
        material=material,
        material_id=material_id,
        banned_words=banned_words(recent_lines),
        banned_openings=banned_openings(recent_lines),
        callback=pending_question if has_question else "",
        music_title=music_title or "",
        music_artist=music_artist or "",
        digest=digest,
        changed=changed,
        same_app_min=same_app_min,
        stale_min=stale_min,
        similarity_threshold=similarity_threshold,
        anchor_tokens=_anchor_tokens(scene),
    )


# --------------------------------------------------------------------------
# 候选打分
# --------------------------------------------------------------------------


def candidate_ok(line: str, plan: TurnPlan) -> bool:
    """禁用词/开头/句式是硬约束，不能只靠扣分。"""
    if any(word in line for word in plan.banned_words):
        return False
    if any(line.startswith(head) for head in plan.banned_openings):
        return False
    return pattern_ok(line, plan.pattern)


def score_candidate(
    line: str,
    strategy_id: str,
    plan: TurnPlan,
    recent_lines: list[str],
) -> float:
    strategy = plan.strategy(strategy_id)
    sim = max_similarity(line, recent_lines)
    novelty = 1.0 - sim
    hits = len(ngrams(line, 2) & plan.anchor_tokens)
    anchor = min(1.0, hits / 3.0)
    lo, hi = plan.bucket
    bucket = 1.0 if lo <= len(line) <= hi else 0.0
    pattern = 1.0 if pattern_ok(line, plan.pattern) else 0.0
    banned = sum(1 for word in plan.banned_words if word in line)
    opening = 1.0 if any(line.startswith(head) for head in plan.banned_openings) else 0.0

    score = 2.4 * novelty + 1.0 * anchor + 0.8 * bucket + 0.5 * pattern
    score -= 1.8 * banned
    score -= 2.0 * opening
    if strategy.needs_anchor and hits == 0:
        score -= 0.6
    if strategy.id == "sing":
        match = re.search(r"「(.+?)」", line)
        if match:
            lyric = match.group(1)
            score += 1.0 if len(lyric) >= 4 and "啦啦" not in lyric else 0.3
        else:
            score -= 0.8
    score += 0.03 * strategy.weight
    return score


def stats_summary(history: list[dict], days: int = 7) -> str:
    cutoff = datetime.now().timestamp() - days * 86400
    rows = [h for h in history if float(h.get("at") or 0) >= cutoff]
    if not rows:
        return "最近还没有记录，等本鱼多说几句再看吧。"
    total = len(rows)
    counts: dict[str, int] = {}
    likes = dislikes = 0
    for row in rows:
        sid = str(row.get("strategy") or "unknown")
        counts[sid] = counts.get(sid, 0) + 1
        if row.get("feedback") == "like":
            likes += 1
        elif row.get("feedback") == "dislike":
            dislikes += 1
    parts = []
    for sid, count in sorted(counts.items(), key=lambda x: -x[1])[:6]:
        label = STRATEGY_BY_ID.get(sid).label if sid in STRATEGY_BY_ID else sid
        parts.append(f"{label} {count}（{count * 100 // total}%）")
    openings: dict[str, int] = {}
    for row in rows[-80:]:
        head = str(row.get("line") or "")[:2]
        if head:
            openings[head] = openings.get(head, 0) + 1
    top_open = "、".join(f"{k}×{v}" for k, v in sorted(openings.items(), key=lambda x: -x[1])[:3])
    return (
        f"最近 {days} 天：{total} 句\n"
        f"{' / '.join(parts)}\n"
        f"反馈：喜欢 {likes}，一般 {dislikes}\n"
        f"高频开头：{top_open or '无'}"
    )
