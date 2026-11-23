"""Git 子进程封装。

三条纪律：
1. 一律关掉 pager，否则在某些终端下会挂起；
2. 一律关掉 core.quotepath，否则中文路径会被转义成八进制；
3. 一律按 UTF-8 解码并容错，Windows 下默认代码页不可信。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

__all__ = ["GitError", "resolve_repo", "run", "stream", "repo_meta"]


# git 的报错直接抛给用户没有意义 —— "your current branch 'main' does not have
# any commits yet" 是给开发者看的，不是给用户看的。这里识别出已知情形，
# 打上 code，由上层按语言给出人话。
_KNOWN_ERRORS = (
    ("does not have any commits yet", "empty_repo"),
    ("does not have any commits", "empty_repo"),
    ("bad revision", "bad_revision"),
    ("not a git repository", "not_a_repo"),
)


def _classify(stderr: str) -> str | None:
    lowered = stderr.lower()
    for needle, code in _KNOWN_ERRORS:
        if needle in lowered:
            return code
    return None


class GitError(RuntimeError):
    """git 调用失败。`code` 用于把已知情形翻译成人话。"""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def _git_binary() -> str:
    binary = shutil.which("git")
    if not binary:
        raise GitError("未找到 git，请确认已安装 Git 并加入 PATH")
    return binary


def _base(repo: Path) -> list[str]:
    # 全局选项必须排在子命令之前
    return [
        _git_binary(),
        "-C",
        str(repo),
        "--no-pager",
        "-c",
        "core.quotepath=false",
        "-c",
        "log.showSignature=false",
        "-c",
        "i18n.logOutputEncoding=UTF-8",
    ]


def resolve_repo(path: str | os.PathLike[str]) -> Path:
    """把任意子目录解析成仓库根目录。"""
    candidate = Path(path).expanduser()
    if not candidate.exists():
        raise GitError(f"路径不存在：{candidate}")
    out, code = run(candidate, ["rev-parse", "--show-toplevel"], check=False)
    if code != 0 or not out.strip():
        raise GitError(f"不是 Git 仓库：{candidate}")
    return Path(out.strip())


def run(
    repo: Path,
    args: list[str],
    *,
    check: bool = True,
    timeout: int = 600,
) -> tuple[str, int]:
    """执行一条 git 命令，返回 (stdout, returncode)。"""
    proc = subprocess.run(
        _base(repo) + args,
        capture_output=True,
        timeout=timeout,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git {' '.join(args)} 失败：{err}", code=_classify(err))
    return out, proc.returncode


def stream(repo: Path, args: list[str]) -> Iterator[str]:
    """流式读取 git 输出，逐行 yield（已去掉行尾换行）。

    用 Popen 而不是 subprocess.run，是为了让几十万条提交也能边读边解析，
    不必先把整个输出塞进内存。
    """
    proc = subprocess.Popen(
        _base(repo) + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1 << 20,
    )
    assert proc.stdout is not None and proc.stderr is not None
    try:
        for raw in proc.stdout:
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", errors="replace").strip()
        proc.stderr.close()
        if proc.wait() != 0:
            raise GitError(f"git {' '.join(args)} 失败：{err}", code=_classify(err))


def repo_meta(repo: Path) -> dict[str, str | None]:
    """仓库名、当前分支、origin 地址。缺哪项给 None，不抛错。"""

    def _try(args: list[str]) -> str | None:
        out, code = run(repo, args, check=False)
        value = out.strip()
        return value or None if code == 0 else None

    branch = _try(["rev-parse", "--abbrev-ref", "HEAD"])
    if branch == "HEAD":
        # detached HEAD，退化成短 sha
        branch = _try(["rev-parse", "--short", "HEAD"])

    return {
        "name": repo.name,
        "root": str(repo),
        "branch": branch,
        "remote": _try(["remote", "get-url", "origin"]),
    }
