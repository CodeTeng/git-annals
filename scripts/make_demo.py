"""生成一个演示仓库，用来做 README 截图与手动验收。

为什么不直接拿真实仓库截图：真实仓库里有业务代号、同事名字、内部路径。
这个脚本生成的 `beacon` 是虚构项目，可以随便公开。

用法：
    python scripts/make_demo.py out/demo/beacon
    chronicle out/demo/beacon --output out/demo/project.html
"""

from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

SEED = 2026

# 提交时刻的分布：上午和下午是主力，凌晨留几个，好让作息热力图有形状
HOUR_WEIGHTS = {
    0: 3, 1: 4, 2: 3, 3: 1, 4: 1, 5: 1,
    9: 4, 10: 6, 11: 5,
    13: 6, 14: 7, 15: 6, 16: 5, 17: 4, 18: 3,
    20: 4, 21: 3, 22: 2, 23: 2,
}
HOURS = list(HOUR_WEIGHTS)
HOUR_W = list(HOUR_WEIGHTS.values())

AUTHORS = [
    ("林澈", "lin.che@example.com"),
    ("周野", "zhou.ye@example.com"),
    ("Ada", "ada@example.com"),
    ("Ken", "ken@example.com"),
]

# 每个阶段：起始日、持续天数、提交密度、主要目录、提交语气
PHASES = [
    ("2025-09-08", 30, 2.8, ["src/core", "src/cli"], "genesis"),
    ("2025-10-08", 75, 1.9, ["src/render", "src/query", "web"], "expansion"),
    ("2025-12-22", 28, 0.12, ["docs", "src/core"], "dormant"),
    # 刻意留出六周空档，让报告能切出「沉寂期 → 二次创业期」
    ("2026-03-05", 45, 2.2, ["src", "src/render", "tests"], "refactor"),
    ("2026-04-20", 55, 1.3, ["tests", "src/core", "docs"], "steady"),
]

# 文件的体量怎么变。真实仓库里扩张期文件在长，重构期在缩 ——
# 如果每版都取随机行数，增删会恒等，章节会全被判成「重构」。
GROWTH = {
    "genesis": (10, 60),
    "expansion": (20, 130),
    "dormant": (-6, 8),
    "refactor": (-0.55, -0.15),   # 相对当前体量的比例
    "steady": (-12, 26),
}

MESSAGES = {
    "genesis": [
        "chore: init repo", "feat: 搭出最小可跑的骨架", "feat: 加上命令行入口",
        "feat: 第一版 markdown 解析", "refactor: 拆出 core 和 cli 两层",
        "fix: 修掉空文件崩溃", "docs: 写第一版 README", "feat: 支持增量构建",
    ],
    "expansion": [
        "feat: 加入查询语法", "feat: 渲染层换成分块缓存", "feat(web): 加上预览面板",
        "fix: 修分页边界", "docs: 补使用示例", "refactor: 合并重复的解析逻辑",
        "feat: 支持自定义主题", "perf: 缓存解析结果", "feat: 导出静态站点",
        "fix: 修复 Windows 路径分隔符", "chore: 升依赖",
    ],
    "dormant": [
        "docs: 补一段说明", "fix: 修一个积压的 issue", "chore: 格式化",
    ],
    "refactor": [
        "refactor: 重写渲染管线", "refactor: 统一错误处理",
        "refactor: 删掉旧的模板层", "test: 补渲染层测试",
        "fix: 修重构引入的回归", "fix: 再修一次回归",
        "refactor: 拆分 2k 行的 core.py", "feat: 恢复被误删的增量构建",
    ],
    "steady": [
        "test: 补边界用例", "docs: 更新 API 说明", "fix: 修时区处理",
        "perf: 减少一次多余遍历", "chore: 整理依赖",
        "feat: 加上 --watch", "fix: 修复 watch 模式内存泄漏",
        "docs: 补常见问题", "真的最后一次改 README", "fix: 上次没说清楚，再补一句",
    ],
}

FILES = {
    "src": ["__init__.py", "types.py", "registry.py", "settings.py"],
    "src/core": ["parser.py", "cache.py", "config.py", "errors.py", "graph.py"],
    "src/cli": ["main.py", "args.py", "output.py"],
    "src/render": ["html.py", "theme.py", "assets.py", "blocks.py"],
    "src/query": ["lexer.py", "eval.py", "index.py"],
    "web": ["app.js", "style.css", "panel.js"],
    "tests": ["test_parser.py", "test_render.py", "test_cache.py", "test_cli.py"],
    "docs": ["usage.md", "design.md", "faq.md", "roadmap.md"],
}


