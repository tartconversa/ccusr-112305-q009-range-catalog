"""滑雪手套/骑行护具尺码建议与换货权益联动引擎。"""

from .intervals import Interval
from .repository import Repository
from .service import CounterService
from .fit import FitAdvisor
from .scales import ScaleService, ScaleNotConnectedError
from .rights import BenefitService
from .workflow import WorkflowService

__all__ = [
    "Interval",
    "Repository",
    "CounterService",
    "SizingEngine",
    "FitAdvisor",
    "ScaleService",
    "ScaleNotConnectedError",
    "BenefitService",
    "WorkflowService",
    "load_service",
]


class SizingEngine(CounterService):
    """语义别名：部分调用方可能按“尺码引擎”查找该门面。"""


def load_service(data_dir: str | None = None) -> CounterService:
    """从默认 ``data/`` 或指定目录载入资料并返回门面服务。"""

    if data_dir:
        return CounterService(data_dir=data_dir)
    return CounterService()
