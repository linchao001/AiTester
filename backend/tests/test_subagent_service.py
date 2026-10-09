"""SSE 服务层端到端：真装配链路上的子智能体（task 注入、父通道审批、结构锁）。

与 test_subagent_graph 的分工：那边吃 drive/塑形的机制单测与图级锁；这边走
create_app → ChatService → router 的全链，只测「装配真的把缝接上了」。
"""

from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from streaming_fakes import ScriptedProvider
from test_agent_runtime import _runtime
from test_chat_auth_api import _stream_resume
from test_chat_stream_api import _NoopKbManager, _pid, _stream

from aitester.adapters.llm import MockProvider
from aitester.config import Settings
from aitester.main import create_app
from aitester.services import ChatService
from aitester.services.agent_runtime import AgentRuntime


def _app(tmp_path: Path, provider):
    """带待批表的装配：审批链路要 /chat/pending 与服务写同一份表（与 test_chat_auth_api 同口径）。"""
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        kb_manager=_NoopKbManager(),
    )
    application.state.chat_service = ChatService(
        provider=provider,
        agent_runtime=application.state.agent_runtime,
        sessions=application.state.sessions,
        projects=application.state.project_config,
        pending=application.state.pending_registry,
    )
    return application


def _task_call(cid: str = "c1", desc: str = "调查失败用例") -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"id": cid, "name": "task", "args": {"description": desc}, "type": "tool_call"}])


def _write_call(cid: str, path: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"id": cid, "name": "write", "args": {"file_path": path, "content": "x"},
         "type": "tool_call"}])


def test_task_delegation_end_to_end(tmp_path: Path) -> None:
    """全链 happy path：sub 双帧 → 摘要回主上下文 → 终帧只含父步骤 → 会话两行。"""
    provider = ScriptedProvider([
        _task_call("c1", desc="调查失败用例"),   # 父 1：派活
        AIMessage(content="子摘要"),             # 子 1：直接作答
        AIMessage(content="主答"),               # 父 2：收摘要收尾
    ])
    application = _app(tmp_path, provider)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "派子调查", "agent_id": "case_design",
                              "project_id": pid})
    kinds = [e for e, _ in frames]
    assert kinds[0] == "start" and kinds[-1] == "done"
    subs = [d for e, d in frames if e == "sub"]
    assert subs[0] == {"phase": "start", "call_id": "c1", "name": "通用子智能体",
                       "title": "通用子智能体"}
    assert subs[1]["phase"] == "done" and subs[1]["ok"] is True
    assert subs[1]["tools"] == 0 and subs[1]["elapsed_ms"] >= 0
    # 子正文不进父流（R2）：用户看到的 delta 只有父两轮里非空的那份
    assert "".join(d["text"] for e, d in frames if e == "delta") == "主答"
    done = frames[-1][1]
    assert done["reply"] == "主答" and done["stopped"] is False
    assert [s["tool"] for s in done["steps"]] == ["task"]        # 子过程不进父步骤（R10）
    # 子体独立入参（R2）：System=子提示词、Human=简报；主会话原文不出现
    assert isinstance(provider.calls[1][0], SystemMessage)
    assert provider.calls[1][0].content.startswith("你是「通用子智能体」")
    assert provider.calls[1][1].content == "调查失败用例"
    assert all("派子调查" not in str(m.content) for m in provider.calls[1])
    # 主上下文只收摘要：父 2 的末条就是 task 的结果消息
    assert provider.calls[2][-1].content == "子摘要"
    rows = client.get(f"/api/chat/sessions/{done['session_id']}/messages", params={"agent_id": "case_design", "project_id": pid}).json()["messages"]
    assert [r["role"] for r in rows] == ["user", "assistant"]


def test_child_outside_write_waits_through_parent_approval(tmp_path: Path) -> None:
    """子写界外 → 父通道弹卡（wait 带来源）、挂起断流零落盘、批准续跑落盘、子步骤带标注。"""
    provider = ScriptedProvider([
        _task_call("c1", desc="落一个界外文件"),   # 父 1：派子写
        _write_call("w1", "../out.md"),            # 子 1：界外写
        AIMessage(content="子写完了"),              # 子 2（批准后续跑）
        AIMessage(content="主答"),                  # 父 2
    ])
    application = _app(tmp_path, provider)
    # 子体默认只读四件：写面要经设置开放——「设置可调」链路的真装配锁
    application.state.capability_config.set_agent_tools(
        "general-purpose", ["read", "grep_search", "glob_search", "web_search", "write"])
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "派子写界外", "agent_id": "case_design",
                              "project_id": pid, "perm_mode": "boundary"})
    kinds = [e for e, _ in frames]
    assert kinds[0] == "start" and kinds[-1] == "wait"           # 挂起不发 done（裁定 7）
    run_id, sid = frames[0][1]["run_id"], frames[0][1]["session_id"]
    child_calls = [d for e, d in frames if e == "call" and "subagent" in d]
    assert [(c["tool"], c["subagent"]["call_id"]) for c in child_calls] == [("write", "c1")]
    wait = frames[-1][1]
    assert wait["call_id"] == "w1" and wait["tool"] == "write"
    assert wait["subagent"] == {"call_id": "c1", "name": "通用子智能体",
                                "title": "通用子智能体"}          # R3 来源标注
    assert not (tmp_path / "out.md").exists()                     # 挂起零副作用
    runs = client.get("/api/chat/pending", params={"agent_id": "case_design",
                                                   "project_id": pid}).json()["runs"]
    assert [p["run_id"] for p in runs] == [run_id]
    assert runs[0]["waiting"][0]["subagent"]["call_id"] == "c1"   # 卡片能标出处
    r = client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "w1",
                                               "decision": "approve", "remember": False})
    assert r.status_code == 204
    second = _stream_resume(client, run_id)
    kinds2 = [e for e, _ in second]
    assert kinds2[0] == "start" and kinds2[-1] == "done"
    assert [d["phase"] for e, d in second if e == "sub"] == ["start", "done"]
    child_steps = [d for e, d in second if e == "step" and "subagent" in d]
    assert [(s["tool"], s["ok"]) for s in child_steps] == [("write", True)]
    assert child_steps[0]["subagent"]["call_id"] == "c1"          # 路由守卫：标注原样透传
    parent_steps = [d for e, d in second if e == "step" and "subagent" not in d]
    assert [s["tool"] for s in parent_steps] == ["task"]          # 子步骤不进父过程（R10）
    done = second[-1][1]
    assert done["reply"] == "主答" and done["stopped"] is False
    assert (tmp_path / "out.md").read_text(encoding="utf-8") == "x"
    rows = client.get(f"/api/chat/sessions/{sid}/messages", params={"agent_id": "case_design", "project_id": pid}).json()["messages"]
    assert [m["role"] for m in rows] == ["user", "assistant"]
    assert client.get("/api/chat/pending", params={"agent_id": "case_design",
                                                   "project_id": pid}).json()["runs"] == []


