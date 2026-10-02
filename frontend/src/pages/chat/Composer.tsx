import { useEffect } from "react";
import { contextUsage } from "./utils";

interface Props {
  input: string;
  busy: boolean;
  modelLabel: string;      // 只用于上下文 tooltip（原型 :1341 口径）；无可用模型传「未配置模型」
  cap: number;             // 上下文上限（ModelInfo.context），0 表示不可估算
  systemPrompt: string;
  messages: { content: string }[];
  inputRef: { current: HTMLTextAreaElement | null };  // 供 chip 点击后聚焦 + 输入框自增高（ChatPage 持有）
  onInput: (v: string) => void;
  onSubmit: () => void;
  onToast: (msg: string) => void;
}

/** 原型 :611-624 逐字对齐：bar 内只有 上下文 meter + 蓝 perm chip + spacer + 发送。
 *  原型橙 chip（:617）是「📁 项目 · Agent 工作目录」，属项目维度 → 第 2 片才接，本期不渲染；
 *  模型 chip 在 chat-header（:598），不在 composer 内，勿在此重复。 */
export default function Composer(p: Props) {
  const usage = contextUsage({ systemPrompt: p.systemPrompt, history: p.messages, input: p.input, cap: p.cap });
  const cls = `ctx-meter${usage.pct >= 90 ? " hot" : usage.pct >= 70 ? " warn" : ""}`;
  const tip = usage.cap
    ? `上下文占用约 ${usage.used} / ${usage.cap} tokens（按「${p.modelLabel}」的最大上下文估算，含系统提示词 + 历史消息 + 当前输入）`
      + (usage.pct >= 90 ? "：已接近上限，建议新建会话" : "")
    : "未配置可用模型，无法估算上下文占用";
  const canSend = p.input.trim().length > 0 && !p.busy;
  // 高度写在 style 上，发送后 input 清空需显回落，否则框体停在 160px（原型 :1437 同款收口）
  useEffect(() => {
    const el = p.inputRef.current;
    if (el && !p.input) el.style.height = "auto";
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
          onChange={(e) => {
            p.onInput(e.target.value);
            // 原型 :1423 的自适应高度：随输入长高，封顶 160px
            const el = e.currentTarget;
            el.style.height = "auto";
            el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
          }}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (canSend) p.onSubmit(); } }}
        />
        <div className="bar">
          <span className={cls} title={tip}>
            <i className="cm-bar"><b style={{ width: `${usage.pct}%` }} /></i>
            <span className="cm-pct">{usage.cap ? `${usage.pct}%` : "—"}</span>
          </span>
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
            title={p.busy ? "正在执行…" : "发送"}
            disabled={!canSend}
            onClick={p.onSubmit}
          >↑</button>
        </div>
      </div>
      <div className="foot-tip">为测试人员而生 · 用例生成 / 脚本编写 / 失败分析</div>
    </div>
  );
}
