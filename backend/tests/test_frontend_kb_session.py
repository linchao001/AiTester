"""知识库助手前端改造的字节级钉桩（本仓前端无测试运行器，照 test_frontend_context_display 先例读源断言）。

钉三件事，防「编译通过但语义漂移」：
1. KbPage 弃用临时键 kb-console 与 localStorage 消息持久化，改接真实会话链路（getSessions/…）；
2. KbAssistantPane 草案逐个展示——只渲染首个 pending/writing 那张，终态收成灰行；
3. 会话历史列表与草案灰行的样式类确实在 App.css 落地。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_kbpage_uses_real_sessions_not_temporary_key():
    src = _text("pages/KbPage.tsx")
    # 临时键与本地持久化彻底退场：留着任何一条都意味着消息只活在浏览器里、不落盘
    assert "kb-console" not in src
    assert "loadKbMsgs" not in src and "saveKbMsgs" not in src and "msgsStorageKey" not in src
    # 固定走 kb_assistant，会话链路复用聊天页那套接口
    assert 'const KB_AGENT_ID = "kb_assistant";' in src
    assert "getSessions(KB_AGENT_ID, pid)" in src
    assert "getSessionMessages(id, KB_AGENT_ID, pid)" in src
    assert "deleteChatSession(id, KB_AGENT_ID, pid)" in src
    # 发送用当前会话 id（新会话空串→服务端建 sess_ 并回写），历史落盘后读回转界面消息
    assert "{ session_id: activeSessionIdRef.current, message: text, agent_id: KB_AGENT_ID" in src
    assert "function toKbChatMsgs(messages: ChatMessage[]): KbChatMsg[] {" in src
    assert "activeSessionId={activeSessionId}" in src          # 会话态传进独立的会话栏


def test_kb_assistant_shows_one_draft_at_a_time():
    src = _text("pages/kb/KbAssistantPane.tsx")
    # 逐个展示：求「首个 pending/writing」下标，只为它渲染可确认的草案卡
    assert 'if (drafts[i].state === "pending" || drafts[i].state === "writing") { active = i; break; }' in src
    assert "onConfirm={() => onConfirmDraft(mi, active, drafts[active].draft)}" in src
    # 终态草案收成灰行（DraftDoneRow），不再堆叠多张活卡
    assert "function DraftDoneRow({ entry }: { entry: KbDraftEntry })" in src
    assert "<DraftDoneRow key={di} entry={e} />" in src


def test_kb_sessions_list_is_a_standalone_left_column():
    pane = _text("pages/kb/KbSessionsPane.tsx")
    # 会话列表从助手栏内联 <details> 迁出，自成一条 aside 左栏
    assert 'import { fmtTime, groupSessions } from "../chat/utils";' in pane
    assert '<aside className="kb-sess-col">' in pane
    assert "const sessionGroups = useMemo(" in pane
    assert "groupSessions(shown)" in pane
    # 助手栏不再自带会话块
    asst = _text("pages/kb/KbAssistantPane.tsx")
    assert 'className="kb-sessions"' not in asst
    assert "groupSessions" not in asst


def test_kb_editor_hidden_until_file_open_and_assistant_fills():
    src = _text("pages/KbPage.tsx")
    # 编辑区仅在 doc 非空时渲染（默认不展示）；助手栏作为会话栏后的独立左栏被 KbPage 接线
    assert "import KbSessionsPane from \"./kb/KbSessionsPane\";" in src
    assert "{doc && (\n          <KbEditorPane" in src
    # 未打开文件时布局打 no-doc 标记，供 CSS 让助手栏撑满
    assert 'doc ? "" : " no-doc"' in src


def test_kb_session_and_draft_css_classes_exist():
    css = _text("App.css")
    # 会话独立左栏；未打开文件时助手栏按 --kbc 定宽可拖，剩余宽度归占位区；草案灰行类
    assert ".kb-sess-col{" in css
    assert ".kb-reader-spacer{flex:1" in css
    assert ".kb-draft-done{" in css
    assert ".kb-draft-done-title{" in css
