"""左右配对、跨店交接、版次回执和确认门槛。"""

from __future__ import annotations

from typing import Any, Optional

from .intervals import Interval, week_key
from .repository import Repository
from .scales import ScaleService
from .rights import BenefitService


ACCEPTANCE_KINDS = {"柜台接收", "寄入接收", "接收", "签收"}
CONFIRMATION_KINDS = {"原店认可", "替换品确认", "版次回执", "寄出店确认"}
REJECTION_WORDS = ("拒绝", "拒收", "退回失败")


class WorkflowService:
    def __init__(self, repo: Repository, scales: ScaleService, rights: BenefitService):
        self.repo = repo
        self.scales = scales
        self.rights = rights

    # 配对件 -----------------------------------------------------------
    def current_style(self, order_id: str, as_of: Optional[str] = None) -> dict[str, Any]:
        order = self.repo.order(order_id)
        signed = [e for e in self.repo.exchanges_for_order(order_id) if e.get("status") == "已签收"]
        if as_of:
            signed = [e for e in signed if week_key(e.get("booked")) <= week_key(as_of)]
        if signed:
            latest = sorted(signed, key=lambda e: str(e.get("booked", "")))[-1]
            return {"sku": latest["candidate"], "version": latest.get("candidate_version"),
                    "source": f"exchange:{latest['exchange']}:已签收"}
        return {"sku": order["sku"], "version": order["version"], "source": f"order:{order_id}:原发货"}

    def pair_check(self, order_id: str, candidate_sku: str,
                   candidate_version: Optional[str],
                   as_of: Optional[str] = None) -> dict[str, Any]:
        order = self.repo.order(order_id)
        partner_id = order.get("partner")
        rule = self.repo.commercial_rule("适配不明")
        if not partner_id:
            return {"paired_voucher": False, "pair_required": False,
                    "note": "单件/双数订单，无左右凭证配对限制"}

        partner_style = self.current_style(partner_id, as_of)
        result: dict[str, Any] = {
            "paired_voucher": True,
            "order": order_id,
            "partner_order": partner_id,
            "candidate": {"sku": candidate_sku, "version": candidate_version},
            "partner_current_style": partner_style,
            "commercial_partial_allowed": bool(rule.get("allowed_pair_overlap", True)),
        }
        if not candidate_version or not self.repo.has_scale(candidate_sku, candidate_version):
            result.update({"pair_overlap": None, "pair_compatible": False,
                           "reason": "候选版次未确认，不能核对两只配合范围"})
            return result
        if not partner_style.get("version") or not self.repo.has_scale(
                partner_style["sku"], partner_style["version"]):
            result.update({"pair_overlap": None, "pair_compatible": False,
                           "reason": "配对件当前版次未知，不能核对配合范围"})
            return result

        a = self.scales.label_interval(candidate_sku, candidate_version)
        b = self.scales.label_interval(partner_style["sku"], partner_style["version"])
        result["candidate_axis"] = a.axis
        result["partner_axis"] = b.axis
        if a.axis != b.axis:
            result.update({"pair_overlap": None, "pair_compatible": False,
                           "reason": "两只不在同一测量量表上，无法计算配合区间；需人工确认成套性"})
            return result
        overlap = a.intersection(b)
        result["pair_overlap"] = overlap.to_dict() if overlap else None
        result["overlap_width_mm"] = overlap.width if overlap else 0.0
        result["pair_compatible"] = bool(overlap) and bool(rule.get("allowed_pair_overlap", True))
        result["positive_width_required_for_robust_pair"] = (overlap.width > 0 if overlap else False)
        result["reason"] = (
            (
                "配合范围有正宽度交集，允许按协议部分换货；但仍需客户实测/试穿证据证明两只都适合"
                if overlap.width > 0 else
                "配合范围仅在边界点相接；数学上相交，实际门店应视为极弱配合，不能据此保证"
            )
            if overlap else "候选款与配对件范围不相交，不能只换这一只"
        )
        result["merchant_acceptance_promise"] = self._acceptance_promise(order_id, as_of)
        return result

    def _acceptance_promise(self, order_id: str,
                            as_of: Optional[str] = None) -> list[dict[str, Any]]:
        promises = []
        order_exchanges = {e["exchange"] for e in self.repo.exchanges_for_order(order_id)}
        for row in self.repo.table("store_handover"):
            if row.get("exchange") not in order_exchanges:
                continue
            if as_of and week_key(row.get("received")) > week_key(as_of):
                continue
            if row.get("kind") in {"原店认可", "替换品确认"} and row.get("units", 0) > 0:
                promises.append(row)
        return promises

    # 跨店流程 ---------------------------------------------------------
    def effective_replacement(self, exchange_id: str,
                              as_of: Optional[str] = None) -> dict[str, Any]:
        """返回接收方已接受的实际替换规格。

        只有申请/等待事件或被拒绝的新版不能覆盖原承诺；空 version 也不能升级成最新版。
        """

        exchange = self.repo.exchange(exchange_id)
        handovers = self.repo.handover_for_exchange(exchange_id, as_of)
        kinds = ACCEPTANCE_KINDS | CONFIRMATION_KINDS
        accepted = [
            h for h in handovers
            if h.get("kind") in kinds and int(h.get("units") or 0) > 0
            and h.get("version")
            and not any(word in str(h.get("kind", "")) for word in REJECTION_WORDS)
        ]
        if accepted:
            latest = accepted[-1]
            return {"sku": latest["sku"], "version": latest["version"],
                    "units": latest.get("units"), "source_record": latest["record"],
                    "kind": latest["kind"], "promise_changed_by_receiver": True}
        return {"sku": exchange["candidate"], "version": exchange.get("candidate_version"),
                "units": None, "source_record": None,
                "promise_changed_by_receiver": False}

    def exchange_routing(self, exchange_id: str,
                         as_of: Optional[str] = None) -> dict[str, Any]:
        exchange = self.repo.exchange(exchange_id)
        order = self.repo.order(exchange["order"])
        handovers = self.repo.handover_for_exchange(exchange_id, as_of)
        receipts = self.repo.receipts_for_exchange(exchange_id, as_of)

        sending_store = exchange.get("vendor")
        intake_store = self._intake_store(exchange, handovers)
        rejection_events = [h for h in handovers
                            if any(word in str(h.get("kind", "")) for word in REJECTION_WORDS)
                            or int(h.get("units") or 0) <= 0 and h.get("kind") not in {"等待回货"}]
        accepted = [h for h in handovers
                    if h.get("kind") in ACCEPTANCE_KINDS and int(h.get("units") or 0) > 0
                    and not any(word in str(h.get("kind", "")) for word in REJECTION_WORDS)]
        confirmed = [h for h in handovers
                     if h.get("kind") in CONFIRMATION_KINDS and int(h.get("units") or 0) > 0]
        signoffs = [h for h in handovers if h.get("kind") == "签收" and int(h.get("units") or 0) > 0]
        # 已完成的“柜台接收 -> 签收”链可视为替换品被确认/接收；但它不能替代财务确认。
        completed_chain = bool(accepted and signoffs)
        dispatch_evidence = confirmed + (signoffs if completed_chain else [])
        version_issues = self._version_gate(exchange, handovers)

        arrived = [r for r in receipts if r.get("state") == "已到账"]
        pending_finance = [r for r in receipts if r.get("state") != "已到账"]
        finance_ok = not pending_finance and (
            not exchange.get("cost") or sum(float(r["cny"]) for r in arrived) >= float(exchange["cost"])
            or exchange.get("issue") == "偏好变更" and not arrived
        )
        # 偏好变更在没有回执时表示待买方付款，而不是财务已确认。
        if exchange.get("issue") == "偏好变更" and not arrived and not receipts:
            finance_ok = False

        gates = [
            {
                "gate": "寄入店接收",
                "required_party": intake_store or "寄入/柜台接收店",
                "satisfied": bool(accepted),
                "evidence": [h["record"] for h in accepted],
            },
            {
                "gate": "寄出店确认替换品/版次",
                "required_party": sending_store or "寄出店",
                "satisfied": bool(dispatch_evidence) and version_issues["resolved"],
                "evidence": [h["record"] for h in dispatch_evidence],
                "version_state": version_issues,
                "completed_signoff_chain": completed_chain,
            },
            {
                "gate": "财务确认差额/费用",
                "required_party": "财务",
                "satisfied": finance_ok,
                "evidence": [r["receipt"] for r in arrived],
                "pending": [r["receipt"] for r in pending_finance],
            },
        ]
        all_satisfied = all(g["satisfied"] for g in gates)
        closed = exchange.get("status") == "已签收"
        blockers = []
        if not accepted:
            blockers.append("缺少寄入店有效接收；拒绝收件或 units=0 不能视为接收")
        if rejection_events:
            blockers.append("存在拒绝/零数量事件: " + ",".join(h["record"] for h in rejection_events))
        if not dispatch_evidence:
            blockers.append("缺少寄出店对替换品的确认或完整签收链")
        if not version_issues["resolved"]:
            blockers.append(version_issues["reason"])
        if not gates[2]["satisfied"]:
            blockers.append("财务差额/费用尚未确认或到账")

        effective_kinds = ACCEPTANCE_KINDS | CONFIRMATION_KINDS
        accepted_combinations = [
            {"record": h["record"], "sku": h.get("sku"), "version": h.get("version"),
             "units": h.get("units"), "kind": h.get("kind"), "acceptor": h.get("acceptor")}
            for h in handovers
            if h.get("kind") in effective_kinds and int(h.get("units") or 0) > 0
            and not any(word in str(h.get("kind", "")) for word in REJECTION_WORDS)
        ]
        return {
            "exchange": exchange_id,
            "order": exchange["order"],
            "voucher": self.rights.voucher_id(exchange["order"]),
            "status": exchange.get("status"),
            "sending_store": sending_store,
            "intake_store": intake_store,
            "participating_stores": sorted({h.get("issuer") for h in handovers if h.get("issuer")}
                                           | {h.get("acceptor") for h in handovers if h.get("acceptor")}),
            "gates": gates,
            "all_confirmed": all_satisfied,
            "pickup_already_completed": closed,
            "pickup_notice_gate_satisfied": all_satisfied,
            "may_notify_pickup": all_satisfied and not closed,
            "customer_notice": (
                "该单已签收，流程关闭；无需再发新的取件通知"
                if closed and all_satisfied else
                "三道确认已齐，可通知顾客新的取件条件"
                if all_satisfied else
                "确认未齐，只能告知待协商/待回货状态，不能通知确定取件条件"
            ),
            "blockers": blockers,
            "rejection_records": [h["record"] for h in rejection_events],
            "lineage": self._lineage(handovers),
            "recorded_combinations": [
                {"record": h["record"], "sku": h.get("sku"), "version": h.get("version"),
                 "units": h.get("units"), "kind": h.get("kind")}
                for h in handovers if int(h.get("units") or 0) > 0
            ],
            "accepted_combinations": accepted_combinations,
            "effective_replacement": self.effective_replacement(exchange_id, as_of),
            "original_entitlement_records": [
                h["record"] for h in handovers if h.get("kind") in {"原店认可", "柜台接收"}
            ],
            "partial_combination_rule": (
                "后续只接受部分组合时，已接受部分和原有效单据上的取货权益仍可追踪；"
                "被拒绝的新版不覆盖原承诺"
            ),
        }

    def _intake_store(self, exchange: dict[str, Any],
                      handovers: list[dict[str, Any]]) -> Optional[str]:
        vendor = exchange.get("vendor")
        for h in handovers:
            if h.get("issuer") == vendor and h.get("acceptor"):
                return h["acceptor"]
        acceptors = [h.get("acceptor") for h in handovers if h.get("acceptor")]
        return acceptors[0] if acceptors else None

    def _version_gate(self, exchange: dict[str, Any],
                      handovers: list[dict[str, Any]]) -> dict[str, Any]:
        unknown = [h for h in handovers if h.get("version") is None]
        receipts = [h for h in handovers if h.get("kind") == "版次回执" and h.get("version")]
        if not unknown:
            return {"resolved": True, "actual_version": exchange.get("candidate_version"),
                    "unknown_records": [], "receipt_records": [h["record"] for h in receipts]}
        if receipts:
            latest = receipts[-1]
            return {"resolved": True, "actual_version": latest["version"],
                    "actual_sku": latest.get("sku"),
                    "unknown_records": [h["record"] for h in unknown],
                    "receipt_records": [h["record"] for h in receipts],
                    "note": "实际版次以回执为准；空 version 记录不得按最新版推断"}
        return {"resolved": False, "actual_version": None,
                "unknown_records": [h["record"] for h in unknown],
                "reason": "存在版本待确认且尚无版次回执，只能并列两种适配边界"}

    @staticmethod
    def _lineage(handovers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {h["record"]: h for h in handovers}
        return [
            {
                "record": h["record"],
                "references": h.get("reference"),
                "reference_chain": [h.get("reference")] if h.get("reference") else [],
                "referenced_kind": by_id.get(h.get("reference"), {}).get("kind"),
                "kind": h.get("kind"),
                "received": h.get("received"),
            }
            for h in handovers
        ]

    # 凭证下的左右件推进 ------------------------------------------------
    def voucher_fulfillment(self, order_id: str, as_of: Optional[str] = None) -> dict[str, Any]:
        ledger = self.rights.ledger(order_id)
        sides = []
        for oid in ledger["orders"]:
            order = self.repo.order(oid)
            routings = [self.exchange_routing(e["exchange"], as_of) for e in self.repo.exchanges_for_order(oid)]
            accepted = [r for r in routings if any(g["gate"] == "寄入店接收" and g["satisfied"] for g in r["gates"])]
            sides.append({
                "order": oid,
                "side": order.get("side"),
                "state": order.get("state"),
                "current_style": self.current_style(oid),
                "exchanges": [e["exchange"] for e in self.repo.exchanges_for_order(oid)],
                "return_gated": bool(routings) and not accepted,
                "routings": routings,
            })
        return {
            "voucher": ledger["voucher"],
            "sides": sides,
            "rule": "可以先推进/补偿一只；另一只必须在退回被有效接收后才有发新件资格",
        }
