"""常用引擎入口别名。"""

from sizing_exchange.service import CounterService


class Engine(CounterService):
    """CounterService 的短名称别名。"""


SizingEngine = Engine

__all__ = ["CounterService", "Engine", "SizingEngine"]
