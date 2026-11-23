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
from datetime import timedelta

from ..clean import file_category, normalize_author_key
from ..i18n import t
from ..models import Commit
from ._baseline import over, quantile, ratio, share
from ._util import log_norm, pct, round2

__all__ = ["build"]

_FIX_KINDS = frozenset({"fix", "revert"})

# 「还活着」的判定窗口。这个是天数，不是比例 —— 它表达的是产品口径
# （三个月没动过的文件，不该和昨天还在改的文件挂同一个标签），不是统计阈值。
_RECENT_DAYS = 90

# 绝对下限：分位是相对的，没有下限时「全员都很稳」的仓库也会被标出异常。
_FLOOR_BRITTLE = 0.20
_FLOOR_VOLATILE = 0.45
_FLOOR_SINGLE = 300

# 文档和配置改得再多也不是「危险代码」。降权让它们在榜上靠后，但不至于消失。
_CATEGORY_WEIGHT = {"code": 1.0, "config": 0.65, "doc": 0.55, "other": 0.8}


def build(
    commits: list[Commit],
    *,
    top_files: int = 15,
    top_dirs: int = 8,
    lang: str = "zh",
) -> dict:
    if not commits:
        return {"files": [], "dirs": [], "baseline": {}, "empty": True}

    files: dict[str, dict] = defaultdict(
        lambda: {
            "changes": 0,
            "insertions": 0,
            "deletions": 0,
            "fix_touches": 0,
            "max_single_churn": 0,
            "authors": set(),
            "recent_changes": 0,
            "last_seen": None,
        }
    )
    single_churns: list[float] = []
    newest = max(c.authored_at for c in commits)
    recent_edge = newest - timedelta(days=_RECENT_DAYS)

    for commit in commits:
        author = normalize_author_key(commit.author, commit.email)
        is_fix = commit.kind in _FIX_KINDS
        for change in commit.changes:
            entry = files[change.path]
            entry["changes"] += 1
            entry["insertions"] += change.insertions
            entry["deletions"] += change.deletions
            entry["max_single_churn"] = max(entry["max_single_churn"], change.churn)
            single_churns.append(float(change.churn))
            if commit.authored_at >= recent_edge:
                entry["recent_changes"] += 1
            if entry["last_seen"] is None or commit.authored_at > entry["last_seen"]:
                entry["last_seen"] = commit.authored_at
            entry["authors"].add(author)
            if is_fix:
                entry["fix_touches"] += 1

    max_changes = max((e["changes"] for e in files.values()), default=1)

    # 先把每个文件的各项指标算出来，再按「本仓库自身分布」定分位线。
    # 这样每个标签天然只标记少数异常文件 —— 在 vite 上 15 个文件全挂「易碎」，
    # 和在 flask 上一个都不挂，是同一类错误：拿绝对阈值去衡量因仓库而异的指标。
    drafts = []
    for path, entry in files.items():
        changes = entry["changes"]
        total_churn = entry["insertions"] + entry["deletions"]
        drafts.append(
            {
                "path": path,
                "category": file_category(path),
                "changes": changes,
                "insertions": entry["insertions"],
                "deletions": entry["deletions"],
                "churn": total_churn,
                "fix_touches": entry["fix_touches"],
                "fix_ratio": entry["fix_touches"] / changes,
                "authors": len(entry["authors"]),
                "max_single_churn": entry["max_single_churn"],
                "volatile_share": share(entry["max_single_churn"], total_churn),
                "recent_changes": entry["recent_changes"],
                "last_change": entry["last_seen"].date().isoformat() if entry["last_seen"] else None,
            }
        )

    rows = []
    for draft in drafts:
        freq_score = log_norm(draft["changes"], max_changes)
        brittle_score = draft["fix_ratio"]
        # 孤岛风险必须乘上频次：一个只改过两次的单人文件不是风险，是没人碰
        silo_score = (1.0 if draft["authors"] == 1 else 1.0 / draft["authors"]) * freq_score
        raw = (0.50 * freq_score + 0.30 * brittle_score + 0.20 * silo_score) * 100
        rows.append(
            {
                **draft,
                "fix_ratio": round2(draft["fix_ratio"]),
                "volatile_share": round2(draft["volatile_share"]),
                "score": round2(raw * _CATEGORY_WEIGHT.get(draft["category"], 0.8)),
            }
        )

    rows.sort(key=lambda r: (-r["score"], -r["changes"]))
    top = rows[:top_files]

    # 标签在**榜内**取分位，而不是对全仓库取分位。
    # 原因：榜单是按综合分排的，而综合分本身就含易碎度和波动度 ——
    # 对全仓库取 q90 会让半个榜都过线（vite 实测 8/15）。
    # 榜内 q70 + 绝对下限，既保证「只标少数」，又保证「真的异常才标」。
    cut_brittle = max(quantile([r["fix_ratio"] for r in top], 0.70), _FLOOR_BRITTLE)
    cut_volatile = max(quantile([r["volatile_share"] for r in top], 0.70), _FLOOR_VOLATILE)
    cut_single = max(quantile(single_churns, 0.90), _FLOOR_SINGLE)

    for row in rows:
        tags = []
        # 「高频」不能当标签：这份榜单里的文件本来就全是高频的。
        # 换成「近期仍在动」，它能区分历史热点和当前热点。
        if row["recent_changes"] >= 2:
            tags.append("active")
        if (
            row["category"] == "code"
            and row["changes"] >= 3
            and row["fix_ratio"] >= cut_brittle
        ):
            tags.append("brittle")
        if row["authors"] == 1 and row["changes"] >= 5:
            tags.append("silo")
        if (
            row["changes"] >= 3
            and row["volatile_share"] >= cut_volatile
            and row["max_single_churn"] >= cut_single
        ):
            tags.append("volatile")
        row["tags"] = tags

    return {
        "files": top,
        "dirs": _aggregate_dirs(rows, top_dirs, lang),
        "baseline": {
            "brittle_cut": round2(cut_brittle * 100),
            "volatile_cut": round2(cut_volatile * 100),
            "single_cut": int(cut_single),
            "recent_window_days": _RECENT_DAYS,
        },
        "counted_files": len(rows),
        "top_score": top[0]["score"] if top else 0,
        "empty": not rows,
    }


def _aggregate_dirs(rows: list[dict], limit: int, lang: str = "zh") -> list[dict]:
    """按顶层模块聚合。报告里给「哪个模块最乱」，比列 15 个文件名更有洞察。"""
    root_label = t("ui.dir.root", lang)
    dirs: dict[str, dict] = defaultdict(
        lambda: {"changes": 0, "churn": 0, "files": 0, "fix_touches": 0, "score": 0.0}
    )
    for row in rows:
        parts = row["path"].split("/")
        name = parts[0] if len(parts) > 1 else root_label
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
