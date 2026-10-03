import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ApiError, wsPutFile, wsReadFile, wsTree,
  type Project, type WsFileResponse, type WsItem,
} from "../../api/client";
import { bindDragBar } from "../../components/dragBar";
import { fmtSize, fmtTime, kbBytes, mdRender, wsIcon } from "../kb/utils";

/** 中栏文档态（形态照 KbPage.KbDocState；工作区无只读态，mode 只有预览/编辑）。 */
interface WsDoc {
  rel: string;
  name: string;
  ext: string;
  content: string;
  disk: string;
  mtime: number;
  mode: "preview" | "edit";
}

/** 拖拽夹持：目录树 [180,560]（KB 已裁定的放宽口径）；聊天↔工作区 [260, 内宽-620]（保住聊天区 620）。 */
const TW_MIN = 180, TW_MAX = 560, WS_MIN = 260, CHAT_KEEP = 620;

export interface WorkspacePaneProps {
  project: Project;
  collapsed: boolean;
  refreshSeq: number;
  onCollapse: () => void;
  onToast: (msg: string) => void;
  onDirtyChange: (dirty: boolean) => void;
}

/** 原型 aside#workspace :631-671 + 工作区 JS :1480-1677 的 React 转写。
    整栏 = resizer + aside fragment；树/打开文件/拖宽都是视图态，key={projectId} 换项目即重挂归零。 */
