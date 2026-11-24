"""小工具：二项比例的 Wilson 下界等可解释可信度。"""

from __future__ import annotations

import math
from typing import Iterable


def wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    phat = successes / total
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (phat + z2 / (2 * total)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z2 / (4 * total)) / total) / denom
    return max(0.0, center - margin)


def mean(values: Iterable[float]) -> float | None:
    vals = list(values)
    return sum(vals) / len(vals) if vals else None


def pstdev(values: Iterable[float]) -> float | None:
    vals = list(values)
    if len(vals) < 2:
        return 0.0 if vals else None
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def credibility_level(score: float, persons: int) -> str:
    if persons < 2 or score < 0.25:
        return "不足"
    if score < 0.40:
        return "低"
    if score < 0.60:
        return "中"
    return "高"
