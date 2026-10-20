# -*- coding: utf-8 -*-
"""气泡停留时长（纯函数，不需要起 QApplication）。"""

from pet_eye.bubble import display_seconds
from pet_eye.config import BubbleConfig


def test_auto_duration_scales_with_length():
    cfg = BubbleConfig(duration_sec=6, auto_duration=True)
    short = display_seconds(cfg, "字" * 20)
    long = display_seconds(cfg, "字" * 50)
    assert 7 <= short < long <= 14


def test_auto_duration_can_be_disabled():
    cfg = BubbleConfig(duration_sec=6, auto_duration=False)
    assert display_seconds(cfg, "字" * 50) == 6


def test_auto_duration_never_shorter_than_base():
    cfg = BubbleConfig(duration_sec=6, auto_duration=True)
    assert display_seconds(cfg, "短") >= 6
