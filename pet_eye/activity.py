# -*- coding: utf-8 -*-
"""用户动向采集：注册表 + PowerShell。

只读，不碰任何文件内容，只拿「名字/标题/时间」这类元数据：

- 注册表：Win+R 运行历史、地址栏敲过的路径、最近应用、Recent 快捷方式
- 文件系统：%APPDATA%\\Microsoft\\Windows\\Recent 下最近打开过的文件
- PowerShell：当前有窗口的进程快照（比较慢，单独缓存，默认 5 分钟一次）

这些信号拼成一段短文本，塞进提示词，让桌宠知道「主人今天在忙什么」。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import ActivityConfig

log = logging.getLogger("pet_eye")

_RECENT_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
_HKCU_EXPLORER = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
_HKCU_SEARCH = r"Software\Microsoft\Windows\CurrentVersion\Search\RecentApps"

# PowerShell 进程快照的缓存（跨线程共享）
_ps_lock = threading.Lock()
_ps_cache: "tuple[float, list[str]]" = (0.0, [])


@dataclass
class Activity:
    """一次采集的结果。全部是短字符串，直接进提示词。"""

    recent_files: list[str] = field(default_factory=list)
    typed_paths: list[str] = field(default_factory=list)
    run_commands: list[str] = field(default_factory=list)
    recent_apps: list[str] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)
    collected_at: float = field(default_factory=time.time)

    def is_empty(self) -> bool:
        return not any(
            (self.recent_files, self.typed_paths, self.run_commands, self.recent_apps, self.processes)
        )

    def digest(self) -> str:
        """一行摘要，用于记忆标签和去重。"""
        parts: list[str] = []
        if self.processes:
            parts.append("应用：" + "、".join(self.processes[:4]))
        if self.recent_files:
            parts.append("文件：" + "、".join(self.recent_files[:4]))
        return "；".join(parts)

    def to_prompt_block(self) -> str:
        """给模型看的块。空的话返回空串。"""
        if self.is_empty():
            return ""
        lines: list[str] = ["【主人最近的动向】"]
        if self.processes:
            lines.append("- 开着的窗口：" + "、".join(self.processes))
        if self.recent_files:
            lines.append("- 最近碰过的文件：" + "、".join(self.recent_files))
        if self.run_commands:
            lines.append("- 最近运行过：" + "、".join(self.run_commands))
        if self.typed_paths:
            lines.append("- 地址栏去过：" + "、".join(self.typed_paths))
        if self.recent_apps:
            lines.append("- 最近打开的应用：" + "、".join(self.recent_apps))
        lines.append("（这些只是线索：能不点名就不点名，别报菜名。）")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 采集工具


def _clean(value: str, limit: int = 60) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
    text = re.sub(r"\s{2,}", " ", text)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _sensitive(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return any(word and word.lower() in low for word in keywords)


def _dedupe(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 注册表


def read_run_mru(limit: int = 6) -> list[str]:
    """Win+R 的运行历史（HKCU\\...\\Explorer\\RunMRU）。"""
    if sys.platform != "win32":
        return []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HKCU_EXPLORER + r"\RunMRU") as key:
            values: list[tuple[str, str]] = []
            index = 0
            while True:
                try:
                    name, data, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if not isinstance(data, str) or name == "MRUList":
                    continue
                # 值形如 "notepad.exe\1"，去掉尾部的序号
                cmd = re.sub(r"\\[0-9]+$", "", data).strip()
                if cmd:
                    values.append((name, cmd))
            values.sort(key=lambda item: item[0])
            return [cmd for _name, cmd in values][:limit]
    except OSError:
        return []


def read_typed_paths(limit: int = 6) -> list[str]:
    """资源管理器地址栏里敲过的路径。"""
    if sys.platform != "win32":
        return []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HKCU_EXPLORER + r"\TypedPaths") as key:
            out: list[str] = []
            index = 0
            while True:
                try:
                    name, data, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if isinstance(data, str) and data.strip():
                    out.append(data.strip())
            return out[-limit:][::-1]
    except OSError:
        return []


def read_recent_apps(limit: int = 8) -> list[str]:
    """搜索框里的最近应用（RecentApps 子键的 AppId）。"""
    if sys.platform != "win32":
        return []
    try:
        import winreg

        out: list[tuple[int, str]] = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HKCU_SEARCH) as root:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, sub) as item:
                        app_id, _ = winreg.QueryValueEx(item, "AppId")
                        count, _ = winreg.QueryValueEx(item, "Count")
                except OSError:
                    continue
                if isinstance(app_id, str) and app_id.strip():
                    out.append((int(count or 0), app_id))
        out.sort(reverse=True)
        return [app for _count, app in out][:limit]
    except OSError:
        return []


def read_recent_files(limit: int = 6, recent_minutes: int = 240) -> list[str]:
    """Recent 目录里最近打开过的文件（.lnk 的文件名就是文件名）。"""
    if not _RECENT_DIR.exists():
        return []
    cutoff = time.time() - recent_minutes * 60
    rows: list[tuple[float, str]] = []
    try:
        for entry in os.scandir(_RECENT_DIR):
            if not entry.is_file() or entry.name.lower().endswith(".lnk") is False:
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                continue
            rows.append((mtime, entry.name[:-4]))
    except OSError:
        return []
    rows.sort(reverse=True)
    out: list[str] = []
    for _mtime, name in rows:
        base = name.strip()
        # 最近文件夹里常混进一长串十六进制/哈希名，没信息量
        if not base or re.fullmatch(r"[0-9A-Fa-f]{16,}", base):
            continue
        out.append(base)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- PowerShell

_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-Process |
  Where-Object { $_.ProcessName -and $_.ProcessName -ne '' } |
  Sort-Object StartTime -Descending |
  Select-Object -First 20 ProcessName |
  ConvertTo-Json -Compress
"""

