"""上下文读数接线：一本账从装配根贯穿到落盘行与服务层 done 帧。

本文件只钉「接线」——尺（context/meter）、账（context/usage）、闸（context/budget）、
缝（adapters/llm/metered）各自的行为已由 C1–C4 钉死；这里证的是它们被装进真链路后
仍然只有**一本**、并且那本账的读数原样抵达磁盘与终帧（SSE 那一层的白名单是
`interaction/router.py` 的 `event["context"]`，严格取键，缺键即 KeyError）。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from test_chat_stream import _Scripted, _runtime, project  # noqa: F401  复用服务层夹具，本片不重写一遍装配

from aitester.adapters.llm import MockProvider
from aitester.adapters.llm.metered import MeteredProvider
from aitester.adapters.tools import build_default_registry
from aitester.context.usage import ContextUsage
from aitester.memory import FileMemoryStore
from aitester.services import ChatService
from aitester.services.session_store import ChatMessage, SessionStore


def test_registry_injects_one_ledger_into_every_tool():
    u = ContextUsage(window=1000)
    reg = build_default_registry(usage=u)
    tools = reg.as_langchain_tools()
    assert tools
    assert all(t.usage is u for t in tools)          # 一本账，不是每个工具各记一遍


def test_registry_without_usage_still_builds():
    reg = build_default_registry()
    assert all(t.usage is None for t in reg.as_langchain_tools())


def test_chat_message_backfills_missing_context_narrowly():
    old = ChatMessage.from_dict({"role": "assistant", "content": "x", "ts": 1,
                                 "steps": None, "stopped": False})
    assert old.context is None                       # 老行：缺节不是坏行
    junk = ChatMessage.from_dict({"role": "assistant", "content": "x", "ts": 1,
                                  "context": "不是字典"})
    assert junk.context is None                       # 窄背填：只认 dict，脏数据不伪装成读数
    ok = ChatMessage.from_dict({"role": "assistant", "content": "x", "ts": 1,
                                "context": {"rounds": 2}})
    assert ok.context == {"rounds": 2}


def test_file_memory_roundtrip_keeps_context(tmp_path):
    """spec §6「追加写不丢已有节」：读数经 memory 层进会话行，再原样读回来。"""
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, "proj_x")
    sid = store.new_id()                             # 会话键必须是 sess_* 形态（SessionStore.create 的硬校验）
    key = f"case_design:{sid}"
    mem.save(key, "user", "问")
    mem.save(key, "assistant", "答",
             steps=[{"tool": "read", "ok": True}], context={"rounds": 3, "window": 1000})
    rows = store.messages(sid)
    assert rows[-1].context == {"rounds": 3, "window": 1000}
    assert rows[0].context is None                    # 老口径调用（不传）照常落盘


def test_agent_instance_carries_one_ledger_for_provider_and_tools(tmp_path):
    inst = _runtime(tmp_path).build("case_design", "sess_ctx",
                                    provider_override=MockProvider())
    assert inst.tools and isinstance(inst.provider, MeteredProvider)
    assert inst.provider.usage is inst.usage          # 装配根与装饰器拿着同一本账
    assert all(t.usage is inst.usage for t in inst.tools)
    assert inst.usage.window == 0                     # 替身路径不解析模型 ⇒ 未知就是 0


def test_done_frame_context_matches_the_persisted_line(tmp_path, project):
    """终帧与落盘必须同源同一份快照——两处分算就是「门上的话会说谎」同族。"""
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                      sessions=store, projects=svc_proj)
    prepared = svc.prepare("", "生成用例", "case_design", pid)
    done = list(svc.stream_turn(prepared))[-1]
    ctx = done["context"]
    assert ctx["rounds"] >= 1 and ctx["peak_occupancy"] > 0
    assert ctx["occupancy_source"] == "estimated"     # Mock 不返 usage：不许冒领真值
    assert store.messages(prepared.session_id)[-1].context == ctx


def test_resume_continues_the_same_ledger(tmp_path, project):
    """挂起→批准→续跑：rounds 递增而非归零（CM-6 的连续性要求）。

    续跑复用 `entry.prepared`（`services/chat.py:382`），所以 `prepared.usage` 就是那本
    正在被写的账——先读它拿挂起时的回合数，再看续跑后的终帧是否在同一本上往上加。
    """
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = ChatService(
        provider=_Scripted([
            AIMessage(content="", tool_calls=[
                {"id": "c1", "name": "write",
                 "args": {"file_path": "../escape_ctx.md", "content": "x"},
                 "type": "tool_call"}]),
            AIMessage(content="写好了"),
        ]),
        agent_runtime=_runtime(tmp_path), sessions=store, projects=svc_proj)
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    list(svc.stream_turn(prepared, run_id="ctx_resume"))      # 发到挂起，第一段没有 done 帧
    r1 = prepared.usage.rounds
    assert r1 >= 1
    svc.approve("ctx_resume", "c1", "approve", False)
    _, stream = svc.resume_stream("ctx_resume")
    done = list(stream)[-1]
    assert done["type"] == "done"
    assert done["context"]["rounds"] > r1                     # 同一本账接着记
    assert store.messages(prepared.session_id)[-1].context == done["context"]
