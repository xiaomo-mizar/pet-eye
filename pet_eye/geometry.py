# -*- coding: utf-8 -*-
"""气泡几何：判断桌宠在屏幕哪一侧，并把气泡放到它的左上/右上角。

这里是纯函数，不依赖 Qt / Win32，方便单测。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

Side = Literal["left", "right"]

# 中文断行优先在这些标点后面切
_PUNCT = "，。！？；：、,.!?;:…—～~）)】」』”’"


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom

    def clamped(self, screen: "Rect", margin: int = 0) -> "Rect":
        x = min(max(self.x, screen.x + margin), max(screen.x + margin, screen.right - margin - self.w))
        y = min(max(self.y, screen.y + margin), max(screen.y + margin, screen.bottom - margin - self.h))
        return Rect(x, y, self.w, self.h)


@dataclass(frozen=True)
class BubblePlacement:
    x: int
    y: int
    side: Side
    tail_ratio: float  # 尾巴在气泡宽度上的相对位置（0=左，1=右）
    above: bool  # True=气泡在桌宠上方，尾巴朝下；False=在下方，尾巴朝上


def choose_side(
    pet: Rect,
    screen: Rect,
    bubble_w: int,
    gap: int = 10,
    margin: int = 18,
) -> Side:
    """判断气泡该放在桌宠的左边还是右边。

    规则（可解释、可测试）：
    1. 桌宠中心在屏幕左半 → 优先把气泡放它右边（右上角），反之放左边；
    2. 优先侧空间不够 → 翻到另一侧；
    3. 两侧都不够（桌宠很大/屏幕很窄）→ 选剩余空间更大的一侧，后面再夹取。
    """
    prefer: Side = "right" if pet.center_x <= screen.center_x else "left"

    def fits(side: Side) -> bool:
        if side == "right":
            return pet.right + gap + bubble_w <= screen.right - margin
        return pet.x - gap - bubble_w >= screen.x + margin

    if fits(prefer):
        return prefer
    other: Side = "left" if prefer == "right" else "right"
    if fits(other):
        return other
    space_left = pet.x - screen.x
    space_right = screen.right - pet.right
    return "right" if space_right >= space_left else "left"


def place_bubble(
    pet: Rect,
    screen: Rect,
    bubble_w: int,
    bubble_h: int,
    *,
    side: Side | None = None,
    gap: int = 10,
    margin: int = 18,
) -> BubblePlacement:
    """把气泡放到桌宠头顶的左上/右上角，空间不够时自动翻转或放下方。"""
    if side is None:
        side = choose_side(pet, screen, bubble_w, gap, margin)

    overlap = min(24, max(8, int(pet.w * 0.12)))

    # 横向：贴住桌宠的上半身一角，而不是完全离开桌宠
    if side == "right":
        x = pet.right - overlap
        anchor_x = pet.right - max(10, int(pet.w * 0.05))
    else:
        x = pet.x - bubble_w + overlap
        anchor_x = pet.x + max(10, int(pet.w * 0.05))

    x = min(max(x, screen.x + margin), max(screen.x + margin, screen.right - margin - bubble_w))

    # 尾巴指向桌宠头顶那一角，而不是气泡的几何中心
    tail_x = min(max(anchor_x, x + 16), x + bubble_w - 16)
    tail_ratio = (tail_x - x) / max(1, bubble_w)

    # 纵向：默认在头顶上方；顶到屏幕上边就翻到下方
    above = True
    y = pet.y - gap - bubble_h
    if y < screen.y + margin:
        above = False
        y = pet.bottom + gap
        if y + bubble_h > screen.bottom - margin:
            # 下方也放不下（桌宠几乎占满高度）：夹在屏幕内，尾巴仍然指向桌宠
            y = max(screen.y + margin, screen.bottom - margin - bubble_h)

    return BubblePlacement(x=int(x), y=int(y), side=side, tail_ratio=tail_ratio, above=above)


def normalize_text(text: str) -> str:
    """把模型输出收成单行：去换行、去多余空白、去首尾引号。"""
    out = str(text or "").replace("\r", "").replace("\n", "").replace("\t", " ")
    out = " ".join(out.split())
    out = out.strip().strip("\"'“”‘’")
    return out.strip()


def split_lines(text: str, max_chars: int = 15) -> list[str]:
    """按标点优先、长度均衡地断成 2~4 行。

    - 保证每行不超过 max_chars + 1（标点容差）；
    - 尽量在标点后断开；
    - 拼接回去与原文一致（不增删字符）。
    """
    text = normalize_text(text)
    if not text:
        return [""]
    if max_chars < 4:
        max_chars = 4
    if len(text) <= max_chars:
        return [text]

    out: list[str] = []
    rest = text
    while len(rest) > max_chars:
        k = max(2, math.ceil(len(rest) / max_chars))
        target = math.ceil(len(rest) / k)
        cut = _best_cut(rest, target, max_chars)
        out.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        out.append(rest)

    # 防止最后一行只有一个标点或一个字：往前挪一点
    if len(out) >= 2 and len(out[-1]) <= 2 and len(out[-2]) > 6:
        moved = out[-2][-2:]
        out[-2] = out[-2][:-2]
        out[-1] = moved + out[-1]
    return [line for line in out if line]


def _best_cut(text: str, target: int, max_chars: int) -> int:
    n = len(text)
    lo = max(4, target - 4)
    hi = min(n - 2, target + 4, max_chars + 1)
    if hi <= lo:
        return min(max(target, 1), n - 1)

    punct_hits = [i + 1 for i in range(lo, hi) if text[i] in _PUNCT]
    if punct_hits:
        return min(punct_hits, key=lambda i: (abs(i - target), i))

    # 英文/数字：优先在空格后断，避免把单词劈开
    space_hits = [i + 1 for i in range(lo, hi) if text[i] == " "]
    if space_hits:
        return min(space_hits, key=lambda i: (abs(i - target), i))

    return min(max(target, 1), n - 1)
