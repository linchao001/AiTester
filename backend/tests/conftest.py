# OS 环境变量会直接灌进 Settings：`_env_file=None` 只挡 `.env` 文件，挡不住进程环境，
# 导出过 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY 等变量的机器上「未配置模型 → 400」这类断言会翻红。
# autouse fixture 统一清掉 config.py 中由环境变量供值的字段，让测试起点一致。

import pytest

# 对应 aitester/config.py 的 Settings 字段（pydantic-settings 默认按大写字段名读环境变量）；
# REME_KNOWLEDGE_BASES_DIR 非 Settings 字段，但 kb/paths.py 的三级判据会直读该进程环境变量，
# 机器上若导出过该变量会让"家目录默认"分支断言翻红，故一并清理。
_SETTINGS_ENV_VARS = ("HOST", "PORT", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "REME_KNOWLEDGE_BASES_DIR")


@pytest.fixture(autouse=True)
def _clean_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # 启动期词表预热线程（main.lifespan）在测试进程里也会被拉起：warm() 拿到缓存词表只要
    # 0.22 秒，却会把 meter 的全局 _enc 翻成词表分支——test_context_meter 里那条「退字符
    # 兜底」的断言就在套件中途变红。置 0 让 warm() 只记因、不触网也不碰 _enc；
    # meter 自己的挂具（_isolated_meter）逐条删这个变量，C1 的用例口径不受影响。
    monkeypatch.setenv("AITESTER_TOKEN_WARM", "0")