export default function WorkspacePane({
  project, collapsed, refreshSeq, onCollapse, onToast, onDirtyChange,
}: WorkspacePaneProps) {
  const wsRef = useRef<HTMLElement>(null);
  const wsBodyRef = useRef<HTMLDivElement>(null);
  const wsSideRef = useRef<HTMLDivElement>(null);
  const chatResizerRef = useRef<HTMLDivElement>(null);
  const treeResizerRef = useRef<HTMLDivElement>(null);

  const [treeHidden, setTreeHidden] = useState(false);
  const [kids, setKids] = useState<Record<string, WsItem[]>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ "": true });
  const [treeErr, setTreeErr] = useState<string | null>(null);
  const kidsRef = useRef(kids);
  const expandedRef = useRef(expanded);
  kidsRef.current = kids;
  expandedRef.current = expanded;

  const [doc, setDoc] = useState<WsDoc | null>(null);
  const docRef = useRef(doc);
  docRef.current = doc;
  const rootSeq = useRef(0);   // 整树重拉的「最新一次」序号：迟到的成败都不得盖过更新的一轮
  const openSeq = useRef(0);   // 打开文件的「最新一次」序号

  const dirty = doc !== null && doc.content !== doc.disk;

  /** 整树重拉：根 + 全部已展开目录（手动 ↻ 与发送后静默刷新共用一条路径）。 */
  const reloadTree = useCallback(async (): Promise<boolean> => {
    const seq = ++rootSeq.current;
    let rootItems: WsItem[];
    try {
      rootItems = (await wsTree(project.id, "")).items;
    } catch (err) {
      if (seq === rootSeq.current) setTreeErr(err instanceof Error ? err.message : String(err));
      return false;
    }
    if (seq !== rootSeq.current) return false;
    const wanted = Object.keys(expandedRef.current).filter((d) => d !== "");
    const nextKids: Record<string, WsItem[]> = { "": rootItems };
    const nextExpanded: Record<string, boolean> = { "": true };
    for (const rel of wanted) {
      try {
        const sub = await wsTree(project.id, rel);
        if (seq !== rootSeq.current) return false;
        nextKids[rel] = sub.items;
        nextExpanded[rel] = true;
      } catch {
        // 目录在刷新窗口内被删/移走：折叠丢弃（刷新是收敛，不弹错）
      }
    }
    if (seq !== rootSeq.current) return false;
    // 拉取期间新展开的目录并入结果：刷新不把用户刚展开的树收回去
    for (const rel of Object.keys(expandedRef.current)) {
      if (nextExpanded[rel]) continue;
      nextExpanded[rel] = true;
      const cached = kidsRef.current[rel];
      if (cached) nextKids[rel] = cached;
    }
    kidsRef.current = nextKids;
    expandedRef.current = nextExpanded;
    setKids(nextKids);
    setExpanded(nextExpanded);
    setTreeErr(null);
    return true;
  }, [project.id]);

  // 挂载首拉（key={projectId} 重挂：换项目全量归零）
  useEffect(() => { void reloadTree(); }, [reloadTree]);

  // 父 bump refreshSeq（发送成功）→ 静默重拉；挂载首帧的 seq 不重复拉
  const seenSeq = useRef(refreshSeq);
  useEffect(() => {
    if (refreshSeq === seenSeq.current) return;
    seenSeq.current = refreshSeq;
    void reloadTree();
  }, [refreshSeq, reloadTree]);

  // 双向拖拽（原型 dragBar :1652-1669；夹持按 spec 裁定）
  useEffect(() => {
    const el = chatResizerRef.current;
    if (!el) return;
    return bindDragBar(el, (ev) => {
      const max = Math.max(WS_MIN, window.innerWidth - CHAT_KEEP);
      const w = Math.min(Math.max(window.innerWidth - ev.clientX, WS_MIN), max);
      wsRef.current?.style.setProperty("--w", `${w}px`);
    });
  }, []);
  useEffect(() => {
    const el = treeResizerRef.current;
    if (!el) return;
    return bindDragBar(el, (ev) => {
      const left = wsSideRef.current?.getBoundingClientRect().left ?? 0;
      const w = Math.min(Math.max(ev.clientX - left, TW_MIN), TW_MAX);
      wsBodyRef.current?.style.setProperty("--tw", `${w}px`);
    });
  }, []);

  /** 原型 kbLoadDir :2381-2385 同款：kids 缺失才拉（懒加载=首次展开触发）。 */
  const toggleDir = useCallback((rel: string) => {
    if (expandedRef.current[rel]) {
      const next = { ...expandedRef.current };
      delete next[rel];
      expandedRef.current = next;
      setExpanded(next);
      return;
    }
    expandedRef.current = { ...expandedRef.current, [rel]: true };
    setExpanded(expandedRef.current);
    if (kidsRef.current[rel]) return;
    wsTree(project.id, rel)
      .then((j) => { kidsRef.current = { ...kidsRef.current, [rel]: j.items }; setKids(kidsRef.current); })
      .catch((err) => onToast(err instanceof Error ? err.message : String(err)));
  }, [project.id, onToast]);

  /** 打开文件（force=true 为 409 后的强载，跳过脏确认）。 */
  const loadDoc = useCallback(async (rel: string, force: boolean): Promise<boolean> => {
    const cur = docRef.current;
    if (!force && cur && cur.content !== cur.disk) {
      if (!window.confirm("当前文件有未保存的修改，放弃并切换？")) return false;
    }
    const seq = ++openSeq.current;
    let j: WsFileResponse;
    try {
      j = await wsReadFile(project.id, rel);
    } catch (err) {
      if (seq === openSeq.current) onToast(err instanceof Error ? err.message : String(err));
      return false;
    }
    if (seq !== openSeq.current) return false;
    // 原型 wsOpenFile :1546 —— 清拖拽写的 inline --w，让 .wide 的 CSS 宽度生效
    wsRef.current?.style.removeProperty("--w");
    const isMd = j.ext === ".md" || j.ext === ".markdown";
    const next: WsDoc = {
      rel: j.rel, name: j.name, ext: j.ext,
      content: j.content, disk: j.content, mtime: j.mtime,
      mode: isMd ? "preview" : "edit",     // 原型 :1552-1554：md 默认预览，非 md 直接编辑
    };
    docRef.current = next;
    setDoc(next);
    return true;
  }, [project.id, onToast]);

  const openDoc = useCallback((rel: string) => { void loadDoc(rel, false); }, [loadDoc]);

  /** 原型 wsCloseFile :1566-1572 + 脏守卫：放弃修改需确认。 */
  const closeDoc = useCallback(() => {
    const cur = docRef.current;
    if (!cur) return;
    if (cur.content !== cur.disk && !window.confirm("当前文件有未保存的修改，放弃并关闭？")) return;
    docRef.current = null;
    setDoc(null);
    wsRef.current?.style.removeProperty("--w");
  }, []);

  /** 原型 wsSave :1574-1577 的落盘版：PUT + mtime 乐观锁；409/404 分支见 spec 错误表。 */
  const saveDoc = useCallback(async () => {
    const cur = docRef.current;
    if (!cur) return;
    if (cur.content === cur.disk) { onToast("没有需要保存的修改"); return; }
    try {
      const j = await wsPutFile(project.id, cur.rel, cur.content, cur.mtime);
      const now = docRef.current;
      if (now && now.rel === cur.rel) {
        // 保存期间可能又敲了字：disk 回填保存时的快照，content 保持当前输入
        const next = { ...now, disk: cur.content, mtime: j.mtime };
        docRef.current = next;
        setDoc(next);
      }
      onToast(`已保存 ${j.rel}`);
      void reloadTree();                      // 体积/mtime 变了，静默收敛树
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // 语义与 KB 页统一（KbPage.tsx kbWrite 同款）：冲突不覆盖不合并，强载磁盘最新
        onToast("文件在别处被改过，已重新载入磁盘最新内容（未保存的修改已丢弃）");
        void loadDoc(cur.rel, true);
        return;
      }
      onToast(err instanceof ApiError ? err.message : String(err));
      if (err instanceof ApiError && err.status === 404) void reloadTree();  // 外部被删：树收敛，内容留着
    }
  }, [loadDoc, onToast, project.id, reloadTree]);

  const setMode = useCallback((m: "preview" | "edit") => {
    const cur = docRef.current;
    if (!cur) return;
    const next = { ...cur, mode: m };
    docRef.current = next;
    setDoc(next);
  }, []);

  const onEditContent = useCallback((v: string) => {
    const cur = docRef.current;
    if (!cur) return;
    const next = { ...cur, content: v };
    docRef.current = next;
    setDoc(next);
  }, []);

  // 脏状态上报父组件（ref 消费）：卸载时回落，别让父带着过期的脏拦人
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const rows = (rel: string, depth: number): ReactNode[] =>
    (kids[rel] || []).map((it) => (
      <Fragment key={it.rel}>
        <div
          className={`ws-node${!it.dir && doc?.rel === it.rel ? " active" : ""}`}
          style={{ paddingLeft: `${8 + depth * 14}px` }}
          onClick={() => (it.dir ? toggleDir(it.rel) : openDoc(it.rel))}
        >
          <span className="arr">{it.dir ? (expanded[it.rel] ? "▾" : "▸") : ""}</span>
          <span>{it.dir ? (expanded[it.rel] ? "📂" : "📁") : wsIcon(it.name)}</span>
          <span className="lbl">{it.name}</span>
        </div>
        {it.dir && expanded[it.rel] && rows(it.rel, depth + 1)}
      </Fragment>
    ));

  const isMd = doc !== null && (doc.ext === ".md" || doc.ext === ".markdown");

  return (
    <>
      {/* 聊天区 / 工作区 拖拽分隔条（原型 :628；收起时整条隐藏） */}
      <div
        ref={chatResizerRef}
        className="resizer"
        title="拖拽调整聊天区 / 工作区宽度"
        style={collapsed ? { display: "none" } : undefined}
      ></div>
      <aside
        ref={wsRef}
        className={`workspace${collapsed ? " collapsed" : ""}${doc ? " wide" : ""}`}
      >
        <div className="ws-head">
          <span>🗀 工作区</span>
          {treeHidden && (
            <button className="icon-btn" title="显示目录栏" onClick={() => setTreeHidden(false)}>☰</button>
          )}
          <div className="spacer" />
          <button className="icon-btn" title="收起工作区" onClick={onCollapse}>»</button>
        </div>
        <div className="ws-toolbar">
          {/* 裁定 2：「＋ 新建文件」不渲染；只留带文字的手动刷新 */}
          <button
            className="ws-btn"
            title="刷新目录"
            onClick={() => void reloadTree().then((ok) => { if (ok) onToast("已刷新目录"); })}
          >↻ 刷新</button>
        </div>
        <div ref={wsBodyRef} className={`ws-body${treeHidden ? " tree-hidden" : ""}`}>
          <div ref={wsSideRef} className="ws-side">
            <div className="ws-dir-head">
              <span>📁 目录</span>
              <button className="icon-btn" title="隐藏目录栏" onClick={() => setTreeHidden(true)}>«</button>
            </div>
            <div className="ws-tree">
              {treeErr !== null ? (
                <div className="empty-tip">
                  {treeErr}
                  <div style={{ marginTop: 8 }}>
                    <button className="mini-btn" onClick={() => void reloadTree()}>↻ 重试</button>
                  </div>
                </div>
              ) : (
                <>
                  {rows("", 0)}
                  {kids[""] && kids[""].length === 0 && (
                    <div className="empty-tip">目录是空的 · 智能体的产出会出现在这里</div>
                  )}
                </>
              )}
            </div>
          </div>
          <div ref={treeResizerRef} className="ws-resizer" title="拖拽调整目录栏宽度"></div>
          {doc && (
            <div className={`ws-editor show${isMd && doc.mode === "preview" ? " preview" : ""}`}>
              <div className="e-head">
                <span className={`dirty${dirty ? " on" : ""}`}></span>
                <span>{doc.name}</span>
                {isMd && (
                  <span className="e-tabs show">
                    <button className={`e-tab${doc.mode === "preview" ? " on" : ""}`} onClick={() => setMode("preview")}>预览</button>
                    <button className={`e-tab${doc.mode === "edit" ? " on" : ""}`} onClick={() => setMode("edit")}>编辑</button>
                  </span>
                )}
                <div className="spacer" />
                <button className="icon-btn" title="关闭文件" onClick={closeDoc}>✕</button>
              </div>
              {isMd && <div className="md-preview" dangerouslySetInnerHTML={{ __html: mdRender(doc.content) }} />}
              <textarea spellCheck={false} value={doc.content} onChange={(e) => onEditContent(e.target.value)} />
              <div className="e-foot">
                <button className="ws-btn main" title="保存到磁盘" onClick={() => void saveDoc()}>💾 保存</button>
                <span style={{ fontSize: 11, color: "var(--text-2)" }}>
                  {fmtSize(kbBytes(doc.content))} · {fmtTime(doc.mtime)}
                </span>
              </div>
            </div>
          )}
        </div>
        {/* 裁定 3：状态栏 = 📁 项目名 · 绝对目录（原型的智能体标签去掉） */}
        <div className="ws-status">📁 {project.name} · {project.dir}</div>
      </aside>
    </>
  );
}
