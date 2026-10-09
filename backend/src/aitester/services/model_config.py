"""运行期模型配置服务：JSON 落盘为唯一真相，.env 仅作首次种子。"""
import copy
from typing import Any

from aitester.adapters.llm import LlmProvider, OpenAICompatProvider, ProviderConfigError
from aitester.adapters.llm.probe import ProbeError, probe_model
from aitester.config import Settings
from aitester.storage import JsonConfigRepository

CATALOG: list[dict[str, Any]] = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "proto": "openai",
        "key_prefix": "sk-",
        "freeze_url": True,
        "models": [
            {
                "id": "deepseek-flash",
                "enabled": True,
                "max_output": 393216,
                "context": 1048576,
                "name": "DeepSeek-V4.1-Flash",
                "caps": ["FC", "视觉"],
                "note": "官方数据 · 1M 上下文 / 最大输出 384K",
            },
            {
                "id": "deepseek-v4-pro",
                "enabled": True,
                "max_output": 393216,
                "context": 1048576,
                "name": "DeepSeek-V4-Pro-0813",
                "caps": ["FC", "推理"],
                "note": "官方数据 · 1M 上下文 / 最大输出 384K",
            },
        ],
    },
    {
        "id": "dashscope",
        "name": "通义千问 · DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "proto": "openai",
        "key_prefix": "sk",
        "base_options": [
            {"label": "中国（北京）", "value": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
            {"label": "国际（新加坡）", "value": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"},
            {"label": "美国（弗吉尼亚）", "value": "https://dashscope-us.aliyuncs.com/compatible-mode/v1"},
        ],
        "models": [
            {
                "id": "qwen3.8-max",
                "enabled": True,
                "max_output": 8192,
                "context": 131072,
                "name": "Qwen3.8 Max",
                "caps": ["FC", "视觉"],
                "note": "目录数据 · 128K 上下文",
            },
            {
                "id": "qwen3.7-max",
                "enabled": True,
                "max_output": 8192,
                "context": 1000000,
                "name": "Qwen3.7 Max",
                "caps": ["FC"],
                "note": "目录数据 · 1M 上下文",
                "recommended": True,
            },
            {
                "id": "qwen3.7-plus",
                "enabled": True,
                "max_output": 8192,
                "context": 1000000,
                "name": "Qwen3.7 Plus",
                "caps": ["FC", "视觉", "视频"],
                "note": "目录数据 · 图视频输入",
            },
            {
                "id": "qwen3.6-plus",
                "enabled": True,
                "max_output": 8192,
                "context": 1000000,
                "name": "Qwen3.6 Plus",
                "caps": ["FC", "视觉", "视频"],
                "note": "目录数据 · 图视频输入",
            },
        ],
    },
]

# 展示元信息只来自官方目录，落盘记录保持精简（启用状态与数值才是用户运行期真相）。
DISPLAY_KEYS = ("name", "caps", "note", "recommended")
RUNTIME_KEYS = ("id", "enabled", "max_output", "context")
PROVIDER_DISPLAY_KEYS = ("proto", "key_prefix", "freeze_url", "base_options")


def _provider_display(provider_id: str) -> dict[str, Any]:
    for p in CATALOG:
        if p["id"] == provider_id:
            return {k: p[k] for k in PROVIDER_DISPLAY_KEYS if k in p}
    return {}


def _display_entry(provider_id: str, model_id: str) -> dict[str, Any]:
    for p in CATALOG:
        if p["id"] != provider_id:
            continue
        for m in p["models"]:
            if m["id"] == model_id:
                return {k: m[k] for k in DISPLAY_KEYS if k in m}
    return {}


def _catalog_index(provider_id: str, model_id: str) -> int:
    for p in CATALOG:
        if p["id"] == provider_id:
            for i, m in enumerate(p["models"]):
                if m["id"] == model_id:
                    return i
    return -1


def _ordered_models(provider: dict[str, Any]) -> list[dict[str, Any]]:
    """读侧按官方目录顺序呈现；目录外的模型（后续自定义）保持存储原序排在最后。"""
    ordered = [m for m in provider["models"] if _catalog_index(provider["id"], m["id"]) >= 0]
    rest = [m for m in provider["models"] if _catalog_index(provider["id"], m["id"]) < 0]
    return sorted(ordered, key=lambda m: _catalog_index(provider["id"], m["id"])) + rest


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}…{key[-4:]}"


