"""SSE 传输层契约：帧序列、守门仍在流前、流中失败走事件、stop 命中在途 run。

注意：starlette TestClient 会把响应体整段缓冲后才交出来（实测每帧到达时刻相同），
所以这里只断「帧的顺序与内容」，不断节奏——逐字观感属真机走查。
"""

import threading
import time
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from fastapi.testclient import TestClient

from aitester.adapters.llm import MockProvider
from aitester.config import Settings
from aitester.main import create_app
from aitester.services import ChatService
from streaming_fakes import ChunkedStreamMixin, sse_frames


class _NoopKbManager:
    kb_root_dir = "kb-root"

    def start(self):
        pass

    def close_all(self, timeout: float = 30.0):
        pass

    async def run_job(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})

    def run_job_sync(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})


def _app(tmp_path: Path, provider=None):
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        kb_manager=_NoopKbManager(),
    )
    # brief 原码只在 provider 非 None 时替换 ChatService；create_app 默认那份走模型配置
    # 解析，fresh 配置没有默认模型 → prepare 直接 400。缺省注入 MockProvider 才有流可断
    application.state.chat_service = ChatService(
        provider=provider if provider is not None else MockProvider(),
        agent_runtime=application.state.agent_runtime,
        sessions=application.state.sessions,
        projects=application.state.project_config,
    )
    return application


def _pid(application, tmp_path: Path) -> str:
    root = tmp_path / "reqs"
    root.mkdir(exist_ok=True)
    return application.state.project_config.create(
        name="订单系统", desc="", dir_=str(root), agents=["case_design"])["id"]


def _stream(client: TestClient, payload: dict) -> list[tuple[str, dict]]:
    with client.stream("POST", "/api/chat/send/stream", json=payload) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8")
        return sse_frames(resp)


