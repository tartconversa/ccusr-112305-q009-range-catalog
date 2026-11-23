"""校验可供分析的资料关系。"""

import unittest

from scripts.inspect_sources import read_table


class SourceContractTest(unittest.TestCase):
    def test_source_observations_and_references(self):
        from collections import defaultdict
        scales = {(row["sku"],row["version"]):row["method"] for row in read_table("scales")}
        observations = defaultdict(set)
        for row in read_table("fittings"):
            self.assertLessEqual(row["lower_mm"], row["upper_mm"])
            observations[row["person"]].add(scales[(row["sku"],row["version"])])
        self.assertGreaterEqual(sum({"手围","指长"} <= methods for methods in observations.values()), 30)
        self.assertEqual({key for key,val in observations.items() if "掌宽" in val}, {"C-sole"})
        orders = {row["order"]:row for row in read_table("orders")}
        self.assertEqual(orders[orders["O1"]["partner"]]["partner"], "O1")
        self.assertTrue(all((row["sku"],row["version"]) in scales for row in orders.values()))
        exchanges = read_table("exchanges")
        self.assertTrue(any(row["order"] == "O2" and row["status"] == "已签收" for row in exchanges))
        self.assertTrue(any(row["order"] == "O1" and row["status"] == "已受理" for row in exchanges))
