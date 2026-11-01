# -*- coding: utf-8 -*-
"""把台词发到 dsh-pet 聊天框的载荷构造。"""

from pet_eye.broadcast import build_payload, post_line


def test_payload_text_only():
    body = build_payload("今晚抽卡吗")
    assert body == {"text": "今晚抽卡吗"}
    assert "image" not in body


def test_payload_skips_missing_image(tmp_path):
    missing = tmp_path / "nope.png"
    assert "image" not in build_payload("hi", missing)


def test_payload_includes_existing_image(tmp_path):
    img = tmp_path / "meme.png"
    img.write_bytes(b"png")
    body = build_payload("配图", img)
    assert body["image"].startswith("file:///")
    assert "meme.png" in body["image"]


def test_post_line_empty_text_is_noop():
    assert post_line("") is False
    assert post_line("   ") is False


def test_post_line_without_origin_is_noop():
    assert post_line("你好", origin="") is False
