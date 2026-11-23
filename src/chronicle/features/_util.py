"""统计口径共用的数学与时间工具。"""

from __future__ import annotations

import math
from datetime import date, datetime
from statistics import pstdev

__all__ = [
    "pct",
    "safe_div",
    "round2",
    "log_norm",
    "mean",
    "std",
    "week_key",
    "week_bounds",
    "month_key",
    "iter_month_keys",
    "iter_week_keys",
]


def pct(part: float, total: float, digits: int = 1) -> float:
    if not total:
        return 0.0
    return round(part / total * 100, digits)


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def round2(value: float) -> float:
    return round(value, 2)


def log_norm(value: float, ceiling: float) -> float:
    """log1p 归一化到 0..1，防止单个超大文件把评分拉爆。"""
    if ceiling <= 0:
        return 0.0
    return math.log1p(value) / math.log1p(ceiling)


def mean(values: list[float] | list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def std(values: list[float] | list[int]) -> float:
    return pstdev(values) if len(values) > 1 else 0.0


def week_key(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_bounds(day: date) -> tuple[date, date]:
    monday = day.fromordinal(day.toordinal() - day.weekday())
    return monday, date.fromordinal(monday.toordinal() + 6)


def month_key(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def iter_month_keys(start: date, end: date) -> list[str]:
    keys: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append(f"{year}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return keys


def iter_week_keys(start: date, end: date) -> list[str]:
    keys: list[str] = []
    cursor = date.fromordinal(start.toordinal() - start.weekday())
    stop = date.fromordinal(end.toordinal() - end.weekday())
    while cursor <= stop:
        keys.append(week_key(cursor))
        cursor = date.fromordinal(cursor.toordinal() + 7)
    return keys



def as_date(value: datetime) -> date:
    return value.date()
