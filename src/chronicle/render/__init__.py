"""单文件 HTML 报告渲染。

产物要求：双击能开、断网能看、发给别人不丢图。
所以样式、图表、数据全部内联，不引任何外链资源。
"""

from __future__ import annotations

import json
from functools import partial
from importlib.resources import files

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from ..i18n import t
from . import charts

__all__ = ["build_html"]

def _kind_labels(lang: str) -> dict[str, str]:
    """提交类型的显示名。词条只存在 i18n 里，脱敏层和渲染层共用一份。"""
    from ..clean import KINDS

    return {kind: t(f"kind.{kind}", lang) for kind in KINDS}

_TAG_LABELS = {
    "zh": {
        "active": "仍在动",
        "brittle": "易碎",
        "silo": "单人维护",
        "volatile": "大起大落",
    },
    "en": {
        "active": "active",
        "brittle": "brittle",
        "silo": "silo",
        "volatile": "volatile",
    },
}


def _environment() -> Environment:
    template_dir = files("chronicle.render") / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["num"] = lambda value: f"{int(value):,}"
    # 0 前面不要挂加减号，「−0」看起来像 bug
    env.filters["signnum"] = lambda value, sign: f"{sign}{int(value):,}" if value else "0"
    return env


def build_html(report: dict) -> str:
    """按视角派发到不同模板。两个模板共用同一份 CSS 令牌。"""
    if report.get("view") == "author":
        return _author_html(report)
    return _project_html(report)


def _project_html(report: dict) -> str:
    env = _environment()
    template = env.get_template("report.html.j2")

    meta = report["meta"]
    lang = meta.get("lang", "zh")
    overview = report["overview"]
    timeline = report["timeline"]
    points = timeline.get("points") or []
    chapters = report.get("chapters") or []
    milestones = report.get("milestones") or []

    return template.render(
        meta=meta,
        overview=overview,
        timeline=timeline,
        rhythm=report["rhythm"],
        churn=report["churn"],
        languages=report["languages"],
        contributors=report["contributors"],
        chapters=chapters,
        milestones=milestones,
        fun_facts=report.get("fun_facts") or [],
        quote=report.get("quote") or "",
        warnings=report.get("warnings") or [],
        events_by_chapter=_group_events(milestones, chapters),
        tag_label=_TAG_LABELS.get(lang, _TAG_LABELS["zh"]),
        svg_timeline=Markup(charts.svg_timeline(points, lang=lang)),
        svg_commit_bars=Markup(charts.svg_commit_bars(points, lang=lang)),
        svg_heatmap=Markup(charts.svg_heatmap(report["rhythm"], lang=lang)),
        svg_share_card=Markup(charts.svg_share_card(report)),
        t=partial(t, lang=lang),
    )


def _author_html(report: dict) -> str:
    env = _environment()
    template = env.get_template("author.html.j2")

    meta = report["meta"]
    lang = meta.get("lang", "zh")
    points = (report.get("timeline") or {}).get("points") or []

    return template.render(
        meta=meta,
        persona=report.get("persona") or {},
        quote=report.get("quote") or "",
        chapters=report.get("chapters") or [],
        peers=report.get("peers") or [],
        fun_facts=report.get("fun_facts") or [],
        warnings=report.get("warnings") or [],
        kind_label=_kind_labels(lang),
        svg_heatmap=Markup(charts.svg_heatmap(report["rhythm"], lang=lang)),
        svg_commit_bars=Markup(charts.svg_commit_bars(points, lang=lang)),
        svg_share_card=Markup(charts.svg_share_card_author(report)),
        t=partial(t, lang=lang),
    )


def _group_events(events: list[dict], chapters: list[dict]) -> dict[str, list[dict]]:
    """把里程碑分配到所属章节。落在章节边界外的丢掉，不硬塞。"""
    grouped: dict[str, list[dict]] = {}
    for event in events:
        for chapter in chapters:
            if chapter["start"] <= event["date"] <= chapter["end"]:
                grouped.setdefault(chapter["id"], []).append(event)
                break
    return grouped


def dump_json(report: dict, path) -> None:
    """中间态导出。ensure_ascii=False 让中文可读，方便直接人肉检查。"""
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
