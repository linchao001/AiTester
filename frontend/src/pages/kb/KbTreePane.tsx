import { Fragment, type ReactElement } from "react";
import type { KbBrowseItem, KbSearchHit } from "../../api/client";
import { fmtSize, wsIcon } from "./utils";

/** brief Step 2 逐字对齐：树数据（kids 缓存/搜索态）由 KbPage 持有，本组件纯渲染。
    渲染规则照原型 kbRenderTree / kbNodes（index.html:2388-2424）。 */
export interface KbTreePaneProps {
  root: string;
  kids: Record<string, KbBrowseItem[]>;        // 目录 rel（''=根）→ 已加载子项
  expanded: Record<string, boolean>;
  activeRel: string;
  total: number | null;                        // 计数 num
  searchHits: KbSearchHit[] | null;            // 非 null = 搜索结果态
  onToggleDir: (rel: string) => void;
  onOpenFile: (rel: string) => void;
}

export default function KbTreePane({
  root,
  kids,
  expanded,
  activeRel,
  total,
  searchHits,
  onToggleDir,
  onOpenFile,
}: KbTreePaneProps) {
  /** 原型 :2411-2413 —— 行 = ▸/▾ + 📁/wsIcon + 文件名 +（文件）体积；目录行无体积列。 */
  const row = (it: { name: string; rel: string; dir: boolean; size: number }, depth: number | null, active: boolean, onClick: () => void): ReactElement => (
    <div
      className={`ws-node${active ? " active" : ""}`}
      // 原型仅树态按深度缩进（8 + depth*13px）；搜索态不设 paddingLeft
      style={depth === null ? undefined : { paddingLeft: `${8 + depth * 13}px` }}
      onClick={onClick}
    >
      <span className="arr">{it.dir && depth !== null ? (expanded[it.rel] ? "▾" : "▸") : ""}</span>
      <span>{it.dir ? "📁" : wsIcon(it.name)}</span>
      <span className="lbl">{it.name}</span>
      {!it.dir && <span className="sz">{fmtSize(it.size)}</span>}
    </div>
  );

  /** 原型 kbNodes :2405-2424 —— 深度优先按 expanded 展开；kids 缺失视为未加载（懒加载由 KbPage 触发）。 */
  const nodes = (rel: string, depth: number): ReactElement[] =>
    (kids[rel] || []).map((it) => (
      <Fragment key={it.rel}>
        {row(it, depth, it.rel === activeRel, () => (it.dir ? onToggleDir(it.rel) : onOpenFile(it.rel)))}
        {it.dir && expanded[it.rel] && nodes(it.rel, depth + 1)}
      </Fragment>
    ));

  /** 原型 :2390-2401 —— 搜索态列 hits；目录项点击无效（对齐原型：仅文件可开）。 */
  const hitsView = (searchHits ?? []).map((h) => (
    <Fragment key={h.rel}>
      {row(h, null, h.rel === activeRel, () => { if (!h.dir) onOpenFile(h.rel); })}
    </Fragment>
  ));

  return (
    <>
      <div className="kb-tree">
        {searchHits !== null ? (
          <>
            <div className="hd">{searchHits.length ? `搜索命中 ${searchHits.length} 条` : "没有命中的文件"}</div>
            {hitsView}
          </>
        ) : (
          <>
            {nodes("", 0)}
            {/* 原型空根渲染空白框；React 侧补一行可读提示（total 消费点，样式复用 .hd） */}
            {total === 0 && <div className="hd">知识库暂无文件</div>}
          </>
        )}
      </div>
      {/* 原型 :703 + kbLoadDir :2381 —— 底部 root 路径脚注 */}
      <div className="kb-root">{root ? `KB · ${root}` : ""}</div>
    </>
  );
}
