"""命令行入口。

一条命令跑完，中间不问问题。所有参数都有默认值。
`chronicle .` 出项目编年史，`chronicle . --view author` 出个人画像。
"""

from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .analyze import AuthorLookupError, Options, analyze
from .config import load_settings
from .gitio import runner
from .gitio.runner import GitError
from .i18n import t

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

console = Console()

_DEFAULT_OUTPUT = {"project": "annals-report.html", "author": "annals-author.html"}


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"gitannals {__version__}")
        raise typer.Exit()


@app.command()
def main(
    path: str = typer.Argument(".", help="本地 Git 仓库路径，默认当前目录。"),
    view: str = typer.Option(
        "project", "--view", help="视角：project 项目编年史 / author 个人开发者画像。"
    ),
    author: str | None = typer.Option(
        None, "--author", "-a", help="画像对象：姓名或邮箱。多邮箱用逗号合并，如 a@x.com,b@y.com。"
    ),
    tone: str = typer.Option("tech", "--tone", "-t", help="叙事风格：tech 硬核纪实 / roast 毒舌吐槽。"),
    no_ai: bool = typer.Option(False, "--no-ai", help="纯离线统计模式，不调用任何 LLM。"),
    anon: bool = typer.Option(False, "--anon", help="脱敏：邮箱打码、作者名替换。"),
    since: str | None = typer.Option(None, "--since", help="起始时间，如 2025-01-01。"),
    until: str | None = typer.Option(None, "--until", help="结束时间。"),
    output: Path | None = typer.Option(None, "--output", "-o", help="报告输出路径（.html）。"),
    save_json: Path | None = typer.Option(None, "--json", help="额外导出中间态 Chronicle JSON。"),
    open_browser: bool = typer.Option(False, "--open", help="生成完成后自动在浏览器打开。"),
    lang: str = typer.Option("zh", "--lang", help="报告语言：zh / en。"),
    all_branches: bool = typer.Option(False, "--all", help="分析所有分支，而非仅当前 HEAD。"),
    follow_renames: bool = typer.Option(
        False, "--follow-renames", help="开启重命名追踪（更准，但大仓库会明显变慢）。"
    ),
    ignore: list[str] = typer.Option(None, "--ignore", help="额外忽略的路径模式，可重复。"),
    top_files: int = typer.Option(15, "--top-files", help="修罗场榜单长度。"),
    model: str | None = typer.Option(None, "--model", help="LLM 模型名，覆盖配置。"),
    base_url: str | None = typer.Option(None, "--base-url", help="OpenAI 兼容接口地址。"),
    api_key: str | None = typer.Option(None, "--api-key", help="API Key，覆盖环境变量。"),
    temperature: float | None = typer.Option(None, "--temperature", help="采样温度，默认 0.7。"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="只输出结果，不打印过程。"),
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True, help="显示版本号。"
    ),
) -> None:
    """把一段 Git 历史，讲成一个能看的故事。"""
    if view not in ("project", "author"):
        console.print(f"[red]未知的 --view：{view}，可选 project / author[/red]")
        raise typer.Exit(2)
    if tone not in ("tech", "roast"):
        console.print(f"[red]未知的 --tone：{tone}，可选 tech / roast[/red]")
        raise typer.Exit(2)
    if lang not in ("zh", "en"):
        console.print(f"[red]未知的 --lang：{lang}，可选 zh / en[/red]")
        raise typer.Exit(2)

    try:
        root = runner.resolve_repo(path)
    except GitError as exc:
        _report_git_error(exc, lang)
        raise typer.Exit(1) from exc

    settings = load_settings(
        root,
        overrides={
            "lang": lang,
            "tone": tone,
            "ignore": list(ignore or []),
            "model": model,
            "llm_base_url": base_url,
            "api_key": api_key,
            "temperature": temperature,
            "no_ai": no_ai,
        },
    )

    options = Options(
        repo=root,
        since=since,
        until=until,
        all_branches=all_branches,
        follow_renames=follow_renames,
        anon=anon,
        lang=settings.lang,
        tone=settings.tone,
        ignore=settings.ignore,
        top_files=top_files,
        view=view,
        author=author,
        author_aliases=settings.author_aliases,
        llm=settings.llm,
    )

    try:
        if quiet:
            report = analyze(options)
        else:
            with console.status("[dim]读取仓库…[/dim]", spinner="dots") as status:
                report = analyze(
                    options, lambda _stage, detail: status.update(f"[dim]{detail}[/dim]")
                )
    except AuthorLookupError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        for candidate in exc.candidates[:12]:
            console.print(f"   [dim]{candidate}[/dim]")
        raise typer.Exit(2) from exc
    except GitError as exc:
        _report_git_error(exc, lang)
        raise typer.Exit(1) from exc

    is_author = view == "author"
    total = report["persona"]["commits"] if is_author else report["overview"]["commits"]
    if not total:
        console.print(f"[yellow]{report['warnings'][0]}[/yellow]")
        raise typer.Exit(2)

    if save_json:
        save_json.parent.mkdir(parents=True, exist_ok=True)
        save_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not quiet:
            console.print(f"[green]✓[/green] JSON 已写入 {save_json}")

    target = output or Path(_DEFAULT_OUTPUT[view])
    html_written = _try_render(report, target, quiet)

    if not quiet:
        _print_summary(report, is_author)

    if html_written and open_browser:
        webbrowser.open(target.resolve().as_uri())