# 这些是壳/输入法/设置之类，不算「主人在干什么」
_NOISE = {
    "explorer",
    "applicationframehost",
    "textinputhost",
    "systemsettings",
    "searchhost",
    "startmenuexperiencehost",
    "shellexperiencehost",
    "runtimebroker",
    "sihost",
    "ctfmon",
    "dwm",
    "conhost",
    "openconsole",
    "python",
    "pythonw",
    "powershell",
    "pwsh",
    "cmd",
    "node",
}


def _ps_process_names(cfg: ActivityConfig) -> list[str]:
    """PowerShell 取一份进程名快照（有些窗口站里只能拿到名字，拿不到标题）。"""
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _PS_SCRIPT,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=cfg.ps_timeout_sec,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = (completed.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        out: list[str] = []
        for item in data if isinstance(data, list) else []:
            name = str((item or {}).get("ProcessName") or "").strip()
            if name and name.lower() not in _NOISE and name not in out:
                out.append(name)
        return out
    except Exception as e:
        log.debug("PowerShell 进程快照失败：%s", e)
        return []


def _window_rows() -> list[str]:
    """进程内枚举可见窗口：exe 名 + 标题。比 PowerShell 稳。"""
    try:
        from .window import iter_windows
    except Exception:
        return []
    shells = {"python", "pythonw", "powershell", "pwsh", "cmd", "conhost", "node", "wsl"}
    rows: list[str] = []
    for win in iter_windows():
        title = (win.title or "").strip()
        if not title:
            continue
        stem = Path(win.exe or "").stem.strip()
        if not stem or stem.lower() in _NOISE:
            continue
        # 控制台/解释器窗口没什么信息量，标题基本就是进程名
        if stem.lower() in shells:
            continue
        rows.append(f"{stem}：{title}")
    return rows


def read_processes(cfg: ActivityConfig) -> list[str]:
    """「主人在开什么」：PowerShell 的进程名 + 进程内窗口枚举，按 ps_interval_min 缓存。"""
    global _ps_cache
    if not cfg.powershell:
        return []
    now = time.time()
    with _ps_lock:
        cached_at, cached = _ps_cache
        if cached and now - cached_at < cfg.ps_interval_min * 60:
            return cached

    rows = _window_rows()
    if len(rows) < 3:
        # 窗口枚举信息太少（比如跑在服务会话里）就用 PowerShell 的进程名补上
        for name in _ps_process_names(cfg):
            low = name.lower()
            if low in _NOISE:
                continue
            if any(low in row.lower() for row in rows):
                continue
            rows.append(name)
            if len(rows) >= 8:
                break

    with _ps_lock:
        _ps_cache = (now, rows)
    return rows


# ---------------------------------------------------------------- 对外入口


def collect(cfg: ActivityConfig, privacy_keywords: list[str] | None = None) -> Activity:
    """采一次动向。注册表/文件很快；PowerShell 有缓存。"""
    if not cfg.enabled:
        return Activity()

    skip = list(cfg.skip_keywords or [])
    skip += [str(x) for x in (privacy_keywords or [])]
    limit = max(1, cfg.max_items)

    def keep(items: list[str]) -> list[str]:
        return _dedupe([_clean(x) for x in items if x and not _sensitive(x, skip)], limit)

    activity = Activity(collected_at=time.time())
    if cfg.registry:
        try:
            activity.run_commands = keep(read_run_mru(limit))
            activity.typed_paths = keep(read_typed_paths(limit))
            activity.recent_apps = keep(read_recent_apps(limit))
            activity.recent_files = keep(
                read_recent_files(limit, cfg.recent_minutes)
            )
        except Exception as e:
            log.debug("注册表动向读取失败：%s", e)

    try:
        activity.processes = keep(read_processes(cfg))
    except Exception as e:
        log.debug("进程快照失败：%s", e)

    return activity
