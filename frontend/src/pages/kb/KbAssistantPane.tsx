import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { ChatStep, KbDraft } from "../../api/client";
import { mdRender } from "./utils";
import KbDraftCard, { type KbDraftState } from "./KbDraftCard";

/** 消息流内嵌草案条目：drafts 逐条挂在所属 ai 消息里（原型 kbDraftCard(host=b) 的 DOM 归属，React 化）。 */
export interface KbDraftEntry {
  draft: KbDraft;
  state: KbDraftState;
  writtenAt?: number;
}

/** 一条聊天消息：me 纯文本；ai 为 markdown 源（pending=「思考中…」占位气泡，error=红色错误行），
    drafts 内嵌该回复携带的草案卡（live-only：重开旧会话不重现草案卡）；
    steps 为历史回合的工具过程（从落盘 jsonl 读回，live 回合不填）。 */
export interface KbChatMsg {
  who: "me" | "ai";
  text: string;
  pending?: boolean;
  error?: boolean;
  /** 被停止的回复：气泡尾部挂「（已停止）」，与聊天页 assistant 行同款标注。 */
  stopped?: boolean;
  drafts?: KbDraftEntry[];
  steps?: ChatStep[];
}

export interface KbAssistantPaneProps {
  messages: KbChatMsg[];
  busy: boolean;
  /** 停止已发出、等后端把 stopped=true 的 done 送回来：期间停止钮置灰防重复请求。 */
  stopRequested: boolean;
  /** 右栏 scope 徽标：界面知识库根名（恒为别名，原型 :2381 处读 KB 根目录名）。 */
  root: string;
  /** 助手为当前主栏（未打开文件）时，头部承载隐藏栏的「显示」chip（目录树/会话历史）。 */
  headLeading?: ReactNode;
  /** 未打开文件时助手是唯一主栏，收起钮无效（收起会无处复原）：此时不渲染 » 钮。 */
  canHide?: boolean;
  onHide: () => void;
  onAsk: (text: string) => void;
  onStop: () => void;
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

/** 已落终态的草案收成一行灰字（逐个展示：当前张落终态后下一张 pending 自动顶上来）。 */
function DraftDoneRow({ entry }: { entry: KbDraftEntry }) {
  const d = entry.draft;
  const kind = d.kind ?? "save";
  const badge =
    kind === "delete" ? "删除" : kind === "edit" ? "改正文"
      : kind === "frontmatter" ? "删元数据" : (d.op === "create" ? "新建" : "修改");
  const doneVerb =
    entry.state === "done" ? (kind === "delete" ? "✓ 已删除" : kind === "save" ? "✓ 已写入" : "✓ 已修改")
      : entry.state === "canceled" ? "已取消"
        : "✕ 失败";
  // 主标题：save 用节点标题，维护类用目标文件名（title 对维护类为空）。
  const subject = kind === "save" ? d.title : (d.path.split("/").pop() || d.path);
  return (
    <div className="kb-draft-done">
      <span className="d-op">{badge}</span>
      <span className="kb-draft-done-title">{subject}</span>
      <span className="kb-draft-done-state">{doneVerb}</span>
    </div>
  );
}

/** 原型助手栏 :737-750 + kbAsk/kbSay（:2556-2563、:2675-2689、:2690-2701）的 React 转写。
    整列 aside.kb-chat 迁入本组件（与 KbEditorPane 同构）；消息状态由 KbPage 持有。 */
export default function KbAssistantPane({
  messages, busy, stopRequested, root, headLeading, canHide = true,
  onHide, onAsk, onStop, onConfirmDraft, onCancelDraft,
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

  // 与聊天页同款：Enter 发送，Shift+Enter 换行（不再要求 Ctrl/Cmd+Enter）
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!busy) send();
    }
  };

  return (
    <aside className="kb-chat">
      <div className="kb-chat-head">
        {headLeading}
        🤖 知识库助手<div className="spacer"></div>
        <span className="scope">{root}</span>
        {canHide && <button className="icon-btn" title="隐藏助手" onClick={onHide}>»</button>}
      </div>
      <div className="kb-msgs" ref={msgsRef}>
        {messages.map((m, mi) => m.who === "me" ? (
          /* 原型 kbSay who==='me' 走 textContent —— React 文本节点天然同义 */
          <div className="kb-msg me" key={mi}>{m.text}</div>
        ) : (
          <div className="kb-msg ai" key={mi}>
            {m.steps && m.steps.length > 0 && (
              <details className="thinking">
                <summary>🔧 执行过程</summary>
                {m.steps.map((s, i) => (
                  <div className="t-step" key={`${s.round}-${s.tool}-${i}`}>
                    <span className="n">{i + 1}.</span>
                    <span>{s.tool} · {s.ok ? "成功" : "失败"}</span>
                    <span className="args">{s.detail}</span>
                  </div>
                ))}
              </details>
            )}
            {m.pending ? (
              <span style={{ color: "var(--text-2)" }}>{m.text}</span>
            ) : m.error ? (
              /* 原型 :2685 —— 错误消息 escapeHtml 后红色呈现，不走 mdRender */
              <span style={{ color: "var(--fail)" }}>{m.text}</span>
            ) : (
              <span className="md-preview" dangerouslySetInnerHTML={{ __html: mdRender(m.text) }} />
            )}
            {m.stopped ? (
              <span style={{ color: "var(--text-2)" }}>（已停止）</span>
            ) : null}
            {/* 草案逐个展示：只渲染「首个 pending/writing」那张卡；已落终态的收成灰行，
                尚未轮到（其后仍 pending）的隐藏——当前卡落终态后自动顶出下一张。 */}
            {(() => {
              const drafts = m.drafts ?? [];
              let active = -1;
              for (let i = 0; i < drafts.length; i++) {
                if (drafts[i].state === "pending" || drafts[i].state === "writing") { active = i; break; }
              }
              return (
                <>
                  {drafts.map((e, di) => (
                    di === active ? null
                      : e.state === "pending" || e.state === "writing" ? null
                        : <DraftDoneRow key={di} entry={e} />
                  ))}
                  {active >= 0 && (
                    <KbDraftCard
                      draft={drafts[active].draft}
                      state={drafts[active].state}
                      writtenAt={drafts[active].writtenAt}
                      onConfirm={() => onConfirmDraft(mi, active, drafts[active].draft)}
                      onCancel={() => onCancelDraft(mi, active)}
                    />
                  )}
                </>
              );
            })()}
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
          title="Enter 发送 · Shift+Enter 换行"
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="bar"><span className="tip">Enter 发送 · Shift+Enter 换行 · 写入磁盘前需确认</span><div className="spacer"></div>
          {busy ? (
            <button className="ws-btn" disabled={stopRequested}
              title={stopRequested ? "停止中…" : "停止生成"} onClick={onStop}>■ 停止</button>
          ) : (
            <button className="ws-btn main" disabled={busy} onClick={send}>发送</button>
          )}
        </div>
      </div>
    </aside>
  );
}
