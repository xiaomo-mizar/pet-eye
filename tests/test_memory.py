# -*- coding: utf-8 -*-
"""记忆：滑动窗口、标签合并、召回、遗忘。"""

import time

import numpy as np
import pytest

from pet_eye.config import MemoryConfig
from pet_eye.memory import MemoryStore, cosine, embed


def make_store(tmp_path, **overrides) -> MemoryStore:
    cfg = MemoryConfig(**overrides)
    return MemoryStore(cfg, tmp_path / "memory.json")


def test_embed_is_normalized_and_stable():
    a = embed("深夜写代码")
    b = embed("深夜写代码")
    assert a.shape == (256,)
    assert pytest.approx(float(np.linalg.norm(a)), rel=1e-5) == 1.0
    assert cosine(a, b) == pytest.approx(1.0, abs=1e-5)
    assert embed("") .sum() == 0


def test_embed_similar_text_beats_unrelated():
    base = embed("深夜写代码")
    near = embed("深夜写代码中")
    far = embed("今天吃了大白饭")
    assert cosine(base, near) > cosine(base, far)


def test_window_keeps_only_three_turns(tmp_path):
    store = make_store(tmp_path, window_size=3)
    for i in range(6):
        store.add_turn(f"场景{i}", f"第{i}句话", "应用：Code")
    window = store.window()
    assert len(window) == 3
    assert [row.line for row in window] == ["第3句话", "第4句话", "第5句话"]
    block = store.window_block()
    assert "第5句话" in block and "第0句话" not in block


def test_remember_merges_similar_tags(tmp_path):
    store = make_store(tmp_path, merge_similarity=0.8)
    a = store.remember("写代码", "主人在改记忆模块", now=1000.0)
    b = store.remember("写代码中", "改到一半去泡了杯茶", now=1001.0)
    assert a == "写代码"
    assert b == "写代码", "相似标签应该合并到同一个"
    assert len(store.tag_names()) == 1
    assert store.stats()["chunks"] == 2


def test_remember_keeps_different_tags_apart(tmp_path):
    store = make_store(tmp_path, merge_similarity=0.9)
    store.remember("写代码", "主人在改记忆模块", now=1000.0)
    store.remember("午饭", "主人吃了大白饭", now=1001.0)
    assert len(store.tag_names()) == 2


def test_duplicate_chunk_is_not_stored_twice(tmp_path):
    store = make_store(tmp_path)
    store.remember("写代码", "主人在改记忆模块", now=1000.0)
    store.remember("写代码", "主人在改记忆模块", now=1001.0)
    assert store.stats()["chunks"] == 1


def test_recall_prefers_relevant_tag(tmp_path):
    store = make_store(tmp_path)
    store.remember("写代码", "主人在改记忆模块", now=1000.0)
    store.remember("听歌", "主人在循环一首老歌", now=1001.0)
    rows = store.recall("写代码 深夜", k=1)
    assert rows and rows[0][0] == "写代码"


def test_forget_drops_stale_chunks_and_tags(tmp_path):
    store = make_store(tmp_path, decay_per_day=0.5, min_weight=0.2, forget_after_days=3)
    now = 10_000_000.0
    store.remember("写代码", "主人在改记忆模块", now=now - 10 * 86400)
    store.remember("听歌", "主人在循环一首老歌", now=now)
    stats = store.forget(now=now)
    assert stats["dropped_tags"] >= 1
    names = store.tag_names()
    assert "写代码" not in names
    assert "听歌" in names


def test_forget_interval_gate(tmp_path):
    store = make_store(tmp_path, forget_interval_hours=24)
    base = 100 * 86400.0
    assert store.need_forget(now=base) is True
    store.forget(now=base)
    assert store.need_forget(now=base + 3600) is False
    assert store.need_forget(now=base + 25 * 3600) is True


def test_max_chunks_per_tag(tmp_path):
    store = make_store(tmp_path, max_chunks_per_tag=2, merge_similarity=0.99)
    store.remember("写代码", "第一条记忆", now=1000.0)
    store.remember("写代码", "第二条记忆", now=1001.0)
    store.remember("写代码", "第三条记忆", now=1002.0)
    assert store.stats()["chunks"] == 2


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "memory.json"
    store = MemoryStore(MemoryConfig(), path)
    store.remember("写代码", "主人在改记忆模块", now=time.time())
    store.add_turn("工作", "还在改呢", "应用：Code")
    store.save(force=True)

    again = MemoryStore(MemoryConfig(), path).load()
    assert again.tag_names() == ["写代码"]
    assert again.window()[0].line == "还在改呢"


def test_remember_many_respects_limit(tmp_path):
    store = make_store(tmp_path, remember_per_turn=2)
    landed = store.remember_many(
        [("写代码", "甲甲"), ("听歌", "乙乙"), ("吃饭", "丙丙")]
    )
    assert len(landed) == 2
