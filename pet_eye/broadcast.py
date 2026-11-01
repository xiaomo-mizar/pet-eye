# -*- coding: utf-8 -*-
"""把台词发到 dsh-pet 自带的聊天气泡里。

dsh-pet 在自己的 HTTP 路由上开了 `POST /dsh-pet-7340/broadcast`，
body 是 `{"text": "...", "image": "..."}`（image 可选，本地路径或 URL）。
这条路由挂在 DSH 的 web 服务上（默认 127.0.0.1:19387），不是桌宠 Electron 的端口。

用它可以「合并聊天框」：小肥鱼识屏不再自己画 Qt 气泡，而是把话塞给桌宠，
由桌宠用它原生的气泡说出来。
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("pet_eye")

ROUTE = "/dsh-pet-7340/broadcast"


def build_payload(text: str, image: str | Path | None = None) -> dict:
    body: dict = {"text": str(text or "").strip()}
    if image:
        path = Path(image)
        if path.is_file():
            body["image"] = "file:///" + str(path.resolve()).replace("\\", "/")
    return body


def post_line(
    text: str,
    origin: str = "http://127.0.0.1:19387",
    pet: str = "",
    image: str | Path | None = None,
    timeout: float = 5.0,
) -> bool:
    """发一条出去。失败只记日志，不抛异常（识别链路不能被它拖死）。"""
    if not str(text or "").strip():
        return False
    base = (origin or "").rstrip("/")
    if not base:
        return False
    url = f"{base}{ROUTE}"
    if pet:
        url += f"?pet={urllib.parse.quote(str(pet))}"
    payload = json.dumps(build_payload(text, image), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read(4096).decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        if isinstance(data, dict) and data.get("ok") is False:
            log.warning("桌宠拒收这条：%s", data.get("reason") or raw[:120])
            return False
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning("发到桌宠聊天框失败：%s", e)
        return False
