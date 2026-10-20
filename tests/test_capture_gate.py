# -*- coding: utf-8 -*-
"""画面门控：相似度计算 + 基准图轮换。"""

import numpy as np

from pet_eye.capture import FrameGate, Shot, similarity
from pet_eye.config import CaptureConfig


def make_shot(seed: int, size=(64, 36)) -> Shot:
    rng = np.random.default_rng(seed)
    thumb = rng.integers(0, 255, size=(size[1], size[0]), dtype=np.uint8)
    return Shot(
        jpeg=b"",
        width=size[0],
        height=size[1],
        hash="0" * 16,
        captured_at=0.0,
        monitor={},
        thumb=thumb,
    )


def flat_shot(value: int) -> Shot:
    thumb = np.full((36, 64), value, dtype=np.uint8)
    return Shot(jpeg=b"", width=64, height=36, hash="0" * 16, captured_at=0.0, monitor={}, thumb=thumb)


def test_similarity_identical_is_one():
    shot = make_shot(1)
    assert similarity(shot.thumb, shot.thumb) == 1.0


def test_similarity_different_is_low():
    a = make_shot(1)
    b = make_shot(2)
    assert similarity(a.thumb, b.thumb) < 0.5


def test_similarity_handles_flat_images():
    assert similarity(flat_shot(10).thumb, flat_shot(10).thumb) == 1.0
    assert similarity(flat_shot(10).thumb, flat_shot(200).thumb) < 0.5


def test_similarity_none_is_zero():
    assert similarity(None, make_shot(1).thumb) == 0.0


def test_gate_blocks_when_same_frame():
    gate = FrameGate(CaptureConfig(gate=True, gate_similarity=0.95))
    shot = make_shot(1)
    assert gate.check(shot) == 0.0          # 还没有基准
    assert gate.should_skip(0.0) is False
    gate.commit(shot)
    assert gate.has_baseline
    score = gate.check(make_shot(1))        # 同一张图
    assert score > 0.95
    assert gate.should_skip(score) is True


def test_gate_lets_different_frame_through():
    gate = FrameGate(CaptureConfig(gate=True, gate_similarity=0.95))
    gate.commit(make_shot(1))
    score = gate.check(make_shot(2))
    assert score <= 0.95
    assert gate.should_skip(score) is False


def test_gate_disabled_never_blocks():
    gate = FrameGate(CaptureConfig(gate=False))
    shot = make_shot(1)
    gate.commit(shot)
    assert gate.should_skip(1.0) is False


def test_gate_reset_clears_baseline():
    gate = FrameGate(CaptureConfig(gate=True))
    gate.commit(make_shot(1))
    gate.reset()
    assert not gate.has_baseline
    assert gate.check(make_shot(1)) == 0.0
