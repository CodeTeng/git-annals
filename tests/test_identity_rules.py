"""身份相关的行为：同名多邮箱、机器人账号。

这两类问题有个共同点 —— **报告看起来完全正常，但数字是错的**。
所以断言不能只看「有没有崩」，要看数字本身。
"""

from __future__ import annotations

from pathlib import Path

from chronicle.analyze import Options, analyze
from chronicle.clean import is_bot
from chronicle.identity import find_name_collisions, name_signature
from chronicle.models import Commit
from helpers import git, make_repo


# ---------------------------------------------------------------- 单元


def test_name_signature_strips_accents() -> None:
    assert name_signature("Jakub Roztočil") == name_signature("Jakub Roztocil")
    assert name_signature("  Jakub   Roztocil ") == "jakub roztocil"
    assert name_signature("") == ""


def test_is_bot() -> None:
    assert is_bot("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
    assert is_bot("allcontributors[bot]", "1+allcontributors[bot]@users.noreply.github.com")
    assert is_bot("renovate", "bot@renovateapp.com")
    assert is_bot("github-actions", "actions@github.com")
    # 真人名字里带 bot 的不该误判
    assert not is_bot("Robot Masters", "robot@example.com")
    assert not is_bot("Teng", "teng@example.com")


def _commit(name: str, email: str, day: int = 1) -> Commit:
    from datetime import datetime, timezone

    moment = datetime(2026, 1, day, 10, tzinfo=timezone.utc)
    return Commit(
        sha=f"{day:040x}",
        parents=(),
        author=name,
        email=email,
        authored_at=moment,
        committed_at=moment,
        subject="feat: x",
        kind="feat",
    )


def test_find_name_collisions() -> None:
    commits = [_commit("Jakub Roztocil", "jakub@a.co", d) for d in range(1, 20)]
    commits += [_commit("Jakub Roztočil", "jakub@b.name", d) for d in range(1, 10)]
    commits += [_commit("Someone Else", "else@x.com", 1)]

    found = find_name_collisions(commits)
    assert len(found) == 1
    assert found[0]["name"] == "Jakub Roztocil"
    assert set(found[0]["emails"]) == {"jakub@a.co", "jakub@b.name"}
    assert found[0]["commits"] == 28


def test_collision_ignores_tiny_groups() -> None:
    """只有一两次提交的同名，多半是重名的不同人，不值得提醒。"""
    assert find_name_collisions([_commit("Wei Zhang", "w1@x.com")]) == []


# ---------------------------------------------------------------- 端到端


def _repo_with_two_emails(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    for day in range(1, 13):
        (root / "a.py").write_text(f"x = {day}\n", encoding="utf-8")
        git(root, "add", "-A")
        email = "me@corp.com" if day <= 7 else "me@personal.dev"
        name = "Solo Dev" if day % 2 else "Solo  Dev"
        git(
            root,
            "commit",
            "-q",
            "-m",
            f"feat: step {day}",
            "--author",
            f"{name} <{email}>",
            when=f"2026-01-{day:02d}T09:00:00+08:00",
        )
    return root


def test_project_warns_about_split_identity(tmp_path: Path) -> None:
    repo = _repo_with_two_emails(tmp_path / "split")
    report = analyze(Options(repo=str(repo)))

    assert report["identity_collisions"], "同名多邮箱必须被检测到"
    joined = " ".join(report["warnings"])
    assert "me@corp.com" in joined and "me@personal.dev" in joined
    assert "--author" in joined, "提示里要给出可直接复制的合并命令"


def test_email_query_merges_detected_identities(tmp_path: Path) -> None:
    """指定一个邮箱时，同一人的其他邮箱也一并算上 —— 画像描述的是「人」，不是邮箱。"""
    repo = _repo_with_two_emails(tmp_path / "split")
    report = analyze(Options(repo=str(repo), view="author", author="me@corp.com"))

    assert report["persona"]["commits"] == 12
    assert any("已自动合并" in w for w in report["warnings"])


def test_author_merged_is_complete(tmp_path: Path) -> None:
    repo = _repo_with_two_emails(tmp_path / "split")
    report = analyze(
        Options(repo=str(repo), view="author", author="me@corp.com,me@personal.dev")
    )

    assert report["persona"]["commits"] == 12
    # 合并之后不该再提示「你只分析了一部分」
    assert not any("只统计了其中一部分" in w for w in report["warnings"])


def test_bots_are_excluded_from_ranking(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "with-bot")
    for day in range(20, 26):
        (repo / "b.py").write_text(f"y = {day}\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(
            repo,
            "commit",
            "-q",
            "-m",
            "chore: bump deps",
            "--author",
            "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
            when=f"2026-02-{day}T09:00:00+08:00",
        )

    report = analyze(Options(repo=str(repo)))
    names = [row["name"] for row in report["contributors"]["rows"]]

    assert not any("bot" in name for name in names), f"榜单里混入了机器人：{names}"
    assert report["overview"]["bots"] == 1
    assert report["overview"]["bot_commits"] == 6
    # 真人数量不该被机器人顶掉
    assert report["overview"]["authors"] == 1


def test_github_login() -> None:
    from chronicle.identity import github_login

    assert github_login("76752051+mo8it@users.noreply.github.com") == "mo8it"
    assert github_login("junegunn@users.noreply.github.com") == "junegunn"
    assert github_login("mo8it@proton.me") is None
    assert github_login("") is None


def test_noreply_matches_real_email(tmp_path: Path) -> None:
    """网页上提交走真邮箱，直接 push 走 noreply —— 同一个人，两个身份。

    这类分裂靠显示名永远发现不了：一边叫「Mo」，一边叫「mo8it」。
    """
    root = tmp_path / "noreply"
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    for day in range(1, 15):
        (root / "a.py").write_text(f"x = {day}\n", encoding="utf-8")
        git(root, "add", "-A")
        email = (
            "76752051+mo8it@users.noreply.github.com" if day <= 6 else "mo8it@proton.me"
        )
        name = "Mo" if day <= 6 else "mo8it"
        git(
            root,
            "commit",
            "-q",
            "-m",
            f"feat: step {day}",
            "--author",
            f"{name} <{email}>",
            when=f"2026-01-{day:02d}T09:00:00+08:00",
        )

    report = analyze(Options(repo=str(root)))
    collisions = report["identity_collisions"]
    assert any(
        "mo8it@proton.me" in c["emails"]
        and "76752051+mo8it@users.noreply.github.com" in c["emails"]
        for c in collisions
    ), f"没识别出 noreply 分裂：{collisions}"


def test_github_login() -> None:
    from chronicle.identity import github_login

    assert github_login("76752051+mo8it@users.noreply.github.com") == "mo8it"
    assert github_login("junegunn@users.noreply.github.com") == "junegunn"
    assert github_login("mo8it@proton.me") is None
    assert github_login("") is None


def test_noreply_matches_real_email(tmp_path: Path) -> None:
    """网页上提交走真邮箱，直接 push 走 noreply —— 同一个人，两个身份。

    这类分裂靠显示名永远发现不了：一边叫「Mo」，一边叫「mo8it」。
    """
    root = tmp_path / "noreply"
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    for day in range(1, 15):
        (root / "a.py").write_text(f"x = {day}\n", encoding="utf-8")
        git(root, "add", "-A")
        email = (
            "76752051+mo8it@users.noreply.github.com" if day <= 6 else "mo8it@proton.me"
        )
        name = "Mo" if day <= 6 else "mo8it"
        git(
            root,
            "commit",
            "-q",
            "-m",
            f"feat: step {day}",
            "--author",
            f"{name} <{email}>",
            when=f"2026-01-{day:02d}T09:00:00+08:00",
        )

    report = analyze(Options(repo=str(root)))
    collisions = report["identity_collisions"]
    assert any(
        "mo8it@proton.me" in c["emails"]
        and "76752051+mo8it@users.noreply.github.com" in c["emails"]
        for c in collisions
    ), f"没识别出 noreply 分裂：{collisions}"


def test_bare_name_merges_same_display_name(tmp_path: Path) -> None:
    """`--author "Solo Dev"` 命中两个邮箱，但显示名相同 —— 直接合并。

    这是「漏合并」最常见的场景：用户不知道要写两个邮箱。
    """
    repo = _repo_with_two_emails(tmp_path / "auto")
    report = analyze(Options(repo=str(repo), view="author", author="Solo Dev"))

    assert report["persona"]["commits"] == 12, "同名多邮箱应当自动合并"
    assert any("已自动合并" in w for w in report["warnings"])


def test_different_names_are_not_merged(tmp_path: Path) -> None:
    """名字不同的两个人不该被合并，哪怕都提交过。"""
    root = tmp_path / "two"
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    for day in range(1, 9):
        (root / "a.py").write_text(f"x = {day}\n", encoding="utf-8")
        git(root, "add", "-A")
        who = "Alice Chen <alice@x.com>" if day <= 4 else "Bob Li <bob@x.com>"
        git(root, "commit", "-q", "-m", f"feat: {day}", "--author", who,
            when=f"2026-03-{day:02d}T09:00:00+08:00")

    report = analyze(Options(repo=str(root), view="author", author="alice@x.com"))
    assert report["persona"]["commits"] == 4


def test_root_dir_is_labelled(tmp_path: Path) -> None:
    """根目录文件不该显示成 `(root)`。"""
    repo = make_repo(tmp_path / "rootlabel")
    report = analyze(Options(repo=str(repo)))
    names = [d["name"] for d in report["churn"]["dirs"]]
    assert "(root)" not in names
    assert any(name in ("根目录", "root") for name in names), names

    english = analyze(Options(repo=str(repo), lang="en"))
    assert "(root)" in [d["name"] for d in english["churn"]["dirs"]]


def test_active_tag_renders_as_dot(tmp_path: Path) -> None:
    """「仍在动」只用一个圆点表示，不占标签位 —— 否则 15 行里 14 行都在重复。"""
    from chronicle.render import build_html
    from helpers import make_repo as build

    repo = build(tmp_path / "dot")
    report = analyze(Options(repo=str(repo)))
    html = build_html(report)

    tags_rendered = [t for t in ("active",) if f'class="ctag {t}"' in html]
    assert not tags_rendered, "active 不该作为标签出现"
    if any("active" in f["tags"] for f in report["churn"]["files"]):
        assert "cdot" in html


def test_bare_name_merges_same_display_name(tmp_path: Path) -> None:
    """`--author "Solo Dev"` 命中两个邮箱，但显示名相同 —— 直接合并。

    这是「漏合并」最常见的场景：用户不知道要写两个邮箱。
    """
    repo = _repo_with_two_emails(tmp_path / "auto")
    report = analyze(Options(repo=str(repo), view="author", author="Solo Dev"))

    assert report["persona"]["commits"] == 12, "同名多邮箱应当自动合并"
    assert any("已自动合并" in w for w in report["warnings"])


def test_different_names_are_not_merged(tmp_path: Path) -> None:
    """名字不同的两个人不该被合并，哪怕都提交过。"""
    root = tmp_path / "two"
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    for day in range(1, 9):
        (root / "a.py").write_text(f"x = {day}\n", encoding="utf-8")
        git(root, "add", "-A")
        who = "Alice Chen <alice@x.com>" if day <= 4 else "Bob Li <bob@x.com>"
        git(root, "commit", "-q", "-m", f"feat: {day}", "--author", who,
            when=f"2026-03-{day:02d}T09:00:00+08:00")

    report = analyze(Options(repo=str(root), view="author", author="alice@x.com"))
    assert report["persona"]["commits"] == 4


def test_root_dir_is_labelled(tmp_path: Path) -> None:
    """根目录文件不该显示成 `(root)`。"""
    repo = make_repo(tmp_path / "rootlabel")
    report = analyze(Options(repo=str(repo)))
    names = [d["name"] for d in report["churn"]["dirs"]]
    assert "(root)" not in names
    assert any(name in ("根目录", "root") for name in names), names

    english = analyze(Options(repo=str(repo), lang="en"))
    assert "(root)" in [d["name"] for d in english["churn"]["dirs"]]


def test_active_tag_renders_as_dot(tmp_path: Path) -> None:
    """「仍在动」只用一个圆点表示，不占标签位 —— 否则 15 行里 14 行都在重复。"""
    from chronicle.render import build_html
    from helpers import make_repo as build

    repo = build(tmp_path / "dot")
    report = analyze(Options(repo=str(repo)))
    html = build_html(report)

    tags_rendered = [t for t in ("active",) if f'class="ctag {t}"' in html]
    assert not tags_rendered, "active 不该作为标签出现"
    if any("active" in f["tags"] for f in report["churn"]["files"]):
        assert "cdot" in html
