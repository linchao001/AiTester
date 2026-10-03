"""在途回合的取消载体：一个 Event，不是异常。

带已生成的产物收尾是正常终态（spec 裁定 2），所以这里没有 TurnCancelled 这类异常类型。
放 orchestration 而非 services：节点体要 import 它，依赖方向必须停在 adapters/orchestration 之下。
"""

from __future__ import annotations

import threading

# 进 LangGraph config.configurable 的键：值是整个 RunControl 对象（P6 实测活对象可读）
RUN_CONTROL_KEY = "aitester_run_control"


class RunControl:
    """只有一格开关的取消位。置位幂等，读侧每 chunk 一次。"""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()
