"""运行期模型配置服务：JSON 落盘为唯一真相，.env 仅作首次种子。"""
import copy
from typing import Any

from aitester.adapters.llm import LlmProvider, OpenAICompatProvider, ProviderConfigError
from aitester.config import Settings
from aitester.storage import JsonConfigRepository

CATALOG: list[dict[str, Any]] = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-flash", "enabled": True, "max_output": 393216, "context": 1048576},
            {"id": "deepseek-v4-pro", "enabled": True, "max_output": 393216, "context": 1048576},
        ],
    },
    {
        "id": "dashscope",
        "name": "通义千问 · DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {"id": "qwen3.7-max", "enabled": True, "max_output": 8192, "context": 1000000},
            {"id": "qwen3.8-max", "enabled": True, "max_output": 8192, "context": 131072},
            {"id": "qwen3.7-plus", "enabled": True, "max_output": 8192, "context": 1000000},
            {"id": "qwen3.6-plus", "enabled": True, "max_output": 8192, "context": 1000000},
        ],
    },
]


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
                    "models": [dict(m) for m in p["models"]],
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

    def _first_available_uid(self) -> str:
        for p in self._config["providers"]:
            if not p["api_key"]:
                continue
            for m in p["models"]:
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

    def build_default_provider(self) -> LlmProvider:
        uid = self._config["default_uid"]
        if not uid:
            raise ProviderConfigError(
                "尚未配置默认模型：请在 设置 · 模型设置 中填写 API Key 并选择默认 LLM"
            )
        pid, _, mid = uid.partition("/")
        provider = self._provider(pid)
        model = self._model(provider, mid)
        if not provider["api_key"]:
            raise ProviderConfigError(
                f"默认模型「{uid}」的提供商未配置 API Key：请在 设置 · 模型设置 中填写"
            )
        if not model["enabled"]:
            raise ProviderConfigError(
                f"默认模型「{uid}」已停用：请在 设置 · 模型设置 中启用或改选默认模型"
            )
        return OpenAICompatProvider(
            name=pid,
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            model=mid,
        )
