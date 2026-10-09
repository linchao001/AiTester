import { useEffect, useState } from "react";
import { fmtK } from "../../utils";
import type { ContextSnapshot, PermMode } from "../../api/client";
import { PERM_MODES, permMeta } from "./utils";

interface Props {
  input: string;
  busy: boolean;
  usage: ContextSnapshot | null;  // 后端每回合的读数快照：界面只呈现，不折算 token（CM-5）
  projectName: string;     // 只读橙 chip 的文案源（项目维度，第 2 片接入）；空串代表项目还没落地，chip 不渲染
  permMode: PermMode;      // 当前档位：chip 的图标、文案与选中态唯一来源
  permLocked: boolean;     // 有待批就置灰（走查 15）：档位与挂起中那轮的判定必须一致
  onPermMode: (m: PermMode) => void;
  sendBlock: string;       // 非空即「现在还不能发」的原因，由 ChatPage 算（它是 agentId/projectId 的唯一持有者）：折进 canSend 并直接进 title
  inputRef: { current: HTMLTextAreaElement | null };  // 供 chip 点击后聚焦 + 输入框自增高（ChatPage 持有）
  stopRequested: boolean;    // ■ 已按下、终态未到：停止钮置灰防二次点击
  onStop: () => void;
  onInput: (v: string) => void;
  onSubmit: () => void;
}

/** 原型 :611-624 逐字对齐：bar 内只有 上下文 meter + 橙项目 chip + 蓝 perm chip + spacer + 发送。
 *  原型橙 chip（:617）是「📁 项目」只读展示，路径不进 UI（第 2 片偏离 5）；
 *  模型 chip 在 chat-header（:598），不在 composer 内，勿在此重复。 */
export default function Composer(p: Props) {
  const [permOpen, setPermOpen] = useState(false);
  useEffect(() => {
    if (!permOpen) return;
    const close = () => setPermOpen(false);
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [permOpen]);

  const u = p.usage;
  const pct = u && u.window > 0 ? Math.min(100, Math.round((u.peak_occupancy / u.window) * 100)) : 0;
  // 估算读数除了 tooltip 里的「估算」二字，界面上也必须看得出来（R-C1：不许伪装成真值）。
  // 前缀、类名、tooltip 三处共用这一个判据：写两遍比较式就会分叉成「有 ≈ 却没有半透明条」。
  const est = !!u && u.occupancy_source !== "actual";
  const cls = `ctx-meter${pct >= 90 ? " hot" : pct >= 70 ? " warn" : ""}${est ? " est" : ""}`;
  const trunc = u && u.truncated?.length
    ? ` · 本回合截断 ${u.truncated.length} 处（`
      + u.truncated.map((t) => `${t.tool} 原 ${fmtK(t.original)}→留 ${fmtK(t.kept)}（省 ${fmtK(t.dropped)}）`).join("；")
      + "）"
    : "";
  const tip = !u || u.window <= 0
    ? "没有后端上下文读数：可能是这条会话的行早于读数落盘、模型未配置最大上下文，或这一回合没产生调用"
    : `上下文占用 ${fmtK(u.peak_occupancy)} / ${fmtK(u.window)} tokens`
      + `（${est ? "估算" : "真值"}·本回合 ${u.rounds} 次调用`
      + `·累计输入 ${fmtK(u.spent_input)}、输出 ${fmtK(u.spent_output)}）${trunc}`
      + (u.error ? `·读数缺口：${u.error}` : "")
      + (pct >= 90 ? "：已接近上限，建议新建会话" : "");
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
            <i className="cm-bar"><b style={{ width: `${pct}%` }} /></i>
            <span className="cm-pct">{u && u.window > 0 ? `${est ? "≈" : ""}${pct}%` : "—"}</span>
          </span>
          {/* 只读展示：路径不进 UI（第 2 片偏离 5）。必须压掉 .c-chip 的 cursor:pointer，
              否则纯装饰 span 会伪装成可点控件——第 1 片「0 个死按钮」的同一条判据。
              项目名还没落地时整只 chip 不渲染：光杆「📁 」是第二种伪装成有内容的空壳 */}
          {p.projectName ? (
            <span className="c-chip orange" style={{ cursor: "default" }} title="智能体在此目录读写文件">
              📁 {p.projectName}
            </span>
          ) : null}
          {/* 原型 :618 的三档弹层：chip 必须是 span——.pop 是 div，塞进 button 是非法内容模型，
              浏览器会把弹层挪出锚点；键盘可达性用 role/tabIndex 补齐（登记为偏离）。
              busy 期间不禁 chip：挂起与在途是两种状态，只在有待批时置灰。 */}
          <span
            className="c-chip blue perm"
            role="button"
            tabIndex={p.permLocked ? -1 : 0}
            aria-disabled={p.permLocked}
            title={p.permLocked ? "还有回答在等你批准，先处理完再切档位" : "权限模式 · 点击选择"}
            onClick={() => { if (!p.permLocked) setPermOpen((v) => !v); }}
            onKeyDown={(e) => {
              if (p.permLocked) return;
              if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPermOpen((v) => !v); }
              if (e.key === "Escape") setPermOpen(false);
            }}
          >
            {permMeta(p.permMode).icon} {permMeta(p.permMode).label} ▾
            <div className={`pop up${permOpen ? " show" : ""}`} onClick={(e) => e.stopPropagation()}>
              <div className="p-title">权限模式</div>
              {PERM_MODES.map((m) => (
                <div className={`opt${m.id === p.permMode ? " sel" : ""}`} key={m.id}
                  title={m.desc}
                  onClick={() => { setPermOpen(false); p.onPermMode(m.id); }}>
                  <span>{m.icon} {m.label}</span>
                  {m.id === p.permMode ? <span className="ck">✓</span> : null}
                </div>
              ))}
            </div>
          </span>
          <div className="spacer" />
          {p.busy ? (
            /* busy 时钮位换成停止：带文字不裸图标（UI 约定），点下就置灰防二次点击，
               终态到达后 busy 落真 → 变回 ↑（验收清单「0 个死按钮」那条） */
            <button className="btn-send stop" disabled={p.stopRequested}
              title={p.stopRequested ? "停止中…" : "停止生成"} onClick={p.onStop}>■ 停止</button>
          ) : (
            <button
              className={`btn-send${canSend ? " on" : ""}`}
              title={p.sendBlock || "发送"}
              disabled={!canSend}
              onClick={p.onSubmit}
            >↑</button>
          )}
        </div>
      </div>
      <div className="foot-tip">为测试人员而生 · 用例生成 / 脚本编写 / 失败分析</div>
    </div>
  );
}
