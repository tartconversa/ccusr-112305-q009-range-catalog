"""不同客户群的手部尺寸分布。"""

from __future__ import annotations

from typing import Any, Optional

from .repository import Repository
from .scales import AXIS_FIELDS, ScaleService
from .stats_utils import mean, pstdev


class CohortService:
    def __init__(self, repo: Repository, scales: ScaleService):
        self.repo = repo
        self.scales = scales

    def distribution(self, cohort: Optional[str], axis: str) -> dict[str, Any]:
        field = AXIS_FIELDS.get(axis)
        values: list[float] = []
        persons: set[str] = set()
        source = "reference"
        if field:
            for row in self.repo.table("reference"):
                if cohort and row.get("size_cohort") != cohort:
                    continue
                if row.get(field) is not None:
                    values.append(float(row[field]))
                    persons.add(row["person"])

        # 大众客群没有 common reference；只能把试穿区间中点作为弱分布线索。
        interval_based = False
        if not values:
            interval_based = True
            for row in self.repo.table("fittings"):
                if cohort and row.get("cohort") != cohort:
                    continue
                if self.scales.axis(row["sku"], row["version"]) != axis:
                    continue
                values.append((float(row["lower_mm"]) + float(row["upper_mm"])) / 2)
                persons.add(row["person"])

        return {
            "cohort": cohort or "全部",
            "axis": axis,
            "persons": len(persons),
            "sample_points": len(values),
            "min_mm": min(values) if values else None,
            "max_mm": max(values) if values else None,
            "mean_mm": round(mean(values), 2) if mean(values) is not None else None,
            "stdev_mm": round(pstdev(values), 2) if pstdev(values) is not None else None,
            "interval_midpoint_based": interval_based,
            "source_warning": (
                "该客群缺少共同实测，分布由试穿区间中点近似，不能替代实测分布"
                if interval_based else None
            ),
        }

    def all_distributions(self) -> list[dict[str, Any]]:
        cohorts = sorted({r.get("size_cohort") for r in self.repo.table("reference") if r.get("size_cohort")}
                         | {r.get("cohort") for r in self.repo.table("fittings") if r.get("cohort")})
        axes = ["手围", "指长", "掌宽"]
        return [self.distribution(cohort, axis) for cohort in cohorts for axis in axes]
