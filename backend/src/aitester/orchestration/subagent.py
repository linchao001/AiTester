"""子智能体（委派式）编排：同线程驱动、受控帧重发、挂起重建冒泡。

依赖方向（R13 断环）：gate 与 agent_graph 都 import 本模块；本模块对二者零 import
（只吃 langchain/langgraph 与消息类型），所以谁都不许在这里回头 import。
`SUBAGENT_KEY`（gate 读来源标注）与 `DETAIL_MAX`/`detail_of`（两处过程块同口径）
都住在这一份里，单一真相。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import ToolException
from langgraph.config import get_stream_writer
from langgraph.errors import GraphBubbleUp, GraphInterrupt

from aitester.adapters.tools.subagent_tools import DEFAULT_SUBAGENT_TYPE, TASK_TOOL_ID

SUBAGENT_KEY = "aitester_subagent"

# 过程块参数摘要的截断上限（spec 接口块登记值，实现此前漂移成裸 80；从 agent_graph 迁居于此）
DETAIL_MAX = 80


def detail_of(call: dict[str, Any]) -> str:
    """过程块的参数摘要：与迁移前逐字同口径（JSON 序列化后截 DETAIL_MAX）。"""
    try:
        return json.dumps(call.get("args") or {}, ensure_ascii=False)[:DETAIL_MAX]
    except (TypeError, ValueError):
        return str(call.get("args"))[:DETAIL_MAX]  # 非常规 args（非 JSON 可序列化）不退化成报错，UI 只截一行


_DEFER_REASON = (
    "Not executed in this round: a round that delegates runs only its 'task' calls, "
    "and it runs several of them in parallel only when every subagent you named has a "
    "read-only tool face. Re-issue this call in your next round."
)


def _parallel_of(call: dict[str, Any], parallel: dict[str, bool]) -> bool:
    """这一路 task 点名的子智能体能不能并行（表里没有按不能办）——与 TaskTool 同一个缺省。"""
    args = call.get("args") or {}
    return bool(parallel.get(str(args.get("subagent_type") or DEFAULT_SUBAGENT_TYPE), False))


def shape_task_batch(
    calls: list[dict[str, Any]], parallel: dict[str, bool]
) -> tuple[list[dict[str, Any]], list[ToolMessage]]:
    """R4 扇出条件：整批 task 都可并行才全跑，否则只跑批内第一个；非 task 兄弟一律延后。

    并行子各占派生 checkpoint ns（R14），互不踩踏；可挂起子仍走父 ns——它的续跑值按
    位置配对，只在父 ns 上被 T1 s1 实测过，两个这样的子同批就会把 resume 值分错家。
    非 task 兄弟与 task 挤同一个 superstep：子一旦挂起、父重入会把兄弟全部重跑（探针实测），
    所以「委派轮只办委派」这条与档位无关（R8）。
    错误结果与拒绝分支同一条通路：声明过的每个 tool_call_id 都有对应 tool 消息，
    provider 的配对约束不破；「不执行」由 tools 节点按已有结果过滤（_unanswered，既有机制）。
    """
    tasks = [c for c in calls if c["name"] == TASK_TOOL_ID]
    if not tasks:
        return list(calls), []
    keep = tasks if all(_parallel_of(c, parallel) for c in tasks) else [tasks[0]]
    kept = {id(c) for c in keep}
    deferred = [
        ToolMessage(content=_DEFER_REASON, tool_call_id=str(c["id"]), name=str(c["name"]),
                    status="error")
        for c in calls if id(c) not in kept
    ]
    return keep, deferred


@dataclass(frozen=True)
class ChildRuntime:
    """一个可驱动的子智能体实例（装配层现装现弃；drive 不持有状态）。"""

    agent_id: str
    system_prompt: str
    provider: Any
    tools: list[Any]
    build_graph: Any          # GraphBuilder：子跑同一套 react 构建器


def _last_reply(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return str(msg.content or "")
    return ""


def drive_child(child: ChildRuntime, brief: str, *, call_id: str, name: str, title: str,
                config: RunnableConfig | None, isolated: bool = False) -> str:
    """在父 tools 任务内驱动子图：同线程、受控帧、挂起重建（母本 = T1 探针 drive_min）。

    调用点在 TaskTool._run（ToolNode 线程池内），writer 输出都经父 stream 外发；子图
    自己的 custom 帧不自动进父流（实测），所以只回收 turn/tools 两类按契约重发（R2）。

    `isolated`（R14）：并行扇出的子把 `checkpoint_ns` 换成 `{父 ns}@sub.{call_id}`——`thread_id`
    仍是父 run，所以 `drop_thread(run_id)` 收尾连带收回子状态；`call_id` 在 superstep 重放里
    稳定，续跑仍落回同一个子。不可省略：同 ns 双跑时下面的「已完结 → 复用摘要」守卫会读到
    兄弟的完结状态，把别人的摘要当成自己的返回。可挂起的子恒 `isolated=False`，沿用 s1 实测过
    的父 ns 通路（续跑值按位置配对）。派生段用 `.` 连接而非 `:`/`|`：langgraph 把 `|` 当层级、
    `:` 当任务 id 分隔（`_internal/_constants.py:87-89`），`recast_checkpoint_ns()` 会剥掉 `:`
    之后的部分（`_internal/_config.py:38-49`）——用 `:` 两个并行子会被折成同一个 ns。
    已知边界（T1 s5 实测）：同一父 tools 任务里第 2 个及以后的子图，langgraph 会把**落库** ns
    再追加 `|N`（`pregel/_loop.py:325-341` 的 `scratchpad.subgraph_counter()`），我们写的只是前缀。
    后果仅一条：并行批次里靠后的子，其「已完结 → 复用摘要」守卫按前缀读不到状态（实测两个子时
    第 2 个子落 `…@sub.c2|1`、前缀下 0 条）。不修：并行面按 R4 恒为只读、不挂起，同一批不会被
    重放第二次，守卫在这条路上没有使用场景；真要用（执行类子也并行）时改为按前缀扫检查点。
    """
    sub = {"call_id": call_id, "name": name, "title": title}
    conf = dict((config or {}).get("configurable") or {})
    conf[SUBAGENT_KEY] = sub                      # 子 gate 依此标注自己的 wait（R3）
    if isolated:
        conf["checkpoint_ns"] = f"{conf.get('checkpoint_ns') or ''}@sub.{call_id}"
    child_cfg: RunnableConfig = {"configurable": conf}
    graph = child.build_graph(child.provider, child.tools)

    # 守卫其一（R5）：同 ns 重入时子已完结 → 复用既有摘要，不发帧不重跑。
    # get_stream_writer 必须在守卫之后取：本函数要能在无流上下文里被裸调（守卫可单测）。
    st = graph.get_state(child_cfg)
    if not st.next and st.values.get("messages"):
        return _last_reply(list(st.values["messages"]))

    writer = get_stream_writer()
    t0 = time.monotonic()
    writer({"type": "sub", "phase": "start", **sub})
    calls_by_id: dict[str, dict[str, Any]] = {}
    round_no = 0
    tools_ran = 0
    summary = ""
    inputs = {"messages": [SystemMessage(content=child.system_prompt),
                           HumanMessage(content=brief)]}
    try:
        for mode, payload in graph.stream(inputs, config=child_cfg,
                                          stream_mode=["custom", "updates"]):
            if mode == "custom":
                if payload.get("type") != "turn":
                    continue                      # 子 delta 不进父流：正文只在摘要里回收
                round_no = max(round_no, int(payload["round"]))
                for call in payload.get("tool_calls") or []:
                    calls_by_id[str(call.get("id"))] = call
                    writer({"type": "call", "subagent": sub, "tool": call["name"],
                            "round": payload["round"], "detail": detail_of(call)})
                if not payload.get("tool_calls") and payload.get("text"):
                    summary = str(payload["text"])
                continue
            if payload.get("__interrupt__"):
                continue                          # 子挂起不上抛：等流干净结束再看 st.next
            for msg in (payload.get("tools") or {}).get("messages") or []:
                if not isinstance(msg, ToolMessage):
                    continue
                tools_ran += 1
                writer({"type": "step", "subagent": sub, "tool": msg.name or "",
                        "ok": str(getattr(msg, "status", "success")) != "error",
                        "round": round_no,
                        "detail": detail_of(calls_by_id.get(str(msg.tool_call_id), {}))})
        st = graph.get_state(child_cfg)
        if st.next:
            # 守卫其二（R5）：子挂起 → 重建 GraphInterrupt 冒泡——父流出现 __interrupt__，
            # wait/resume 零特判走既有通道（P1b 实测；不许改成 yield 一条 wait 自派发）
            raise GraphInterrupt(tuple(i for t in st.tasks for i in t.interrupts))
        if not summary:
            summary = _last_reply(list(st.values.get("messages") or []))
    except GraphBubbleUp:
        raise                                     # 停止/中断不是失败：让父层原样收
    except Exception as exc:
        writer({"type": "sub", "phase": "fail", "ok": False, **sub,
                "elapsed_ms": int((time.monotonic() - t0) * 1000), "tools": tools_ran})
        raise ToolException(f"Subagent '{child.agent_id}' failed: {exc}") from exc
    writer({"type": "sub", "phase": "done", "ok": True, **sub,
            "elapsed_ms": int((time.monotonic() - t0) * 1000), "tools": tools_ran})
    return summary or "(subagent returned no summary)"
