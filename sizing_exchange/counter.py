"""柜台现场单据。

柜台单据刻意只保存：备选款、测量方式、是否成副试穿。它不是测量服务商报告，
不能从“用了手围法”反推出具体手围，也不能补写数值或适配结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


KNOWN_METHOD_AXIS = {
    "手围": "手围",
    " circumference": "手围",
    "circumference": "手围",
    "指长": "指长",
    "finger": "指长",
    "掌宽": "掌宽",
    "palm": "掌宽",
}


@dataclass
class CounterSlip:
    record_id: str
    candidates: list[dict[str, Optional[str]]]
    measurement_method: str
    tried_as_pair: bool
    exchange_id: Optional[str] = None
    order_id: Optional[str] = None
    store: Optional[str] = None
    week: Optional[str] = None
    notes: Optional[str] = None
    # 明确禁止把这些字段塞进柜台单据；保留参数仅为在误传时报错。
    measured_lo_mm: Optional[float] = field(default=None, repr=False)
    measured_hi_mm: Optional[float] = field(default=None, repr=False)
    fit_result: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.measured_lo_mm is not None or self.measured_hi_mm is not None:
            raise ValueError("柜台单据不得记录具体测量数值；数值应以 provider_reports 上报")
        if self.fit_result is not None:
            raise ValueError("柜台单据不得补写试穿等级；试穿等级应来自 fittings 记录")
        if not self.candidates:
            raise ValueError("柜台单据至少要记录一个备选款")
        for candidate in self.candidates:
            if "sku" not in candidate:
                raise ValueError("备选款必须包含 sku")

    @property
    def measurement_axis(self) -> Optional[str]:
        return KNOWN_METHOD_AXIS.get(self.measurement_method.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "document_type": "counter_slip_limited",
            "exchange_id": self.exchange_id,
            "order_id": self.order_id,
            "store": self.store,
            "week": self.week,
            "candidates": self.candidates,
            "measurement_method": self.measurement_method,
            "measurement_axis": self.measurement_axis,
            "tried_as_pair": self.tried_as_pair,
            "notes": self.notes,
            "evidence_limit": (
                "只能证明现场选了哪些备选、用何种测量方式以及是否成副试穿；"
                "不能替代测量区间或试穿结论"
            ),
        }


def normalize_counter_record(row: dict[str, Any]) -> CounterSlip:
    """兼容松散 JSON/字典输入。"""

    candidates = row.get("candidates")
    if candidates is None:
        candidates = [{"sku": row["sku"], "version": row.get("version")}]
    return CounterSlip(
        record_id=row.get("record_id") or row.get("record") or "COUNTER",
        candidates=candidates,
        measurement_method=row.get("measurement_method") or row.get("method") or "未记录",
        tried_as_pair=bool(row.get("tried_as_pair", row.get("paired_trial", False))),
        exchange_id=row.get("exchange_id") or row.get("exchange"),
        order_id=row.get("order_id") or row.get("order"),
        store=row.get("store") or row.get("vendor"),
        week=row.get("week") or row.get("received"),
        notes=row.get("notes"),
        measured_lo_mm=row.get("lo_mm") or row.get("measured_lo_mm"),
        measured_hi_mm=row.get("hi_mm") or row.get("measured_hi_mm"),
        fit_result=row.get("result") or row.get("fit_result"),
    )
