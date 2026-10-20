# -*- coding: utf-8 -*-
"""DeepSeek / OpenAI 兼容视觉调用 + 候选策略打分 + Pydantic 字数约束。"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, Field, ValidationError

from .capture import Shot
from .config import LLMConfig
from .diversity import TurnPlan, candidate_ok, max_similarity, score_candidate
from .geometry import normalize_text
from .meme import (
    Meme,
    build_meme_system_prompt,
    build_meme_user_text,
    fallback_caption,
)
from .prompt import build_system_prompt, build_user_text
from .schemas import extract_remember

MIN_CHARS = 20
MAX_CHARS = 50
_SENTENCE_END = "，。！？；：、,.!?;:…—～~）)】」』”’"


class LLMError(RuntimeError):
    pass


class LineTooShort(ValueError):
    pass


class PetSpeech(BaseModel):
    """模型输出经 Pydantic 约束：20~50 个字符。"""

    line: str = Field(min_length=MIN_CHARS, max_length=MAX_CHARS)


@dataclass
class SpeechResult:
    line: str
    raw: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1
    strategy: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)
    novelty: float = 0.0
    remember: list[tuple[str, str]] = field(default_factory=list)


def _strip_wrapping(text: str) -> str:
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    s = re.sub(r"^\s*(line|speech|text|内容|台词)\s*[:：]\s*", "", s, flags=re.I)
    s = s.strip().strip("\"'“”‘’")
    return s.strip()


def _extract_line(content: str) -> str:
    """从模型输出里取出那句话：优先 JSON，其次纯文本。"""
    raw = (content or "").strip()
    if not raw:
        return ""

    unfenced = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    unfenced = re.sub(r"\s*```$", "", unfenced).strip()
    for candidate in (unfenced, raw):
        try:
            data = json.loads(candidate)
        except Exception:
            data = None
        if isinstance(data, dict):
            for key in ("line", "speech", "text", "content", "message"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return _strip_wrapping(value)
        if isinstance(data, str) and data.strip():
            return _strip_wrapping(data)

    match = re.search(r"\{.*\}", unfenced, flags=re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                for key in ("line", "speech", "text", "content", "message"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        return _strip_wrapping(value)
        except Exception:
            pass

    return _strip_wrapping(unfenced or raw)


def clean_line(content: str) -> str:
    """收成一行并强制 20~50 字；超过 50 在标点处截断，低于 20 抛 LineTooShort。"""
    s = _extract_line(content)
    s = normalize_text(s)
    s = s.strip("\"'“”‘’")
    if not s:
        raise LineTooShort("模型没有返回正文")

    if len(s) > MAX_CHARS:
        cut = MAX_CHARS
        for i in range(MAX_CHARS, 34, -1):
            if s[i - 1] in _SENTENCE_END:
                cut = i
                break
        s = s[:cut].rstrip("，,、；;：:")
        if len(s) > MAX_CHARS:
            s = s[: MAX_CHARS - 1] + "…"
        elif len(s) == 0:
            s = s[:MAX_CHARS]

    if len(s) < MIN_CHARS:
        raise LineTooShort(f"只有 {len(s)} 个字：{s}")
    if len(s) > MAX_CHARS:
        s = s[:MAX_CHARS]
    return s


def validate_line(line: str) -> str:
    cleaned = clean_line(line)
    PetSpeech(line=cleaned)
    return cleaned


def _json_objects(content: str) -> list[Any]:
    raw = (content or "").strip()
    if not raw:
        return []
    unfenced = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    unfenced = re.sub(r"\s*```$", "", unfenced).strip()
    found: list[Any] = []
    for candidate in (unfenced, raw):
        try:
            found.append(json.loads(candidate))
        except Exception:
            pass
    if not found:
        match = re.search(r"\{.*\}", unfenced, flags=re.S)
        if match:
            try:
                found.append(json.loads(match.group(0)))
            except Exception:
                pass
    return found


def extract_candidates(content: str, plan: TurnPlan) -> list[dict[str, Any]]:
    """解析 candidates；兼容单个 line 的旧格式。"""
    ids = plan.strategy_ids
    default_id = ids[0] if ids else "observe"
    results: list[dict[str, Any]] = []
    for data in _json_objects(content):
        items: list[Any] = []
        if isinstance(data, dict) and isinstance(data.get("candidates"), list):
            items = data["candidates"]
        elif isinstance(data, dict) and ("line" in data or "text" in data):
            items = [data]
        elif isinstance(data, list):
            items = data
        for item in items:
            if isinstance(item, str):
                text, sid = item, default_id
            elif isinstance(item, dict):
                text = item.get("line") or item.get("text") or item.get("speech") or ""
                sid = str(item.get("strategy") or default_id)
            else:
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            if sid not in ids:
                sid = default_id
            try:
                line = clean_line(text)
            except (LineTooShort, ValidationError):
                continue
            if any(row["line"] == line for row in results):
                continue
            results.append({"strategy": sid, "line": line})
    return results


class VisionLLM:
    def __init__(self, cfg: LLMConfig, api_key: str):
        if not api_key:
            raise LLMError("没有找到 DEEPSEEK_API_KEY")
        self.cfg = cfg
        self.client = OpenAI(
            api_key=api_key,
            base_url=cfg.base_url.rstrip("/"),
            timeout=cfg.timeout_sec,
            max_retries=0,
        )
        self.system_prompt = build_system_prompt()

    def _request_once(
        self, user_text: str, shot: Shot, system_prompt: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": shot.data_url, "detail": "high"}},
        ]
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt or self.system_prompt},
                {"role": "user", "content": content},
            ],
            "max_tokens": self.cfg.max_tokens,
            "temperature": self.cfg.temperature,
        }
        if self.cfg.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        kwargs["extra_body"] = {"reasoning_effort": self.cfg.reasoning_effort}

        try:
            resp = self.client.chat.completions.create(**kwargs)
        except TypeError:
            kwargs.pop("extra_body", None)
            resp = self.client.chat.completions.create(**kwargs)

        message = resp.choices[0].message
        text = getattr(message, "content", None) or ""
        finish = getattr(resp.choices[0], "finish_reason", None)
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
        if not text.strip():
            reasoning = getattr(message, "reasoning_content", None) or ""
            raise LLMError(
                f"模型没有返回正文（finish={finish}，reasoning={len(reasoning)} 字符，"
                f"max_tokens={self.cfg.max_tokens}）"
            )
        return text, usage

    def _request_with_retry(
        self, user_text: str, shot: Shot, system_prompt: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        delay = 2.0
        last: Exception | None = None
        for attempt in range(self.cfg.connect_retries + 1):
            try:
                return self._request_once(user_text, shot, system_prompt)
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                last = e
                if attempt >= self.cfg.connect_retries:
                    break
                time.sleep(delay)
                delay = min(delay * 2, 20)
            except APIStatusError as e:
                if e.status_code >= 500 or e.status_code == 429:
                    last = e
                    if attempt >= self.cfg.connect_retries:
                        break
                    time.sleep(delay)
                    delay = min(delay * 2, 20)
                    continue
                detail = ""
                try:
                    detail = e.response.text[:500]
                except Exception:
                    pass
                raise LLMError(f"接口返回 {e.status_code}：{e.message} {detail}") from e
        raise LLMError(f"接口连接失败：{last}") from last

    def generate(
        self,
        shot: Shot,
        plan: TurnPlan,
        window_title: str,
        idle_min: float,
        recent_lines: list[str] | None = None,
        scene_block: str = "",
        activity_block: str = "",
        memory_block: str = "",
        window_block: str = "",
    ) -> SpeechResult:
        base_user = build_user_text(
            window_title=window_title,
            idle_min=idle_min,
            scene_block=scene_block,
            recent_lines=recent_lines,
            plan=plan,
            activity_block=activity_block,
            memory_block=memory_block,
            window_block=window_block,
        )
        user_text = base_user
        last_error = ""
        attempts = 0
        count = max(1, len(plan.strategies))
        for attempt in range(self.cfg.line_retries + 1):
            attempts = attempt + 1
            try:
                raw, usage = self._request_with_retry(user_text, shot)
            except LLMError as e:
                if "没有返回正文" in str(e) and attempt < self.cfg.line_retries:
                    time.sleep(1.5)
                    continue
                raise
            candidates = extract_candidates(raw, plan)
            if candidates:
                recent = list(recent_lines or [])
                acceptable = [row for row in candidates if candidate_ok(row["line"], plan)]
                if not acceptable and attempt >= self.cfg.line_retries:
                    # 重试到最后一次还是全被硬约束打回：宁可说一句，也别整轮哑掉
                    acceptable = candidates
                    last_error = "最后一次仍不满足硬约束，退化为最不像复读的一句"
                    logging.getLogger("pet_eye").warning(
                        "候选全部违反硬约束，最后一轮放宽：%s", "; ".join(r["line"] for r in candidates[:3])
                    )
                if not acceptable:
                    last_error = "候选都用了禁用词/禁用开头，或句式不对"
                else:
                    scored = [
                        (score_candidate(row["line"], row["strategy"], plan, recent), row)
                        for row in acceptable
                    ]
                    scored.sort(key=lambda item: item[0], reverse=True)
                    _best_score, best = scored[0]
                    sim = max_similarity(best["line"], recent)
                    if sim > plan.similarity_threshold and attempt < self.cfg.line_retries:
                        last_error = f"和最近说过的话太像（相似度 {sim:.2f}）"
                    else:
                        return SpeechResult(
                            line=best["line"],
                            raw=raw,
                            model=self.cfg.model,
                            usage=usage,
                            attempts=attempts,
                            strategy=best["strategy"],
                            candidates=[row for _, row in scored],
                            novelty=round(1.0 - sim, 3),
                            remember=extract_remember(raw),
                        )
            else:
                last_error = f"没有 {count} 个 20~50 字的合格候选"
            user_text = (
                base_user
                + f"\n\n上一次输出不合格（{last_error}）。"
                + f"请严格输出 {count} 个候选，每个 20~50 字、单行；换一个角度和开头，只输出 JSON。"
            )
        raise LLMError(f"连续 {attempts} 次没有给出合格候选：{last_error}")

    def pick_meme(
        self,
        shot: Shot,
        candidates: list[Meme],
        window_title: str,
        scene_block: str,
        tone_label: str = "日常",
        recent_lines: list[str] | None = None,
        banned_words: list[str] | None = None,
    ) -> tuple[Meme | None, str, dict[str, Any]]:
        if not candidates:
            return None, "", {}
        base_user = build_meme_user_text(
            window_title=window_title,
            scene_block=scene_block,
            candidates=candidates,
            tone_label=tone_label,
            recent_lines=recent_lines,
            banned_words=banned_words,
        )
        user_text = base_user
        usage: dict[str, Any] = {}
        best_meme: Meme | None = None
        for attempt in range(self.cfg.line_retries + 1):
            raw, usage = self._request_with_retry(
                user_text, shot, build_meme_system_prompt()
            )
            for data in _json_objects(raw):
                if not isinstance(data, dict):
                    continue
                key = str(data.get("meme") or "").strip()
                match = re.match(r"M(\d+)", key, re.I)
                if not match:
                    continue
                index = int(match.group(1)) - 1
                if not (0 <= index < len(candidates)):
                    continue
                best_meme = candidates[index]
                try:
                    caption = clean_line(str(data.get("line") or ""))
                except (LineTooShort, ValidationError):
                    caption = ""
                if not caption:
                    break
                if banned_words and any(word in caption for word in banned_words):
                    break
                return best_meme, caption, usage
            user_text = (
                base_user
                + "\n\n上一次配文不合格：必须是 20~50 字单行，且不能出现禁用词。"
                + "请重新选一张并重写配文，只输出 JSON。"
            )
        if best_meme is not None:
            return best_meme, fallback_caption(best_meme.tag), usage
        return None, "", usage
