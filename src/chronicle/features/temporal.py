"""时间维度：活跃度序列 + 开发作息热力图。

两条硬性要求：
1. 时间序列必须**补齐空桶**。缺周的折线图会把「停了三个月」画成「平滑过渡」，
   那是最容易骗到自己的失真。
2. 作息用作者本地时区（%aI 自带偏移），不要换算 UTC —— 深夜提交是人的行为，
   不是服务器的行为。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from ..i18n import weekdays
from ..models import Commit
from ._util import (
    iter_month_keys,
    iter_week_keys,
    month_key,
    pct,
    week_bounds,
    week_key,
)

__all__ = ["build_timeline", "build_rhythm"]

# 周数超过这个值就换成按月聚合，否则横轴密到没法看
_MAX_WEEKS = 130


def build_timeline(commits: list[Commit]) -> dict:
    if not commits:
        return {"granularity": "week", "points": [], "totals": {"commits": 0}}

    ordered = sorted(commits, key=lambda c: c.authored_at)
    first_day = ordered[0].authored_at.date()
    last_day = ordered[-1].authored_at.date()

    week_keys = iter_week_keys(first_day, last_day)
    granularity = "week" if len(week_keys) <= _MAX_WEEKS else "month"
    keys = week_keys if granularity == "week" else iter_month_keys(first_day, last_day)

    buckets: dict[str, dict[str, int]] = {
        key: {"commits": 0, "insertions": 0, "deletions": 0} for key in keys
    }
    authors: dict[str, set[str]] = defaultdict(set)

    for commit in ordered:
        day = commit.authored_at.date()
        key = week_key(day) if granularity == "week" else month_key(day)
        bucket = buckets.get(key)
        if bucket is None:
            continue
        bucket["commits"] += 1
        bucket["insertions"] += commit.insertions
        bucket["deletions"] += commit.deletions
        authors[key].add(commit.email.lower())

    points = []
    for key in keys:
        bucket = buckets[key]
        start, end = _bounds(key, granularity)
        points.append(
            {
                "key": key,
                "label": key if granularity == "week" else key,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "commits": bucket["commits"],
                "insertions": bucket["insertions"],
                "deletions": bucket["deletions"],
                "churn": bucket["insertions"] + bucket["deletions"],
                "authors": len(authors[key]),
            }
        )

    return {
        "granularity": granularity,
        "points": points,
        "totals": {
            "commits": sum(p["commits"] for p in points),
            "insertions": sum(p["insertions"] for p in points),
            "deletions": sum(p["deletions"] for p in points),
        },
    }


def _bounds(key: str, granularity: str) -> tuple[date, date]:
    if granularity == "week":
        year, week = key.split("-W")
        monday = date.fromisocalendar(int(year), int(week), 1)
        return monday, date.fromordinal(monday.toordinal() + 6)
    year, month = (int(part) for part in key.split("-"))
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, date.fromordinal(end.toordinal() - 1)


def build_rhythm(commits: list[Commit], *, lang: str = "zh") -> dict:
    labels = weekdays(lang)
    matrix = [[0] * 24 for _ in range(7)]
    for commit in commits:
        moment = commit.authored_at
        matrix[moment.weekday()][moment.hour] += 1

    total = len(commits)
    hourly = [sum(matrix[dow][hour] for dow in range(7)) for hour in range(24)]
    weekday = [sum(matrix[dow]) for dow in range(7)]

    peak_dow = max(range(7), key=lambda d: weekday[d]) if total else 0
    peak_hour = max(range(24), key=lambda h: hourly[h]) if total else 0

    night = sum(hourly[0:6])
    weekend = weekday[5] + weekday[6]

    return {
        "matrix": matrix,
        "hourly": hourly,
        "weekday": weekday,
        "total": total,
        "peak": {
            "dow": peak_dow,
            "hour": peak_hour,
            "count": matrix[peak_dow][peak_hour],
            "label": f"{labels[peak_dow]} {peak_hour:02d}:00",
        },
        "night_ratio": pct(night, total),
        "weekend_ratio": pct(weekend, total),
        "weekday_labels": list(labels),
        "max_cell": max((max(row) for row in matrix), default=0),
    }
