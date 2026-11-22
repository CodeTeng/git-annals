"""个人开发者画像。

和项目报告共用同一个 Git 遍历结果，只是换一个切面看：把「项目经历了什么」
换成「这个人在这个项目里是什么样」。

所有判断都是规则，且每条规则都能用报告里的数字反查 —— 画像最怕的是
「看起来很懂你，但其实在编」。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date

from ..clean import file_category
from ..i18n import t
from ..identity import KeyResolver, build_resolver
from ..models import Commit
from ._util import pct, round2

__all__ = [
    "AuthorLookupError",
    "resolve_aliases",
    "resolve_author",
    "build_persona",
    "build_peers",
    "chapter_participation",
    "author_quote",
]


class AuthorLookupError(ValueError):
    """找不到人，或者匹配到多个。带候选列表，方便命令行直接打印。"""

    def __init__(self, message: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


def _index(commits: list[Commit], resolver: KeyResolver | None = None) -> dict[str, dict]:
    resolver = resolver or build_resolver()
    table: dict[str, dict] = {}
    for commit in commits:
        key = resolver.key(commit)
        entry = table.setdefault(
            key, {"names": Counter(), "emails": Counter(), "commits": 0}
        )
        entry["names"][commit.author] += 1
        entry["emails"][commit.email.lower()] += 1
        entry["commits"] += 1
    return table


def resolve_aliases(
    query: str | None, aliases: dict[str, list[str]] | None
) -> tuple[str, dict[str, list[str]]]:
    """把 `--author a@x,b@y` 这种写法展开成别名表，返回 (首个查询词, 合并后的别名表)。

    抽出来的原因：画像本身和「同路人」必须用同一份合并规则，否则那个人会
    把自己算成自己的同事。
    """
    parts = [part.strip() for part in (query or "").split(",") if part.strip()]
    merged = dict(aliases or {})
    if len(parts) > 1:
        merged["__cli__"] = parts
    return (parts[0] if parts else ""), merged


def resolve_author(
    commits: list[Commit],
    query: str,
    *,
    aliases: dict[str, list[str]] | None = None,
) -> tuple[str, str, str, list[Commit]]:
    """按邮箱 / 姓名 / 邮箱前缀找人，返回 (key, 显示名, 邮箱, 该人的提交)。

    query 用逗号分隔多个身份时，视为同一个人的多个邮箱，直接合并。
    """
    parts = [part.strip() for part in (query or "").split(",") if part.strip()]
    merged = dict(aliases or {})
    if len(parts) > 1:
        merged["__cli__"] = parts

    resolver = build_resolver(merged)
    table = _index(commits, resolver)
    if not table:
        raise AuthorLookupError("仓库里没有提交")

    needle = (parts[0] if parts else "").lower()
    if not needle:
        raise AuthorLookupError("--author 不能为空")

    key: str | None = None
    if needle in table:
        key = needle
    else:
        by_name = [k for k, e in table.items() if any(needle in n.lower() for n in e["names"])]
        by_email = [k for k in table if needle in k]
        matches = sorted(set(by_name) | set(by_email))
        if len(matches) == 1:
            key = matches[0]
        elif len(matches) > 1:
            raise AuthorLookupError(
                f"「{query}」匹配到 {len(matches)} 个人，请写得更具体",
                [_label(table[k]) for k in matches],
            )
        else:
            raise AuthorLookupError(
                f"没找到「{query}」，仓库里的贡献者是：",
                [_label(table[k]) for k in table],
            )

    entry = table[key]
    name = entry["names"].most_common(1)[0][0]
    email = entry["emails"].most_common(1)[0][0]
    subset = [c for c in commits if resolver.key(c) == key]
    return key, name, email, subset


def _label(entry: dict) -> str:
    email = entry["emails"].most_common(1)[0][0]
    name = entry["names"].most_common(1)[0][0]
    return f"{name} <{email}>"


def _streak(days: list[date]) -> int:
    best = current = 1
    for previous, day in zip(days, days[1:]):
        current = current + 1 if (day - previous).days == 1 else 1
        best = max(best, current)
    return best if days else 0


def build_persona(
    subset: list[Commit],
    all_commits: list[Commit],
    *,
    key: str,
    name: str,
    email: str,
    lang: str = "zh",
    repo_authors: int = 1,
) -> dict:
    if not subset:
        raise AuthorLookupError("这个人没有提交")

    ordered = sorted(subset, key=lambda c: c.authored_at)
    first, last = ordered[0].authored_at, ordered[-1].authored_at
    days = sorted({c.authored_at.date() for c in subset})
    span_days = (last.date() - first.date()).days + 1

    insertions = sum(c.insertions for c in subset)
    deletions = sum(c.deletions for c in subset)
    kinds = Counter(c.kind for c in subset)
    total = len(all_commits)

    night = sum(1 for c in subset if c.authored_at.hour < 6)
    weekend = sum(1 for c in subset if c.authored_at.weekday() >= 5)
    hour_counts = Counter(c.authored_at.hour for c in subset)
    peak_hour = hour_counts.most_common(1)[0][0] if hour_counts else 0

    per_day: dict[date, int] = Counter(c.authored_at.date() for c in subset)
    busiest_day, busiest_count = per_day.most_common(1)[0]

    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    longest_gap = max(gaps) if gaps else 0

    footprint = _footprint(subset)
    avg_churn = (insertions + deletions) / max(1, len(subset))

    persona = {
        "key": key,
        "name": name,
        "email": email,
        "commits": len(subset),
        "share": pct(len(subset), total),
        "insertions": insertions,
        "deletions": deletions,
        "net": insertions - deletions,
        "active_days": len(days),
        "span_days": span_days,
        "span_start": first.date().isoformat(),
        "span_end": last.date().isoformat(),
        "first_commit_at": first.isoformat(),
        "last_commit_at": last.isoformat(),
        "avg_commits_per_active_day": round2(len(subset) / max(1, len(days))),
        "avg_churn_per_commit": round2(avg_churn),
        "night_ratio": pct(night, len(subset)),
        "weekend_ratio": pct(weekend, len(subset)),
        "peak_hour": peak_hour,
        "streak_days": _streak(days),
        "longest_gap_days": longest_gap,
        "busiest_day": busiest_day.isoformat(),
        "busiest_day_commits": busiest_count,
        "kind_mix": [
            {"kind": kind, "count": count, "share": pct(count, len(subset))}
            for kind, count in kinds.most_common()
        ],
        "dominant_kind": kinds.most_common(1)[0][0] if kinds else "other",
        "top_dirs": footprint["dirs"],
        "top_files": footprint["files"],
        "repo_authors": repo_authors,
        "repo_commits": total,
        "repo_span_days": (
            (max(c.authored_at for c in all_commits).date()
             - min(c.authored_at for c in all_commits).date()).days + 1
        ),
    }

    persona["tags"] = _derive_tags(persona, lang)
    persona["verdict"] = _verdict(persona, lang)
    return persona


def _footprint(commits: list[Commit], *, limit: int = 5) -> dict:
    dirs: Counter[str] = Counter()
    files: Counter[str] = Counter()
    for commit in commits:
        for change in commit.changes:
            parts = change.path.split("/")
            dirs[parts[0] if len(parts) > 1 else "(root)"] += change.churn
            files[change.path] += change.churn
    return {
        "dirs": [{"name": name, "churn": value} for name, value in dirs.most_common(limit)],
        "files": [
            {"path": path, "churn": value, "category": file_category(path)}
            for path, value in files.most_common(limit)
        ],
    }


# 标签规则：id, 判定, 权重。权重只用于排序，不做展示。
_TAG_RULES: tuple[tuple[str, Callable[[dict], bool], int], ...] = (
    ("night_owl", lambda p: p["night_ratio"] >= 15, 9),
    ("weekend_warrior", lambda p: p["weekend_ratio"] >= 18, 8),
    ("sprinter", lambda p: p["streak_days"] >= 5, 7),
    ("firefighter", lambda p: _kind_share(p, "fix", "revert") >= 28, 9),
    ("refactorer", lambda p: _kind_share(p, "refactor") >= 18, 8),
    ("documenter", lambda p: _kind_share(p, "docs") >= 22, 6),
    ("big_bang", lambda p: p["avg_churn_per_commit"] >= 400, 6),
    ("micro", lambda p: p["avg_churn_per_commit"] <= 60 and p["commits"] >= 15, 6),
    ("anchor", lambda p: p["share"] >= 40, 10),
    ("lone_wolf", lambda p: p["share"] >= 70 and p["repo_authors"] > 1, 7),
    ("ghost", lambda p: p["active_days"] / max(1, p["span_days"]) < 0.15, 5),
)


def _kind_share(persona: dict, *kinds: str) -> float:
    return sum(
        item["share"] for item in persona["kind_mix"] if item["kind"] in kinds
    )


def _derive_tags(persona: dict, lang: str, limit: int = 3) -> list[dict]:
    tags: list[dict] = []
    probe = persona
    for tag_id, rule, weight in _TAG_RULES:
        try:
            if rule(probe):
                tags.append({"id": tag_id, "label": t(f"persona.tag.{tag_id}", lang), "weight": weight})
        except (KeyError, ZeroDivisionError):
            continue
    tags.sort(key=lambda item: -item["weight"])
    return [{"id": item["id"], "label": item["label"]} for item in tags[:limit]]


def _verdict(persona: dict, lang: str) -> str:
    tag = persona["tags"][0]["id"] if persona["tags"] else "default"
    key = f"persona.verdict.{tag}"
    fallback = "persona.verdict.default"
    text = t(key, lang, **{
        "name": persona["name"],
        "commits": persona["commits"],
        "share": persona["share"],
        "night": persona["night_ratio"],
        "weekend": persona["weekend_ratio"],
        "streak": persona["streak_days"],
        "days": persona["span_days"],
        "churn": f"{int(persona['avg_churn_per_commit'])}",
    })
    if text == key:
        text = t(fallback, lang, name=persona["name"], commits=persona["commits"],
                 share=persona["share"], days=persona["span_days"])
    return text


def build_peers(
    all_commits: list[Commit],
    key: str,
    *,
    limit: int = 5,
    aliases: dict[str, list[str]] | None = None,
) -> list[dict]:
    """同路人：按「共同动过的文件数」衡量协作密度。"""
    resolver = build_resolver(aliases)
    table = _index(all_commits, resolver)
    mine: set[str] = set()
    for commit in all_commits:
        if resolver.key(commit) == key:
            mine.update(change.path for change in commit.changes)
    if not mine:
        return []

    overlap: dict[str, set[str]] = defaultdict(set)
    for commit in all_commits:
        other = resolver.key(commit)
        if other == key:
            continue
        overlap[other].update(change.path for change in commit.changes)

    rows = []
    for other, files in overlap.items():
        shared = mine & files
        if not shared:
            continue
        entry = table.get(other) or {"names": Counter(), "emails": Counter(), "commits": 0}
        email = entry["emails"].most_common(1)[0][0]
        name = entry["names"].most_common(1)[0][0]
        rows.append(
            {
                "name": name,
                "email": email,
                "commits": entry["commits"],
                "shared_files": len(shared),
                "overlap": pct(len(shared), len(mine)),
            }
        )
    rows.sort(key=lambda r: (-r["shared_files"], -r["commits"]))
    return rows[:limit]


def chapter_participation(
    chapters: list[dict], subset: list[Commit], *, lang: str
) -> list[dict]:
    """把项目章节映射到这个人身上：他参与了哪几段，各占多少。"""
    rows = []
    for chapter in chapters:
        lo, hi = date.fromisoformat(chapter["start"]), date.fromisoformat(chapter["end"])
        mine = [c for c in subset if lo <= c.authored_at.date() <= hi]
        if not mine:
            rows.append(
                {
                    "id": chapter["id"],
                    "title": chapter["title"],
                    "start": chapter["start"],
                    "end": chapter["end"],
                    "commits": 0,
                    "share": 0.0,
                    "chapter_commits": chapter["commits"],
                    "absent": True,
                }
            )
            continue
        rows.append(
            {
                "id": chapter["id"],
                "title": chapter["title"],
                "start": chapter["start"],
                "end": chapter["end"],
                "commits": len(mine),
                "share": pct(len(mine), chapter["commits"]),
                "chapter_commits": chapter["commits"],
                "absent": False,
            }
        )
    return rows


def author_quote(persona: dict, *, lang: str = "zh", tone: str = "tech") -> str:
    key = "persona.quote.roast" if tone == "roast" else "persona.quote.tech"
    return t(
        key,
        lang,
        name=persona["name"],
        commits=persona["commits"],
        share=persona["share"],
        days=persona["span_days"],
        insertions=f"{persona['insertions']:,}",
        deletions=f"{persona['deletions']:,}",
    )
