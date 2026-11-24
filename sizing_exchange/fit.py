"""按款、标注版本、客群和受试者计算适配证据与可信度。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from .intervals import Interval, union_intervals, week_key
from .repository import Repository
from .scales import AXIS_FIELDS, ScaleService, MatchedFittingOnlyError
from .cohorts import CohortService
from .stats_utils import credibility_level, wilson_lower


NEGATIVE_RESULTS = {"偏紧", "偏松"}
UNKNOWN_RESULTS = {"单边未知", "未知", "未记录"}


def is_holdout_person(person: str) -> bool:
    """确定性留出桶：数字编号模 5 余 4；非数字 ID 用字符和。"""

    digits = "".join(ch for ch in person if ch.isdigit())
    bucket = int(digits) % 5 if digits else sum(map(ord, person)) % 5
    return bucket == 4


class FitAdvisor:
    def __init__(self, repo: Repository, scales: ScaleService):
        self.repo = repo
        self.scales = scales
        self.cohorts = CohortService(repo, scales)

    def rows(self, sku: str, version: str, cohort: Optional[str] = None,
             as_of: Optional[str] = None) -> list[dict[str, Any]]:
        rows = [r for r in self.repo.table("fittings")
                if r["sku"] == sku and r["version"] == version]
        if cohort:
            rows = [r for r in rows if r.get("cohort") == cohort]
        if as_of:
            from .intervals import week_key
            rows = [r for r in rows if week_key(r["week"]) <= week_key(as_of)]
        return rows

    def evidence_summary(self, sku: str, version: str,
                         cohort: Optional[str] = None,
                         as_of: Optional[str] = None,
                         exclude_holdout: bool = False) -> dict[str, Any]:
        rows = self.rows(sku, version, cohort, as_of)
        if exclude_holdout:
            rows = [r for r in rows if not is_holdout_person(r["person"])]
        by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_person[row["person"]].append(row)

        result_persons: dict[str, set[str]] = defaultdict(set)
        suitable_intervals: list[Interval] = []
        all_intervals: list[Interval] = []
        for row in rows:
            result_persons[row["result"]].add(row["person"])
            interval = Interval(row["lower_mm"], row["upper_mm"],
                                self.scales.axis(sku, version), f"fitting:{row.get('person')}")
            all_intervals.append(interval)
            if row["result"] == "适合":
                suitable_intervals.append(interval)

        classified = set().union(*(result_persons[r] for r in ("适合", "偏紧", "偏松"))) if result_persons else set()
        suitable_persons = result_persons.get("适合", set())
        lower = wilson_lower(len(suitable_persons), len(classified))
        merged_suitable = union_intervals(suitable_intervals)
        return {
            "cohort": cohort or "全部客群",
            "records": len(rows),
            "independent_persons": len(by_person),
            "result_persons": {k: len(v) for k, v in result_persons.items()},
            "classified_persons": len(classified),
            "suitable_persons": len(suitable_persons),
            "suitable_rate": round(len(suitable_persons) / len(classified), 3) if classified else None,
            "suitable_wilson_lower95": round(lower, 3),
            "suitable_intervals": [i.to_dict() for i in merged_suitable],
            "suitable_envelope": (
                Interval(min(i.lo for i in suitable_intervals),
                         max(i.hi for i in suitable_intervals)).to_dict()
                if suitable_intervals else None
            ),
            "evidence_envelope": (
                Interval(min(i.lo for i in all_intervals),
                         max(i.hi for i in all_intervals)).to_dict()
                if all_intervals else None
            ),
            "dependency_warning": "同一受试者的多款试穿是配对样本，不能按款数重复计为独立人群",
        }

    def direct_records(self, person: str, sku: str, version: str,
                       as_of: Optional[str] = None) -> list[dict[str, Any]]:
        return [r for r in self.repo.fittings_for_person(person, as_of)
                if r["sku"] == sku and r["version"] == version]

    def matched_grade_links(self, sku: str, version: str,
                           as_of: Optional[str] = None) -> dict[str, Any]:
        """列出与该款该版由同一受试者连接的其他量表试穿等级。

        返回的是配对关系，不能把每个款的行数当成互不相关的独立样本。
        """

        source_axis = self.scales.axis(sku, version)
        source_rows = self.rows(sku, version, as_of=as_of)
        source_by_person = {r["person"]: r for r in source_rows}
        links = []
        counterpart_pairs: set[tuple[str, str, str]] = set()
        for row in self.repo.table("fittings"):
            if as_of and week_key(row.get("week")) > week_key(as_of):
                continue
            person = row["person"]
            source = source_by_person.get(person)
            if not source:
                continue
            other_axis = self.scales.axis(row["sku"], row["version"])
            if other_axis == source_axis:
                continue
            key = (row["sku"], row["version"], row["result"])
            counterpart_pairs.add(key)
            links.append({
                "person": person,
                "source_result": source["result"],
                "source_interval": [source["lower_mm"], source["upper_mm"]],
                "matched_sku": row["sku"],
                "matched_version": row["version"],
                "matched_axis": other_axis,
                "matched_result": row["result"],
                "matched_interval": [row["lower_mm"], row["upper_mm"]],
            })
        return {
            "source_axis": source_axis,
            "paired_persons": len(source_by_person),
            "counterpart_grade_kinds": len(counterpart_pairs),
            "links": links[:50],
            "dependency_warning": "这些是同一批人的配对试穿，不能拆成多款独立人群",
        }

    def advise(self, sku: str, version: str, person: Optional[str] = None,
              cohort: Optional[str] = None, measurement: Optional[Interval] = None,
              as_of: Optional[str] = None) -> dict[str, Any]:
        if version is None or not self.repo.has_scale(sku, version):
            return {
                "sku": sku, "version": version, "axis": None, "cohort": cohort or "未指定",
                "person": person, "status": "unknown_scale",
                "status_text": "没有该款/标注版本的量表，不能推断适配",
                "reasons": ["未知款或版次；空 version 不得按最新版补全"],
                "store_guarantee_allowed": False,
                "counter_statement": "版次/量表未知，不能给出适配承诺",
                "fit_range": {"label_range": None, "safe_committable_range": None},
                "confidence": 0.0, "confidence_level": "不足",
                "cohort_evidence": {"independent_persons": 0},
                "all_cohort_evidence": {"independent_persons": 0},
                "heldout_bias": {"records": 0},
                "matching_evidence": {"matched_records": 0},
                "measurement_views": [], "measurement_provenance_detail": [],
                "direct_fittings": [],
                "measurement_provenance": [], "pending_negotiation_items": [],
            }
        axis = self.scales.axis(sku, version)
        if measurement is not None and not measurement.axis:
            measurement = Interval(measurement.lo, measurement.hi, axis, measurement.source)
        label = self.scales.label_interval(sku, version)
        cohort_to_use = cohort
        if not cohort_to_use and person:
            cohort_to_use = self._person_cohort(person)

        cohort_evidence = self.evidence_summary(sku, version, cohort_to_use, as_of)
        training_evidence = self.evidence_summary(
            sku, version, cohort_to_use, as_of, exclude_holdout=True
        )
        all_evidence = self.evidence_summary(sku, version, None, as_of)
        validation = self.validate(sku, version, cohort_to_use, as_of)

        measurement_views = self._measurement_views(
            axis, person=person, measurement=measurement, cohort=cohort_to_use, as_of=as_of
        )
        direct = self.direct_records(person, sku, version, as_of) if person else []
        matching = self._matching_evidence(sku, version, axis, measurement_views, cohort_to_use, as_of)
        after_sales = self.after_sales_check(sku, version, as_of)
        matched_links = self.matched_grade_links(sku, version, as_of)
        matched_summary = {k: v for k, v in matched_links.items() if k != "links"}

        status, reasons = self._classify(
            axis, label, measurement_views, direct, matching
        )
        score, confidence_level = self._confidence(training_evidence, validation, direct, status)
        guarantee = status == "fit_supported" and not any(
            v.get("table_relation") not in {"inside", "direct_observation"} for v in measurement_views
        )

        safe_ranges = []
        for view in measurement_views:
            converted = view.get("converted_interval")
            if converted:
                q = Interval(converted["lo_mm"], converted["hi_mm"], axis)
                intersection = label.intersection(q)
                if intersection:
                    safe_ranges.append(intersection)
        safe_range = None
        if safe_ranges:
            # 不同测量来源/场景可能冲突；保守取所有可承诺交集的共同部分。
            common = safe_ranges[0]
            for rng in safe_ranges[1:]:
                common = common.intersection(rng)
                if common is None:
                    break
            safe_range = common.to_dict() if common else None

        return {
            "sku": sku,
            "version": version,
            "mark": self.repo.scale(sku, version).get("mark"),
            "class": self.repo.scale(sku, version).get("class"),
            "axis": axis,
            "cohort": cohort_to_use or "未指定",
            "person": person,
            "status": status,
            "status_text": self.status_text(status),
            "reasons": reasons,
            "store_guarantee_allowed": guarantee,
            "counter_statement": self.counter_statement(status, guarantee),
            "fit_range": {
                "label_range": label.to_dict(),
                "safe_committable_range": safe_range,
                "observed_suitable_union": cohort_evidence["suitable_intervals"],
                "observed_suitable_envelope": cohort_evidence["suitable_envelope"],
            },
            "confidence": score,
            "confidence_level": confidence_level,
            "cohort_distribution": self.cohorts.distribution(cohort_to_use, axis),
            "cohort_evidence": cohort_evidence,
            "training_evidence": training_evidence,
            "all_cohort_evidence": all_evidence,
            "heldout_bias": validation,
            "after_sales_check": after_sales,
            "matched_grade_links": matched_summary,
            "measurement_views": measurement_views,
            "measurement_provenance_detail": [
                {
                    "source": v.get("source"),
                    "kind": v.get("source_kind"),
                    "axis": v.get("source_axis"),
                    "scope": v.get("source_scope"),
                    "sample_week": v.get("week"),
                    "received": v.get("received"),
                    "conversion": (v.get("conversion") or {}).get("method"),
                    "confidence": (v.get("conversion") or {}).get("confidence"),
                }
                for v in measurement_views
            ],
            "direct_fittings": direct,
            "matching_evidence": matching,
            "measurement_provenance": [v.get("source") for v in measurement_views],
        }

    def _person_cohort(self, person: str) -> Optional[str]:
        ref = self.repo.reference_for(person)
        if ref and ref.get("size_cohort"):
            return ref["size_cohort"]
        rows = [r for r in self.repo.table("fittings") if r["person"] == person and r.get("cohort")]
        return rows[-1]["cohort"] if rows else None

    def _measurement_views(self, target_axis: str, person: Optional[str],
                           measurement: Optional[Interval], cohort: Optional[str],
                           as_of: Optional[str]) -> list[dict[str, Any]]:
        source_sets: list[dict[str, Any]] = []
        if measurement is not None:
            interval = Interval(measurement.lo, measurement.hi,
                                measurement.axis or target_axis, measurement.source or "application")
            source_sets.append({"interval": interval, "axis": interval.axis,
                                "source": interval.source, "kind": "application_input",
                                "scope": "申请输入"})
        if person:
            source_sets.extend(self.scales.measurement_sets(person, as_of))

        views: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        for source in source_sets:
            interval_data = source["interval"]
            if isinstance(interval_data, Interval):
                interval = interval_data
            else:
                interval = Interval(
                    interval_data["lo_mm"], interval_data["hi_mm"],
                    interval_data.get("axis"), interval_data.get("source")
                )
            source_id = str(source.get("source"))
            if source_id in seen_sources:
                continue
            seen_sources.add(source_id)
            view: dict[str, Any] = {
                "source": source_id,
                "source_kind": source.get("kind"),
                "source_axis": source["axis"],
                "source_scope": source.get("scope"),
                "week": source.get("week"),
                "received": source.get("received"),
                "original_interval": interval.to_dict(),
            }
            try:
                conversion = self.scales.convert(
                    interval, target_axis, person=person if source.get("kind") != "application_input" else None,
                    cohort=cohort, as_of=as_of
                )
                converted = conversion.interval
                view["conversion"] = conversion.to_dict()
                view["converted_interval"] = converted.to_dict()
            except MatchedFittingOnlyError as exc:
                view["conversion_error"] = str(exc)
                view["table_relation"] = "matched_grade_only"
            except Exception as exc:  # ScaleNotConnectedError or insufficient mapping
                view["conversion_error"] = str(exc)
                view["table_relation"] = "disconnected"
            views.append(view)
        return views

    def _matching_evidence(self, sku: str, version: str, axis: str,
                           views: list[dict[str, Any]], cohort: Optional[str],
                           as_of: Optional[str]) -> dict[str, Any]:
        rows = self.rows(sku, version, None, as_of)
        intervals = [Interval(r["lower_mm"], r["upper_mm"], axis) for r in rows]
        query_intervals = []
        for view in views:
            data = view.get("converted_interval")
            if data and not view.get("conversion_error"):
                query_intervals.append(Interval(data["lo_mm"], data["hi_mm"], axis))

        matched_ids: set[str] = set()
        persons_by_result: dict[str, set[str]] = defaultdict(set)
        for row, interval in zip(rows, intervals):
            if cohort and row.get("cohort") != cohort:
                continue
            if any(q.intersects(interval) for q in query_intervals):
                rid = row.get("record") or f"{row['person']}:{sku}:{version}:{row['week']}:{row['result']}"
                matched_ids.add(rid)
                persons_by_result[row["result"]].add(row["person"])
        return {
            "matched_records": len(matched_ids),
            "persons_by_result": {k: len(v) for k, v in persons_by_result.items()},
            "note": "只按区间相交匹配；相交不等于证明适合",
        }

    def _classify(self, axis: str, label: Interval, views: list[dict[str, Any]],
                  direct: list[dict[str, Any]], matching: dict[str, Any]) -> tuple[str, list[str]]:
        reasons: list[str] = []
        relations = []
        for view in views:
            data = view.get("converted_interval")
            if view.get("conversion_error"):
                reasons.append(view["conversion_error"])
                continue
            q = Interval(data["lo_mm"], data["hi_mm"], axis)
            relation = label.relation_to(q)
            view["table_relation"] = relation
            relations.append(relation)
            reasons.append(f"{view['source']}: {relation}")

        direct_results = {r["result"] for r in direct}
        if direct:
            reasons.append("存在同一受试者对该款该版的直接试穿记录")

        disconnected = bool(views) and all(v.get("table_relation") == "disconnected" for v in views)
        if disconnected and not direct:
            return "scale_disconnected", reasons

        matched_only = bool(views) and all(
            v.get("table_relation") == "matched_grade_only" for v in views
        )
        if matched_only and not direct:
            return "matched_grade_only", reasons + [
                "只能通过同一受试者的匹配试穿比较等级，不能给出毫米适配区间"
            ]

        if "适合" in direct_results and (direct_results & NEGATIVE_RESULTS):
            return "conflicting_evidence", reasons + ["同一受试者同时有适合与偏紧/偏松记录"]
        if direct_results & NEGATIVE_RESULTS:
            side = "偏紧" if "偏紧" in direct_results else "偏松"
            return "one_sided_signal", reasons + [f"直接记录只有{side}等单边信号，不能反推出完整适配范围"]
        if "适合" in direct_results:
            if relations and any(r in {"below", "above"} for r in relations) and any(
                r in {"inside", "overlaps"} for r in relations
            ):
                return "conflicting_measurements", reasons + [
                    "虽有直接适合记录，但不同实测来源与边界关系冲突，不能作无条件保证"
                ]
            if not relations or "inside" in relations or "overlaps" in relations:
                if "overlaps" in relations and "inside" not in relations:
                    return "intersection_only", reasons + ["直接适合但测量区间只与版次边界相交，保守按相交呈现"]
                return "fit_supported", reasons + ["直接适合且测点/区间在本版边界内"]
            return "conflicting_evidence", reasons + ["直接适合但测量落在版次边界外，需复核测量或版次"]
        if direct_results & UNKNOWN_RESULTS:
            return "unknown_only", reasons + ["试穿仅为单边未知，不能判定适合"]

        if not relations:
            return "insufficient_evidence", reasons + ["没有可连接到该量表的实测或试穿"]
        outside = {"below", "above"}
        intersecting = {"inside", "overlaps"}
        if any(r in outside for r in relations) and any(r in intersecting for r in relations):
            return "conflicting_measurements", reasons + [
                "不同实测来源分别落在边界内/相交区和边界外，必须并列来源，不能合并成单一保证"
            ]
        if all(r == "inside" for r in relations):
            persons = sum(matching.get("persons_by_result", {}).values())
            if persons == 0:
                return "table_supported_unvalidated", reasons + ["量表边界包含测量，但缺少相交试穿验证"]
            return "table_supported", reasons
        if any(r == "overlaps" for r in relations):
            return "intersection_only", reasons + ["测量区间与适配边界仅部分相交，只能承诺交集"]
        if all(r in {"below", "above"} for r in relations):
            side = "loose_risk" if "below" in relations else "tight_risk"
            return "one_sided_signal", reasons + [f"测量整体位于边界单侧：{side}"]
        return "conflicting_measurements", reasons + ["不同测量来源给出冲突关系，需并列呈现"]

    @staticmethod
    def _confidence(summary: dict[str, Any], validation: dict[str, Any],
                     direct: list[dict[str, Any]], status: str) -> tuple[float, str]:
        evidence = float(summary["suitable_wilson_lower95"] or 0.0)
        validation_score = float(validation.get("fit_binary_wilson_lower95") or 0.0)
        score = round(0.6 * evidence + 0.4 * validation_score, 3)
        if direct and any(r["result"] == "适合" for r in direct):
            score = max(score, 0.55)
        if status in {"scale_disconnected", "insufficient_evidence", "matched_grade_only"}:
            score = min(score, 0.15)
        if status == "intersection_only":
            score = min(score, 0.45)
        return score, credibility_level(score, summary["independent_persons"])

    def validate(self, sku: Optional[str] = None, version: Optional[str] = None,
                 cohort: Optional[str] = None, as_of: Optional[str] = None) -> dict[str, Any]:
        records = []
        agree_binary = 0
        agree_side = 0
        total = 0
        offsets = []
        tight_margins = []
        loose_margins = []
        for row in self.repo.table("fittings"):
            if sku and row["sku"] != sku:
                continue
            if version and row["version"] != version:
                continue
            if cohort and row.get("cohort") != cohort:
                continue
            if as_of:
                from .intervals import week_key
                if week_key(row["week"]) > week_key(as_of):
                    continue
            if not is_holdout_person(row["person"]):
                continue
            ref = self.repo.reference_for(row["person"], as_of)
            if not ref:
                continue
            axis = self.scales.axis(row["sku"], row["version"])
            field = AXIS_FIELDS[axis]
            x = ref.get(field)
            if x is None:
                continue
            label = self.scales.label_interval(row["sku"], row["version"])
            relation = label.relation_to(Interval(x, x, axis))
            expected = {"inside": "适合", "below": "偏松", "above": "偏紧"}[relation]
            total += 1
            binary = (relation == "inside" and row["result"] == "适合") or (
                relation != "inside" and row["result"] != "适合"
            )
            side = row["result"] == expected
            agree_binary += binary
            agree_side += side
            offsets.append(float(x) - label.midpoint)
            if row["result"] == "偏紧":
                tight_margins.append(float(x) - label.hi)
            if row["result"] == "偏松":
                loose_margins.append(label.lo - float(x))
            records.append({
                "person": row["person"], "sku": row["sku"], "version": row["version"],
                "cohort": row.get("cohort"), "measurement_mm": x,
                "table_relation": relation, "actual_result": row["result"],
                "binary_agreement": binary, "side_agreement": side,
            })
        return {
            "holdout_rule": "编号模5余4的受试者；同一人全部记录同时留出",
            "records": total,
            "persons": len({r["person"] for r in records}),
            "fit_binary_agreement": agree_binary,
            "fit_binary_rate": round(agree_binary / total, 3) if total else None,
            "fit_binary_wilson_lower95": round(wilson_lower(agree_binary, total), 3),
            "side_agreement": agree_side,
            "side_agreement_rate": round(agree_side / total, 3) if total else None,
            "mean_measurement_offset_from_label_midpoint_mm": (
                round(sum(offsets) / len(offsets), 2) if offsets else None
            ),
            "mean_tight_margin_above_upper_mm": (
                round(sum(tight_margins) / len(tight_margins), 2) if tight_margins else None
            ),
            "mean_loose_margin_below_lower_mm": (
                round(sum(loose_margins) / len(loose_margins), 2) if loose_margins else None
            ),
            "examples": records[:10],
        }

    def after_sales_check(self, sku: str, version: str,
                          as_of: Optional[str] = None) -> dict[str, Any]:
        """汇总同款同版的退换/受理事实，用于检查偏差但不伪造尺寸观察。"""

        exchanges = [
            e for e in self.repo.table("exchanges")
            if e["candidate"] == sku and e.get("candidate_version") == version
            and (as_of is None or week_key(e.get("booked")) <= week_key(as_of))
        ]
        rejection_ids = set()
        for h in self.repo.table("store_handover"):
            if as_of and week_key(h.get("received")) > week_key(as_of):
                continue
            if any(word in str(h.get("kind", "")) for word in ("拒绝", "拒收")):
                rejection_ids.add(h.get("exchange"))
        receipt_exchange = set()
        for r in self.repo.table("compensation_receipts"):
            if as_of and week_key(r.get("week")) > week_key(as_of):
                continue
            if r.get("state") == "已到账":
                receipt_exchange.add(r.get("exchange"))
        return {
            "exchanges": len(exchanges),
            "signed": sum(e.get("status") == "已签收" for e in exchanges),
            "accepted": sum(e.get("status") == "已受理" for e in exchanges),
            "in_transit_or_open": sum(e.get("status") in {"在途", "申请"} for e in exchanges),
            "rejected_or_refused": sum(e["exchange"] in rejection_ids for e in exchanges),
            "with_arrived_compensation": sum(e["exchange"] in receipt_exchange for e in exchanges),
            "bias_use": (
                "退换记录用于核对实际受理偏差；签收/到账不是尺寸测量或适合等级，"
                "没有新测量区间时不扩大毫米适配范围"
            ),
        }

    @staticmethod
    def status_text(status: str) -> str:
        return {
            "fit_supported": "有本版边界和直接适合记录支持",
            "table_supported": "量表区间包含测量且有相交试穿",
            "table_supported_unvalidated": "量表区间包含测量，但试穿验证不足",
            "intersection_only": "仅区间相交，只能呈现交集，不能保证适配",
            "one_sided_signal": "只有偏紧/偏松等单边信号，不能反推完整范围",
            "unknown_only": "只有单边未知记录，证据不足",
            "conflicting_evidence": "试穿证据相互冲突",
            "conflicting_measurements": "不同测量来源或场景相互冲突",
            "scale_disconnected": "量表之间没有共同测量或匹配试穿连接，不能推断",
            "matched_grade_only": "只有匹配试穿可连接，只能比较等级，不能换算毫米范围",
            "insufficient_evidence": "试穿数据不足",
        }.get(status, status)

    @staticmethod
    def counter_statement(status: str, guarantee: bool) -> str:
        if guarantee:
            return "可陈述现有证据支持该款该版适配；仍需保留测量来源与版次回执"
        if status == "preference":
            return "这是偏好变更；适配结论仅供参考，费用按偏好变更处理"
        return "证据只支持如实描述单边或交集，门店不得据此保证适配"
