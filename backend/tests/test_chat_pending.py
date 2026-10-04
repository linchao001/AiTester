"""pending 表：同会话多条并存、决策按队列顺序消费、级联清理。全是进程内内存态（裁定 2）。"""

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from test_chat_stream import _runtime, project  # noqa: F401  复用服务层夹具，本片不重写一遍装配
from streaming_fakes import ChunkedStreamMixin

from aitester.orchestration.checkpoint import get_checkpointer
from aitester.orchestration.run_control import RunControl
from aitester.services import ChatService
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
from aitester.services.project_config import ProjectConfigError
from aitester.services.session_store import SessionStore


class _Scripted(ChunkedStreamMixin):
    """剧本用完仍要答得出：续跑后模型还要收一句，脚本见底不能炸测试。"""

    name = "scripted"
    model_ref = "scripted/model"

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_Scripted":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        item = self._script.pop(0) if self._script else AIMessage(content="收到")
        if isinstance(item, Exception):     # 脚本里放异常实例：失败收摊路径要它
            raise item
        return item


def _call_round(*paths: str, text: str = "") -> AIMessage:
    """write 调用轮：c1/c2… 依序编号，正文可给（停止用例要有前缀可落）。"""
    return AIMessage(content=text, tool_calls=[
        {"id": f"c{i}", "name": "write", "args": {"file_path": p, "content": "x"},
         "type": "tool_call"} for i, p in enumerate(paths, start=1)])


def _svc(tmp_path: Path, svc_proj, provider) -> ChatService:
    return ChatService(provider=provider, agent_runtime=_runtime(tmp_path),
                       sessions=SessionStore(tmp_path / "sessions"), projects=svc_proj)


def _hold(tmp_path: Path, svc_proj, pid: str, perm_mode: str, run_id: str,
          script: list[AIMessage]) -> tuple[ChatService, str]:
    """发到挂起：回服务与 session_id，pending 表里躺着一条等待的。"""
    svc = _svc(tmp_path, svc_proj, _Scripted(script))
    prepared = svc.prepare("", "写界外", "case_design", pid, perm_mode)
    list(svc.stream_turn(prepared, run_id=run_id))
    return svc, prepared.session_id


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


# —— 服务层接线：prepare → stream_turn → 挂起入表 → resume_stream 的整链路。run_id 一律 cs 前缀——
# _SAVER 是进程级单例，test_chat_auth.py 收尾时 r2/r3/r7 线程上挂着中断态，同名开局即串台。


def test_resume_carries_forward_the_hung_segment_steps_and_prefix(tmp_path, project) -> None:
    """续跑接着算：前一段已执行的过程行与已投递正文不能因为换了一段流就丢。"""
    svc_proj, pid, root = project
    store = SessionStore(tmp_path / "sessions")
    first = AIMessage(content="先写个界内的", tool_calls=[
        {"id": "k1", "name": "write", "args": {"file_path": "notes.md", "content": "n"},
         "type": "tool_call"}])
    svc = _svc(tmp_path, svc_proj,
               _Scripted([first, _call_round("../a.md"), AIMessage(content="都写了")]))
    prepared = svc.prepare("", "两步", "case_design", pid, "boundary")
    list(svc.stream_turn(prepared, run_id="cs10"))
    assert [s["tool"] for s in svc.pending.peek("cs10").steps] == ["write"]     # 界内那条已执行
    svc.approve("cs10", "c1", "approve", False)
    _, stream = svc.resume_stream("cs10")
    done = list(stream)[-1]
    assert [s["tool"] for s in done["steps"]] == ["write", "write"]             # 界内 + 批准后执行的界外
    assert [s["tool"] for s in store.messages(prepared.session_id)[-1].steps] == ["write", "write"]
    assert (root / "notes.md").exists() and (tmp_path / "a.md").exists()   # 界内落项目目录，界外落在其外


