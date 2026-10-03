import { useEffect, useRef } from "react";
import type { ChatMessage, ChatStep } from "../../api/client";
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
  onCopy: (text: string) => void;
  onChip: (text: string) => void;   // 原型 :1322-1324：chip 只填输入框，绝不自动发送
}

function Steps({ steps }: { steps: ChatStep[] }) {
  if (!steps.length) return null;  // 无工具调用时整个过程块不渲染（spec 裁定 4）
  return (
    <details className="thinking">
      <summary>🔧 执行过程</summary>
      {steps.map((s, i) => (
        <div className="t-step" key={`${s.round}-${s.tool}-${i}`}>
          <span className="n">{i + 1}.</span>
          <span>{s.tool} · {s.ok ? "成功" : "失败"}</span>
          <span className="args">{s.detail}</span>
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
  }, [p.messages, p.busy]);

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
              🗀 {fmtTime(m.ts)}<span className="copy" onClick={() => p.onCopy(m.content)}>⧉</span>
            </div>
          </div>
        ))}
      {p.busy && (
        <div className="msg agent">
          <div className="who"><span className="avatar">Ai</span>AiTester</div>
          <span className="typing"><i /><i /><i /></span>
        </div>
      )}
    </div>
  );
}
