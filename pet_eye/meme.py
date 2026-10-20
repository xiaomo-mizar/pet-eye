# -*- coding: utf-8 -*-
"""从 dsh-meme 的图库里挑表情包：扫描 index.db、按情景检索、让模型最终选一张。"""

from __future__ import annotations

import logging
import random
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .prompt import SYSTEM_PROMPT

log = logging.getLogger("pet_eye")

MEME_ROOT = Path.home() / ".dsh" / "meme-packs"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# 策略 -> 优先情绪
PREFERRED_TAGS: dict[str, tuple[str, ...]] = {
    "observe": ("confused", "daily", "happy"),
    "care": ("shy", "sad", "happy"),
    "tease": ("angry", "happy"),
    "ask": ("confused", "daily"),
    "fact": ("happy", "confused"),
    "praise": ("happy", "shy"),
    "story": ("daily", "sad"),
    "tsundere": ("angry", "shy"),
    "jealous": ("angry", "sad"),
    "guess": ("confused", "daily"),
    "sing": ("happy", "shy"),
    "remind": ("sad", "angry"),
    "callback": ("confused", "happy"),
    "meme": ("daily", "happy", "angry"),
}


@dataclass(frozen=True)
class Meme:
    key: str
    path: Path
    tag: str
    caption: str
    keywords: str


class MemeLibrary:
    def __init__(self, packs: list[str] | None = None):
        self.pack_names = [str(x) for x in (packs or []) if str(x).strip()]
        self._memes: list[Meme] = []
        self._loaded = False

    def load(self) -> list[Meme]:
        if self._loaded:
            return self._memes
        self._loaded = True
        root = MEME_ROOT
        if not root.exists():
            log.warning("表情包目录不存在：%s", root)
            return self._memes

        if self.pack_names:
            dirs = [root / name for name in self.pack_names]
        else:
            dirs = [d for d in root.iterdir() if d.is_dir()]

        for pack_dir in dirs:
            db = pack_dir / "index.db"
            if not pack_dir.is_dir() or not db.exists():
                continue
            try:
                uri = db.resolve().as_uri() + "?mode=ro"
                con = sqlite3.connect(uri, uri=True)
                rows = list(
                    con.execute(
                        "select path, tag, caption, keywords from memes "
                        "where path is not null and path != ''"
                    )
                )
                con.close()
            except Exception as e:
                log.warning("读取表情库 %s 失败：%s", db, e)
                continue
            for rel, tag, caption, keywords in rows:
                path = pack_dir / str(rel)
                if path.suffix.lower() not in _IMAGE_EXTS or not path.exists():
                    continue
                self._memes.append(
                    Meme(
                        key=f"{pack_dir.name}:{rel}",
                        path=path,
                        tag=str(tag or "daily"),
                        caption=str(caption or "").strip() or str(tag or ""),
                        keywords=str(keywords or ""),
                    )
                )
        log.info("表情包库：%d 张（%s）", len(self._memes), "、".join(self.pack_names) or "全部")
        return self._memes

    @property
    def count(self) -> int:
        return len(self.load())

    def candidates(
        self,
        *,
        tone: str,
        context: str,
        recent_keys: Iterable[str] = (),
        k: int = 12,
        rng: random.Random | None = None,
    ) -> list[Meme]:
        rng = rng or random.Random()
        memes = self.load()
        if not memes:
            return []
        recent = set(recent_keys)
        pool = [m for m in memes if m.key not in recent] or memes
        tags = PREFERRED_TAGS.get(tone, ("daily", "happy", "angry"))
        ctx = (context or "").lower()

        def score(meme: Meme) -> float:
            value = 0.0
            if meme.tag in tags:
                value += 4.0 - 0.7 * tags.index(meme.tag)
            if "游戏" in ctx and meme.tag in {"angry", "happy"}:
                value += 1.0
            if ("听" in ctx or "歌" in ctx) and meme.tag in {"happy", "shy"}:
                value += 1.0
            if any(word in ctx for word in ("代码", "报错", "error", "失败")) and meme.tag in {
                "confused",
                "angry",
            }:
                value += 1.0
            for word in re.split(r"\s+", meme.keywords):
                word = word.strip()
                if len(word) >= 2 and word.lower() in ctx:
                    value += 1.3
            return value + rng.random() * 1.8

        ranked = sorted(pool, key=score, reverse=True)
        # 每个情绪先取几张，避免 12 张全是同一个 tag
        picked: list[Meme] = []
        for tag in tags:
            group = [m for m in ranked if m.tag == tag and m not in picked][:3]
            picked.extend(group)
        for meme in ranked:
            if len(picked) >= k:
                break
            if meme not in picked:
                picked.append(meme)
        return picked[:k]


