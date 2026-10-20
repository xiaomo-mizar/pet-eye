# -*- coding: utf-8 -*-
"""模型输出的 Pydantic 约束。

模型的返回不是一个裸字符串，而是：

```json
{"candidates": [{"strategy": "care", "line": "……"}],
 "remember": [{"tag": "写代码", "text": "主人在改 pet-eye 的记忆模块"}]}
```

`remember` 是可选的：只有它觉得「这件事以后用得上」才写。这里用 Pydantic 把形状和
长度卡死，脏数据一律丢掉，不让它污染记忆库。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

TAG_MAX = 12
NOTE_MAX = 60
REMEMBER_MAX = 2


class MemoryNote(BaseModel):
    """模型想记住的一件事。"""

    tag: str = Field(min_length=1, max_length=TAG_MAX)
    text: str = Field(min_length=2, max_length=NOTE_MAX)

    @field_validator("tag", "text")
    @classmethod
    def _clean(cls, value: str) -> str:
        text = " ".join(str(value or "").split())
        text = text.strip("。，,.;；:：\"'“”")
        return text

    @field_validator("tag")
    @classmethod
    def _no_bad_tag(cls, value: str) -> str:
        # 标签只要名词性短语，出现句子标点说明模型写歪了
        if any(ch in value for ch in "，。！？；、,.!?;"):
            raise ValueError("tag 里不该有句子标点")
        return value


class Candidate(BaseModel):
    """一个候选句子。长度由 llm.clean_line 统一收，这里只管形状。"""

    strategy: str = ""
    line: str = Field(min_length=1, max_length=200)


class TurnResponse(BaseModel):
    """一整轮的返回。"""

    candidates: list[Candidate] = Field(default_factory=list)
    remember: list[MemoryNote] = Field(default_factory=list)

    def notes(self, limit: int = REMEMBER_MAX) -> list[tuple[str, str]]:
        return [(note.tag, note.text) for note in self.remember[:limit]]


def _json_candidates(content: str) -> list[Any]:
    raw = (content or "").strip()
    if not raw:
        return []
    unfenced = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    unfenced = re.sub(r"\s*```$", "", unfenced).strip()
    out: list[Any] = []
    for candidate in (unfenced, raw):
        try:
            out.append(json.loads(candidate))
        except Exception:
            pass
    if not out:
        match = re.search(r"\{.*\}", unfenced, flags=re.S)
        if match:
            try:
                out.append(json.loads(match.group(0)))
            except Exception:
                pass
    return out


def extract_remember(content: str, limit: int = REMEMBER_MAX) -> list[tuple[str, str]]:
    """从模型输出里抠出 remember 列表；任何一条不合格就丢那一条，不影响别的。"""
    notes: list[tuple[str, str]] = []
    for data in _json_candidates(content):
        if not isinstance(data, dict):
            continue
        items = data.get("remember")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                continue
            if not isinstance(item, dict):
                continue
            try:
                note = MemoryNote.model_validate(
                    {"tag": item.get("tag"), "text": item.get("text") or item.get("value")}
                )
            except (ValidationError, ValueError):
                continue
            pair = (note.tag, note.text)
            if pair not in notes:
                notes.append(pair)
            if len(notes) >= limit:
                return notes
    return notes


def validate_turn(payload: dict[str, Any]) -> TurnResponse:
    """严格校验一份完整返回（测试和调试用）。"""
    return TurnResponse.model_validate(payload)
