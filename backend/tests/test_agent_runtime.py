"""装配器单测：实例内容来自数据（提示词/模型/工具），实例本身不持有状态。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aitester.agents import find_agent
from aitester.adapters.llm import MockProvider, ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.adapters.llm.metered import MeteredProvider
from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools import FileObservationStore
from aitester.config import Settings
from aitester.case_design.graph import build_case_design_graph
from aitester.services.agent_runtime import AgentInstance, AgentRuntime
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.storage import FileJsonConfigRepository


def _runtime(
    tmp_path: Path, **settings_kwargs: object
) -> tuple[AgentRuntime, CapabilityConfigService, ModelConfigService]:
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    return AgentRuntime(capability, model_config), capability, model_config


class _FakeChat:
    """记录构造参数，避免真建 OpenAI 客户端。"""

    last: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last = dict(kwargs)


@pytest.fixture(autouse=True)
def _no_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_compat, "ChatOpenAI", _FakeChat)
    _FakeChat.last = {}


def test_unknown_agent_raises_config_not_found(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    with pytest.raises(ConfigNotFoundError) as exc_info:
        runtime.build("ghost", "s1", provider_override=MockProvider())
    assert exc_info.value.detail == "未知智能体「ghost」"


def test_legacy_id_is_not_resolved_by_runtime(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        runtime.build("a1", "s1", provider_override=MockProvider())


def test_prompt_and_builder_come_from_spec(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    instance = runtime.build("case_design", "s1", provider_override=MockProvider())
    spec = find_agent("case_design")
    assert isinstance(instance, AgentInstance)
    assert instance.agent_id == "case_design"
    assert instance.system_prompt == spec.prompt
    assert instance.build_graph is build_case_design_graph   # spec.graph_builder="case_design_loop"


def test_agent_default_model_wins_over_global(tmp_path: Path) -> None:
    runtime, capability, _ = _runtime(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("case_design", "dashscope/qwen3.7-max")
    instance = runtime.build("case_design", "s1")
    assert instance.provider.model_ref == "dashscope/qwen3.7-max"
    assert _FakeChat.last["model"] == "qwen3.7-max"


def test_falls_back_to_global_default_when_agent_default_unusable(tmp_path: Path) -> None:
    runtime, capability, model_config = _runtime(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("case_design", "dashscope/qwen3.7-max")
    model_config.set_model_enabled("dashscope", "qwen3.7-max", False)
    instance = runtime.build("case_design", "s1")
    assert instance.provider.model_ref == "deepseek/deepseek-flash"


def test_nothing_configured_raises_actionable_400(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    with pytest.raises(ProviderConfigError) as exc_info:
        runtime.build("case_design", "s1")
    assert "设置 · 模型设置" in exc_info.value.detail


def test_provider_override_skips_model_resolution(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)  # 全局也没配 Key
    mock = MockProvider()
    instance = runtime.build("case_design", "s1", provider_override=mock)
    # C5：注入的替身也被计量缝包上（否则离线端到端永远量不到），但包的就是那一个替身——
    # 短路语义没变：仍然不解析模型，窗口因此是「未知」的 0
    assert isinstance(instance.provider, MeteredProvider)
    assert instance.provider._inner is mock
    assert instance.provider.usage is instance.usage
    assert instance.usage.window == 0
    assert _FakeChat.last == {}


def test_tools_are_carried_exactly_as_enabled_state(tmp_path: Path) -> None:
    runtime, capability, _ = _runtime(tmp_path)
    capability.set_agent_tools("case_design", ["read", "web_search"])
    instance = runtime.build("case_design", "s1", provider_override=MockProvider())
    assert [t.tool_id() for t in instance.tools] == ["read", "web_search"]


def test_empty_carrier_list_builds_no_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, capability, _ = _runtime(tmp_path)
    capability.set_agent_tools("case_design", [])

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("无携带工具时不应构建工具注册表")

    monkeypatch.setattr("aitester.services.agent_runtime.build_default_registry", boom)
    instance = runtime.build("case_design", "s1", provider_override=MockProvider())
    assert instance.tools == []


def test_registry_uses_given_cwd_and_shared_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _Registry:
        def get_many(self, tool_ids: list[str]) -> list[Any]:
            return []

    def fake_registry(
        cwd: str = ".",
        session_id: str = "default",
        observed: Any = None,
        kb: Any = None,
        agent_id: str = "console",
        task: Any = None,
        usage: Any = None,                       # C5 新形参：替身的形状要跟住真实签名，否则透传一断链就没人说
    ) -> Any:
        captured.update(
            {"cwd": cwd, "session_id": session_id, "observed": observed, "kb": kb, "agent_id": agent_id}
        )
        return _Registry()

    monkeypatch.setattr("aitester.services.agent_runtime.build_default_registry", fake_registry)
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"), Settings(_env_file=None)
    )
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    store = FileObservationStore()
    kb = object()  # 替身 manager：只验证透传形状，不调用
    runtime = AgentRuntime(capability, model_config, store, kb=kb)
    runtime.build("case_design", "s9", provider_override=MockProvider())
    assert captured["cwd"] == "."  # 默认值：echo 链路与既有调用方不破
    assert captured["session_id"] == "case_design:s9"  # 守卫键与 memory/storage 一样按智能体 scoped
    assert captured["observed"] is store
    assert captured["kb"] is kb
    assert captured["agent_id"] == "case_design"


def test_registry_uses_project_cwd_when_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _Registry:
        def get_many(self, tool_ids: list[str]) -> list[Any]:
            return []

    def fake_registry(
        cwd: str = ".",
        session_id: str = "default",
        observed: Any = None,
        kb: Any = None,
        agent_id: str = "console",
        task: Any = None,
        usage: Any = None,                       # C5 新形参：替身的形状要跟住真实签名，否则透传一断链就没人说
    ) -> Any:
        captured.update(
            {"cwd": cwd, "session_id": session_id, "observed": observed, "kb": kb, "agent_id": agent_id}
        )
        return _Registry()

    monkeypatch.setattr("aitester.services.agent_runtime.build_default_registry", fake_registry)
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"), Settings(_env_file=None)
    )
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    store = FileObservationStore()
    runtime = AgentRuntime(capability, model_config, store)
    root = tmp_path / "reqs"
    root.mkdir()
    runtime.build("case_design", "s9", provider_override=MockProvider(), cwd=str(root))
    assert captured["cwd"] == str(root)  # 第 2 片刻意把落点交给项目目录：产出物不再落进仓库

    # 上面只钉到「注册表收到 cwd」，还得钉「每个工具实例都拿到」：build_default_registry 把同一个
    # cwd 同时交给文件、搜索与命令工具（pwsh/bash 也在其中），所以这里泛化认注册表里带 cwd 的全部
    # 实例，不列文件工具名单——只数文件工具就永远没人钉命令工具的落点
    real = build_default_registry(cwd=str(root), session_id="case_design:s9")
    carried = {t.tool_id(): str(t.cwd) for t in real.as_langchain_tools() if hasattr(t, "cwd")}
    assert {"read", "write", "edit", "grep_search", "glob_search", "pwsh", "bash"} <= set(carried)
    assert set(carried.values()) == {str(root)}  # web_search 不带 cwd，泛化断言只认有落点的那批


def test_instances_are_independent_objects(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    mock = MockProvider()  # 同一个注入对象传两次，才能验证「实例各自新建」而非「provider 相同」
    a = runtime.build("case_design", "s1", provider_override=mock)
    b = runtime.build("case_design", "s1", provider_override=mock)
    assert a is not b
    # C5 之后一回合一个计量缝：共用同一个包装对象就是共用同一本账，两回合的读数必须互不串味
    assert a.provider is not b.provider
    assert a.provider._inner is b.provider._inner is mock   # 注入的仍是同一个 mock
    assert a.usage is not b.usage and a.provider.usage is a.usage
    assert a.tools and len(a.tools) == len(b.tools)
    assert [t.tool_id() for t in a.tools] == [t.tool_id() for t in b.tools]
    assert all(x is not y for x, y in zip(a.tools, b.tools))  # 工具对象也每次新建，不跨请求复用


def test_kb_assistant_forced_binding_ignores_capability(tmp_path: Path) -> None:
    # 公共件均已在模块顶部导入，直接复用（简报「该文件已有的构造 helper 复用」）
    s = Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases"))
    model = ModelConfigService(FileJsonConfigRepository(tmp_path / "m.json"), s)
    cap = CapabilityConfigService(FileJsonConfigRepository(tmp_path / "c.json"), model)

    class _StubKb:
        is_enabled = True
        kb_root_dir = tmp_path / "bases" / "zhb_kb"
        def workspace_dir(self, project_id, agent_id):
            return tmp_path / "workspaces" / project_id / agent_id
        def run_job_sync(self, name, *, project_id="default", agent_id="console", **kw):
            # 终审项 4 后装配期会预热 status job：RecordingKb 风格记录即可，不触真 reme
            return SimpleNamespace(success=True, answer="ok", metadata={})

    runtime = AgentRuntime(cap, model, FileObservationStore(), kb=_StubKb())
    inst = runtime.build("kb_assistant", "kb-console", provider_override=MockProvider())
    assert [t.name for t in inst.tools] == [
        "read", "grep_search", "glob_search", "knowledge_search", "prepare_kb_write"]
    read_tool = inst.tools[0]
    assert str(read_tool.cwd) == str(tmp_path / "workspaces" / "default" / "kb_assistant")
    # 不受能力勾选管辖：case_design 工具集清空也不影响 kb_assistant
    cap.set_agent_tools("case_design", [])
    inst2 = runtime.build("kb_assistant", "kb-console", provider_override=MockProvider())
    assert len(inst2.tools) == 5


def test_kb_assistant_warms_kb_instance_on_build(tmp_path: Path) -> None:
    # 终审项 4：knowledge junction 由 reme 实例首启创建（manager._start_app →
    # reme/knowledge/mount.py），装配期必须 best-effort 派发一次 status 预热，
    # 否则冷 workspace 首轮 read/grep/glob 只见空目录
    s = Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases"))
    model = ModelConfigService(FileJsonConfigRepository(tmp_path / "m.json"), s)
    cap = CapabilityConfigService(FileJsonConfigRepository(tmp_path / "c.json"), model)

    class _RecordingKb:
        is_enabled = True
        kb_root_dir = tmp_path / "bases" / "zhb_kb"

        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def workspace_dir(self, project_id, agent_id):
            return tmp_path / "workspaces" / project_id / agent_id

        def run_job_sync(self, name, *, project_id="default", agent_id="console", **kw):
            self.calls.append((name, project_id, agent_id))
            return SimpleNamespace(success=True, answer="ok", metadata={})

    kb = _RecordingKb()
    runtime = AgentRuntime(cap, model, FileObservationStore(), kb=kb)
    inst = runtime.build("kb_assistant", "kb-console", provider_override=MockProvider())
    assert kb.calls == [("status", "default", "kb_assistant")]  # 调用形状对齐 manager 签名
    assert len(inst.tools) == 5


def test_kb_assistant_build_survives_warm_failure(tmp_path: Path) -> None:
    # 终审项 4 守卫：预热失败（reme 坏/超时）只记日志，绝不炸 build()
    s = Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases"))
    model = ModelConfigService(FileJsonConfigRepository(tmp_path / "m.json"), s)
    cap = CapabilityConfigService(FileJsonConfigRepository(tmp_path / "c.json"), model)

    class _FailingKb:
        is_enabled = True
        kb_root_dir = tmp_path / "bases" / "zhb_kb"

        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def workspace_dir(self, project_id, agent_id):
            return tmp_path / "workspaces" / project_id / agent_id

        def run_job_sync(self, name, *, project_id="default", agent_id="console", **kw):
            self.calls.append((name, project_id, agent_id))
            raise RuntimeError("reme 实例起不来")

    kb = _FailingKb()
    runtime = AgentRuntime(cap, model, FileObservationStore(), kb=kb)
    inst = runtime.build("kb_assistant", "s", provider_override=MockProvider())
    assert kb.calls == [("status", "default", "kb_assistant")]
    assert [t.name for t in inst.tools] == [
        "read", "grep_search", "glob_search", "knowledge_search", "prepare_kb_write"]


def test_kb_assistant_unregistered_kb_degrades(tmp_path: Path) -> None:
    # 与上一用例同构（Settings/Model/Capability 三段式），仅 kb=None —— 工具面退化为三件套
    s = Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases"))
    model = ModelConfigService(FileJsonConfigRepository(tmp_path / "m.json"), s)
    cap = CapabilityConfigService(FileJsonConfigRepository(tmp_path / "c.json"), model)

    runtime = AgentRuntime(cap, model, FileObservationStore(), kb=None)
    inst = runtime.build("kb_assistant", "s", provider_override=MockProvider())
    assert [t.name for t in inst.tools] == ["read", "grep_search", "glob_search"]
