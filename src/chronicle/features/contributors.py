"""贡献者画像。

身份归一一律以邮箱为准。真实仓库里同一个人常有多个 name（中文名 / 英文名 /
机器名），用 name 合并会把一个人拆成三个，也可能把两个人并成一个。
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..clean import normalize_author_key
from ..models import Commit
from ._util import pct, round2

__all__ = ["build"]


def build(commits: list[Commit], *, top: int = 12) -> dict:
    """注意：这里不做脱敏。脱敏只有一个入口（redact.redact_report），
    否则会出现两套别名互相对不上的情况。"""

    if not commits:
        return {"rows": [], "total_commits": 0, "empty": True}

    stats: dict[str, dict] = defaultdict(
        lambda: {
            "names": Counter(),
            "emails": Counter(),
            "commits": 0,
            "insertions": 0,
            "deletions": 0,
            "days": set(),
            "night": 0,
            "weekend": 0,
            "kinds": Counter(),
            "first": None,
            "last": None,
        }
    )

    for commit in commits:
        key = normalize_author_key(commit.author, commit.email)
        entry = stats[key]
        entry["names"][commit.author] += 1
        entry["emails"][commit.email.lower()] += 1
        entry["commits"] += 1
        entry["insertions"] += commit.insertions
        entry["deletions"] += commit.deletions
        entry["days"].add(commit.authored_at.date())
        entry["kinds"][commit.kind] += 1
        moment = commit.authored_at
        if moment.hour < 6:
            entry["night"] += 1
        if moment.weekday() >= 5:
            entry["weekend"] += 1
        if entry["first"] is None or moment < entry["first"]:
            entry["first"] = moment
        if entry["last"] is None or moment > entry["last"]:
            entry["last"] = moment

    total = len(commits)
    rows = []
    for key, entry in stats.items():
        name = entry["names"].most_common(1)[0][0]
        email = entry["emails"].most_common(1)[0][0]
        dominant = entry["kinds"].most_common(1)[0][0] if entry["kinds"] else "other"
        rows.append(
            {
                "key": key,
                "name": name,
                "email": email,
                "commits": entry["commits"],
                "share": pct(entry["commits"], total),
                "insertions": entry["insertions"],
                "deletions": entry["deletions"],
                "net": entry["insertions"] - entry["deletions"],
                "active_days": len(entry["days"]),
                "first_commit_at": entry["first"].isoformat() if entry["first"] else None,
                "last_commit_at": entry["last"].isoformat() if entry["last"] else None,
                "night_share": pct(entry["night"], entry["commits"]),
                "weekend_share": pct(entry["weekend"], entry["commits"]),
                "dominant_kind": dominant,
                "avg_commits_per_active_day": round2(
                    entry["commits"] / max(1, len(entry["days"]))
                ),
            }
        )

    rows.sort(key=lambda r: (-r["commits"], -r["net"]))
    top_rows = rows[:top]

    return {
        "rows": top_rows,
        "total_commits": total,
        "count": len(rows),
        "single_author": len(rows) == 1,
        "concentration": round2(top_rows[0]["share"]) if top_rows else 0.0,
        "rest_commits": total - sum(r["commits"] for r in top_rows),
        "empty": False,
    }
