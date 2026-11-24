"""购买凭证、共用补偿限额、费用分担和已用权益。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from .repository import Repository


VERIFIED_RECEIPT_STATES = {"已到账"}


def voucher_id_for_order(order: dict[str, Any]) -> str:
    oid = order["order"]
    partner = order.get("partner")
    if partner:
        left, right = sorted([oid, partner])
        return f"凭证:{left}+{right}"
    return f"凭证:{oid}"


class BenefitService:
    def __init__(self, repo: Repository):
        self.repo = repo

    def voucher_orders(self, order_id: str) -> list[dict[str, Any]]:
        order = self.repo.order(order_id)
        voucher = voucher_id_for_order(order)
        orders = [o for o in self.repo.table("orders") if voucher_id_for_order(o) == voucher]
        if not any(o["order"] == order_id for o in orders):
            orders.append(order)
        return sorted(orders, key=lambda o: o["order"])

    def voucher_id(self, order_id: str) -> str:
        return voucher_id_for_order(self.repo.order(order_id))

    def ledger(self, order_id: str) -> dict[str, Any]:
        orders = self.voucher_orders(order_id)
        voucher = voucher_id_for_order(orders[0])
        # 互指左右行重复填写同一限额；凭证只取一次。
        limit = float(orders[0].get("limits") or 0)
        order_ids = {o["order"] for o in orders}
        exchanges = [e for e in self.repo.table("exchanges") if e["order"] in order_ids]
        exchange_ids = {e["exchange"] for e in exchanges}
        receipts = [r for r in self.repo.table("compensation_receipts")
                     if r["exchange"] in exchange_ids]

        arrived = [r for r in receipts if r.get("state") in VERIFIED_RECEIPT_STATES]
        pending = [r for r in receipts if r.get("state") not in VERIFIED_RECEIPT_STATES]
        arrived_amount = sum(float(r["cny"]) for r in arrived)
        pending_amount = sum(float(r["cny"]) for r in pending)
        accepted_cost_total = sum(float(e.get("cost") or 0) for e in exchanges)
        verified_freight = sum(float(r["cny"]) for r in arrived if r.get("kind") == "运费")
        verified_gap = sum(float(r["cny"]) for r in arrived if r.get("kind") == "差额")

        by_payer_kind: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for r in arrived:
            by_payer_kind[r.get("payer", "未知")][r.get("kind", "未知")] += float(r["cny"])

        received_styles = []
        for ex in exchanges:
            if ex.get("status") == "已签收":
                received_styles.append({
                    "exchange": ex["exchange"], "order": ex["order"],
                    "sku": ex["candidate"], "version": ex.get("candidate_version"),
                    "status": ex["status"], "cost_cny": ex.get("cost"),
                })

        return {
            "voucher": voucher,
            "orders": [o["order"] for o in orders],
            "holder": orders[0].get("holder"),
            "shared_limit_cny": limit,
            "verified_paid_cny": arrived_amount,
            "verified_freight_cny": verified_freight,
            "verified_gap_cny": verified_gap,
            "additional_now_cny": max(0.0, limit - arrived_amount),
            "over_limit_verified_cny": max(0.0, arrived_amount - limit),
            "pending_acknowledgement_cny": pending_amount,
            "remaining_if_all_pending_accepted_cny": max(
                0.0, limit - arrived_amount - pending_amount
            ),
            "accepted_exchange_cost_total_cny": accepted_cost_total,
            "remaining_after_accepted_costs_cny": max(0.0, limit - accepted_cost_total),
            "cost_reconciliation_note": (
                "exchange.cost 是换货总费用；不得与 compensation_receipts 再次相加。"
                "已到账回执应能按 exchange 核对其费用构成。"
            ),
            "paid_by_payer_and_kind": {k: dict(v) for k, v in by_payer_kind.items()},
            "arrived_receipts": arrived,
            "pending_receipts": pending,
            "exchanges": exchanges,
            "customer_received_styles": received_styles,
            "used_benefits_persist": [
                {
                    "exchange": r["exchange"], "receipt": r["receipt"],
                    "payer": r["payer"], "kind": r["kind"], "cny": r["cny"],
                    "state": r["state"],
                }
                for r in arrived
            ],
        }

    def additional_now(self, order_id: str) -> float:
        return float(self.ledger(order_id)["additional_now_cny"])

    def remaining_after_accepted_costs(self, order_id: str) -> float:
        return float(self.ledger(order_id)["remaining_after_accepted_costs_cny"])

    def commercial_rule(self, issue: str) -> dict[str, Any]:
        return self.repo.commercial_rule(issue)

    def allocate_costs(self, issue: str, freight_cny: float = 0.0,
                       gap_cny: float = 0.0) -> dict[str, Any]:
        rule = self.commercial_rule(issue)
        return {
            "issue": issue,
            "policy_issue": rule["issue"],
            "freight": {"bearer": rule["freight_bearer"], "cny": float(freight_cny)},
            "price_gap": {"bearer": rule["gap_bearer"], "cny": float(gap_cny)},
            "total_cny": float(freight_cny + gap_cny),
            "allowed_pair_overlap": bool(rule.get("allowed_pair_overlap", True)),
            "min_cap_cny": rule.get("min_cap", 0),
            "proof_required": rule["issue"] == "标注争议",
        }

    def assess_new_charge(self, order_id: str, issue: str,
                          freight_cny: float = 0.0, gap_cny: float = 0.0) -> dict[str, Any]:
        ledger = self.ledger(order_id)
        allocation = self.allocate_costs(issue, freight_cny, gap_cny)
        total = allocation["total_cny"]
        available = ledger["additional_now_cny"]
        coverable = min(total, available)
        return {
            "voucher": ledger["voucher"],
            "allocation": allocation,
            "verified_paid_cny": ledger["verified_paid_cny"],
            "shared_limit_cny": ledger["shared_limit_cny"],
            "additional_now_cny": available,
            "new_charge_total_cny": total,
            "contract_coverable_now_cny": coverable,
            "excess_or_negotiation_cny": max(0.0, total - coverable),
            "pending_receipts_cny": ledger["pending_acknowledgement_cny"],
            "additional_if_pending_recognized_cny": ledger[
                "remaining_if_all_pending_accepted_cny"
            ],
            "warning": (
                "待认可回执不是已验证支付事实；只能作为或有占用，不能提前当作到账"
                if ledger["pending_acknowledgement_cny"] else None
            ),
        }
