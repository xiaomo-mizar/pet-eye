# -*- coding: utf-8 -*-
"""应用入口：托盘 + 控制面板 + 气泡 + 定时识屏。"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .agent import PetEyeAgent
from .bubble import SpeechBubble, display_seconds
from .capture import ScreenCapture
from .config import Config, load_config
from .context import get_scene
from .control import ControlServer
from .credentials import load_api_key
from .diversity import plan_turn
from .llm import LLMError, VisionLLM
from .logging_setup import setup_logging
from .paths import CONFIG_PATH, LOG_PATH, RUNTIME_DIR, ensure_runtime_dir, ensure_user_config
from .window import (
    foreground_title,
    get_pet_rect,
    idle_seconds,
    is_screen_locked,
)

SAMPLE_LINE = "宝宝又在认真做事呀，本鱼就在旁边陪着你，累了记得歇一会儿哦。"

# --------------------------------------------------------------------------
# 控制面板
# --------------------------------------------------------------------------


class ControlPanel(QWidget):
    def __init__(self, agent: PetEyeAgent, cfg: Config):
        super().__init__()
        self.agent = agent
        self.cfg = cfg
        self.setWindowTitle("小肥鱼识屏")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setFixedWidth(400)
        self.setStyleSheet(
            """
            QWidget { background:#f4f7fd; color:#20304a; font-family:"Microsoft YaHei UI"; font-size:13px; }
            QLabel#title { font-size:19px; font-weight:700; color:#1f3f73; }
            QLabel#sub { color:#6b7d99; }
            QLabel#last { background:#ffffff; border:1px solid #dbe6f5; border-radius:10px;
                          padding:10px 12px; font-size:14px; line-height:1.6; }
            QLabel#err { color:#c0392b; }
            QPushButton { background:#e7f0ff; border:1px solid #b9cdf0; border-radius:8px;
                          padding:7px 12px; }
            QPushButton:hover { background:#d8e6ff; }
            QPushButton#primary { background:#2f6fed; color:#ffffff; border-color:#2f6fed; font-weight:600; }
            QPushButton#primary:hover { background:#245acc; }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("🐟 小肥鱼识屏")
        title.setObjectName("title")
        layout.addWidget(title)

        self.status_label = QLabel("准备中")
        self.status_label.setObjectName("sub")
        layout.addWidget(self.status_label)

        self.next_label = QLabel("下一次：—")
        layout.addWidget(self.next_label)

        self.last_label = QLabel("还没有说过话")
        self.last_label.setObjectName("last")
        self.last_label.setWordWrap(True)
        self.last_label.setMinimumHeight(70)
        layout.addWidget(self.last_label)

        self.error_label = QLabel("")
        self.error_label.setObjectName("err")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        self.look_btn = QPushButton("立刻看一眼")
        self.look_btn.setObjectName("primary")
        self.pause_btn = QPushButton("暂停")
        self.log_btn = QPushButton("日志")
        self.quit_btn = QPushButton("退出")
        buttons.addWidget(self.look_btn)
        buttons.addWidget(self.pause_btn)
        buttons.addWidget(self.log_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.quit_btn)
        layout.addLayout(buttons)

        self.look_btn.clicked.connect(lambda *_: self._look())
        self.pause_btn.clicked.connect(lambda *_: self._toggle_pause())
        self.log_btn.clicked.connect(lambda *_: open_log())
        self.quit_btn.clicked.connect(lambda *_: QApplication.instance().quit())

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._refresh_countdown)
        self._tick.start()

        self._last_status: dict = {}
        self.update_status(agent.status())

    def _look(self) -> None:
        if self.agent.paused:
            self.status_label.setText("先点「继续」，本鱼才敢抬头看呀")
            return
        if not self.agent.trigger_now():
            self.status_label.setText("上一次还没看完，稍等一下呀")

    def _toggle_pause(self) -> None:
        self.agent.toggle_pause()

    def _refresh_countdown(self) -> None:
        if self._last_status:
            self.update_status(self.agent.status())

    def update_status(self, state: dict) -> None:
        self._last_status = state
        if state.get("paused"):
            self.status_label.setText("已暂停 · 本鱼先眯一会儿")
        elif state.get("busy"):
            self.status_label.setText("正在看屏幕…")
        else:
            self.status_label.setText(str(state.get("message") or "等你回来"))

        next_at = state.get("next_at") or 0
        if state.get("paused") or not next_at:
            self.next_label.setText("下一次：—")
        else:
            remain = max(0, int(next_at - time.time()))
            clock = time.strftime("%H:%M", time.localtime(next_at))
            self.next_label.setText(f"下一次：{clock}（还有 {remain // 60} 分 {remain % 60:02d} 秒）")

        last = state.get("last_line") or "还没有说过话"
        self.last_label.setText(last)

        err = state.get("last_error") or ""
        if err:
            self.error_label.setText("上次失败：" + err[:200])
            self.error_label.show()
        else:
            self.error_label.hide()

        self.pause_btn.setText("继续" if state.get("paused") else "暂停")


