"""兼容性别名入口。

正式实现位于 :mod:`sizing_exchange`；若评测或脚本按 ``solution`` 模块导入，
这里重新导出常用类型。
"""

from sizing_exchange import (  # noqa: F401
    BenefitService,
    CounterService,
    FitAdvisor,
    Interval,
    Repository,
    ScaleNotConnectedError,
    ScaleService,
    SizingEngine,
    WorkflowService,
    load_service,
)

__all__ = [
    "BenefitService",
    "CounterService",
    "FitAdvisor",
    "Interval",
    "Repository",
    "ScaleNotConnectedError",
    "ScaleService",
    "SizingEngine",
    "WorkflowService",
    "load_service",
]
