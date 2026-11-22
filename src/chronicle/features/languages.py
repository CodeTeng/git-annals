"""语言与模块构成，以及占比随时间的漂移。

「语言」在这里指文件扩展名映射出的类别，不是 LOC 精确统计 —— 报告讲的是
演进趋势，不是代码行审计。用增删行数当权重比数文件更贴近真实投入。
"""

from __future__ import annotations

from collections import defaultdict

from ..models import Commit
from ._util import month_key, pct

__all__ = ["build"]

_EXT_LANG = {
    ".py": "Python",
    ".pyi": "Python",
    ".ipynb": "Jupyter",
    ".java": "Java",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".m": "Objective-C",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "CSS",
    ".less": "CSS",
    ".sh": "Shell",
    ".bash": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".tex": "TeX",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".proto": "Protobuf",
    ".tf": "Terraform",
    ".dockerfile": "Docker",
}

# 这些不算「代码」，但也不该丢，单列一个类别让报告能说明构成
_DOC_LANGS = {"Markdown", "reStructuredText", "TeX"}
_CONFIG_LANGS = {"JSON", "YAML", "TOML"}


def _language_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if name.lower() in ("dockerfile", "makefile", "jenkinsfile"):
        return {"dockerfile": "Docker"}.get(name.lower(), "Makefile")
    dot = name.rfind(".")
    if dot <= 0:
        return "Other"
    return _EXT_LANG.get(name[dot:].lower(), "Other")


def build(commits: list[Commit], *, top_langs: int = 6) -> dict:
    if not commits:
        return {"rows": [], "trend": {"keys": [], "series": []}, "empty": True}

    churn: dict[str, int] = defaultdict(int)
    files: dict[str, set[str]] = defaultdict(set)
    by_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for commit in commits:
        month = month_key(commit.authored_at.date())
        for change in commit.changes:
            lang = _language_of(change.path)
            churn[lang] += change.churn
            files[lang].add(change.path)
            by_month[month][lang] += change.churn

    total = sum(churn.values()) or 1
    items = [
        {
            "lang": lang,
            "churn": value,
            "files": len(files[lang]),
            "share": pct(value, total),
            "category": _category(lang),
        }
        for lang, value in churn.items()
    ]
    items.sort(key=lambda r: -r["churn"])
    top = items[:top_langs]

    keys = sorted(by_month)
    series = []
    for lang in [item["lang"] for item in top]:
        values = []
        for month in keys:
            month_total = sum(by_month[month].values()) or 1
            values.append(pct(by_month[month].get(lang, 0), month_total))
        series.append({"lang": lang, "values": values})

    return {
        "rows": top,
        "rest": {
            "churn": sum(item["churn"] for item in items[top_langs:]),
            "share": pct(sum(item["churn"] for item in items[top_langs:]), total),
        },
        "trend": {"keys": keys, "series": series},
        "empty": False,
    }


def _category(lang: str) -> str:
    if lang in _DOC_LANGS:
        return "doc"
    if lang in _CONFIG_LANGS:
        return "config"
    if lang in ("Other",):
        return "other"
    return "code"
