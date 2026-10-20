# -*- coding: utf-8 -*-
"""项目路径与 DSH 桌宠资源的定位。

源码运行和打包（PyInstaller）运行的区别：
- 源码：项目根目录 = pet_eye 的上一级
- 打包：可执行文件所在目录 = 用户的工作目录（配置、日志、.env 都放这儿），
  程序自带的数据（默认 config.toml）在 PyInstaller 的解包目录里。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = PROJECT_ROOT

CONFIG_PATH = PROJECT_ROOT / "config.toml"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOG_PATH = RUNTIME_DIR / "pet-eye.log"
STATE_PATH = RUNTIME_DIR / "state.json"

DSH_HOME = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))).expanduser()
PET_PLUGIN_DIR = DSH_HOME / "profiles" / "desktop" / "node_modules" / "dsh-pet"
PET_CONFIG_PATH = DSH_HOME / "dsh-pet" / "main-config.json"
PET_FONT_PATH = PET_PLUGIN_DIR / "assets" / "fonts" / "上首软糖体.ttf"
PET_WINDOW_TITLE_PREFIX = "dsh-pet"

# dsh-pet 的本地路由（只读探测用；识屏本身不依赖它）
DSH_WEB_ORIGIN = os.environ.get("DSH_WEB_ORIGIN", "http://127.0.0.1:19387").rstrip("/")


def ensure_runtime_dir() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR


def ensure_user_config() -> bool:
    """打包版首次运行时，把自带的默认 config.toml 复制到可执行文件旁边。

    返回 True 表示刚刚生成了一份新的配置。
    """
    if not FROZEN or CONFIG_PATH.exists():
        return False
    src = BUNDLE_DIR / "config.toml"
    if not src.exists():
        return False
    try:
        shutil.copyfile(src, CONFIG_PATH)
        return True
    except OSError:
        return False
