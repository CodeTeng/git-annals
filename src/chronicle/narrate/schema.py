"""LLM 输出的结构与边界校验。

模型返回的 JSON 不能直接信。这里做三件事：
1. 结构必须对（id 不能编、字段类型必须对）；
2. 长度要有边界（否则一段 800 字的旁白会把版面撑爆）；
3. 只接受「我们认得的东西」，多余字段一律丢弃。

校验失败不是致命错误 —— 调用方会带着失败原因重试一次，再不行就回落规则叙事。
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["NarrativeError", "validate"]

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_LIMITS = {
    "zh": {
        "title": (2, 24), "narrative": (20, 420),
        "note": (6, 160), "comment": (2, 120), "quote": (4, 120),
    },
    "en": {
        "title": (2, 40), "narrative": (20, 700),
        "note": (6, 320), "comment": (2, 200), "quote": (4, 200),
    },
}


class NarrativeError(ValueError):
    """输出不合格。消息会被原样塞回给模型用于自我修正。"""


def _text(value: Any, field: str, low: int, high: int) -> str:
    if not isinstance(value, str):
        raise NarrativeError(f"{field} 必须是字符串，收到 {type(value).__name__}")
    cleaned = _CONTROL.sub("", value).strip()
    if not (low <= len(cleaned) <= high):
        raise NarrativeError(f"{field} 长度必须在 {low}-{high} 之间，实际 {len(cleaned)}")
    return cleaned


def validate(
    data: dict[str, Any],
    *,
    chapter_ids: list[str],
    fact_ids: list[str],
    lang: str = "zh",
    mode: str = "project",
) -> dict[str, Any]:
    limits = _LIMITS.get(lang, _LIMITS["zh"])
    allowed_chapters = set(chapter_ids)
    allowed_facts = set(fact_ids)

    phases_out: list[dict[str, str]] = []
    if mode == "author":
        raw_phases = data.get("phases")
        if raw_phases is not None:
            if not isinstance(raw_phases, list):
                raise NarrativeError("phases 必须是数组")
            seen_phases: set[str] = set()
            for index, item in enumerate(raw_phases):
                if not isinstance(item, dict):
                    raise NarrativeError(f"phases[{index}] 必须是对象")
                pid = item.get("id")
                if pid not in allowed_chapters:
                    raise NarrativeError(f"phases[{index}].id={pid!r} 不在允许列表")
                if pid in seen_phases:
                    raise NarrativeError(f"phases 里 {pid} 重复出现")
                seen_phases.add(pid)
                phases_out.append(
                    {"id": pid, "note": _text(item.get("note"), f"phases[{pid}].note", *limits["note"])}
                )

    chapters_out: list[dict[str, str]] = []
    raw_chapters = data.get("chapters")
    if raw_chapters is not None:
        if not isinstance(raw_chapters, list):
            raise NarrativeError("chapters 必须是数组")
        seen: set[str] = set()
        for index, item in enumerate(raw_chapters):
            if not isinstance(item, dict):
                raise NarrativeError(f"chapters[{index}] 必须是对象")
            cid = item.get("id")
            if cid not in allowed_chapters:
                raise NarrativeError(f"chapters[{index}].id={cid!r} 不在允许列表 {sorted(allowed_chapters)}")
            if cid in seen:
                raise NarrativeError(f"chapters 里 {cid} 重复出现")
            seen.add(cid)
            chapters_out.append(
                {
                    "id": cid,
                    "title": _text(item.get("title"), f"chapters[{cid}].title", *limits["title"]),
                    "narrative": _text(
                        item.get("narrative"), f"chapters[{cid}].narrative", *limits["narrative"]
                    ),
                }
            )

    facts_out: list[dict[str, str]] = []
    raw_facts = data.get("fun_facts")
    if raw_facts is not None:
        if not isinstance(raw_facts, list):
            raise NarrativeError("fun_facts 必须是数组")
        seen_facts: set[str] = set()
        for index, item in enumerate(raw_facts):
            if not isinstance(item, dict):
                raise NarrativeError(f"fun_facts[{index}] 必须是对象")
            fid = item.get("id")
            if fid not in allowed_facts:
                raise NarrativeError(f"fun_facts[{index}].id={fid!r} 不在允许列表")
            if fid in seen_facts:
                continue
            seen_facts.add(fid)
            facts_out.append(
                {"id": fid, "comment": _text(item.get("comment"), f"fun_facts[{fid}]", *limits["comment"])}
            )

    quote = None
    if data.get("quote") is not None:
        quote = _text(data.get("quote"), "quote", *limits["quote"])

    if not chapters_out and not phases_out and not facts_out and not quote:
        raise NarrativeError("返回里没有任何可用字段（全空）")

    return {
        "chapters": chapters_out,
        "phases": phases_out,
        "fun_facts": facts_out,
        "quote": quote,
    }
