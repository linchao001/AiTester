from typing import Any

from pydantic import BaseModel, Field


class EchoRequest(BaseModel):
    session_id: str = "default"
    message: str = Field(min_length=1)


class EchoResponse(BaseModel):
    reply: str
    trace: list[str]


class SendRequest(BaseModel):
    session_id: str = "default"
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


class SendResponse(BaseModel):
    reply: str
    trace: list[str]
    model: str
    drafts: list[KbDraft] = []


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
