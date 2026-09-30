"""运行期能力配置：智能体默认模型与工具启用状态，JSON 落盘为唯一真相。"""
import copy
from typing import Any

from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.storage import JsonConfigRepository

TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "read",
        "group": "文件处理工具",
        "icon": "📖",
        "label": "read",
        "os": "全平台",
        "desc": "读取项目与知识库中的文本文件，返回带行号的原文；改动前用它确认现状。",
    },
    {
        "id": "write",
        "group": "文件处理工具",
        "icon": "✍️",
        "label": "write",
        "os": "全平台",
        "desc": "整文件写入：新建文件，或用新内容整体覆盖旧文件。写知识库真实文件前必须先出草案确认。",
    },
    {
        "id": "edit",
        "group": "文件处理工具",
        "icon": "✏️",
        "label": "edit",
        "os": "全平台",
        "desc": "按精确字符串定位替换，只做小范围改动；目标不唯一或未命中即失败，避免误伤无关代码。",
    },
    {
        "id": "pwsh",
        "group": "命令执行工具",
        "icon": "🖥",
        "label": "pwsh",
        "os": "Windows",
        "desc": "执行 PowerShell：跑 pytest / npm test、装依赖、调用 CLI，回收 stdout、stderr 与退出码。",
    },
    {
        "id": "bash",
        "group": "命令执行工具",
        "icon": "🐚",
        "label": "bash",
        "os": "macOS",
        "desc": "在 macOS / Linux 上执行 Bash，用途与 pwsh 相同。当前运行在 Windows，默认禁用。",
    },
    {
        "id": "web_search",
        "group": "网页搜索工具",
        "icon": "🔎",
        "label": "web_search",
        "os": "全平台",
        "desc": "按关键词搜索公网，返回标题、链接与摘要，用于查官方文档、错误码与版本变更说明。",
    },
]

AGENT_CATALOG: list[dict[str, Any]] = [
    {
        "id": "a1",
        "icon": "📋",
        "name": "用例设计智能体",
        "desc": "读需求与接口文档，产出可直接执行的测试用例并同步用例平台，覆盖等价类、边界值与异常路径。",
        "prompt": """你是「用例设计智能体」，服务对象是软件测试工程师。

## 职责
- 依据需求说明、接口文档与存量用例，设计功能 / 接口 / 回归测试用例
- 用等价类划分、边界值、状态迁移、异常注入保证覆盖，并标注 P0 / P1 / P2
- 产出统一用例表：编号、需求号、前置条件、步骤、预期结果、优先级

## 约束
- 项目文件只读，新增文件一律落在 cases/ 下，不改动生产配置
- 需求信息不足时先列出待澄清问题，不臆造验收标准
- 每条用例必须能被测试执行智能体直接跑：步骤可操作、预期结果可判定
- 全程使用中文与 Markdown 表格，不省略步骤""",
    },
]

DEFAULT_TOOL_STATE: dict[str, bool] = {t["id"]: t["id"] != "bash" for t in TOOL_CATALOG}
DEFAULT_AGENT_STATE: dict[str, dict[str, Any]] = {
    "a1": {"default_uid": "", "tool_ids": ["read", "write", "edit", "web_search"]}
}


