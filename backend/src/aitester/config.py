from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    return Settings()
