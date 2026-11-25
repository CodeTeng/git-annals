"""管线端到端测试。

不用 mock：真的建一个 Git 仓库、真的提交、真的跑一遍分析。
统计类工具最怕的就是「测试全绿但数字是错的」，mock 掉 git 就测不到这个。
"""

from __future__ import annotations

from datetime import date, timedelta
import subprocess
from pathlib import Path

import pytest

from chronicle.analyze import AuthorLookupError, Options, analyze
from chronicle.clean import BULK_LINE_THRESHOLD, classify, file_category, is_noise
from chronicle.gitio.parse import normalize_renamed_path
from chronicle.render import build_html
from helpers import git, make_repo, write


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
    # output / build 既可能是产物，也常是被 Git 跟踪的源码目录。
    # httpie 的 httpie/output/、vite 的 src/node/build/ 都曾被整段吃掉。
    assert not is_noise("httpie/output/formatters/colors.py")
    assert not is_noise("packages/vite/src/node/build/index.ts")
    assert is_noise("out/report.html")
    assert is_noise("build/artifacts/app.bin")


def test_deepest_night_prefers_midnight() -> None:
    """「最深的夜」要选更接近零点的那次，而不是 1 点。

    旧实现用 (24 - hour) % 24，0 点得 0、1 点得 23，反而把 1 点排在前面。
    """
    from datetime import datetime

    from chronicle.features.fun_facts import build_facts
    from chronicle.models import Commit, FileChange

    def make(sha: str, hour: int, minute: int) -> Commit:
        moment = datetime(2026, 1, 1, hour, minute)
        return Commit(
            sha=sha,
            parents=(),
            author="Alice",
            email="alice@example.com",
            authored_at=moment,
            committed_at=moment,
            subject="feat: night work",
            changes=[FileChange(path="src/a.py", insertions=1, deletions=0)],
        )

    facts = build_facts([make("a" * 8, 0, 15), make("b" * 8, 3, 40)])
    deepest = next(fact for fact in facts if fact["id"] == "deepest_night")
    assert deepest["value"] == "00:15", deepest


def test_single_chapter_reports_real_weekly_rate(tmp_path: Path) -> None:
    """单章仓库的「每周提交数」不能直接等于提交总数。

    页面上的章节卡片就展示这个字段，短历史仓库曾经显示成 20 次/周。
    """
    root = tmp_path / "short"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    base = date(2026, 1, 1)
    for index in range(8):
        write(root, "app.py", "".join(f"line {j}\n" for j in range(index + 1)))
        git(root, "add", ".")
        when = f"{base + timedelta(days=index * 1.5)}T10:00:00+08:00"
        git(root, "commit", "-q", "-m", f"feat: line {index}", when=when)

    report = analyze(Options(repo=root))
    assert len(report["chapters"]) == 1
    only = report["chapters"][0]
    expected = only["commits"] / max(1.0, only["weeks"])
    assert abs(only["commits_per_week"] - expected) < 0.2, only["commits_per_week"]
    assert only["commits_per_week"] != only["commits"]


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


def test_parse_zulu_timestamp() -> None:
    """git 在 UTC 时区输出 Z 结尾，Python 3.10 的 fromisoformat 不认。

    CI runner 与大多数服务器都在 UTC —— 这不是边角情况，是默认路径。
    """
    from chronicle.gitio.parse import _parse_dt

    parsed = _parse_dt("2026-11-23T13:34:11Z")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert parsed.year == 2026 and parsed.hour == 13

    # 常规偏移仍然正常
    assert _parse_dt("2026-11-23T21:34:11+08:00").utcoffset().total_seconds() == 8 * 3600


def test_repo_committed_in_utc(tmp_path: Path) -> None:
    """整条链路：以 UTC 提交的仓库也能分析（CI 上就是这个时区）。"""
    root = tmp_path / "utc"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: utc", when="2026-05-01T12:00:00+00:00")

    report = analyze(Options(repo=str(root)))
    assert report["overview"]["commits"] == 1


def test_parse_zulu_timestamp() -> None:
    """git 在 UTC 时区输出 Z 结尾，Python 3.10 的 fromisoformat 不认。

    CI runner 与大多数服务器都在 UTC —— 这不是边角情况，是默认路径。
    """
    from chronicle.gitio.parse import _parse_dt

    parsed = _parse_dt("2026-11-23T13:34:11Z")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert parsed.year == 2026 and parsed.hour == 13

    # 常规偏移仍然正常
    assert _parse_dt("2026-11-23T21:34:11+08:00").utcoffset().total_seconds() == 8 * 3600


def test_repo_committed_in_utc(tmp_path: Path) -> None:
    """整条链路：以 UTC 提交的仓库也能分析（CI 上就是这个时区）。"""
    root = tmp_path / "utc"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: utc", when="2026-05-01T12:00:00+00:00")

    report = analyze(Options(repo=str(root)))
    assert report["overview"]["commits"] == 1


# ---------------------------------------------------------------- 本轮回归

def test_bare_repository(tmp_path: Path) -> None:
    """bare 仓库没有工作树，show-toplevel 会失败，但 git log 照样能读。

    服务端和 CI 缓存里到处都是 bare clone。
    """
    work = make_repo(tmp_path / "work")
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)

    report = analyze(Options(repo=str(bare)))
    assert report["overview"]["commits"] > 0