def test_stream_event_sequence_and_single_terminal(tmp_path: Path) -> None:
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    events = _stream(client, {"message": "生成用例", "agent_id": "case_design",
                              "project_id": pid})
    kinds = [e for e, _ in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"                      # 唯一终态
    assert kinds.count("done") == 1 and "error" not in kinds
    start = dict(events[0][1])
    assert start["session_id"].startswith("sess_")
    assert len(start["run_id"]) == 32               # uuid4().hex：停止要用它
    assert "".join(d["text"] for e, d in events if e == "delta") == "[mock] 生成用例"
    done = events[-1][1]
    assert done["reply"] == "[mock] 生成用例"
    assert done["steps"] == [] and done["stopped"] is False
    assert done["title"] == "生成用例"
    sid = done["session_id"]
    rows = client.get(f"/api/chat/sessions/{sid}/messages").json()["messages"]
    assert [r["role"] for r in rows] == ["user", "assistant"]


def test_guard_still_plain_http_with_zero_frames(tmp_path: Path) -> None:
    """守门未过 = 普通 4xx 且一帧都不发：前端沿用既有 ApiError→toast 路径，detail 逐字不变。"""
    application = _app(tmp_path)
    client = TestClient(application)
    r = client.post("/api/chat/send/stream", json={"message": "hi", "agent_id": "case_design"})
    assert r.status_code == 400
    assert r.json()["detail"] == "请先选择项目，再发送消息"
    pid = _pid(application, tmp_path)
    r2 = client.post("/api/chat/send/stream",
                     json={"session_id": "sess_deadbeef", "message": "hi",
                           "agent_id": "case_design", "project_id": pid})
    assert r2.status_code == 404
    assert r2.json()["detail"] == "会话不存在或已被删除"


class _Boom(ChunkedStreamMixin):
    name = "fake"
    model_ref = "fake/model-x"

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_Boom":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        raise RuntimeError("调用 fake/model-x 失败: HTTP 401")

    def stream_messages(self, messages: list):
        raise RuntimeError("调用 fake/model-x 失败: HTTP 401")


def test_provider_failure_travels_as_error_event(tmp_path: Path) -> None:
    """流开始后 HTTP 已是 200，失败只能走 error 事件（迁移前这里是 502）。"""
    application = _app(tmp_path, provider=_Boom())
    pid = _pid(application, tmp_path)
    events = _stream(TestClient(application),
                     {"message": "hi", "agent_id": "case_design", "project_id": pid})
    kinds = [e for e, _ in events]
    assert kinds[-1] == "error"
    # _Boom 抛的是非 ProviderError 的内部异常：detail 只落固定中文，str(exc) 不进用户文案
    assert events[-1][1]["detail"] == "流式输出异常，本条回答未完成"
    # 失败不落盘：一条也不写（与迁移前同口径）
    assert TestClient(application).get(
        "/api/chat/sessions", params={"agent_id": "case_design", "project_id": pid}
    ).json() == {"sessions": []}


class _Slow(ChunkedStreamMixin):
    """每块之间睡 50ms × 40 块：给 stop 一个两秒的在途窗口，够慢机也来得及。

    started 在首块产出时置位——TestClient 整段缓冲（模块 docstring），主线程读不到
    「在途」窗口，后台线程等这个信号才有真在途的 stop 时机。
    """

    name = "slow"
    model_ref = "slow/model"

    def __init__(self) -> None:
        self.started = threading.Event()

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_Slow":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        return AIMessage(content="x" * 160)

    def stream_messages(self, messages: list):
        first = True
        for chunk in chunks_from_text("x" * 160):
            if first:
                self.started.set()
                first = False
            time.sleep(0.05)
            yield chunk


def chunks_from_text(text: str):
    from langchain_core.messages import AIMessageChunk
    for i in range(0, len(text), 4):
        yield AIMessageChunk(content=text[i:i + 4])


def test_stop_hits_an_in_flight_run(tmp_path: Path, monkeypatch) -> None:
    """stop 命中在途 run = 200 {ok:true}，终帧仍是 done{stopped:true}。

    brief 原码「读到 start 帧就发停止」在这台 starlette 上拿不到在途窗口（实测整段
    缓冲到流结束才交出来，届时 finally 已 finish，stop 只会 404）：流改后台线程消费，
    run_id 经 new_run_id 固定成已知值，主线程在首块信号后打 stop。断言值一字未改。
    """
    known_run_id = "a" * 32
    # interaction/__init__ 里 `router` 属性被 APIRouter 实例遮蔽，模块对象只能经 import_module 拿
    chat_router = import_module("aitester.interaction.router")
    monkeypatch.setattr(chat_router, "new_run_id", lambda: known_run_id)
    provider = _Slow()
    application = _app(tmp_path, provider=provider)
    pid = _pid(application, tmp_path)
    collected: dict[str, object] = {}

    def run_stream() -> None:
        try:
            with TestClient(application).stream(
                "POST", "/api/chat/send/stream",
                json={"message": "慢一点", "agent_id": "case_design", "project_id": pid}
            ) as resp:
                assert resp.status_code == 200
                collected["frames"] = sse_frames(resp)
        except BaseException as exc:          # 后台线程的失败要能在主线程响亮复现
            collected["error"] = exc

    thread = threading.Thread(target=run_stream, daemon=True)
    thread.start()
    assert provider.started.wait(5.0), "慢 provider 未开流"
    client = TestClient(application)
    r = client.post("/api/chat/stop", json={"run_id": known_run_id})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    thread.join(30)
    assert "error" not in collected, collected.get("error")
    assert "frames" in collected, "SSE 流未走完，后台线程没有交出帧"
    frames = collected["frames"]
    assert frames[0][0] == "start" and frames[0][1]["run_id"] == known_run_id
    assert frames[-1][0] == "done"
    assert frames[-1][1]["stopped"] is True   # 停止走 done{stopped:true}，不是第三条终态


def test_stop_unknown_run_returns_404(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    r = client.post("/api/chat/stop", json={"run_id": "0" * 32})
    assert r.status_code == 404
    assert r.json()["detail"] == "这条回答已经结束"


def test_one_shot_send_endpoint_is_gone(tmp_path: Path) -> None:
    """裁定 1 的接口锁：一次性口整体不存在（Step 1 实测未注册路径回 404 而非 405）。"""
    client = TestClient(_app(tmp_path))
    assert client.post("/api/chat/send", json={"message": "hi"}).status_code == 404


def test_registry_releases_the_run_when_the_stream_completes(tmp_path: Path) -> None:
    """Task 5 留给本片的欠条：流收尾后注册表必须已释放该 run——再 stop 同一条回 404。

    finish 挂在 frames() 的 finally 上；这条锁用真 HTTP 打到真注册表，验的不是替身记账。
    """
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    events = _stream(client, {"message": "收尾即释放", "agent_id": "case_design",
                              "project_id": pid})
    assert events[-1][0] == "done"
    r = client.post("/api/chat/stop", json={"run_id": events[0][1]["run_id"]})
    assert r.status_code == 404
    assert r.json()["detail"] == "这条回答已经结束"
