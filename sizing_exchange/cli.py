"""柜台尺码/换货查询命令行。

示例：
  python3 -m sizing_exchange.cli dashboard
  python3 -m sizing_exchange.cli advice --sku B1 --version V3 --person U014 --as-of W10
  python3 -m sizing_exchange.cli application O1 B1 V3 --exchange E1 --as-of W13
  python3 -m sizing_exchange.cli ledger O1
  python3 -m sizing_exchange.cli routing E1
  python3 -m sizing_exchange.cli impact
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .intervals import Interval
from .repository import DEFAULT_DATA_DIR
from .service import CounterService


def build_service(args: argparse.Namespace) -> CounterService:
    data_dir = getattr(args, "data_dir", None) or DEFAULT_DATA_DIR
    return CounterService(data_dir=str(data_dir))


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="尺码量表、试穿证据与换货权益联动")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="JSON 资料目录")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("dashboard", help="输出资料中关键柜台场景的完整结论")

    p_advice = sub.add_parser("advice", help="查询款/版/客户的适配建议")
    p_advice.add_argument("--sku", required=True)
    p_advice.add_argument("--version")
    p_advice.add_argument("--person")
    p_advice.add_argument("--cohort")
    p_advice.add_argument("--as-of")
    p_advice.add_argument("--measurement-lo", type=float)
    p_advice.add_argument("--measurement-hi", type=float)
    p_advice.add_argument("--axis")

    p_app = sub.add_parser("application", help="综合评估新换货申请")
    p_app.add_argument("order")
    p_app.add_argument("sku")
    p_app.add_argument("version", nargs="?")
    p_app.add_argument("--exchange")
    p_app.add_argument("--issue")
    p_app.add_argument("--as-of")
    p_app.add_argument("--freight", type=float, default=0.0)
    p_app.add_argument("--gap", type=float, default=0.0)
    p_app.add_argument("--quote-provided", action="store_true")
    p_app.add_argument("--measurement-lo", type=float)
    p_app.add_argument("--measurement-hi", type=float)
    p_app.add_argument("--axis")
    p_app.add_argument("--counter-record-json", help="柜台单据 JSON 字符串，只含备选/测量方式/是否成副试穿")
    p_app.add_argument("--explicit-label-error-proof", action="store_true")

    p_ledger = sub.add_parser("ledger", help="查看购买凭证限额和已验证支付")
    p_ledger.add_argument("order")

    p_route = sub.add_parser("routing", help="查看跨店换货确认门槛")
    p_route.add_argument("exchange")

    sub.add_parser("impact", help="检查尺码表发布对各类订单/换货的影响")
    args = parser.parse_args(argv)
    service = build_service(args)

    if args.command == "dashboard":
        print_json(service.counter_dashboard())
    elif args.command == "advice":
        measurement = None
        if args.measurement_lo is not None:
            measurement = Interval(args.measurement_lo,
                                  args.measurement_hi if args.measurement_hi is not None else args.measurement_lo,
                                  args.axis)
        print_json(service.size_advice(
            args.sku, args.version, person=args.person, cohort=args.cohort,
            as_of=args.as_of, measurement=measurement
        ))
    elif args.command == "application":
        measurement = None
        if args.measurement_lo is not None:
            measurement = Interval(args.measurement_lo,
                                  args.measurement_hi if args.measurement_hi is not None else args.measurement_lo,
                                  args.axis)
        counter_record = json.loads(args.counter_record_json) if args.counter_record_json else None
        result = service.assess_application(
            args.order, args.sku, args.version, issue=args.issue, as_of=args.as_of,
            freight_cny=args.freight, gap_cny=args.gap, quote_provided=args.quote_provided,
            measurement=measurement, exchange_id=args.exchange,
            explicit_label_error_proof=args.explicit_label_error_proof,
            counter_record=counter_record,
        )
        print_json(result)
    elif args.command == "ledger":
        print_json(service.rights.ledger(args.order))
    elif args.command == "routing":
        print_json(service.workflow.exchange_routing(args.exchange))
    elif args.command == "impact":
        print_json(service.chart_impact())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
