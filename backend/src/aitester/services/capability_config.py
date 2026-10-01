"""运行期能力配置：智能体默认模型与工具启用状态，JSON 落盘为唯一真相。

工具目录与实现保持一致：不可用的工具（本机缺 shell 等）一律禁用且不可携带，
种子启用态由 `availability` 探测本机推导，不写死平台结论。
"""
import copy
import sys
from typing import Any

from aitester.adapters.tools.availability import unavailable_reason
from aitester.agents import AGENT_CATALOG, DEFAULT_AGENT_STATE, LEGACY_AGENT_IDS
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
        "id": "grep_search",
        "group": "文件处理工具",
        "icon": "🔍",
        "label": "grep_search",
        "os": "全平台",
        "desc": "按模式递归检索文件内容，输出「文件:行号: 命中行」；支持正则、大小写、上下文行与文件名过滤。",
    },
    {
        "id": "glob_search",
        "group": "文件处理工具",
        "icon": "📁",
        "label": "glob_search",
        "os": "全平台",
        "desc": "按通配符查找文件与目录（如 **/*.json），返回相对路径列表。",
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
        "os": "macOS / Linux",
        "desc": "在 macOS / Linux 上执行 Bash，用途与 pwsh 相同；Windows 上依赖 Git Bash 等 POSIX 环境。",
    },
    {
        "id": "web_search",
        "group": "网页搜索工具",
        "icon": "🔎",
        "label": "web_search",
        "os": "全平台",
        "desc": "按关键词搜索公网，返回标题、链接与摘要，用于查官方文档、错误码与版本变更说明。",
    },
    {
        "id": "knowledge_search",
        "group": "知识库工具",
        "icon": "🔍",
        "label": "knowledge_search",
        "os": "全平台",
        "desc": "检索全局共享知识库（发布桶范围），返回命中节点与出处；回答业务问题前先查证。",
    },
    {
        "id": "save_to_knowledge",
        "group": "知识库工具",
        "icon": "📥",
        "label": "save_to_knowledge",
        "os": "全平台",
        "desc": "把已确认的知识节点写入共享知识库发布桶，全项目共享可见；写入前必须经用户确认。",
    },
]


def default_tool_enabled(tool_id: str) -> bool:
    """工具的出厂开关：本机不可用的一律 False。

    bash 在 Windows 上即使探测到 Git Bash 也默认关（跨平台脚本口径不统一），留待用户显式开启。
    """
    if unavailable_reason(tool_id) is not None:
        return False
    return not (tool_id == "bash" and sys.platform == "win32")


DEFAULT_TOOL_STATE: dict[str, bool] = {t["id"]: default_tool_enabled(t["id"]) for t in TOOL_CATALOG}


