# -*- coding: utf-8 -*-
"""听歌 / 游戏 / 工作识别的单测（不触碰真实窗口）。"""

from pet_eye.config import ContextConfig
from pet_eye.context import MediaInfo, Scene, _classify, _parse_track
from pet_eye.geometry import Rect
from pet_eye.window import WindowInfo, get_monitors


def _win(exe: str, title: str, rect: Rect | None = None) -> WindowInfo:
    return WindowInfo(
        hwnd=1,
        pid=2,
        title=title,
        exe=rf"C:\Apps\{exe}",
        rect=rect or Rect(100, 100, 1200, 800),
        exstyle=0,
    )


def test_parse_track_from_player_titles():
    assert _parse_track("Chasing Lights - 千坂") == ("Chasing Lights", "千坂")
    assert _parse_track("Song - Artist - 网易云音乐") == ("Song", "Artist")
    assert _parse_track("Song • Artist") == ("Song", "Artist")
    assert _parse_track("没有分隔符") == ("没有分隔符", "")


def test_game_by_process():
    scene = _classify(_win("GenshinImpact.exe", "原神"), None, ContextConfig())
    assert scene.category == "游戏"
    assert scene.game == "原神"


def test_game_by_fullscreen_guess():
    screen = get_monitors()[0].rect
    scene = _classify(
        _win("SomeGame-Win64-Shipping.exe", "Some Game", screen),
        None,
        ContextConfig(),
    )
    assert scene.category == "游戏"


def test_browser_video():
    scene = _classify(
        _win("chrome.exe", "【4K】某视频_哔哩哔哩_bilibili"),
        None,
        ContextConfig(),
    )
    assert scene.category == "看视频"


def test_code_and_terminal():
    assert _classify(_win("Code.exe", "context.py - VS Code"), None, ContextConfig()).category == "写代码"
    assert _classify(_win("WindowsTerminal.exe", "PowerShell"), None, ContextConfig()).category == "终端"


def test_media_in_prompt_block():
    scene = Scene(
        media=MediaInfo(title="Chasing Lights", artist="千坂", app="网易云音乐", playing=True),
        app="VS Code",
        exe="code.exe",
        window_title="context.py - VS Code",
        category="写代码",
    )
    block = scene.to_prompt_block()
    assert "Chasing Lights" in block
    assert "千坂" in block
    assert "VS Code" in block
    assert "正在听" in scene.media.label()


def test_smtc_video_and_pause_label():
    media = MediaInfo(title="某视频", app="Chrome", playing=False, kind="video")
    assert "已暂停" in media.label()
    assert "正在看" in media.label()
