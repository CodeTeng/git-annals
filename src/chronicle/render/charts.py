"""SVG 图表生成。

全部在 Python 侧算好坐标再输出 SVG，理由有三：
1. 零第三方图表库，单文件才能压到几百 KB；
2. 断网、内网、老浏览器都能看，不依赖 CDN；
3. 截图和导出 PNG 时是矢量重绘，不会糊。

所有来自仓库的字符串（作者名、路径、commit message）一律 html.escape ——
commit message 是不可信输入，`<script>` 直接进 SVG 会破图甚至执行。
"""

from __future__ import annotations

import html
import math

from ..i18n import t, weekdays

__all__ = [
    "svg_timeline",
    "svg_commit_bars",
    "svg_heatmap",
    "svg_share_card",
    "svg_share_card_author",
]

_ADD = "#1D9E75"
_DEL = "#D85A30"
_INK = "#16151A"
_MUTED = "#6E6A63"
_LINE = "#E4E0D8"
_FONT = "ui-sans-serif,system-ui,'PingFang SC','Microsoft YaHei',sans-serif"


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _fmt(value: float) -> str:
    return f"{int(value):,}"


def _compact(value: float) -> str:
    for limit, suffix in ((1_000_000, "M"), (1_000, "k")):
        if value >= limit:
            text = f"{value / limit:.1f}"
            return text.removesuffix(".0") + suffix
    return str(int(value))


def _signed(value: float, sign: str) -> str:
    return f"{sign}{int(value):,}" if value else "0"


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    for factor in (1, 2, 2.5, 5, 10):
        candidate = magnitude * factor
        if candidate >= value:
            return candidate
    return magnitude * 10


