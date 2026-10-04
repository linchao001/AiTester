"""pending 表：同会话多条并存、决策按队列顺序消费、级联清理。全是进程内内存态（裁定 2）。"""

from dataclasses import dataclass

import pytest

from aitester.services.pending import (
    CALL_DECIDED_DETAIL,
    PENDING_GONE_DETAIL,
    RESUME_NOT_READY_DETAIL,
    CallDecidedError,
    PendingEntry,
    PendingGoneError,
    PendingRegistry,
    ResumeNotReadyError,
)


@dataclass
class _Prepared:                 # 本测试不吃 PreparedRun 的真实字段，只当占位
    key: str = "case_design:sess_1"
    session_id: str = "sess_1"


def _item(call_id: str) -> dict:
    return {"call_id": call_id, "tool": "write", "action": "写入项目目录外的文件",
            "target": "../out.md", "command": "", "cwd": ""}


def _entry(run_id: str, call_ids: tuple[str, ...], session_id: str = "sess_1",
           project_id: str = "p1", created_at: float = 0) -> PendingEntry:
    return PendingEntry(
        run_id=run_id, thread_id=run_id, prepared=_Prepared(), perm_mode="boundary",
        project_id=project_id, project_dir="D:/proj",
        session_key=f"case_design:{session_id}", session_id=session_id,
        agent_id="case_design", prefix_text="[moc",
        steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}],
        created_at=created_at, queue=[_item(c) for c in call_ids])


def test_multiple_pending_runs_coexist_per_session() -> None:
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1",)))
    reg.open(_entry("r2", ("c2",)))
    assert [r.run_id for r in reg.view("sess_1")] == ["r1", "r2"]      # 裁定 10：新发送不隐式作废旧 pending


def test_view_for_scopes_by_agent_and_project() -> None:
    """R14：UI 取数按「智能体 × 项目」——挂起会话没落盘，刷新后只能按这一维找回来。"""
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1",), session_id="sess_orphan"))
    reg.open(_entry("r2", ("c2",), project_id="p2"))
    reg.open(_entry("r3", ("c3",), session_id="sess_9"))
    assert [r.run_id for r in reg.view_for("case_design", "p1")] == ["r1", "r3"]
    assert [r.run_id for r in reg.view_for("case_design", "p2")] == ["r2"]
    assert reg.view_for("review", "p1") == []                          # 别的智能体一条不给


def test_answer_requires_known_call_id() -> None:
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1",)))
    reg.answer("r1", "c1", "approve", False)
    assert reg.peek("r1").decided == [{**_item("c1"), "decision": "approve"}]
    with pytest.raises(CallDecidedError) as exc:
        reg.answer("r1", "c1", "approve", False)
    assert exc.value.detail == CALL_DECIDED_DETAIL
    with pytest.raises(CallDecidedError) as exc:
        reg.answer("r1", "cX", "approve", False)                        # R7：未知 call_id 同文案
    assert exc.value.detail == CALL_DECIDED_DETAIL                       # 同文案不是巧合：走查表按这一句渲染


def test_unknown_run_raises_gone() -> None:
    reg = PendingRegistry()
    with pytest.raises(PendingGoneError) as exc:
        reg.answer("nope", "c1", "approve", False)
    assert exc.value.detail == PENDING_GONE_DETAIL
    assert reg.take("nope") is None
    with pytest.raises(PendingGoneError):
        reg.take_resume("nope")                                          # 未知 run 与「还没批」是两种失败


def test_take_resume_consumes_answers_in_queue_order() -> None:
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1", "c2")))
    with pytest.raises(ResumeNotReadyError):
        reg.take_resume("r1")                                            # 一条都没批就没有可喂的值（R6）
    reg.answer("r1", "c2", "reject", False)
    reg.answer("r1", "c1", "approve", True)
    assert reg.take_resume("r1") == {"call_id": "c1", "decision": "approve", "remember": True}
    assert reg.take_resume("r1") == {"call_id": "c2", "decision": "reject", "remember": False}
    with pytest.raises(ResumeNotReadyError):
        reg.take_resume("r1")


def test_peek_resume_does_not_consume() -> None:
    """guard 用 peek_resume：只问「现在能不能续」，绝不烧决策。

    真正的消费必须等续跑真开跑（Task 6 在生成器体里调 take_resume）——若在 guard 处就落 consumed，
    「取到决策却没喂出去」的那次请求会让下一条决策灌进上一条中断位（LangGraph 按位置匹配）。
    所以 peek 两次结果一致、且 peek 之后 take 仍能喂恰好一次。
    """
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1",)))
    reg.answer("r1", "c1", "approve", True)
    expected = {"call_id": "c1", "decision": "approve", "remember": True}
    assert reg.peek_resume("r1") == expected                             # 第一次 peek
    assert reg.peek_resume("r1") == expected                             # 重复 peek 不消耗：仍是同一条
    assert reg.take_resume("r1") == expected                             # peek 后仍可喂恰好一次
    with pytest.raises(ResumeNotReadyError):
        reg.take_resume("r1")                                            # 喂过一次即耗尽：peek 没多喂出第二条


