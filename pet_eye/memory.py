# -*- coding: utf-8 -*-
"""桌宠的记忆。

三层，各管各的：

1. **滑动窗口**（`window`）：最近 3 轮「场景 + 说了什么」，逐字进提示词，保证接话不断片。
2. **标签 + chunk**（`tags`）：长期记忆。一个标签（如“写代码”“深夜赶工”）下面挂若干
   chunk（一句话的事实），标签是向量化的，新标签和旧标签相似就用向量合并成同一个。
3. **遗忘**（`forget`）：每 24 小时跑一次，按天衰减权重，低于阈值的 chunk / 标签直接删掉。
   所以不需要“先全库扫一遍再去重”的双层架构——库本身就一直是小的。

向量库很轻：字符 bigram 哈希到 256 维 + L2 归一化，余弦相似度用 numpy 算。
不依赖任何外部向量数据库，进程内就是全部。
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Iterable

import numpy as np
from pydantic import BaseModel, Field

from .config import MemoryConfig

log = logging.getLogger("pet_eye")

VECTOR_DIM = 256


def embed(text: str) -> np.ndarray:
    """把一句话打成 256 维稀疏向量（字符 bigram + unigram，哈希分桶）。"""
    vec = np.zeros(VECTOR_DIM, dtype=np.float32)
    clean = "".join(ch for ch in (text or "") if not ch.isspace())
    if not clean:
        return vec
    grams: list[str] = list(clean)
    grams += [clean[i : i + 2] for i in range(len(clean) - 1)]
    for gram in grams:
        # 稳定哈希：不用 Python 的 hash()，它每个进程都变
        h = 2166136261
        for ch in gram:
            h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
        vec[h % VECTOR_DIM] += 1.0
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None or a.size == 0 or b.size == 0:
        return 0.0
    return float(np.dot(a, b))


# ---------------------------------------------------------------- 数据模型


class MemoryChunk(BaseModel):
    """一条长期记忆：一句事实。"""

    text: str = Field(min_length=2, max_length=120)
    created_at: float = 0.0
    last_used_at: float = 0.0
    weight: float = 1.0
    hits: int = 0


class MemoryTag(BaseModel):
    """一类记忆：名字 + 若干 chunk。"""

    name: str = Field(min_length=1, max_length=16)
    created_at: float = 0.0
    last_used_at: float = 0.0
    weight: float = 1.0
    hits: int = 0
    chunks: list[MemoryChunk] = Field(default_factory=list)


class TurnMemory(BaseModel):
    """滑动窗口里的一轮。"""

    scene: str = ""
    activity: str = ""
    line: str = ""
    at: float = 0.0


class MemorySnapshot(BaseModel):
    """落盘格式。"""

    version: int = 1
    tags: list[MemoryTag] = Field(default_factory=list)
    window: list[TurnMemory] = Field(default_factory=list)
    last_forget_at: float = 0.0
    total_remembered: int = 0
    total_forgotten: int = 0


class MemoryStore:
    """标签 + chunk 的记忆库，带滑动窗口和遗忘。

    线程安全：后台识屏线程会写，UI 会读状态。
    """

    def __init__(self, cfg: MemoryConfig, path: Path | None = None):
        self.cfg = cfg
        self.path = path
        self._lock = threading.RLock()
        self._snapshot = MemorySnapshot()
        self._vectors: dict[str, np.ndarray] = {}
        self._dirty = False

    # ---------- 持久化 ----------

    def load(self) -> "MemoryStore":
        if self.path is None or not self.path.exists():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            snap = MemorySnapshot.model_validate(raw)
            with self._lock:
                self._snapshot = snap
                self._vectors = {tag.name: embed(tag.name) for tag in snap.tags}
        except Exception as e:
            log.warning("记忆库读取失败（当作空的）：%s", e)
        return self

    def save(self, force: bool = False) -> None:
        if self.path is None:
            return
        with self._lock:
            if not self._dirty and not force:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(
                    self._snapshot.model_dump_json(indent=1), encoding="utf-8"
                )
                tmp.replace(self.path)
                self._dirty = False
            except Exception as e:
                log.warning("记忆库写入失败：%s", e)

    # ---------- 滑动窗口 ----------

    def add_turn(self, scene: str, line: str, activity: str = "", now: float | None = None) -> None:
        """把这一轮记进滑动窗口（只保留最近 window_size 轮）。"""
        if not self.cfg.enabled:
            return
        now = now or time.time()
        with self._lock:
            self._snapshot.window.append(
                TurnMemory(scene=_short(scene, 80), activity=_short(activity, 80), line=_short(line, 60), at=now)
            )
            keep = max(1, int(self.cfg.window_size))
            self._snapshot.window = self._snapshot.window[-keep:]
            self._dirty = True

    def window(self) -> list[TurnMemory]:
        with self._lock:
            return list(self._snapshot.window)

    def window_block(self) -> str:
        """给提示词的「最近几轮」。"""
        rows = self.window()
        lines = [row for row in rows if row.line]
        if not lines:
            return ""
        out = ["【最近几轮（接话用，别复读）】"]
        for row in lines:
            scene = f"（{row.scene}）" if row.scene else ""
            out.append(f"- {scene}{row.line}")
        return "\n".join(out) + "\n"

    # ---------- 长期记忆 ----------

    def remember(self, tag_name: str, text: str, now: float | None = None) -> str:
        """写入一条长期记忆，返回最终落在哪个标签下（可能被合并到旧标签）。"""
        if not self.cfg.enabled:
            return ""
        now = now or time.time()
        name = _short(str(tag_name or "").strip(), 16)
        body = _short(str(text or "").strip(), int(self.cfg.chunk_max_chars))
        if not name or len(body) < 2:
            return ""

        with self._lock:
            target = self._find_similar_tag(name)
            if target is None:
                target = MemoryTag(name=name, created_at=now, last_used_at=now)
                self._snapshot.tags.append(target)
                self._vectors[name] = embed(name)
            else:
                target.last_used_at = now
                target.weight = min(3.0, target.weight + 0.2)

            # 同一个意思的 chunk 不重复存，只刷新权重
            chunk_vec = embed(body)
            for chunk in target.chunks:
                if cosine(chunk_vec, embed(chunk.text)) >= 0.95:
                    chunk.weight = min(3.0, chunk.weight + 0.3)
                    chunk.last_used_at = now
                    chunk.hits += 1
                    self._dirty = True
                    return target.name

            target.chunks.append(
                MemoryChunk(text=body, created_at=now, last_used_at=now, weight=1.0, hits=1)
            )
            target.chunks = target.chunks[-max(1, int(self.cfg.max_chunks_per_tag)) :]
            self._snapshot.total_remembered += 1
            self._enforce_tag_limit()
            self._dirty = True
            return target.name

    def remember_many(self, notes: Iterable[tuple[str, str]], now: float | None = None) -> list[str]:
        """一轮最多写 remember_per_turn 条。"""
        limit = max(0, int(self.cfg.remember_per_turn))
        if limit == 0:
            return []
        out: list[str] = []
        for tag, text in list(notes)[:limit]:
            landed = self.remember(tag, text, now=now)
            if landed:
                out.append(landed)
        return out

    def recall(self, query: str, k: int | None = None) -> list[tuple[str, str]]:
        """按向量相似度召回几条记忆：(标签, chunk)。"""
        if not self.cfg.enabled:
            return []
        limit = max(0, int(k if k is not None else self.cfg.recall_k))
        if limit == 0:
            return []
        with self._lock:
            if not self._snapshot.tags:
                return []
            qvec = embed(query)
            scored: list[tuple[float, MemoryTag]] = []
            for tag in self._snapshot.tags:
                score = cosine(qvec, self._vec(tag.name)) * (0.4 + 0.6 * min(1.0, tag.weight))
                scored.append((score, tag))
            scored.sort(key=lambda item: item[0], reverse=True)

            out: list[tuple[str, str]] = []
            for score, tag in scored:
                if len(out) >= limit:
                    break
                if score <= 0.01 and out:
                    break
                for chunk in sorted(tag.chunks, key=lambda c: c.weight, reverse=True)[:2]:
                    out.append((tag.name, chunk.text))
                    tag.hits += 1
                    tag.last_used_at = time.time()
                    chunk.hits += 1
                    chunk.last_used_at = time.time()
                    if len(out) >= limit:
                        break
            self._dirty = True
            return out

    def memory_block(self, query: str) -> str:
        """给提示词的长期记忆块。"""
        rows = self.recall(query)
        if not rows:
            return ""
        lines = ["【你记得的事（可以参考，不要整句复述）】"]
        for tag, text in rows:
            lines.append(f"- [{tag}] {text}")
        return "\n".join(lines) + "\n"

    # ---------- 标签合并 ----------

    def _vec(self, name: str) -> np.ndarray:
        """取标签向量（缓存，缺失就现算）。注意不能用 `or`——numpy 数组的真值会炸。"""
        vec = self._vectors.get(name)
        if vec is None:
            vec = embed(name)
            self._vectors[name] = vec
        return vec

    def _find_similar_tag(self, name: str) -> MemoryTag | None:
        if not self._snapshot.tags:
            return None
        vec = embed(name)
        best: tuple[float, MemoryTag | None] = (0.0, None)
        for tag in self._snapshot.tags:
            if tag.name == name:
                return tag
            score = cosine(vec, self._vec(tag.name))
            if score > best[0]:
                best = (score, tag)
        if best[1] is not None and best[0] >= float(self.cfg.merge_similarity):
            return best[1]
        return None

    def merge_tags(self, threshold: float | None = None) -> int:
        """把所有标签两两比一遍，相似的合并（只在遗忘时跑，量很小）。"""
        limit = float(threshold if threshold is not None else self.cfg.merge_similarity)
        merged = 0
        with self._lock:
            tags = sorted(self._snapshot.tags, key=lambda t: t.weight, reverse=True)
            survivors: list[MemoryTag] = []
            for tag in tags:
                hit = None
                for base in survivors:
                    if cosine(self._vec(tag.name), self._vec(base.name)) >= limit:
                        hit = base
                        break
                if hit is None:
                    survivors.append(tag)
                    continue
                # 合并：chunk 搬过去，权重相加
                for chunk in tag.chunks:
                    if not any(cosine(embed(chunk.text), embed(c.text)) >= 0.95 for c in hit.chunks):
                        hit.chunks.append(chunk)
                hit.weight = min(3.0, hit.weight + tag.weight * 0.5)
                hit.hits += tag.hits
                hit.last_used_at = max(hit.last_used_at, tag.last_used_at)
                hit.chunks = hit.chunks[-max(1, int(self.cfg.max_chunks_per_tag)) :]
                self._vectors.pop(tag.name, None)
                merged += 1
            if merged:
                self._snapshot.tags = survivors
                self._dirty = True
        return merged

    # ---------- 遗忘 ----------

    def forget(self, now: float | None = None) -> dict:
        """按天衰减权重，删掉过时的 chunk / 标签。每 24 小时跑一次就够。"""
        now = now or time.time()
        decay = float(self.cfg.decay_per_day)
        min_weight = float(self.cfg.min_weight)
        max_age_days = float(self.cfg.forget_after_days)
        dropped_chunks = 0
        dropped_tags = 0
        with self._lock:
            keep_tags: list[MemoryTag] = []
            for tag in self._snapshot.tags:
                age_days = max(0.0, (now - (tag.last_used_at or tag.created_at or now)) / 86400.0)
                tag.weight = float(tag.weight) * (decay ** age_days)

                keep_chunks: list[MemoryChunk] = []
                for chunk in tag.chunks:
                    chunk_age = max(0.0, (now - (chunk.last_used_at or chunk.created_at or now)) / 86400.0)
                    chunk.weight = float(chunk.weight) * (decay ** chunk_age)
                    if chunk.weight < min_weight or chunk_age > max_age_days:
                        dropped_chunks += 1
                        continue
                    keep_chunks.append(chunk)
                tag.chunks = keep_chunks

                if not tag.chunks or tag.weight < min_weight or age_days > max_age_days:
                    dropped_tags += 1
                    self._vectors.pop(tag.name, None)
                    continue
                keep_tags.append(tag)
            self._snapshot.tags = keep_tags
            self._enforce_tag_limit()
            self._snapshot.last_forget_at = now
            self._snapshot.total_forgotten += dropped_chunks + dropped_tags
            self._dirty = True
        merged = self.merge_tags()
        return {
            "dropped_chunks": dropped_chunks,
            "dropped_tags": dropped_tags,
            "merged_tags": merged,
            "tags_left": len(self._snapshot.tags),
        }

    def need_forget(self, now: float | None = None) -> bool:
        now = now or time.time()
        last = float(self._snapshot.last_forget_at or 0.0)
        return (now - last) >= float(self.cfg.forget_interval_hours) * 3600.0

    def _enforce_tag_limit(self) -> None:
        limit = max(1, int(self.cfg.max_tags))
        if len(self._snapshot.tags) <= limit:
            return
        self._snapshot.tags.sort(key=lambda t: t.weight, reverse=True)
        for tag in self._snapshot.tags[limit:]:
            self._vectors.pop(tag.name, None)
        self._snapshot.tags = self._snapshot.tags[:limit]

    # ---------- 状态 ----------

    def stats(self) -> dict:
        with self._lock:
            return {
                "tags": len(self._snapshot.tags),
                "chunks": sum(len(t.chunks) for t in self._snapshot.tags),
                "window": len(self._snapshot.window),
                "remembered": self._snapshot.total_remembered,
                "forgotten": self._snapshot.total_forgotten,
                "last_forget_at": self._snapshot.last_forget_at,
            }

    def tag_names(self) -> list[str]:
        with self._lock:
            return [t.name for t in self._snapshot.tags]


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def forget_due_seconds(store: MemoryStore, now: float | None = None) -> float:
    """距离下次遗忘还有多少秒（UI 用）。"""
    now = now or time.time()
    last = float(store.stats().get("last_forget_at") or 0.0)
    return max(0.0, store.cfg.forget_interval_hours * 3600.0 - (now - last))
