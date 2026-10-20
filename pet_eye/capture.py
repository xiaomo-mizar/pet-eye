# -*- coding: utf-8 -*-
"""OpenCV 截屏：mss 抓屏 → OpenCV 缩放/编码/感知哈希。

说明：OpenCV 本身没有跨平台"抓桌面"的 API，Windows 上最稳的组合是
mss（DXGI/GDI 抓屏）+ OpenCV（颜色转换、缩放、JPEG 编码、8x8 均值哈希）。
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Iterable

import cv2
import mss
import numpy as np

from .config import CaptureConfig
from .geometry import Rect
from .paths import ensure_runtime_dir


@dataclass
class Shot:
    jpeg: bytes
    width: int
    height: int
    hash: str
    captured_at: float
    monitor: dict
    pet_rect: Rect | None = None
    # 门控用的缩略图（64x36 灰度），只有几 KB，留在进程内存里
    thumb: np.ndarray | None = None

    @property
    def data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode("ascii")


def _average_hash(bgr: np.ndarray) -> str:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    tiny = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
    avg = float(tiny.mean())
    bits = (tiny >= avg).astype(np.uint8).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 64
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except ValueError:
        return 64


THUMB_SIZE = (64, 36)


def _thumb(bgr: np.ndarray) -> np.ndarray:
    """门控用的缩略灰度图：64x36，几 KB，比整图好存也好算。"""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, THUMB_SIZE, interpolation=cv2.INTER_AREA)


def similarity(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """两张缩略图的相似度，0~1。

    - 归一化互相关（`matchTemplate`，对亮度变化不敏感）
    - 平均像素差（补上「整体明暗一致但细节全换」的情况）
    两者加权，越接近 1 越像。
    """
    if a is None or b is None:
        return 0.0
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    af = a.astype(np.float32)
    bf = b.astype(np.float32)
    if af.std() < 1e-3 or bf.std() < 1e-3:
        # 纯色画面没法算相关，只能看像素差
        return max(0.0, 1.0 - float(np.abs(af - bf).mean()) / 255.0)
    score = float(cv2.matchTemplate(bf, af, cv2.TM_CCOEFF_NORMED)[0][0])
    score = max(0.0, min(1.0, score))
    pixel = max(0.0, 1.0 - float(np.abs(af - bf).mean()) / 255.0)
    return round(0.55 * score + 0.45 * pixel, 4)


class FrameGate:
    """画面门控：只留两张图——上一张发出去的（基准）+ 当前这张。

    相似度 > 阈值就整轮跳过：不采动向、不调模型、不写记忆，一分钱不花。
    """

    def __init__(self, cfg: CaptureConfig):
        self.cfg = cfg
        self.enabled = bool(cfg.gate)
        self.threshold = float(cfg.gate_similarity)
        self._baseline: np.ndarray | None = None
        self.last_similarity: float = 0.0

    @property
    def has_baseline(self) -> bool:
        return self._baseline is not None

    def check(self, shot: Shot) -> float:
        """和基准比一下；返回相似度（没有基准时返回 0，当作「该看」）。"""
        if not self.enabled or self._baseline is None:
            return 0.0
        self.last_similarity = similarity(self._baseline, shot.thumb)
        return self.last_similarity

    def should_skip(self, score: float) -> bool:
        return self.enabled and self.has_baseline and score > self.threshold

    def commit(self, shot: Shot) -> None:
        """真正要调模型了，才把这张设成新的基准。"""
        if shot.thumb is not None:
            self._baseline = shot.thumb.copy()

    def reset(self) -> None:
        self._baseline = None
        self.last_similarity = 0.0


class ScreenCapture:
    def __init__(self, cfg: CaptureConfig):
        self.cfg = cfg

    def _monitor(self, sct: "mss.mss") -> dict:
        if self.cfg.monitor == "all":
            return sct.monitors[0]
        return sct.monitors[1]

    def grab(self, mask_rects: Iterable[Rect] = ()) -> Shot:
        with mss.MSS() as sct:
            monitor = self._monitor(sct)
            raw = np.asarray(sct.grab(monitor))
            bgr = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)

        # 把桌宠/气泡区域糊掉，避免模型对着"屏幕角落的小人"说话
        if self.cfg.mask_pet:
            self._mask(bgr, monitor, mask_rects)

        h = int(monitor.get("height") or bgr.shape[0])
        w = int(monitor.get("width") or bgr.shape[1])
        if w > self.cfg.max_width:
            new_h = max(1, int(round(h * self.cfg.max_width / w)))
            bgr = cv2.resize(bgr, (self.cfg.max_width, new_h), interpolation=cv2.INTER_AREA)

        hh = int(bgr.shape[0])
        ww = int(bgr.shape[1])
        digest = _average_hash(bgr)
        thumb = _thumb(bgr)
        ok, buf = cv2.imencode(
            ".jpg",
            bgr,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(self.cfg.jpeg_quality)],
        )
        if not ok:
            raise RuntimeError("OpenCV JPEG 编码失败")
        jpeg = buf.tobytes()

        if self.cfg.save_debug_shot:
            out = ensure_runtime_dir() / "last-shot.jpg"
            out.write_bytes(jpeg)

        return Shot(
            jpeg=jpeg,
            width=ww,
            height=hh,
            hash=digest,
            captured_at=time.time(),
            monitor=dict(monitor),
            thumb=thumb,
        )

    def _mask(self, bgr: np.ndarray, monitor: dict, rects: Iterable[Rect]) -> None:
        m_left = int(monitor.get("left") or 0)
        m_top = int(monitor.get("top") or 0)
        img_h, img_w = bgr.shape[:2]
        for r in rects:
            x0 = max(0, r.x - m_left)
            y0 = max(0, r.y - m_top)
            x1 = min(img_w, r.right - m_left)
            y1 = min(img_h, r.bottom - m_top)
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            roi = bgr[y0:y1, x0:x1]
            k = max(9, int(self.cfg.mask_blur) | 1)
            k = min(k, (min(roi.shape[:2]) // 2) * 2 + 1)
            blurred = cv2.GaussianBlur(roi, (k, k), 0)
            bgr[y0:y1, x0:x1] = cv2.addWeighted(roi, 0.12, blurred, 0.88, 0)
