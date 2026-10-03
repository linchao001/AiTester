"""在途回合注册表：run_id → RunControl，进程内内存态。

重启即在途 run 消失——此时 /api/chat/stop 回 404，而界面早已在终态之前断开，无害（spec 契约表）。
不做持久化也不做鉴权：服务只绑 127.0.0.1、单用户本机（第 1/2 片同口径限制）。
"""

from __future__ import annotations

import threading
import uuid

from aitester.orchestration.run_control import RunControl


def new_run_id() -> str:
    return uuid.uuid4().hex


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, RunControl] = {}
        self._lock = threading.Lock()

    def start(self, run_id: str) -> RunControl:
        control = RunControl()
        with self._lock:
            self._runs[run_id] = control
        return control

    def cancel(self, run_id: str) -> bool:
        """置位并回「有没有命中」；未命中 = 这条回答已经结束或从未存在。"""
        with self._lock:
            control = self._runs.get(run_id)
        if control is None:
            return False
        control.cancel()
        return True

    def finish(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)
