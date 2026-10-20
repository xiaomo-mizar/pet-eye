# -*- coding: utf-8 -*-
"""模型输出清洗 / 20~50 字约束单测。"""

import pytest
from pydantic import ValidationError

from pet_eye.llm import LineTooShort, PetSpeech, clean_line, validate_line


def test_json_line():
    raw = '{"line":"宝宝又在跟扫雷较劲呀，本鱼陪你慢慢点，这盘玩完就早点休息好不好？"}'
    assert clean_line(raw) == "宝宝又在跟扫雷较劲呀，本鱼陪你慢慢点，这盘玩完就早点休息好不好？"


def test_plain_text_and_quotes():
    raw = '“宝宝今天也超棒的呀，本鱼一直在旁边看着你呢。”'
    assert clean_line(raw) == "宝宝今天也超棒的呀，本鱼一直在旁边看着你呢。"


def test_code_fence_json():
    raw = '```json\n{"line":"宝宝先喝口水吧，本鱼陪你把这盘慢慢下完。"}\n```'
    assert clean_line(raw) == "宝宝先喝口水吧，本鱼陪你把这盘慢慢下完。"


def test_newlines_are_removed():
    raw = "宝宝别急呀\n本鱼在这里陪着你呢，慢慢来就好。"
    out = clean_line(raw)
    assert "\n" not in out
    assert 20 <= len(out) <= 50


def test_too_long_is_truncated_to_50():
    raw = "宝宝" + "今天也超级认真呀，" * 8 + "本鱼最喜欢这样努力的你了。"
    out = clean_line(raw)
    assert 20 <= len(out) <= 50


def test_too_short_raises():
    with pytest.raises(LineTooShort):
        clean_line('{"line":"宝宝好"}')
    with pytest.raises(LineTooShort):
        clean_line("")


def test_pydantic_rejects_short():
    with pytest.raises(ValidationError):
        PetSpeech(line="太短了")


def test_validate_line_returns_clean():
    out = validate_line('{"line":"宝宝又在忙呀，本鱼给你倒杯水，记得歇一会儿哦。"}')
    assert 20 <= len(out) <= 50
