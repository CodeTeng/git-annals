"""叙事增强的编排层。

核心约束：**任何失败都不能毁掉报告**。
网络挂了、模型胡说、JSON 破了 —— 一律回落到规则叙事，只在警告区留一行说明。
用户拿到的永远是一份完整可看的报告，而不是一个堆栈。
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import Settings
from ..i18n import t
from .client import ChatClient, LlmError
from .prompt import build_payload, system_prompt, user_prompt
from .schema import NarrativeError, validate

__all__ = ["enhance"]

ProgressHook = Callable[[str], None]


def enhance(report: dict, settings: Settings, progress: ProgressHook | None = None) -> dict:
    """在已算好的报告上叠加 LLM 叙事。就地修改并返回。"""
    emit = progress or (lambda _msg: None)

    if not settings.llm.enabled:
        report["meta"]["ai"] = {"used": False, "reason": "disabled"}
        return report

    lang = settings.lang

    if not settings.llm.ready():
        report["warnings"].append(t("ui.warn.no_key", lang))
        report["meta"]["ai"] = {"used": False, "reason": "no_api_key"}
        return report

    chapters = report.get("chapters") or []
    facts = report.get("fun_facts") or []
    if not chapters and not facts:
        report["meta"]["ai"] = {"used": False, "reason": "nothing_to_narrate"}
        return report

    view = report.get("view", "project")
    payload = build_payload(report)
    system = system_prompt(lang, settings.tone, view)
    user = user_prompt(payload, lang, view)
    client = ChatClient(settings.llm)

    emit("让模型给这段历史写旁白")
    repair: str | None = None
    last_error = ""

    for attempt in range(settings.llm.retries + 1):
        try:
            data, meta = client.complete_json(system, user, repair=repair)
            validated = validate(
                data,
                chapter_ids=[c["id"] for c in chapters],
                fact_ids=[f["id"] for f in facts],
                lang=lang,
                mode=view,
            )
        except (LlmError, NarrativeError) as exc:
            last_error = str(exc)
            repair = last_error if isinstance(exc, NarrativeError) else None
            if repair is None:
                # 网络层/返回非 JSON，重试一次大概率还是失败，直接放弃
                break
            continue

        applied = _apply(report, validated, lang)
        report["meta"]["ai"] = {
            "used": True,
            "model": meta["model"],
            "latency_ms": meta["latency_ms"],
            "usage": meta["usage"],
            "applied": applied,
            "attempts": attempt + 1,
        }
        return report

    report["warnings"].append(t("ui.warn.llm_failed", lang, msg=last_error[:160]))
    report["meta"]["ai"] = {"used": False, "reason": "failed", "error": last_error[:300]}
    return report


def _apply(report: dict, validated: dict, lang: str) -> dict[str, int]:
    """把模型给的文案贴回报告。原文案保留在 rule_* 字段里，方便对照排查。"""
    if report.get("view") == "author":
        notes = {item["id"]: item["note"] for item in validated["phases"]}
        for phase in report.get("chapters") or []:
            note = notes.get(phase["id"])
            if note:
                phase["note"] = note
        comments = {item["id"]: item["comment"] for item in validated["fun_facts"]}
        for fact in report.get("fun_facts") or []:
            if fact["id"] in comments:
                fact["comment"] = comments[fact["id"]]
        if validated.get("quote"):
            report.setdefault("rule_quote", report.get("quote") or "")
            report["quote"] = validated["quote"]
        return {
            "chapters": len(notes),
            "fun_facts": len(comments),
            "quote": 1 if validated.get("quote") else 0,
        }

    by_id = {item["id"]: item for item in validated["chapters"]}
    replaced_chapters = 0
    for chapter in report.get("chapters") or []:
        update = by_id.get(chapter["id"])
        if not update:
            continue
        chapter.setdefault("rule_title", chapter["title"])
        chapter.setdefault("rule_narrative", chapter["narrative"])
        chapter["title"] = update["title"]
        chapter["narrative"] = update["narrative"]
        replaced_chapters += 1

    comments = {item["id"]: item["comment"] for item in validated["fun_facts"]}
    replaced_facts = 0
    for fact in report.get("fun_facts") or []:
        comment = comments.get(fact["id"])
        if comment:
            fact["comment"] = comment
            replaced_facts += 1

    if validated.get("quote"):
        report.setdefault("rule_quote", report.get("quote") or "")
        report["quote"] = validated["quote"]

    return {
        "chapters": replaced_chapters,
        "fun_facts": replaced_facts,
        "quote": 1 if validated.get("quote") else 0,
    }
