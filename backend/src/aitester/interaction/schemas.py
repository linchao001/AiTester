from typing import Any, Literal

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
    # 必填判据在 service 层（含 kb_assistant：必须先选项目）
    project_id: str = ""
    # 三档权限（第 5 片）：缺省 free = 零行为变化；合法性由 auth_rules.validate_perm_mode 判，
    # 这里不做 Literal——/kb 与旧客户端不发这字段，而中文 detail 要过路由的 _GUARD_MAP
    perm_mode: str = "free"


class StreamStopRequest(BaseModel):
    # start 事件里下发的 uuid4().hex；已结束或不存在的 run 一律 404
    run_id: str


class StreamStopResponse(BaseModel):
    ok: bool


class KbDraft(BaseModel):
    """助手写入草案：确认后走 Reme save_to_knowledge（title/content/bucket）。

    path/abs_display 仅供卡片展示预期落点；base/mtime 保留兼容，Reme 确认路径不用。
    """

    title: str
    content: str
    bucket: str
    summary: str = ""
    op: str = "create"
    path: str = ""
    abs_display: str = ""
    base: str | None = None
    mtime: int = 0


class StepInfo(BaseModel):
    tool: str
    ok: bool
    round: int
    detail: str


class ApproveRequest(BaseModel):
    """只登记决策；续跑是另一条请求，两步分开才好收敛重复提交与双弹层。"""

    run_id: str
    call_id: str
    decision: Literal["approve", "reject"]
    remember: bool = False


class ResumeRequest(BaseModel):
    # 与 StreamStopRequest 同键：续跑、停止、批准三者都以 run_id 定位同一条待批
    run_id: str


class SubagentRef(BaseModel):
    """wait 载荷里的子智能体来源标注（R3）：父层为 None，子层给卡片出处的快照。"""

    call_id: str
    name: str
    title: str = ""


class PendingCallInfo(BaseModel):
    """一张授权卡：与后端 wait 事件七键逐字对齐（六键 + subagent 来源标注，R3/R11）。"""

    call_id: str
    tool: str
    action: str
    target: str
    command: str
    cwd: str
    subagent: SubagentRef | None


class PendingDecidedCall(PendingCallInfo):
    """已答项：六键照给，多一个 decision——刷新后痕迹卡要能还原「批准/拒绝的是哪一次」。"""

    decision: str


class PendingRunInfo(BaseModel):
    run_id: str
    session_id: str         # 待批停止后这条会话才第一次落盘：前端按它判断要不要重拉正文
    perm_mode: str          # 锁档：卡片按挂起时那一档展示，不受用户事后切档影响
    prefix: str             # 已投递正文前缀：刷新后气泡照显示（裁定 8）
    steps: list[StepInfo]
    waiting: list[PendingCallInfo]
    decided: list[PendingDecidedCall]
    created_at: float


class PendingResponse(BaseModel):
    runs: list[PendingRunInfo]


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
    subagents: list[AgentInfo] = []


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
    # 无默认值：可达与否必须每次现探测，给个「可达」默认就等于把没探过的项目伪装成能写
    dir_exists: bool


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


class PickDirRequest(BaseModel):
    """`path` 非空且是本机真实目录时，作为选择器的初始位置；无效值静默回落。"""

    path: str = ""


class PickDirResponse(BaseModel):
    """`path` 为空串 = 用户取消了弹窗，不是错误。"""

    path: str


class SessionInfo(BaseModel):
    id: str
    agent_id: str
    project_id: str
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
    stopped: bool = False      # 读侧字段只增不删：GET /{sid}/messages 向后兼容
    # 本回合的上下文读数快照（老行/老口径为 None）：同「只增不删」口径
    context: dict | None = None


class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[ChatMessageInfo]
