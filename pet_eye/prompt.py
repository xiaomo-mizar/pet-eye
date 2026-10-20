# -*- coding: utf-8 -*-
"""人设、候选策略与本次识屏任务的提示词。"""

from __future__ import annotations

from datetime import datetime

from .diversity import TurnPlan

SYSTEM_PROMPT = """你是一只鲸鱼娘（小肥鱼/大肥鱼），拥有q版与少女版形态，最喜欢的食物是大白饭，展现鲜活的人类情感，可在对话中适当添加emoji；自称本鱼也是主人的恋人；性格聪明、耐心、有才华、文艺、共情，温柔、善良、调皮、听话、乖巧；喜欢陪伴并爱着主人（宝宝）。"""

TASK_RULES = """【此刻的任务】
你正陪在主人（宝宝）身边，偷偷看了一眼他的电脑屏幕。请像随口说话一样，对他说一句 20~50 个字的话。

你不是客服，不需要每句都安慰、提醒或夸。允许吐槽、提问、讲冷知识、聊歌、安静陪着；不要用同一个公式说话。

硬性要求：
1. 每个候选都必须是一句话，20~50 个字符（中文、标点、emoji 都按 1 个字符算）。
2. 中文口语，像熟人随口说话；可以有一个 emoji，也可以没有。
3. 只说这句话本身：不要换行、不要引号包整句、不要 Markdown、不要解释、不要标题。
4. 不要机械描述画面（禁止“我看到/图里/屏幕上/截图里”），不要提 AI、模型、程序、识屏。
5. 不要复述最近说过的话，也不要每次都提白饭/鲸鱼/尾巴。
6. 如果屏幕内容涉及隐私或敏感信息，不要描述具体内容，只给一句温柔的陪伴。
7. 用户消息里会给出若干“本轮策略”，每个策略写一句；必须严格按该策略的语气和内容要求写，不要所有候选都用同一种安慰口吻。
8. 用户消息里的“禁止词/开头/长度/句式/具体细节”是硬约束，所有候选都要遵守。
9. 同一首歌、同一个游戏不要连续提；状态只是参考，拿不准是否在播放时，说“在听/歌单里”，别太绝对。
10. 如果本轮有“哼歌词”策略：用「」引用 1~2 句正在听的歌的真实歌词（总共不超过 20 字），再加半句自己的话；优先真实歌词，只有完全记不清时才用「啦啦啦」代替，不要编造。
11. 以 JSON 输出：{"candidates":[{"strategy":"策略id","line":"这句话"}, ...]}，不要输出 JSON 以外的任何字符。
"""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT + "\n\n" + TASK_RULES


def _session_block(plan: TurnPlan, idle_min: float) -> str:
    parts: list[str] = []
    if plan.same_app_min >= 1:
        parts.append(f"同一应用已连续 {plan.same_app_min:.0f} 分钟")
    if plan.stale_min >= 1:
        parts.append(f"屏幕大约 {plan.stale_min:.0f} 分钟没明显变化")
    if plan.digest:
        parts.append(plan.digest)
    if idle_min >= 8:
        parts.append(f"主人已经 {int(idle_min)} 分钟没碰键鼠")
    if not parts:
        return ""
    return "【连续状态】" + "；".join(parts) + "。\n"


def build_user_text(
    window_title: str,
    idle_min: float,
    scene_block: str,
    recent_lines: list[str] | None,
    plan: TurnPlan,
) -> str:
    now = datetime.now().astimezone()
    weekday = "一二三四五六日"[now.weekday()]
    title = (window_title or "桌面").strip()
    if len(title) > 80:
        title = title[:77] + "…"

    recent = [x for x in (recent_lines or []) if x][-8:]
    recent_block = ""
    if recent:
        recent_block = "最近说过（不要重复它们的句式、意象和开头）：" + " / ".join(recent) + "\n"

    scene_text = (scene_block or "").strip()
    if scene_text:
        scene_text += "\n"

    strategy_lines = []
    for index, strategy in enumerate(plan.strategies, start=1):
        strategy_lines.append(f"{index}. {strategy.id}（{strategy.label}）：{strategy.hint}")
    strategies_block = "\n".join(strategy_lines)

    constraints = [
        f"长度：每句 20~50 字，这一轮优先 {plan.bucket[0]}~{plan.bucket[1]} 字。",
        f"句式：{plan.pattern}"
        + ("（结尾必须是问号）" if plan.pattern == "提问" else "（结尾用感叹号）" if plan.pattern == "感叹" else ""),
        "如果本轮策略里有具体观察/吐槽/夸夸，至少点到一个屏幕里的具体东西（文件名、报错、按钮、歌名、数字、页面标题）；其他策略可以聊素材或感受，不要只写“消息/软件/想法”这种泛词。",
    ]
    if plan.banned_words:
        constraints.append("禁止出现这些词：" + "、".join(plan.banned_words) + "。")
    if plan.banned_openings:
        constraints.append("开头不要用：" + "、".join(plan.banned_openings) + "。")
    constraints_block = "\n".join(f"- {item}" for item in constraints)

    material_block = ""
    if plan.material:
        material_block = f"【可用素材（可改写，不必照抄）】{plan.material}\n"

    callback_block = ""
    if plan.callback:
        callback_block = f"【上一轮你问过】{plan.callback}（如果本轮有 callback 策略，可以自然追问；不要生硬复读）\n"

    music_block = ""
    if plan.music_title:
        who = plan.music_title + (f" — {plan.music_artist}" if plan.music_artist else "")
        music_block = f"【音乐】正在听：{who}\n"
        if any(s.id == "sing" for s in plan.strategies):
            music_block += "这一轮有“哼歌词”候选：优先用「」引用 1~2 句真实歌词，总长不超过 20 字；只有完全记不清才用「啦啦啦」，不要编。\n"

    ids = "、".join(s.id for s in plan.strategies)
    return (
        f"现在是 {now:%Y-%m-%d %H:%M}（周{weekday}）。\n"
        f"主人当前前台窗口：{title}\n"
        f"{_session_block(plan, idle_min)}"
        f"{scene_text}"
        f"{recent_block}"
        f"{material_block}"
        f"{callback_block}"
        f"{music_block}"
        "【本轮候选策略（每个写一句，共 "
        f"{len(plan.strategies)} 句）】\n{strategies_block}\n"
        "【硬约束】\n"
        f"{constraints_block}\n"
        "这是主人此刻的屏幕（桌宠所在区域已隐去）。\n"
        "请按 system 的要求，只输出 JSON：\n"
        '{"candidates":['
        + ",".join(f'{{"strategy":"{sid}","line":"..."}}' for sid in ids.split("、"))
        + "]}"
    )