class CapabilityConfigError(RuntimeError):
    """配置类校验失败，交互层映射 400，detail 面向用户且可照做。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _default_config() -> dict[str, Any]:
    return {
        "version": 1,
        # 每次现算而不复用模块常量：探测结果与运行时一致，测试也能注入假探测
        "tool_state": {t["id"]: default_tool_enabled(t["id"]) for t in TOOL_CATALOG},
        "agents": copy.deepcopy(DEFAULT_AGENT_STATE),
    }


def _normalized(config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """按目录重建配置形状，返回 (归一结果, 是否与磁盘现状不同)。"""
    seed = _default_config()
    raw_tools = config.get("tool_state")
    raw_tools = raw_tools if isinstance(raw_tools, dict) else {}
    tool_state: dict[str, bool] = {}
    for t in TOOL_CATALOG:
        tool_id = t["id"]
        enabled = bool(raw_tools[tool_id]) if tool_id in raw_tools else seed["tool_state"][tool_id]
        # 不可用的工具（本机缺 shell 等）一律落回禁用：配置不宣称跑不了的能力
        tool_state[tool_id] = enabled and unavailable_reason(tool_id) is None

    raw_agents = config.get("agents")
    raw_agents = raw_agents if isinstance(raw_agents, dict) else {}
    # 一次性迁移：旧 id 的状态搬到新 id（保住用户改过的默认模型与携带工具），旧键随后被丢弃
    for legacy_id, new_id in LEGACY_AGENT_IDS.items():
        if legacy_id in raw_agents and new_id not in raw_agents:
            raw_agents[new_id] = raw_agents[legacy_id]
    agents: dict[str, dict[str, Any]] = {}
    # 注意：seed["agents"] 是 DEFAULT_AGENT_STATE 形态——aid -> {"default_uid","tool_ids"}，条目内没有 "id" 键
    for agent_id, seed_state in seed["agents"].items():
        raw = raw_agents.get(agent_id)
        raw = raw if isinstance(raw, dict) else {}
        raw_ids = raw.get("tool_ids", seed_state["tool_ids"])
        raw_ids = raw_ids if isinstance(raw_ids, list) else []
        unique: list[str] = []
        for tool_id in raw_ids:
            # 只有启用（因而必然可用）的工具才允许留在携带列表里，旧配置由此自愈
            if tool_state.get(tool_id) and tool_id not in unique:
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

    def agent_state(self, agent_id: str) -> dict[str, Any]:
        """公开读入口：返回副本，调用方改不坏配置真相。"""
        state = self._agent_state(agent_id)
        return {"default_uid": state["default_uid"], "tool_ids": list(state["tool_ids"])}

    def effective_uid(self, agent_id: str) -> str:
        """该智能体的有效模型：自身默认「可用」则用之，否则回落全局默认（读侧回落，不写回）。"""
        uid = self._agent_state(agent_id)["default_uid"]
        if uid and self._model_config.is_usable_uid(uid):
            return uid
        return self._model_config.default_uid

    def _enabled(self, tool_id: str) -> bool:
        return bool(self._config["tool_state"][tool_id])

    def get_view(self) -> dict[str, Any]:
        agents_state = self._config["agents"]
        tools = []
        for t in TOOL_CATALOG:
            reason = unavailable_reason(t["id"])
            tools.append(
                {
                    "id": t["id"],
                    "group": t["group"],
                    "icon": t["icon"],
                    "label": t["label"],
                    "os": t["os"],
                    "desc": t["desc"],
                    "enabled": self._enabled(t["id"]),
                    "available": reason is None,
                    "unavailable_reason": reason,
                    "carried_by": [
                        aid
                        for aid, state in agents_state.items()
                        if t["id"] in state["tool_ids"]
                    ],
                }
            )
        views: list[dict[str, Any]] = []
        for spec in AGENT_CATALOG:
            state = agents_state[spec.id]
            views.append(
                {
                    "id": spec.id,
                    "icon": spec.icon,
                    "name": spec.name,
                    "desc": spec.desc,
                    "prompt": spec.prompt,
                    "default_uid": state["default_uid"],
                    "effective_uid": self.effective_uid(spec.id),
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
        unavailable: list[str] = []
        for tool_id in tool_ids:
            tool = self._tool(tool_id)
            if tool_id in unique:
                continue
            unique.append(tool_id)
            reason = unavailable_reason(tool_id)
            if reason is not None:
                unavailable.append(f"{tool['label']}（{reason}）")
        if unavailable:
            raise CapabilityConfigError(f"工具「{'、'.join(unavailable)}」当前不可用，不能携带")
        disabled = [t for t in unique if not self._enabled(t)]
        if disabled:
            raise CapabilityConfigError(
                f"工具「{'、'.join(disabled)}」已禁用：请先在 设置 · 工具 中启用后再携带"
            )
        state["tool_ids"] = unique
        self._repo.save(self._config)

    def set_tool_enabled(self, tool_id: str, enabled: bool) -> None:
        tool = self._tool(tool_id)
        reason = unavailable_reason(tool_id)
        if enabled and reason is not None:
            raise CapabilityConfigError(f"工具「{tool['label']}」当前不可用（{reason}），无法启用")
        self._config["tool_state"][tool_id] = enabled
        if not enabled:
            for state in self._config["agents"].values():
                if tool_id in state["tool_ids"]:
                    state["tool_ids"] = [t for t in state["tool_ids"] if t != tool_id]
        self._repo.save(self._config)
