"""按「形态」生成仓库，用于不变式测试。

真实仓库太慢也太随机，但历史上踩到的坑都有明确形态：
  uniform     匀速前进（TheAlgorithms：切出来全是同一种章节）
  fix_heavy   修复占比畸高（vite：33% 的提交是 fix，标签全糊）
  delete_heavy 持续删减（重构被误判成常态）
  quiet       大量空档（沉寂 / 二次创业）
  bursty      忽快忽慢（章节跨度极不均）

每个形态都是「一个曾经让报告出错的形状」。测试跑遍它们，
断言的是性质而不是具体数字 —— 换成任何真实仓库，这些性质都该成立。
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

__all__ = ["build_repo", "SHAPES"]

SHAPES = ("uniform", "fix_heavy", "delete_heavy", "quiet", "bursty")

_AUTHORS = [
    ("Ada Lovelace", "ada@example.com"),
    ("Grace Hopper", "grace@example.com"),
    ("Alan Turing", "alan@example.com"),
    ("Barbara Liskov", "barbara@example.com"),
]


def build_repo(path: Path, shape: str = "uniform", *, n: int = 160, seed: int = 7) -> Path:
    if shape not in SHAPES:
        raise ValueError(f"未知形态：{shape}")
    rng = random.Random(seed)
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=demo", "-c", "user.email=demo@example.com",
         "fast-import", "--quiet"],
        cwd=path,
        input=_stream(shape, n, rng),
        check=True,
    )
    subprocess.run(["git", "reset", "-q", "--hard", "main"], cwd=path, check=True)
    return path


def _epoch(day_offset: int) -> int:
    return 1_600_000_000 + day_offset * 86_400


def _stream(shape: str, n: int, rng: random.Random) -> bytes:
    chunks: list[bytes] = []
    sizes: dict[str, int] = {}
    marks = 0
    cursor = 0

    for index in range(n):
        # 时间推进方式决定了这个仓库的「形态」
        if shape == "quiet":
            cursor += rng.choice([0, 0, 1, 40, 90])
        elif shape == "bursty":
            cursor += 0 if index % 40 < 30 else rng.randint(8, 20)
        else:
            cursor += 2

        author, email = rng.choice(_AUTHORS[: (1 if shape == "solo" else 3)])
        kind = "fix" if _is_fix(shape, index, n, rng) else "feat"
        subject = f"{kind}: touch module {rng.randint(0, 9)}"

        path = f"src/mod{rng.randint(0, 9)}.py"
        current = sizes.get(path, 0)
        if shape == "delete_heavy":
            size = max(20, int(current * 0.7)) if current else rng.randint(80, 160)
        elif current == 0:
            size = rng.randint(40, 90)
        else:
            size = current + rng.randint(10, 40)
        sizes[path] = size

        body = "\n".join(f"value_{i} = {i}" for i in range(size)) + "\n"
        marks += 1
        chunks.append(
            b"blob\nmark :%d\n" % marks
            + f"data {len(body.encode())}\n".encode()
            + body.encode()
            + b"\n"
        )
        blob = marks
        marks += 1
        ts = _epoch(cursor)
        chunks.append(
            b"commit refs/heads/main\nmark :%d\n" % marks
            + f"author {author} <{email}> {ts} +0800\n".encode()
            + f"committer {author} <{email}> {ts} +0800\n".encode()
            + f"data {len(subject.encode())}\n".encode()
            + subject.encode()
            + b"\n"
            + b"M 100644 :%d %s\n\n" % (blob, path.encode())
        )
    return b"".join(chunks)


def _is_fix(shape: str, index: int, total: int, rng: random.Random) -> bool:
    if shape == "fix_heavy":
        return rng.random() < 0.45
    if shape == "delete_heavy":
        # 前一半建，后一半拆 —— 让删除集中在一段，模拟真实重构
        return index > total // 2 and rng.random() < 0.5
    return rng.random() < 0.12
