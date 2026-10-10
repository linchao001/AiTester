import type { KbDraft } from "../../api/client";
import { escapeHtml, fmtSize, fmtTime, kbBytes, kbDisp } from "./utils";

/** 草案卡写盘状态机：pending 可确认/取消，writing 锁按钮，终态 done/canceled/failed。 */
export type KbDraftState = "pending" | "writing" | "done" | "canceled" | "failed";

export interface KbDraftCardProps {
  draft: KbDraft;
  state: KbDraftState;
  writtenAt?: number;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Reme save_to_knowledge 草案卡：展示 title/bucket + 正文预览；确认走 /api/kb/save。 */
export default function KbDraftCard({ draft, state, writtenAt, onConfirm, onCancel }: KbDraftCardProps) {
  const preview = draft.content.split(/\r?\n/).slice(0, 14)
    .map((l) => `<span class="add">+ ${escapeHtml(l)}</span>`).join("\n");

  const stateLabel =
    state === "done" ? `已写入 · ${fmtTime(writtenAt ?? 0)}`
      : state === "canceled" ? "已取消"
        : state === "failed" ? "写入失败"
          : state === "writing" ? "写入中…"
            : "未写入";

  const where = draft.path
    ? kbDisp(draft.path)
    : `${draft.bucket} · ${draft.title}`;

  return (
    <div className={`kb-draft${state === "done" ? " done" : ""}`}>
      <div className="d-head">
        <span className="d-op">{draft.op === "create" ? "新建" : "修改"}</span>
        <span>待写入草案（Reme）</span>
        <div className="spacer"></div>
        <span className="d-state">{stateLabel}</span>
      </div>
      <div className="d-path">{where}</div>
      <div className="d-sum">
        {draft.summary || `${draft.title} → ${draft.bucket}`}
      </div>
      <pre className="d-diff" dangerouslySetInnerHTML={{ __html: preview }} />
      <div className="d-acts">
        {state === "pending" && (
          <>
            <button className="ws-btn main" onClick={onConfirm}>✓ 确认写入知识库</button>
            <button className="mini-btn" onClick={onCancel}>取消草案</button>
          </>
        )}
        <div className="spacer"></div>
        <span className="d-state">{fmtSize(kbBytes(draft.content))}</span>
      </div>
    </div>
  );
}
