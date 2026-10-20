# -*- coding: utf-8 -*-
"""Win32 窗口探测：找到 dsh-pet 桌面窗、前台窗口、空闲时间、显示器工作区。

只读，不修改桌宠；找不到桌面窗时退回 main-config.json 的角落配置。
"""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .geometry import Rect
from .paths import PET_CONFIG_PATH, PET_WINDOW_TITLE_PREFIX

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
MONITOR_DEFAULTTONEAREST = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_CACHED_PET_HWND = 0

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
MonitorEnumProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    exe: str
    rect: Rect
    exstyle: int
    visible: bool = True
    minimized: bool = False


@dataclass(frozen=True)
class MonitorInfo:
    device: str
    rect: Rect
    work: Rect
    primary: bool


def enable_dpi_awareness() -> None:
    """在创建 QApplication 之前调用，保证 Win32 坐标是物理像素。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def _exe_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_rect(hwnd: int) -> Rect:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return Rect(int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top))


def iter_windows() -> Iterator[WindowInfo]:
    """枚举可见、有标题、非最小化的顶层窗口。"""
    result: list[WindowInfo] = []

    def cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        title = _window_text(hwnd)
        rect = _window_rect(hwnd)
        if rect.w < 40 or rect.h < 40:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        result.append(
            WindowInfo(
                hwnd=int(hwnd),
                pid=int(pid.value),
                title=title,
                exe=_exe_path(int(pid.value)),
                rect=rect,
                exstyle=int(exstyle),
            )
        )
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    yield from result


def iter_windows_all() -> Iterator[WindowInfo]:
    """所有带标题的顶层窗口，包括隐藏/最小化的。

    播放器缩到托盘后主窗口通常还留着标题（当前歌名），SMTC 不可用时靠它。
    """
    result: list[WindowInfo] = []

    def cb(hwnd: int, _lparam: int) -> bool:
        title = _window_text(hwnd)
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        result.append(
            WindowInfo(
                hwnd=int(hwnd),
                pid=int(pid.value),
                title=title,
                exe=_exe_path(int(pid.value)),
                rect=_window_rect(hwnd),
                exstyle=int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE)),
                visible=bool(user32.IsWindowVisible(hwnd)),
                minimized=bool(user32.IsIconic(hwnd)),
            )
        )
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    yield from result


def find_pet_window() -> WindowInfo | None:
    """找到 dsh-pet 的桌面小窗。

    首选标题以 dsh-pet 开头；其次找 dsh-pet 用的 Electron 透明置顶小窗。
    """
    titled: list[WindowInfo] = []
    electron: list[WindowInfo] = []
    for win in iter_windows():
        if win.title.lower().startswith(PET_WINDOW_TITLE_PREFIX.lower()):
            titled.append(win)
            continue
        exe = win.exe.lower()
        if "electron.exe" not in exe:
            continue
        if "dsh-pet" not in exe and ".dsh" not in exe:
            continue
        if not (win.exstyle & WS_EX_LAYERED):
            continue
        if not (win.exstyle & WS_EX_TOOLWINDOW):
            continue
        if not (300 <= win.rect.w <= 1600 and 200 <= win.rect.h <= 1200):
            continue
        electron.append(win)

    candidates = titled or electron
    if not candidates:
        return None
    # 多只桌宠时取面积最大的那只（通常是主宠）
    return max(candidates, key=lambda w: w.rect.w * w.rect.h)


def pet_sprite_rect(win: WindowInfo, fallback_size: int = 462) -> Rect:
    """从窗口矩形推算宠物 Sprite 包围盒（窗口 = sprite + 四周 0.5*size 余量）。"""
    size = int(round(win.rect.w / 2)) or fallback_size
    margin = size // 2
    sprite_w = size
    sprite_h = int(round(size * 9 / 16))
    return Rect(win.rect.x + margin, win.rect.y + margin, sprite_w, sprite_h)


def pet_body_rect(win: WindowInfo, fallback_size: int = 462) -> Rect:
    """Sprite 里真正有画面的身体区域（对应 dsh-pet 的 HIT_BOX 200..440 / 50..335）。

    透明视频四周有大量留白，气泡必须贴着"看得见的身体"，而不是贴 Sprite 边框。
    """
    sprite = pet_sprite_rect(win, fallback_size)
    x0 = sprite.x + round(sprite.w * 200 / 640)
    x1 = sprite.x + round(sprite.w * 440 / 640)
    y0 = sprite.y + round(sprite.h * 50 / 360)
    y1 = sprite.y + round(sprite.h * 335 / 360)
    return Rect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def get_monitors() -> list[MonitorInfo]:
    monitors: list[MonitorInfo] = []

    def cb(hmon: int, _hdc: int, _lprect, _lparam: int) -> bool:
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return True
        r = mi.rcMonitor
        w = mi.rcWork
        monitors.append(
            MonitorInfo(
                device=mi.szDevice,
                rect=Rect(int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)),
                work=Rect(int(w.left), int(w.top), int(w.right - w.left), int(w.bottom - w.top)),
                primary=bool(mi.dwFlags & 1),
            )
        )
        return True

    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(cb), 0)
    return monitors


def screen_rect_for(pet: Rect) -> Rect:
    """返回桌宠所在显示器的工作区（不含任务栏）；找不到就用主屏。"""
    monitors = get_monitors()
    if not monitors:
        return Rect(0, 0, 1920, 1080)
    for m in monitors:
        if m.rect.contains(pet.center_x, pet.center_y):
            return m.work
    for m in monitors:
        if m.primary:
            return m.work
    return monitors[0].work


def config_fallback_pet_rect() -> Rect | None:
    """没有桌面窗时，用 dsh-pet 的 main-config.json 估算桌宠位置。"""
    try:
        data = json.loads(Path(PET_CONFIG_PATH).read_text(encoding="utf-8"))
        pets = data.get("pets") or []
        if not pets:
            return None
        pet = pets[0]
        size = int(pet.get("size") or 462)
        pos = pet.get("position") or {}
        corner = str(pos.get("corner") or "top-right")
        mx = int(pos.get("marginX") or 24)
        my = int(pos.get("marginY") or 100)
    except Exception:
        return None

    monitors = get_monitors()
    work = next((m.work for m in monitors if m.primary), monitors[0].work if monitors else Rect(0, 0, 1920, 1080))
    h = int(round(size * 9 / 16))
    if "right" in corner:
        x = work.right - mx - size
    else:
        x = work.x + mx
    if "bottom" in corner:
        y = work.bottom - my - h
    else:
        y = work.y + my
    sprite = Rect(int(x), int(y), size, h)
    body = Rect(
        sprite.x + round(sprite.w * 200 / 640),
        sprite.y + round(sprite.h * 50 / 360),
        max(1, round(sprite.w * 240 / 640)),
        max(1, round(sprite.h * 285 / 360)),
    )
    return body


def get_pet_rect() -> tuple[Rect | None, WindowInfo | None]:
    """返回桌宠身体矩形。

    拖拽时气泡会以 30fps 调这里；如果每次都 EnumWindows + OpenProcess，
    会明显卡顿。所以找到一次后缓存 HWND，之后只做 IsWindow/GetWindowRect。
    """
    global _CACHED_PET_HWND

    if _CACHED_PET_HWND:
        win = _quick_window_info(_CACHED_PET_HWND)
        if win is not None:
            return pet_body_rect(win), win
        _CACHED_PET_HWND = 0

    win = find_pet_window()
    if win is not None:
        _CACHED_PET_HWND = win.hwnd
        return pet_body_rect(win), win
    return config_fallback_pet_rect(), None


def _quick_window_info(hwnd: int) -> WindowInfo | None:
    """缓存 HWND 的轻量读取：不枚举、不查进程路径。"""
    if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = _window_rect(hwnd)
    if rect.w < 40 or rect.h < 40:
        return None
    title = _window_text(hwnd)
    exstyle = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
    is_pet = title.lower().startswith(PET_WINDOW_TITLE_PREFIX.lower())
    if not is_pet:
        is_pet = bool(exstyle & WS_EX_LAYERED) and bool(exstyle & WS_EX_TOOLWINDOW) and 300 <= rect.w <= 1600
    if not is_pet:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return WindowInfo(
        hwnd=int(hwnd),
        pid=int(pid.value),
        title=title,
        exe="",
        rect=rect,
        exstyle=exstyle,
    )


def foreground_window() -> WindowInfo | None:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if int(pid.value) == os.getpid():
        return None
    return WindowInfo(
        hwnd=int(hwnd),
        pid=int(pid.value),
        title=_window_text(hwnd),
        exe=_exe_path(int(pid.value)),
        rect=_window_rect(hwnd),
        exstyle=int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE)),
    )


def is_screen_locked() -> bool:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    cls = buf.value.lower()
    return cls in {"lockapp", "logonui"}


def idle_seconds() -> float:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    tick = kernel32.GetTickCount64() & 0xFFFFFFFF
    delta = (tick - int(info.dwTime)) & 0xFFFFFFFF
    return delta / 1000.0


def foreground_title() -> str:
    win = foreground_window()
    return win.title if win else ""
