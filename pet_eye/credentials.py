# -*- coding: utf-8 -*-
"""读取 DeepSeek API Key：环境变量 → 项目 .env → DSH 凭证文件。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .paths import DSH_HOME, PROJECT_ROOT


def _from_env_file(path: Path, env_name: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == env_name:
                return v.strip().strip("\"'")
    except Exception:
        pass
    return ""


def load_api_key(env_name: str = "DEEPSEEK_API_KEY") -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value

    value = _from_env_file(PROJECT_ROOT / ".env", env_name)
    if value:
        return value

    cred = DSH_HOME / ".credentials.yaml"
    try:
        text = cred.read_text(encoding="utf-8")
        match = re.search(rf"^\s*{re.escape(env_name)}\s*:\s*(\S+)\s*$", text, flags=re.M)
        if match:
            return match.group(1).strip().strip("\"'")
    except Exception:
        pass

    raise RuntimeError(
        f"没有找到 {env_name}。请设置环境变量，或在 {PROJECT_ROOT / '.env'} 写入 "
        f"{env_name}=sk-..."
    )
