"""测试用的 Git 仓库脚手架。

不用 mock：真的 init、真的 commit。统计工具最怕「测试全绿但数字是错的」。
"""

from __future__ import annotations

import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

from chronicle.clean import BULK_LINE_THRESHOLD

__all__ = ["git", "write", "make_repo", "BULK_LINE_THRESHOLD", "BASE_DATE"]

BASE_DATE = date(2026, 1, 5)

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Alice",
    "GIT_AUTHOR_EMAIL": "alice@example.com",
    "GIT_COMMITTER_NAME": "Alice",
    "GIT_COMMITTER_EMAIL": "alice@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}


def git(repo: Path, *args: str, when: str | None = None) -> None:
    env = dict(_GIT_ENV)
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(
        ["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "user.name=Alice",
         "-c", "user.email=alice@example.com", *args],
        check=True,
        capture_output=True,
        env={**env, "PATH": __import__("os").environ.get("PATH", "")},
    )


def write(repo: Path, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")




def make_repo(root: Path) -> Path:
    """造一个够用的样本仓库：建仓、噪音目录、批量导入、长空档，一个不少。"""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    base = BASE_DATE

    def at(days: int) -> str:
        return f"{base + timedelta(days=days)}T10:00:00+08:00"

    write(root, "README.md", "# demo\n")
    git(root, "add", ".", when=at(0))
    git(root, "commit", "-q", "-m", "chore: init", when=at(0))

    write(root, "src/a.py", "\n".join(f"x{i} = {i}" for i in range(20)) + "\n")
    git(root, "add", ".", when=at(1))
    git(root, "commit", "-q", "-m", "feat: add module a", when=at(1))

    write(root, "node_modules/dep/index.js", "module.exports = 1\n")
    git(root, "add", "-f", ".", when=at(2))
    git(root, "commit", "-q", "-m", "chore: vendor deps", when=at(2))

    write(root, "data/corpus.txt", "line\n" * (BULK_LINE_THRESHOLD + 5000))
    git(root, "add", "-f", ".", when=at(3))
    git(root, "commit", "-q", "-m", "feat: add corpus", when=at(3))

    write(root, "src/a.py", "x0 = 0\n")
    git(root, "add", ".", when=at(4))
    git(root, "commit", "-q", "-m", "fix: repair module a", when=at(4))

    write(root, "src/b.py", "def b():\n    return 1\n")
    git(root, "add", ".", when=at(80))
    git(root, "commit", "-q", "-m", "feat: add module b", when=at(80))
    return root
