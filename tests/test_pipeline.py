"""管线端到端测试。

不用 mock：真的建一个 Git 仓库、真的提交、真的跑一遍分析。
统计类工具最怕的就是「测试全绿但数字是错的」，mock 掉 git 就测不到这个。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from chronicle.analyze import AuthorLookupError, Options, analyze
from chronicle.clean import BULK_LINE_THRESHOLD, classify, file_category, is_noise
from chronicle.gitio.parse import normalize_renamed_path
from chronicle.render import build_html
from helpers import git, write


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("bulk") / "demo-repo"
    from helpers import make_repo as _make

    return analyze(Options(repo=_make(root), lang="zh"))


# ---------------------------------------------------------------- 单元


def test_noise_rules() -> None:
    assert is_noise("node_modules/react/index.js")
    assert is_noise("dist/bundle.js")
    assert is_noise("package-lock.json")
    assert is_noise("assets/logo.png")
    assert is_noise("data/train.jsonl")
    assert not is_noise("src/main.py")
    assert not is_noise("docs/readme.md")
    assert is_noise("vendor/x.c")
    assert is_noise(".superpowers/x.md", extra=[".superpowers/"])
    assert not is_noise("src/main.py", extra=["node_modules"])


def test_classify() -> None:
    assert classify("feat(rag): add retriever") == "feat"
    assert classify("fix: handle empty input") == "fix"
    assert classify("修复登录异常") == "fix"
    assert classify("Merge branch 'dev'") == "merge"
    assert classify("Revert \"feat: x\"") == "revert"
    assert classify("refactor: split module") == "refactor"
    assert classify("随便写点什么") == "other"


def test_rename_path_normalisation() -> None:
    assert normalize_renamed_path("a.py => b.py") == "b.py"
    assert normalize_renamed_path("src/{old => new}/x.py") == "src/new/x.py"
    assert normalize_renamed_path("plain.py") == "plain.py"


def test_file_category() -> None:
    assert file_category("src/a.py") == "code"
    assert file_category("README.md") == "doc"
    assert file_category("pyproject.toml") == "config"
    assert file_category(".gitignore") == "config"


# ---------------------------------------------------------------- 端到端


def test_overview_counts(report: dict) -> None:
    overview = report["overview"]
    assert overview["commits"] == 6
    # 2.5 万行的语料必须被剔除，否则这个数字会假到没法看
    assert overview["insertions"] < 200, overview["insertions"]
    assert overview["bulk_commits"] == 1
    # node_modules 不算文件
    touched = {f["path"] for f in report["churn"]["files"]}
    assert not any("node_modules" in p for p in touched)
    assert not any(p.startswith("data/") for p in touched)


def test_bulk_warning(report: dict) -> None:
    assert any("批量导入" in w for w in report["warnings"])


def test_chapters_ordered_and_contiguous(report: dict) -> None:
    chapters = report["chapters"]
    assert chapters
    for earlier, later in zip(chapters, chapters[1:]):
        assert earlier["start"] <= earlier["end"]
        assert later["start"] > earlier["start"]
    assert chapters[0]["start"] <= chapters[-1]["end"]
    assert sum(c["commits"] for c in chapters) <= report["overview"]["commits"]


def test_timeline_has_no_gap(report: dict) -> None:
    points = report["timeline"]["points"]
    gaps = [
        i for i, p in enumerate(points)
        if p["commits"] == 0 and i not in (0, len(points) - 1)
    ]
    assert gaps, "80 天的空档必须在时间序列里留下空桶，否则折线图会骗人"


def test_rhythm_matrix_shape(report: dict) -> None:
    matrix = report["rhythm"]["matrix"]
    assert len(matrix) == 7
    assert all(len(row) == 24 for row in matrix)
    assert sum(sum(row) for row in matrix) == report["overview"]["commits"]


def test_anon_masks_email(repo: Path) -> None:
    """脱敏只有一个入口：邮箱变别名，原文一个字不留。"""
    masked = analyze(Options(repo=repo, anon=True))
    for person in masked["contributors"]["rows"]:
        assert person["email"].endswith("@redacted")
        assert "example.com" not in person["email"]
        assert person["name"].startswith("开发者")


def test_html_is_self_contained(report: dict) -> None:
    html = build_html(report)
    assert "<!DOCTYPE html>" in html
    assert "{{" not in html and "{%" not in html
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in html
    assert 'id="share-card"' in html
    # 金句与章节标题必须出现在页面里
    assert report["quote"][:8] in html


def test_html_escapes_commit_message(tmp_path: Path) -> None:
    """commit message 是不可信输入：塞 </script> 不能破页面。"""
    root = tmp_path / "xss"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "a.py", "print(1)\n")
    git(root, "add", ".", when="2026-02-01T10:00:00+08:00")
    git(
        root, "commit", "-q", "-m", "feat: </script><img src=x onerror=alert(1)>",
        when="2026-02-01T10:00:00+08:00",
    )
    data = analyze(Options(repo=root))
    html = build_html(data)
    assert "</script><img" not in html
    assert "&lt;/script&gt;" in html or "&lt;/script" in html


# ---------------------------------------------------------------- 个人视角


def test_author_profile(report: dict) -> None:
    from helpers import make_repo

    data = analyze(Options(repo=report["meta"]["repo"]["root"], view="author", author="alice"))

    persona = data["persona"]
    assert persona["commits"] == report["overview"]["commits"]
    assert persona["share"] == 100.0
    assert persona["tags"], "标签不能为空"
    assert persona["verdict"]
    assert data["view"] == "author"
    # 阶段参与必须覆盖全部章节
    assert len(data["chapters"]) == len(report["chapters"])


def test_author_missing(report: dict) -> None:
    with pytest.raises(AuthorLookupError):
        analyze(Options(repo=report["meta"]["repo"]["root"], view="author", author="nobody"))


def test_author_anon(report: dict) -> None:
    data = analyze(
        Options(repo=report["meta"]["repo"]["root"], view="author", author="alice", anon=True)
    )
    assert data["persona"]["email"].endswith("@redacted")
    assert "alice@example.com" not in data["persona"]["email"]
    assert "alice" not in str(data).lower()


def test_author_html(report: dict) -> None:
    data = analyze(Options(repo=report["meta"]["repo"]["root"], view="author", author="alice"))
    html = build_html(data)
    assert "{{" not in html and "{%" not in html
    assert 'id="share-card"' in html
    assert "DEVELOPER PROFILE" in html
    assert data["persona"]["name"] in html


# ---------------------------------------------------------------- 个人视角


def test_author_profile(report: dict) -> None:
    from helpers import make_repo

    data = analyze(Options(repo=report["meta"]["repo"]["root"], view="author", author="alice"))

    persona = data["persona"]
    assert persona["commits"] == report["overview"]["commits"]
    assert persona["share"] == 100.0
    assert persona["tags"], "标签不能为空"
    assert persona["verdict"]
    assert data["view"] == "author"
    # 阶段参与必须覆盖全部章节
    assert len(data["chapters"]) == len(report["chapters"])


def test_author_missing(report: dict) -> None:
    with pytest.raises(AuthorLookupError):
        analyze(Options(repo=report["meta"]["repo"]["root"], view="author", author="nobody"))


def test_author_anon(report: dict) -> None:
    data = analyze(
        Options(repo=report["meta"]["repo"]["root"], view="author", author="alice", anon=True)
    )
    assert data["persona"]["email"].endswith("@redacted")
    assert "alice@example.com" not in data["persona"]["email"]
    assert "alice" not in str(data).lower()


def test_author_html(report: dict) -> None:
    data = analyze(Options(repo=report["meta"]["repo"]["root"], view="author", author="alice"))
    html = build_html(data)
    assert "{{" not in html and "{%" not in html
    assert 'id="share-card"' in html
    assert "DEVELOPER PROFILE" in html
    assert data["persona"]["name"] in html


def test_empty_repo_raises_classified_error(tmp_path: Path) -> None:
    """空仓库：不抛原始 git 报错，而是带 code 的 GitError，好让 CLI 说人话。"""
    from chronicle.gitio import runner

    root = tmp_path / "empty"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")

    with pytest.raises(runner.GitError) as excinfo:
        analyze(Options(repo=str(root)))

    assert getattr(excinfo.value, "code", None) == "empty_repo"


def test_binary_only_repo_does_not_claim_first_line(tmp_path: Path) -> None:
    """只有二进制文件时，「写下了第一行」是错的。"""
    root = tmp_path / "bin"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "blob.bin").write_bytes(bytes(range(256)) * 50)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: add binary")

    report = analyze(Options(repo=str(root)))
    origin = next(f for f in report["fun_facts"] if f["id"] == "origin")
    assert "第一行" not in origin["detail"]
    assert "没有文本改动" in origin["detail"]


def test_empty_repo_raises_classified_error(tmp_path: Path) -> None:
    """空仓库：不抛原始 git 报错，而是带 code 的 GitError，好让 CLI 说人话。"""
    from chronicle.gitio import runner

    root = tmp_path / "empty"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")

    with pytest.raises(runner.GitError) as excinfo:
        analyze(Options(repo=str(root)))

    assert getattr(excinfo.value, "code", None) == "empty_repo"


def test_binary_only_repo_does_not_claim_first_line(tmp_path: Path) -> None:
    """只有二进制文件时，「写下了第一行」是错的。"""
    root = tmp_path / "bin"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "blob.bin").write_bytes(bytes(range(256)) * 50)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: add binary")

    report = analyze(Options(repo=str(root)))
    origin = next(f for f in report["fun_facts"] if f["id"] == "origin")
    assert "第一行" not in origin["detail"]
    assert "没有文本改动" in origin["detail"]
