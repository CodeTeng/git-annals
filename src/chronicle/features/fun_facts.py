"""考古彩蛋与收尾金句。

全部由规则生成 —— 这不是妥协，是设计要求：
`--no-ai` 模式下报告也必须「有梗」，否则工具的价值就塌了一半。
LLM 上线后只负责在同样的事实上换个说法，事实本身不接受编造。
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date

from ..clean import normalize_author_key
from ..models import Commit
from ..i18n import t
from ._util import pct

__all__ = ["build_facts", "build_quote"]

_FINAL_WORDS = re.compile(
    r"\b(final|finally|last|end|really final|for real)\b|最终|最后一次|终于|真的最后|收尾",
    re.IGNORECASE,
)


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def build_facts(
    commits: list[Commit],
    *,
    lang: str = "zh",
    limit: int = 6,
    author_label: str | None = None,
) -> list[dict]:
    if not commits:
        return []

    ordered = sorted(commits, key=lambda c: c.authored_at)
    facts: list[dict] = []
    total_insertions = sum(c.insertions for c in commits)
    total_deletions = sum(c.deletions for c in commits)

    def add(fact_id: str, detail: str, value: str) -> None:
        facts.append(
            {
                "id": fact_id,
                "title": t(f"fact.{fact_id}.title", lang),
                "detail": detail,
                "value": value,
            }
        )

    # 原点
    first = ordered[0]
    origin_key = (
        "fact.origin.detail" if first.insertions or first.deletions else "fact.origin.detail.binary"
    )
    add(
        "origin",
        t(
            origin_key,
            lang,
            date=first.authored_at.date().isoformat(),
            author=author_label or first.author,
            subject=_clip(first.subject, 48),
        ),
        first.authored_at.date().isoformat(),
    )

    # 最深的夜：取「最晚的凌晨」而不是「最早的钟点」，0:15 比 5:59 更像熬通宵
    night = [c for c in commits if c.authored_at.hour < 6]
    if night:
        # 取一天里最早的那个凌晨时刻：00:15 比 05:59 更像熬通宵。
        # 原来用 (24 - hour) % 24，0 点会算成 0、1 点算成 23，反而选中 1 点。
        deepest = min(night, key=lambda c: c.authored_at.time())
        add(
            "deepest_night",
            t(
                "fact.deepest_night.detail",
                lang,
                date=deepest.authored_at.date().isoformat(),
                time=deepest.authored_at.strftime("%H:%M"),
                author=author_label or deepest.author,
                subject=_clip(deepest.subject, 40),
            ),
            deepest.authored_at.strftime("%H:%M"),
        )

    # 最长连续作战
    days = sorted({c.authored_at.date() for c in commits})
    streak, best = 1, (days[0], days[0], 1)
    for prev, cur in zip(days, days[1:]):
        if (cur - prev).days == 1:
            streak += 1
        else:
            streak = 1
        if streak > best[2]:
            best = (cur.fromordinal(cur.toordinal() - streak + 1), cur, streak)
    if best[2] >= 3:
        add(
            "longest_streak",
            t(
                "fact.longest_streak.detail",
                lang,
                days=best[2],
                start=best[0].isoformat(),
                end=best[1].isoformat(),
            ),
            f"{best[2]}{t('unit.day', lang)}",
        )

    # 单条最大提交
    biggest = max(commits, key=lambda c: c.churn)
    if biggest.churn > 0:
        add(
            "biggest_commit",
            t(
                "fact.biggest_commit.detail",
                lang,
                short_sha=biggest.short_sha,
                lines=_fmt_int(biggest.churn),
                subject=_clip(biggest.subject, 48),
            ),
            f"{_fmt_int(biggest.churn)} {t('unit.line', lang)}",
        )

    # 最疯狂的一天
    per_day = Counter(c.authored_at.date() for c in commits)
    busiest_day, busiest_count = per_day.most_common(1)[0]
    if busiest_count >= 4:
        add(
            "busiest_day",
            t("fact.busiest_day.detail", lang, date=busiest_day.isoformat(), commits=busiest_count),
            f"{busiest_count}",
        )

    # final 陷阱
    final_hits = [c for c in commits if _FINAL_WORDS.search(c.subject)]
    if len(final_hits) >= 2:
        add(
            "final_loop",
            t("fact.final_loop.detail", lang, count=len(final_hits)),
            f"{len(final_hits)}",
        )

    # 最长的沉默
    gaps = [
        ((b - a).days, a, b)
        for a, b in zip(days, days[1:])
    ]
    if gaps:
        gap_days, gap_start, gap_end = max(gaps)
        if gap_days >= 14:
            add(
                "silence",
                t(
                    "fact.silence.detail",
                    lang,
                    start=gap_start.isoformat(),
                    end=gap_end.isoformat(),
                    days=gap_days,
                ),
                f"{gap_days}{t('unit.day', lang)}",
            )

    # 深夜在场者
    per_author = Counter()
    per_author_night = Counter()
    for commit in commits:
        key = normalize_author_key(commit.author, commit.email)
        per_author[key] += 1
        if commit.authored_at.hour < 6:
            per_author_night[key] += 1
    if per_author_night:
        key, count = per_author_night.most_common(1)[0]
        if count >= 3 and count / max(1, per_author[key]) >= 0.2:
            name = author_label or next(
                c.author for c in commits if normalize_author_key(c.author, c.email) == key
            )
            add(
                "night_owl",
                t(
                    "fact.night_owl.detail",
                    lang,
                    author=name,
                    share=f"{pct(count, per_author[key]):.0f}%",
                    count=count,
                ),
                f"{pct(count, per_author[key]):.0f}%",
            )

    return facts[:limit]


def build_quote(commits: list[Commit], *, lang: str = "zh", tone: str = "tech") -> str:
    if not commits:
        return ""
    ordered = sorted(commits, key=lambda c: c.authored_at)
    days = (ordered[-1].authored_at.date() - ordered[0].authored_at.date()).days + 1
    insertions = sum(c.insertions for c in commits)
    deletions = sum(c.deletions for c in commits)
    net = insertions - deletions
    key = "quote.roast" if tone == "roast" else "quote.tech"
    # 措辞跟着正负走。这里曾经传的是 insertions+deletions 并写成「行代码」——
    # 对 flask 会报 23 万行，而它十五年净增只有 3.4 万行。
    net_key = "unit.net_add" if net >= 0 else "unit.net_cut"
    return t(
        key,
        lang,
        commits=_fmt_int(len(commits)),
        net_desc=t(net_key, lang, v=_fmt_int(abs(net))),
        days=days,
        insertions=_fmt_int(insertions),
        deletions=_fmt_int(deletions),
    )


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