_GIT_ERROR_KEYS = {
    "empty_repo": "ui.err.no_commits_hint",
}


def _report_git_error(exc: GitError, lang: str) -> None:
    """已知情形给人话，未知情形保留原始 stderr —— 排查时那才是唯一线索。"""
    key = _GIT_ERROR_KEYS.get(getattr(exc, "code", None) or "")
    if key:
        console.print(f"[yellow]⚠[/yellow] {t(key, lang)}")
    else:
        console.print(f"[red]✗ {exc}[/red]")


def _try_render(report: dict, output: Path, quiet: bool) -> bool:
    try:
        from .render import build_html
    except ImportError:
        if not quiet:
            console.print("[yellow]·[/yellow] 渲染层不可用，本次只做分析。用 --json 导出中间结果。")
        return False

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(report), encoding="utf-8")
    if not quiet:
        size_kb = output.stat().st_size / 1024
        console.print(f"[green]✓[/green] 报告已生成 {output} （{size_kb:.0f} KB）")
    return True


def _print_summary(report: dict, is_author: bool) -> None:
    console.print()
    if is_author:
        _print_author_summary(report)
    else:
        _print_project_summary(report)

    ai = (report.get("meta") or {}).get("ai") or {}
    if ai.get("used"):
        console.print(
            f"\n  [dim]叙事由 {ai.get('model')} 生成，耗时 {ai.get('latency_ms')} ms[/dim]"
        )
    elif ai.get("reason") == "no_api_key" and not (report["meta"].get("options") or {}).get("no_ai"):
        console.print(
            "\n  [dim]未配 LLM，使用规则叙事（功能完整）。想启用旁白：export ANNALS_API_KEY=sk-...[/dim]"
        )

    for warning in report.get("warnings") or []:
        console.print(f"\n  [yellow]· {warning}[/yellow]")


def _print_project_summary(report: dict) -> None:
    overview = report["overview"]
    meta = report["meta"]

    title = Text()
    title.append(meta["repo"]["name"] or "repository", style="bold")
    if meta["repo"]["branch"]:
        title.append(f"  {meta['repo']['branch']}", style="dim")

    grid = Table.grid(padding=(0, 3))
    grid.add_column(justify="right", style="dim")
    grid.add_column()
    grid.add_row("提交", f"{overview['commits']:,}")
    grid.add_row(
        "新增 / 删除",
        f"{_signed(overview['insertions'], '+')} / {_signed(overview['deletions'], '-')}",
    )
    grid.add_row("贡献者", f"{overview['authors']}")
    grid.add_row("活跃天数", f"{overview['active_days']} / {overview['span_days']}")
    console.print(Panel(grid, title=title, border_style="dim", padding=(0, 2)))

    chapters = report.get("chapters") or []
    if chapters:
        table = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
        table.add_column("")
        table.add_column("章节")
        table.add_column("区间", style="dim")
        table.add_column("提交", justify="right")
        table.add_column("±行", justify="right")
        for chapter in chapters:
            table.add_row(
                "·",
                chapter["title"],
                f"{chapter['start']} → {chapter['end']}",
                str(chapter["commits"]),
                f"{_signed(chapter['insertions'], '+')}/{_signed(chapter['deletions'], '-')}",
            )
        console.print()
        console.print(table)

    _print_facts(report)
    if report.get("quote"):
        console.print()
        console.print(f"  [italic dim]{report['quote']}[/italic dim]")


def _print_author_summary(report: dict) -> None:
    persona = report["persona"]

    title = Text()
    title.append(persona["name"], style="bold")
    title.append(f"  {persona['email']}", style="dim")

    grid = Table.grid(padding=(0, 3))
    grid.add_column(justify="right", style="dim")
    grid.add_column()
    grid.add_row("提交", f"{persona['commits']:,}（占全仓库 {persona['share']}%）")
    grid.add_row(
        "新增 / 删除",
        f"{_signed(persona['insertions'], '+')} / {_signed(persona['deletions'], '-')}",
    )
    grid.add_row("活跃天数", f"{persona['active_days']} / {persona['span_days']}")
    grid.add_row("标签", "、".join(tag["label"] for tag in persona["tags"]) or "—")
    console.print(Panel(grid, title=title, border_style="dim", padding=(0, 2)))

    console.print()
    console.print(f"  [italic]{persona['verdict']}[/italic]")

    if report.get("chapters"):
        table = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
        table.add_column("阶段")
        table.add_column("区间", style="dim")
        table.add_column("参与", justify="right")
        for phase in report["chapters"]:
            table.add_row(
                phase["title"],
                f"{phase['start']} → {phase['end']}",
                "—" if phase["absent"] else f"{phase['commits']}/{phase['chapter_commits']}",
            )
        console.print()
        console.print(table)

    _print_facts(report)
    if report.get("quote"):
        console.print()
        console.print(f"  [italic dim]{report['quote']}[/italic dim]")


def _signed(value: int, sign: str) -> str:
    """零不带符号：「-0」看起来像 bug。"""
    return f"{sign}{value:,}" if value else "0"


def _print_facts(report: dict) -> None:
    facts = report.get("fun_facts") or []
    if not facts:
        return
    console.print()
    for fact in facts[:3]:
        console.print(f"  [dim]{fact['title']}[/dim]  {fact['detail']}")


if __name__ == "__main__":
    sys.exit(app())
