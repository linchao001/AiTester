from aitester.context import ContextBuilder, PassthroughContextBuilder


def test_passthrough_builds_system_history_user() -> None:
    builder: ContextBuilder = PassthroughContextBuilder()
    messages = builder.build(
        "你是测试智能体",
        [{"role": "user", "content": "第一条"}],
        "第二条",
    )
    assert messages == [
        {"role": "system", "content": "你是测试智能体"},
        {"role": "user", "content": "第一条"},
        {"role": "user", "content": "第二条"},
    ]
