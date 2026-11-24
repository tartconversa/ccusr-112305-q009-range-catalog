"""JSON 资料仓储与来源过滤。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from .intervals import not_later_than


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"

TABLE_NAMES = (
    "scales",
    "fittings",
    "reference",
    "orders",
    "exchanges",
    "provider_reports",
    "store_handover",
    "compensation_receipts",
    "commercial",
)


class Repository:
    """以表名索引全部 JSON 来源。

    服务层不直接读文件，便于测试时传入小型内存资料集。
    """

    def __init__(self, data: "dict[str, list[dict[str, Any]]] | str | Path | None" = None,
                 data_dir: str | Path | None = None):
        if isinstance(data, (str, Path)):
            data_dir = data
            data = None
        if data is not None:
            self.tables = {name: list(rows) for name, rows in data.items()}
            self.data_dir: Optional[Path] = None
        else:
            self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
            self.tables = {}
            for name in TABLE_NAMES:
                path = self.data_dir / f"{name}.json"
                if path.exists():
                    with path.open(encoding="utf-8") as stream:
                        rows = json.load(stream)
                    if not isinstance(rows, list):
                        raise ValueError(f"{path} 应为对象数组")
                    self.tables[name] = rows
        for name in TABLE_NAMES:
            self.tables.setdefault(name, [])

    @classmethod
    def from_dir(cls, path: str | Path) -> "Repository":
        return cls(data_dir=path)

    def table(self, name: str) -> list[dict[str, Any]]:
        return self.tables.get(name, [])

    # 常用索引 ---------------------------------------------------------
    def scale_rows(self) -> list[dict[str, Any]]:
        return self.table("scales")

    def scale(self, sku: str, version: str) -> dict[str, Any]:
        for row in self.scale_rows():
            if row["sku"] == sku and row["version"] == version:
                return row
        raise KeyError(f"找不到量表版次 {sku}/{version}")

    def has_scale(self, sku: str, version: Optional[str]) -> bool:
        return any(r["sku"] == sku and r["version"] == version for r in self.scale_rows())

    def order(self, order_id: str) -> dict[str, Any]:
        for row in self.table("orders"):
            if row["order"] == order_id:
                return row
        raise KeyError(f"找不到订单 {order_id}")

    def exchange(self, exchange_id: str) -> dict[str, Any]:
        for row in self.table("exchanges"):
            if row["exchange"] == exchange_id:
                return row
        raise KeyError(f"找不到换货单 {exchange_id}")

    def exchanges_for_order(self, order_id: str) -> list[dict[str, Any]]:
        return [r for r in self.table("exchanges") if r["order"] == order_id]

    def handover_for_exchange(self, exchange_id: str,
                              as_of: Optional[str] = None) -> list[dict[str, Any]]:
        rows = [r for r in self.table("store_handover") if r["exchange"] == exchange_id]
        rows = [r for r in rows if not_later_than(r.get("received"), as_of)]
        return sorted(rows, key=lambda r: (str(r.get("received", "")), str(r.get("record", ""))))

    def receipts_for_exchange(self, exchange_id: str,
                              as_of: Optional[str] = None) -> list[dict[str, Any]]:
        rows = [r for r in self.table("compensation_receipts") if r["exchange"] == exchange_id]
        return [r for r in rows if not_later_than(r.get("week"), as_of)]

    def provider_reports(self, person: str, as_of: Optional[str] = None) -> list[dict[str, Any]]:
        rows = [r for r in self.table("provider_reports") if r["wearer"] == person]
        return [r for r in rows if not_later_than(r.get("received"), as_of)]

    def reference_for(self, person: str, as_of: Optional[str] = None) -> Optional[dict[str, Any]]:
        rows = [r for r in self.table("reference") if r["person"] == person]
        rows = [r for r in rows if not_later_than(r.get("recorded"), as_of)]
        if not rows:
            return None
        return sorted(rows, key=lambda r: str(r.get("recorded", "")))[-1]

    def fittings_for_person(self, person: str, as_of: Optional[str] = None) -> list[dict[str, Any]]:
        rows = [r for r in self.table("fittings") if r["person"] == person]
        return [r for r in rows if not_later_than(r.get("week"), as_of)]

    def commercial_rule(self, issue: str) -> dict[str, Any]:
        aliases = {
            "原说明有误": "标注争议",
            "标注错误": "标注争议",
            "标注争议": "标注争议",
            "试穿数据不足": "适配不明",
            "适配不明": "适配不明",
            "客户单纯改变偏好": "偏好变更",
            "偏好变更": "偏好变更",
        }
        key = aliases.get(issue, issue)
        for row in self.table("commercial"):
            if row["issue"] == key:
                return row
        # 未知原因不能擅自给出责任方。
        return {
            "issue": key,
            "freight_bearer": "待复核",
            "gap_bearer": "待复核",
            "allowed_pair_overlap": True,
            "min_cap": 0,
        }


def rows_until(rows: Iterable[dict[str, Any]], as_of: Optional[str],
               week_field: str = "week") -> list[dict[str, Any]]:
    if as_of is None:
        return list(rows)
    return [row for row in rows if not_later_than(row.get(week_field), as_of)]
