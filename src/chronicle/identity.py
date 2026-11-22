"""作者身份归并。

以邮箱为主键是最稳的默认，但现实里同一个人常有多个邮箱（公司 + 个人 + 学校），
`--view author` 会把他的一生切成两半。这里提供显式合并：

    chronicle . --view author --author "me@corp.com,me@gmail.com"

或者在仓库根的 `.chronicle.toml` 里写死：

    [author]
    [author.aliases]
    Ambition = ["teng2002823@outlook.com", "lt537674@antgroup.com"]

不做自动猜：误合并两个人比漏合并一个人更伤，而且用户看得出后者。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .clean import normalize_author_key
from .models import Commit

__all__ = ["KeyResolver", "build_resolver"]


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
