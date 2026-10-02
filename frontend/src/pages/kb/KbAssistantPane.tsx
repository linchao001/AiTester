import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { KbDraft } from "../../api/client";
import { mdRender } from "./utils";
import KbDraftCard, { type KbDraftState } from "./KbDraftCard";

/** 消息流内嵌草案条目：drafts 逐条挂在所属 ai 消息里（原型 kbDraftCard(host=b) 的 DOM 归属，React 化）。 */
export interface KbDraftEntry {
  draft: KbDraft;
  state: KbDraftState;
  writtenAt?: number;
}

/** 一条聊天消息：me 纯文本；ai 为 markdown 源（pending=「思考中…」占位气泡，error=红色错误行），
    drafts 内嵌该回复携带的草案卡。 */
export interface KbChatMsg {
  who: "me" | "ai";
  text: string;
  pending?: boolean;
  error?: boolean;
  drafts?: KbDraftEntry[];
}

export interface KbAssistantPaneProps {
  messages: KbChatMsg[];
  busy: boolean;
  /** 右栏 scope 徽标：KB 根目录名（原型 :2381，root 未载入回退 zhb_kb）。 */
  root: string;
  onHide: () => void;
  onAsk: (text: string) => void;
  /** (消息下标, 草案下标, 草案) —— KbPage 侧 confirmDraft 接线。 */
  onConfirmDraft: (msgIdx: number, draftIdx: number, draft: KbDraft) => void;
  onCancelDraft: (msgIdx: number, draftIdx: number) => void;
}

/** 五条快速指令 chips（原型 :2699 文案逐字）。 */
const QUICK_COMMANDS = [
  "按当前目录生成索引",
  "查重复用例",
  "给当前笔记补 description",
  "导出 P0 用例清单",
  "搜索 舍入",
];

/** 原型助手栏 :737-750 + kbAsk/kbSay（:2556-2563、:2675-2689、:2690-2701）的 React 转写。
    整列 aside.kb-chat 迁入本组件（与 KbEditorPane 同构）；消息状态由 KbPage 持有。 */
export default function KbAssistantPane({
  messages, busy, root, onHide, onAsk, onConfirmDraft, onCancelDraft,
}: KbAssistantPaneProps) {
  const [input, setInput] = useState("");
  const msgsRef = useRef<HTMLDivElement>(null);

  // 原型 kbSay/kbDraftCard/kbAsk 尾部的 scrollTop 跟手（:2561/:2595/:2687）
  useEffect(() => {
    const el = msgsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  const send = () => {
    const v = input.trim();
    if (!v) return;
    setInput("");
    onAsk(v); // busy 锁在 KbPage.ask 内统一守卫（原型 KB.busy :2676）
  };

  // 原型 :2696-2698 —— Ctrl/Cmd+Enter 发送
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      send();
    }
  };

  return (
    <aside className="kb-chat">
      <div className="kb-chat-head">🤖 知识库助手<div className="spacer"></div>
        <span className="scope">{root ? root.split(/[\\/]/).pop() : "zhb_kb"}</span>
        <button className="icon-btn" title="隐藏助手" onClick={onHide}>»</button>
      </div>
      <div className="kb-msgs" ref={msgsRef}>
        {messages.map((m, mi) => m.who === "me" ? (
          /* 原型 kbSay who==='me' 走 textContent —— React 文本节点天然同义 */
          <div className="kb-msg me" key={mi}>{m.text}</div>
        ) : (
          <div className="kb-msg ai" key={mi}>
            {m.pending ? (
              <span style={{ color: "var(--text-2)" }}>{m.text}</span>
            ) : m.error ? (
              /* 原型 :2685 —— 错误消息 escapeHtml 后红色呈现，不走 mdRender */
              <span style={{ color: "var(--fail)" }}>{m.text}</span>
            ) : (
              <span className="md-preview" dangerouslySetInnerHTML={{ __html: mdRender(m.text) }} />
            )}
            {m.drafts?.map((e, di) => (
              <KbDraftCard
                key={di}
                draft={e.draft}
                state={e.state}
                writtenAt={e.writtenAt}
                onConfirm={() => onConfirmDraft(mi, di, e.draft)}
                onCancel={() => onCancelDraft(mi, di)}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="kb-chips">
        {QUICK_COMMANDS.map((c) => (
          <button className="chip" key={c} onClick={() => onAsk(c)}>{c}</button>
        ))}
      </div>
      <div className="kb-composer">
        <textarea
          value={input}
          placeholder="例如：把当前笔记补上 description / 导出 test 目录的 P0 用例清单"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="bar"><span className="tip">助手只生成草案，写入磁盘前需你确认</span><div className="spacer"></div>
          <button className="ws-btn main" disabled={busy} onClick={send}>发送</button>
        </div>
      </div>
    </aside>
  );
}
