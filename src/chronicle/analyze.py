"""分析编排。

两个视角共用同一次 Git 遍历：
  project —— 这个仓库经历了什么
  author  —— 这个人在这个仓库里是什么样

渲染层拿到的是同一套 JSON 契约；`--no-ai` 与 LLM 模式的区别只落在
chapters / fun_facts / quote 三个叙事字段上。
"""

from __future__ import annotations

import platform
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .clean import DEFAULT_IGNORE, filter_noise
from .config import LlmSettings, Settings
from .features import (
    churn,
    contributors,
    fun_facts,
    languages,
    milestones,
    overview,
    profile,
    temporal,
)
from .gitio import parse, runner
from .i18n import t
from .narrate import enhance
from .redact import redact_report

__all__ = ["Options", "analyze", "SCHEMA_VERSION", "AuthorLookupError"]

SCHEMA_VERSION = 1

ProgressHook = Callable[[str, str], None]


class AuthorLookupError(LookupError):
    """转发画像模块的查找错误，避免 CLI 直接依赖 features。"""

    def __init__(self, message: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


@dataclass(slots=True)
class Options:
    repo: str | Path = "."
    since: str | None = None
    until: str | None = None
    all_branches: bool = False
    follow_renames: bool = False
    anon: bool = False
    lang: str = "zh"
    tone: str = "tech"
    ignore: list[str] = field(default_factory=list)
    top_files: int = 15
    view: str = "project"
    author: str | None = None
    author_aliases: dict[str, list[str]] = field(default_factory=dict)
    llm: LlmSettings | None = None

    def as_settings(self) -> Settings:
        return Settings(
            lang=self.lang,
            tone=self.tone,
            ignore=list(self.ignore),
            llm=self.llm or LlmSettings(enabled=False),
        )


def _noop(_stage: str, _detail: str) -> None:
    return None


def analyze(options: Options, progress: ProgressHook | None = None) -> dict[str, Any]:
    emit = progress or _noop

    emit("repo", "定位仓库根目录")
    root = runner.resolve_repo(options.repo)
    repo_info = runner.repo_meta(root)

    emit("read", "遍历提交历史")
    raw = list(
        parse.iter_raw_commits(
            root,
            since=options.since,
            until=options.until,
            all_branches=options.all_branches,
            follow_renames=options.follow_renames,
        )
    )

    emit("clean", f"清洗噪音文件（{len(raw)} 条提交）")
    commits = filter_noise(raw, list(DEFAULT_IGNORE) + list(options.ignore))

    if options.view == "author":
        report = _author_report(commits, root, repo_info, options, emit)
    else:
        report = _project_report(commits, root, repo_info, options, emit)

    # 脱敏必须在 LLM 与渲染之前：它同时守住「发出去的报告」和「发出去的 prompt」
    if options.anon:
        primary = (report.get("meta", {}).get("author") or {}).get("email")
        report = redact_report(report, commits, primary_email=primary)

    if options.llm is not None and options.llm.enabled and report.get("chapters"):
        report = enhance(
            report, options.as_settings(), progress=lambda msg: emit("narrate", msg)
        )
    elif options.llm is not None:
        report["meta"]["ai"] = {"used": False, "reason": "disabled"}
    else:
        report["meta"]["ai"] = {"used": False, "reason": "not_requested"}

    return report


# ------------------------------------------------------------------ 项目视角


def _project_report(
    commits: list, root: Path, repo_info: dict, options: Options, emit: ProgressHook
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "view": "project",
        "meta": _build_meta(root, repo_info, options, commits),
        "warnings": [],
    }
    if not commits:
        report.update(_empty_payload(options))
        return report

    emit("overview", "统计全貌指标")
    report["overview"] = overview.build(commits)

    emit("timeline", "聚合时间序列")
    timeline = temporal.build_timeline(commits)
    report["timeline"] = timeline
    report["rhythm"] = temporal.build_rhythm(commits, lang=options.lang)

    emit("churn", "计算文件修罗场评分")
    report["churn"] = churn.build(commits, top_files=options.top_files)
    report["languages"] = languages.build(commits)
    report["contributors"] = contributors.build(commits)

    emit("chapters", "检测突变节点并切分章节")
    bundle = milestones.build_chapters(
        commits, timeline["points"], lang=options.lang, granularity=timeline["granularity"]
    )
    report["chapters"] = bundle["chapters"]
    report["breakpoints"] = bundle["breakpoints"]
    report["milestones"] = milestones.build_events(
        commits, bundle["chapters"], lang=options.lang, limit=12
    )

    emit("facts", "挖考古彩蛋")
    report["fun_facts"] = fun_facts.build_facts(commits, lang=options.lang)
    report["quote"] = fun_facts.build_quote(commits, lang=options.lang, tone=options.tone)

    _append_data_warnings(report, options)
    return report


# ------------------------------------------------------------------ 个人视角


def _author_report(
    commits: list, root: Path, repo_info: dict, options: Options, emit: ProgressHook
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "view": "author",
        "meta": _build_meta(root, repo_info, options, commits),
        "warnings": [],
    }
    if not commits:
        report.update(_empty_payload(options))
        return report

    emit("author", "定位贡献者")
    query = options.author
    if not query:
        counts = Counter((c.email or c.author).lower() for c in commits)
        query = counts.most_common(1)[0][0]
        report["warnings"].append(t("ui.warn.default_author", options.lang, who=query))
    query, aliases = profile.resolve_aliases(query, options.author_aliases)
    try:
        key, name, email, subset = profile.resolve_author(
            commits, query, aliases=aliases, lang=options.lang
        )
    except profile.AuthorLookupError as exc:
        raise AuthorLookupError(str(exc), exc.candidates) from exc

    authors_total = len({(c.email or c.author).lower() for c in commits})
    report["meta"]["author"] = {"key": key, "name": name, "email": email}

    emit("persona", f"统计 {name} 的画像")
    report["persona"] = profile.build_persona(
        subset,
        commits,
        key=key,
        name=name,
        email=email,
        lang=options.lang,
        repo_authors=authors_total,
    )

    emit("timeline", "聚合个人时间序列")
    report["timeline"] = temporal.build_timeline(subset)
    report["rhythm"] = temporal.build_rhythm(subset, lang=options.lang)

    emit("chapters", "对齐项目阶段")
    project_timeline = temporal.build_timeline(commits)
    bundle = milestones.build_chapters(
        commits,
        project_timeline["points"],
        lang=options.lang,
        granularity=project_timeline["granularity"],
    )
    report["chapters"] = profile.chapter_participation(
        bundle["chapters"], subset, lang=options.lang
    )
    report["milestones"] = milestones.build_events(
        subset, bundle["chapters"], lang=options.lang, limit=8
    )

    emit("peers", "计算协作关系")
    report["peers"] = profile.build_peers(commits, key, aliases=aliases)

    emit("facts", "挖个人彩蛋")
    report["fun_facts"] = fun_facts.build_facts(
        subset, lang=options.lang, author_label=name
    )
    report["quote"] = profile.author_quote(
        report["persona"], lang=options.lang, tone=options.tone
    )

    if report["persona"]["span_days"] < 7:
        report["warnings"].append(t("ui.warn.short_span", options.lang))
    if len(subset) < 10:
        report["warnings"].append(t("ui.warn.few_personal", options.lang))
    if report["persona"]["share"] < 30 and authors_total > 1:
        report["warnings"].append(
            t("ui.warn.low_share", options.lang, share=report["persona"]["share"])
        )

    _append_data_warnings(report, options)
    return report


# ------------------------------------------------------------------ 公共


def _append_data_warnings(report: dict, options: Options) -> None:
    lang = options.lang
    block = report.get("overview")
    if block:
        if block["merges"] > block["commits"] * 0.3:
            report["warnings"].append(t("ui.warn.merges", lang))
        if block["bulk_commits"]:
            lines = block["bulk_insertions"] + block["bulk_deletions"]
            report["warnings"].append(
                t("ui.warn.bulk", lang, n=block["bulk_commits"], lines=f"{lines:,}")
            )
        if block["commits"] < 20:
            report["warnings"].append(t("ui.warn.few_commits", lang, n=20))


def _build_meta(
    root: Path, repo_info: dict, options: Options, commits: list
) -> dict[str, Any]:
    ordered = sorted(commits, key=lambda c: c.authored_at) if commits else []
    return {
        "tool": "git-chronicle",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "lang": options.lang,
        "tone": options.tone,
        "view": options.view,
        "repo": repo_info,
        "range": {
            "since": options.since,
            "until": options.until,
            "first_commit": ordered[0].authored_at.isoformat() if ordered else None,
            "last_commit": ordered[-1].authored_at.isoformat() if ordered else None,
        },
        "options": {
            "all_branches": options.all_branches,
            "follow_renames": options.follow_renames,
            "anon": options.anon,
            "no_ai": not (options.llm and options.llm.enabled),
            "ignore": list(options.ignore),
        },
    }


def _empty_payload(options: Options) -> dict[str, Any]:
    return {
        "overview": overview.build([]),
        "timeline": {"granularity": "week", "points": [], "totals": {"commits": 0}},
        "rhythm": temporal.build_rhythm([], lang=options.lang),
        "churn": {"files": [], "dirs": [], "empty": True, "counted_files": 0, "top_score": 0},
        "languages": {"rows": [], "trend": {"keys": [], "series": []}, "empty": True},
        "contributors": {"rows": [], "total_commits": 0, "empty": True},
        "chapters": [],
        "breakpoints": [],
        "milestones": [],
        "fun_facts": [],
        "quote": "",
        "persona": {},
        "peers": [],
        "warnings": [t("empty.no_commits", options.lang)],
    }
