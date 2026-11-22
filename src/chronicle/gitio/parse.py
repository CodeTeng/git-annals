"""`git log --numstat` 的单次遍历解析器。

为什么不是逐 commit 调 `git show --numstat`：
后者是 O(n) 个子进程，一万条提交就要几千次进程创建，慢两个数量级。
这里一次 `git log` 走完整段历史，边读边解析。

格式用不可见控制符做分隔，避免 commit message 里的任意字符撞分隔符：
  \\x1e (RS) 记录开始    \\x1f (FS) 字段分隔
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ..models import Commit, FileChange
from .runner import GitError, stream

__all__ = ["iter_raw_commits", "normalize_renamed_path"]

_RS = "\x1e"
_FS = "\x1f"
# 注意：%s 是单行 subject，不含换行，因此可以安全地放在同一行格式串里。
# 正文（%b）含换行，会破坏按行解析，v1 不取。
_HEADER = _RS + _FS.join(["%H", "%P", "%an", "%ae", "%aI", "%cI", "%s"])
_FIELD_COUNT = 7


def normalize_renamed_path(path: str) -> str:
    """把 numstat 里的重命名写法收敛成新路径。

    `src/{old => new}/x.py` -> `src/new/x.py`
    `a.py => b.py`          -> `b.py`
    """
    if "=>" not in path:
        return path
    if "{" in path and "}" in path:
        head, rest = path.split("{", 1)
        middle, tail = rest.split("}", 1)
        new_part = middle.split("=>", 1)[1].strip()
        return f"{head}{new_part}{tail}".replace("//", "/")
    return path.split("=>", 1)[1].strip()


def _parse_numstat_line(line: str) -> FileChange | None:
    parts = line.split("\t", 2)
    if len(parts) != 3:
        return None
    raw_ins, raw_del, path = parts
    path = normalize_renamed_path(path.strip())
    if not path:
        return None
    binary = raw_ins == "-" or raw_del == "-"
    insertions = 0 if binary else int(raw_ins)
    deletions = 0 if binary else int(raw_del)
    return FileChange(path=path, insertions=insertions, deletions=deletions, binary=binary)


def iter_raw_commits(
    repo: Path,
    *,
    since: str | None = None,
    until: str | None = None,
    all_branches: bool = False,
    follow_renames: bool = False,
) -> Iterator[Commit]:
    """按 git 默认顺序（新到旧）产出提交，changes 尚未做噪音过滤。"""
    args = ["log", "--numstat", f"--format={_HEADER}"]
    # 默认关闭重命名检测：它是日志遍历里最贵的一步，只在需要时开。
    args.append("-M" if follow_renames else "--no-renames")
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    if all_branches:
        args.append("--all")

    current: dict[str, object] | None = None
    changes: list[FileChange] = []

    for line in stream(repo, args):
        if line.startswith(_RS):
            if current is not None:
                yield _build(current, changes)
            current = _parse_header(line)
            changes = []
            continue
        if not line.strip():
            continue
        if current is None:
            continue
        change = _parse_numstat_line(line)
        if change is not None:
            changes.append(change)

    if current is not None:
        yield _build(current, changes)


def _parse_header(line: str) -> dict[str, object]:
    fields = line[1:].split(_FS)
    if len(fields) < _FIELD_COUNT:
        fields += [""] * (_FIELD_COUNT - len(fields))
    sha, parents, author, email, authored, committed, subject = fields[:_FIELD_COUNT]
    return {
        "sha": sha,
        "parents": tuple(p for p in parents.split() if p),
        "author": author,
        "email": email,
        "authored_at": _parse_dt(authored),
        "committed_at": _parse_dt(committed),
        "subject": subject,
    }


def _parse_dt(value: str) -> datetime:
    """%aI / %cI 带本地时区偏移，fromisoformat 可直接吃。"""
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise GitError(f"无法解析提交时间：{value!r}") from exc


def _build(header: dict[str, object], changes: list[FileChange]) -> Commit:
    return Commit(
        sha=str(header["sha"]),
        parents=header["parents"],  # type: ignore[arg-type]
        author=str(header["author"]),
        email=str(header["email"]),
        authored_at=header["authored_at"],  # type: ignore[arg-type]
        committed_at=header["committed_at"],  # type: ignore[arg-type]
        subject=str(header["subject"]),
        changes=changes,
    )