def test_peek_resume_not_ready_when_all_consumed() -> None:
    """take_resume 抽干后 peek_resume 报 ResumeNotReady——这正是它能当路由 400 guard 的理由。"""
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1",)))
    reg.answer("r1", "c1", "approve", False)
    reg.take_resume("r1")                                               # 已答项全部喂出
    with pytest.raises(ResumeNotReadyError) as exc:
        reg.peek_resume("r1")
    assert exc.value.detail == RESUME_NOT_READY_DETAIL


def test_peek_resume_unknown_run_raises_gone() -> None:
    """未知 run 与「还没批」是两种失败：Task 6 的 guard 顺序依赖 peek_resume 也抛 PendingGoneError。"""
    reg = PendingRegistry()
    with pytest.raises(PendingGoneError) as exc:
        reg.peek_resume("nope")
    assert exc.value.detail == PENDING_GONE_DETAIL


def test_waiting_returns_unanswered_and_partitions_queue() -> None:
    """R14 另一侧：Task 7 直接拿 waiting() 渲染未答卡片——它须恰好给未答项，与 decided 互补分完队列。"""
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1", "c2")))
    reg.answer("r1", "c1", "approve", False)
    entry = reg.peek("r1")
    assert entry.waiting() == [_item("c2")]                             # 只剩未答的 c2
    decided_ids = {c["call_id"] for c in entry.decided}
    waiting_ids = {c["call_id"] for c in entry.waiting()}
    queue_ids = {c["call_id"] for c in entry.queue}
    assert decided_ids & waiting_ids == set()                          # 二者互斥
    assert decided_ids | waiting_ids == queue_ids                       # 合起来正好覆盖队列


def test_view_orders_by_created_at_not_insertion_order() -> None:
    """view/view_for 用 sorted(key=created_at)：既有 fixture 全用 created_at=0，删掉 sorted 也照绿——
    这条故意让插入顺序与时间顺序相反，锁住排序真的在按 created_at 走。"""
    reg = PendingRegistry()
    reg.open(_entry("r_late", ("c1",), created_at=5.0))                 # 先插、时间靠后
    reg.open(_entry("r_early", ("c2",), created_at=1.0))                # 后插、时间靠前
    assert [r.run_id for r in reg.view("sess_1")] == ["r_early", "r_late"]
    assert [r.run_id for r in reg.view_for("case_design", "p1")] == ["r_early", "r_late"]


def test_update_hold_appends_new_waiting_without_losing_decided() -> None:
    reg = PendingRegistry()
    entry = _entry("r1", ("c1",))
    reg.open(entry)
    reg.answer("r1", "c1", "approve", False)
    reg.take_resume("r1")
    reg.update_hold(entry, prefix="[mock 继续", steps=[], waiting=[_item("c2"), _item("c1")])
    assert [c["call_id"] for c in reg.peek("r1").queue] == ["c1", "c2"]   # 按 call_id 去重
    assert reg.peek("r1").prefix_text == "[mock 继续"
    assert reg.peek("r1").decided == [{**_item("c1"), "decision": "approve"}]


def test_update_hold_is_a_noop_after_removal() -> None:
    reg = PendingRegistry()
    entry = _entry("r1", ("c1",))
    reg.open(entry)
    reg.take("r1")                                                       # 已被停止摘除
    reg.update_hold(entry, prefix="x", steps=[], waiting=[_item("c2")])
    assert reg.peek("r1") is None                                        # 不复活


def test_remembered_table_is_per_session_and_live() -> None:
    reg = PendingRegistry()
    reg.remembered_for("case_design:sess_1").add("write|D:/x")
    assert reg.remembered_for("case_design:sess_1") == {"write|D:/x"}     # 同一对象：gate 写、判定读
    assert reg.remembered_for("case_design:sess_2") == set()              # R5：不跨会话解锁


def test_drop_session_clears_entries_and_remembered() -> None:
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1",)))
    reg.open(_entry("r2", ("c2",), session_id="sess_2"))
    reg.remembered_for("case_design:sess_1").add("write|D:/x")
    assert reg.drop_session("sess_1") == ["r1"]
    assert reg.peek("r1") is None and reg.peek("r2") is not None
    assert reg.view("sess_1") == []
    assert reg.remembered_for("case_design:sess_1") == set()
