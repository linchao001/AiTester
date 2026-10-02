import type { ReactNode } from "react";
import type { KbDocState } from "../KbPage";
import { KB_FM_KEYS, fmtSize, fmtTime, kbBytes, kbDirOf, kbIsMd, mdRender, splitFm } from "./utils";

/** brief Step 1 props 接口逐字实现；root/headLeading/headTrailing 为转写偏差，见 task-9-report。
    - root：面包屑父路径缺省回退（原型读全局 KB.root :2440，此处显式传入保持纯组件）
    - headLeading/headTrailing：Task 8 的「📁 目录」「💬 助手」恢复钮在原型 kb-head 内
      （:709/:718），head 整段迁入本组件后经插槽回传，避免把 sideHidden/chatHidden 状态耦合进来 */
export interface KbEditorPaneProps {
  doc: KbDocState | null;
  dirty: boolean;
  root: string;
  headLeading?: ReactNode;
  headTrailing?: ReactNode;
  onMode: (m: "view" | "edit") => void;   // edit→view 时 KbPage 先回收 textarea 值
  onEditContent: (v: string) => void;
  onSave: () => void;
  onDiscard: () => void;
  onReload: () => void;
  onNewNote: () => void;
}

/** 原型中部列 :697-735 + kbOpen/kbPaint/kbStat/kbSetMode（:2427-2476）的 React 转写。
    原型用 hidden/classList 开关元素，React 等价：条件渲染 + className 拼接。 */
export default function KbEditorPane({
  doc,
  dirty,
  root,
  headLeading,
  headTrailing,
  onMode,
  onEditContent,
  onSave,
  onDiscard,
  onReload,
  onNewNote,
}: KbEditorPaneProps) {
  // 原型 :2442 ——「知识库里的脚本/配置只展示原文，避免误改可执行文件」
  const ro = doc !== null && !kbIsMd(doc.ext);
  const parts = doc ? doc.rel.split("/") : [];
  const parent = doc ? kbDirOf(doc.rel) : "";

  // 原型 kbPaint :2450-2455 —— 元信息 chips 基于实时 content（编辑中 frontmatter 改动即时反映）
  const { fm, body } = splitFm(doc ? doc.content : "");
  const chips: ReactNode[] = fm
    ? KB_FM_KEYS.filter((k) => fm[k]).map((k) => (
        <span className="kv" key={k}><b>{k}</b> {String(fm[k]).slice(0, 90)}</span>
      ))
    : [<span className="kv" key="_none">无 frontmatter</span>];
  if (doc && ro) chips.push(<span className="kv" key="_ro">只读 · 仅 Markdown 可编辑</span>);

  // 原型 kbStat :2466-2470 —— rel · N 行 · 体积 · 磁盘时间
  const lines = doc ? doc.content.split(/\r?\n/).length : 0;

  return (
    <section className="kb-main">
      {/* 原型 :708-719 kb-head：sideShow → 面包屑 → spacer → tabs → 重载 → 新建笔记 → chatShow */}
      <div className="kb-head">
        {headLeading}
        <div className="kb-crumbs">
          {doc ? (
            <>
              <span>{parts[parts.length - 1]}</span>
              <span className="p">{(parent || root) + (parent ? "/" : "")}</span>
            </>
          ) : (
            "未打开文件"
          )}
        </div>
        <div className="spacer"></div>
        {/* 原型 kbTabs：hidden 属性 + .show 类共同控制；React 条件渲染即等价 */}
        {doc && (
          <span className="e-tabs show">
            <button className={`e-tab${doc.mode === "view" ? " on" : ""}`} onClick={() => onMode("view")}>
              {ro ? "原文" : "预览"}
            </button>
            {/* 原型 :2444 —— 非 md 隐藏编辑 tab */}
            {!ro && (
              <button className={`e-tab${doc.mode === "edit" ? " on" : ""}`} onClick={() => onMode("edit")}>
                编辑
              </button>
            )}
          </span>
        )}
        {doc && <button className="mini-btn" title="从磁盘重新载入" onClick={onReload}>↻ 重载</button>}
        <button className="mini-btn" onClick={onNewNote}>＋ 新建笔记</button>
        {headTrailing}
      </div>

      {/* 原型 :720 kb-meta + :2455 体积/磁盘时间尾 chip（content 实时体积，对齐原型） */}
      {doc && (
        <div className="kb-meta">
          {chips}
          <span className="kv"><b>体积</b> {fmtSize(kbBytes(doc.content))} · {fmtTime(doc.mtime)}</span>
        </div>
      )}

      {/* 原型 :721-726 kb-doc 三态互斥：kbPaint :2457-2461 */}
      <div className="kb-doc">
        {!doc ? (
          <div className="kb-empty"><span className="big">📚</span><span>左侧选择知识库文件；中间渲染或编辑，右侧可让助手帮你改。</span></div>
        ) : doc.mode === "edit" ? (
          <textarea
            className="kb-area show"
            spellCheck={false}
            value={doc.content}
            onChange={(e) => onEditContent(e.target.value)}
          />
        ) : !ro ? (
          <div className="md-preview show" dangerouslySetInnerHTML={{ __html: mdRender(body) }} />
        ) : (
          <pre className="kb-code show">{doc.content}</pre>
        )}
      </div>

      {/* 原型 :727-733 kb-foot —— 保存/放弃按钮仅 edit 态显示（:2463） */}
      {doc && (
        <div className="kb-foot show">
          {dirty && <span className="unsaved">● 未保存</span>}
          <span className="stat">{`${doc.rel} · ${lines} 行 · ${fmtSize(kbBytes(doc.content))} · 磁盘时间 ${fmtTime(doc.mtime)}`}</span>
          <div className="spacer"></div>
          {doc.mode === "edit" && <button className="mini-btn" onClick={onDiscard}>放弃修改</button>}
          {doc.mode === "edit" && <button className="ws-btn main" onClick={onSave}>💾 保存并写入磁盘</button>}
        </div>
      )}
    </section>
  );
}
