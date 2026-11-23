"""基线比较。

这个模块存在的唯一理由，是修掉一类**同一个病根**的 bug：

    用绝对阈值去判断一个基线因仓库而异的指标。

vite 全仓库的 fix 占 33%，flask 是 5% —— 同一个 `fix_ratio >= 0.35` 在前者身上
让 15 个文件里 14 个都挂上「易碎」，在后者身上一个都不挂。判断标准本身错了，
不是阈值调得不准。

所以这里的函数不接受「常数」，只接受「该仓库自己的分布」：

    over(value, baseline, factor)   value 是不是显著高于本仓库常态
    quantile(values, q)             分位数（自带排序，调用方不必预处理）
    share(part, total)              比例，总数为 0 时返回 0

**约定**：任何 features 模块都不许再写裸的 `>= 0.42` 这类魔数做分类判断。
要么用这里的函数，要么把常数写进「结构常数」（如「至少 3 次变更才算样本」）。
这条约定由 tests/test_invariants.py 从行为上兜底。
"""

from __future__ import annotations

__all__ = ["quantile", "median", "over", "share", "ratio"]


def quantile(values: list[float], q: float) -> float:
    """线性插值分位数。空列表返回 0。"""
    if not values:
        return 0.0
    if q <= 0:
        return float(min(values))
    if q >= 1:
        return float(max(values))
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return float(ordered[low] * (1 - fraction) + ordered[high] * fraction)


def median(values: list[float]) -> float:
    return quantile(values, 0.5)


def share(part: float, total: float) -> float:
    return part / total if total else 0.0


def ratio(value: float, baseline: float, *, default: float = 1.0) -> float:
    """value 相对 baseline 的倍数。baseline 为 0 时返回 default。"""
    if baseline <= 0:
        return default
    return value / baseline


def over(value: float, baseline: float, *, factor: float, floor: float = 0.0) -> bool:
    """value 是否显著高于本仓库常态。

    floor 是「不值得为它打标签」的绝对下限，用于挡掉极小样本：
    一个只改过 2 行的小文件，fix_ratio 100% 也不该叫「易碎」。
    """
    if value < floor:
        return False
    if baseline <= 0:
        return value > floor
    return value >= baseline * factor
