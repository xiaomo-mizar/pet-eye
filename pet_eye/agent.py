# -*- coding: utf-8 -*-
"""调度器：按配置间隔看一眼屏幕，生成一句话并交给气泡。"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .activity import Activity, collect as collect_activity
from .capture import ScreenCapture, Shot, hamming
from .config import Config
from .context import get_scene
from .diversity import STRATEGY_BY_ID, TurnPlan, plan_turn, screen_digest, stats_summary
from .llm import LLMError, VisionLLM
from .meme import MemeLibrary, fallback_caption
from .memory import MemoryStore, forget_due_seconds
from .paths import MEMORY_PATH, STATE_PATH, ensure_runtime_dir
from .window import (
    foreground_window,
    get_pet_rect,
    idle_seconds,
    is_screen_locked,
)

log = logging.getLogger("pet_eye")


@dataclass
class Session:
    recent_lines: list[str] = field(default_factory=list)
    recent_meta: list[dict] = field(default_factory=list)
    feedback: dict = field(default_factory=dict)
    pending_question: str = ""
    pending_at: float = 0.0
    pending_turns: int = 0
    recent_materials: list[str] = field(default_factory=list)
    last_hash: str = ""
    last_exe: str = ""
    last_switch_at: float = 0.0
    last_change_at: float = 0.0
    since_meme: int = 0
    recent_meme_keys: list[str] = field(default_factory=list)


@dataclass
class CycleResult:
    ok: bool = False
    line: str = ""
    skipped: str = ""
    error: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    shot_hash: str = ""
    title: str = ""
    idle_min: float = 0.0
    attempts: int = 0
    scene_short: str = ""
    strategy: str = ""
    candidates: list[dict] = field(default_factory=list)
    novelty: float = 0.0
    exe: str = ""
    media_key: str = ""
    digest: str = ""
    changed: bool = False
    same_app_min: float = 0.0
    material_id: str = ""
    pattern: str = ""
    bucket: tuple[int, int] = (20, 50)
    meme_path: str = ""
    meme_caption: str = ""
    meme_line: str = ""
    meme_key: str = ""
    meme_tag: str = ""
    remember: list[tuple[str, str]] = field(default_factory=list)
    activity_digest: str = ""
    scene_text: str = ""


class WorkerSignals(QObject):
    done = Signal(object)


class CycleWorker(QRunnable):
    def __init__(
        self,
        cfg: Config,
        capture: ScreenCapture,
        llm: VisionLLM,
        session: Session,
        memes: MemeLibrary,
        memory: MemoryStore,
    ):
        super().__init__()
        self.cfg = cfg
        self.capture = capture
        self.llm = llm
        self.session = session
        self.memes = memes
        self.memory = memory
        self.signals = WorkerSignals()

    def run(self) -> None:
        result = CycleResult()
        try:
            pet, _win = get_pet_rect()
            shot: Shot = self.capture.grab([pet] if pet else [])
            result.shot_hash = shot.hash
            threshold = self.cfg.capture.change_threshold
            if (
                self.cfg.capture.skip_if_unchanged
                and self.session.last_hash
                and hamming(shot.hash, self.session.last_hash) <= threshold
            ):
                result.ok = False
                result.skipped = "画面没有明显变化"
                self.signals.done.emit(result)
                return

            win = foreground_window()
            title = (win.title if win else "桌面").strip() or "桌面"
            result.title = title
            result.idle_min = idle_seconds() / 60.0
            scene = get_scene(self.cfg.context)
            result.scene_short = scene.short()
            result.exe = scene.exe
            media = getattr(scene, "media", None)
            result.media_key = (
                f"{getattr(media, 'title', '')}|{getattr(media, 'artist', '')}" if media else ""
            )

            # 用户动向：注册表 + PowerShell（PowerShell 部分有缓存，不会每轮都跑）
            activity = Activity()
            if self.cfg.activity.enabled:
                try:
                    activity = collect_activity(
                        self.cfg.activity, self.cfg.privacy.skip_window_keywords
                    )
                except Exception as e:
                    log.debug("动向采集失败：%s", e)
            result.activity_digest = activity.digest()

            # 记忆：长期（向量召回）+ 短期（最近 3 轮）
            memory_block = ""
            window_block = ""
            if self.cfg.memory.enabled:
                try:
                    query = " ".join(
                        part for part in (scene.short(), title, activity.digest()) if part
                    )
                    memory_block = self.memory.memory_block(query)
                    window_block = self.memory.window_block()
                except Exception as e:
                    log.debug("记忆召回失败：%s", e)

            now = time.time()
            if self.session.last_hash:
                digest, changed = screen_digest(shot.hash, self.session.last_hash, threshold)
            else:
                digest, changed = "", True
            if not self.cfg.diversity.screen_digest:
                digest = ""
            result.digest = digest
            result.changed = changed

            same_app_min = 0.0
            if (
                scene.exe
                and self.session.last_exe == scene.exe
                and self.session.last_switch_at
            ):
                same_app_min = max(0.0, (now - self.session.last_switch_at) / 60.0)
            result.same_app_min = same_app_min
            stale_min = (
                max(0.0, (now - self.session.last_change_at) / 60.0)
                if self.session.last_change_at
                else 0.0
            )

            if self.cfg.diversity.enabled:
                plan = plan_turn(
                    scene,
                    now=now,
                    recent_lines=self.session.recent_lines,
                    recent_meta=self.session.recent_meta,
                    feedback=self.session.feedback,
                    pending_question=self.session.pending_question,
                    pending_at=self.session.pending_at,
                    recent_materials=self.session.recent_materials,
                    same_app_min=same_app_min,
                    stale_min=stale_min,
                    digest=digest,
                    changed=changed,
                    candidates=self.cfg.diversity.candidates,
                    music_lyrics=self.cfg.diversity.music_lyrics,
                    material_bank=self.cfg.diversity.material_bank,
                    callback_enabled=self.cfg.diversity.callback,
                    similarity_threshold=self.cfg.diversity.similarity_threshold,
                    rng=random.Random(),
                )
            else:
                plan = TurnPlan(
                    strategies=[STRATEGY_BY_ID["care"]],
                    bucket=(20, 50),
                    pattern="陈述",
                )
            result.pattern = plan.pattern
            result.bucket = plan.bucket
            result.material_id = plan.material_id

            if (
                self.cfg.meme.enabled
                and self.session.since_meme >= max(1, self.cfg.meme.every - 1)
                and self.memes.count > 0
            ):
                tone = plan.strategies[0]
                media_title = getattr(media, "title", "") if media else ""
                context = " ".join(
                    part
                    for part in (
                        scene.short(),
                        title,
                        getattr(scene, "category", ""),
                        getattr(scene, "game", ""),
                        media_title,
                    )
                    if part
                )
                meme_pool = self.memes.candidates(
                    tone=tone.id,
                    context=context,
                    recent_keys=self.session.recent_meme_keys,
                    k=12,
                )
                usage: dict[str, Any] = {}
                picked = None
                meme_line = ""
                if meme_pool:
                    try:
                        picked, meme_line, usage = self.llm.pick_meme(
                            shot,
                            meme_pool,
                            window_title=title,
                            scene_block=scene.to_prompt_block(),
                            tone_label=tone.label,
                            recent_lines=self.session.recent_lines[-3:],
                            banned_words=plan.banned_words,
                        )
                    except Exception as e:
                        log.warning("挑表情包失败：%s", e)
                    if picked is None:
                        picked = meme_pool[0]
                        meme_line = fallback_caption(picked.tag)
                if picked is not None:
                    result.ok = True
                    result.meme_path = str(picked.path)
                    result.meme_caption = picked.caption
                    result.meme_line = meme_line or fallback_caption(picked.tag)
                    result.meme_key = picked.key
                    result.meme_tag = picked.tag
                    result.strategy = "meme"
                    result.usage = usage
                    result.attempts = 1
                    self.signals.done.emit(result)
                    return

            speech = self.llm.generate(
                shot,
                plan,
                window_title=title,
                idle_min=result.idle_min,
                recent_lines=self.session.recent_lines,
                scene_block=scene.to_prompt_block(),
                activity_block=activity.to_prompt_block(),
                memory_block=memory_block,
                window_block=window_block,
            )
            result.ok = True
            result.line = speech.line
            result.usage = speech.usage
            result.attempts = speech.attempts
            result.strategy = speech.strategy
            result.candidates = speech.candidates
            result.novelty = speech.novelty
            result.remember = list(speech.remember)
            result.scene_text = scene.short()
        except LLMError as e:
            result.error = str(e)
        except Exception as e:  # 截图/窗口/编码失败都不应该让进程崩
            result.error = f"{type(e).__name__}: {e}"
        self.signals.done.emit(result)


class PetEyeAgent(QObject):
    line_ready = Signal(str)
    image_ready = Signal(str, str)
    status_changed = Signal(dict)

    def __init__(self, cfg: Config, api_key: str, parent=None, config_path=None):
        super().__init__(parent)
        self.cfg = cfg
        self._config_path = config_path
        self.capture = ScreenCapture(cfg.capture)
        self.llm = VisionLLM(cfg.llm, api_key)
        self.memes = MemeLibrary(cfg.meme.packs)
        self.memory = MemoryStore(cfg.memory, MEMORY_PATH).load()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._on_due)
        self._rng = random.Random()
        self._paused = False
        self._busy = False
        self._stopped = False
        self._message = "准备中"
        self._last_line = ""
        self._last_error = ""
        self._last_title = ""
        self._last_scene = ""
        self._last_strategy = ""
        self._last_at = 0.0
        self._next_at = 0.0
        self._count = 0
        self._recent_lines: list[str] = []
        self._recent_meta: list[dict] = []
        self._feedback: dict = {}
        self._pending_question = ""
        self._pending_at = 0.0
        self._pending_turns = 0
        self._recent_materials: list[str] = []
        self._last_hash = ""
        self._last_exe = ""
        self._last_switch_at = 0.0
        self._last_change_at = 0.0
        self._since_meme = 0
        self._recent_meme_keys: list[str] = []
        self._history: list[dict] = []
        self._worker: CycleWorker | None = None
        self._load_state()

    # ---------- 生命周期 ----------

    def start(self) -> None:
        self._stopped = False
        self._paused = False
        self._message = "启动中"
        min_sec = self.cfg.agent.min_interval_min * 60
        max_sec = self.cfg.agent.max_interval_min * 60
        if abs(min_sec - max_sec) < 1e-6:
            interval = f"{min_sec:.0f} 秒"
        else:
            interval = f"{min_sec:.0f}~{max_sec:.0f} 秒"
        log.info("识屏智能体启动：每 %s一次，模型 %s", interval, self.cfg.llm.model)
        if self.cfg.memory.enabled:
            stats = self.memory.stats()
            log.info(
                "记忆：%d 个标签 / %d 条 chunk，下次遗忘约 %.1f 小时后",
                stats["tags"],
                stats["chunks"],
                forget_due_seconds(self.memory) / 3600.0,
            )
            self._maybe_forget()
        self._schedule(self.cfg.agent.first_delay_sec, message="第一次看屏幕")

    def stop(self) -> None:
        self._stopped = True
        self.timer.stop()
        self.pool.waitForDone(5000)

    # ---------- 对外操作 ----------

    def pause(self) -> None:
        if self._paused:
            return
        self._paused = True
        self.timer.stop()
        self._message = "已暂停"
        log.info("已暂停")
        self._emit_status()

    def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        self._schedule(self._rng.uniform(10, 30), message="继续陪主人")
        log.info("已继续")

    def toggle_pause(self) -> bool:
        if self._paused:
            self.resume()
        else:
            self.pause()
        return self._paused

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def busy(self) -> bool:
        return self._busy

    def set_interval_minutes(self, minutes: float) -> None:
        """菜单里换识屏间隔：立即改配置，空闲时 1 秒后按新间隔重排。"""
        try:
            value = float(minutes)
        except (TypeError, ValueError):
            return
        value = max(0.5, min(720.0, value))
        self.cfg.agent.min_interval_min = value
        self.cfg.agent.max_interval_min = value
        self._persist_interval(value)
        log.info("识屏间隔改为 %.1f 分钟", value)
        if not self._stopped and not self._paused and not self._busy:
            self._schedule(1.0, message="新的识屏间隔已生效")
        self._emit_status()

    def _persist_interval(self, value: float) -> None:
        """把菜单里选的间隔写回 config.toml，重启后仍然生效。"""
        path = self._config_path
        if path is None or not path.exists():
            return
        try:
            text = path.read_text(encoding="utf-8")
            if not re.search(r"(?m)^\s*min_interval_min\s*=", text):
                return
            if not re.search(r"(?m)^\s*max_interval_min\s*=", text):
                return
            new = re.sub(
                r"(?m)^(\s*min_interval_min\s*=\s*).*$",
                lambda m: m.group(1) + f"{value:g}",
                text,
            )
            new = re.sub(
                r"(?m)^(\s*max_interval_min\s*=\s*).*$",
                lambda m: m.group(1) + f"{value:g}",
                new,
            )
            if new != text:
                path.write_text(new, encoding="utf-8")
                log.info("已把识屏间隔写回 %s", path)
        except Exception as e:
            log.warning("写回识屏间隔失败：%s", e)

    def trigger_now(self) -> bool:
        if self._paused or self._busy:
            return False
        self.timer.stop()
        self._run_cycle(manual=True)
        return True

    def feedback(self, kind: str) -> str:
        """菜单里的“这句喜欢/这句一般”。"""
        if kind not in {"like", "dislike"}:
            return ""
        if not self._history:
            return "还没有可评价的句子"
        row = self._history[-1]
        if row.get("feedback"):
            return "这一句已经记过啦"
        sid = str(row.get("strategy") or "unknown")
        row["feedback"] = "like" if kind == "like" else "dislike"
        stat = self._feedback.setdefault(sid, {"like": 0, "dislike": 0})
        stat["like" if kind == "like" else "dislike"] += 1
        self._save_state()
        label = STRATEGY_BY_ID.get(sid).label if sid in STRATEGY_BY_ID else sid
        return f"收到：{label} {'喜欢' if kind == 'like' else '一般'}"

    def stats_text(self) -> str:
        return stats_summary(self._history, 7)

    def status(self) -> dict:
        return {
            "paused": self._paused,
            "busy": self._busy,
            "message": self._message,
            "last_line": self._last_line,
            "last_error": self._last_error,
            "last_title": self._last_title,
            "last_scene": self._last_scene,
            "last_strategy": self._last_strategy,
            "last_at": self._last_at,
            "next_at": self._next_at,
            "count": self._count,
            "model": self.cfg.llm.model,
            "min_interval_min": self.cfg.agent.min_interval_min,
            "max_interval_min": self.cfg.agent.max_interval_min,
            "next_in_sec": max(0.0, self._next_at - time.time()) if self._next_at else None,
            "memory": self.memory.stats() if self.cfg.memory.enabled else {},
        }

    # ---------- 状态 ----------

    def _load_state(self) -> None:
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            self._last_line = str(data.get("last_line") or "")
            self._last_error = str(data.get("last_error") or "")
            self._last_title = str(data.get("last_title") or "")
            self._last_scene = str(data.get("last_scene") or "")
            self._last_strategy = str(data.get("last_strategy") or "")
            self._last_at = float(data.get("last_at") or 0)
            self._count = int(data.get("count") or 0)
            self._last_hash = str(data.get("last_hash") or "")
            self._last_exe = str(data.get("last_exe") or "")
            self._last_switch_at = float(data.get("last_switch_at") or 0)
            self._last_change_at = float(data.get("last_change_at") or 0)
            self._since_meme = int(data.get("since_meme") or 0)
            self._recent_meme_keys = [str(x) for x in (data.get("recent_meme_keys") or [])][-10:]
            self._pending_question = str(data.get("pending_question") or "")
            self._pending_at = float(data.get("pending_at") or 0)
            self._pending_turns = int(data.get("pending_turns") or 0)
            self._recent_materials = [str(x) for x in (data.get("recent_materials") or [])][-8:]
            self._feedback = data.get("feedback") if isinstance(data.get("feedback"), dict) else {}
            history = data.get("history") or []
            self._history = [dict(x) for x in history if isinstance(x, dict)][-400:]
            recent = data.get("recent_lines") or []
            keep = max(1, self.cfg.agent.remember_last_lines)
            self._recent_lines = [str(x) for x in recent][-keep:]
            meta = data.get("recent_meta") or []
            self._recent_meta = [dict(x) for x in meta if isinstance(x, dict)][-keep:]
            if not self._recent_meta and self._recent_lines:
                self._recent_meta = [
                    {"line": line, "strategy": "", "at": 0.0, "length": len(line)}
                    for line in self._recent_lines
                ]
        except Exception:
            pass

    def _save_state(self) -> None:
        try:
            ensure_runtime_dir()
            keep = max(1, self.cfg.agent.remember_last_lines)
            payload = {
                "last_line": self._last_line,
                "last_error": self._last_error,
                "last_title": self._last_title,
                "last_scene": self._last_scene,
                "last_strategy": self._last_strategy,
                "last_at": self._last_at,
                "next_at": self._next_at,
                "count": self._count,
                "last_hash": self._last_hash,
                "last_exe": self._last_exe,
                "last_switch_at": self._last_switch_at,
                "last_change_at": self._last_change_at,
                "since_meme": self._since_meme,
                "recent_meme_keys": self._recent_meme_keys[-10:],
                "recent_lines": self._recent_lines[-keep:],
                "recent_meta": self._recent_meta[-keep:],
                "feedback": self._feedback,
                "pending_question": self._pending_question,
                "pending_at": self._pending_at,
                "pending_turns": self._pending_turns,
                "recent_materials": self._recent_materials[-8:],
                "history": self._history[-400:],
                "updated_at": time.time(),
            }
            STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("保存状态失败：%s", e)

    def _emit_status(self) -> None:
        self.status_changed.emit(self.status())

    # ---------- 记忆 ----------

    def _remember_turn(self, scene: str, line: str, activity_digest: str, notes) -> None:
        """一轮结束后：写滑动窗口 + 收下模型主动记的事 + 存盘。"""
        if not self.cfg.memory.enabled:
            return
        try:
            self.memory.add_turn(scene, line, activity_digest)
            if notes:
                landed = self.memory.remember_many(notes)
                if landed:
                    log.info("记住：%s", "、".join(landed))
            self.memory.save()
        except Exception as e:
            log.warning("写记忆失败：%s", e)

    def _maybe_forget(self) -> None:
        """每 24 小时清一次过时的标签和 chunk。"""
        if not self.cfg.memory.enabled:
            return
        if not self.memory.need_forget():
            return
        try:
            stats = self.memory.forget()
            self.memory.save(force=True)
            log.info(
                "遗忘完成：删 %d 个 chunk、%d 个标签，合并 %d 个标签，剩 %d 个标签",
                stats["dropped_chunks"],
                stats["dropped_tags"],
                stats["merged_tags"],
                stats["tags_left"],
            )
        except Exception as e:
            log.warning("遗忘失败：%s", e)

    # ---------- 调度 ----------

    def _schedule(self, delay_sec: float, message: str = "") -> None:
        if self._stopped or self._paused:
            return
        delay_sec = max(1.0, float(delay_sec))
        self._next_at = time.time() + delay_sec
        self.timer.start(int(delay_sec * 1000))
        if message:
            self._message = message
        self._emit_status()

    def _schedule_next(self) -> None:
        delay = self.cfg.next_interval_sec(self._rng)
        self._message = "刚说完一句，等下一次"
        log.info("下一次识屏：%.0f 秒后", delay)
        self._schedule(delay, message=self._message)

    def _on_due(self) -> None:
        self._run_cycle(manual=False)

    def _privacy_skip(self) -> str | None:
        if self.cfg.privacy.skip_when_locked and is_screen_locked():
            return "屏幕已锁定"
        win = foreground_window()
        title = (win.title if win else "").strip()
        low = title.lower()
        for kw in self.cfg.privacy.skip_window_keywords:
            if kw and kw.lower() in low:
                return f"前台窗口命中隐私关键词「{kw}」"
        return None

    def _run_cycle(self, manual: bool = False) -> None:
        if self._stopped or self._paused:
            return
        if self._busy:
            self._message = "上一次还没看完，稍等"
            self._emit_status()
            if not manual:
                self._schedule(60, message=self._message)
            return

        skip = self._privacy_skip()
        if skip:
            self._message = f"跳过：{skip}"
            log.info("跳过本次识屏：%s", skip)
            self._emit_status()
            self._schedule(self.cfg.agent.retry_on_error_sec, message=self._message)
            return

        if self.cfg.agent.pause_when_idle:
            idle_min = idle_seconds() / 60.0
            if idle_min >= self.cfg.agent.idle_threshold_min:
                self._message = f"主人离开 {idle_min:.0f} 分钟了，先不打扰"
                log.info(self._message)
                self._emit_status()
                self._schedule(300, message=self._message)
                return

        self._busy = True
        self._message = "正在看屏幕…"
        self._emit_status()
        log.info("开始识屏（%s）", "手动" if manual else "定时")
        session = Session(
            recent_lines=list(self._recent_lines),
            recent_meta=list(self._recent_meta),
            feedback=dict(self._feedback),
            pending_question=self._pending_question,
            pending_at=self._pending_at,
            pending_turns=self._pending_turns,
            recent_materials=list(self._recent_materials),
            last_hash=self._last_hash,
            last_exe=self._last_exe,
            last_switch_at=self._last_switch_at,
            last_change_at=self._last_change_at,
            since_meme=self._since_meme,
            recent_meme_keys=list(self._recent_meme_keys),
        )
        worker = CycleWorker(
            self.cfg, self.capture, self.llm, session, self.memes, self.memory
        )
        worker.signals.done.connect(self._on_worker_done)
        self._worker = worker
        self.pool.start(worker)

    def _on_worker_done(self, result: CycleResult) -> None:
        self._busy = False
        self._worker = None
        if result.error:
            self._last_error = result.error
            self._message = "这次没看成"
            log.warning("识屏失败：%s", result.error)
            self._emit_status()
            self._schedule(self.cfg.agent.retry_on_error_sec, message="稍后重试")
            self._save_state()
            return

        if result.skipped:
            self._message = f"没说话：{result.skipped}"
            log.info(self._message)
            self._emit_status()
            self._schedule_next()
            self._save_state()
            return

        if result.meme_path:
            now = time.time()
            line = result.meme_line or fallback_caption(result.meme_tag)
            self._last_error = ""
            self._last_line = line
            self._last_title = result.title
            self._last_scene = result.scene_short
            self._last_strategy = "meme"
            self._last_at = now
            self._last_hash = result.shot_hash
            self._count += 1
            self._since_meme = 0
            keep = max(1, self.cfg.agent.remember_last_lines)
            self._recent_lines.append(line)
            self._recent_lines = self._recent_lines[-keep:]
            self._recent_meta.append(
                {
                    "line": line,
                    "strategy": "meme",
                    "pattern": "表情",
                    "at": now,
                    "length": len(line),
                    "novelty": 1.0,
                    "material": result.meme_key,
                }
            )
            self._recent_meta = self._recent_meta[-keep:]
            if result.meme_key and result.meme_key not in self._recent_meme_keys:
                self._recent_meme_keys.append(result.meme_key)
                self._recent_meme_keys = self._recent_meme_keys[-10:]
            self._history.append(
                {
                    "at": now,
                    "strategy": "meme",
                    "line": line,
                    "length": len(line),
                    "novelty": 1.0,
                    "feedback": "",
                    "tag": result.meme_tag,
                }
            )
            self._history = self._history[-400:]
            self._pending_question = ""
            self._pending_at = 0.0
            self._pending_turns = 0
            if result.exe:
                if result.exe != self._last_exe:
                    self._last_switch_at = now
                self._last_exe = result.exe
            if result.changed or not self._last_change_at:
                self._last_change_at = now
            self._message = "刚发了一张表情包"
            log.info(
                "发表情：%s（图：%s，tag=%s，tokens=%s）",
                line,
                result.meme_caption,
                result.meme_tag or "?",
                result.usage.get("total_tokens", "?"),
            )
            self.image_ready.emit(result.meme_path, line)
            self._remember_turn(result.scene_text or result.title, line, result.activity_digest, [])
            self._maybe_forget()
            self._emit_status()
            self._schedule_next()
            self._save_state()
            return

        if not result.ok or not result.line:
            self._last_error = "模型没有返回可用的话"
            self._message = "这次没看成"
            log.warning(self._last_error)
            self._emit_status()
            self._schedule(self.cfg.agent.retry_on_error_sec, message="稍后重试")
            self._save_state()
            return

        now = time.time()
        self._last_error = ""
        self._last_line = result.line
        self._last_title = result.title
        self._last_scene = result.scene_short
        self._last_strategy = result.strategy
        self._last_at = now
        self._last_hash = result.shot_hash
        self._count += 1
        self._since_meme += 1

        keep = max(1, self.cfg.agent.remember_last_lines)
        self._recent_lines.append(result.line)
        self._recent_lines = self._recent_lines[-keep:]
        self._recent_meta.append(
            {
                "line": result.line,
                "strategy": result.strategy,
                "pattern": result.pattern,
                "at": now,
                "length": len(result.line),
                "novelty": result.novelty,
                "material": result.material_id,
            }
        )
        self._recent_meta = self._recent_meta[-keep:]
        self._history.append(
            {
                "at": now,
                "strategy": result.strategy,
                "line": result.line,
                "length": len(result.line),
                "novelty": result.novelty,
                "feedback": "",
            }
        )
        self._history = self._history[-400:]

        if result.material_id and result.material_id not in self._recent_materials:
            self._recent_materials.append(result.material_id)
            self._recent_materials = self._recent_materials[-8:]

        if result.exe:
            if result.exe != self._last_exe:
                self._last_switch_at = now
            self._last_exe = result.exe
        if result.changed or not self._last_change_at:
            self._last_change_at = now

        if result.line.rstrip("」』”’ ").endswith(("？", "?")):
            self._pending_question = result.line
            self._pending_at = now
            self._pending_turns = 0
        elif result.strategy == "callback":
            self._pending_question = ""
            self._pending_at = 0.0
            self._pending_turns = 0
        elif self._pending_question:
            self._pending_turns += 1
            if self._pending_turns >= 2:
                self._pending_question = ""
                self._pending_at = 0.0
                self._pending_turns = 0

        self._message = "刚说完一句"
        log.info("此刻状态：%s", result.scene_short or "桌面")
        log.info(
            "说：%s（%d 字，策略=%s，新颖度=%.2f，%d 次尝试，tokens=%s）",
            result.line,
            len(result.line),
            result.strategy or "?",
            result.novelty,
            result.attempts,
            result.usage.get("total_tokens", "?"),
        )
        self._remember_turn(
            result.scene_text or result.title, result.line, result.activity_digest, result.remember
        )
        self._maybe_forget()
        self.line_ready.emit(result.line)
        self._emit_status()
        self._schedule_next()
        self._save_state()