def test_unknown_subagent_type_is_a_plain_tool_error(tmp_path: Path) -> None:
    """R11：不在册的子体 → 零 sub 帧、英文错误进主上下文，模型下一轮可自纠。"""
    provider = ScriptedProvider([
        AIMessage(content="", tool_calls=[
            {"id": "c1", "name": "task",
             "args": {"description": "x", "subagent_type": "ghost"},
             "type": "tool_call"}]),
        AIMessage(content="改用直答"),
    ])
    application = _app(tmp_path, provider)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "派错子体", "agent_id": "case_design",
                              "project_id": pid})
    assert not [1 for e, _ in frames if e == "sub"]        # 一个 sub 帧都没有
    assert frames[-1][0] == "done" and frames[-1][1]["reply"] == "改用直答"
    err = provider.calls[1][-1]
    assert isinstance(err, ToolMessage) and err.status == "error"
    assert "Unknown subagent_type 'ghost'" in str(err.content)
    assert ("Available subagents: case_review, case_review_blind, general-purpose"
            in str(err.content))


def test_assembly_level_structure_locks(tmp_path: Path) -> None:
    """R7/R13 装配级锁：父面 task 在场；子面四件只读、无 task、provider 继承注入；
    kb_assistant 恒无 task（平台路径不注入）。"""
    runtime, _, _ = _runtime(tmp_path)
    mock = MockProvider()
    parent = runtime.build("case_design", "s1", provider_override=mock)
    tools = {t.tool_id(): t for t in parent.tools}
    assert "task" in tools
    child = tools["task"].build_child("general-purpose")
    assert {t.tool_id() for t in child.tools} == {"read", "grep_search",
                                                  "glob_search", "web_search"}
    review = tools["task"].build_child("case_review")
    assert {t.tool_id() for t in review.tools} == {"read", "grep_search", "glob_search",
                                                   "web_search"}   # kb 未注入：面收缩为四件
    blind = tools["task"].build_child("case_review_blind")
    assert {t.tool_id() for t in blind.tools} == {"read"}
    assert child.provider.inner is mock                   # provider_override 继承（R13 测试缝）：包的就是那一个
    assert child.provider.usage is parent.usage            # 子共用父账本（CM-6）：一回合只有一本
    assert child.system_prompt.startswith("你是「通用子智能体」")
    kb_inst = runtime.build("kb_assistant", "kb-console", provider_override=mock)
    assert "task" not in {t.tool_id() for t in kb_inst.tools}


def test_parallel_table_follows_the_settings_face(tmp_path: Path) -> None:
    """R14 真装配锁：默认只读四件 → 可并行；设置页给子勾上 write → 该子自动退回串行。
    「并行与否」不是隐藏闸门，是用户自己勾出来的，且 gate 塑形与 drive 读同一份表。"""
    runtime, capability, _ = _runtime(tmp_path)
    task_tool = {t.tool_id(): t for t in runtime.build(
        "case_design", "s1", provider_override=MockProvider()).tools}["task"]
    assert task_tool.parallel == {"general-purpose": True, "case_review": True,
                                  "case_review_blind": True}
    # 同一份勾选取决第二件事：模型可见的工具面清单——父模型只有看到 write 在场才敢派写任务
    assert "[tool face: read, grep_search, glob_search, web_search]" in task_tool.description
    capability.set_agent_tools("general-purpose",
                               ["read", "grep_search", "glob_search", "web_search", "write"])
    reopened = {t.tool_id(): t for t in runtime.build(
        "case_design", "s2", provider_override=MockProvider()).tools}["task"]
    assert reopened.parallel == {"general-purpose": False, "case_review": True,
                                 "case_review_blind": True}
    assert ("[tool face: read, grep_search, glob_search, web_search, write]"
            in reopened.description)


def test_reviewer_child_gets_kb_injection(tmp_path: Path) -> None:
    """T7：注入 kb 后评审子面收敛为五件，knowledge_search 拿到父装配同一份 KB。"""
    _, capability, model_config = _runtime(tmp_path)
    fake_kb = _NoopKbManager()
    runtime = AgentRuntime(capability, model_config, kb=fake_kb)
    parent = runtime.build("case_design", "s1", provider_override=MockProvider())
    task_tool = {t.tool_id(): t for t in parent.tools}["task"]
    child = task_tool.build_child("case_review")
    tools = {t.tool_id(): t for t in child.tools}
    assert set(tools) == {"read", "grep_search", "glob_search", "web_search",
                          "knowledge_search"}
    assert tools["knowledge_search"].kb is fake_kb
