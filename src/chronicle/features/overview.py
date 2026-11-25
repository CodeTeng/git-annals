"""全貌指标与提交类型分布。"""

from __future__ import annotations

from collections import Counter

from ..clean import is_bot, normalize_author_key
from ..models import Commit
from ._util import pct

__all__ = ["build"]


def build(commits: list[Commit]) -> dict:
    if not commits:
        return {
            "commits": 0,
            "commits_code": 0,
            "merges": 0,
            "commits_empty": 0,
            "insertions": 0,
            "deletions": 0,
            "churn": 0,
            "net": 0,
            "bulk_commits": 0,
            "bulk_insertions": 0,
            "bulk_deletions": 0,
            "bulk_files": 0,
            "files_touched": 0,
            "span_days": 0,
            "active_days": 0,
            "authors": 0,
            "bots": 0,
            "bot_commits": 0,
            "avg_commits_per_active_day": 0.0,
            "first_commit_at": None,
            "last_commit_at": None,
            "by_kind": {},
            "author_commits": [],
        }

    ordered = sorted(commits, key=lambda c: c.authored_at)
    first, last = ordered[0].authored_at, ordered[-1].authored_at

    active_days = {c.authored_at.date() for c in commits}
    files: set[str] = set()
    for commit in commits:
        files.update(change.path for change in commit.changes)

    author_keys = {normalize_author_key(c.author, c.email) for c in commits}
    bot_keys = {
        normalize_author_key(c.author, c.email) for c in commits if is_bot(c.author, c.email)
    }
    author_counts = Counter(normalize_author_key(c.author, c.email) for c in commits)
    kinds = Counter(c.kind for c in commits)
    insertions = sum(c.insertions for c in commits)
    deletions = sum(c.deletions for c in commits)
    span_days = (last.date() - first.date()).days + 1

    bulk_commits = sum(1 for c in commits if c.is_bulk)
    bulk_files = sum(len(c.dropped) for c in commits)
    bulk_insertions = sum(d.insertions for c in commits for d in c.dropped)
    bulk_deletions = sum(d.deletions for c in commits for d in c.dropped)

    return {
        "commits": len(commits),
        "commits_code": sum(1 for c in commits if c.is_code_commit),
        "merges": sum(1 for c in commits if c.is_merge),
        # 既不是 merge、又没有任何文件变更的提交（空提交 / 只改了噪音文件）。
        # 单独列出来，是为了让 commits = commits_code + merges + commits_empty 成立 ——
        # 三个数并排出现时，读者一定会去加总。
        "commits_empty": sum(
            1 for c in commits if not c.is_merge and not c.changes
        ),
        "insertions": insertions,
        "deletions": deletions,
        "churn": insertions + deletions,
        "net": insertions - deletions,
        "bulk_commits": bulk_commits,
        "bulk_insertions": bulk_insertions,
        "bulk_deletions": bulk_deletions,
        "bulk_files": bulk_files,
        "files_touched": len(files),
        "span_days": span_days,
        "active_days": len(active_days),
        "authors": len(author_keys) - len(bot_keys),
        "bots": len(bot_keys),
        "bot_commits": sum(1 for c in commits if is_bot(c.author, c.email)),
        "avg_commits_per_active_day": round(len(commits) / max(1, len(active_days)), 2),
        "first_commit_at": first.isoformat(),
        "last_commit_at": last.isoformat(),
        "by_kind": {
            kind: {"count": count, "share": pct(count, len(commits))}
            for kind, count in kinds.most_common()
        },
        "author_commits": [count for _, count in author_counts.most_common()],
    }
