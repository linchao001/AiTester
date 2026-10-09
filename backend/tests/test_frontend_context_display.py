"""前端不许再自己算 token：尺只有一把（CM-5、R-C2）。

本仓前端没有测试运行器（package.json 只有 dev/build/preview），故这些钉桩放在后端，
照 test_agents.py 逐字读提示词 md 的先例读前端源文件断言字节级事实。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# R-C2 的覆盖面是**整个界面层**，不是某一个文件里的某两个名字：把旧尺改名、
# 或搬到别的页面，一样是第二把尺。`length / 4` 与 `* 0.25` 是被删掉的那把 CJK 启发式的
# 形状——必须带 `length` 前缀，否则中文注释里的「404 / 409」这类错误码会把它撞红。
_BANNED = ("estTokens", "contextUsage", "HISTORY_MAX", "\\u4e00-\\u9fff", "length / 4", "length/4", "* 0.25")


def test_chat_utils_no_longer_estimates_tokens():
    offenders = [f"{p.relative_to(ROOT).as_posix()}: {gone}"
                 for p in sorted(ROOT.rglob("*.ts*"))
                 for text in [p.read_text(encoding="utf-8")]
                 for gone in _BANNED if gone in text]
    assert offenders == []
    # 界面层唯一还合法数「字节」的地方是 kb 体积展示（TextEncoder 数的是字节，不是 token）；
    # 它一旦扩散到第二个文件，就该怀疑有人又造了一把尺。
    counters = [p.relative_to(ROOT).as_posix() for p in sorted(ROOT.rglob("*.ts*"))
                if "TextEncoder" in p.read_text(encoding="utf-8")]
    assert counters == ["pages/kb/utils.ts"], counters


def test_composer_renders_backend_snapshot_only():
    src = _text("pages/chat/Composer.tsx")
    # CM-6「占用与花费是两个读数，不许混」的判据是**画条子那个表达式**，不是符号出没出现：
    # peak_occupancy 在 tooltip 里也露面，所以只钉符号等于没钉（变异成 spent_input 照样绿）。
    assert "Math.round((u.peak_occupancy / u.window) * 100)" in src
    assert "u.spent_input / u.window" not in src
    assert "occupancy_source" in src
    # 钉的是「界面层不许再算 token」，不是「不许做百分比折算」——
    # 早期草稿里那条 `assert "Math.round(" not in src` 会误伤 pct 折算，已按 R-C2 的正确
    # 口径换成下面两条符号钉（改测试的理由写进 commit message）。
    assert "estTokens" not in src and "contextUsage" not in src
    assert '"—"' in src                     # 没有窗口就是「—」，不是 0% 也不是 100%
    # R-1（spec §3.8 + R-C1）：估算读数除了 tooltip 里那句「估算」，界面上也必须看得出来
    assert "≈" in src                        # 估算的百分比前缀
    assert " est" in src                     # 估算时挂在 .ctx-meter 上的类名
    css = _text("App.css")
    assert ".ctx-meter.est .cm-bar b{opacity:.55}" in css   # 与之配对的那一条样式
    # M-1：前缀、类名、tooltip 三处必须共用**同一个**判据——两处写不同的比较式，
    # 后端一旦给出第三种 occupancy_source 就会「有 ≈ 却没有半透明条」自相矛盾。
    assert src.count("occupancy_source") == 1
    assert '${est ? " est" : ""}' in src and '${est ? "≈" : ""}' in src
    # R-C3 + spec §3.8：截断的原长/保留/省略三个数必须在界面上看得见（本片落在上下文
    # tooltip，逐条列到工具名）。只钉「截断 N 处」不够——把三个数删光照样绿。
    assert "本回合截断" in src
    assert all(k in src for k in ("t.original", "t.kept", "t.dropped"))


def test_done_frame_and_message_row_carry_context():
    src = _text("api/client.ts")
    assert "export interface ContextSnapshot" in src
    assert "stopped: boolean; context: ContextSnapshot | null }>" in src   # done 帧那一行
    assert "context?: ContextSnapshot | null;" in src                       # 会话行可选节
    # I-4：CM-3 的另一半写在 ChatPage 的状态搬运上——这条链没人钉，删掉「新会话清读数」
    # 全仓不会红，而屏幕上会出现「一条消息都没有却挂着上次会话占用」的谎。
    page = _text("pages/ChatPage.tsx")
    assert "setCtxSnap(f.context ?? null)" in page          # 终帧读数进界面
    assert "if (!id) { setMessages([]); setCtxSnap(null); return; }" in page   # 新建会话必清
    assert 'const last = [...j.messages].reverse().find((m) => m.role === "assistant");' in page
    assert "setCtxSnap(last?.context ?? null)" in page      # 打开会话读末条 assistant 行
    assert "usage={ctxSnap}" in page                        # 界面唯一读数来源就是这份快照
