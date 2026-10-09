"""工具产物 token 闸的行为测试——1 token = 1 字符，让每条断言都是整数而不是近似。"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from aitester.adapters.tools.base import AiTooler
from aitester.context import meter
from aitester.context.budget import (
    CAP_ENV,
    TOOL_OUTPUT_TOKEN_CAP,
    TRUNCATION_MARK,
    cap_content,
    cap_for,
    cap_result,
)
from aitester.context.usage import ContextUsage


class _OneCharOneToken:
    """一个 token = 一个字符，好让断言是整数而不是「近似」。"""

    def encode(self, text, disallowed_special=()):
        return list(text)


@pytest.fixture(autouse=True)
def _one_char_one_token(monkeypatch):
    monkeypatch.delenv(CAP_ENV, raising=False)      # cap_for() 读 env：机器上导出该变量会让默认断言无故变红
    meter.reset_for_tests()
    meter._enc = _OneCharOneToken()
    meter._ready.set()
    yield
    meter.reset_for_tests()


def _usage() -> ContextUsage:
    return ContextUsage(window=1000)


def test_cap_for_reads_env_and_rejects_garbage(monkeypatch):
    assert cap_for() == TOOL_OUTPUT_TOKEN_CAP
    monkeypatch.setenv(CAP_ENV, "777")
    assert cap_for() == 777
    monkeypatch.setenv(CAP_ENV, "abc")            # 非法值不许把闸关掉
    assert cap_for() == TOOL_OUTPUT_TOKEN_CAP
    monkeypatch.setenv(CAP_ENV, "0")              # 0/负数同理：不许关闸
    assert cap_for() == TOOL_OUTPUT_TOKEN_CAP


def test_under_cap_returns_identical_bytes_and_leaves_no_trace(monkeypatch):
    monkeypatch.setenv(CAP_ENV, "100")
    u = _usage()
    text = "短内容" * 5                            # 15 字符 → ceil(15×1.15)=18
    assert cap_content(text, tool="shell", usage=u) == text
    assert u.truncations == []


def test_exactly_at_cap_is_untouched_and_cap_plus_one_moves_the_knife(monkeypatch):
    """spec §6 的边界三类之一：正好上限与上限 +1 的差别必须是「动不动刀」。
    1 token = 1 字符时 ceil(69×1.15)=80（正好）、ceil(70×1.15)=81（越界）。"""
    monkeypatch.setenv(CAP_ENV, "80")
    u = _usage()
    assert cap_content("x" * 69, tool="shell", usage=u) == "x" * 69
    assert u.truncations == []
    out = cap_content("x" * 70, tool="shell", usage=u)
    assert out != "x" * 70 and "已截断" in out
    assert meter.estimate_text(out) <= 80          # 含标记的整段才算数
    assert u.truncations[0].dropped > 0


def test_over_cap_marks_with_three_numbers_and_keeps_head_and_tail(monkeypatch):
    monkeypatch.setenv(CAP_ENV, "100")
    u = _usage()
    text = "".join(f"L{i:03d}\n" for i in range(100))   # 500 字符 → ceil(500×1.15)=575
    out = cap_content(text, tool="shell", usage=u)
    assert out.startswith(TRUNCATION_MARK.split("{")[0])
    assert "原约 575 tokens" in out
    assert meter.estimate_text(out) <= 100          # 标记自身也计入预算——超线就是谎
    assert text[:5] in out and "L099\n" in out      # 头尾都在
    assert out.count("L000\n") == 1 and out.count("L099\n") == 1
    t = u.truncations[0]
    assert t.tool == "shell" and t.original == 575 and t.kept + t.dropped == t.original


def test_multibyte_caps_on_tokens_not_bytes(monkeypatch):
    """CJK 正文按 token 口径截：字节数不是放行理由，截完仍是合法字符串。"""
    monkeypatch.setenv(CAP_ENV, "80")
    u = _usage()
    text = "智会宝用例" * 40                        # 200 字符 / 600 UTF-8 字节 → est 230
    out = cap_content(text, tool="shell", usage=u)
    assert meter.estimate_text(out) <= 80
    assert 0 < out.count("智") < 40 and "\n…\n" in out
    t = u.truncations[0]
    assert t.kept + t.dropped == t.original == 230


def test_tiny_cap_below_the_mark_itself_lets_the_mark_win(monkeypatch):
    """cap 小于一行标记自己（≈70 token）时不静默塞内容：标记必在、正文让位。
    默认 12000 离这个下限很远，这条钉的是「闸关不掉的极端区间不许说谎」。"""
    monkeypatch.setenv(CAP_ENV, "10")
    u = _usage()
    out = cap_content("y" * 400, tool="shell", usage=u)
    assert out.startswith(TRUNCATION_MARK.split("{")[0])
    assert len(out) < 400
    assert u.truncations[0].kept + u.truncations[0].dropped == u.truncations[0].original


def test_no_usage_still_caps(monkeypatch):
    monkeypatch.setenv(CAP_ENV, "50")
    text = "x" * 200
    out = cap_content(text, tool="read", usage=None)
    assert len(out) < len(text) and "已截断" in out


def test_cap_failure_returns_original_and_records_reason(monkeypatch):
    monkeypatch.setenv(CAP_ENV, "100")
    u = _usage()

    def boom(text):
        raise RuntimeError("estimator down")
    monkeypatch.setattr(meter, "estimate_text", boom)
    text = "y" * 10
    assert cap_content(text, tool="shell", usage=u) == text       # 原样放行，不炸回合
    assert u.error is not None and "RuntimeError" in u.error


def test_cap_result_only_touches_content_never_the_artifact(monkeypatch):
    monkeypatch.setenv(CAP_ENV, "40")
    u = _usage()
    artifact = {"diffs": [{"before": "a" * 500, "after": "b" * 500}]}
    text, kept_artifact = cap_result(("x" * 300, artifact), tool="read", usage=u)
    assert len(text) < 300
    assert kept_artifact is artifact                  # 身份与内容都不动：前端 diff 面板不许被砍坏
    assert cap_result(12345, tool="kb", usage=u) == 12345        # 非字符串原样通过


class _EchoIn(BaseModel):
    text: str = Field(default="")


class _EchoTool(AiTooler):
    name: str = "echo_gate_probe"
    description: str = "returns its input verbatim"
    args_schema: type[BaseModel] | None = _EchoIn

    def _run(self, text: str) -> str:
        return text


def test_run_caps_without_any_graph(monkeypatch):
    """不经 ToolNode、不经图：直接 tool.run 也被截——证明闸在工具层（CM-5 的证）。"""
    monkeypatch.setenv(CAP_ENV, "60")
    u = _usage()
    tool = _EchoTool(usage=u)
    assert tool.usage is u                        # pydantic 不许重新构造它（身份钉）
    out = tool.run({"text": "z" * 400})
    assert "已截断" in out and len(out) < 400
    assert u.truncations[0].original == 460


def test_arun_caps_identically(monkeypatch):
    """两条入口逐字同效：只有 run 被覆盖的话，这条测试就是红证。"""
    monkeypatch.setenv(CAP_ENV, "60")
    u = _usage()
    out = asyncio.run(_EchoTool(usage=u).arun({"text": "z" * 400}))
    assert "已截断" in out
    assert u.truncations[0].tool == "echo_gate_probe"


def test_usage_free_tool_still_caps_and_no_crash(monkeypatch):
    monkeypatch.setenv(CAP_ENV, "60")
    out = _EchoTool().run({"text": "z" * 400})    # 装配层没给 usage：照样管住
    assert "已截断" in out


def test_double_entry_does_not_double_book(monkeypatch):
    """arun 内部若落到 run，第二次 cap 看到已达标正文 ⇒ 原样返回、不记第二笔。
    这条钉的是「两条调用点不会变成两本账」——cap_content 的 original<=cap 短路是它的支点。"""
    monkeypatch.setenv(CAP_ENV, "200")
    u = _usage()
    once = cap_content("w" * 400, tool="echo_gate_probe", usage=u)
    twice = cap_content(once, tool="echo_gate_probe", usage=u)
    assert twice == once
    assert len(u.truncations) == 1


# 生产形状（ToolNode→invoke/ainvoke 得到 ToolMessage）的补测——闸必须认得消息形态。
# 命名 _ToolCallProbe 而非 _EchoTool：文件上方已有 _EchoTool（name=echo_gate_probe），
# 复用同名会遮蔽它、打断既有测试；此处工具名仍逐字为 "echo_tool"，断言字面量不变。
class _ToolCallProbe(AiTooler):
    name: str = "echo_tool"
    description: str = "echo"

    def _run(self, text: str) -> str:
        return text


def test_invoke_with_tool_call_id_caps_the_tool_message_content(tmp_path, monkeypatch):
    """生产形状（ToolNode→invoke(tool_call)）也必须被截——主路径漏管就是闸没闸。"""
    from langchain_core.messages import ToolMessage
    monkeypatch.setenv(CAP_ENV, "20")
    u = _usage()
    msg = _ToolCallProbe(usage=u).invoke(
        {"name": "echo_tool", "args": {"text": "z" * 200}, "id": "c1", "type": "tool_call"})
    assert isinstance(msg, ToolMessage)
    assert len(msg.content) < 200
    assert "原约" in msg.content and "省略中段" in msg.content      # R-C3 标记进了模型看得见的那条
    assert msg.tool_call_id == "c1" and msg.name == "echo_tool"
    t = u.truncations[0]
    assert t.tool == "echo_tool" and t.kept + t.dropped == t.original


def test_ainvoke_with_tool_call_id_caps_once_and_does_not_double_book(tmp_path, monkeypatch):
    """异步主路径同样管；arun→run 不许把一条产物记成两次截断。"""
    monkeypatch.setenv(CAP_ENV, "20")
    u = _usage()
    msg = asyncio.run(_ToolCallProbe(usage=u).ainvoke(
        {"name": "echo_tool", "args": {"text": "z" * 200}, "id": "c2", "type": "tool_call"}))
    assert "原约" in msg.content and len(msg.content) < 200
    assert len(u.truncations) == 1                    # 一次调用 = 一条留痕


def test_content_and_artifact_message_keeps_artifact_identity_when_capped(tmp_path, monkeypatch):
    """文件工具走消息形态时，artifact 仍是同一个对象——一个字节都不动的规则不打折。"""
    from aitester.adapters.tools.file_tools import ReadTool
    monkeypatch.setenv(CAP_ENV, "20")
    (tmp_path / "big.txt").write_text("行\n" * 400, encoding="utf-8")
    u = _usage()
    tool = ReadTool(cwd=str(tmp_path), session_id="s", observed=None)
    msg = tool.invoke({"name": "read", "args": {"file_path": "big.txt"},
                       "id": "c3", "type": "tool_call"})
    assert "原约" in msg.content
    assert msg.artifact is not None                    # artifact 仍在，且是被原样带过去的引用


def test_under_cap_message_is_returned_by_identity_and_leaves_no_trace(monkeypatch):
    monkeypatch.setenv(CAP_ENV, "1000")
    u = _usage()
    msg = _ToolCallProbe(usage=u).invoke(
        {"name": "echo_tool", "args": {"text": "短"}, "id": "c4", "type": "tool_call"})
    assert msg.content == "短" and u.truncations == []   # 没砍就不许新建对象、不许留痕


def test_tiny_body_clamps_kept_so_the_three_numbers_cannot_lie(monkeypatch):
    """正文短于省略号自己时 body 反而比原文长，夹子必须咬合：模型不许看见「保留 > 原约」。
    1 token = 1 字符再乘 1.15：4 字符原文 est 5，收窄后 body（"a\\n…\\nd"）est 6。"""
    monkeypatch.setenv(CAP_ENV, "2")
    u = _usage()
    out = cap_content("abcd", tool="shell", usage=u)
    t = u.truncations[0]
    assert (t.original, t.kept, t.dropped) == (5, 5, 0)
    assert "原约 5 tokens" in out and "保留 5" in out and "省略中段 0" in out


def test_cap_result_returns_the_very_same_message_object_when_under_cap(monkeypatch):
    """白盒直断「没砍就不新建对象」：经 invoke 拿到的消息每次都是新的，外部断不出身份。"""
    from langchain_core.messages import ToolMessage
    monkeypatch.setenv(CAP_ENV, "1000")
    msg = ToolMessage(content="短", tool_call_id="c9", name="echo_tool")
    assert cap_result(msg, tool="echo_tool", usage=None) is msg
