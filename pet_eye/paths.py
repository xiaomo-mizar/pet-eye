# -*- coding: utf-8 -*-
"""项目路径与 DSH 桌宠资源的定位。"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