class CapabilityConfigError(RuntimeError):
    """配置类校验失败，交互层映射 400，detail 面向用户且可照做。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _default_config() -> dict[str, Any]:
    return {
        "version": 1,
        "tool_state": dict(DEFAULT_TOOL_STATE),
        "agents": copy.deepcopy(DEFAULT_AGENT_STATE),
    }


def _normalized(config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """按目录重建配置形状，返回 (归一结果, 是否与磁盘现状不同)。"""
    seed = _default_config()
    raw_tools = config.get("tool_state")
    raw_tools = raw_tools if isinstance(raw_tools, dict) else {}
    tool_state = {
        t["id"]: bool(raw_tools[t["id"]]) if t["id"] in raw_tools else seed["tool_state"][t["id"]]
        for t in TOOL_CATALOG
    }

    raw_agents = config.get("agents")
    raw_agents = raw_agents if isinstance(raw_agents, dict) else {}
    agents: dict[str, dict[str, Any]] = {}
    # 注意：seed["agents"] 是 DEFAULT_AGENT_STATE 形态——aid -> {"default_uid","tool_ids"}，条目内没有 "id" 键
    for agent_id, seed_state in seed["agents"].items():
        raw = raw_agents.get(agent_id)
        raw = raw if isinstance(raw, dict) else {}
        raw_ids = raw.get("tool_ids", seed_state["tool_ids"])
        raw_ids = raw_ids if isinstance(raw_ids, list) else []
        unique: list[str] = []
        for tool_id in raw_ids:
            if tool_id in tool_state and tool_id not in unique:
                unique.append(tool_id)
        agents[agent_id] = {
            "default_uid": str(raw.get("default_uid", seed_state["default_uid"])),
            "tool_ids": unique,
        }

    normalized = {"version": seed["version"], "tool_state": tool_state, "agents": agents}
    return normalized, normalized != config


class CapabilityConfigService:
    """能力配置真相：构造时 load（缺则种子并落盘），每次变更立即 save。"""

    def __init__(self, repo: JsonConfigRepository, model_config: ModelConfigService) -> None:
        self._repo = repo
        self._model_config = model_config
        config = repo.load()
        if config is None:
            config = _default_config()
            repo.save(config)
        else:
            config, drifted = _normalized(config)
            if drifted:
                repo.save(config)
        self._config = config

    def _tool(self, tool_id: str) -> dict[str, Any]:
        for t in TOOL_CATALOG:
            if t["id"] == tool_id:
                return t
        raise ConfigNotFoundError(f"未知工具「{tool_id}」")

    def _agent_state(self, agent_id: str) -> dict[str, Any]:
        agents = self._config["agents"]
        if agent_id not in agents:
            raise ConfigNotFoundError(f"未知智能体「{agent_id}」")
        return agents[agent_id]

    def _enabled(self, tool_id: str) -> bool:
        return bool(self._config["tool_state"][tool_id])

    def get_view(self) -> dict[str, Any]:
        agents_state = self._config["agents"]
        tools = [
            {
                "id": t["id"],
                "group": t["group"],
                "icon": t["icon"],
                "label": t["label"],
                "os": t["os"],
                "desc": t["desc"],
                "enabled": self._enabled(t["id"]),
                "carried_by": [
                    aid
                    for aid, state in agents_state.items()
                    if t["id"] in state["tool_ids"]
                ],
            }
            for t in TOOL_CATALOG
        ]
        views: list[dict[str, Any]] = []
        for seed in AGENT_CATALOG:
            state = agents_state[seed["id"]]
            default_uid = state["default_uid"]
            if default_uid and self._model_config.is_usable_uid(default_uid):
                effective_uid = default_uid
            else:
                effective_uid = self._model_config.default_uid
            views.append(
                {
                    **seed,
                    "default_uid": default_uid,
                    "effective_uid": effective_uid,
                    "tool_ids": list(state["tool_ids"]),
                }
            )
        return {"tools": tools, "agents": views}

    def set_agent_default_model(self, agent_id: str, uid: str) -> None:
        state = self._agent_state(agent_id)
        value = uid.strip()
        if value and not self._model_config.is_usable_uid(value):
            raise CapabilityConfigError(
                f"模型「{value}」当前不可用（未启用或提供商未配置 API Key）："
                "请在 设置 · 模型设置 处理，或改选「跟随全局默认」"
            )
        state["default_uid"] = value
        self._repo.save(self._config)

    def set_agent_tools(self, agent_id: str, tool_ids: list[str]) -> None:
        state = self._agent_state(agent_id)
        unique: list[str] = []
        for tool_id in tool_ids:
            self._tool(tool_id)
            if tool_id not in unique:
                unique.append(tool_id)
        disabled = [t for t in unique if not self._enabled(t)]
        if disabled:
            raise CapabilityConfigError(
                f"工具「{'、'.join(disabled)}」已禁用：请先在 设置 · 工具 中启用后再携带"
            )
        state["tool_ids"] = unique
        self._repo.save(self._config)

    def set_tool_enabled(self, tool_id: str, enabled: bool) -> None:
        self._tool(tool_id)
        self._config["tool_state"][tool_id] = enabled
        if not enabled:
            for state in self._config["agents"].values():
                if tool_id in state["tool_ids"]:
                    state["tool_ids"] = [t for t in state["tool_ids"] if t != tool_id]
        self._repo.save(self._config)
