# OS 环境变量会直接灌进 Settings：`_env_file=None` 只挡 `.env` 文件，挡不住进程环境，
# 导出过 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY 等变量的机器上「未配置模型 → 400」这类断言会翻红。
# autouse fixture 统一清掉 config.py 中由环境变量供值的字段，让测试起点一致。

import pytest

# 对应 aitester/config.py 的 Settings 字段（pydantic-settings 默认按大写字段名读环境变量）
_SETTINGS_ENV_VARS = ("HOST", "PORT", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY")


@pytest.fixture(autouse=True)
def _clean_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
