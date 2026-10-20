# -*- coding: utf-8 -*-
"""表情包图库与提示词的单测（没有图库时自动跳过）。"""

import pytest

from pet_eye.meme import MEME_ROOT, MemeLibrary, build_meme_user_text


def _library() -> MemeLibrary:
    if not MEME_ROOT.exists():
        pytest.skip("本机没有 dsh-meme 图库")
    lib = MemeLibrary(["whale-girl-emoji", "dafeiyu-learned"])
    if lib.count == 0:
        pytest.skip("图库为空")
    return lib


def test_library_loads_and_candidates():
    lib = _library()
    cands = lib.candidates(tone="tease", context="写代码 报错 群青", k=8)
    assert cands
    assert all(c.path.exists() for c in cands)
    assert any(c.tag in {"angry", "happy", "confused"} for c in cands)


def test_meme_prompt_has_numbered_candidates():
    lib = _library()
    cands = lib.candidates(tone="care", context="深夜 文档", k=4)
    text = build_meme_user_text(
        "context.py - VS Code",
        "【主人此刻的状态】\n- 在写代码",
        cands,
        tone_label="陪伴关心",
        recent_lines=["刚才那句"],
    )
    assert "M1 [" in text
    assert "只输出 JSON" in text
    assert cands[0].caption in text
