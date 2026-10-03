import { useEffect } from "react";
import { fmtK } from "../../utils";
import { contextUsage } from "./utils";

interface Props {
  input: string;
  busy: boolean;
  modelLabel: string;      // 只用于上下文 tooltip（原型 :1418）；无可用模型传「未配置模型」（原型 :1332）
  cap: number;             // 上下文上限（ModelInfo.context），0 表示不可估算
  systemPrompt: string;
  messages: { content: string }[];
  projectName: string;     // 只读橙 chip 的文案源（项目维度，第 2 片接入）；空串代表项目还没落地，chip 不渲染
  sendBlock: string;       // 非空即「现在还不能发」的原因，由 ChatPage 算（它是 agentId/projectId 的唯一持有者）：折进 canSend 并直接进 title
  inputRef: { current: HTMLTextAreaElement | null };  // 供 chip 点击后聚焦 + 输入框自增高（ChatPage 持有）
  onInput: (v: string) => void;
  onSubmit: () => void;
  onToast: (msg: string) => void;
}

/** 原型 :611-624 逐字对齐：bar 内只有 上下文 meter + 橙项目 chip + 蓝 perm chip + spacer + 发送。
 *  原型橙 chip（:617）是「📁 项目」只读展示，路径不进 UI（第 2 片偏离 5）；
 *  模型 chip 在 chat-header（:598），不在 composer 内，勿在此重复。 */
export default function Composer(p: Props) {
  const usage = contextUsage({ systemPrompt: p.systemPrompt, history: p.messages, input: p.input, cap: p.cap });
  const cls = `ctx-meter${usage.pct >= 90 ? " hot" : usage.pct >= 70 ? " warn" : ""}`;
  const tip = usage.cap
    ? `上下文占用约 ${fmtK(usage.used)} / ${fmtK(usage.cap)} tokens（按「${p.modelLabel}」的最大上下文估算，含系统提示词 + 历史消息 + 当前输入）`
      + (usage.pct >= 90 ? "：已接近上限，建议新建会话" : "")
    : "未配置可用模型，无法估算上下文占用";
  // 阻塞原因非空就不给发：ChatPage.send 在这种状态下是静默 return 的，按钮若还亮着就是死按钮
  const canSend = p.input.trim().length > 0 && !p.busy && !p.sendBlock;
  // 自增高只在这里做：打字、chip 填值、失败回填都只改 input，清空时同样要显回落，
  // 否则框体停在 160px（原型 :1437 的封顶口径）
  useEffect(() => {
    const el = p.inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [p.input, p.inputRef]);

  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          rows={2}
          ref={p.inputRef}
          value={p.input}
          disabled={p.busy}
          placeholder="例如：根据这份需求生成测试用例"
          title="Enter 发送 · Shift+Enter 换行"
          onChange={(e) => p.onInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (canSend) p.onSubmit(); } }}
        />
        <div className="bar">
          <span className={cls} title={tip}>
            <i className="cm-bar"><b style={{ width: `${usage.pct}%` }} /></i>
            <span className="cm-pct">{usage.cap ? `${usage.pct}%` : "—"}</span>
          </span>
          {/* 只读展示：路径不进 UI（第 2 片偏离 5）。必须压掉 .c-chip 的 cursor:pointer，
              否则纯装饰 span 会伪装成可点控件——第 1 片「0 个死按钮」的同一条判据。
              项目名还没落地时整只 chip 不渲染：光杆「📁 」是第二种伪装成有内容的空壳 */}
          {p.projectName ? (
            <span className="c-chip orange" style={{ cursor: "default" }} title="智能体在此目录读写文件">
              📁 {p.projectName}
            </span>
          ) : null}
          {/* 原型 :618 是可展开弹层（自由/严格，严格置灰）。本期只有「自由权限」一档生效，
              做成可点的按钮并给出原型同一条 toast 文案，避免 .c-chip 的 cursor:pointer 变成死控件 */}
          <button
            className="c-chip blue perm"
            disabled={p.busy}
            title="权限模式 · 严格权限暂未开放"
            onClick={() => p.onToast("「严格权限」暂未开放，敬请期待")}
          >🛡 自由权限 ▾</button>
          <div className="spacer" />
          <button
            className={`btn-send${canSend ? " on" : ""}`}
            title={p.busy ? "正在执行…" : p.sendBlock || "发送"}
            disabled={!canSend}
            onClick={p.onSubmit}
          >↑</button>
        </div>
      </div>
      <div className="foot-tip">为测试人员而生 · 用例生成 / 脚本编写 / 失败分析</div>
    </div>
  );
}
