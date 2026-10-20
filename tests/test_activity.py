# -*- coding: utf-8 -*-
"""用户动向：清洗、过滤、提示词块，以及 Recently 目录解析。"""

import os
import time

from pet_eye import activity as act
from pet_eye.config import ActivityConfig


def test_clean_trims_and_limits():
    assert act._clean("  a\n b\t c ") == "a b c"
    assert len(act._clean("x" * 200)) <= 60


def test_sensitive_filters_private_paths():
    words = ["password", "密码", ".ssh"]
    assert act._sensitive("C:/Users/x/passwords.txt", words)
    assert act._sensitive("我的密码.txt", words)
    assert not act._sensitive("report.docx", words)


def test_dedupe_keeps_order_and_limit():
    out = act._dedupe(["a", "A", "b", "a", "c"], 2)
    assert out == ["a", "b"]


def test_activity_empty_and_block():
    empty = act.Activity()
    assert empty.is_empty()
    assert empty.to_prompt_block() == ""

    filled = act.Activity(
        processes=["Code：pet-eye"],
        recent_files=["记忆模块.py"],
        run_commands=["notepad.exe"],
    )
    block = filled.to_prompt_block()
    assert "Code：pet-eye" in block
    assert "记忆模块.py" in block
    assert "notepad.exe" in block
    assert "报菜名" in block
    assert "Code" in filled.digest()


def test_recent_files_reads_lnk_names(tmp_path, monkeypatch):
    now = time.time()
    for name, age in (("旧的.docx.lnk", 10_000), ("新的.py.lnk", 30)):
        p = tmp_path / name
        p.write_text("x", encoding="utf-8")
        ts = now - age
        os.utime(p, (ts, ts))
    monkeypatch.setattr(act, "_RECENT_DIR", tmp_path)

    names = act.read_recent_files(limit=5, recent_minutes=60)
    assert names == ["新的.py"]
    assert act.read_recent_files(limit=5, recent_minutes=100000) == ["新的.py", "旧的.docx"]


def test_collect_disabled_returns_empty():
    result = act.collect(ActivityConfig(enabled=False))
    assert result.is_empty()


def test_collect_filters_sensitive_files(tmp_path, monkeypatch):
    monkeypatch.setattr(act, "_RECENT_DIR", tmp_path)
    (tmp_path / "我的密码.txt.lnk").write_text("x", encoding="utf-8")
    (tmp_path / "报告.docx.lnk").write_text("x", encoding="utf-8")
    monkeypatch.setattr(act, "read_run_mru", lambda limit=6: [])
    monkeypatch.setattr(act, "read_typed_paths", lambda limit=6: [])
    monkeypatch.setattr(act, "read_recent_apps", lambda limit=8: [])
    monkeypatch.setattr(act, "read_processes", lambda cfg: [])

    cfg = ActivityConfig(powershell=False, registry=True, recent_minutes=600, max_items=5)
    got = act.collect(cfg, privacy_keywords=["密码"])
    assert got.recent_files == ["报告.docx"]
