"""区间与版本周次工具。

项目中的测量值都是毫米区间；单点测量表示为上下界相等的区间。
区间运算刻意保持保守：只有真正相交的部分才允许作为“可承诺范围”。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional


WEEK_RE = re.compile(r"W?0*(\d+)", re.IGNORECASE)


def week_key(week: object) -> int:
    """把 ``W01`` / ``W12`` 一类周次转成可比较整数。"""

    if week is None:
        return -1
    match = WEEK_RE.search(str(week))
    if not match:
        raise ValueError(f"无法识别的周次: {week!r}")
    return int(match.group(1))


def not_later_than(week: object, as_of: Optional[str]) -> bool:
    """记录周次是否已在截止周之前（含同一周）。"""

    return as_of is None or week_key(week) <= week_key(as_of)


@dataclass(frozen=True)
class Interval:
    """闭区间。``axis`` 标明该区间使用的测量部位/量表。"""

    lo: float
    hi: float
    axis: Optional[str] = None
    source: Optional[str] = None

    def __post_init__(self) -> None:
        lo = float(self.lo)
        hi = float(self.hi)
        if lo > hi:
            raise ValueError(f"区间下界不能大于上界: {lo} > {hi}")
        object.__setattr__(self, "lo", lo)
        object.__setattr__(self, "hi", hi)

    @property
    def is_point(self) -> bool:
        return self.lo == self.hi

    @property
    def width(self) -> float:
        return self.hi - self.lo

    @property
    def midpoint(self) -> float:
        return (self.lo + self.hi) / 2.0

    def intersects(self, other: "Interval") -> bool:
        return max(self.lo, other.lo) <= min(self.hi, other.hi)

    # 常见命名别名。
    overlaps = intersects

    def intersection(self, other: "Interval") -> Optional["Interval"]:
        lo = max(self.lo, other.lo)
        hi = min(self.hi, other.hi)
        if lo > hi:
            return None
        axis = self.axis or other.axis
        return Interval(lo, hi, axis=axis)

    overlap = intersection

    def contains(self, other: "Interval | float | int") -> bool:
        if isinstance(other, (int, float)):
            return self.lo <= float(other) <= self.hi
        return self.lo <= other.lo and other.hi <= self.hi

    def relation_to(self, other: "Interval | float | int") -> str:
        """返回 ``inside`` / ``overlaps`` / ``below`` / ``above``。

        ``self`` 通常是量表边界，``other`` 是客户测量区间。
        """

        q = other if isinstance(other, Interval) else Interval(other, other)
        if self.contains(q):
            return "inside"
        if self.intersects(q):
            return "overlaps"
        if q.hi < self.lo:
            return "below"
        return "above"

    def shift(self, lo_delta: float, hi_delta: Optional[float] = None) -> "Interval":
        if hi_delta is None:
            hi_delta = lo_delta
        return Interval(self.lo + lo_delta, self.hi + hi_delta, self.axis, self.source)

    def to_dict(self) -> dict:
        return {"lo_mm": self.lo, "hi_mm": self.hi, "axis": self.axis, "source": self.source}


def intersection_all(intervals: Iterable[Interval]) -> Optional[Interval]:
    result: Optional[Interval] = None
    for interval in intervals:
        result = interval if result is None else result.intersection(interval)
        if result is None:
            return None
    return result


def bounding_union(intervals: Iterable[Interval]) -> Optional[Interval]:
    items = list(intervals)
    if not items:
        return None
    return Interval(min(i.lo for i in items), max(i.hi for i in items), items[0].axis)


def union_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    """合并相交或相接的区间，返回有序不相交区间。"""

    items = sorted(intervals, key=lambda x: (x.lo, x.hi))
    if not items:
        return []
    merged = [Interval(items[0].lo, items[0].hi, items[0].axis)]
    for item in items[1:]:
        last = merged[-1]
        if item.lo <= last.hi:
            merged[-1] = Interval(last.lo, max(last.hi, item.hi), last.axis or item.axis)
        else:
            merged.append(Interval(item.lo, item.hi, item.axis))
    return merged
