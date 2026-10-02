from typing import Any

from pydantic import BaseModel, Field


class EchoRequest(BaseModel):
    session_id: str = "default"
    message: str = Field(min_length=1)


class EchoResponse(BaseModel):
    reply: str
    trace: list[str]


class SendRequest(BaseModel):
    # 空串 = 服务端新建会话；sess_* 续写；其余形态按临时键处理（chat.py 三态判据）
    session_id: str = ""
    message: str = Field(min_length=1)
    agent_id: str = "case_design"


class KbDraft(BaseModel):
    op: str
    path: str
    abs_display: str
    summary: str = ""
    content: str
    base: str | None = None
    mtime: int = 0


class StepInfo(BaseModel):
    tool: str
    ok: bool
    round: int
    detail: str


class SendResponse(BaseModel):
    reply: str
    trace: list[str]
    model: str
    drafts: list[KbDraft] = []
    # 默认值不可省：既有测试用 SimpleNamespace 替身返回缺键 dict，靠默认兜住旧形态
    session_id: str = ""
    title: str = ""
    steps: list[StepInfo] = []


class ModelInfo(BaseModel):
    id: str
    enabled: bool
    max_output: int
    context: int
    name: str | None = None
    caps: list[str] = []
    note: str | None = None
    recommended: bool = False


class ProviderUrlOption(BaseModel):
    label: str
    value: str


class ProviderInfo(BaseModel):
    id: str
    name: str
    base_url: str
    has_key: bool
    key_masked: str
    models: list[ModelInfo]
    proto: str | None = None
    key_prefix: str | None = None
    freeze_url: bool = False
    base_options: list[ProviderUrlOption] = []


class ModelsResponse(BaseModel):
    default_uid: str
    providers: list[ProviderInfo]


class KeyUpdate(BaseModel):
    api_key: str | None = None


class EnabledUpdate(BaseModel):
    enabled: bool


class ProviderTestRequest(BaseModel):
    api_key: str | None = None


class ProviderTestResponse(BaseModel):
    ok: bool
    latency_ms: int | None = None
    reason: str | None = None


class DefaultUpdate(BaseModel):
    uid: str


class ToolInfo(BaseModel):
    id: str
    group: str
    icon: str
    label: str
    os: str
    desc: str
    enabled: bool
    available: bool
    unavailable_reason: str | None
    carried_by: list[str]


class AgentInfo(BaseModel):
    id: str
    icon: str
    name: str
    desc: str
    prompt: str
    default_uid: str
    effective_uid: str
    tool_ids: list[str]


class CapabilityResponse(BaseModel):
    tools: list[ToolInfo]
    agents: list[AgentInfo]


class AgentDefaultUpdate(BaseModel):
    uid: str


class AgentToolsUpdate(BaseModel):
    tool_ids: list[str] = []


class ToolEnabledUpdate(BaseModel):
    enabled: bool


class KbSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=100)
    bucket: str = "all"


class KbSaveRequest(BaseModel):
    title: str
    content: str
    bucket: str = "business/wiki"


class KbInboxStemRequest(BaseModel):
    stem: str


class KbInboxMergeRequest(BaseModel):
    stem: str
    target_path: str = ""
    mode: str = "REFINE"


class KbResponse(BaseModel):
    success: bool
    answer: Any = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectInfo(BaseModel):
    """项目条目：kb 只回别名，真实知识库 id 与实体路径不外泄。"""

    id: str
    name: str
    desc: str
    dir: str
    agents: list[str]
    kb: str
    session_count: int = 0


class ProjectsResponse(BaseModel):
    projects: list[ProjectInfo]


class ProjectCreateRequest(BaseModel):
    name: str
    desc: str = ""
    dir: str
    agents: list[str]
    kb: str | None = None


class ProjectUpdateRequest(BaseModel):
    name: str
    desc: str = ""
    agents: list[str]
    # 不可改字段：不传即不改，传了必须与原值相同（判等在服务层）
    dir: str | None = None
    kb: str | None = None


class SessionInfo(BaseModel):
    id: str
    agent_id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int


class SessionsResponse(BaseModel):
    sessions: list[SessionInfo]


class ChatMessageInfo(BaseModel):
    role: str
    content: str
    ts: int
    steps: list[StepInfo] | None = None


class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[ChatMessageInfo]
