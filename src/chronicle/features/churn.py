"""文件「修罗场」评分。

原始 PRD 的口径是「变更频次 + 伴随 fix/revert 比例」。用下来有问题：
`utils.py` 会被顶到第一名，因为它被改得最多 —— 但改得多不等于危险。

这里换成三因子：
  freq   变更频次（log 归一化，避免超大文件通吃）
  brittle 该文件的提交里 fix/revert 的占比（易碎度）
  silo   只有一个人碰过 = 知识孤岛，交接时最疼
score = 0.45*freq + 0.30*brittle + 0.25*silo

三个分项都保留在输出里，报告可以直接告诉用户「它为什么上榜」。
"""

from __future__ import annotations

from collections import defaultdict

from ..clean import file_category, normalize_author_key
from ..models import Commit
from ._util import log_norm, pct, round2

__all__ = ["build"]

_FIX_KINDS = frozenset({"fix", "revert"})

# 文档和配置改得再多也不是「危险代码」。降权让它们在榜上靠后，但不至于消失。
_CATEGORY_WEIGHT = {"code": 1.0, "config": 0.65, "doc": 0.55, "other": 0.8}


def build(commits: list[Commit], *, top_files: int = 15, top_dirs: int = 8) -> dict:
    if not commits:
        return {"files": [], "dirs": [], "threshold": 0}

    files: dict[str, dict] = defaultdict(
        lambda: {
            "changes": 0,
            "insertions": 0,
            "deletions": 0,
            "fix_touches": 0,
            "max_single_churn": 0,
            "authors": set(),
        }
    )

    for commit in commits:
        author = normalize_author_key(commit.author, commit.email)
        is_fix = commit.kind in _FIX_KINDS
        for change in commit.changes:
            entry = files[change.path]
            entry["changes"] += 1
            entry["insertions"] += change.insertions
            entry["deletions"] += change.deletions
            entry["max_single_churn"] = max(entry["max_single_churn"], change.churn)
            entry["authors"].add(author)
            if is_fix:
                entry["fix_touches"] += 1

    max_changes = max((e["changes"] for e in files.values()), default=1)
    churn_values = sorted(
        (e["insertions"] + e["deletions"] for e in files.values()), reverse=True
    )
    # 单次波动阈值取 90 分位，且不低于 50 行，避免小仓库全员「volatile」
    volatile_threshold = max(50, churn_values[len(churn_values) // 10] if churn_values else 50)

    rows = []
    for path, entry in files.items():
        changes = entry["changes"]
        fix_ratio = entry["fix_touches"] / changes
        authors = len(entry["authors"])
        silo = 1.0 / authors
        category = file_category(path)

        freq_score = log_norm(changes, max_changes)
        brittle_score = fix_ratio
        # 孤岛风险必须乘上频次：一个只改过两次的单人文件不是风险，是没人碰
        silo_score = (1.0 if authors == 1 else silo) * freq_score

        raw = (0.50 * freq_score + 0.30 * brittle_score + 0.20 * silo_score) * 100
        score = raw * _CATEGORY_WEIGHT.get(category, 0.8)

        tags = []
        if freq_score >= 0.75:
            tags.append("hot")
        if fix_ratio >= 0.35 and changes >= 3 and category == "code":
            tags.append("brittle")
        if authors == 1 and changes >= 5:
            tags.append("silo")
        if entry["max_single_churn"] >= volatile_threshold:
            tags.append("volatile")

        rows.append(
            {
                "path": path,
                "category": category,
                "changes": changes,
                "insertions": entry["insertions"],
                "deletions": entry["deletions"],
                "churn": entry["insertions"] + entry["deletions"],
                "fix_touches": entry["fix_touches"],
                "fix_ratio": round2(fix_ratio),
                "authors": authors,
                "max_single_churn": entry["max_single_churn"],
                "score": round2(score),
                "tags": tags,
            }
        )

    rows.sort(key=lambda r: (-r["score"], -r["changes"]))
    top = rows[:top_files]

    return {
        "files": top,
        "dirs": _aggregate_dirs(rows, top_dirs),
        "threshold": volatile_threshold,
        "counted_files": len(rows),
        "top_score": top[0]["score"] if top else 0,
        "empty": not rows,
    }


def _aggregate_dirs(rows: list[dict], limit: int) -> list[dict]:
    """按顶层模块聚合。报告里给「哪个模块最乱」，比列 15 个文件名更有洞察。"""
    dirs: dict[str, dict] = defaultdict(
        lambda: {"changes": 0, "churn": 0, "files": 0, "fix_touches": 0, "score": 0.0}
    )
    for row in rows:
        parts = row["path"].split("/")
        name = parts[0] if len(parts) > 1 else "(root)"
        entry = dirs[name]
        entry["changes"] += row["changes"]
        entry["churn"] += row["churn"]
        entry["files"] += 1
        entry["fix_touches"] += row["fix_touches"]
        entry["score"] = max(entry["score"], row["score"])

    total_churn = sum(entry["churn"] for entry in dirs.values()) or 1
    result = [
        {
            "name": name,
            "changes": entry["changes"],
            "churn": entry["churn"],
            "files": entry["files"],
            "share": pct(entry["churn"], total_churn),
            "score": round2(entry["score"]),
        }
        for name, entry in dirs.items()
    ]
    result.sort(key=lambda r: (-r["churn"], -r["changes"]))
    return result[:limit]