def svg_timeline(
    points: list[dict], *, width: int = 880, height: int = 260, lang: str = "zh"
) -> str:
    """增减行的镜像面积图：新增在上，删除在下，零线居中。"""
    if not points:
        return f'<p class="empty">{_e(t("chart.empty", lang))}</p>'

    n = len(points)
    pad_l, pad_r, pad_t, pad_b = 46, 14, 16, 26
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    step = plot_w / n
    zero = pad_t + plot_h * 0.5

    max_up = max((p["insertions"] for p in points), default=0) or 1
    max_down = max((p["deletions"] for p in points), default=0) or 1
    top = _nice_max(max_up)
    bottom = _nice_max(max_down)
    scale_up = (plot_h * 0.5 - 6) / top
    scale_down = (plot_h * 0.5 - 6) / bottom

    def x_of(index: int) -> float:
        return pad_l + step * (index + 0.5)

    up_pts = [(x_of(i), zero - p["insertions"] * scale_up) for i, p in enumerate(points)]
    down_pts = [(x_of(i), zero + p["deletions"] * scale_down) for i, p in enumerate(points)]

    up_area = f"M{x_of(0) - step / 2:.1f},{zero:.1f} " + " ".join(
        f"L{x:.1f},{y:.1f}" for x, y in up_pts
    ) + f" L{x_of(n - 1) + step / 2:.1f},{zero:.1f} Z"
    down_area = f"M{x_of(0) - step / 2:.1f},{zero:.1f} " + " ".join(
        f"L{x:.1f},{y:.1f}" for x, y in down_pts
    ) + f" L{x_of(n - 1) + step / 2:.1f},{zero:.1f} Z"

    up_line = " ".join(f"{x:.1f},{y:.1f}" for x, y in up_pts)
    down_line = " ".join(f"{x:.1f},{y:.1f}" for x, y in down_pts)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">',
        f'<title>{_e(t("chart.timeline.title", lang))}</title>',
        # 网格
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{width - pad_r}" y2="{pad_t}" stroke="{_LINE}" stroke-width="0.5"/>',
        f'<line x1="{pad_l}" y1="{zero:.1f}" x2="{width - pad_r}" y2="{zero:.1f}" stroke="{_INK}" stroke-width="0.8" opacity="0.25"/>',
        f'<line x1="{pad_l}" y1="{height - pad_b}" x2="{width - pad_r}" y2="{height - pad_b}" stroke="{_LINE}" stroke-width="0.5"/>',
        # y 轴刻度
        f'<text x="{pad_l - 8}" y="{pad_t + 4}" text-anchor="end" font-size="11" fill="{_MUTED}">+{_compact(top)}</text>',
        f'<text x="{pad_l - 8}" y="{height - pad_b}" text-anchor="end" font-size="11" fill="{_MUTED}">−{_compact(bottom)}</text>',
        f'<text x="{pad_l - 8}" y="{zero + 4:.1f}" text-anchor="end" font-size="11" fill="{_MUTED}">0</text>',
        # 面积
        f'<path d="{up_area}" fill="{_ADD}" opacity="0.14"/>',
        f'<path d="{down_area}" fill="{_DEL}" opacity="0.14"/>',
        f'<polyline points="{up_line}" fill="none" stroke="{_ADD}" stroke-width="1.6" stroke-linejoin="round"/>',
        f'<polyline points="{down_line}" fill="none" stroke="{_DEL}" stroke-width="1.6" stroke-linejoin="round"/>',
    ]

    # x 轴标签：最多 6 个，避免重叠
    label_step = max(1, n // 6)
    for index in range(0, n, label_step):
        point = points[index]
        parts.append(
            f'<text x="{x_of(index):.1f}" y="{height - 8}" text-anchor="middle" '
            f'font-size="10.5" fill="{_MUTED}">{_e(point["start"][5:10])}</text>'
        )

    # 悬停提示用原生 title，不写 JS
    for index, point in enumerate(points):
        parts.append(
            f'<g><title>{_e(t("chart.tooltip.point", lang, label=point["label"], n=point["commits"], i=_signed(point["insertions"], "+"), d=_signed(point["deletions"], "−")))}</title>'
            f'<rect x="{x_of(index) - step / 2:.1f}" y="{pad_t}" width="{step:.1f}" '
            f'height="{plot_h}" fill="transparent"/></g>'
        )

    parts.append("</svg>")
    return "".join(parts)


def svg_commit_bars(
    points: list[dict], *, width: int = 880, height: int = 78, lang: str = "zh"
) -> str:
    """提交频次柱状图。单独成图，避免和增删量挤在一个坐标系里互相干扰。"""
    if not points:
        return ""
    n = len(points)
    pad_l, pad_r, pad_t, pad_b = 46, 14, 8, 18
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    step = plot_w / n
    peak = max((p["commits"] for p in points), default=0) or 1
    bar_w = max(1.5, step * 0.62)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">',
        f'<title>{_e(t("chart.bars.title", lang))}</title>',
        f'<text x="{pad_l - 8}" y="{pad_t + 9}" text-anchor="end" font-size="11" fill="{_MUTED}">{peak}</text>',
        f'<text x="{pad_l - 8}" y="{height - pad_b}" text-anchor="end" font-size="11" fill="{_MUTED}">0</text>',
        f'<line x1="{pad_l}" y1="{height - pad_b}" x2="{width - pad_r}" y2="{height - pad_b}" stroke="{_LINE}" stroke-width="0.5"/>',
    ]
    for index, point in enumerate(points):
        # 空周不画：一条 2px 的矮条会让整行看起像虚线，反而掩盖了「这周他不在」
        if not point["commits"]:
            continue
        bar_h = plot_h * (point["commits"] / peak)
        x = pad_l + step * index + (step - bar_w) / 2
        y = height - pad_b - bar_h
        opacity = 0.28 + 0.62 * (point["commits"] / peak)
        parts.append(
            f'<g><title>{_e(t("chart.tooltip.bar", lang, label=point["label"], n=point["commits"]))}</title>'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(bar_h, 2.5):.1f}" '
            f'rx="1" fill="{_INK}" opacity="{opacity:.2f}"/></g>'
        )
    parts.append("</svg>")
    return "".join(parts)


def svg_heatmap(rhythm: dict, *, cell: int = 15, gap: int = 3, lang: str = "zh") -> str:
    """7×24 作息热力图。周一在最上，凌晨在最左。"""
    matrix = rhythm.get("matrix") or [[0] * 24 for _ in range(7)]
    labels = rhythm.get("weekday_labels") or list(weekdays(lang))
    peak = max((max(row) for row in matrix), default=0) or 1

    left, top_pad = 38, 18
    width = left + 24 * (cell + gap)
    height = top_pad + 7 * (cell + gap) + 4

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">',
        f'<title>{_e(t("chart.heatmap.title", lang))}</title>'
        f'<desc>{_e(t("chart.heatmap.desc", lang))}</desc>',
    ]

    for hour in (0, 6, 12, 18, 23):
        x = left + hour * (cell + gap) + cell / 2
        parts.append(
            f'<text x="{x:.1f}" y="{top_pad - 6}" text-anchor="middle" '
            f'font-size="10" fill="{_MUTED}">{hour:02d}</text>'
        )

    for dow in range(7):
        y = top_pad + dow * (cell + gap)
        parts.append(
            f'<text x="{left - 8}" y="{y + cell * 0.72:.1f}" text-anchor="end" '
            f'font-size="10.5" fill="{_MUTED}">{_e(labels[dow])}</text>'
        )
        for hour in range(24):
            value = matrix[dow][hour]
            x = left + hour * (cell + gap)
            if value:
                ratio = (value / peak) ** 0.55  # 幂次压缩，否则零星提交全看不见
                opacity = 0.16 + 0.84 * ratio
                fill = _INK
            else:
                opacity = 0.035
                fill = _INK
            parts.append(
                f'<g><title>{_e(t("chart.tooltip.cell", lang, dow=labels[dow], h=f"{hour:02d}", n=value))}</title>'
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2.5" '
                f'fill="{fill}" opacity="{opacity:.3f}"/></g>'
            )

    parts.append("</svg>")
    return "".join(parts)