def _content(path: str, size: int) -> str:
    ext = Path(path).suffix
    stem = Path(path).stem.replace("-", "_").replace(".", "_")
    if ext == ".md":
        lines = [f"- 第 {i} 条说明" for i in range(size)]
    elif ext in (".css", ".js"):
        lines = [f".rule-{i} {{ margin: {i}px; }}" for i in range(size)]
    else:
        lines = [f"{stem}_{i} = {i}" for i in range(size)]
    return "\n".join(lines) + "\n"


def _next_size(current: int, tone: str) -> int:
    if current <= 0:
        return random.randint(30, 90)
    low, high = GROWTH[tone]
    if isinstance(low, float):
        return max(20, int(current * random.uniform(1 + low, 1 + high)))
    return max(20, current + random.randint(low, high))


def _content(path: str, size: int) -> str:
    ext = Path(path).suffix
    stem = Path(path).stem.replace("-", "_").replace(".", "_")
    if ext == ".md":
        lines = [f"- 第 {i} 条说明" for i in range(size)]
    elif ext in (".css", ".js"):
        lines = [f".rule-{i} {{ margin: {i}px; }}" for i in range(size)]
    else:
        lines = [f"{stem}_{i} = {i}" for i in range(size)]
    return "\n".join(lines) + "\n"


def _next_size(current: int, tone: str) -> int:
    if current <= 0:
        return random.randint(30, 90)
    low, high = GROWTH[tone]
    if isinstance(low, float):
        return max(20, int(current * random.uniform(1 + low, 1 + high)))
    return max(20, current + random.randint(low, high))


def _commit_stream() -> bytes:
    random.seed(SEED)
    chunks: list[bytes] = []
    marks = 0
    sizes: dict[str, int] = {}
    sizes: dict[str, int] = {}
    for start, days, density, dirs, tone in PHASES:
        y, m, d = (int(part) for part in start.split("-"))
        base = _epoch(y, m, d)
        total = max(2, int(days * density) + random.randint(0, 4))
        for index in range(total):
            day_offset = int(index * days / total)
            hour = random.choices(HOURS, weights=HOUR_W)[0]
            ts = base + day_offset * 86400 + hour * 3600 + random.randint(0, 3599)
            name, email = random.choices(
                AUTHORS, weights=[42, 27, 20, 11]
            )[0]
            message = random.choice(MESSAGES[tone])

            count = random.choices([1, 1, 2, 3], weights=[50, 25, 18, 7])[0]
            touched: list[tuple[str, int]] = []
            for _ in range(count):
                folder = random.choice(dirs)
                path = f"{folder}/{random.choice(FILES[folder])}"
                size = _next_size(sizes.get(path, 0), tone)
                sizes[path] = size
                touched.append((path, size))

            for path, size in touched:
                marks += 1
                body = _content(path, size)
                chunks.append(
                    b"blob\nmark :%d\n" % marks
                    + f"data {len(body.encode())}\n".encode()
                    + body.encode()
                    + b"\n"
                )
                blob_mark = marks
                marks += 1
                chunks.append(
                    b"commit refs/heads/main\nmark :%d\n" % marks
                    + f"author {name} <{email}> {ts} +0800\n".encode()
                    + f"committer {name} <{email}> {ts} +0800\n".encode()
                    + f"data {len(message.encode())}\n".encode()
                    + message.encode()
                    + b"\n"
                    + b"M 100644 :%d %s\n\n" % (blob_mark, path.encode())
                )
    return b"".join(chunks)


def _epoch(year: int, month: int, day: int) -> int:
    from datetime import datetime, timezone

    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "out/demo/beacon")
    if target.exists() and any(target.iterdir()):
        print(f"{target} 已存在且非空，先删掉再来")
        return 1
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=target, check=True)
    subprocess.run(
        ["git", "-c", "user.name=demo", "-c", "user.email=demo@example.com",
         "fast-import", "--quiet"],
        cwd=target,
        input=_commit_stream(),
        check=True,
    )
    subprocess.run(["git", "reset", "-q", "--hard", "main"], cwd=target, check=True)
    count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=target, capture_output=True, text=True, check=True,
    ).stdout.strip()
    print(f"演示仓库已生成：{target}（{count} 次提交）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
