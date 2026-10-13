"""进程级 checkpointer 单例：实例每请求现装现弃，挂起状态必须跨请求活着。

装配期每次 `build_agent_graph` 都新建图，但同一个 run 的 pending→resume 是两个请求，
所以 checkpointer 绝不能 per-build——否则续跑拿到空线程，中断状态直接蒸发。
清理口是 `delete_thread`（langgraph-checkpoint 4.2.0 实测：未知 id 不报错）。

**不是业务 durable owner。** 本对象只服务「同一次 SSE run 内」的图挂起
（工具权限 gate / 子图 interrupt → ``/chat/resume/stream``）。跨回合、跨人审、
进程重启后的用例设计进度，唯一真相是项目目录 ``design/ledger.json``
（见 ``case_design.ledger``）。run 终态必须 ``drop_thread``——检查点按设计不跨回合存活。
"""

from __future__ import annotations

import uuid

from langgraph.checkpoint.memory import InMemorySaver

_SAVER = InMemorySaver()


def get_checkpointer() -> InMemorySaver:
    return _SAVER


def new_thread_id() -> str:
    return uuid.uuid4().hex


def drop_thread(thread_id: str) -> None:
    """终态（落全行或截断）后释放该 run 的检查点：不删就是每轮泄漏一条线程。"""
    _SAVER.delete_thread(thread_id)
