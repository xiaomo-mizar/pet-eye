# -*- coding: utf-8 -*-
"""Qt 透明置顶气泡：贴在桌宠左上/右上角，自动换行、淡入淡出、跟随桌宠。"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Property,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from .config import BubbleConfig
from .geometry import BubblePlacement, Rect, place_bubble, split_lines
from .paths import PET_FONT_PATH
from .window import get_pet_rect

log = logging.getLogger("pet_eye")


def _load_font(font_size_px: int) -> tuple[QFont, str]:
    family = ""
    try:
        if PET_FONT_PATH.exists():
            font_id = QFontDatabase.addApplicationFont(str(PET_FONT_PATH))
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    family = families[0]
    except Exception:
        family = ""

    font = QFont()
    families = [f for f in (family, "Microsoft YaHei UI", "Segoe UI Emoji") if f]
    font.setFamilies(families)
    font.setPixelSize(int(font_size_px))
    font.setStyleStrategy(QFont.PreferAntialias)
    return font, family or "Microsoft YaHei UI"


def display_seconds(cfg: BubbleConfig, text: str) -> float:
    """气泡停留时长：字数越多停得越久，但不超过 14 秒。"""
    base = float(cfg.duration_sec)
    if not cfg.auto_duration:
        return base
    return min(14.0, max(base, 4.5 + len(text) * 0.16))


def _physical_to_logical(rect: Rect) -> tuple[Rect, QRectF]:
    """物理像素 → Qt 逻辑坐标；同时返回该矩形所在屏幕的可用区域（逻辑）。"""
    screens = QGuiApplication.screens()
    chosen = QGuiApplication.primaryScreen()
    for screen in screens:
        g = screen.geometry()
        dpr = screen.devicePixelRatio() or 1.0
        px = g.x() * dpr
        py = g.y() * dpr
        pw = g.width() * dpr
        ph = g.height() * dpr
        if px <= rect.center_x < px + pw and py <= rect.center_y < py + ph:
            chosen = screen
            break

    g = chosen.geometry()
    dpr = chosen.devicePixelRatio() or 1.0
    px = g.x() * dpr
    py = g.y() * dpr
    logical = Rect(
        x=int(round(g.x() + (rect.x - px) / dpr)),
        y=int(round(g.y() + (rect.y - py) / dpr)),
        w=int(round(rect.w / dpr)),
        h=int(round(rect.h / dpr)),
    )
    avail = chosen.availableGeometry()
    return logical, QRectF(avail.x(), avail.y(), avail.width(), avail.height())


class SpeechBubble(QWidget):
    """一只无边框、透明、点击穿透、永远置顶的气泡。"""

    TAIL_H = 22
    SHADOW_PAD = 10
    RADIUS = 20

    def __init__(self, cfg: BubbleConfig, parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint,
        )
        self.cfg = cfg
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowOpacity(0.0)

        self._font, self._font_family = _load_font(cfg.font_size_px)
        self._lines: list[str] = []
        self._text = ""
        self._side = "right"
        self._tail_ratio = 0.18
        self._above = True
        self._pet_phys: Rect | None = None
        self._anchor_provider: Callable[[], tuple[Rect | None, object | None]] = get_pet_rect

        self._body_rect = QRectF()
        self._text_rect = QRectF()
        self._line_h = 0.0
        self._image_pixmap: QPixmap | None = None
        self._image_rect = QRectF()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_animated)

        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(int(cfg.follow_interval_ms))
        self._follow_timer.setTimerType(Qt.PreciseTimer)
        self._follow_timer.timeout.connect(self._follow_pet)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(self._on_fade_out)
        self._fading_out = False

        self._pop = 1.0
        self._pop_anim = QPropertyAnimation(self, b"pop", self)
        self._pop_anim.setDuration(320)
        self._pop_anim.setEasingCurve(QEasingCurve.OutBack)
        self._pop_anim.setStartValue(0.86)
        self._pop_anim.setEndValue(1.0)

        self._body_rect = QRectF()
        self._text_rect = QRectF()

    # ---------- 弹入动画属性 ----------

    def _get_pop(self) -> float:
        return self._pop

    def _set_pop(self, value: float) -> None:
        self._pop = float(value)
        self.update()

    pop = Property(float, _get_pop, _set_pop)

    # ---------- 对外 ----------

    def show_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._image_pixmap = None
        self._image_rect = QRectF()
        self._fade.stop()
        self._fading_out = False
        self._text = text
        self._lines = self._fit_lines(text)
        self._measure()
        self._pet_phys, _ = self._anchor_provider()
        self._reposition(force=True)
        self._present(display_seconds(self.cfg, text))

    def show_image(self, path: str) -> bool:
        return self.show_image_with_text(path, "")

    def show_image_with_text(self, path: str, text: str) -> bool:
        """表情包 + 配文；加载失败返回 False。"""
        pix = QPixmap(str(path))
        if pix.isNull():
            log.warning("表情包加载失败：%s", path)
            return False
        text = (text or "").strip()
        max_h = min(self.cfg.image_max_height_px, 230) if text else self.cfg.image_max_height_px
        pix = pix.scaled(
            self.cfg.image_max_width_px,
            max_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._image_pixmap = pix
        self._text = text
        self._lines = self._fit_lines(text) if text else []
        self._fade.stop()
        self._fading_out = False
        if text:
            self._measure_image_text()
        else:
            self._measure_image()
        self._pet_phys, _ = self._anchor_provider()
        self._reposition(force=True)
        self._present(max(9.0, float(self.cfg.duration_sec)))
        return True

    def _present(self, seconds: float) -> None:
        self._follow_timer.start()
        self._hide_timer.start(int(max(1.0, seconds) * 1000))
        self.setWindowOpacity(0.0)
        self._pop = 0.86
        self.show()
        self.raise_()
        self._pop_anim.stop()
        self._pop_anim.start()
        self._fade.stop()
        self._fade.setDuration(max(1, self.cfg.fade_ms))
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def hide_animated(self) -> None:
        self._hide_timer.stop()
        self._follow_timer.stop()
        self._fade.stop()
        self._fading_out = True
        self._fade.setDuration(max(1, self.cfg.fade_ms))
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_out(self) -> None:
        if self._fading_out and self.windowOpacity() <= 0.02:
            self.hide()

    def set_anchor_provider(self, provider: Callable[[], tuple[Rect | None, object | None]]) -> None:
        self._anchor_provider = provider

    @property
    def current_text(self) -> str:
        return self._text

    # ---------- 布局 ----------

    def _fit_lines(self, text: str) -> list[str]:
        """按实际字体宽度断行：保证每一行都放得进气泡，不会被裁掉。"""
        fm = QFontMetricsF(self._font)
        max_text_w = max(80.0, self.cfg.max_width_px - 2 * self.cfg.padding_x_px)
        per_line = max(6, int(self.cfg.max_chars_per_line))
        # 中文字符大约 1.0~1.2 个 font-size 宽，先估一个上限
        per_line = min(per_line, max(6, int(max_text_w / max(6.0, self.cfg.font_size_px * 0.95))))
        lines = split_lines(text, per_line)
        for _ in range(8):
            widest = max((fm.horizontalAdvance(line) for line in lines), default=0.0)
            if widest <= max_text_w - 2 or per_line <= 6:
                break
            per_line -= 1
            lines = split_lines(text, per_line)
        return lines

    def _measure(self) -> None:
        fm = QFontMetricsF(self._font)
        max_text_w = max(80.0, self.cfg.max_width_px - 2 * self.cfg.padding_x_px)
        line_widths = [fm.horizontalAdvance(line) for line in self._lines] or [0.0]
        text_w = min(max_text_w, max(100.0, max(line_widths) + 2.0))
        text_w = float(int(text_w))

        # 每行一个等高的行盒，文字在行盒里水平、垂直都居中
        self._line_h = round(fm.height() * 1.5, 2)
        text_h = self._line_h * max(1, len(self._lines))

        body_w = text_w + 2 * self.cfg.padding_x_px
        body_h = text_h + 2 * self.cfg.padding_y_px
        total_w = body_w + 2 * self.SHADOW_PAD
        total_h = body_h + self.TAIL_H + 2 * self.SHADOW_PAD
        self.setFixedSize(int(total_w), int(total_h))

        body_x = self.SHADOW_PAD
        body_y = self.SHADOW_PAD if self._above else self.SHADOW_PAD + self.TAIL_H
        self._body_rect = QRectF(body_x, body_y, body_w, body_h)
        self._text_rect = QRectF(
            body_x + self.cfg.padding_x_px,
            body_y + self.cfg.padding_y_px,
            text_w,
            text_h,
        )

    def _measure_image(self) -> None:
        pix = self._image_pixmap
        if pix is None:
            return
        pad = 6.0
        body_w = pix.width() + 2 * pad
        body_h = pix.height() + 2 * pad
        total_w = body_w + 2 * self.SHADOW_PAD
        total_h = body_h + self.TAIL_H + 2 * self.SHADOW_PAD
        self.setFixedSize(int(total_w), int(total_h))

        body_x = self.SHADOW_PAD
        body_y = self.SHADOW_PAD if self._above else self.SHADOW_PAD + self.TAIL_H
        self._body_rect = QRectF(body_x, body_y, body_w, body_h)
        self._image_rect = QRectF(body_x + pad, body_y + pad, pix.width(), pix.height())
        self._text_rect = QRectF()

    def _measure_image_text(self) -> None:
        pix = self._image_pixmap
        if pix is None or not self._lines:
            self._measure_image()
            return
        fm = QFontMetricsF(self._font)
        max_text_w = max(80.0, self.cfg.max_width_px - 2 * 10)
        line_widths = [fm.horizontalAdvance(line) for line in self._lines]
        text_w = min(max_text_w, max(120.0, max(line_widths) + 2.0))
        text_w = float(int(text_w))
        self._line_h = round(fm.height() * 1.5, 2)
        text_h = self._line_h * len(self._lines)

        pad, gap = 10.0, 8.0
        body_w = max(pix.width(), text_w) + 2 * pad
        body_h = pad + pix.height() + gap + text_h + pad
        total_w = body_w + 2 * self.SHADOW_PAD
        total_h = body_h + self.TAIL_H + 2 * self.SHADOW_PAD
        self.setFixedSize(int(total_w), int(total_h))

        body_x = self.SHADOW_PAD
        body_y = self.SHADOW_PAD if self._above else self.SHADOW_PAD + self.TAIL_H
        self._body_rect = QRectF(body_x, body_y, body_w, body_h)
        image_x = body_x + (body_w - pix.width()) / 2
        image_y = body_y + pad
        self._image_rect = QRectF(image_x, image_y, pix.width(), pix.height())
        self._text_rect = QRectF(
            body_x + (body_w - text_w) / 2,
            image_y + pix.height() + gap,
            text_w,
            text_h,
        )

    def _text_color(self) -> QColor:
        color = QColor(self.cfg.text_color)
        if not color.isValid():
            color = QColor("#2a2622")
        return color

    def _reposition(self, force: bool = False) -> None:
        if self._pet_phys is None:
            self._pet_phys, _ = self._anchor_provider()
        if self._pet_phys is None:
            return
        pet_rect = self._pet_phys
        logical_pet, screen = _physical_to_logical(pet_rect)

        side = None if self.cfg.side == "auto" else self.cfg.side
        placement: BubblePlacement = place_bubble(
            logical_pet,
            Rect(int(screen.x()), int(screen.y()), int(screen.width()), int(screen.height())),
            self.width(),
            self.height(),
            side=side,
            gap=self.cfg.gap_px,
            margin=self.cfg.screen_margin_px,
        )
        side_changed = placement.side != self._side
        above_changed = placement.above != self._above
        tail_changed = abs(placement.tail_ratio - self._tail_ratio) > 1e-6
        self._side = placement.side
        self._tail_ratio = placement.tail_ratio
        self._above = placement.above
        # side/tail 只影响尾巴与左右贴法，不改变气泡尺寸；只有上下翻转才需要重算 body
        if above_changed:
            if self._image_pixmap is not None:
                if self._lines:
                    self._measure_image_text()
                else:
                    self._measure_image()
            else:
                self._measure()
        if force or self.pos() != QPoint(placement.x, placement.y):
            self.move(placement.x, placement.y)
        if above_changed or side_changed or tail_changed:
            self.update()

    def _follow_pet(self) -> None:
        if not self.isVisible():
            return
        pet, _ = self._anchor_provider()
        if pet is None:
            return
        if pet != self._pet_phys:
            self._pet_phys = pet
            self._reposition()

    # ---------- 绘制 ----------

    def _tail_geometry(self) -> tuple[float, float, float, float, float]:
        """返回尾巴根部 x、尖端 x、根部 y、尖端 y、方向（上/下）。"""
        body = self._body_rect
        tx = body.left() + body.width() * self._tail_ratio
        # 离圆角远一点，尾巴从平边上长出来，不会变成"猫耳朵"
        edge = self.RADIUS + 16
        tx = min(max(tx, body.left() + edge), body.right() - edge)
        # 尾巴尖朝桌宠那边偏一点，看起来是在"对着人说话"
        lean = 9.0 if self._side == "left" else (-9.0 if self._side == "right" else 0.0)
        tip_x = tx + lean
        if self._above:
            base = body.bottom() - 2.0
            tip = body.bottom() + self.TAIL_H
            sign = 1.0
        else:
            base = body.top() + 2.0
            tip = body.top() - self.TAIL_H
            sign = -1.0
        return tx, tip_x, base, tip, sign

    def _shape_path(self) -> QPainterPath:
        """一个整体的气泡轮廓：圆角身体 + 从底边平滑长出来的尾巴。"""
        body = self._body_rect
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        path.addRoundedRect(body, self.RADIUS, self.RADIUS)

        tx, tip_x, base, tip, sign = self._tail_geometry()
        half = 13.0

        # 尾巴两侧在根部与底边相切，再收成一个圆润的小尖；
        # 和身体做并集，避免出现"圆角矩形 + 三角形"的接缝。
        tail = QPainterPath()
        tail.moveTo(tx - half, base)
        tail.cubicTo(tx - 8.0, base, tx - 4.0, base + sign * 15.0, tip_x - 1.0, tip)
        tail.lineTo(tip_x + 1.0, tip)
        tail.cubicTo(tx + 4.0, base + sign * 15.0, tx + 8.0, base, tx + half, base)
        tail.closeSubpath()

        merged = path.united(tail)
        merged.setFillRule(Qt.WindingFill)
        return merged

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setPen(Qt.NoPen)

        body = self._body_rect
        path = self._shape_path()

        _tx, tip_x, _base, tip_y, _sign = self._tail_geometry()

        accent = QColor(self.cfg.accent_color)
        if not accent.isValid():
            accent = QColor("#ff8f5e")

        # 从尾巴尖弹出来：缩小一点再回到 1.0
        tip = QPointF(tip_x, tip_y)
        painter.save()
        painter.translate(tip)
        painter.scale(self._pop, self._pop)
        painter.translate(-tip)

        # 三层柔和投影，白底上也能看出边界；颜色跟着强调色走
        if self.cfg.vivid:
            shadow_rgb = (accent.red() // 3, accent.green() // 3, accent.blue() // 3)
            alpha_base = 32
        else:
            shadow_rgb = (30, 50, 90)
            alpha_base = 18
        painter.setPen(Qt.NoPen)
        for dy, alpha in ((3, alpha_base), (7, alpha_base * 2 // 3), (11, alpha_base // 3)):
            shadow = QColor(*shadow_rgb)
            shadow.setAlpha(alpha)
            painter.setBrush(shadow)
            painter.drawPath(path.translated(0, dy))

        # 暖色外发光，远看也能注意到
        if self.cfg.vivid:
            glow = QColor(accent)
            glow.setAlpha(55)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(glow, 5.0))
            painter.drawPath(path)

        alpha = int(255 * max(0.5, min(1.0, self.cfg.opacity)))
        if self.cfg.vivid:
            fill = QColor(self.cfg.fill_color)
            if not fill.isValid():
                fill = QColor("#e9f3ff")
            fill.setAlpha(alpha)
            border = QColor(accent)
            border.setAlpha(235)
            pen_width = 2.0
        else:
            fill = QColor(255, 255, 255, alpha)
            border = QColor(150, 175, 215, 90)
            pen_width = 1.0
        painter.setBrush(fill)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        if self._image_pixmap is not None:
            image_clip = QPainterPath()
            image_clip.addRoundedRect(self._image_rect, 12.0, 12.0)
            painter.save()
            painter.setClipPath(image_clip)
            painter.drawPixmap(self._image_rect.toRect(), self._image_pixmap)
            painter.restore()
            edge = QColor(border)
            edge.setAlpha(70)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(edge, 1.0))
            painter.drawRoundedRect(self._image_rect, 12.0, 12.0)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(border, pen_width))
        painter.drawPath(path)

        # 正文：每行在等高的行盒里居中，左右和上下都不偏
        if self._lines:
            painter.setFont(self._font)
            painter.setPen(self._text_color())
            for index, line in enumerate(self._lines):
                line_rect = QRectF(
                    self._text_rect.left(),
                    self._text_rect.top() + index * self._line_h,
                    self._text_rect.width(),
                    self._line_h,
                )
                painter.drawText(line_rect, Qt.AlignHCenter | Qt.AlignVCenter, line)

        painter.restore()
        painter.end()