def build_meme_system_prompt() -> str:
    return (
        SYSTEM_PROMPT
        + "\n\n【此刻的任务】\n"
        "现在要从候选表情包里挑一张最贴合主人此刻屏幕情景的，并配一句 20~50 个字的话。"
        "判断屏幕里的情绪/场景（写代码、看文档、听歌、玩游戏、聊天、深夜），"
        "不要只因为“可爱”就选。配文要像鲸鱼娘随口说的，和这张图、这块屏幕都对得上；"
        "不要描述图片本身，不要提表情包/图片。"
        "只输出 JSON：{\"meme\":\"M1\",\"line\":\"配文\"}；"
        "如果都不贴合，输出 {\"meme\":\"\",\"line\":\"\"}。不要输出任何解释。"
    )


FALLBACK_CAPTIONS = {
    "angry": "本鱼先替你气一下，等会儿再继续弄。",
    "happy": "这张正合适，本鱼陪你一起高兴一会儿。",
    "sad": "先别硬撑，本鱼在这儿陪你缓一缓。",
    "confused": "本鱼也有点看懵了，要不先停一下？",
    "shy": "本鱼才没有一直盯着你看呢，继续忙吧。",
    "daily": "本鱼在旁边守着你，慢慢来就好。",
}


def fallback_caption(tag: str) -> str:
    return FALLBACK_CAPTIONS.get(tag, FALLBACK_CAPTIONS["daily"])


def build_meme_user_text(
    window_title: str,
    scene_block: str,
    candidates: list[Meme],
    tone_label: str = "日常",
    recent_lines: list[str] | None = None,
    banned_words: list[str] | None = None,
) -> str:
    now = datetime.now().astimezone()
    recent = [x for x in (recent_lines or []) if x][-3:]
    recent_block = ("最近说过（别复述）：" + " / ".join(recent) + "\n") if recent else ""
    candidate_lines = []
    for i, meme in enumerate(candidates, start=1):
        keyword = f"；关键词：{meme.keywords}" if meme.keywords else ""
        candidate_lines.append(f"M{i} [{meme.tag}] {meme.caption}{keyword}")
    scene_text = (scene_block or "").strip()
    if scene_text:
        scene_text += "\n"
    banned_block = ""
    if banned_words:
        banned_block = "配文禁止出现：" + "、".join(banned_words) + "。\n"
    return (
        f"现在是 {now:%Y-%m-%d %H:%M}。\n"
        f"主人当前前台窗口：{(window_title or '桌面').strip()[:80]}\n"
        f"{scene_text}"
        f"{recent_block}"
        f"这一轮想用表情包回复，语气参考：{tone_label}。\n"
        "【候选表情包】\n"
        + "\n".join(candidate_lines)
        + "\n配文要求：20~50 字、单行、鲸鱼娘口吻，结合屏幕和这张图；不要描述图片，不要提表情包。\n"
        f"{banned_block}"
        "只输出 JSON：{\"meme\":\"M3\",\"line\":\"配文\"}；都不贴就输出 {\"meme\":\"\",\"line\":\"\"}。"
    )