_BREAK_CHARS = "，。、；：！？,.;:!?）)」』”"


def _wrap(text: str, limit: int, max_lines: int = 3) -> list[str]:
    """按字符宽度折行。中文一字一格，英文按 0.55 折算。

    溢出时优先回退到最近的标点处断开 —— 否则中文会被切成「一段仍在生 / 长的历史」
    这种断在词中间的难看位置。
    """
    lines: list[str] = []
    current = ""
    width = 0.0
    for char in text:
        weight = 1.0 if ord(char) > 0x2E80 else 0.55
        if width + weight > limit and current:
            cut = -1
            for back in range(len(current) - 1, max(len(current) - 10, 0), -1):
                if current[back] in _BREAK_CHARS:
                    cut = back + 1
                    break
            if cut > 0:
                lines.append(current[:cut])
                current = current[cut:] + char
                width = sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in current)
            else:
                lines.append(current)
                current, width = char, weight
            if len(lines) == max_lines:
                current = ""
                break
            continue
        current += char
        width += weight
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def _mini_churn(
    points: list[dict], x0: float, y0: float, width: float, height: float
) -> str:
    """分享卡里的迷你增删曲线。画在深色底上，所以用浅一档的配色。"""
    if len(points) < 2:
        return ""
    n = len(points)
    step = width / n
    mid = y0 + height * 0.5
    max_up = max((p["insertions"] for p in points), default=0) or 1
    max_dn = max((p["deletions"] for p in points), default=0) or 1
    amp = height * 0.44

    up = [(x0 + step * (i + 0.5), mid - p["insertions"] / max_up * amp) for i, p in enumerate(points)]
    dn = [(x0 + step * (i + 0.5), mid + p["deletions"] / max_dn * amp) for i, p in enumerate(points)]

    up_area = f"M{x0:.1f},{mid:.1f} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in up) + f" L{x0 + width:.1f},{mid:.1f} Z"
    dn_area = f"M{x0:.1f},{mid:.1f} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in dn) + f" L{x0 + width:.1f},{mid:.1f} Z"
    up_line = " ".join(f"{x:.1f},{y:.1f}" for x, y in up)
    dn_line = " ".join(f"{x:.1f},{y:.1f}" for x, y in dn)

    return "".join(
        [
            f'<path d="{up_area}" fill="#5DCAA5" opacity="0.16"/>',
            f'<path d="{dn_area}" fill="#F0997B" opacity="0.16"/>',
            f'<polyline points="{up_line}" fill="none" stroke="#5DCAA5" stroke-width="1.4" opacity="0.85"/>',
            f'<polyline points="{dn_line}" fill="none" stroke="#F0997B" stroke-width="1.4" opacity="0.85"/>',
            f'<line x1="{x0}" y1="{mid:.1f}" x2="{x0 + width}" y2="{mid:.1f}" stroke="#F5F2EA" stroke-width="0.5" opacity="0.12"/>',
        ]
    )


