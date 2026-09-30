"""运行期模型配置服务：JSON 落盘为唯一真相，.env 仅作首次种子。"""
import copy
from typing import Any

from aitester.config import Settings
from aitester.storage import ModelConfigRepository

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

    def __init__(self, repo: ModelConfigRepository, settings: Settings) -> None:
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
