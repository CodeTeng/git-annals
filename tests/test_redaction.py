"""脱敏测试。

这份测试的存在意义不是「覆盖率」，而是**执行一个承诺**：
`--anon` 的产物里，不该出现任何能指回真实人和内部业务的信息。

所以断言方式很粗暴：把真实姓名、邮箱、局部名、域名、仓库名、目录名、文件名、
标题关键词、分支名全部列出来，在渲染后的 HTML 里逐个搜，命中即失败。
新增字段忘了脱敏，这条测试就会响。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chronicle.analyze import Options, analyze
from chronicle.render import build_html
from helpers import git, write

# 每一个都能唯一指回来源，命中任意一个都算泄漏
_SECRETS = (
    "zephyr",
    "quill",
    "acme-internal",
    "acme-billing",
    "acme-secret-project",
    "invoice_renderer",
    "billing",
    "ledger-sync",
)


@pytest.fixture()
def secret_repo(tmp_path: Path) -> Path:
    root = tmp_path / "acme-secret-project"
    root.mkdir()
    git(root, "init", "-q", "-b", "feature/acme-billing-refactor")
    when = "2026-04-01T10:00:00+08:00"

    write(root, "README.md", "# internal\n")
    git(root, "add", ".", when=when)
    git(
        root, "commit", "-q",
        "-m", "feat: add acme billing invoice renderer",
        "--author", "Zephyr Quill <zephyr.quill@acme-internal.com>",
        when=when,
    )

    write(root, "acme_billing/invoice_renderer.py", "x = 1\n")
    git(root, "add", ".", when="2026-04-02T10:00:00+08:00")
    git(
        root, "commit", "-q",
        "-m", "fix: acme billing ledger-sync crash",
        "--author", "Zephyr Quill <zephyr.quill@acme-internal.com>",
        when="2026-04-02T10:00:00+08:00",
    )

    write(root, "src/core.py", "y = 2\n")
    git(root, "add", ".", when="2026-04-09T10:00:00+08:00")
    git(
        root, "commit", "-q",
        "-m", "docs: note about acme billing",
        "--author", "Robin Vale <robin@acme-internal.com>",
        when="2026-04-09T10:00:00+08:00",
    )
    return root


def _assert_clean(payload: str, where: str) -> None:
    lowered = payload.lower()
    hits = [token for token in _SECRETS if token in lowered]
    assert not hits, f"{where} 里仍然出现敏感串：{hits}"


def test_project_report_is_clean(secret_repo: Path) -> None:
    report = analyze(Options(repo=secret_repo, anon=True))
    html = build_html(report)

    _assert_clean(html, "项目报告 HTML")
    _assert_clean(str(report), "项目报告 JSON")


def test_author_report_is_clean(secret_repo: Path) -> None:
    report = analyze(
        Options(repo=secret_repo, anon=True, view="author", author="zephyr.quill@acme-internal.com")
    )
    html = build_html(report)

    _assert_clean(html, "画像 HTML")
    _assert_clean(str(report), "画像 JSON")


def test_redaction_actually_happened(secret_repo: Path) -> None:
    """不能靠「输出为空」通过测试，必须证明确实替换成了别名。"""
    report = analyze(Options(repo=secret_repo, anon=True))

    assert report["meta"]["anon"] is True
    assert report["meta"]["repo"]["name"].startswith("repo-")
    assert report["meta"]["repo"]["root"] == "[redacted]"
    assert report["meta"]["repo"]["remote"] is None
    assert "/" not in report["meta"]["repo"]["branch"].split("/", 1)[1], "分支名后缀必须被哈希"

    names = {row["name"] for row in report["contributors"]["rows"]}
    assert all(name.startswith("开发者") for name in names), names
    assert all(row["email"].endswith("@redacted") for row in report["contributors"]["rows"])

    # 路径保留结构（通用目录名 + 扩展名），但业务段必须换掉
    paths = [row["path"] for row in report["churn"]["files"]]
    assert all(path.endswith(".py") or path.endswith(".md") for path in paths), paths
    assert any(path.startswith("src/") for path in paths), paths


def test_primary_author_takes_letter_a(secret_repo: Path) -> None:
    report = analyze(
        Options(repo=secret_repo, anon=True, view="author", author="zephyr.quill@acme-internal.com")
    )
    assert report["persona"]["name"] == "开发者 A"
    # 自己不能出现在自己的同路人里
    peer_names = {peer["name"] for peer in report["peers"]}
    assert "开发者 A" not in peer_names


def test_non_anon_keeps_real_data(secret_repo: Path) -> None:
    """脱敏是开关，不是默认行为 —— 别把正常报告也一起改了。"""
    report = analyze(Options(repo=secret_repo))
    html = build_html(report)
    assert "Zephyr Quill" in html
    assert report["meta"].get("anon") is not True


def test_anon_keeps_time_keys(secret_repo: Path) -> None:
    """时间轴的 key 是 `2026-W14` 这类周编号，不能被当成邮箱替换掉。

    `_walk` 曾把所有名为 key 的字段都交给 email() 处理，于是 --json 里的
    timeline / breakpoints 全变成 dev-xxxx@redacted。
    """
    report = analyze(Options(repo=secret_repo, anon=True))
    keys = [point["key"] for point in report["timeline"]["points"]]
    assert keys, "时间轴不该为空"
    assert all("@" not in key for key in keys), keys
    for breakpoint in report["breakpoints"]:
        assert "@" not in breakpoint["key"], breakpoint["key"]


def test_branch_keeps_conventional_prefix(secret_repo: Path) -> None:
    report = analyze(Options(repo=secret_repo, anon=True))
    branch = report["meta"]["repo"]["branch"]
    assert branch.startswith("feature/")
    assert branch != "feature/acme-billing-refactor"
