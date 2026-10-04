import { useEffect, useRef } from "react";
import type { ChatMessage, ChatStep } from "../../api/client";
import { isWaiting, liveText, type PendingCall, type StreamingState } from "./streamState";
import { fmtTime } from "./utils";
import { mdRender } from "../kb/utils";

// 原型 :1316-1319 同形：label 显示在 chip 上、prompt 填进输入框
const WELCOME_CHIPS = [
  { label: "📋 根据需求生成测试用例", prompt: "根据这份需求文档生成测试用例，按团队模板输出" },
  { label: "🧩 等价类与边界值补覆盖", prompt: "用等价类划分和边界值分析补齐这个功能的用例覆盖" },
  { label: "🔌 接口用例设计", prompt: "为这个接口设计测试用例，列出入参组合与断言点" },
  { label: "🐞 回归失败归因分析", prompt: "分析这次 CI 回归失败，判断是脚本问题还是真实缺陷" },
];

interface Props {
  messages: ChatMessage[];
  agentName: string;
  projectName: string;
  busy: boolean;
  live: StreamingState | null;   // 流式中的唯一 live 状态（ChatPage 持有）：null 即没有进行中的一轮
  onCopy: (text: string) => void;
  onChip: (text: string) => void;   // 原型 :1322-1324：chip 只填输入框，绝不自动发送
}

function Steps({ steps, pending }: { steps: ChatStep[]; pending?: PendingCall[] }) {
  const rows = pending ?? [];
  if (!steps.length && !rows.length) return null;  // 无工具调用时整个过程块不渲染（spec 裁定 4）
  return (
    // 有 ⏳ 行时强制展开：真机行为定义写着「工具轮次期间过程块可见且逐条增长」；
    // pending 清空后 prop 变 undefined，用户此前的开合状态不再被受控属性抢走
    <details className="thinking" open={rows.length ? true : undefined}>
      <summary>🔧 执行过程</summary>
      {steps.map((s, i) => (
        <div className="t-step" key={`${s.round}-${s.tool}-${i}`}>
          <span className="n">{i + 1}.</span>
          <span>{s.tool} · {s.ok ? "成功" : "失败"}</span>
          <span className="args">{s.detail}</span>
        </div>
      ))}
      {rows.map((p, i) => (
        <div className="t-step pending" key={`p-${p.key}-${i}`}>
          <span className="n">{steps.length + i + 1}.</span>
          <span>{p.tool} · ⏳</span>
          <span className="args">{p.detail}</span>
        </div>
      ))}
    </details>
  );
}

export default function MessageList(p: Props) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [p.messages, p.busy, p.live]);

  if (!p.messages.length && !p.busy) {
    return (
      <div className="messages" ref={box}>
        <div className="welcome">
          <div className="w-logo">Ai</div>
          <h2>你好，我是 {p.agentName.replace("智能体", "")}</h2>
          {/* 项目名还没落地时连「📁 名字 ·」一起不渲染，免得欢迎语挂着空占位 */}
          <p>{p.projectName ? `📁 ${p.projectName} · ` : ""}发送消息即在此项目开始新会话 · 会话保存在本机</p>
          <div className="chips">
            {WELCOME_CHIPS.map((c) => (
              <button className="chip" key={c.label} onClick={() => p.onChip(c.prompt)}>{c.label}</button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="messages" ref={box}>
      {p.messages.map((m, i) =>
        m.role === "user" ? (
          <div className="msg user" key={`${m.ts}-${i}`}>
            <div>
              <div className="bubble">{m.content}</div>
              <div className="meta" style={{ justifyContent: "flex-end" }}>
                {fmtTime(m.ts)}<span className="copy" onClick={() => p.onCopy(m.content)}>⧉</span>
              </div>
            </div>
          </div>
        ) : (
          <div className="msg agent" key={`${m.ts}-${i}`}>
            <div className="who"><span className="avatar">Ai</span>AiTester</div>
            <Steps steps={m.steps ?? []} />
            <div className="body md-preview" dangerouslySetInnerHTML={{ __html: mdRender(m.content) }} />
            <div className="meta">
              🗀 {fmtTime(m.ts)}{m.stopped ? <span>（已停止）</span> : null}<span className="copy" onClick={() => p.onCopy(m.content)}>⧉</span>
            </div>
          </div>
        ))}
      {p.busy && isWaiting(p.live) && (
        <div className="msg agent">
          <div className="who"><span className="avatar">Ai</span>AiTester</div>
          <span className="typing"><i /><i /><i /></span>
        </div>
      )}
      {/* 终态门控只看「有没有正文」：done 后 setLive(terminal) 与 append 助手行是两个 React task，
          中间那一帧 terminal=true 但 liveText 还有字——只按 terminal 抑制会让刚流完的回复闪一下没掉。
          要挡的只有 error 先到且一字未出：terminal 且 liveText 空才收气泡。 */}
      {p.busy && p.live && !isWaiting(p.live) && !(p.live.terminal && !liveText(p.live)) && (
        <div className="msg agent">
          <div className="who"><span className="avatar">Ai</span>AiTester</div>
          <Steps steps={p.live.steps} pending={p.live.pending} />
          <div className="body md-preview"
            dangerouslySetInnerHTML={{ __html: mdRender(liveText(p.live)) }} />
          {/* 光标独立成行：mdRender 出的是块级元素，塞进同一段落会被浏览器的
              非法嵌套纠正规则挪位 */}
          <span className="live-caret" />
        </div>
      )}
    </div>
  );
}