class ConfigNotFoundError(KeyError):
    """未知提供商/模型，交互层映射 404。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _default_config(settings: Settings) -> dict[str, Any]:
    providers = copy.deepcopy(CATALOG)
    keys = {
        "deepseek": settings.deepseek_api_key.strip(),
        "dashscope": settings.dashscope_api_key.strip(),
    }
    for provider in providers:
        provider["api_key"] = keys[provider["id"]]
        for k in PROVIDER_DISPLAY_KEYS:
            provider.pop(k, None)
        provider["models"] = [{k: m[k] for k in RUNTIME_KEYS} for m in provider["models"]]
    default_uid = ""
    for provider in providers:
        if provider["api_key"]:
            default_uid = f"{provider['id']}/{provider['models'][0]['id']}"
            break
    return {"version": 1, "default_uid": default_uid, "providers": providers}


class ModelConfigService:
    """配置真相：构造时 load（缺则种子并落盘），每次变更立即 save。"""

    def __init__(self, repo: JsonConfigRepository, settings: Settings) -> None:
        self._repo = repo
        config = repo.load()
        if config is None:
            config = _default_config(settings)
            repo.save(config)
        self._config = config

    @property
    def default_uid(self) -> str:
        return self._config["default_uid"]

    def get_view(self) -> dict[str, Any]:
        return {
            "default_uid": self._config["default_uid"],
            "providers": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "base_url": p["base_url"],
                    "has_key": bool(p["api_key"]),
                    "key_masked": mask_key(p["api_key"]),
                    "models": [
                        {**dict(m), **_display_entry(p["id"], m["id"])}
                        for m in _ordered_models(p)
                    ],
                    **_provider_display(p["id"]),
                }
                for p in self._config["providers"]
            ],
        }

    def _provider(self, provider_id: str) -> dict[str, Any]:
        for p in self._config["providers"]:
            if p["id"] == provider_id:
                return p
        raise ConfigNotFoundError(f"未知提供商「{provider_id}」")

    def _model(self, provider: dict[str, Any], model_id: str) -> dict[str, Any]:
        for m in provider["models"]:
            if m["id"] == model_id:
                return m
        raise ConfigNotFoundError(f"提供商「{provider['id']}」没有模型「{model_id}」")

    def _uid_usable(self, uid: str) -> bool:
        pid, sep, mid = uid.partition("/")
        if not sep or not mid:
            return False
        for p in self._config["providers"]:
            if p["id"] == pid:
                for m in p["models"]:
                    if m["id"] == mid:
                        return bool(m["enabled"] and p["api_key"])
        return False

    def is_usable_uid(self, uid: str) -> bool:
        """供能力配置读取：该 uid 是否「模型已启用 + 提供商已配 Key」。"""
        return self._uid_usable(uid)

    def _first_available_uid(self) -> str:
        for p in self._config["providers"]:
            if not p["api_key"]:
                continue
            for m in _ordered_models(p):
                if m["enabled"]:
                    return f"{p['id']}/{m['id']}"
        return ""

    def _cascade_default(self) -> None:
        current = self._config["default_uid"]
        if current and not self._uid_usable(current):
            self._config["default_uid"] = self._first_available_uid()

    def update_api_key(self, provider_id: str, api_key: str | None) -> None:
        provider = self._provider(provider_id)
        if api_key is not None:
            provider["api_key"] = api_key.strip()
        self._cascade_default()
        self._repo.save(self._config)

    def set_model_enabled(self, provider_id: str, model_id: str, enabled: bool) -> None:
        provider = self._provider(provider_id)
        model = self._model(provider, model_id)
        model["enabled"] = enabled
        self._cascade_default()
        self._repo.save(self._config)

    def set_default(self, uid: str) -> None:
        pid, sep, mid = uid.partition("/")
        if not sep or not mid:
            raise ProviderConfigError(f"无效的模型标识「{uid}」，应为 提供商/模型 形式")
        provider = self._provider(pid)
        model = self._model(provider, mid)
        if not model["enabled"]:
            raise ProviderConfigError(f"模型「{uid}」已停用，请先在 设置 · 模型设置 中启用")
        if not provider["api_key"]:
            raise ProviderConfigError(
                f"提供商「{provider['name']}」未配置 API Key，请在 设置 · 模型设置 中填写"
            )
        self._config["default_uid"] = uid
        self._repo.save(self._config)

    def build_provider(self, uid: str) -> LlmProvider:
        """按 uid 构建 provider：空 uid / 未配 Key / 已停用 → ProviderConfigError（400 可照做文案）。"""
        if not uid:
            raise ProviderConfigError(
                "尚未配置默认模型：请在 设置 · 模型设置 中填写 API Key 并选择默认 LLM"
            )
        pid, _, mid = uid.partition("/")
        provider = self._provider(pid)
        model = self._model(provider, mid)
        if not provider["api_key"]:
            raise ProviderConfigError(
                f"模型「{uid}」的提供商未配置 API Key：请在 设置 · 模型设置 中填写"
            )
        if not model["enabled"]:
            raise ProviderConfigError(
                f"模型「{uid}」已停用：请在 设置 · 模型设置 中启用或改选模型"
            )
        return OpenAICompatProvider(
            name=pid,
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            model=mid,
            stream_usage=True,
        )

    def window_for(self, uid: str) -> int:
        """该模型的最大上下文（0=未知）。配置坏不抛：读数是尽力而为，坏元数据自己显形。

        只认真整数：`bool` 是 `int` 的子类，`context: true` 会被 `isinstance(v, int)` 收
        成窗口 1；浮点/字符串一律按「元数据脏 ⇒ 未知」处理，不猜测也不截断。
        """
        pid, sep, mid = (uid or "").partition("/")
        if not sep or not mid:
            return 0
        try:
            provider = self._provider(pid)
            model = self._model(provider, mid)
        except Exception:
            return 0
        value = model.get("context")
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    def build_default_provider(self) -> LlmProvider:
        return self.build_provider(self._config["default_uid"])

    def probe_provider(
        self, provider_id: str, api_key_draft: str | None = None
    ) -> dict[str, Any]:
        """连接探测：草稿 Key 优先于已存 Key，取第一个已启用模型发一次极小补全；只读不落盘。"""
        provider = self._provider(provider_id)
        api_key = (api_key_draft or "").strip() or provider["api_key"]
        if not api_key:
            return {"ok": False, "reason": "未配置 API Key，请先填写后再测试连接"}
        enabled = [m for m in _ordered_models(provider) if m["enabled"]]
        if not enabled:
            return {"ok": False, "reason": "该提供商没有已启用模型，请先启用一个模型再测试"}
        try:
            latency_ms = probe_model(
                model=enabled[0]["id"], api_key=api_key, base_url=provider["base_url"]
            )
        except ProbeError as exc:
            return {"ok": False, "reason": exc.detail}
        return {"ok": True, "latency_ms": latency_ms}
