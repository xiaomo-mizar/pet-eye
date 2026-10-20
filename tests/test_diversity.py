# -*- coding: utf-8 -*-
"""策略轮盘 / 去重 / 屏幕变化 / 歌词括号的单测。"""

import random

from pet_eye.context import MediaInfo
from pet_eye.diversity import (
    STRATEGY_BY_ID,
    TurnPlan,
    banned_words,
    choose_length_bucket,
    plan_turn,
    screen_digest,
    score_candidate,
    stats_summary,
)
from pet_eye.llm import clean_line


class FakeScene:
    media = MediaInfo(title="群青", artist="YOASOBI", app="网易云音乐", playing=True, kind="music")
    app = "VS Code"
    exe = "code.exe"
    window_title = "context.py - pet-eye - Visual Studio Code"
    category = "写代码"
    game = ""


def test_banned_words_from_repetition():
    lines = [
        "本鱼在旁边乖乖陪着你，慢慢来就好哦😊",
        "本鱼陪着你，别累着，慢慢做哦😊",
        "消息慢慢回，本鱼陪着你呢🐳",
    ]
    banned = banned_words(lines)
    assert "本鱼" in banned
    assert "陪着" in banned or "陪你" in banned
    assert "😊" in banned


def test_length_bucket_rotates_away_from_recent():
    assert choose_length_bucket(["字" * 42, "字" * 40, "字" * 38], random.Random(1)) == (20, 28)
    assert choose_length_bucket(["字" * 22, "字" * 24, "字" * 21], random.Random(1)) == (34, 48)


def test_screen_digest_detects_big_change():
    text, changed = screen_digest("0" * 16, "f" * 16, 4)
    assert changed is True
    assert text


def test_clean_line_preserves_lyric_brackets():
    line = "「あの日の僕はまだ青いままで」……本鱼小声陪你把这页啃完哦"
    out = clean_line(line)
    assert out.startswith("「")
    assert "」" in out
    assert 20 <= len(out) <= 50


def test_plan_includes_sing_when_music():
    plan = plan_turn(
        FakeScene(),
        now=1792460000,
        recent_lines=[],
        recent_meta=[],
        feedback={},
        same_app_min=12,
        stale_min=2,
        candidates=3,
        music_lyrics=True,
        rng=random.Random(1),
    )
    assert "sing" in plan.strategy_ids
    assert plan.music_title == "群青"
    assert 20 <= plan.bucket[0] < plan.bucket[1] <= 50


def test_plan_avoids_remind_without_long_work():
    plan = plan_turn(
        FakeScene(),
        now=1792460000,
        recent_lines=[],
        recent_meta=[],
        feedback={},
        same_app_min=5,
        stale_min=1,
        candidates=3,
        rng=random.Random(2),
    )
    assert "remind" not in plan.strategy_ids


def test_score_penalizes_banned_word():
    plan = TurnPlan(
        strategies=[STRATEGY_BY_ID["care"]],
        bucket=(20, 30),
        pattern="陈述",
        banned_words=["本鱼"],
    )
    clean = score_candidate("屏幕右下角那个弹窗先别关，也许还要用。", "care", plan, [])
    dirty = score_candidate("本鱼在旁边陪着，右下角弹窗先别关哦。", "care", plan, [])
    assert clean > dirty


def test_stats_summary_empty():
    assert "还没有" in stats_summary([])
