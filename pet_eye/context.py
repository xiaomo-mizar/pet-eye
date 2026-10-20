# -*- coding: utf-8 -*-
"""识别主人此刻在听什么、玩什么、做什么。

三条来源，按可靠性排序：
1. Windows 媒体会话（SMTC）：歌名、歌手、播放状态，最准；
2. 播放器窗口标题：网易云 / QQ 音乐 / Spotify 等，托盘里也能读到歌名；
3. 前台窗口：进程名 + 标题，判断写代码 / 文档 / 聊天 / 游戏 / 看视频。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import ContextConfig
from .window import WindowInfo, foreground_window, get_monitors, iter_windows_all

log = logging.getLogger("pet_eye")

# --------------------------------------------------------------------------
# 应用 / 游戏字典
# --------------------------------------------------------------------------

# exe 文件名（小写） -> (给人看的名字, 活动分类)
_APPS: dict[str, tuple[str, str]] = {
    "code.exe": ("VS Code", "写代码"),
    "cursor.exe": ("Cursor", "写代码"),
    "devenv.exe": ("Visual Studio", "写代码"),
    "pycharm64.exe": ("PyCharm", "写代码"),
    "idea64.exe": ("IntelliJ IDEA", "写代码"),
    "webstorm64.exe": ("WebStorm", "写代码"),
    "goland64.exe": ("GoLand", "写代码"),
    "sublime_text.exe": ("Sublime Text", "写代码"),
    "notepad++.exe": ("Notepad++", "写代码"),
    "windowsterminal.exe": ("Windows Terminal", "终端"),
    "wt.exe": ("Windows Terminal", "终端"),
    "powershell.exe": ("PowerShell", "终端"),
    "pwsh.exe": ("PowerShell", "终端"),
    "cmd.exe": ("命令提示符", "终端"),
    "python.exe": ("Python", "终端"),
    "pythonw.exe": ("Python", "终端"),
    "node.exe": ("Node.js", "终端"),
    "chrome.exe": ("Chrome", "浏览网页"),
    "msedge.exe": ("Edge", "浏览网页"),
    "firefox.exe": ("Firefox", "浏览网页"),
    "brave.exe": ("Brave", "浏览网页"),
    "opera.exe": ("Opera", "浏览网页"),
    "winword.exe": ("Word", "写文档"),
    "excel.exe": ("Excel", "做表格"),
    "powerpnt.exe": ("PowerPoint", "做演示"),
    "onenote.exe": ("OneNote", "记笔记"),
    "wps.exe": ("WPS", "写文档"),
    "et.exe": ("WPS 表格", "做表格"),
    "wpp.exe": ("WPS 演示", "做演示"),
    "wechat.exe": ("微信", "聊天"),
    "weixin.exe": ("微信", "聊天"),
    "qq.exe": ("QQ", "聊天"),
    "telegram.exe": ("Telegram", "聊天"),
    "discord.exe": ("Discord", "聊天"),
    "slack.exe": ("Slack", "聊天"),
    "feishu.exe": ("飞书", "聊天"),
    "lark.exe": ("飞书", "聊天"),
    "dingtalk.exe": ("钉钉", "聊天"),
    "photoshop.exe": ("Photoshop", "做设计"),
    "illustrator.exe": ("Illustrator", "做设计"),
    "figma.exe": ("Figma", "做设计"),
    "blender.exe": ("Blender", "做设计"),
    "explorer.exe": ("文件资源管理器", "整理文件"),
    "notepad.exe": ("记事本", "写东西"),
    "deepseek harness.exe": ("DeepSeek Harness", "工作"),
    "electron.exe": ("Electron 应用", "工作"),
    "cloudmusic.exe": ("网易云音乐", "听歌"),
    "qqmusic.exe": ("QQ音乐", "听歌"),
    "spotify.exe": ("Spotify", "听歌"),
    "kugou.exe": ("酷狗音乐", "听歌"),
    "foobar2000.exe": ("foobar2000", "听歌"),
    "potplayermini64.exe": ("PotPlayer", "看视频"),
    "potplayer.exe": ("PotPlayer", "看视频"),
    "mpv.exe": ("mpv", "看视频"),
    "vlc.exe": ("VLC", "看视频"),
    "kuwo.exe": ("酷我音乐", "听歌"),
    "musicbee.exe": ("MusicBee", "听歌"),
    "aimp.exe": ("AIMP", "听歌"),
}

# exe 文件名（小写） -> 游戏名
_GAMES: dict[str, str] = {
    "genshinimpact.exe": "原神",
    "yuanshen.exe": "原神",
    "starrail.exe": "崩坏：星穹铁道",
    "zenlesszonezero.exe": "绝区零",
    "league of legends.exe": "英雄联盟",
    "valorant-win64-shipping.exe": "无畏契约",
    "cs2.exe": "CS2",
    "dota2.exe": "Dota 2",
    "pubg.exe": "PUBG",
    "deltaforceclient-win64-shipping.exe": "三角洲行动",
    "narakabladepoint.exe": "永劫无间",
    "blackmythwukong.exe": "黑神话：悟空",
    "eldenring.exe": "艾尔登法环",
    "overwatch.exe": "守望先锋",
    "apex_legends.exe": "Apex 英雄",
    "gta5.exe": "GTA5",
    "rdr2.exe": "荒野大镖客 2",
    "stardew valley.exe": "星露谷物语",
    "terraria.exe": "泰拉瑞亚",
    "hollow_knight.exe": "空洞骑士",
}

_GAME_KEYWORDS = (
    "原神",
    "星穹铁道",
    "绝区零",
    "英雄联盟",
    "无畏契约",
    "三角洲",
    "永劫无间",
    "黑神话",
    "我的世界",
    "Minecraft",
    "守望先锋",
    "Apex",
    "CS2",
    "Dota",
    "艾尔登法环",
    "星露谷",
)

_BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}

_VIDEO_HINTS = (
    "youtube",
    "bilibili",
    "哔哩哔哩",
    "netflix",
    "爱奇艺",
    "腾讯视频",
    "优酷",
    "斗鱼",
    "虎牙",
    "twitch",
    "disney+",
    "网飞",
)

# 这些应用即使全屏也不当游戏
_NOT_GAMES = (
    set(_BROWSERS)
    | {
        "code.exe",
        "cursor.exe",
        "devenv.exe",
        "pycharm64.exe",
        "idea64.exe",
        "windowsterminal.exe",
        "wt.exe",
        "powershell.exe",
        "pwsh.exe",
        "cmd.exe",
        "explorer.exe",
        "winword.exe",
        "excel.exe",
        "powerpnt.exe",
        "wps.exe",
        "et.exe",
        "wpp.exe",
        "wechat.exe",
        "weixin.exe",
        "qq.exe",
        "telegram.exe",
        "discord.exe",
        "slack.exe",
        "feishu.exe",
        "lark.exe",
        "dingtalk.exe",
        "photoshop.exe",
        "illustrator.exe",
        "figma.exe",
        "blender.exe",
        "cloudmusic.exe",
        "qqmusic.exe",
        "spotify.exe",
        "kugou.exe",
        "foobar2000.exe",
        "potplayermini64.exe",
        "potplayer.exe",
        "mpv.exe",
        "vlc.exe",
        "kuwo.exe",
        "musicbee.exe",
        "aimp.exe",
        "deepseek harness.exe",
    }
)

_PLAYERS: dict[str, tuple[str, str]] = {
    "cloudmusic.exe": ("网易云音乐", "music"),
    "qqmusic.exe": ("QQ音乐", "music"),
    "spotify.exe": ("Spotify", "music"),
    "kugou.exe": ("酷狗音乐", "music"),
    "kuwo.exe": ("酷我音乐", "music"),
    "foobar2000.exe": ("foobar2000", "music"),
    "musicbee.exe": ("MusicBee", "music"),
    "aimp.exe": ("AIMP", "music"),
    "potplayermini64.exe": ("PotPlayer", "video"),
    "potplayer.exe": ("PotPlayer", "video"),
    "vlc.exe": ("VLC", "video"),
    "mpv.exe": ("mpv", "video"),
}

_MEDIA_SUFFIXES = (
    " - 网易云音乐",
    " - QQ音乐",
    " - 酷狗音乐",
    " - 酷我音乐",
    " - Spotify",
    " - foobar2000",
    " - VLC media player",
    " - PotPlayer",
)

_GENERIC_TITLES = {
    "网易云音乐",
    "QQ音乐",
    "酷狗音乐",
    "酷我音乐",
    "Spotify",
    "Spotify Premium",
    "Spotify Free",
    "foobar2000",
    "VLC media player",
    "PotPlayer",
    "Default IME",
    "MSCTFIME UI",
    "Program Manager",
    "本地音乐",
    "我的音乐",
    "发现音乐",
    "每日推荐",
    "私人漫游",
    "播放列表",
    "我的播客",
    "搜索",
}


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class MediaInfo:
    title: str
    artist: str = ""
    app: str = ""
    # True 播放中 / False 暂停 / None 未知（窗口标题兜底时拿不到状态）
    playing: bool | None = None
    kind: str = "music"  # music / video

    def label(self) -> str:
        who = f"《{self.title}》" + (f" — {self.artist}" if self.artist else "")
        verb = "正在听" if self.kind == "music" else "正在看"
        if self.playing is True:
            state = "，播放中"
        elif self.playing is False:
            state = "，已暂停"
        else:
            state = ""
        return f"{verb}：{who}（{self.app or '媒体播放器'}{state}）"


@dataclass
class Scene:
    media: MediaInfo | None = None
    app: str = ""
    exe: str = ""
    window_title: str = ""
    category: str = ""
    game: str = ""
    fullscreen: bool = False

    def to_prompt_block(self) -> str:
        lines: list[str] = ["【主人此刻的状态】"]
        if self.media and self.media.title:
            lines.append("- " + self.media.label())
        elif self.category != "看视频":
            lines.append("- 没有检测到正在播放的音乐或视频。")

        if self.game:
            lines.append(f"- 正在玩游戏：《{self.game}》")
        elif self.app and self.app != "桌面":
            title = self.window_title.strip()
            if len(title) > 70:
                title = title[:67] + "…"
            extra = f"（{self.category}）" if self.category else ""
            tail = f"，窗口「{title}」" if title else ""
            lines.append(f"- 前台：{self.app}{extra}{tail}")
        elif self.category == "看视频":
            lines.append("- 前台像是在看视频。")
        return "\n".join(lines)

    def short(self) -> str:
        parts: list[str] = []
        if self.media and self.media.title:
            verb = "听" if self.media.kind == "music" else "看"
            parts.append(f"{verb}《{self.media.title}》")
        if self.game:
            parts.append(f"游戏《{self.game}》")
        elif self.app and self.app != "桌面":
            parts.append(f"{self.category or '使用'}·{self.app}")
        return " / ".join(parts) or "桌面"


# --------------------------------------------------------------------------
# 媒体：系统会话 + 播放器窗口标题
# --------------------------------------------------------------------------


def _friendly_media_app(aumid: str) -> str:
    a = (aumid or "").lower()
    for key, name in (
        ("spotify", "Spotify"),
        ("cloudmusic", "网易云音乐"),
        ("netease", "网易云音乐"),
        ("qqmusic", "QQ音乐"),
        ("kugou", "酷狗音乐"),
        ("kuwo", "酷我音乐"),
        ("foobar", "foobar2000"),
        ("potplayer", "PotPlayer"),
        ("vlc", "VLC"),
        ("zunemusic", "媒体播放器"),
        ("music.ui", "媒体播放器"),
        ("chrome", "Chrome"),
        ("msedge", "Edge"),
        ("firefox", "Firefox"),
    ):
        if key in a:
            return name
    head = (aumid or "").split("!")[0]
    return head.split("_")[0] or "媒体播放器"


def _read_smtc() -> MediaInfo | None:
    """Windows 媒体会话：最准，能拿到播放/暂停状态。"""
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
        )
    except Exception:
        return None

    async def read() -> MediaInfo | None:
        manager = await Manager.request_async()
        session = manager.get_current_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        title = (props.title or "").strip()
        if not title:
            return None
        info = session.get_playback_info()
        status = int(info.playback_status)
        kind = "video" if int(info.playback_type) == 2 else "music"
        return MediaInfo(
            title=title,
            artist=(props.artist or "").strip(),
            app=_friendly_media_app(session.source_app_user_model_id),
            playing=True if status == 4 else (False if status == 5 else None),
            kind=kind,
        )

    try:
        return asyncio.run(asyncio.wait_for(read(), timeout=2.0))
    except Exception as e:
        log.debug("媒体会话读取失败：%s", e)
        return None


def _clean_title(raw: str) -> str:
    return (raw or "").replace("\u00a0", " ").strip()


def _parse_track(title: str) -> tuple[str, str]:
    t = _clean_title(title)
    for suffix in _MEDIA_SUFFIXES:
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    for sep in (" • ", " – ", " - "):
        if sep in t:
            left, right = t.rsplit(sep, 1)
            left, right = left.strip(), right.strip()
            if left and right:
                return left, right
    return t, ""


def _audio_active_pids() -> set[int] | None:
    """当前真的在出声的进程 PID。

    播放器缩在托盘、窗口标题还留着上一首歌时，光看标题会误判；
    Windows 音频会话的 State/Peak 才代表“有没有在放”。
    """
    try:
        import comtypes
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        from pycaw.api.endpointvolume import IAudioMeterInformation
        from pycaw.pycaw import AudioUtilities
    except Exception:
        return None

    try:
        active: set[int] = set()
        for session in AudioUtilities.GetAllSessions():
            pid = int(getattr(session, "ProcessId", 0) or 0)
            if not pid:
                continue
            try:
                state = int(getattr(session, "State", 0) or 0)
            except Exception:
                state = 0
            peak = 0.0
            try:
                meter = session._ctl.QueryInterface(IAudioMeterInformation)
                peak = float(meter.GetPeakValue())
            except Exception:
                peak = 0.0
            if state == 1 or peak > 0.001:
                active.add(pid)
        return active
    except Exception as e:
        log.debug("读取音频会话失败：%s", e)
        return None


def _read_player_windows() -> MediaInfo | None:
    """从播放器窗口标题兜底；必须同时确认这个进程真的在出声。"""
    active = _audio_active_pids()
    if active is None:
        # 没有音频会话能力时宁可不报，避免把“上次没关的歌”当成正在听
        return None
    best: tuple[int, MediaInfo] | None = None
    for win in iter_windows_all():
        base = Path(win.exe).name.lower() if win.exe else ""
        meta = _PLAYERS.get(base)
        if not meta:
            continue
        if win.pid not in active:
            continue
        app, kind = meta
        title = _clean_title(win.title)
        if not title or title in _GENERIC_TITLES or title.startswith("GDI+"):
            continue
        if title.lower().endswith(".exe"):
            continue

        score = 0
        if " • " in title or " - " in title:
            score += 4
        if len(title) >= 6:
            score += 1
        if win.visible and not win.minimized:
            score += 2
        elif not win.minimized:
            score += 1

        song, artist = _parse_track(title)
        if not song:
            continue
        info = MediaInfo(title=song, artist=artist, app=app, playing=True, kind=kind)
        if best is None or score > best[0]:
            best = (score, info)
    return best[1] if best else None


def read_media() -> MediaInfo | None:
    info = _read_smtc()
    # 只认“确实在播放”的会话；暂停/停止时宁可不说，也不要报上一首
    if info and info.title and info.playing is True:
        return info
    return _read_player_windows()


# --------------------------------------------------------------------------
# 前台应用
# --------------------------------------------------------------------------


def _looks_like_video(title: str) -> bool:
    low = title.lower()
    return any(h in low for h in _VIDEO_HINTS)


def _is_fullscreen(win: WindowInfo) -> bool:
    if win.rect.w <= 0 or win.rect.h <= 0:
        return False
    cx = win.rect.x + win.rect.w / 2
    cy = win.rect.y + win.rect.h / 2
    for mon in get_monitors():
        if mon.rect.contains(cx, cy):
            return win.rect.w >= mon.rect.w * 0.97 and win.rect.h >= mon.rect.h * 0.94
    return False


def _classify(win: WindowInfo | None, media: MediaInfo | None, cfg: ContextConfig) -> Scene:
    scene = Scene(media=media)
    if win is None:
        return scene

    scene.exe = Path(win.exe).name.lower() if win.exe else ""
    scene.window_title = (win.title or "").strip()
    title_low = scene.window_title.lower()
    scene.fullscreen = _is_fullscreen(win)

    game = _GAMES.get(scene.exe, "")
    if not game and scene.exe == "javaw.exe" and "minecraft" in title_low:
        game = "Minecraft"
    if not game and cfg.games and scene.exe not in _NOT_GAMES:
        for kw in _GAME_KEYWORDS:
            if kw.lower() in title_low:
                game = kw
                break

    app, category = _APPS.get(scene.exe, ("", ""))
    if not app:
        stem = Path(scene.exe).stem
        app = stem.replace("_", " ").replace("-", " ").strip().title() or "桌面"
    scene.app = app
    scene.category = category

    if game:
        scene.game = game
        scene.category = "游戏"
    elif scene.fullscreen and cfg.games and scene.exe not in _NOT_GAMES:
        scene.category = "游戏"
        scene.game = ""
    elif scene.exe in _BROWSERS and _looks_like_video(scene.window_title):
        scene.category = "看视频"
    elif media and media.kind == "video" and scene.exe in _BROWSERS:
        scene.category = "看视频"
    return scene


def get_scene(cfg: ContextConfig) -> Scene:
    """在后台线程调用；任何探测失败都只退回少一点信息，不抛异常。"""
    if not cfg.enabled:
        return Scene()
    try:
        media = read_media() if cfg.media else None
    except Exception as e:
        log.debug("读取媒体信息失败：%s", e)
        media = None
    try:
        win = foreground_window()
    except Exception as e:
        log.debug("读取前台窗口失败：%s", e)
        win = None
    try:
        return _classify(win, media, cfg)
    except Exception as e:
        log.debug("识别当前状态失败：%s", e)
        return Scene(media=media)
