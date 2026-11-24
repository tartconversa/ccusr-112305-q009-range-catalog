"""柜台/售后可直接调用的门面服务。

一个 ``CounterService`` 把资料仓储、量表图、适配证据、权益、配对和跨店流程
串成同一份可序列化结论。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional

from .cohorts import CohortService
from .fit import FitAdvisor
from .intervals import Interval, intersection_all, week_key
from .repository import Repository
from .rights import BenefitService
from .scales import ScaleService
from .workflow import WorkflowService


FIT_STRONG_STATUSES = {"fit_supported", "table_supported"}
FIT_WEAK_STATUSES = {
    "intersection_only", "one_sided_signal", "unknown_only",
    "table_supported_unvalidated", "conflicting_evidence", "conflicting_measurements",
    "matched_grade_only", "unknown_scale", "version_ambiguous",
}


class CounterService:
    def __init__(self, repo: "Repository | str | None" = None, data_dir: Optional[str] = None,
                 data_path: Optional[str] = None):
        if isinstance(repo, str):
            data_dir = repo
            repo = None
        data_dir = data_dir or data_path
        self.repo = repo or Repository.from_dir(data_dir) if data_dir else (repo or Repository())
        self.scales = ScaleService(self.repo)
        self.cohorts = CohortService(self.repo, self.scales)
        self.fit = FitAdvisor(self.repo, self.scales)
        self.rights = BenefitService(self.repo)
        self.workflow = WorkflowService(self.repo, self.scales, self.rights)

    # 资料更新 ----------------------------------------------------------
    def with_scale_rows(self, rows: list[dict[str, Any]]) -> "CounterService":
        """返回加入新款/更正尺码表后的新服务，不修改当前实例。"""

        return self.with_additional_records("scales", rows)

    def with_additional_records(self, table: str, rows: list[dict[str, Any]]) -> "CounterService":
        """加入回访、受理体验或单据后按相同规则重算。"""

        data = deepcopy(self.repo.tables)
        data.setdefault(table, []).extend(deepcopy(rows))
        return CounterService(Repository(data=data))

    def counter_slip(self, row: dict[str, Any]):
        from .counter import normalize_counter_record
        return normalize_counter_record(row).to_dict()

    def remaining_compensation(self, order_id: str) -> float:
        """凭证限额内当前可追加金额（只扣已到账回执）。"""

        return self.rights.additional_now(order_id)

    # 基础柜台建议 ------------------------------------------------------
    def size_advice(self, sku: str, version: Optional[str] = None,
                    person: Optional[str] = None, cohort: Optional[str] = None,
                    as_of: Optional[str] = None, measurement: Optional[Interval] = None,
                    allow_version_alternatives: bool = True) -> dict[str, Any]:
        if version is None:
            candidates = self.scales.version_candidates(sku, None, as_of)
            if not candidates:
                raise KeyError(f"未知款 {sku}")
            if len(candidates) == 1 or not allow_version_alternatives:
                version = candidates[-1]
            else:
                scenarios = [
                    self.fit.advise(sku, v, person=person, cohort=cohort,
                                    measurement=measurement, as_of=as_of)
                    for v in candidates
                ]
                labels = [self.scales.label_interval(sku, v) for v in candidates]
                robust = intersection_all(labels)
                return {
                    "sku": sku,
                    "version": None,
                    "version_state": "actual_version_unknown",
                    "scenarios": scenarios,
                    "boundaries": {v: self.scales.label_interval(sku, v).to_dict()
                                   for v in candidates},
                    "committable_range_across_versions": robust.to_dict() if robust else None,
                    "counter_statement": "实际发货版次不明，只能并列各版边界；跨版交集才可保守承诺",
                    "store_guarantee_allowed": False,
                }
        return self.fit.advise(sku, version, person=person, cohort=cohort,
                               measurement=measurement, as_of=as_of)

    def recommend_alternatives(self, person: str, as_of: Optional[str] = None,
                               cohort: Optional[str] = None,
                               require_guarantee: bool = False) -> list[dict[str, Any]]:
        versions: set[tuple[str, str]] = set()
        for row in self.repo.scale_rows():
            if as_of and week_key(row["publish_week"]) > week_key(as_of):
                continue
            versions.add((row["sku"], row["version"]))
        advice = [self.fit.advise(sku, version, person=person, cohort=cohort, as_of=as_of)
                  for sku, version in sorted(versions)]

        def rank(item: dict[str, Any]) -> tuple[int, float, int]:
            strong = item["status"] in FIT_STRONG_STATUSES
            guaranteed = bool(item.get("store_guarantee_allowed"))
            return (
                2 if guaranteed else 1 if strong else 0,
                float(item.get("confidence") or 0),
                item.get("cohort_evidence", {}).get("independent_persons", 0),
            )

        advice.sort(key=rank, reverse=True)
        if require_guarantee:
            advice = [a for a in advice if a.get("store_guarantee_allowed")]
        return advice

    # 原因与版次纠正 ----------------------------------------------------
    def classify_cause(self, order_id: Optional[str] = None,
                       claimed_issue: Optional[str] = None,
                       person: Optional[str] = None,
                       original_sku: Optional[str] = None,
                       original_version: Optional[str] = None,
                       candidate_sku: Optional[str] = None,
                       candidate_version: Optional[str] = None,
                       as_of: Optional[str] = None,
                       explicit_proof: bool = False) -> dict[str, Any]:
        if order_id:
            order = self.repo.order(order_id)
            person = person or order.get("holder")
            original_sku = original_sku or order["sku"]
            original_version = original_version or order["version"]
        claim = claimed_issue or "适配不明"
        if claim in {"偏好变更", "客户单纯改变偏好"}:
            return {
                "code": "customer_preference",
                "reason_category": "客户单纯改变偏好",
                "text": "客户单纯改变偏好",
                "policy_issue": "偏好变更",
                "proven_guide_error": False,
                "proof": [],
            }

        proof = self.label_error_proof(
            person=person, sku=original_sku, version=original_version,
            candidate_sku=candidate_sku, candidate_version=candidate_version,
            as_of=as_of, explicit_proof=explicit_proof,
        )
        if proof["proven"]:
            return {
                "code": "original_guide_error",
                "reason_category": "原说明有误",
                "text": "原说明/标注有误且已有证据",
                "policy_issue": "标注争议",
                "proven_guide_error": True,
                "proof": proof,
            }
        return {
            "code": "insufficient_fit_evidence",
            "reason_category": "试穿数据不足",
            "text": "试穿或测量证据不足，不能认定原说明有误",
            "policy_issue": "适配不明",
            "proven_guide_error": False,
            "proof": proof,
        }

    def label_error_proof(self, person: Optional[str], sku: Optional[str],
                          version: Optional[str], candidate_sku: Optional[str] = None,
                          candidate_version: Optional[str] = None,
                          as_of: Optional[str] = None,
                          explicit_proof: bool = False) -> dict[str, Any]:
        proof_items: list[str] = []
        flip = None
        correction_row = None
        if sku and version:
            newer = [r for r in self.scales.versions(sku, as_of) if r.get("replace") == version]
            if newer:
                correction_row = newer[-1]
                proof_items.append(f"官方新版 {sku}/{correction_row['version']} 标注 replace={version}")
                if person:
                    old = self.fit.advise(sku, version, person=person, as_of=as_of)
                    new = self.fit.advise(sku, correction_row["version"], person=person, as_of=as_of)
                    flip = {
                        "old_status": old["status"],
                        "new_status": new["status"],
                        "flipped": self._strength(old["status"]) != self._strength(new["status"]),
                        "old_range": old["fit_range"]["label_range"],
                        "new_range": new["fit_range"]["label_range"],
                    }
                    if flip["flipped"]:
                        proof_items.append("同一测量来源下，版次纠正导致适配判断翻转")

        validation = self.fit.validate(sku, version, as_of=as_of) if sku and version else None
        systematic = bool(
            validation and validation.get("records", 0) >= 8
            and (validation.get("side_agreement_rate") or 1) <= 0.35
        )
        if systematic:
            proof_items.append("留出共同试穿显示该版侧别一致率显著偏低，存在系统性偏差信号")

        proven = bool(explicit_proof) or (
            correction_row is not None and flip is not None and flip["flipped"]
            and (explicit_proof or systematic or self._direct_corroboration(person, sku, version, as_of))
        )
        if explicit_proof:
            proof_items.append("调用方提交了可核验的标注错误证明")
        return {
            "proven": proven,
            "official_correction": correction_row,
            "version_flip": flip,
            "systematic_bias_signal": systematic,
            "heldout_validation": validation,
            "direct_corroboration": self._direct_corroboration(person, sku, version, as_of),
            "items": proof_items,
            "standard": "官方更正+适配翻转+直接试穿/留出偏差佐证，或调用方明确提交已核验证明",
        }

    def _direct_corroboration(self, person: Optional[str], sku: Optional[str],
                              version: Optional[str], as_of: Optional[str]) -> bool:
        if not person or not sku or not version:
            return False
        direct = self.fit.direct_records(person, sku, version, as_of)
        return any(r.get("result") in {"适合", "偏紧", "偏松"} for r in direct)

    @staticmethod
    def _strength(status: str) -> int:
        if status in FIT_STRONG_STATUSES:
            return 2
        if status in FIT_WEAK_STATUSES:
            return 1
        return 0

    # 新申请综合认定 ----------------------------------------------------
    def assess_application(self, order_id: str, candidate_sku: str,
                           candidate_version: Optional[str] = None,
                           issue: Optional[str] = None, as_of: Optional[str] = None,
                           freight_cny: float = 0.0, gap_cny: float = 0.0,
                           quote_provided: bool = False,
                           measurement: Optional[Interval] = None,
                           exchange_id: Optional[str] = None,
                           explicit_label_error_proof: bool = False,
                           counter_record: Optional[dict[str, Any]] = None,
                           payment_arranged: bool = False) -> dict[str, Any]:
        order = self.repo.order(order_id)
        exchange = self.repo.exchange(exchange_id) if exchange_id else None
        if exchange:
            issue = issue or exchange.get("issue")
            candidate_sku = candidate_sku or exchange["candidate"]
            candidate_version = candidate_version or exchange.get("candidate_version")
        claimed_issue = issue or "适配不明"
        counter_slip = None
        if counter_record is not None:
            counter_slip = self.counter_slip(counter_record)
            candidate_keys = {(c.get("sku"), c.get("version")) for c in counter_slip["candidates"]}
            counter_slip["current_candidate_recorded"] = any(
                sku == candidate_sku for sku, _ in candidate_keys
            )

        version_state = "known"
        version_scenarios = None
        if not candidate_version or not self.repo.has_scale(candidate_sku, candidate_version):
            candidates = self.scales.version_candidates(candidate_sku, candidate_version, as_of)
            if candidate_version is None and len(candidates) > 1:
                version_state = "actual_version_unclear"
                version_scenarios = [
                    self.fit.advise(candidate_sku, v, person=order["holder"], as_of=as_of,
                                    measurement=measurement)
                    for v in candidates
                ]
                labels = [self.scales.label_interval(candidate_sku, v) for v in candidates]
                robust = intersection_all(labels)
                fit = {
                    "sku": candidate_sku,
                    "version": None,
                    "status": "version_ambiguous",
                    "store_guarantee_allowed": False,
                    "scenarios": version_scenarios,
                    "committable_range_across_versions": robust.to_dict() if robust else None,
                }
            elif candidates:
                candidate_version = candidates[0]
                fit = self.fit.advise(candidate_sku, candidate_version, person=order["holder"],
                                      as_of=as_of, measurement=measurement)
            else:
                fit = {"sku": candidate_sku, "version": candidate_version,
                       "status": "unknown_scale", "store_guarantee_allowed": False}
        else:
            fit = self.fit.advise(candidate_sku, candidate_version, person=order["holder"],
                                  as_of=as_of, measurement=measurement)

        if counter_slip is not None and counter_slip.get("measurement_axis") and fit.get("axis"):
            counter_slip["measurement_method_matches_candidate_axis"] = (
                counter_slip["measurement_axis"] == fit["axis"]
            )

        cause = self.classify_cause(
            order_id=order_id, claimed_issue=claimed_issue, as_of=as_of,
            candidate_sku=candidate_sku, candidate_version=candidate_version,
            explicit_proof=explicit_label_error_proof,
        )
        charge = self.rights.assess_new_charge(
            order_id, cause["policy_issue"], freight_cny, gap_cny
        )
        pair = self.workflow.pair_check(order_id, candidate_sku, candidate_version, as_of)
        routing = self.workflow.exchange_routing(exchange_id, as_of) if exchange_id else None
        if routing:
            version_gate = routing["gates"][1].get("version_state") or {}
            if version_gate and not version_gate.get("resolved", True):
                version_state = "receipt_pending"

        fit_met = fit.get("status") in FIT_STRONG_STATUSES and bool(fit.get("store_guarantee_allowed"))
        pair_met = (not pair.get("paired_voucher")) or bool(pair.get("pair_compatible"))
        workflow_met = routing is None or bool(routing.get("all_confirmed"))
        benefit = self._benefit_readiness(
            cause["policy_issue"], charge, quote_provided, payment_arranged
        )
        remedies = {
            "free_price_gap_available": cause["policy_issue"] == "标注争议",
            "freight_compensation_available": cause["policy_issue"] == "标注争议",
            "buyer_pays_freight_and_gap": cause["policy_issue"] == "偏好变更",
            "merchant_brand_allocation_pending": cause["policy_issue"] == "适配不明",
            "voucher_limit_covers_quote": charge["excess_or_negotiation_cny"] == 0,
            "note": "责任方资格与凭证限额是两件事；限额不足部分仍需协商，不把已用权益清零",
        }
        fully_supported = bool(
            candidate_version and fit_met and pair_met and benefit["ready"] and workflow_met
        )

        negotiation: list[str] = []
        if not candidate_version or version_state in {"actual_version_unclear", "receipt_pending"}:
            negotiation.append("实际发货版次回执未齐：需版次回执后才能确定承诺边界")
        if not fit_met:
            negotiation.append("候选款没有达到可承诺适配：" + fit.get("status_text", fit.get("status", "未知")))
        if pair.get("paired_voucher") and not pair.get("pair_compatible"):
            negotiation.append(pair.get("reason", "左右配合范围不满足"))
        if not benefit["ready"]:
            negotiation.append(benefit["reason"])
        if routing and not routing["all_confirmed"]:
            negotiation.extend(routing["blockers"])

        counter_answer = self._counter_answer(fit, cause, charge, pair, fully_supported)
        counter_answer["measurement_sets"] = fit.get("measurement_provenance_detail", [])
        if routing:
            counter_answer["sending_store"] = routing.get("sending_store")
            counter_answer["intake_store"] = routing.get("intake_store")
            counter_answer["required_confirmations"] = [
                {"gate": g["gate"], "party": g["required_party"], "satisfied": g["satisfied"]}
                for g in routing["gates"]
            ]
            counter_answer["customer_notice"] = routing.get("customer_notice")

        return {
            "order_id": order_id,
            "voucher": self.rights.voucher_id(order_id),
            "holder": order.get("holder"),
            "original_shipment_spec": {
                "sku": order["sku"], "version": order["version"],
                "side": order.get("side"), "purchase_week": order.get("purchase"),
                "state": order.get("state"),
            },
            "original_promise": {
                "shipped_version_controls_history": True,
                "must_not_overwrite_with_latest_chart": True,
                "existing_exchange": exchange_id,
                "commercial_issue_recorded": claimed_issue,
            },
            "candidate": {"sku": candidate_sku, "version": candidate_version},
            "counter_slip": counter_slip,
            "version_state": version_state,
            "fit": fit,
            "cause": cause,
            "cost_and_benefit": charge,
            "benefit_readiness": benefit,
            "remedies": remedies,
            "pair": pair,
            "routing": routing,
            "contractually_processable": True,
            "fit_requirement_met": fit_met,
            "pair_requirement_met": pair_met,
            "workflow_requirement_met": workflow_met,
            "benefit_requirement_met": benefit["ready"],
            "fully_supported_alternative": fully_supported,
            "negotiation_required": not fully_supported,
            "pending_negotiation_items": negotiation,
            "counter_answer": counter_answer,
        }

    @staticmethod
    def _benefit_readiness(policy_issue: str, charge: dict[str, Any],
                           quote_provided: bool,
                           payment_arranged: bool = False) -> dict[str, Any]:
        if policy_issue == "偏好变更":
            if payment_arranged:
                return {"ready": True, "mode": "customer_pays_confirmed",
                        "reason": "已确认由买方承担运费/差额并完成支付约定"}
            return {"ready": False, "mode": "customer_pays",
                    "reason": "偏好变更的运费和差额由买方承担；需顾客确认支付后才算权益/支付安排齐备"}
        if policy_issue == "适配不明":
            return {"ready": False, "mode": "pending_review",
                    "reason": "适配原因未证实，运费/差额方待复核，不能先行承诺商家或品牌承担"}
        # 标注争议已经被证明。
        if not quote_provided:
            return {"ready": False, "mode": "quote_required",
                    "reason": "缺少新运费/差额报价，无法判断凭证剩余限额是否足够"}
        if charge["excess_or_negotiation_cny"] > 0:
            return {"ready": False, "mode": "limit_shortfall",
                    "reason": f"凭证剩余限额不足，尚有 {charge['excess_or_negotiation_cny']} 元需协商"}
        return {"ready": True, "mode": "policy_and_limit_ok",
                "reason": "标注争议责任明确且凭证剩余限额可覆盖本次报价"}

    @staticmethod
    def _counter_answer(fit: dict[str, Any], cause: dict[str, Any],
                        charge: dict[str, Any], pair: dict[str, Any],
                        supported: bool) -> dict[str, Any]:
        return {
            "measurement_provenance": fit.get("measurement_provenance"),
            "fit_statement": fit.get("counter_statement"),
            "cause": cause["text"],
            "reason_category": cause.get("reason_category"),
            "policy_issue": cause.get("policy_issue"),
            "freight_bearer": charge["allocation"]["freight"]["bearer"],
            "gap_bearer": charge["allocation"]["price_gap"]["bearer"],
            "pair_statement": pair.get("reason"),
            "ready_to_commit": supported,
            "store_must_not_guarantee": not fit.get("store_guarantee_allowed", False),
        }

    # 新版/更正表到站后的影响 ------------------------------------------
    def chart_impact(self, as_of: Optional[str] = None) -> dict[str, Any]:
        releases = [r for r in self.repo.scale_rows() if r.get("replace")]
        order_impacts = []
        for order in self.repo.table("orders"):
            impact = self._record_impact(
                "order", order["order"], order.get("holder"), order["sku"], order["version"],
                order.get("state"), order.get("purchase"), as_of
            )
            order_impacts.append(impact)
        exchange_impacts = []
        for ex in self.repo.table("exchanges"):
            impact = self._record_impact(
                "exchange", ex["exchange"], self.repo.order(ex["order"]).get("holder"),
                ex["candidate"], ex.get("candidate_version"), ex.get("status"),
                ex.get("booked"), as_of, order_id=ex["order"]
            )
            exchange_impacts.append(impact)
        return {
            "rule": (
                "待发货可按新版复核；在途/版次不明需双边界和回执；已签收历史仍按实际发货版次解释，"
                "更正只有在证据成立时产生新责任，不溯及既往地抹掉已用权益"
            ),
            "releases": releases,
            "orders": order_impacts,
            "exchanges": exchange_impacts,
        }

    def _record_impact(self, kind: str, record_id: str, person: str, sku: str,
                       version: Optional[str], state: str, event_week: Optional[str],
                       as_of: Optional[str], order_id: Optional[str] = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "record_type": kind, "id": record_id, "person": person, "sku": sku,
            "version": version, "state": state, "event_week": event_week,
        }
        if not version or not self.repo.has_scale(sku, version):
            candidates = self.scales.version_candidates(sku, version, as_of or event_week)
            result["version_state"] = "unclear"
            result["possible_versions"] = candidates
            result["boundaries"] = {v: self.scales.label_interval(sku, v).to_dict() for v in candidates}
            labels = [self.scales.label_interval(sku, v) for v in candidates]
            common = intersection_all(labels)
            result["committable_range"] = common.to_dict() if common else None
            result["advice"] = [
                self.fit.advise(sku, v, person=person, as_of=as_of) for v in candidates
            ]
            return result

        result["version_state"] = "actual_known"
        old_advice = self.fit.advise(sku, version, person=person, as_of=as_of)
        result["current_version_advice"] = old_advice
        newer = [r for r in self.scales.versions(sku, as_of) if r.get("replace") == version]
        if newer:
            new_version = newer[-1]["version"]
            new_advice = self.fit.advise(sku, new_version, person=person, as_of=as_of)
            result["correction"] = {
                "new_version": new_version,
                "old_boundary": old_advice["fit_range"]["label_range"],
                "new_boundary": new_advice["fit_range"]["label_range"],
                "old_status": old_advice["status"],
                "new_status": new_advice["status"],
                "flipped": self._strength(old_advice["status"]) != self._strength(new_advice["status"]),
                "new_advice": new_advice,
            }
        if state in {"待发货", "申请"}:
            result["handling"] = "尚未发货/未受理：可 hold 并发出版本复核或更换建议"
        elif state in {"在途", "已受理"}:
            result["handling"] = "在途/已受理：保留原承诺，等待实际版次与跨店确认；双边界并列"
        elif state == "已签收":
            result["handling"] = "已签收：历史适配仍按实际发货版次；若更正证据成立，可追加责任但不重置已用限额"
        else:
            result["handling"] = "按原有效单据继续追踪"
        if order_id:
            result["voucher"] = self.rights.voucher_id(order_id)
            result["benefit"] = self.rights.ledger(order_id)
        return result

    # 退换/实际受理体验 -----------------------------------------------
    def exchange_experience(self, sku: Optional[str] = None,
                            version: Optional[str] = None) -> dict[str, Any]:
        checks = []
        for ex in self.repo.table("exchanges"):
            if sku and ex["candidate"] != sku:
                continue
            if version and ex.get("candidate_version") != version:
                continue
            order = self.repo.order(ex["order"])
            routing = self.workflow.exchange_routing(ex["exchange"])
            has_rejection = bool(routing.get("rejection_records"))
            terminal_success = ex.get("status") == "已签收" and routing["all_confirmed"]
            terminal_failure = has_rejection
            as_booked = None
            try:
                as_booked = self.fit.advise(
                    ex["candidate"], ex["candidate_version"], person=order["holder"],
                    as_of=ex.get("booked")
                )
            except Exception:
                as_booked = None
            checks.append({
                "exchange": ex["exchange"],
                "order": ex["order"],
                "person": order["holder"],
                "candidate": {"sku": ex["candidate"], "version": ex.get("candidate_version")},
                "issue": ex.get("issue"),
                "status": ex.get("status"),
                "terminal_success": terminal_success,
                "terminal_failure": terminal_failure,
                "open": not terminal_success and not terminal_failure,
                "fit_status_at_booking": as_booked.get("status") if as_booked else "version_unknown",
                "fit_strong_at_booking": bool(as_booked and as_booked.get("status") in FIT_STRONG_STATUSES),
                "routing_confirmed": routing["all_confirmed"],
                "interpretation": (
                    "签收/到账是受理事实，不是新的尺寸测量；不能据此扩大适配毫米区间，"
                    "只能更新流程/客诉可信度"
                ),
            })
        summary = {
            "checks": checks,
            "total": len(checks),
            "terminal_success": sum(c["terminal_success"] for c in checks),
            "terminal_failure": sum(c["terminal_failure"] for c in checks),
            "open": sum(c["open"] for c in checks),
            "fit_range_update": "没有新增测量区间或试穿等级，因此毫米适配范围不变",
        }
        if checks:
            summary["operational_success_rate"] = round(
                summary["terminal_success"] / max(1, summary["terminal_success"] + summary["terminal_failure"]), 3
            ) if summary["terminal_success"] + summary["terminal_failure"] else None
        return summary

    def review_accepted_exchanges(self, as_of: Optional[str] = None,
                                 freight_quotes: Optional[dict[str, float]] = None,
                                 gap_quotes: Optional[dict[str, float]] = None) -> list[dict[str, Any]]:
        """复查所有“已受理”但可能缺少适配/权益/确认替代的换货。"""

        freight_quotes = freight_quotes or {}
        gap_quotes = gap_quotes or {}
        reviews = []
        for ex in self.repo.table("exchanges"):
            if ex.get("status") != "已受理":
                continue
            result = self.assess_application(
                ex["order"], ex["candidate"], ex.get("candidate_version"),
                exchange_id=ex["exchange"], as_of=as_of,
                freight_cny=freight_quotes.get(ex["exchange"], float(ex.get("cost") or 0)),
                gap_cny=gap_quotes.get(ex["exchange"], 0.0),
                quote_provided=ex["exchange"] in freight_quotes or ex["exchange"] in gap_quotes,
            )
            reviews.append({
                "exchange": ex["exchange"],
                "fully_supported_alternative": result["fully_supported_alternative"],
                "pending_negotiation_items": result["pending_negotiation_items"],
                "counter_answer": result["counter_answer"],
            })
        return reviews

    def resolve_version_receipt(self, sku: str, actual_version: str, person: str,
                                as_of: Optional[str] = None,
                                possible_versions: Optional[list[str]] = None) -> dict[str, Any]:
        """实际版次回执到齐后，比较哪些旧判断被推翻、哪些仍成立。"""

        possible_versions = possible_versions or self.scales.version_candidates(sku, None, as_of)
        scenarios = {
            v: self.fit.advise(sku, v, person=person, as_of=as_of) for v in possible_versions
            if self.repo.has_scale(sku, v)
        }
        actual = scenarios[actual_version]
        comparisons = []
        for version, advice in scenarios.items():
            if version == actual_version:
                continue
            relation_flip = (
                {v.get("table_relation") for v in advice["measurement_views"]}
                != {v.get("table_relation") for v in actual["measurement_views"]}
            )
            overturned = advice["status"] != actual["status"] or relation_flip
            comparisons.append({
                "alternative_version": version,
                "alternative_status": advice["status"],
                "actual_status": actual["status"],
                "overturned": overturned,
                "maintained": not overturned,
                "statement": (
                    "回执推翻了按另一版边界得到的建议/关系"
                    if overturned else "即使版次不同，粗粒度结论仍可维持"
                ),
            })
        labels = [self.scales.label_interval(sku, v) for v in possible_versions]
        ambiguous_common = intersection_all(labels)
        actual_range = self.scales.label_interval(sku, actual_version)
        return {
            "sku": sku,
            "actual_version": actual_version,
            "actual_advice": actual,
            "comparisons": comparisons,
            "pre_receipt_common_boundary": ambiguous_common.to_dict() if ambiguous_common else None,
            "post_receipt_boundary": actual_range.to_dict(),
            "surviving_facts": [
                "客户身份和测量来源不变",
                "没有新测量时不得补写过去缺失部位",
                "原订单/原换货凭证和已用权益不变",
            ],
        }

    def correction_response(self, order_id: str, new_freight_quote: float = 0.0,
                            new_gap_quote: float = 0.0, quote_provided: bool = True,
                            as_of: Optional[str] = None,
                            explicit_label_error_proof: bool = False) -> dict[str, Any]:
        """版次纠正翻转适配时，给候选替换和两方新增费用责任。"""

        order = self.repo.order(order_id)
        proof = self.classify_cause(
            order_id=order_id, claimed_issue="标注争议", as_of=as_of,
            explicit_proof=explicit_label_error_proof,
        )
        alternatives = self.recommend_alternatives(order["holder"], as_of=as_of)
        supported = [a for a in alternatives if a.get("store_guarantee_allowed")
                     or a.get("status") in FIT_STRONG_STATUSES]
        policy_issue = "标注争议" if proof["proven_guide_error"] else "适配不明"
        charge = self.rights.assess_new_charge(
            order_id, policy_issue,
            new_freight_quote, new_gap_quote
        )
        hypothetical = self.rights.allocate_costs(
            "标注争议", new_freight_quote, new_gap_quote
        )
        return {
            "order_id": order_id,
            "voucher": self.rights.voucher_id(order_id),
            "correction_proof": proof,
            "candidate_replacements": supported[:5],
            "all_ranked_alternatives": alternatives,
            "incremental_responsibility": charge["allocation"],
            "if_label_error_proven_responsibility": hypothetical,
            "responsibility_explanation": (
                "当前证据未达标时先挂待复核；若官方更正+翻转/佐证被认可，"
                "新增运费由商家、差额由品牌承担，买方不承担该两项"
            ),
            "benefit_capacity": {
                "shared_limit_cny": charge["shared_limit_cny"],
                "verified_paid_cny": charge["verified_paid_cny"],
                "additional_now_cny": charge["additional_now_cny"],
                "contract_coverable_now_cny": charge["contract_coverable_now_cny"],
                "excess_or_negotiation_cny": charge["excess_or_negotiation_cny"],
            },
            "used_benefits_remain_effective": self.rights.ledger(order_id)["used_benefits_persist"],
            "note": "新款更合适不会把已到账补偿或已签收款式重置为未使用",
        }

    def counter_dashboard(self, as_of: Optional[str] = None) -> dict[str, Any]:
        """柜台常用 ID 的汇总，便于 CLI 和人工核对。"""

        return {
            "measurement_sets_U014_as_of_W10": self.scales.measurement_sets("U014", "W10"),
            "measurement_sets_U014_current": self.scales.measurement_sets("U014", as_of),
            "alternatives_U014_W10": self.recommend_alternatives("U014", "W10"),
            "voucher_O1": self.rights.ledger("O1"),
            "routing_E1": self.workflow.exchange_routing("E1"),
            "routing_E2": self.workflow.exchange_routing("E2"),
            "application_E1_B1V3": self.assess_application(
                "O1", "B1", "V3", exchange_id="E1", as_of="W13"
            ),
            "application_E3": self.assess_application(
                "O3", "S1", "V1", exchange_id="E3", as_of="W13"
            ),
            "application_E4": self.assess_application(
                "O4", "C1", "V5", exchange_id="E4", as_of="W13"
            ),
            "accepted_reviews": self.review_accepted_exchanges("W13"),
            "exchange_experience": self.exchange_experience(),
            "chart_impact": self.chart_impact(as_of),
            "cohort_distributions": self.cohorts.all_distributions(),
        }
