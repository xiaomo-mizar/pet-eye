# -*- coding: utf-8 -*-
"""纯几何/断行单测（不启动 Qt）。"""

from pet_eye.geometry import (
    Rect,
    choose_side,
    normalize_text,
    place_bubble,
    split_lines,
)
from pet_eye.window import WindowInfo, pet_body_rect

SCREEN = Rect(0, 0, 2560, 1600)
PET_W = 462
PET_H = 260


def pet_at(x: int, y: int) -> Rect:
    return Rect(x, y, PET_W, PET_H)


def test_left_half_pet_gets_right_side():
    pet = pet_at(100, 1000)
    assert choose_side(pet, SCREEN, 300) == "right"


def test_right_half_pet_gets_left_side():
    pet = pet_at(2000, 1000)
    assert choose_side(pet, SCREEN, 300) == "left"


def test_no_room_on_preferred_side_flips():
    # 桌宠贴右边，但气泡太宽，右边放不下 → 翻到左边
    pet = pet_at(2350, 600)
    assert choose_side(pet, SCREEN, 500) == "left"


def test_place_right_of_pet():
    pet = pet_at(100, 1000)
    p = place_bubble(pet, SCREEN, 300, 120, side="right", gap=10, margin=18)
    assert p.side == "right"
    assert p.above is True
    assert p.y + 120 <= pet.y  # 在头顶上方
    assert p.x >= SCREEN.x + 18
    assert p.x + 300 <= SCREEN.right - 18


def test_place_left_of_pet():
    pet = pet_at(2000, 1000)
    p = place_bubble(pet, SCREEN, 300, 120, side="left", gap=10, margin=18)
    assert p.side == "left"
    assert p.x + 300 <= pet.right
    assert p.x >= SCREEN.x + 18


def test_top_edge_flips_below():
    pet = pet_at(100, 5)
    p = place_bubble(pet, SCREEN, 300, 120, side="right", gap=10, margin=18)
    assert p.above is False
    assert p.y >= pet.bottom


def test_bubble_never_offscreen():
    pet = pet_at(2500, 100)
    p = place_bubble(pet, SCREEN, 300, 120, side="left", gap=10, margin=18)
    assert p.x >= SCREEN.x + 18
    assert p.x + 300 <= SCREEN.right - 18
    assert p.y >= SCREEN.y + 18


def test_tail_points_at_pet_corner():
    right = place_bubble(pet_at(100, 1000), SCREEN, 300, 120, side="right")
    tail_right = right.x + 300 * right.tail_ratio
    assert 90 <= tail_right <= 100 + PET_W
    left = place_bubble(pet_at(2000, 1000), SCREEN, 300, 120, side="left")
    tail_left = left.x + 300 * left.tail_ratio
    assert 2000 <= tail_left <= 2000 + PET_W


def test_pet_body_rect_ignores_transparent_sprite_padding():
    win = WindowInfo(
        hwnd=1,
        pid=1,
        title="dsh-pet 桌宠（桌面模式）",
        exe="electron.exe",
        rect=Rect(462, 740, 1386, 1115),
        exstyle=0,
    )
    body = pet_body_rect(win)
    assert body == Rect(1025, 1140, 259, 309)


def test_normalize_text_strips_newlines_and_quotes():
    # 中文换行直接拼接；英文换行保留成空格由调用方决定
    assert normalize_text("  “宝宝\n要开心呀！”  ") == "宝宝要开心呀！"


def test_split_lines_all_lengths():
    for n in (20, 21, 28, 35, 42, 50):
        text = ("本鱼看着宝宝认真做事的样子，尾巴都忍不住啪嗒啪嗒拍水啦。" * 2)[:n]
        lines = split_lines(text, 15)
        assert "".join(lines) == text
        assert 2 <= len(lines) <= 5
        assert all(len(line) <= 16 for line in lines), lines


def test_split_lines_prefers_punctuation():
    text = "宝宝又在跟扫雷较劲呀，本鱼陪你慢慢点，不着急嘛。"
    lines = split_lines(text, 15)
    assert "".join(lines) == text
    assert any(line.endswith("，") or line.endswith("。") for line in lines)