def svg_share_card(report: dict, *, width: int = 720, height: int = 1080) -> str:
    """竖版分享卡，可直接在浏览器里转成 PNG。

    版式固定四段：标识 / 仓库名 + 金句 / 迷你曲线 / 四宫格数字。
    中间那条曲线不是装饰 —— 没有它，卡中间会空掉三分之一。
    """
    meta = report.get("meta", {})
    overview = report.get("overview", {})
    points = (report.get("timeline") or {}).get("points") or []
    repo_name = (meta.get("repo") or {}).get("name") or "repository"
    first = ((meta.get("range") or {}).get("first_commit") or "")[:10]
    last = ((meta.get("range") or {}).get("last_commit") or "")[:10]
    quote = report.get("quote") or ""
    card_lang = meta.get("lang", "zh")

    pad = 64
    inner = width - pad * 2
    parts = [
        f'<svg id="share-card" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">',
        f'<title>{_e(t("chart.card.title", meta.get("lang", "zh")))}</title>',
        f'<rect width="{width}" height="{height}" fill="#16151A"/>',
        f'<text x="{pad}" y="72" font-size="12.5" fill="#8C877E" letter-spacing="3">GIT CHRONICLE</text>',
        f'<text x="{width - pad}" y="72" text-anchor="end" font-size="12.5" fill="#8C877E">'
        f"{_e(first)} — {_e(last)}</text>",
    ]

    y = 168
    for line in _wrap(repo_name, 17, max_lines=2):
        parts.append(
            f'<text x="{pad}" y="{y}" font-size="56" font-weight="600" fill="#F5F2EA" '
            f'letter-spacing="-1.5">{_e(line)}</text>'
        )
        y += 64

    quote_y = 300
    for index, line in enumerate(_wrap(quote, 24, max_lines=2)):
        parts.append(
            f'<text x="{pad}" y="{quote_y + index * 32}" font-size="18" fill="#C9C3B6">{_e(line)}</text>'
        )

    parts.append(
        f'<text x="{pad}" y="386" font-size="10.5" fill="#6B6660" letter-spacing="1.5">'
        f'{_e(t("chart.card.weekly", card_lang))}</text>'
    )
    parts.append(_mini_churn(points, pad, 400, inner, 150))

    stats = [
        (t("ui.stat.commits", card_lang), _fmt(overview.get("commits", 0))),
        (t("ui.stat.added_lines", card_lang), _fmt(overview.get("insertions", 0))),
        (t("ui.stat.authors", card_lang), str(overview.get("authors", 0))),
        (t("ui.stat.active_days", card_lang), str(overview.get("active_days", 0))),
    ]
    for index, (label, value) in enumerate(stats):
        col, row = index % 2, index // 2
        x = pad + col * (inner / 2)
        sy = 690 + row * 122
        parts.append(
            f'<text x="{x}" y="{sy}" font-size="11.5" fill="#8C877E" letter-spacing="1.5">{_e(label)}</text>'
        )
        parts.append(
            f'<text x="{x}" y="{sy + 50}" font-size="40" font-weight="600" fill="#F5F2EA">{_e(value)}</text>'
        )

    parts.append(
        f'<line x1="{pad}" y1="{height - 96}" x2="{width - pad}" y2="{height - 96}" '
        f'stroke="#2E2B27" stroke-width="1"/>'
    )
    parts.append(
        f'<text x="{pad}" y="{height - 60}" font-size="12" fill="#6B6660">'
        f'git-chronicle · {_e(meta.get("version", "0.1.0"))}</text>'
    )
    zh = card_lang == "zh"
    tail = (
        f"{overview.get('span_days', 0)} 天 · {overview.get('files_touched', 0)} 个文件"
        if zh
        else f"{overview.get('span_days', 0)} days · {overview.get('files_touched', 0)} files"
    )
    parts.append(
        f'<text x="{width - pad}" y="{height - 60}" text-anchor="end" font-size="12" fill="#6B6660">'
        f"{_e(tail)}</text>"
    )
    parts.append("</svg>")
    return "".join(parts)


def _mini_heatmap(rhythm: dict, x0: float, y0: float, width: float, height: float) -> str:
    """分享卡里的迷你作息点阵。7 行 24 列，格子在卡片上小到只能看形状 —— 这正是重点。"""
    matrix = rhythm.get("matrix") or [[0] * 24 for _ in range(7)]
    peak = max((max(row) for row in matrix), default=0) or 1
    gap = 3.0
    cell_w = (width - 23 * gap) / 24
    cell_h = (height - 6 * gap) / 7

    parts = []
    for dow in range(7):
        for hour in range(24):
            value = matrix[dow][hour]
            x = x0 + hour * (cell_w + gap)
            y = y0 + dow * (cell_h + gap)
            if value:
                opacity = 0.14 + 0.86 * (value / peak) ** 0.55
                fill = "#5DCAA5"
            else:
                opacity = 0.06
                fill = "#F5F2EA"
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w:.1f}" height="{cell_h:.1f}" '
                f'rx="2" fill="{fill}" opacity="{opacity:.3f}"/>'
            )
    return "".join(parts)


