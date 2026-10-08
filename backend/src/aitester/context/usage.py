"""一回合一个的上下文累加件。

刻意做成**普通类**（不是 dataclass）：它要作为 `AiTooler` 的 pydantic 字段跨装配层传递，
dataclass 会被 pydantic 重新构造、丢掉「同一个累加件」这个身份。
所有读数**从 samples 派生**，不提供任何 setter——手改字段就是本仓最怕的「会说谎的账」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Truncation:
    tool: str
    original: int
    kept: int
    dropped: int

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "original": self.original,
                "kept": self.kept, "dropped": self.dropped}


ACTUAL = "actual"
ESTIMATED = "estimated"


class ContextUsage:
    def __init__(self, window: int) -> None:
        self.window = int(window or 0)
        self.rounds = 0
        self.truncations: list[Truncation] = []
        self.error: str | None = None
        self._input: list[tuple[int, str]] = []        # 每次请求一个样本，真值回来就地替换
        self._output: list[tuple[int, str]] = []

    def note_request(self, est_input: int) -> None:
        self.rounds += 1
        self._input.append((max(0, int(est_input)), ESTIMATED))

    def note_response(self, real_input: int | None, real_output: int | None) -> None:
        if real_input is not None:
            if self._input:
                self._input[-1] = (max(0, int(real_input)), ACTUAL)
            else:
                self._input.append((max(0, int(real_input)), ACTUAL))
        if real_output is not None:
            self._output.append((max(0, int(real_output)), ACTUAL))

    def note_truncation(self, *, tool: str, original: int, kept: int, dropped: int) -> None:
        self.truncations.append(Truncation(tool=tool, original=int(original),
                                           kept=int(kept), dropped=int(dropped)))

    @property
    def peak_occupancy(self) -> int:
        return max((v for v, _ in self._input), default=0)

    @property
    def occupancy_source(self) -> str:
        """peak 那一格有真值就是 actual，否则 estimated；一条样本都没有也算 estimated。"""
        peak = self.peak_occupancy
        return ACTUAL if any(v == peak and s == ACTUAL for v, s in self._input) else ESTIMATED

    @property
    def spent_input(self) -> int:
        return sum(v for v, _ in self._input)

    @property
    def spent_output(self) -> int:
        return sum(v for v, _ in self._output)

    def snapshot(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "rounds": self.rounds,
            "peak_occupancy": self.peak_occupancy,
            "occupancy_source": self.occupancy_source,
            "spent_input": self.spent_input,
            "spent_output": self.spent_output,
            "truncated": [t.to_dict() for t in self.truncations],
            "error": self.error,
        }
