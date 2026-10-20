# -*- coding: utf-8 -*-
"""诊断桌宠窗口位置（不启动 Qt、不调用模型）。

用法：
    .venv\\Scripts\\python.exe scripts\\diagnose_window.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet_eye.window import (
    enable_dpi_awareness,
    find_pet_window,
    foreground_window,
    get_monitors,
    get_pet_rect,
    idle_seconds,
    pet_sprite_rect,
)


def main() -> int:
    enable_dpi_awareness()
    win = find_pet_window()
    print("桌宠窗口：", win)
    if win:
        print("  Sprite：", pet_sprite_rect(win))
    body, _ = get_pet_rect()
    print("  身体区域（气泡锚点用）：", body)
    print("显示器：")
    for m in get_monitors():
        print(f"  - {m.device} rect={m.rect} work={m.work} primary={m.primary}")
    print("前台窗口：", foreground_window())
    print("空闲秒数：", round(idle_seconds(), 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
