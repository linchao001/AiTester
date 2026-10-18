import type { KbDraft } from "../../api/client";
import { escapeHtml, fmtSize, fmtTime, kbBytes, kbDisp } from "./utils";

/** 草案卡落盘状态机：pending 可确认/取消，writing 锁按钮，终态 done/canceled/failed。 */
export type KbDraftState = "pending" | "writing" | "done" | "canceled" | "failed";

export interface KbDraftCardProps {
  draft: KbDraft;
  state: KbDraftState;
  writtenAt?: number;
  onConfirm: () => void;
  onCancel: () => void;
}

/** 草案动作类型 → 界面标签（徽标 / 确认按钮 / 终态动词）。 */
const KIND_LABEL: Record<KbDraft["kind"], { badge: string; confirm: string; done: string }> = {
  save: { badge: "", confirm: "✓ 确认写入知识库", done: "已写入" },
  delete: { badge: "删除", confirm: "✓ 确认删除文件", done: "已删除" },
  edit: { badge: "改正文", confirm: "✓ 确认修改正文", done: "已修改" },
  frontmatter: { badge: "删元数据", confirm: "✓ 确认删元数据字段", done: "已更新元数据" },
};

/** 助手草案卡：按 kind 分支渲染写入(save)/硬删(delete)/改正文(edit)/删元数据(frontmatter)。
 *  确认动作由父层按 kind 调对应 /api/kb/* 端点；卡片本身即确认入口，不再弹二次确认。 */
export default function KbDraftCard({ draft, state, writtenAt, onConfirm, onCancel }: KbDraftCardProps) {
  const kind = draft.kind ?? "save";
  const label = KIND_LABEL[kind];

  // 徽标：save 沿用「新建/修改」，维护类用动作名。
  const badge = kind === "save" ? (draft.op === "create" ? "新建" : "修改") : label.badge;

  // 落点展示：save 优先桶+标题，维护类展示目标文件路径。
  const where = kind === "save"
    ? (draft.path ? kbDisp(draft.path) : `${draft.bucket} · ${draft.title}`)
    : kbDisp(draft.path);

  // 正文/差异预览：按 kind 组装 HTML 行。
  let diffHtml = "";
  if (kind === "save") {
    diffHtml = draft.content.split(/\r?\n/).slice(0, 14)
      .map((l) => `<span class="add">+ ${escapeHtml(l)}</span>`).join("\n");
  } else if (kind === "edit") {
    const lines = [`<span class="del">- ${escapeHtml(draft.old)}</span>`];
    lines.push(draft.new === ""
      ? "<span>（删除命中文本，无替换）</span>"
      : `<span class="add">+ ${escapeHtml(draft.new)}</span>`);
    diffHtml = lines.join("\n");
  } else if (kind === "frontmatter") {
    diffHtml = `<span>删除字段：${(draft.keys || []).map(escapeHtml).join("、") || "（无）"}</span>`;
  } else {
    // delete：不展正文，给硬删风险说明。
    diffHtml = "<span class=\"del\">⚠ 将从磁盘永久删除该文件，并清理其在检索索引中的投影。</span>";
  }

  const stateLabel =
    state === "done" ? `${label.done} · ${fmtTime(writtenAt ?? 0)}`
      : state === "canceled" ? "已取消"
        : state === "failed" ? (kind === "delete" ? "删除失败" : "写入失败")
          : state === "writing" ? (kind === "delete" ? "删除中…" : "写入中…")
            : "未写入";

  // 体积脚注仅对带正文的 save/edit 有意义；其余留空。
  const sizeText = kind === "save" ? fmtSize(kbBytes(draft.content))
    : kind === "edit" ? fmtSize(kbBytes(draft.new)) : "";

  return (
    <div className={`kb-draft${state === "done" ? " done" : ""}`}>
      <div className="d-head">
        <span className="d-op">{badge}</span>
        <span>{kind === "delete" ? "待删除草案" : kind === "edit" ? "待修改草案（正文）"
          : kind === "frontmatter" ? "待修改草案（元数据）" : "待写入草案（Reme）"}</span>
        <div className="spacer"></div>
        <span className="d-state">{stateLabel}</span>
      </div>
      <div className="d-path">{where}</div>
      <div className="d-sum">
        {draft.summary || (kind === "save" ? `${draft.title} → ${draft.bucket}` : draft.path)}
      </div>
      <pre className="d-diff" dangerouslySetInnerHTML={{ __html: diffHtml }} />
      <div className="d-acts">
        {state === "pending" && (
          <>
            <button className="ws-btn main" onClick={onConfirm}>{label.confirm}</button>
            <button className="mini-btn" onClick={onCancel}>取消草案</button>
          </>
        )}
        <div className="spacer"></div>
        <span className="d-state">{sizeText}</span>
      </div>
    </div>
  );
}