def test_stop_during_a_live_pending_fold_lands_the_prefix_as_stopped(tmp_path, project) -> None:
    """R8：流还活着时按停止（/chat/stop 打到正在挂起的折叠）→ 落已投递前缀的 stopped 行。

    走 finish.reply 会落一条空正文、stopped=False 的「已完成回答」：用户看到的半句没了，
    气泡还会当收尾渲染。done 帧必须照发——前端靠它收流并让授权卡退场。
    """
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _svc(tmp_path, svc_proj,
               _Scripted([_call_round("../escape.md", text="我先想想"), AIMessage(content="好的")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    control = RunControl()
    events = []
    for event in svc.stream_turn(prepared, control, run_id="cs9"):
        events.append(event)
        if event["type"] == "wait":
            control.cancel()          # 真实停止就是把这条折叠的 control 置位
    assert events[-1]["type"] == "done" and events[-1]["stopped"] is True
    assert events[-1]["reply"] == "我先想想"
    rows = store.messages(prepared.session_id)
    assert rows[-1].content == "我先想想" and rows[-1].stopped is True
    assert svc.pending.peek("cs9") is None


def test_failed_resume_releases_entry_and_thread(tmp_path, project) -> None:
    """图跑挂不留尸体：条目和线程一起摘，否则那条 pending 带着已消费的决策永远 400。"""
    svc_proj, pid, _ = project
    svc = _svc(tmp_path, svc_proj, _Scripted([_call_round("../a.md"), RuntimeError("续跑炸了")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    list(svc.stream_turn(prepared, run_id="cs11"))
    svc.approve("cs11", "c1", "approve", False)
    _, stream = svc.resume_stream("cs11")
    with pytest.raises(RuntimeError):
        list(stream)
    assert svc.pending.peek("cs11") is None
    assert get_checkpointer().get_tuple({"configurable": {"thread_id": "cs11"}}) is None


def test_free_mode_never_holds(tmp_path, project) -> None:
    """默认档零行为（红线）：gate 不在场、事件流照旧收到 done、pending 表空。"""
    svc_proj, pid, _ = project
    svc = _svc(tmp_path, svc_proj, _Scripted([AIMessage(content="直答")]))
    prepared = svc.prepare("", "生成用例", "case_design", pid, "free")
    assert prepared.gate is None and prepared.perm_mode == "free"
    assert prepared.project_dir == str(Path(str(tmp_path / "reqs")).resolve())
    events = list(svc.stream_turn(prepared, run_id="csf"))
    assert events[-1]["type"] == "done"
    assert svc.pending.view(prepared.session_id) == []


def test_invalid_perm_mode_detail_verbatim(tmp_path, project) -> None:
    from aitester.orchestration.auth_rules import PERM_MODE_DETAIL, PermModeError
    svc_proj, pid, _ = project
    svc = _svc(tmp_path, svc_proj, _Scripted([AIMessage(content="直答")]))
    with pytest.raises(PermModeError) as exc:
        svc.prepare("", "hi", "case_design", pid, "yolo")
    assert exc.value.detail == PERM_MODE_DETAIL


def test_boundary_out_of_bounds_holds_without_persisting(tmp_path, project) -> None:
    """裁定 7+8：挂起即断流、一条不落；界外文件确实还没被写出去（走查项 4 的单测锁）。"""
    svc_proj, pid, root = project
    store = SessionStore(tmp_path / "sessions")
    svc = _svc(tmp_path, svc_proj, _Scripted([_call_round("../escape.md"), AIMessage(content="好的")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    events = list(svc.stream_turn(prepared, run_id="cs1"))
    assert [e["type"] for e in events][-1] == "wait"
    assert store.messages(prepared.session_id) == []
    assert not (tmp_path / "escape.md").exists()
    entry = svc.pending.peek("cs1")
    assert entry is not None and entry.thread_id == "cs1"
    assert [c["call_id"] for c in entry.queue] == ["c1"]
    assert entry.prefix_text == "" and entry.perm_mode == "boundary"


def test_approve_then_resume_persists_exactly_one_row(tmp_path, project) -> None:
    """裁定 8 的正面锁：批准续跑到收尾，磁盘只有一 user 一 assistant；线程收摊。"""
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _svc(tmp_path, svc_proj, _Scripted([_call_round("../escape.md"), AIMessage(content="写好了")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    list(svc.stream_turn(prepared, run_id="cs2"))
    svc.approve("cs2", "c1", "approve", False)
    sid, stream = svc.resume_stream("cs2")
    events = list(stream)
    assert sid == prepared.session_id
    assert events[-1]["type"] == "done" and events[-1]["reply"] == "写好了"
    assert (tmp_path / "escape.md").read_text(encoding="utf-8") == "x"
    rows = store.messages(sid)
    assert [r.role for r in rows] == ["user", "assistant"]
    assert svc.pending.peek("cs2") is None
    assert get_checkpointer().get_tuple({"configurable": {"thread_id": "cs2"}}) is None


def test_reject_then_resume_answers_without_writing(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _svc(tmp_path, svc_proj, _Scripted([_call_round("../escape.md"), AIMessage(content="好的，不写了")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    list(svc.stream_turn(prepared, run_id="cs3"))
    svc.approve("cs3", "c1", "reject", False)
    _, stream = svc.resume_stream("cs3")
    done = list(stream)[-1]
    assert done["type"] == "done" and done["reply"] == "好的，不写了"
    assert not (tmp_path / "escape.md").exists()
    assert done["steps"] == []                                   # R9：拒绝不进过程行（事件侧口径）
    # 磁盘侧同一条锁：空过程行落盘归一为 null（session_store.append 的 `steps or None`，
    # test_session_store.py:65 钉死），所以这里断「没有过程行」而不是「是空 list」——
    # assistant 行的 steps 永远不携空数组上读侧，落盘契约决定了这一层只能这么断。
    assert not store.messages(prepared.session_id)[-1].steps


def test_second_interrupt_appends_to_the_same_entry(tmp_path, project) -> None:
    """两条待批串行挂：第二条的 wait 追加进同一条目，已答的不丢、去重按 call_id。"""
    svc_proj, pid, _ = project
    svc = _svc(tmp_path, svc_proj,
               _Scripted([_call_round("../a.md", "../b.md", text="第一段思考"),
                          AIMessage(content="两个都写了")]))
    prepared = svc.prepare("", "并行两个", "case_design", pid, "strict")
    list(svc.stream_turn(prepared, run_id="cs4"))
    svc.approve("cs4", "c1", "approve", False)
    _, stream = svc.resume_stream("cs4")
    assert [e["type"] for e in stream][-1] == "wait"               # 又挂一次：仍是断流收尾
    entry = svc.pending.peek("cs4")
    assert [c["call_id"] for c in entry.queue] == ["c1", "c2"]
    assert entry.decided == [{**entry.queue[0], "decision": "approve"}]   # R14：六键跟着一起回显
    assert entry.prefix_text == "第一段思考"                        # 续跑段没新正文：前缀还是上一段那份
    assert not (tmp_path / "a.md").exists()                        # 批准的也要等 c2 决策后才执行（spec 风险节）


def test_abandoned_resume_feeds_the_same_decision(tmp_path, project) -> None:
    """评审锁：续跑请求拿到决策却没开跑（客户端在首帧前断开）时，决策绝不能被烧掉。

    烧掉之后队列里下一条「已答未喂」会被喂进上一条的中断位——位置匹配（实测），
    最坏情形是用户拒过的操作被当成批准执行。
    """
    svc_proj, pid, _ = project
    svc = _svc(tmp_path, svc_proj, _Scripted([_call_round("../a.md"), AIMessage(content="写好了")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    list(svc.stream_turn(prepared, run_id="cs7"))
    svc.approve("cs7", "c1", "approve", False)
    _, abandoned = svc.resume_stream("cs7")                 # 一次 next 都没跑
    assert svc.pending.peek("cs7").consumed == []           # 没开跑就不该落 consumed
    _, stream = svc.resume_stream("cs7")                    # 同一条决策仍喂得出同一个中断位
    done = list(stream)[-1]
    assert done["type"] == "done" and done["reply"] == "写好了"
    assert (tmp_path / "a.md").exists()                    # 批准的那次写入真发生了


def test_stop_while_pending_persists_prefix_and_kills_resume(tmp_path, project) -> None:
    """裁定 10 第二条：待批期间停止 → 落 stopped 截断行、条目摘除、再续跑必 404 文案。"""
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _svc(tmp_path, svc_proj,
               _Scripted([_call_round("../escape.md", text="我先想想"), AIMessage(content="好的")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    list(svc.stream_turn(prepared, run_id="cs5"))
    assert svc.pending.peek("cs5").prefix_text == "我先想想"
    assert svc.cancel_pending("cs5") is True
    assert svc.cancel_pending("cs5") is False                       # 已摘除：二次停止不再落盘
    rows = store.messages(prepared.session_id)
    assert rows[-1].content == "我先想想" and rows[-1].stopped is True
    assert get_checkpointer().get_tuple({"configurable": {"thread_id": "cs5"}}) is None
    assert svc.pending.peek("cs5") is None
    with pytest.raises(PendingGoneError) as exc:               # 顶部已 import（Task 5 那组用过）
        svc.resume_stream("cs5")
    assert exc.value.detail == PENDING_GONE_DETAIL


def test_resume_reuses_the_same_project_guard_detail(tmp_path, project) -> None:
    """守门只有一段：挂起后删掉项目目录，续跑必被同一条中文 detail 拦下且没建出目录。"""
    svc_proj, pid, root = project
    svc, _ = _hold(tmp_path, svc_proj, pid, "boundary", "cs6",
                   [_call_round("../escape.md"), AIMessage(content="好的")])
    shutil.rmtree(root)
    svc.approve("cs6", "c1", "approve", False)
    with pytest.raises(ProjectConfigError) as exc:
        svc.resume_stream("cs6")
    assert exc.value.detail == (
        f"项目「订单系统」的目录 {root} 不存在或不可访问，请到项目页确认路径")
    assert not root.exists()
    assert not (tmp_path / "escape.md").exists()


def test_drop_session_releases_pending_and_thread(tmp_path, project) -> None:
    """验收 10 的服务侧：删会话级联摘 pending 并释放检查点线程。"""
    svc_proj, pid, _ = project
    svc, sid = _hold(tmp_path, svc_proj, pid, "boundary", "cs8",
                     [_call_round("../escape.md"), AIMessage(content="好的")])
    assert svc.pending.peek("cs8") is not None
    svc.drop_session(sid)
    assert svc.pending.peek("cs8") is None
    assert get_checkpointer().get_tuple({"configurable": {"thread_id": "cs8"}}) is None
