# -*- coding: utf-8 -*-
"""给桌宠右键菜单用的本地控制接口。

只监听 127.0.0.1，带一个固定 token，避免随便哪个网页都能暂停/退出桌宠。
菜单补丁（dafeiyu-pet-menu）通过 HTTP 调这里，动作再经 Qt 信号回到主线程执行。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("pet_eye")

TOKEN = "xiaofeiyu-8765"
ACTIONS = {"look", "pause", "resume", "toggle", "panel", "log", "quit", "like", "dislike", "stats"}


class ControlServer(QObject):
    action = Signal(str)
    interval = Signal(float)

    def __init__(self, port: int, status_provider: Callable[[], dict], parent=None):
        super().__init__(parent)
        self.port = int(port)
        self._status_provider = status_provider
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._last_menu_at = 0.0
        self._menu_logged = False

    def start(self) -> bool:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args) -> None:  # noqa: A003
                log.debug("control %s - %s", self.address_string(), fmt % args)

            def _cors(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "content-type, x-pet-eye-token")
                self.send_header("Access-Control-Max-Age", "600")

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self._cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _authed(self) -> bool:
                if self.headers.get("X-Pet-Eye-Token", "") != TOKEN:
                    return False
                if self.headers.get("X-Pet-Eye-Client", "") == "menu":
                    outer._last_menu_at = time.time()
                    if not outer._menu_logged:
                        outer._menu_logged = True
                        log.info("桌宠右键菜单已连接")
                return True

            def _body(self) -> dict:
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    length = 0
                raw = self.rfile.read(min(length, 65536)) if length > 0 else b""
                if not raw:
                    return {}
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception:
                    return {}
                return data if isinstance(data, dict) else {}

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self._cors()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path != "/pet-eye/status":
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                if not self._authed():
                    self._send(403, {"ok": False, "error": "forbidden"})
                    return
                try:
                    status = outer._status_provider() or {}
                except Exception as e:
                    status = {"message": f"status error: {e}"}
                payload = {"ok": True, **status}
                payload["menu_seen_sec_ago"] = (
                    round(time.time() - outer._last_menu_at, 1) if outer._last_menu_at else None
                )
                self._send(200, payload)

            def do_POST(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                data = self._body()
                if not self._authed():
                    self._send(403, {"ok": False, "error": "forbidden"})
                    return
                if path == "/pet-eye/control":
                    act = str(data.get("action") or "").strip()
                    if act not in ACTIONS:
                        self._send(400, {"ok": False, "error": "bad action"})
                        return
                    outer.action.emit(act)
                    self._send(200, {"ok": True, "action": act})
                    return
                if path == "/pet-eye/interval":
                    try:
                        minutes = float(data.get("minutes") or 0)
                    except (TypeError, ValueError):
                        minutes = 0
                    if not (0.5 <= minutes <= 720):
                        self._send(400, {"ok": False, "error": "bad minutes"})
                        return
                    outer.interval.emit(minutes)
                    self._send(200, {"ok": True, "minutes": minutes})
                    return
                self._send(404, {"ok": False, "error": "not found"})

        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as e:
            log.warning("菜单控制端口 %d 不可用：%s", self.port, e)
            return False
        httpd.daemon_threads = True
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="pet-eye-control", daemon=True)
        self._thread.start()
        log.info("菜单控制已启动：http://127.0.0.1:%d/pet-eye/status", self.port)
        return True

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
