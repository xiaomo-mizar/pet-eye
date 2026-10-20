# -*- coding: utf-8 -*-
"""模型输出的 Pydantic 约束。"""

import pytest
from pydantic import ValidationError

from pet_eye.schemas import MemoryNote, TurnResponse, extract_remember, validate_turn


def test_extract_remember_from_json():
    raw = (
        '{"candidates":[{"strategy":"care","line":"……"}],'
        '"remember":[{"tag":"写代码","text":"主人在改记忆模块"}]}'
    )
    assert extract_remember(raw) == [("写代码", "主人在改记忆模块")]


def test_extract_remember_skips_bad_items():
    raw = (
        '{"candidates":[],"remember":['
        '{"tag":"太长的标签带标点，不行","text":"甲乙丙"},'
        '{"tag":"写代码","text":"x"},'
        '{"tag":"听歌","text":"主人在循环一首老歌"}]}'
    )
    assert extract_remember(raw) == [("听歌", "主人在循环一首老歌")]


def test_extract_remember_missing_field_is_empty():
    assert extract_remember('{"candidates":[]}') == []
    assert extract_remember("not json") == []


def test_extract_remember_respects_limit():
    raw = (
        '{"remember":[{"tag":"甲","text":"一二三"},{"tag":"乙","text":"四五六"},'
        '{"tag":"丙","text":"七八九"}]}'
    )
    assert len(extract_remember(raw, limit=2)) == 2


def test_note_rejects_sentence_tag():
    with pytest.raises(ValidationError):
        MemoryNote(tag="我在写代码，很忙", text="主人在改记忆模块")


def test_note_strips_punctuation():
    note = MemoryNote(tag="写代码。", text="主人在改记忆模块。")
    assert note.tag == "写代码"
    assert note.text == "主人在改记忆模块"


def test_validate_turn_shape():
    turn = validate_turn(
        {"candidates": [{"strategy": "care", "line": "一句话"}], "remember": []}
    )
    assert isinstance(turn, TurnResponse)
    assert turn.candidates[0].strategy == "care"
    assert turn.notes() == []
