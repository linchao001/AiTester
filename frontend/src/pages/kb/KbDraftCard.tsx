import type { KbDraft } from "../../api/client";
import { escapeHtml, fmtSize, fmtTime, kbBytes, kbDiffHtml, kbDisp } from "./utils";

/** 草案卡写盘状态机（Task 10 固定契约）：pending 可确认/取消，writing 锁按钮，终态 done/canceled/failed。 */
export type KbDraftState = "pending" | "writing" | "done" | "canceled" | "failed";

/** brief Step 1 props 接口逐字实现。 */
export interface KbDraftCardProps {
  draft: KbDraft;
  state: KbDraftState;
  writtenAt?: number;
  onConfirm: () => void;
  onCancel: () => void;
}

/** 原型 kbDraftCard（:2572-2596）的 React 转写：innerHTML 拼装 → JSX + 受控状态。
    偏差（见 task-10-report）：
    - 按钮仅 pending 态渲染（原型完成后 remove、失败复挂）；writing 用禁用表达「写入中…」；
    - 原型 .d-state 两处（头部态标 + 尾部体积）在 done/cancel 时被一并覆写，React 侧体积 span 保留，
      态标只在头部呈现，语义与原型的「已写入 · 时间 / 已取消 / 写入失败」一致。 */
export default function KbDraftCard({ draft, state, writtenAt, onConfirm, onCancel }: KbDraftCardProps) {
  // 原型 :2575-2576 —— create 显 content 前 14 行「+ 」（.add）；modify 走行级 diff
  const diffHtml = draft.op === "create"
    ? draft.content.split(/\r?\n/).slice(0, 14)
      .map((l) => `<span class="add">+ ${escapeHtml(l)}</span>`).join("\n")
    : kbDiffHtml(draft.base ?? "", draft.content);

  // 右态标（原型头部 .d-state）：pending「未写入」/ writing「写入中…」/ done「已写入 · 时间」/ canceled / failed
  const stateLabel =
    state === "done" ? `已写入 · ${fmtTime(writtenAt ?? 0)}`
      : state === "canceled" ? "已取消"
        : state === "failed" ? "写入失败"
          : state === "writing" ? "写入中…"
            : "未写入";

  return (
    <div className={`kb-draft${state === "done" ? " done" : ""}`}>
      <div className="d-head">
        <span className="d-op">{draft.op === "create" ? "新建" : "修改"}</span>
        <span>待写入草案</span>
        <div className="spacer"></div>
        <span className="d-state">{stateLabel}</span>
      </div>
      <div className="d-path">{kbDisp(draft.path)}</div>
      {/* create 草案缺 summary 时的兜底文案（brief Step 1，逐字） */}
      <div className="d-sum">{draft.summary || "新建文件，不改动任何原始笔记"}</div>
      {/* diff 行内 span 已由 escapeHtml/kbDiffHtml 收口，仅 .add/.del 结构 */}
      <pre className="d-diff" dangerouslySetInnerHTML={{ __html: diffHtml }} />
      <div className="d-acts">
        {state === "pending" && (
          <>
            <button className="ws-btn main" onClick={onConfirm}>✓ 确认写入磁盘</button>
            <button className="mini-btn" onClick={onCancel}>取消草案</button>
          </>
        )}
        <div className="spacer"></div>
        <span className="d-state">{fmtSize(kbBytes(draft.content))}</span>
      </div>
    </div>
  );
}
