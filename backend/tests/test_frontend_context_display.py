"""前端不许再自己算 token：尺只有一把（CM-5、R-C2）。

本仓前端没有测试运行器（package.json 只有 dev/build/preview），故这些钉桩放在后端，
照 test_agents.py 逐字读提示词 md 的先例读前端源文件断言字节级事实。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_chat_utils_no_longer_estimates_tokens():
    src = _read("pages/chat/utils.ts")
    for gone in ("estTokens", "HISTORY_MAX", "contextUsage", "\\u4e00-\\u9fff"):
        assert gone not in src, gone


def test_composer_renders_backend_snapshot_only():
    src = _read("pages/chat/Composer.tsx")
    assert "peak_occupancy" in src and "occupancy_source" in src
    # 钉的是「界面层不许再算 token」，不是「不许做百分比折算」——
    # 早期草稿里那条 `assert "Math.round(" not in src` 会误伤 pct 折算，已按 R-C2 的正确
    # 口径换成下面两条符号钉（改测试的理由写进 commit message）。
    assert "estTokens" not in src and "contextUsage" not in src
    assert '"—"' in src                     # 没有窗口就是「—」，不是 0% 也不是 100%
    # R-1（spec §3.8 + R-C1）：估算读数除了 tooltip 里那句「估算」，界面上也必须看得出来
    assert "≈" in src                        # 估算的百分比前缀
    assert " est" in src                     # 估算时挂在 .ctx-meter 上的类名
    css = _read("App.css")
    assert ".ctx-meter.est .cm-bar b{opacity:.55}" in css   # 与之配对的那一条样式


def test_done_frame_and_message_row_carry_context():
    src = _read("api/client.ts")
    assert "export interface ContextSnapshot" in src
    assert "stopped: boolean; context: ContextSnapshot | null }>" in src   # done 帧那一行
    assert "context?: ContextSnapshot | null;" in src                       # 会话行可选节
