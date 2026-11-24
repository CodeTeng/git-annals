"""语言测试。

英文报告里出现中文，比根本不做英文更难看 —— 用户会认为这工具半成品。
所以这条测试的断言很硬：英文产物里**一个汉字都不许有**（样式和脚本除外）。

数据本身不翻译：commit 信息、作者名、目录名该是什么就是什么。
所以测试用一个全英文的仓库，把「数据」这个变量控制掉。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from chronicle.analyze import Options, analyze
from chronicle.i18n import TEXT
from chronicle.render import build_html
from helpers import git, write

_CJK = re.compile(r"[\u4e00-\u9fff]")
_STYLE = re.compile(r"<style>.*?</style>", re.S)
_SCRIPT = re.compile(r"<script>.*?</script>", re.S)


def _visible(html: str) -> str:
    return _SCRIPT.sub("", _STYLE.sub("", html))


@pytest.fixture()
def english_repo(tmp_path: Path) -> Path:
    root = tmp_path / "sundial"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")

    steps = [
        ("README.md", "# sundial\n", "chore: bootstrap", "2026-01-05T09:12:00+08:00"),
        ("src/parser.py", "x = 1\n", "feat: add parser", "2026-01-06T14:30:00+08:00"),
        ("tests/test_parser.py", "def test():\n    pass\n", "test: cover parser", "2026-01-07T11:05:00+08:00"),
        ("src/parser.py", "x = 2\n", "fix: handle empty input", "2026-01-20T23:40:00+08:00"),
        ("docs/usage.md", "- run it\n", "docs: usage notes", "2026-02-02T10:00:00+08:00"),
    ]
    for path, content, message, when in steps:
        write(root, path, content)
        git(root, "add", ".", when=when)
        git(root, "commit", "-q", "-m", message,
            "--author", "Nora Bell <nora@example.com>", when=when)
    return root


def test_english_project_report_has_no_chinese(english_repo: Path) -> None:
    report = analyze(Options(repo=english_repo, lang="en"))
    html = build_html(report)
    leftovers = sorted(set(_CJK.findall(_visible(html))))
    assert not leftovers, f"英文报告里还有汉字：{leftovers}"
    assert "share-card" in html


def test_english_author_report_has_no_chinese(english_repo: Path) -> None:
    report = analyze(
        Options(repo=english_repo, lang="en", view="author", author="nora@example.com")
    )
    html = build_html(report)
    leftovers = sorted(set(_CJK.findall(_visible(html))))
    assert not leftovers, f"英文画像里还有汉字：{leftovers}"
    assert "share-card" in html


def test_chinese_report_stays_chinese(english_repo: Path) -> None:
    """别为了英文把中文改坏了 —— 默认语言是中文。"""
    report = analyze(Options(repo=english_repo, lang="zh"))
    html = build_html(report)
    assert "编年史" in html
    assert "提交" in html
    assert "文件修罗场" in html


def test_warnings_are_localized(english_repo: Path) -> None:
    """提交少会触发警告，这些句子也必须是英文的。"""
    report = analyze(Options(repo=english_repo, lang="en"))
    assert report["warnings"], "这个仓库应该触发「提交太少」的警告"
    for warning in report["warnings"]:
        assert not _CJK.search(warning), f"警告仍是中文：{warning}"


def test_i18n_key_parity() -> None:
    """两边词条必须一一对应，否则漏翻译会静默回落到中文。"""
    zh, en = set(TEXT["zh"]), set(TEXT["en"])
    assert not (zh - en), f"英文缺失：{sorted(zh - en)[:10]}"
    assert not (en - zh), f"中文缺失：{sorted(en - zh)[:10]}"


def test_weekday_labels_follow_language() -> None:
    from chronicle.i18n import weekdays

    assert weekdays("en")[0] == "Mon"
    assert weekdays("zh")[0] == "周一"


def test_i18n_uses_current_config_filename() -> None:
    """文案里的配置文件名必须和真正被读取的那个是同一个。

    改名后 `.chronicle.toml` 曾在警告里活了六个版本：用户照着提示写配置，
    工具却当它不存在。这类错误不会报错，只能靠一条钉死的断言拦住。
    """
    from chronicle.config import CONFIG_FILENAME
    from chronicle.i18n import LANGS, t

    for lang, texts in TEXT.items():
        for key, value in texts.items():
            assert ".chronicle.toml" not in value, f"{lang}.{key} 仍写着旧配置名"

    for lang in LANGS:
        warning = t("ui.warn.low_share", lang, share="10")
        assert CONFIG_FILENAME in warning, f"{lang} 的合并提示没有指向当前配置文件"