# --------------------------------------------------------------------------
# 托盘
# --------------------------------------------------------------------------


def make_icon() -> QIcon:
    """托盘图标：蓝底「鱼」，比桌宠通知图（白底）更适合托盘。"""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(QColor("#2f6fed"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    p.setPen(QColor("#ffffff"))
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(30)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignCenter, "鱼")
    p.end()
    return QIcon(pix)


def open_log() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists():
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_PATH)))
    else:
        QMessageBox.information(None, "小肥鱼识屏", f"日志还没生成：{LOG_PATH}")


# --------------------------------------------------------------------------
# 单次 / 测试
# --------------------------------------------------------------------------


def run_once(cfg: Config, app: QApplication) -> int:
    try:
        key = load_api_key(cfg.llm.api_key_env)
    except Exception as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2

    if cfg.privacy.skip_when_locked and is_screen_locked():
        print("[跳过] 屏幕已锁定")
        return 3

    title = foreground_title() or "桌面"
    low = title.lower()
    for kw in cfg.privacy.skip_window_keywords:
        if kw and kw.lower() in low:
            print(f"[跳过] 前台窗口命中隐私关键词：{kw}（{title}）")
            return 3

    pet, win = get_pet_rect()
    print(f"[窗口] pet={pet} desktop={bool(win)}", flush=True)
    shot = ScreenCapture(cfg.capture).grab([pet] if pet else [])
    print(f"[截图] {shot.width}x{shot.height} {len(shot.jpeg)} bytes hash={shot.hash}", flush=True)
    scene = get_scene(cfg.context)
    print(f"[状态] {scene.short()}", flush=True)

    try:
        llm = VisionLLM(cfg.llm, key)
        plan = plan_turn(
            scene,
            now=time.time(),
            recent_lines=[],
            recent_meta=[],
            feedback={},
            candidates=cfg.diversity.candidates,
            music_lyrics=cfg.diversity.music_lyrics,
            material_bank=cfg.diversity.material_bank,
            callback_enabled=False,
            similarity_threshold=cfg.diversity.similarity_threshold,
        )
        result = llm.generate(
            shot,
            plan,
            window_title=title,
            idle_min=idle_seconds() / 60.0,
            recent_lines=[],
            scene_block=scene.to_prompt_block(),
        )
    except LLMError as e:
        print(f"[失败] {e}", file=sys.stderr)
        return 1

    print(f"[策略] {result.strategy} / 候选 {len(result.candidates)}", flush=True)
    print(f"[台词] {result.line}", flush=True)
    print(
        f"[字数] {len(result.line)}  尝试 {result.attempts}  tokens={result.usage.get('total_tokens', '?')}",
        flush=True,
    )
    bubble = SpeechBubble(cfg.bubble)
    bubble.show_text(result.line)
    QTimer.singleShot(int((display_seconds(cfg.bubble, result.line) + 1.0) * 1000), app.quit)
    return app.exec()


