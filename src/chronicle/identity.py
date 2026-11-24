"""作者身份归并。

以邮箱为主键是最稳的默认，但现实里同一个人常有多个邮箱（公司 + 个人 + 学校），
`--view author` 会把他的一生切成两半。这里提供显式合并：

    gitannals . --view author --author "me@corp.com,me@gmail.com"

或者在仓库根的 `.annals.toml` 里写死：

    [author]
    [author.aliases]
    Ambition = ["teng2002823@outlook.com", "lt537674@antgroup.com"]

项目视角只提示、不替用户合并：误合并两个同名真人比漏合并更难发现。
个人视角 (`--view author`) 会自动合并判定为同一人的身份 —— `--author` 指向的是人，不是邮箱。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

from .clean import normalize_author_key
from .models import Commit

__all__ = [
    "KeyResolver",
    "build_resolver",
    "name_signature",
    "find_name_collisions",
    "github_login",
]

_NOREPLY = re.compile(r"^(?:\d+\+)?([A-Za-z0-9_-]{3,})@users\.noreply\.github\.com$", re.I)


def github_login(email: str) -> str | None:
    """从 GitHub noreply 地址里取登录名。

    `12345+mo8it@users.noreply.github.com` → `mo8it`。
    这是同一人换邮箱最常见的形态：网页上提交走真邮箱，直接 push 走 noreply。
    """
    match = _NOREPLY.match((email or "").strip())
    return match.group(1).lower() if match else None


def _local_part(email: str) -> str:
    return (email or "").split("@")[0].strip().lower()


def name_signature(name: str) -> str:
    """把显示名压成可比较的签名：去重音、小写、合并空白。

    「Jakub Roztočil」和「Jakub Roztocil」在 Git 里是两个字符串，
    但它们是同一个人 —— 这类差异不该靠人眼发现。
    """
    decomposed = unicodedata.normalize("NFKD", name or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.lower().split())


def find_name_collisions(
    commits: list[Commit],
    *,
    min_commits: int = 8,
    min_each: int = 2,
    limit: int = 3,
) -> list[dict]:
    """找出「同一个显示名对应多个邮箱」的身份组。

    这是漏合并的主要来源：同一个人换了邮箱，Git 认为他们是两个人，
    而画像只会统计到其中一半 —— 用户很难自己意识到这一点。
    只报告，不自动合并：同名同姓的两个真人确实存在，误合并更难纠正。
    """
    if not commits:
        return []

    by_email: dict[str, dict] = {}
    for commit in commits:
        key = normalize_author_key(commit.author, commit.email)
        entry = by_email.setdefault(
            key, {"emails": Counter(), "names": Counter(), "commits": 0}
        )
        entry["emails"][(commit.email or "").strip().lower() or key] += 1
        entry["names"][commit.author.strip()] += 1
        entry["commits"] += 1

    grouped: dict[str, list[dict]] = defaultdict(list)
    for key, entry in by_email.items():
        signature = name_signature(entry["names"].most_common(1)[0][0])
        if signature:
            grouped[signature].append(
                {
                    "key": key,
                    "email": entry["emails"].most_common(1)[0][0],
                    "name": entry["names"].most_common(1)[0][0],
                    "commits": entry["commits"],
                }
            )

    # 第二类分裂：显示名不同，但一个是 GitHub noreply、另一个是真邮箱。
    # 「Mo」和「mo8it」在 Git 眼里毫无关系，靠名字永远匹配不到。
    by_login: dict[str, list[dict]] = defaultdict(list)
    for key, entry in by_email.items():
        name = entry["names"].most_common(1)[0][0]
        for email in entry["emails"]:
            login = github_login(email)
            if not login:
                continue
            by_login[login].append(
                {"key": key, "email": email, "name": name, "commits": entry["commits"]}
            )

    merged_into: dict[str, str] = {}
    for login, hits in by_login.items():
        # 典型形态是「1 个 noreply + 1 个真邮箱」，所以不能要求组内本来就有多人。
        # noreply 的登录名要能和另一方的邮箱前缀或名字对上，才认。
        # 只要有一方是 noreply 就说明是 GitHub 账号，误伤概率低。
        member_keys = {hit["key"] for hit in hits}
        for key, entry in by_email.items():
            if key in member_keys:
                continue
            name = entry["names"].most_common(1)[0][0]
            prefixes = {_local_part(email) for email in entry["emails"]}
            match = name_signature(name) == login or any(
                prefix == login or prefix.startswith(login + c)
                for prefix in prefixes
                for c in (".", "_", "-")
            )
            if match:
                member_keys.add(key)
                merged_into[key] = hits[0]["key"]

    if merged_into:
        # 把这些身份归到同一个组里，按 canonical key 重新聚合
        regrouped: dict[str, list[dict]] = defaultdict(list)
        for key, entry in by_email.items():
            canonical = merged_into.get(key, key)
            name = entry["names"].most_common(1)[0][0]
            regrouped[canonical].append(
                {
                    "key": key,
                    "email": entry["emails"].most_common(1)[0][0],
                    "name": name,
                    "commits": entry["commits"],
                }
            )
        for members in regrouped.values():
            signature = name_signature(members[0]["name"])
            for member in members:
                if member not in grouped[signature]:
                    grouped[signature].append(member)

    collisions = []
    for signature, group in grouped.items():
        if len(group) < 2:
            continue
        # 每个身份都得是真在提交的人。只出现过一次的同名，多半是重名的路人。
        if any(item["commits"] < min_each for item in group):
            continue
        total = sum(item["commits"] for item in group)
        if total < min_commits:
            continue
        group.sort(key=lambda item: -item["commits"])
        collisions.append(
            {
                "name": group[0]["name"],
                "emails": [item["email"] for item in group],
                "commits": total,
                "parts": [(item["email"], item["commits"]) for item in group],
            }
        )

    collisions.sort(key=lambda item: -item["commits"])
    return collisions[:limit]


class KeyResolver:
    """把 (name, email) 映射到统一的作者 key。"""

    __slots__ = ("_alias",)

    def __init__(self, aliases: Mapping[str, Iterable[str]] | None = None) -> None:
        table: dict[str, str] = {}
        for emails in (aliases or {}).values():
            cleaned = [str(email).strip().lower() for email in emails if str(email).strip()]
            if len(cleaned) < 2:
                continue
            # 组内第一个邮箱作为规范 key，其余指向它
            canonical = cleaned[0]
            for email in cleaned:
                table[email] = canonical
        self._alias = table

    def key(self, commit: Commit) -> str:
        base = normalize_author_key(commit.author, commit.email)
        return self._alias.get(base, base)

    def canonical(self, email_or_name: str) -> str:
        base = (email_or_name or "").strip().lower()
        return self._alias.get(base, base)

    def group(self, canonical: str) -> list[str]:
        """列出属于同一身份的所有邮箱，用于提示用户合并了什么。"""
        return sorted({base for base, target in self._alias.items() if target == canonical} | {canonical})


def build_resolver(aliases: Mapping[str, Iterable[str]] | None = None) -> KeyResolver:
    return KeyResolver(aliases)
