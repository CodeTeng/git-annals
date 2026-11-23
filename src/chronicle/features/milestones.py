"""里程碑检测与章节切分。

关键取舍：章节边界**不交给 LLM**。
在「提交频次 + 代码波动」两条序列上跑滑窗突变检测，得到候选断点，再按
最小间隔贪心挑选。LLM 只负责给已切好的段起名字、写旁白 —— 这样同样的仓库
跑两次结果一致，评审、复盘、CI 都能复现。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime

from ..i18n import chapter_archetypes, t
from ..models import Commit
from ._baseline import quantile, ratio, share
from ._util import mean, pct, round2, std

__all__ = ["build_chapters", "detect_breakpoints", "build_events"]

_MIN_SEGMENT = 3          # 每章至少 3 个时间桶
# 「时间型」章节按停顿的长度定义，不是按节奏定义。
# 「隔了三年才回来」这种章节越长越说明问题，切它没有意义；
# 「三年持续扩张」才该按节奏切成几段。
_TIME_SHAPED = frozenset({"silence", "renaissance"})

# 「时间型」章节按停顿的长度定义，不是按节奏定义。
# 「隔了三年才回来」这种章节越长越说明问题，切它没有意义；
# 「三年持续扩张」才该按节奏切成几段。
_TIME_SHAPED = frozenset({"silence", "renaissance"})

_MAX_CHAPTERS = 8          # 兜底上限，实际章数由跨度决定
_MAX_CHAPTER_SHARE = 0.40  # 单章不超过总跨度的 40%
_GAP_DAYS = 21            # 超过三周没提交，视为一次「沉默」
_DEP_FILES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "requirements-dev.txt",
        "Pipfile",
        "poetry.lock",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "composer.json",
        "Gemfile",
        "Dockerfile",
        "docker-compose.yml",
        "environment.yml",
    }
)


def _change_scores(values: list[float], window: int) -> list[float]:
    """滑窗均值差 / 全窗标准差。值越大，说明这个位置前后「画风」变化越剧烈。"""
    n = len(values)
    scores = [0.0] * n
    if n < _MIN_SEGMENT * 2:
        return scores
    for i in range(n):
        before = values[max(0, i - window) : i]
        after = values[i : min(n, i + window)]
        if len(before) < 2 or len(after) < 2:
            continue
        spread = std(values[max(0, i - window) : min(n, i + window)])
        scores[i] = abs(mean(after) - mean(before)) / (spread + 1e-9)
    return scores


def detect_breakpoints(points: list[dict], max_breaks: int) -> list[dict]:
    """在时间桶序列上找候选断点，返回按 index 升序的断点列表。"""
    n = len(points)
    if n < _MIN_SEGMENT * 2 or max_breaks <= 0:
        return []

    window = max(2, min(6, n // 4))
    commits = [float(p["commits"]) for p in points]
    churn = [float(p["churn"]) for p in points]

    sc = _change_scores(commits, window)
    sk = _change_scores(churn, window)
    combined = [0.6 * a + 0.4 * b for a, b in zip(sc, sk)]

    candidates = [
        (i, score)
        for i, score in enumerate(combined)
        if _MIN_SEGMENT <= i <= n - _MIN_SEGMENT and score > 0
    ]
    candidates.sort(key=lambda pair: -pair[1])

    chosen: list[int] = []
    for index, _score in candidates:
        if len(chosen) >= max_breaks:
            break
        if all(abs(index - other) >= _MIN_SEGMENT for other in chosen):
            chosen.append(index)

    chosen.sort()
    return [
        {
            "index": index,
            "key": points[index]["key"],
            "date": points[index]["start"],
            "score": round2(combined[index]),
        }
        for index in chosen
    ]


def build_chapters(
    commits: list[Commit],
    points: list[dict],
    *,
    lang: str = "zh",
    granularity: str = "week",
) -> dict:
    if not commits:
        return {"chapters": [], "breakpoints": [], "empty_reason": "no_commits"}

    if len(points) < _MIN_SEGMENT * 2:
        return {
            "chapters": [_single_chapter(commits, points, lang, granularity)],
            "breakpoints": [],
            "flat": True,
        }

    baseline = _repo_baseline(commits)

    # 长历史先在粗粒度上找断点：一万多周里用 6 周滑窗，等于拿放大镜看地图，
    # 抓到的全是噪声。检测用粗粒度，渲染仍用原始 points。
    coarse, mapper = _coarsen(points)
    # 章数跟着历史的长度走：三个月的仓库切六章是硬凑，十年仓库切三章是偷懒。
    horizon = (
        date.fromisoformat(points[-1]["end"]) - date.fromisoformat(points[0]["start"])
    ).days + 1
    target = _target_chapters(horizon)
    max_breaks = min(target - 1, max(1, len(coarse) // 8))
    raw_breaks = detect_breakpoints(coarse, max_breaks)
    breakpoints = [
        {"index": mapper[bp["index"]], "key": bp["key"], "date": bp["date"], "score": bp["score"]}
        for bp in raw_breaks
        if 0 <= bp["index"] < len(mapper)
    ]

    bounds = [0] + [bp["index"] for bp in breakpoints] + [len(points)]
    segments = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]

    # 相邻段落信号太像就合并。不为「凑满六章」硬切 ——
    # 一个十五年匀速前进的仓库，正常答案是三章，不是六章。
    #
    # 还要防另一种失败：切出来的段落画风全一样。那说明这几刀没切出结构，
    # 只是在一条直线上画刻度 —— 逐级放宽容差重切，直到出现差异或只剩三章。
    chapters: list[dict] = []
    for tolerance in (0.35, 0.55, 0.75, 1.0):
        segments = _merge_homogeneous(segments, points, baseline, tolerance)
        chapters = _build_chapters_from(segments, commits, points, lang, granularity, baseline)
        # genesis 由位置决定（第一章必然是它），不参与「画风是否单调」的判断
        variety = {chapter["archetype"] for chapter in chapters} - {"genesis"}
        if len(variety) > 1 or len(chapters) <= 3:
            break

    # 还有一类章节：检测器在那一段里没找到突变（缓坡），于是一章跨了六年。
    # 这不是检测失败 —— 那六年确实没有突变 —— 但读者拿不到任何信息。
    # 按跨度比例强制切开，只在内部找最强的那一个断点。
    # 切分要有预算：章数已经够了还硬切，会切出「扩张 / 打磨 / 扩张 / 打磨」
    # 这种来回横跳的章节 —— 它比一个大章更难读。
    # 但「一章吃掉 79% 历史」是另一种病，跟章数够不够无关，得单独给额度。
    cap_days = horizon * _MAX_CHAPTER_SHARE
    over_limit = sum(1 for seg in segments if _span(seg, points) > cap_days)
    budget = max(over_limit, target - len(segments))
    if budget:
        segments = _split_long(
            segments, points, horizon * _MAX_CHAPTER_SHARE, budget=budget
        )
    chapters = _build_chapters_from(segments, commits, points, lang, granularity, baseline)

    # 同一种画风连续超过两章，就把后两章并掉。
    # 「功能扩张期 Ⅲ」这种第 III 次重复，读者一眼就看出是模板在硬撑。
    cap_days = horizon * _MAX_CHAPTER_SHARE
    for _ in range(_MAX_CHAPTERS):
        run_start = _long_run(chapters, points, segments, cap_days)
        if run_start is None:
            break
        segments = (
            segments[:run_start]
            + [(segments[run_start][0], segments[run_start + 1][1])]
            + segments[run_start + 2 :]
        )
        chapters = _build_chapters_from(segments, commits, points, lang, granularity, baseline)

    # 「扩张 → 打磨 → 扩张」这种交替**不合并**。实测合并会把结构一起抹平：
    # fzf 从 5 章压成 3 章，两章各占 1700 天，读者反而什么都看不到。
    # 交替是这类项目的真实节奏 —— 一轮大改之后修一阵，再改一轮。

    merged = _merge_tiny(chapters)
    _dedupe_titles(merged)

    return {"chapters": merged, "breakpoints": breakpoints, "flat": len(merged) <= 1}


def _long_run(
    chapters: list[dict],
    points: list[dict] | None = None,
    segments: list[tuple[int, int]] | None = None,
    cap_days: float | None = None,
    limit: int = 2,
) -> int | None:
    """返回第一处「同一原型连续超过 limit 章」的起始下标。

    合并会让章节变长。如果并完会突破跨度上限，这一处就跳过 ——
    跨度是硬约束，宁可让「功能扩张期 Ⅱ Ⅲ」并排出现，
    也不要留下一个吃掉 87% 历史的章节。
    """
    for index in range(len(chapters) - limit):
        window = chapters[index : index + limit + 1]
        if len({chapter["archetype"] for chapter in window}) != 1:
            continue
        if points and segments and cap_days:
            if window[0]["archetype"] not in _TIME_SHAPED:
                merged = (segments[index + 1][0], segments[index + limit][1])
                if _span(merged, points) > cap_days:
                    continue
        return index + 1
    return None


def _build_chapters_from(
    segments: list[tuple[int, int]],
    commits: list[Commit],
    points: list[dict],
    lang: str,
    granularity: str,
    baseline: dict,
) -> list[dict]:
    chapters: list[dict] = []
    prev_end: date | None = None
    for order, (start_idx, end_idx) in enumerate(segments):
        chapter = _build_chapter(
            commits,
            points[start_idx:end_idx],
            order=order,
            lang=lang,
            granularity=granularity,
            prev_end=prev_end,
            baseline=baseline,
        )
        if chapter is None:
            continue
        chapters.append(chapter)
        prev_end = date.fromisoformat(chapter["end"])
    return chapters


def _span(segment: tuple[int, int], points: list[dict]) -> int:
    left = date.fromisoformat(points[segment[0]]["start"])
    right = date.fromisoformat(points[segment[1] - 1]["end"])
    return (right - left).days + 1


def _split_long(
    segments: list[tuple[int, int]],
    points: list[dict],
    cap: int,
    *,
    budget: int = 99,
    depth: int = 0,
) -> list[tuple[int, int]]:
    """把超过 cap 的段落按内部最强的断点切开。budget 是这一轮最多切几刀。"""
    if depth >= 2 or budget <= 0:
        return segments
    # 先切最长的那一段，避免预算被小段落吃掉
    order = sorted(
        range(len(segments)), key=lambda i: -_span(segments[i], points)
    )
    out = list(segments)
    for index in order:
        start, end = out[index]
        if _span((start, end), points) <= cap or end - start < _MIN_SEGMENT * 2:
            continue
        window = points[start:end]
        breaks = detect_breakpoints(window, 1)
        span = _span((start, end), points)
        if breaks:
            cut = start + breaks[0]["index"]
        elif span > cap * 2:
            # 内部一点突变都没有，但这一段已经是半部历史了。
            # 按时间中点切：人造边界，但比「五年前面的所有事都叫同一章」诚实。
            cut = start + (end - start) // 2
        else:
            continue
        if cut <= start + _MIN_SEGMENT - 1 or cut >= end - _MIN_SEGMENT + 1:
            continue
        # 切开之后两侧必须真的不一样。匀速仓库在这里会被切出一堆假阶段 ——
        # 长章节的代价是「信息少」，虚假阶段的代价是「信息错」，后者更贵。
        # 但超限一倍以上的段落例外：它已经不是「一章」了，是半部历史。
        if span <= cap * 2 and _similar(
            _segment_signals(points, start, cut),
            _segment_signals(points, cut, end),
            0.35,
        ):
            continue
        out[index : index + 1] = [(start, cut), (cut, end)]
        budget -= 1
        if budget <= 0:
            break
    return out


def _target_chapters(horizon_days: int) -> int:
    """目标章数。宁可少切，也不要为了凑数把一条直线剁成六段。"""
    if horizon_days < 120:
        return 1
    if horizon_days < 400:
        return 3
    if horizon_days < 1100:
        return 4
    if horizon_days < 2200:
        return 5
    return 6


def _repo_baseline(commits: list[Commit]) -> dict:
    """本仓库自己的常态。所有章节判定都相对它，不写死常数。"""
    insertions = sum(c.insertions for c in commits)
    deletions = sum(c.deletions for c in commits)
    total = insertions + deletions
    fixes = sum(1 for c in commits if c.kind in ("fix", "revert"))
    return {
        "del_ratio": share(deletions, total),
        "fix_share": share(fixes, len(commits)) * 100,
        "churn_per_commit": share(total, len(commits)),
        "commits": len(commits),
    }


def _coarsen(points: list[dict], target: int = 120) -> tuple[list[dict], list[int]]:
    """把过长的序列按比例聚合成 target 个桶，同时返回每个粗桶对应的原始下标。"""
    n = len(points)
    if n <= target * 1.5:
        return points, list(range(n))

    step = n / target
    coarse: list[dict] = []
    mapper: list[int] = []
    for bucket in range(target):
        start = int(bucket * step)
        end = min(n, int((bucket + 1) * step))
        if start >= end:
            continue
        window = points[start:end]
        coarse.append(
            {
                "key": window[0]["key"],
                "label": window[0]["label"],
                "start": window[0]["start"],
                "end": window[-1]["end"],
                "commits": sum(p["commits"] for p in window),
                "insertions": sum(p["insertions"] for p in window),
                "deletions": sum(p["deletions"] for p in window),
                "churn": sum(p["churn"] for p in window),
            }
        )
        mapper.append(start)
    return coarse, mapper


def _segment_signals(points: list[dict], start: int, end: int) -> dict:
    window = points[start:end]
    if not window:
        return {"per_week": 0.0, "churn_per_commit": 0.0, "del_ratio": 0.0, "days": 0}
    commits = sum(p["commits"] for p in window)
    churn = sum(p["churn"] for p in window)
    deletions = sum(p["deletions"] for p in window)
    days = (date.fromisoformat(window[-1]["end"]) - date.fromisoformat(window[0]["start"])).days + 1
    return {
        "per_week": commits / max(1.0, days / 7),
        "churn_per_commit": share(churn, commits),
        "del_ratio": share(deletions, churn),
        "days": days,
    }


def _similar(a: dict, b: dict, tolerance: float = 0.35) -> bool:
    """两个阶段的「画风」是否接近。三个信号都在容差内，就认为不该拆开。"""
    for key in ("per_week", "churn_per_commit", "del_ratio"):
        left, right = a[key], b[key]
        scale = max(left, right, 1e-9)
        if abs(left - right) / scale > tolerance:
            return False
    return True


def _merge_homogeneous(
    segments: list[tuple[int, int]],
    points: list[dict],
    baseline: dict,
    tolerance: float = 0.35,
) -> list[tuple[int, int]]:
    """从最相似的一对开始合并，直到没有可合并的相邻段落。

    合并有两条刹车，缺一条都会出问题：
    - 画风要像（tolerance），否则「救火期」和「打磨期」会被混为一谈；
    - 合并后不能太长（_MAX_CHAPTER_SHARE），否则一段缓坡会把两边一起吞掉，
      最后留下「2017~2024，功能扩张期」这种没有信息量的章节。
    """
    segments = list(segments)
    if not points:
        return segments
    horizon = (date.fromisoformat(points[-1]["end"]) - date.fromisoformat(points[0]["start"])).days + 1
    cap = horizon * _MAX_CHAPTER_SHARE

    while len(segments) > 1:
        signals = [_segment_signals(points, start, end) for start, end in segments]
        best_index, best_gap = -1, None
        for index in range(len(segments) - 1):
            if not _similar(signals[index], signals[index + 1], tolerance):
                continue
            left = date.fromisoformat(points[segments[index][0]]["start"])
            right = date.fromisoformat(points[segments[index + 1][1] - 1]["end"])
            if (right - left).days + 1 > cap:
                continue
            gap = abs(signals[index]["per_week"] - signals[index + 1]["per_week"])
            if best_gap is None or gap < best_gap:
                best_index, best_gap = index, gap
        if best_index < 0:
            break
        merged = (segments[best_index][0], segments[best_index + 1][1])
        segments[best_index : best_index + 2] = [merged]
    return segments


def _dedupe_titles(chapters: list[dict]) -> None:
    """同名章节加时间后缀。

    原来加罗马数字（「功能扩张期 Ⅱ Ⅲ Ⅳ」），但四连的 Ⅱ Ⅲ Ⅳ 只是告诉读者
    「又重复了一次」，年份才说得清是哪一段。vite 的四个扩张期在 2021~2026，
    写成「功能扩张期 · 2021」比「Ⅳ」有用得多。
    """
    roman = ["", " Ⅱ", " Ⅲ", " Ⅳ", " Ⅴ", " Ⅵ", " Ⅶ"]
    seen: Counter[str] = Counter()
    used: set[str] = set()
    for chapter in chapters:
        archetype = chapter["archetype"]
        seen[archetype] += 1
        if seen[archetype] == 1:
            used.add(chapter["title"])
            continue
        year = chapter["start"][:4]
        candidate = f"{chapter['title']} · {year}"
        if candidate in used:
            candidate = f"{chapter['title']} · {chapter['start'][:7]}"
        if candidate in used:
            candidate = f"{chapter['title']} · {chapter['start'][:7]}"
        if candidate in used:
            candidate = chapter["title"] + roman[min(seen[archetype] - 1, len(roman) - 1)]
        chapter["title"] = candidate
        used.add(candidate)


def _chapter_commits(commits: list[Commit], start: str, end: str) -> list[Commit]:
    lo, hi = date.fromisoformat(start), date.fromisoformat(end)
    return [c for c in commits if lo <= c.authored_at.date() <= hi]


def _build_chapter(
    commits: list[Commit],
    chunk: list[dict],
    *,
    order: int,
    lang: str,
    granularity: str,
    prev_end: date | None,
    baseline: dict,
) -> dict | None:
    if not chunk:
        return None
    bucket_start, bucket_end = chunk[0]["start"], chunk[-1]["end"]
    members = _chapter_commits(commits, bucket_start, bucket_end)
    if not members:
        return None

    # 章节的起止用**真实提交日期**，不是时间桶的边界。
    # 否则 `--since 2025-01-01` 会显示「本章从 2024-12-30 开始」——
    # 那是那一周的周一，不是这个仓库的历史。
    # 注意 members 是 git log 的原序（最新在前），必须显式取最值。
    start = min(c.authored_at for c in members).date().isoformat()
    end = max(c.authored_at for c in members).date().isoformat()

    insertions = sum(c.insertions for c in members)
    deletions = sum(c.deletions for c in members)
    authors = {c.email.lower() for c in members}
    span_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    weeks = max(1.0, span_days / 7)
    per_week = round2(len(members) / weeks)
    fix_count = sum(1 for c in members if c.kind in ("fix", "revert"))
    fix_share = pct(fix_count, len(members))
    total_lines = insertions + deletions
    del_ratio = (deletions / total_lines) if total_lines else 0.0
    gap_before = (date.fromisoformat(start) - prev_end).days if prev_end else 0

    churn_per_commit = total_lines / len(members) if members else 0.0

    dirs = _top_dirs(members, limit=3)
    archetype = _classify(
        order=order,
        per_week=per_week,
        del_ratio=del_ratio,
        fix_share=fix_share,
        gap_before=gap_before,
        span_days=span_days,
        churn_per_commit=churn_per_commit,
        baseline=baseline,
    )

    names = chapter_archetypes(lang)
    summary = t(
        "chapter.summary",
        lang,
        commits=len(members),
        insertions=f"{insertions:,}",
        deletions=f"{deletions:,}",
        authors=len(authors),
    )
    if dirs:
        summary += t("chapter.summary_dirs", lang, dirs="、".join(dirs))

    return {
        "id": f"ch{order + 1:02d}",
        "archetype": archetype,
        "title": names.get(archetype, archetype),
        "start": start,
        "end": end,
        "span_days": span_days,
        "weeks": round2(weeks),
        "commits": len(members),
        "insertions": insertions,
        "deletions": deletions,
        "net": insertions - deletions,
        "authors": len(authors),
        "commits_per_week": per_week,
        "fix_share": fix_share,
        "churn_per_commit": round2(churn_per_commit),
        "delete_ratio": pct(deletions, total_lines),
        "gap_before_days": gap_before,
        "top_dirs": dirs,
        "granularity": granularity,
        "summary": summary,
        "narrative": _narrative(
            archetype=archetype,
            lang=lang,
            start=start,
            per_week=per_week,
            insertions=insertions,
            deletions=deletions,
            commits=len(members),
            fix_share=fix_share,
            gap=gap_before,
            cpc=f"{churn_per_commit:.0f}",
        ),
    }


def _classify(
    *,
    order: int,
    per_week: float,
    del_ratio: float,
    fix_share: float,
    gap_before: int,
    span_days: int,
    churn_per_commit: float,
    baseline: dict,
) -> str:
    """判断这一章的「画风」。

    三个关键信号全部**相对本仓库常态**，不写死阈值：
      相对删除比 = 本章删除占比 / 全仓库删除占比
      相对修复比 = 本章 fix 占比 / 全仓库 fix 占比
      相对单次体量 = 本章平均每次变更行数 / 全仓库平均

    为什么必须相对：成熟仓库的增删天然接近 1:1。绝对阈值 `del_ratio >= 0.42`
    会把 flask 十五年的正常开发全判成「重构」—— 实测六个章节里三个中招。

    判定顺序有讲究：空档 > 起点 > 低活跃 > 大删除 > 高频修复 > 啃硬骨头 >
    打磨 > 高速扩张 > 稳态。
    """
    rel_del = ratio(del_ratio, baseline["del_ratio"])
    rel_fix = ratio(fix_share, baseline["fix_share"])
    rel_churn = ratio(churn_per_commit, baseline["churn_per_commit"])

    if gap_before >= _GAP_DAYS:
        return "renaissance" if per_week >= 1.5 else "silence"
    if order == 0:
        return "genesis"
    if per_week < 0.6 and span_days >= 28:
        return "dormant"
    if rel_del >= 1.4 and per_week >= 0.25:
        return "refactor"
    if rel_fix >= 1.6 and fix_share >= 15:
        return "firefight"
    if rel_churn >= 1.8 and per_week < 2.5:
        return "grind"
    if rel_churn <= 0.55 and per_week >= 1.2:
        return "polish"
    if per_week >= 2.5:
        return "expansion"
    return "steady"


def _narrative(
    *,
    archetype: str,
    lang: str,
    start: str,
    per_week: float,
    insertions: int,
    deletions: int,
    commits: int,
    fix_share: float,
    gap: int,
    cpc: str = "0",
) -> str:
    key = f"chapter.narrative.{archetype}"
    return t(
        key,
        lang,
        start=start,
        per_week=per_week,
        insertions=f"{insertions:,}",
        deletions=f"{deletions:,}",
        commits=commits,
        fix_share=f"{fix_share:.0f}%",
        gap=gap,
        cpc=cpc,
    )


def _single_chapter(commits: list[Commit], points: list[dict], lang: str, granularity: str) -> dict:
    ordered = sorted(commits, key=lambda c: c.authored_at)
    start = ordered[0].authored_at.date().isoformat()
    end = ordered[-1].authored_at.date().isoformat()
    insertions = sum(c.insertions for c in commits)
    deletions = sum(c.deletions for c in commits)
    names = chapter_archetypes(lang)
    # 单章说明整段历史没有可切分的结构。刚起步的仓库叫「稳态迭代」是错的。
    span_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    archetype = "genesis" if span_days <= 180 else "steady"
    return {
        "id": "ch01",
        "archetype": archetype,
        "title": names.get(archetype, archetype),
        "start": start,
        "end": end,
        "span_days": (date.fromisoformat(end) - date.fromisoformat(start)).days + 1,
        "weeks": round2(max(1.0, (date.fromisoformat(end) - date.fromisoformat(start)).days / 7)),
        "commits": len(commits),
        "insertions": insertions,
        "deletions": deletions,
        "net": insertions - deletions,
        "authors": len({c.email.lower() for c in commits}),
        "commits_per_week": round2(len(commits)),
        "fix_share": pct(sum(1 for c in commits if c.kind == "fix"), len(commits)),
        "delete_ratio": pct(deletions, insertions + deletions),
        "gap_before_days": 0,
        "top_dirs": _top_dirs(commits, limit=3),
        "granularity": granularity,
        "summary": t(
            "chapter.summary",
            lang,
            commits=len(commits),
            insertions=f"{insertions:,}",
            deletions=f"{deletions:,}",
            authors=len({c.email.lower() for c in commits}),
        ),
        "narrative": t(
            "chapter.narrative.flat",
            lang,
            commits=len(commits),
            days=(date.fromisoformat(end) - date.fromisoformat(start)).days + 1,
        ),
    }


def _merge_tiny(chapters: list[dict]) -> list[dict]:
    if len(chapters) <= 1:
        return chapters
    merged: list[dict] = []
    for chapter in chapters:
        # 沉寂期的价值在它前面那段空档的长度，不在自己的跨度 ——
        # 「隔了两年回来提交两次」不该被并进前一章。
        tiny = (
            chapter["commits"] == 0
            or (chapter["span_days"] < 3 and chapter["archetype"] not in ("silence", "renaissance"))
        )
        if merged and tiny:
            prev = merged[-1]
            for field in ("commits", "insertions", "deletions"):
                prev[field] += chapter[field]
            prev["end"] = chapter["end"]
            prev["span_days"] = (
                datetime.fromisoformat(prev["end"]) - datetime.fromisoformat(prev["start"])
            ).days + 1
            continue
        merged.append(chapter)
    for index, chapter in enumerate(merged):
        chapter["id"] = f"ch{index + 1:02d}"
        chapter["net"] = chapter["insertions"] - chapter["deletions"]
    return merged


def _top_dirs(commits: list[Commit], limit: int) -> list[str]:
    counter: Counter[str] = Counter()
    for commit in commits:
        for change in commit.changes:
            parts = change.path.split("/")
            name = parts[0] if len(parts) > 1 else "(root)"
            counter[name] += change.churn
    return [name for name, _ in counter.most_common(limit)]


def build_events(
    commits: list[Commit], chapters: list[dict], *, lang: str = "zh", limit: int = 12
) -> list[dict]:
    """时间轴上的高亮节点。不追求全，只挑「值得停下来看一眼」的。"""
    if not commits:
        return []

    events: list[dict] = []
    seen: set[str] = set()

    def push(commit: Commit, label: str, metric: str) -> None:
        if commit.sha in seen or len(events) >= limit:
            return
        seen.add(commit.sha)
        events.append(
            {
                "date": commit.authored_at.date().isoformat(),
                "sha": commit.sha,
                "short_sha": commit.short_sha,
                "author": commit.author,
                "subject": commit.subject,
                "kind": commit.kind,
                "label": label,
                "label_text": t(f"event.{label}", lang),
                "metric": metric,
            }
        )

    # 1. 批量导入 —— 历史事故现场，通常值得单独标出来
    for commit in sorted(
        (c for c in commits if c.is_bulk), key=lambda c: -c.dropped_churn
    )[:2]:
        push(commit, "bulk_import", f"{commit.dropped_churn:,} lines")

    # 2. 依赖清单变更 —— 通常是技术栈转向的信号
    dep_commits = [
        c
        for c in commits
        if any(change.path.rsplit("/", 1)[-1] in _DEP_FILES for change in c.changes)
    ]
    for commit in sorted(dep_commits, key=lambda c: -c.churn)[:2]:
        push(commit, "deps", f"{commit.churn} lines")

    # 3. 单条最大变更
    for commit in sorted(commits, key=lambda c: -c.churn)[:3]:
        push(commit, "big_commit", f"+{commit.insertions}/-{commit.deletions}")

    # 4. 回滚
    for commit in [c for c in commits if c.kind == "revert"][:2]:
        push(commit, "revert", commit.subject[:40])

    # 5. 每章的第一条提交（章节起点）
    for chapter in chapters:
        lo, hi = date.fromisoformat(chapter["start"]), date.fromisoformat(chapter["end"])
        in_range = [c for c in commits if lo <= c.authored_at.date() <= hi]
        if in_range:
            push(min(in_range, key=lambda c: c.authored_at), "chapter_start", chapter["title"])

    events.sort(key=lambda e: e["date"])
    return events