def test_overview_counts_add_up(report: dict) -> None:
    """三个数并排出现，读者一定会加总：代码 + merge + 空 = 总数。"""
    o = report["overview"]
    assert o["commits"] == o["commits_code"] + o["merges"] + o["commits_empty"]


def test_contributors_rest_excludes_bots(repo: Path) -> None:
    """榜单只列真人，余量也必须排除机器人，否则「榜单 + 其他人」大于总数。"""
    for day in range(20, 24):
        (repo / "b.py").write_text(f"y = {day}\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "chore: bump",
            "--author", "dependabot[bot] <1+dependabot[bot]@users.noreply.github.com>",
            when=f"2026-02-{day}T09:00:00+08:00")

    data = analyze(Options(repo=str(repo)))
    c = data["contributors"]
    listed = sum(r["commits"] for r in c["rows"]) + c["rest_commits"]
    assert listed == c["total_commits"] - c["bot_commits"]


def test_config_lang_survives_cli_default(tmp_path: Path) -> None:
    """配置文件设了英文，命令行没传 --lang 就应该是英文。

    曾经 CLI 的默认值 "zh" 无条件覆盖 toml —— 配置文件形同虚设。
    """
    from chronicle.config import load_settings

    repo = make_repo(tmp_path / "cfg")
    (repo / ".annals.toml").write_text('[report]\nlang = "en"\ntone = "roast"\n', encoding="utf-8")

    settings = load_settings(repo, overrides={})
    assert settings.lang == "en"
    assert settings.tone == "roast"

    overridden = load_settings(repo, overrides={"lang": "zh"})
    assert overridden.lang == "zh"
    assert overridden.tone == "roast", "没被覆盖的项应保留配置文件的值"


def test_lookup_error_is_one_class() -> None:
    """analyze 与 features 必须抛同一个异常类，否则调用方 except 捕获不到。"""
    import chronicle.analyze as a
    import chronicle.features.profile as p

    assert a.AuthorLookupError is p.AuthorLookupError


def test_toml_fallback_without_tomllib(tmp_path: Path, monkeypatch) -> None:
    """Python 3.10 没有标准库 tomllib，必须回落到 tomli，不能把配置整份丢掉。

    这条在 3.13 上用假的 ModuleNotFoundError 模拟 3.10，CI 的 3.10 job 则是真的。
    """
    import builtins

    from chronicle import config

    root = tmp_path / "cfg"
    root.mkdir()
    (root / ".annals.toml").write_text(
        '[report]\nlang = "en"\ntone = "roast"\n', encoding="utf-8"
    )

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tomllib":
            raise ModuleNotFoundError("No module named 'tomllib'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    settings = config.load_settings(root, overrides={})
    assert settings.lang == "en", "3.10 上配置文件曾被整份忽略"
    assert settings.tone == "roast"


# ---------------------------------------------------------------- 0.1.6 低优先级项


def test_author_dominant_auto_pick(tmp_path: Path) -> None:
    """候选里第一名是第二名的 10 倍以上：自动选第一名并留警告，不再逼用户猜。"""
    repo = make_repo(tmp_path / "dominant")
    for i in range(20):
        (repo / "src/a.py").write_text(f"# v{i}\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"chore: v{i}",
            "--author", "Alex Chen <alex.chen@example.com>",
            when=f"2026-03-{i + 1:02d}T10:00:00+08:00")
    (repo / "src/b.py").write_text("b = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: one",
        "--author", "Alex Wu <alex.wu@example.com>",
        when="2026-03-25T10:00:00+08:00")

    data = analyze(Options(repo=str(repo), view="author", author="alex"))
    assert data["persona"]["name"] == "Alex Chen"
    assert any("自动选" in w for w in data["warnings"]), data["warnings"]

    # 脱敏版：查询词是用户输入的姓名片段，不能原样出现在可外发的报告里
    anon = analyze(
        Options(repo=str(repo), view="author", author="alex", anon=True)
    )
    assert "alex" not in str(anon).lower()
    assert any("自动选" in w for w in anon["warnings"]), anon["warnings"]


def test_author_close_candidates_still_ambiguous(tmp_path: Path) -> None:
    """差距不到 10 倍就必须继续报歧义 —— 自动选择只留给一骑绝尘的情况。"""
    repo = make_repo(tmp_path / "close")
    for i in range(10):
        (repo / "src/a.py").write_text(f"# v{i}\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"chore: v{i}",
            "--author", "Sam A <sam.a@example.com>",
            when=f"2026-03-{i + 1:02d}T10:00:00+08:00")
    for i in range(5):
        (repo / "src/b.py").write_text(f"# w{i}\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"chore: w{i}",
            "--author", "Sam B <sam.b@example.com>",
            when=f"2026-04-{i + 1:02d}T10:00:00+08:00")

    with pytest.raises(AuthorLookupError):
        analyze(Options(repo=str(repo), view="author", author="sam"))


def test_narrow_screen_churn_layout(report: dict) -> None:
    """窄屏：标签列换到下一行、路径占满整行；圆点用悬挂缩进，不跟换行文字挤在一起。"""
    html = build_html(report)
    assert ".crow{grid-template-columns:22px minmax(0,1fr)}" in html
    assert ".crow > div:last-child{grid-column:2}" in html
    assert ".cpath.has-dot{text-indent:-11px;padding-left:11px}" in html
    if any("active" in f["tags"] for f in report["churn"]["files"]):
        assert 'class="cpath has-dot"' in html
