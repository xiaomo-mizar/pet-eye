# -*- coding: utf-8 -*-
"""日志：滚动文件 + 可选控制台。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import LOG_PATH, ensure_runtime_dir

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("pet_eye")
    if _CONFIGURED:
        return logger

    ensure_runtime_dir()
    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        console.setLevel(level)
        logger.addHandler(console)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    _CONFIGURED = True
    logger.info("日志已启动：%s", LOG_PATH)
    return logger
