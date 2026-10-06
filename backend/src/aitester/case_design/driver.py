"""T9 图的驱动节点：把 stages.drive_turn 包成 (state, config) -> {"messages", "case"}。

case_env 经 RunnableConfig.configurable[CASE_DESIGN_KEY] 注入（与 gate 的 GATE_KEY 同款
通道；GraphBuilder 签名不变）；取不到 = 直通（A10）。终帧经 get_stream_writer() 发，与
react 路径走同一前端口径；无图运行上下文（单测直调）时降级为丢弃。
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from aitester.case_design.constants import CASE_DESIGN_KEY
from aitester.case_design.env import CaseDesignEnv
from aitester.case_design.stages import drive_turn


def case_env_of(config: Any) -> CaseDesignEnv | None:
    """从注入的 RunnableConfig 取 case_env（channel：configurable[CASE_DESIGN_KEY]）。"""
    if not config:
        return None
    return (config.get("configurable") or {}).get(CASE_DESIGN_KEY)


def make_driver_node(task_tool: Any):
    """构造 case_design 图的驱动节点（task_tool=父 run 的 TaskTool，评审子经它驱动）。"""

    # config 必须标注为 RunnableConfig：langgraph 按类型注解决定是否注入
    # （本模块有 `from __future__ import annotations`，注解以字符串比对白名单，
    # 只认 "RunnableConfig"——Any 或 "RunnableConfig | None" 都会被静默跳过，
    # T9 接线时实测缺参 TypeError）
    # 上述白名单细节按 langgraph==1.2.12 核实（T9 M-2）：升级 langgraph 时须重新核实。
    def driver_node(state: dict, config: RunnableConfig) -> dict:
        try:
            writer = get_stream_writer()
        except RuntimeError:                       # 单测直调：无图运行上下文
            writer = lambda event: None            # noqa: E731
        return drive_turn(state, config, env=case_env_of(config),
                          task_tool=task_tool, writer=writer)

    return driver_node
