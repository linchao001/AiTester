from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


@lru_cache(maxsize=1)
def _kb_embedding_constants() -> tuple[str, str]:
    # 延迟 import：避免 aitester.config ↔ memory.reme 顶层成环。
    from aitester.memory.reme.config import (
        DEFAULT_EMBEDDING_BASE_URL,
        DEFAULT_EMBEDDING_MODEL,
    )

    return DEFAULT_EMBEDDING_BASE_URL, DEFAULT_EMBEDDING_MODEL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000

    # 仅首次启动生成 backend/data/model_config.json 时作为种子读取
    deepseek_api_key: str = ""
    dashscope_api_key: str = ""

    # 知识库（ReMe）
    kb_enabled: bool = True
    kb_id: str = "zhb_kb"
    kb_bases_dir: str = ""
    kb_create_missing: bool = True
    kb_embedding_api_key: str = ""
    kb_embedding_base_url: str = Field(
        default_factory=lambda: _kb_embedding_constants()[0]
    )
    kb_embedding_model: str = Field(default_factory=lambda: _kb_embedding_constants()[1])
    kb_embedding_dimensions: int = 1024

    # 个人记忆（Reme search / auto_memory）；会话 SessionStore 仍独立
    personal_memory_enabled: bool = True
    auto_memory_search_enabled: bool = True
    auto_memory_enabled: bool = True
    auto_memory_search_limit: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
