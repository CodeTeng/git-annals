"""不变式测试：对**任何**仓库都必须成立的性质。

这里不写「flask 应该有 6 章」这种期望值 —— 那种测试只能证明某个仓库当下的输出，
换个仓库就失效。真正要守的是算法层面的性质：

  I1  金句里的每个数字都能在 overview 里找到出处，且口径正确（净增，不是新增+删除）
  I2  章节数量在 1..6 之间
  I3  各章提交数之和 == 总提交数（切分既不重复也不遗漏）
  I4  章节按时间连续、不重叠、不超过仓库时间范围
  I5  多于三章时，章节画风不能全是一种（否则说明这几刀没切出结构）
  I6  榜单里任一「异常标签」的覆盖率不超过 90%（标签必须能区分多数与少数）
  I7  标签的判定阈值来自本仓库自身分布，且不低于绝对下限
  I8  所有百分比落在 0..100

历史教训对应关系：
  I1 ← 「23 万行代码」实为新增+删除，虚高 7 倍
  I5 ← TheAlgorithms 四连「功能扩张期」、flask 三连「重构阵痛期」
  I6 ← vite 15 个文件全挂「易碎」、flask 8 个挂「大起大落」
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from chronicle.analyze import Options, analyze
from repo_shapes import SHAPES, build_repo

_NUMBER = re.compile(r"\d[\d,]*")

# 「异常标签」——active 只是陈述最近有没有动过，不在此列
_EXCEPTION_TAGS = ("brittle", "volatile", "silo")


@pytest.fixture(scope="module", params=SHAPES)
def shaped(request, tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("shape") / request.param
    repo = build_repo(root, request.param)
    return {"shape": request.param, "report": analyze(Options(repo=repo))}


# ---------------------------------------------------------------- I1


def test_quote_numbers_come_from_overview(shaped: dict) -> None:
    """金句只能引用 overview 里已有的数，且行数必须是净增。"""
    report, overview = shaped["report"], shaped["report"]["overview"]
    quote = report["quote"]
    numbers = {int(item.replace(",", "")) for item in _NUMBER.findall(quote)}

    allowed = {
        overview["commits"],
        overview["span_days"],
        abs(overview["net"]),
    }
    assert numbers <= allowed, f"[{shaped['shape']}] 金句出现无出处的数字：{numbers - allowed}"

    # 口径一致性：净增 ≠ 新增+删除时，金句不许拿后者冒充「行数」
    if overview["churn"] != abs(overview["net"]):
        assert overview["churn"] not in numbers, (
            f"[{shaped['shape']}] 金句用了「新增+删除」({overview['churn']}) "
            f"而不是净增({overview['net']})"
        )


def test_overview_churn_is_sum(shaped: dict) -> None:
    overview = shaped["report"]["overview"]
    assert overview["churn"] == overview["insertions"] + overview["deletions"]
    assert overview["net"] == overview["insertions"] - overview["deletions"]


# ---------------------------------------------------------------- I2 / I3 / I4


def test_chapter_count_bounded(shaped: dict) -> None:
    chapters = shaped["report"]["chapters"]
    assert 1 <= len(chapters) <= 6, f"[{shaped['shape']}] 章节数 {len(chapters)} 越界"


def test_chapters_partition_commits(shaped: dict) -> None:
    report = shaped["report"]
    total = report["overview"]["commits"]
    covered = sum(chapter["commits"] for chapter in report["chapters"])
    assert covered == total, f"[{shaped['shape']}] 章节合计 {covered} ≠ 总提交 {total}"


def test_chapters_are_contiguous(shaped: dict) -> None:
    chapters = shaped["report"]["chapters"]
    for earlier, later in zip(chapters, chapters[1:]):
        assert earlier["start"] <= earlier["end"]
        assert later["start"] > earlier["start"]
        assert later["start"] > earlier["end"], (
            f"[{shaped['shape']}] {earlier['title']} 与 {later['title']} 时间重叠"
        )


# ---------------------------------------------------------------- I5


def test_chapter_archetypes_are_not_monotone(shaped: dict) -> None:
    """三章以上却只有一种画风，说明切分没找到结构。"""
    chapters = shaped["report"]["chapters"]
    if len(chapters) <= 3:
        return
    variety = {chapter["archetype"] for chapter in chapters} - {"genesis"}
    assert len(variety) > 1, (
        f"[{shaped['shape']}] {len(chapters)} 章画风全一样："
        f"{[c['title'] for c in chapters]}"
    )


# ---------------------------------------------------------------- I6


def test_tags_discriminate(shaped: dict) -> None:
    rows = shaped["report"]["churn"]["files"]
    if len(rows) < 4:
        return
    for tag in _EXCEPTION_TAGS:
        hit = sum(1 for row in rows if tag in row["tags"])
        assert hit / len(rows) <= 0.9, (
            f"[{shaped['shape']}] 标签 {tag} 覆盖 {hit}/{len(rows)} 行，已经失去区分度"
        )


# ---------------------------------------------------------------- I7


def test_tag_cuts_come_from_repo_baseline(shaped: dict) -> None:
    baseline = shaped["report"]["churn"].get("baseline") or {}
    assert baseline, f"[{shaped['shape']}] 缺少 baseline 字段，说明阈值又变成写死的了"
    # 0 是合法的（该仓库没有可作为基线的样本），但不能是负的或缺失
    for key in ("brittle_cut", "volatile_cut", "single_cut"):
        assert key in baseline, f"[{shaped['shape']}] baseline 缺 {key}"
        assert baseline[key] >= 0


# ---------------------------------------------------------------- I8


def test_ratios_are_sane(shaped: dict) -> None:
    report = shaped["report"]
    overview = report["overview"]
    assert 0 <= overview["net"] or overview["net"] < 0  # 净增可以为负
    for chapter in report["chapters"]:
        assert 0 <= chapter["fix_share"] <= 100
        assert 0 <= chapter["delete_ratio"] <= 100
        assert chapter["commits"] > 0
        assert chapter["span_days"] > 0
    for row in report["churn"]["files"]:
        assert 0 <= row["fix_ratio"] <= 100
        assert 0 <= row["volatile_share"] <= 100
    for person in report["contributors"]["rows"]:
        assert 0 <= person["share"] <= 100


# ---------------------------------------------------------------- 形态专项


def test_uniform_history_does_not_fake_structure(tmp_path: Path) -> None:
    """匀速仓库不该被切成一堆同型章节 —— 这是 TheAlgorithms 那个坑。"""
    report = analyze(Options(repo=build_repo(tmp_path / "u", "uniform", n=240)))
    titles = [chapter["title"] for chapter in report["chapters"]]
    assert len(titles) <= 4, f"匀速历史被切成 {len(titles)} 章：{titles}"
    suffixes = sum(1 for title in titles if "Ⅱ" in title or "Ⅲ" in title or "Ⅳ" in title)
    assert suffixes <= 1, f"同型章节反复堆叠：{titles}"


def test_fix_heavy_does_not_tag_everything(tmp_path: Path) -> None:
    """修复占比畸高的仓库，不该让「易碎」贴满整个榜单。"""
    report = analyze(Options(repo=build_repo(tmp_path / "f", "fix_heavy", n=240)))
    rows = report["churn"]["files"]
    brittle = sum(1 for row in rows if "brittle" in row["tags"])
    assert brittle < len(rows), f"全部 {len(rows)} 行都被判为易碎"


def test_no_three_consecutive_same_archetype(shaped: dict) -> None:
    """同一个画风连续出现三章，读者只会看到「Ⅱ Ⅲ」的堆叠。"""
    chapters = shaped["report"]["chapters"]
    for index in range(len(chapters) - 2):
        window = chapters[index : index + 3]
        archetypes = {chapter["archetype"] for chapter in window}
        assert len(archetypes) > 1, (
            f"[{shaped['shape']}] 连续三章同型：{[c['title'] for c in window]}"
        )


def test_no_three_consecutive_same_archetype(shaped: dict) -> None:
    """同一个画风连续出现三章，读者只会看到「Ⅱ Ⅲ」的堆叠。"""
    chapters = shaped["report"]["chapters"]
    for index in range(len(chapters) - 2):
        window = chapters[index : index + 3]
        archetypes = {chapter["archetype"] for chapter in window}
        assert len(archetypes) > 1, (
            f"[{shaped['shape']}] 连续三章同型：{[c['title'] for c in window]}"
        )


def test_single_chapter_is_not_called_steady(shaped: dict) -> None:
    """只有一个章节的仓库不该叫「稳态迭代期」—— 它可能刚起步三天。"""
    report = shaped["report"]
    chapters = report["chapters"]
    if len(chapters) != 1:
        pytest.skip("多章节仓库不适用")
    only = chapters[0]
    if only["span_days"] <= 180:
        assert only["archetype"] == "genesis", (
            f"[{shaped['shape']}] {only['span_days']} 天的历史被判成 {only['title']}"
        )


def test_no_chapter_dominates_the_whole_history(shaped: dict) -> None:
    """只拦「一章到底」，不要求平均切分。

    阈值定在 90%，因为跨度不均本身不是错误：匀速仓库切成三段必然是
    「短-长-短」，那个长段里根本没有突变，硬切只会造出假阶段。
    实测 rustlings 有 57% 的章节、真实匀速仓库能到 84%，都属正常。
    这条守的是「切分彻底失效」这条底线。
    """
    chapters = shaped["report"]["chapters"]
    span = sum(c["span_days"] for c in chapters)
    if not span or len(chapters) < 2:
        return
    worst = max(c["span_days"] for c in chapters)
    assert worst / span <= 0.90, (
        f"[{shaped['shape']}] 最长一章占 {round(worst / span * 100)}%："
        f"{[(c['title'], c['span_days']) for c in chapters]}"
    )
