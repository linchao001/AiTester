import { useState } from "react";
import type { AuthDecision, PendingCallInfo } from "../../api/client";

interface Props {
  ask: PendingCallInfo;
  /** 已答的痕迹态：null 表示还没答。六键由 decided 项自带（R14），刷新后照样读得出内容。 */
  decided: AuthDecision | null;
  /** 队列里排在后面的未答项：只给「排队中」，不给按钮——逐条批是本片接受的语义（P5/P7）。 */
  queued: boolean;
  busy: boolean;
  onDecide: (decision: AuthDecision, remember: boolean) => void;
}

/** 授权卡（spec「事件与前端折叠」）：复用草案卡那族形态内嵌在气泡里，不做遮罩弹窗。
 *  命令与路径一律全文展示，不套 DETAIL_MAX——批准前看不全就等于骗用户点确认。 */
export default function AuthCard(p: Props) {
  const [remember, setRemember] = useState(false);
  // 态标只在头部一处（草案卡同款收口）：pending「⏳ 等待授权」/ queued「排队中」/ 已答 ✓✕
  const stateLabel = p.decided === "approve" ? "✓ 已批准"
    : p.decided === "reject" ? "✕ 已拒绝"
      : p.queued ? "排队中"
        : "⏳ 等待授权";
  const locked = p.decided !== null || p.queued || p.busy;

  return (
    // 批准后的痕迹沿用草案卡 done 的绿（同一个「事情成了」的视觉口径）；
    // 拒绝保持中性底色——绿色的「已拒绝」是反话，不新造色值
    <div className={`kb-draft${p.decided === "approve" ? " done" : ""}`}>
      <div className="d-head">
        <span className="d-op">{p.ask.command ? "执行命令" : "写文件"}</span>
        {/* 命令类的 action 与类标是同一串「执行命令」（auth_rules 口径），并显会把头部念成复读 */}
        {p.ask.command ? null : <span>{p.ask.action}</span>}
        <div className="spacer" />
        <span className="d-state">{stateLabel}</span>
      </div>
      {/* 目标路径给写类调用，工作目录给命令类：命令类的 target 就是那个 cwd，两处都渲染等于把同一路径摆两遍 */}
      {p.ask.command || !p.ask.target ? null : <div className="d-path">{p.ask.target}</div>}
      {p.ask.command
        ? <pre className="d-diff">{p.ask.command}{p.ask.cwd ? `\n工作目录：${p.ask.cwd}` : ""}</pre>
        : <div className="d-sum">批准后立即执行，拒绝则跳过这一步并让模型继续作答。</div>}
      {/* 派发来源（R3）：子智能体发起的授权在这里点名，用户知道批的是谁的调用 */}
      {p.ask.subagent ? <div className="d-sum">来自子智能体「{p.ask.subagent.title}」</div> : null}
      <div className="d-acts">
        {p.decided === null && !p.queued && (
          <>
            <button className="ws-btn main" disabled={locked}
              title={p.busy ? "续跑中，请稍候" : "批准后立即执行这一步"}
              onClick={() => p.onDecide("approve", remember)}>✓ 批准</button>
            <button className="mini-btn" disabled={locked}
              title="拒绝这一步，模型收到拒绝后继续作答"
              onClick={() => p.onDecide("reject", remember)}>✕ 拒绝</button>
            <label className="auth-rem" title="只记「这个工具 + 这个目标」，换路径仍会问">
              <input type="checkbox" checked={remember} disabled={locked}
                onChange={(e) => setRemember(e.target.checked)} />
              本次会话内同路径不再询问
            </label>
          </>
        )}
        <div className="spacer" />
        <span className="d-state">{p.ask.tool}</span>
      </div>
    </div>
  );
}