def show_test_bubble(cfg: Config, app: QApplication, text: str) -> int:
    bubble = SpeechBubble(cfg.bubble)
    bubble.show_text(text)
    QTimer.singleShot(int((display_seconds(cfg.bubble, text) + 1.0) * 1000), app.quit)
    return app.exec()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pet-eye", description="小肥鱼识屏：OpenCV 截屏 + 视觉大模型 + Qt 气泡")
    ap.add_argument("--config", type=Path, default=None, help="配置文件路径（默认 config.toml）")
    ap.add_argument("--once", action="store_true", help="立刻跑一轮（截屏→模型→气泡），然后退出")
    ap.add_argument("--test-bubble", nargs="?", const=SAMPLE_LINE, metavar="TEXT", help="只测试气泡，不调用模型")
    ap.add_argument("--show-panel", action="store_true", help="启动时显示控制面板")
    ap.add_argument("--no-tray", action="store_true", help="不创建系统托盘")
    ap.add_argument("--allow-multi", action="store_true", help="允许同时运行多个实例")
    ap.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    app = QApplication([sys.argv[0]])
    app.setApplicationName("小肥鱼识屏")
    app.setQuitOnLastWindowClosed(False)

    logger = setup_logging(getattr(logging, args.log_level))
    if ensure_user_config():
        logger.info("首次运行：已在程序目录生成 config.toml")
    try:
        cfg = load_config(args.config)
    except Exception as e:
        logger.exception("配置读取失败")
        QMessageBox.critical(None, "小肥鱼识屏", f"配置读取失败：\n{e}")
        return 2

    if args.test_bubble is not None:
        return show_test_bubble(cfg, app, args.test_bubble)

    if args.once:
        return run_once(cfg, app)

    try:
        api_key = load_api_key(cfg.llm.api_key_env)
    except Exception as e:
        logger.error("API Key 缺失：%s", e)
        QMessageBox.critical(
            None,
            "小肥鱼识屏",
            "没有找到 DeepSeek API Key。\n\n"
            "请设置环境变量 DEEPSEEK_API_KEY，或在项目根目录写 .env：\n"
            "DEEPSEEK_API_KEY=sk-...\n\n"
            f"详情：{e}",
        )
        return 2

    ensure_runtime_dir()
    lock: QLockFile | None = None
    if not args.allow_multi:
        lock = QLockFile(str(RUNTIME_DIR / "pet-eye.lock"))
        if not lock.tryLock(100):
            QMessageBox.information(None, "小肥鱼识屏", "小肥鱼已经在陪主人了，看看系统托盘～")
            return 0
        app._pet_eye_lock = lock  # type: ignore[attr-defined]

    bubble = SpeechBubble(cfg.bubble)
    agent = PetEyeAgent(cfg, api_key, config_path=args.config or CONFIG_PATH)
    panel = ControlPanel(agent, cfg)

    control: ControlServer | None = None
    if cfg.control.enabled:

        def handle_control(action: str) -> None:
            if action == "look":
                agent.trigger_now()
            elif action == "pause":
                agent.pause()
            elif action == "resume":
                agent.resume()
            elif action == "toggle":
                agent.toggle_pause()
            elif action == "panel":
                panel.show()
                panel.raise_()
                panel.activateWindow()
            elif action == "log":
                open_log()
            elif action in {"like", "dislike"}:
                message = agent.feedback(action)
                if message:
                    log.info(message)
            elif action == "stats":
                QMessageBox.information(None, "小肥鱼周报", agent.stats_text())
            elif action == "quit":
                app.quit()

        control = ControlServer(cfg.control.port, agent.status)
        control.action.connect(handle_control, Qt.QueuedConnection)
        control.interval.connect(agent.set_interval_minutes, Qt.QueuedConnection)
        if control.start():
            app.aboutToQuit.connect(control.stop)
        else:
            control = None

    if not args.no_tray and QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(make_icon(), app)
        tray.setToolTip("小肥鱼识屏 · 正在陪着你")

        from PySide6.QtWidgets import QMenu

        qmenu = QMenu()
        act_look = QAction("立刻看一眼", qmenu)
        act_pause = QAction("暂停", qmenu)
        act_pause.setCheckable(True)
        act_panel = QAction("控制面板", qmenu)
        act_log = QAction("打开日志", qmenu)
        act_quit = QAction("退出", qmenu)
        qmenu.addAction(act_look)
        qmenu.addAction(act_pause)
        qmenu.addSeparator()
        qmenu.addAction(act_panel)
        qmenu.addAction(act_log)
        qmenu.addSeparator()
        qmenu.addAction(act_quit)
        tray.setContextMenu(qmenu)
        app._pet_eye_menu = qmenu  # type: ignore[attr-defined]
        app._pet_eye_tray = tray  # type: ignore[attr-defined]

        def manual_look() -> None:
            if not agent.trigger_now():
                tray.showMessage("小肥鱼识屏", "上一次还没看完，稍等一下呀", QSystemTrayIcon.Information, 3000)
            else:
                tray.showMessage("小肥鱼识屏", "本鱼这就抬头看一眼～", QSystemTrayIcon.Information, 2500)

        def show_panel() -> None:
            panel.show()
            panel.raise_()
            panel.activateWindow()

        def on_pause(checked: bool) -> None:
            if checked:
                agent.pause()
            else:
                agent.resume()

        def sync_pause(state: dict) -> None:
            act_pause.blockSignals(True)
            act_pause.setChecked(bool(state.get("paused")))
            act_pause.setText("继续" if state.get("paused") else "暂停")
            act_pause.blockSignals(False)

        act_look.triggered.connect(lambda *_: manual_look())
        act_pause.toggled.connect(on_pause)
        act_panel.triggered.connect(lambda *_: show_panel())
        act_log.triggered.connect(lambda *_: open_log())
        act_quit.triggered.connect(lambda *_: app.quit())

        def on_tray_activated(reason) -> None:
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
                show_panel()

        tray.activated.connect(on_tray_activated)
        tray.messageClicked.connect(show_panel)
        tray.show()

        agent.status_changed.connect(panel.update_status)
        agent.status_changed.connect(sync_pause)

        def on_line(line: str) -> None:
            if cfg.agent.tray_notifications:
                tray.showMessage("小肥鱼", line, QSystemTrayIcon.Information, 5000)

        agent.line_ready.connect(on_line)

        def on_status_tooltip(state: dict) -> None:
            msg = state.get("last_line") or ("已暂停" if state.get("paused") else "等着陪主人")
            tray.setToolTip("小肥鱼识屏 · " + str(msg)[:36])

        agent.status_changed.connect(on_status_tooltip)
    else:
        agent.status_changed.connect(panel.update_status)
        panel.show()

    agent.line_ready.connect(bubble.show_text)
    agent.image_ready.connect(bubble.show_image_with_text)
    app.aboutToQuit.connect(agent.stop)
    if lock is not None:
        app.aboutToQuit.connect(lock.unlock)

    agent.start()
    if args.show_panel:
        panel.show()
        panel.raise_()
        panel.activateWindow()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