def svg_share_card_author(report: dict, *, width: int = 720, height: int = 1080) -> str:
    """竖版个人分享卡。和项目卡同一套骨架，但把曲线换成作息点阵 ——
    那张图是「这个人几点写代码」的指纹，比任何数字都好认。"""
    meta = report.get("meta", {})
    persona = report.get("persona", {})
    rhythm = report.get("rhythm", {})
    quote = report.get("quote") or ""
    name = persona.get("name") or "developer"
    tags = [tag["label"] for tag in persona.get("tags", [])][:3]
    card_lang = meta.get("lang", "zh")

    pad = 64
    inner = width - pad * 2
    first = (persona.get("span_start") or "")[:10]
    last = (persona.get("span_end") or "")[:10]

    parts = [
        f'<svg id="share-card" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">',
        f'<title>{_e(t("chart.card.title", meta.get("lang", "zh")))}</title>',
        f'<rect width="{width}" height="{height}" fill="#16151A"/>',
        f'<text x="{pad}" y="72" font-size="12.5" fill="#8C877E" letter-spacing="3">DEVELOPER PROFILE</text>',
        f'<text x="{width - pad}" y="72" text-anchor="end" font-size="12.5" fill="#8C877E">'
        f"{_e(first)} — {_e(last)}</text>",
    ]

    y = 168
    for line in _wrap(name, 16, max_lines=2):
        parts.append(
            f'<text x="{pad}" y="{y}" font-size="54" font-weight="600" fill="#F5F2EA" '
            f'letter-spacing="-1.5">{_e(line)}</text>'
        )
        y += 62

    # 标签药丸
    pill_x = pad
    pill_y = y + 6
    for label in tags:
        pill_w = len(label) * 13 + 26
        if pill_x + pill_w > width - pad:
            break
        parts.append(
            f'<rect x="{pill_x}" y="{pill_y}" width="{pill_w}" height="30" rx="15" '
            f'fill="#1D9E75" opacity="0.14"/>'
            f'<text x="{pill_x + pill_w / 2}" y="{pill_y + 20}" text-anchor="middle" '
            f'font-size="13" fill="#5DCAA5">{_e(label)}</text>'
        )
        pill_x += pill_w + 8

    quote_y = pill_y + 78
    for index, line in enumerate(_wrap(quote, 25, max_lines=2)):
        parts.append(
            f'<text x="{pad}" y="{quote_y + index * 32}" font-size="18" fill="#C9C3B6">{_e(line)}</text>'
        )

    band = quote_y + 84
    parts.append(
        f'<text x="{pad}" y="{band}" font-size="10.5" fill="#6B6660" letter-spacing="1.5">'
        f'{_e(t("chart.card.rhythm", meta.get("lang", "zh")))}</text>'
    )
    parts.append(_mini_heatmap(rhythm, pad, band + 14, inner, 146))

    stats = [
        (t("ui.stat.commits", card_lang), _fmt(persona.get("commits", 0))),
        (t("ui.stat.share_of_repo", card_lang), f"{persona.get('share', 0)}%"),
        (t("ui.stat.added_lines", card_lang), _fmt(persona.get("insertions", 0))),
        (t("ui.stat.active_days", card_lang), str(persona.get("active_days", 0))),
    ]
    grid_top = band + 232
    for index, (label, value) in enumerate(stats):
        col, row = index % 2, index // 2
        x = pad + col * (inner / 2)
        sy = grid_top + row * 116
        parts.append(
            f'<text x="{x}" y="{sy}" font-size="11.5" fill="#8C877E" letter-spacing="1.5">{_e(label)}</text>'
        )
        parts.append(
            f'<text x="{x}" y="{sy + 48}" font-size="38" font-weight="600" fill="#F5F2EA">{_e(value)}</text>'
        )

    parts.append(
        f'<line x1="{pad}" y1="{height - 96}" x2="{width - pad}" y2="{height - 96}" '
        f'stroke="#2E2B27" stroke-width="1"/>'
    )
    repo_name = (meta.get("repo") or {}).get("name") or ""
    parts.append(
        f'<text x="{pad}" y="{height - 60}" font-size="12" fill="#6B6660">'
        f"{_e(repo_name)} · git-chronicle {_e(meta.get('version', '0.1.0'))}</text>"
    )
    zh = card_lang == "zh"
    tail = (
        f"{persona.get('span_days', 0)} 天"
        if zh
        else f"{persona.get('span_days', 0)} days"
    )
    parts.append(
        f'<text x="{width - pad}" y="{height - 60}" text-anchor="end" font-size="12" fill="#6B6660">'
        f"{_e(tail)}</text>"
    )
    parts.append("</svg>")
    return "".join(parts)
