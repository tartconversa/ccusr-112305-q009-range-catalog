import unittest

from sizing_exchange import CounterService, Interval, Repository, ScaleNotConnectedError
from sizing_exchange.counter import CounterSlip


class SizingDomainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = CounterService()

    def test_new_measurement_is_axis_time_and_scope_limited(self):
        old = self.service.scales.measurement_sets("U014", "W10")
        old_sources = " ".join(m["source"] for m in old)
        self.assertIn("R1", old_sources)
        self.assertNotIn("R2", old_sources)
        self.assertNotIn("R4", old_sources)
        self.assertTrue(all(m["axis"] in {"手围", "指长"} for m in old))

        current = self.service.scales.measurement_sets("U014", "W13")
        axes = {(m["axis"], m["source"].split(":")[1]) for m in current}
        self.assertIn(("指长", "R2"), axes)
        self.assertIn(("手围", "R4"), axes)
        # 指长复测不能凭空生成新的手围历史。
        r2 = [m for m in current if m["source"].endswith("R2")][0]
        self.assertEqual(r2["axis"], "指长")
        self.assertEqual((r2["interval"]["lo_mm"], r2["interval"]["hi_mm"]), (69, 73))

    def test_scale_graph_does_not_connect_palm_width(self):
        edge = self.service.scales.connection("手围", "指长")
        self.assertTrue(edge["connected"])
        self.assertIn("common_measurement", edge["kinds"])
        with self.assertRaises(ScaleNotConnectedError):
            self.service.scales.assert_connected("手围", "掌宽")
        advice = self.service.fit.advise("C1", "V5", person="U017")
        self.assertEqual(advice["status"], "scale_disconnected")
        self.assertFalse(advice["store_guarantee_allowed"])

    def test_same_subject_styles_are_clustered_not_independent_rows(self):
        summary = self.service.fit.evidence_summary("S1", "V1")
        self.assertEqual(summary["records"], 84)
        self.assertEqual(summary["independent_persons"], 84)
        self.assertIn("dependency_warning", summary)

    def test_u014_counter_advice_distinguishes_strong_and_one_sided(self):
        b1 = self.service.fit.advise("B1", "V3", person="U014", as_of="W10")
        self.assertEqual(b1["status"], "one_sided_signal")
        self.assertFalse(b1["store_guarantee_allowed"])
        self.assertTrue(any("偏紧" in str(r) for r in b1["direct_fittings"]))

        b2 = self.service.fit.advise("B2", "V3", person="U014", as_of="W10")
        self.assertEqual(b2["status"], "fit_supported")
        self.assertTrue(b2["store_guarantee_allowed"])
        self.assertTrue(b2["measurement_provenance"])

        # W13 复测与 W02 共同实测冲突时，不能继续无条件保证。
        b2_now = self.service.fit.advise("B2", "V3", person="U014", as_of="W13")
        self.assertEqual(b2_now["status"], "conflicting_measurements")
        self.assertFalse(b2_now["store_guarantee_allowed"])

    def test_unknown_version_gives_dual_boundaries_and_common_range(self):
        advice = self.service.size_advice("B1", person="U014", as_of="W12")
        self.assertEqual(advice["version_state"], "actual_version_unknown")
        self.assertEqual(set(advice["boundaries"]), {"V3", "V4"})
        self.assertEqual(advice["committable_range_across_versions"]["lo_mm"], 67)
        self.assertEqual(advice["committable_range_across_versions"]["hi_mm"], 72)
        self.assertFalse(advice["store_guarantee_allowed"])

        resolved = self.service.resolve_version_receipt("B1", "V4", "U014", "W13")
        self.assertEqual(resolved["actual_version"], "V4")
        self.assertTrue(any(c["overturned"] for c in resolved["comparisons"]))

    def test_voucher_limit_is_shared_and_only_arrived_receipts_count_now(self):
        ledger = self.service.rights.ledger("O1")
        self.assertEqual(ledger["voucher"], "凭证:O1+O2")
        self.assertEqual(ledger["orders"], ["O1", "O2"])
        self.assertEqual(ledger["shared_limit_cny"], 35)
        self.assertEqual(ledger["verified_paid_cny"], 18)
        self.assertEqual(ledger["additional_now_cny"], 17)
        self.assertEqual(ledger["pending_acknowledgement_cny"], 12)
        self.assertEqual(ledger["remaining_if_all_pending_accepted_cny"], 5)
        self.assertEqual(ledger["accepted_exchange_cost_total_cny"], 30)
        self.assertEqual(ledger["remaining_after_accepted_costs_cny"], 5)
        self.assertEqual(ledger["verified_freight_cny"], 8)
        self.assertEqual(ledger["verified_gap_cny"], 10)

        charge = self.service.rights.assess_new_charge("O1", "标注争议", 12, 10)
        self.assertEqual(charge["allocation"]["freight"]["bearer"], "商家")
        self.assertEqual(charge["allocation"]["price_gap"]["bearer"], "品牌")
        self.assertEqual(charge["new_charge_total_cny"], 22)
        self.assertEqual(charge["contract_coverable_now_cny"], 17)
        self.assertEqual(charge["excess_or_negotiation_cny"], 5)

    def test_cost_bearers_for_preference_and_unknown(self):
        pref = self.service.rights.allocate_costs("偏好变更", 9, 4)
        self.assertEqual(pref["freight"]["bearer"], "买方")
        self.assertEqual(pref["price_gap"]["bearer"], "买方")
        unknown = self.service.rights.allocate_costs("适配不明", 9, 4)
        self.assertEqual(unknown["freight"]["bearer"], "待复核")
        self.assertEqual(unknown["price_gap"]["bearer"], "待复核")

    def test_cross_store_routing_e1_blocked_e2_closed(self):
        e1 = self.service.workflow.exchange_routing("E1")
        self.assertFalse(e1["all_confirmed"])
        self.assertFalse(e1["may_notify_pickup"])
        self.assertEqual(e1["intake_store"], "南柜")
        self.assertEqual(e1["sending_store"], "北柜")
        self.assertIn("H5", e1["rejection_records"])
        self.assertEqual(e1["effective_replacement"]["version"], "V3")
        gate_names = [g["gate"] for g in e1["gates"]]
        self.assertEqual(len(gate_names), 3)

        e2 = self.service.workflow.exchange_routing("E2")
        self.assertTrue(e2["all_confirmed"])
        self.assertTrue(e2["pickup_already_completed"])
        self.assertFalse(e2["may_notify_pickup"])

    def test_pair_overlap_and_merchant_promise(self):
        strong = self.service.workflow.pair_check("O1", "B1", "V4")
        self.assertTrue(strong["paired_voucher"])
        self.assertTrue(strong["pair_compatible"])
        self.assertEqual((strong["pair_overlap"]["lo_mm"], strong["pair_overlap"]["hi_mm"]), (67, 72))
        self.assertGreater(strong["overlap_width_mm"], 0)
        self.assertTrue(strong["merchant_acceptance_promise"])

        point_only = self.service.workflow.pair_check("O1", "B2", "V3")
        self.assertTrue(point_only["pair_compatible"])
        self.assertEqual(point_only["overlap_width_mm"], 0)
        self.assertFalse(point_only["positive_width_required_for_robust_pair"])

        disconnected_pair = self.service.workflow.pair_check("O1", "C1", "V5")
        self.assertFalse(disconnected_pair["pair_compatible"])

    def test_application_e1_has_pending_negotiation(self):
        result = self.service.assess_application(
            "O1", "B1", "V3", exchange_id="E1", as_of="W13",
            freight_cny=12, gap_cny=10, quote_provided=True,
        )
        self.assertFalse(result["fully_supported_alternative"])
        self.assertFalse(result["fit_requirement_met"])
        self.assertTrue(result["pending_negotiation_items"])
        self.assertEqual(result["counter_answer"]["freight_bearer"], "待复核")
        self.assertIn("measurement_provenance", result["counter_answer"])

    def test_e3_null_version_is_resolved_by_receipt_not_latest_guess(self):
        pending = self.service.workflow.exchange_routing("E3", "W12")
        pending_gate = pending["gates"][1]["version_state"]
        self.assertFalse(pending_gate["resolved"])
        self.assertEqual(pending_gate["unknown_records"], ["H6"])

        routing = self.service.workflow.exchange_routing("E3", "W13")
        version_gate = routing["gates"][1]["version_state"]
        self.assertTrue(version_gate["resolved"])
        self.assertEqual(version_gate["actual_version"], "V1")
        self.assertIn("H6", version_gate["unknown_records"])
        self.assertIn("H7", version_gate["receipt_records"])

        app = self.service.assess_application("O3", "S1", exchange_id="E3", as_of="W13")
        self.assertEqual(app["candidate"]["version"], "V1")
        self.assertEqual(app["cause"]["code"], "customer_preference")
        self.assertEqual(app["cost_and_benefit"]["allocation"]["freight"]["bearer"], "买方")

    def test_e1_future_receipt_is_excluded_by_as_of(self):
        at_w12 = self.service.workflow.exchange_routing("E1", "W12")
        finance = at_w12["gates"][2]
        self.assertTrue(finance["satisfied"] or not finance["pending"])
        self.assertNotIn("T3", finance["pending"])
        current = self.service.workflow.exchange_routing("E1", "W13")
        self.assertIn("T3", current["gates"][2]["pending"])

    def test_proven_guide_error_unlocks_remedy_allocation(self):
        result = self.service.assess_application(
            "O1", "B2", "V3", issue="标注争议", as_of="W10",
            freight_cny=5, gap_cny=5, quote_provided=True,
            explicit_label_error_proof=True,
        )
        self.assertEqual(result["cause"]["reason_category"], "原说明有误")
        self.assertTrue(result["remedies"]["free_price_gap_available"])
        self.assertTrue(result["remedies"]["freight_compensation_available"])
        self.assertEqual(result["cost_and_benefit"]["allocation"]["freight"]["bearer"], "商家")
        self.assertEqual(result["cost_and_benefit"]["allocation"]["price_gap"]["bearer"], "品牌")

    def test_counter_slip_cannot_smuggle_measurement_or_fit(self):
        slip = CounterSlip("C1", [{"sku": "B1", "version": None}], "手围", True)
        self.assertTrue(slip.to_dict()["tried_as_pair"])
        with self.assertRaises(ValueError):
            CounterSlip("C2", [{"sku": "B1"}], "手围", False, measured_lo_mm=180)
        with self.assertRaises(ValueError):
            CounterSlip("C3", [{"sku": "B1"}], "手围", False, fit_result="适合")

    def test_chart_impact_covers_order_states(self):
        impact = self.service.chart_impact()
        states = {row["id"]: row["state"] for row in impact["orders"]}
        self.assertEqual(states["O4"], "待发货")
        self.assertEqual(states["O3"], "在途")
        self.assertEqual(states["O1"], "已签收")
        joined = str(impact)
        self.assertIn("尚未发货", joined)
        self.assertIn("已签收", joined)

    def test_in_memory_new_chart_uses_same_rules(self):
        new_row = {
            "version": "V6", "class": "测试手套", "sku": "S9", "method": "手围",
            "mark": "测试", "lower_mm": 180, "upper_mm": 190,
            "publish_week": "W14", "replace": None,
        }
        next_service = self.service.with_scale_rows([new_row])
        advice = next_service.fit.advise(
            "S9", "V6", measurement=Interval(184, 186, "手围")
        )
        self.assertEqual(advice["status"], "table_supported_unvalidated")
        self.assertEqual(advice["axis"], "手围")

    def test_repository_accepts_small_in_memory_dataset(self):
        repo = Repository({
            "scales": [{"sku": "X", "version": "1", "method": "指长", "lower_mm": 70,
                        "upper_mm": 80, "publish_week": "W01", "replace": None}],
            "orders": [], "fittings": [], "reference": [], "commercial": [],
        })
        service = CounterService(repo)
        advice = service.fit.advise("X", "1", measurement=Interval(72, 75, "指长"))
        self.assertEqual(advice["status"], "table_supported_unvalidated")


if __name__ == "__main__":
    unittest.main()
