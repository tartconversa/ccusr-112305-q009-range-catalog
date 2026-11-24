"""测量量表、版本链与可推断关系图。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from typing import Any, Optional

from .intervals import Interval, week_key
from .repository import Repository


AXIS_FIELDS = {"手围": "circumference", "指长": "finger", "掌宽": "palm"}


class ScaleNotConnectedError(ValueError):
    """两个量表之间没有共同测量或匹配试穿连接。"""


class MatchedFittingOnlyError(ScaleNotConnectedError):
    """量表可由匹配试穿连接，但不能据此做毫米数值换算。"""


@dataclass(frozen=True)
class Conversion:
    interval: Interval
    from_axis: str
    to_axis: str
    method: str  # direct | common_measurement | person_common_measurement | matched_fitting
    confidence: float
    persons: int
    provenance: list[str]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval": self.interval.to_dict(),
            "from_axis": self.from_axis,
            "to_axis": self.to_axis,
            "method": self.method,
            "confidence": round(self.confidence, 4),
            "persons": self.persons,
            "provenance": self.provenance,
            "note": self.note,
        }


class ScaleService:
    def __init__(self, repo: Repository):
        self.repo = repo
        self._method_index = {
            (r["sku"], r["version"]): r["method"] for r in repo.scale_rows()
        }
        self._graph = self._build_graph()

    # 基础查询 ---------------------------------------------------------
    def axis(self, sku: str, version: str) -> str:
        return self._method_index[(sku, version)]

    def label_interval(self, sku: str, version: str) -> Interval:
        row = self.repo.scale(sku, version)
        return Interval(row["lower_mm"], row["upper_mm"], axis=row["method"],
                        source=f"scales:{sku}/{version}")

    def versions(self, sku: str, as_of: Optional[str] = None) -> list[dict[str, Any]]:
        rows = [r for r in self.repo.scale_rows() if r["sku"] == sku]
        if as_of is not None:
            rows = [r for r in rows if week_key(r["publish_week"]) <= week_key(as_of)]
        return sorted(rows, key=lambda r: week_key(r["publish_week"]))

    def latest_version(self, sku: str, as_of: Optional[str] = None) -> Optional[str]:
        rows = self.versions(sku, as_of)
        return rows[-1]["version"] if rows else None

    def version_candidates(self, sku: str, actual_version: Optional[str] = None,
                           as_of: Optional[str] = None,
                           sell_through_weeks: int = 2) -> list[str]:
        """实际版次优先；未知时给旧版/新版候选，但绝不把最新版当实物。"""

        if actual_version:
            return [actual_version] if self.repo.has_scale(sku, actual_version) else [actual_version]
        rows = self.versions(sku, as_of)
        if not rows:
            return []
        if len(rows) == 1:
            return [rows[0]["version"]]
        newest = rows[-1]
        candidates = [newest["version"]]
        # 改款后两周内旧网页组套/库存仍可能发出。
        if week_key(as_of or newest["publish_week"]) - week_key(newest["publish_week"]) <= sell_through_weeks:
            if newest.get("replace"):
                candidates.append(newest["replace"])
            elif len(rows) >= 2:
                candidates.append(rows[-2]["version"])
        # 去重并保留发布顺序。
        seen: set[str] = set()
        ordered: list[str] = []
        for version in reversed([r["version"] for r in rows]):
            if version in candidates and version not in seen:
                ordered.append(version)
                seen.add(version)
        return ordered or [r["version"] for r in rows]

    # 关系图 -----------------------------------------------------------
    def _build_graph(self) -> dict[str, dict[str, dict[str, Any]]]:
        graph: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

        def add_edge(a: str, b: str, kind: str, source: str, persons: Optional[list[str]] = None):
            if a == b:
                return
            old = graph[a].get(b)
            if old is None:
                edge = {"kinds": {kind}, "sources": [source], "persons": set(persons or [])}
                graph[a][b] = edge
                graph[b][a] = edge
            else:
                old["kinds"].add(kind)
                old["sources"].append(source)
                if persons:
                    old["persons"].update(persons)

        # 同一天/同一来源的共同实测：手围 <-> 指长。
        for row in self.repo.table("reference"):
            person = row["person"]
            for axis, field in AXIS_FIELDS.items():
                if field not in row or row[field] is None:
                    continue
                for other_axis, other_field in AXIS_FIELDS.items():
                    if other_axis >= axis or other_field not in row or row[other_field] is None:
                        continue
                    add_edge(axis, other_axis, "common_measurement",
                             f"reference:{person}:{row.get('recorded')}", [person])

        # 同一受试者试过多款：只能证明量表可连接，不能当作独立人群。
        person_axes: dict[str, set[str]] = defaultdict(set)
        for row in self.repo.table("fittings"):
            key = (row["sku"], row["version"])
            if key in self._method_index:
                person_axes[row["person"]].add(self._method_index[key])
        for person, axes in person_axes.items():
            axes_list = sorted(axes)
            for i, axis in enumerate(axes_list):
                for other in axes_list[i + 1:]:
                    add_edge(axis, other, "matched_fitting", f"fittings:{person}", [person])
        return graph

    def axes(self) -> list[str]:
        return sorted({r["method"] for r in self.repo.scale_rows()})

    def connection(self, from_axis: str, to_axis: str) -> dict[str, Any]:
        if from_axis == to_axis:
            return {"connected": True, "path": [from_axis], "kinds": {"direct"},
                    "sources": [], "persons": set()}
        # BFS，保留来源路径。
        queue: list[tuple[str, list[str]]] = [(from_axis, [from_axis])]
        seen = {from_axis}
        while queue:
            node, path = queue.pop(0)
            for nxt, edge in self._graph.get(node, {}).items():
                if nxt in seen:
                    continue
                new_path = path + [nxt]
                if nxt == to_axis:
                    return {
                        "connected": True,
                        "path": new_path,
                        "kinds": set(edge["kinds"]),
                        "sources": list(edge["sources"])[:20],
                        "persons": set(edge["persons"]),
                    }
                seen.add(nxt)
                queue.append((nxt, new_path))
        return {"connected": False, "path": [], "kinds": set(), "sources": [], "persons": set()}

    def assert_connected(self, from_axis: str, to_axis: str) -> dict[str, Any]:
        edge = self.connection(from_axis, to_axis)
        if not edge["connected"]:
            raise ScaleNotConnectedError(
                f"{from_axis} 与 {to_axis} 之间没有共同测量或匹配试穿，不能互相推断"
            )
        return edge

    # 客户实测资料 ------------------------------------------------------
    def measurement_sets(self, person: str, as_of: Optional[str] = None) -> list[dict[str, Any]]:
        """返回截至 ``as_of`` 已可使用的实测，按来源逐条保留。

        新的复测不会覆盖旧记录，也不会补写过去缺失的部位。
        """

        sets: list[dict[str, Any]] = []
        ref = self.repo.reference_for(person, as_of)
        if ref:
            for axis, field in AXIS_FIELDS.items():
                if ref.get(field) is not None:
                    value = float(ref[field])
                    interval = Interval(value, value, axis, f"reference:{ref['person']}:{ref.get('recorded')}:{axis}")
                    sets.append({
                        "interval": interval.to_dict(),
                        "axis": axis,
                        "source": f"reference:{ref['person']}:{ref.get('recorded')}:{axis}",
                        "kind": "common_measurement",
                        "week": ref.get("recorded"),
                        "received": ref.get("recorded"),
                        "scope": "共同实测",
                        "method": "reference",
                        "cohort": ref.get("size_cohort"),
                    })
        for row in self.repo.provider_reports(person, as_of):
            axis = row["measured_axis"]
            interval = Interval(row["lo_mm"], row["hi_mm"], axis, row.get("reading") or row.get("receipt"))
            sets.append({
                "interval": interval.to_dict(),
                "axis": axis,
                "source": f"provider_reports:{row.get('reading') or row.get('receipt')}",
                "kind": "provider_report",
                "week": row.get("sample_week"),
                "received": row.get("received"),
                "scope": row.get("scope"),
                "method": row.get("method"),
                "cohort": None,
            })
        return sorted(sets, key=lambda x: week_key(x.get("received")))

    def measurements_for_axis(self, person: str, axis: str,
                              as_of: Optional[str] = None) -> list[dict[str, Any]]:
        return [m for m in self.measurement_sets(person, as_of) if m["axis"] == axis]

    # 数值转换 ---------------------------------------------------------
    def convert(self, interval: Interval, to_axis: str, person: Optional[str] = None,
                cohort: Optional[str] = None, as_of: Optional[str] = None) -> Conversion:
        from_axis = interval.axis
        if not from_axis:
            raise ValueError("待转换区间必须标明 axis")
        if from_axis == to_axis:
            return Conversion(interval, from_axis, to_axis, "direct", 1.0, 0,
                              [interval.source or "direct"], "同一测量部位")

        edge = self.assert_connected(from_axis, to_axis)

        # 优先使用客户本人的“同次共同实测”。不能拿另一场景/另一次复测冒充
        # 本次区间的数值变换；新报告自身会作为独立 measurement view 参与判断。
        if person:
            ref = self.repo.reference_for(person, as_of)
            target_field = AXIS_FIELDS.get(to_axis)
            if ref and target_field and ref.get(target_field) is not None:
                value = float(ref[target_field])
                source_week = self._source_sample_week(interval.source, person, as_of)
                same_event = source_week in {None, ref.get("recorded")}
                return Conversion(
                    Interval(value, value, to_axis, f"reference:{person}:{ref.get('recorded')}:{to_axis}"),
                    from_axis, to_axis,
                    "person_common_measurement", 0.95 if same_event else 0.55, 1,
                    [interval.source or "query", f"reference:{person}:{ref.get('recorded')}"],
                    ("同次共同实测的另一部位" if same_event else
                     f"仅能用 {ref.get('recorded')} 的历史共同实测作连接锚点；不能当作 {source_week} 同场景换算"),
                )

            paired_provider = self._paired_provider_report(interval, person, to_axis, as_of)
            if paired_provider is not None:
                return paired_provider

        pairs = self._paired_reference(from_axis, to_axis, cohort)
        if len(pairs) >= 3:
            converted, residual_sd = self._ols_interval(interval, pairs, to_axis)
            cohort_note = "客群共同测量" if cohort and any(
                p.get("cohort") == cohort for p in self.repo.table("reference")) else "指定客群样本不足，使用全部客群"
            confidence = self._mapping_confidence(len(pairs), residual_sd)
            return Conversion(
                converted, from_axis, to_axis, "common_measurement", confidence,
                len(pairs), [f"reference_pairs:{len(pairs)}"], cohort_note,
            )

        if "matched_fitting" in edge["kinds"]:
            raise MatchedFittingOnlyError(
                "只有匹配试穿连接：可比较同一受试者的等级/款标，但不能把毫米区间直接换算到另一量表"
            )
        raise ScaleNotConnectedError("没有足够的共同测量执行数值转换")

    def _source_sample_week(self, source: Optional[str], person: str,
                            as_of: Optional[str]) -> Optional[str]:
        if not source:
            return None
        source_id = source.split(":")[-1]
        for row in self.repo.provider_reports(person, as_of):
            if row.get("receipt") == source_id or row.get("reading") == source_id:
                return row.get("sample_week")
        if source.startswith("reference:"):
            parts = source.split(":")
            return parts[2] if len(parts) >= 3 else None
        return None

    def _paired_provider_report(self, interval: Interval, person: str, to_axis: str,
                                as_of: Optional[str]) -> Optional[Conversion]:
        source_id = (interval.source or "").split(":")[-1]
        source_row = None
        for row in self.repo.provider_reports(person, as_of):
            if row.get("receipt") == source_id or row.get("reading") == source_id:
                source_row = row
                break
        if not source_row:
            return None
        candidates = [
            row for row in self.repo.provider_reports(person, as_of)
            if row.get("measured_axis") == to_axis
            and row.get("sample_week") == source_row.get("sample_week")
        ]
        same_scope = [row for row in candidates if row.get("scope") == source_row.get("scope")]
        if same_scope:
            row = same_scope[-1]
            target = Interval(row["lo_mm"], row["hi_mm"], to_axis, row.get("reading"))
            return Conversion(target, interval.axis, to_axis, "paired_provider_report", 0.9, 1,
                              [interval.source or "source", f"provider_reports:{row['reading']}"],
                              "同周次且同测量场景的服务商配对上报")
        if candidates:
            row = candidates[-1]
            target = Interval(row["lo_mm"], row["hi_mm"], to_axis, row.get("reading"))
            return Conversion(target, interval.axis, to_axis, "week_matched_provider_report", 0.55, 1,
                              [interval.source or "source", f"provider_reports:{row['reading']}"],
                              "仅周次匹配但场景不同，只能弱连接，不能替代同场景共同测量")
        return None

    def _paired_reference(self, from_axis: str, to_axis: str,
                          cohort: Optional[str] = None) -> list[dict[str, float]]:
        from_field = AXIS_FIELDS[from_axis]
        to_field = AXIS_FIELDS[to_axis]
        pairs: list[dict[str, float]] = []
        for row in self.repo.table("reference"):
            if cohort and row.get("size_cohort") != cohort:
                continue
            if row.get(from_field) is not None and row.get(to_field) is not None:
                pairs.append({"x": float(row[from_field]), "y": float(row[to_field]),
                              "cohort": row.get("size_cohort")})
        if cohort and len(pairs) < 3:
            return self._paired_reference(from_axis, to_axis, None)
        return pairs

    @staticmethod
    def _ols_interval(interval: Interval, pairs: list[dict[str, float]],
                      to_axis: str) -> tuple[Interval, float]:
        xs = [p["x"] for p in pairs]
        ys = [p["y"] for p in pairs]
        n = len(pairs)
        mx = sum(xs) / n
        my = sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs) or 1.0
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
        intercept = my - slope * mx
        residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
        sd = math.sqrt(sum(r * r for r in residuals) / max(1, n - 2))
        # 斜率在本资料中为正；仍按端点排序以保持稳健。
        a = intercept + slope * interval.lo
        b = intercept + slope * interval.hi
        lo, hi = sorted((a, b))
        # 一个残差标准差作为保守推断带。
        return Interval(lo - sd, hi + sd, axis=to_axis), sd

    @staticmethod
    def _mapping_confidence(n: int, residual_sd: float) -> float:
        sample_score = min(1.0, n / 40.0)
        error_score = 1.0 / (1.0 + residual_sd / 5.0)
        return round(0.25 + 0.55 * sample_score * error_score, 3)
