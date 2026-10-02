import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { kbSearchFiles, kbTree, type KbBrowseItem, type KbSearchHit } from "../api/client";
import KbTreePane from "./kb/KbTreePane";

/** Task 9/10 消费：中栏文档状态（原型 KB 对象 :2342-2345 的文档部分）。 */
export interface KbDocState {
  rel: string;
  name: string;
  ext: string;
  content: string;
  disk: string;
  mtime: number;
  mode: "view" | "edit";
}

/** 收起按钮恢复文案与 resizer 夹持范围（用户裁定：180–560 / 260–640，宽于原型 520/620 上限）。 */
const KBW_MIN = 180, KBW_MAX = 560, KBC_MIN = 260, KBC_MAX = 640;

/** 原型 :1652-1660 dragBar 的 hook 化：mousedown → document mousemove → mouseup，卸载即清理。 */
function bindDragBar(el: HTMLElement, onMove: (ev: MouseEvent) => void): () => void {
  let detachMove: (() => void) | null = null;
  const down = (e: MouseEvent) => {
    e.preventDefault();
    el.classList.add("dragging");
    const move = (ev: MouseEvent) => onMove(ev);
    const up = () => {
      el.classList.remove("dragging");
      detachMove?.();
      detachMove = null;
      document.body.style.userSelect = "";
    };
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    detachMove = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
  };
  el.addEventListener("mousedown", down);
  return () => { el.removeEventListener("mousedown", down); detachMove?.(); document.body.style.userSelect = ""; };
}

/** 原型对 #kbTree 直接换 innerHTML（搜索中…/错误行），React 等价：占位盒与 KbTreePane 同构（树盒+脚注）。 */
function TreeBoxPlaceholder({ msg, root }: { msg: string; root: string }): ReactNode {
  return (
    <>
      <div className="kb-tree"><div className="hd">{msg}</div></div>
      <div className="kb-root">{root ? `KB · ${root}` : ""}</div>
    </>
  );
}

export default function KbPage() {
  // —— 三栏布局状态（原型 viewKb classList + --kbw/--kbc）——
  const layoutRef = useRef<HTMLDivElement>(null);
  const [sideHidden, setSideHidden] = useState(false);
  const [chatHidden, setChatHidden] = useState(false);

  // —— toast（原型 :1512：2200ms 自动收起，重复触发重置计时）——
  const [toastMsg, setToastMsg] = useState("");
  const toastTimer = useRef<number>();
  const toast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(""), 2200);
  }, []);

  // —— 树数据加载（原型 KB.kids/expanded/root + kbLoadDir :2378-2385，缓存留在本页）——
  const [root, setRoot] = useState("");
  const [kids, setKids] = useState<Record<string, KbBrowseItem[]>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ "": true });
  const [total, setTotal] = useState<number | null>(null);
  const [treeErr, setTreeErr] = useState<string | null>(null);
  const [activeRel, setActiveRel] = useState("");
  const kidsRef = useRef(kids);
  const expandedRef = useRef(expanded);
  const rootRef = useRef(root);
  kidsRef.current = kids;
  expandedRef.current = expanded;
  rootRef.current = root;

  /** 原型 kbLoadDir：kids 缺失才拉（懒加载=首次展开触发）；root 首次响应时落一次。 */
  const loadKids = useCallback(async (rel: string): Promise<KbBrowseItem[]> => {
    if (kidsRef.current[rel]) return kidsRef.current[rel];
    const j = await kbTree(rel);
    if (!rootRef.current) { rootRef.current = j.root; setRoot(j.root); }
    kidsRef.current = { ...kidsRef.current, [rel]: j.items };
    setKids(kidsRef.current);
    if (rel === "") setTotal(j.items.length); // 计数 num：根条目数
    return j.items;
  }, []);

  // 根目录 mount 时加载一次（原型 kbEnter 的懒进入，React 侧在挂载时执行）
  const didInit = useRef(false);
  useEffect(() => {
    if (didInit.current) return;
    didInit.current = true;
    loadKids("").catch((e: Error) =>
      // 原型 :2733 提示语中的 serve.js 启动方式不适用本项目（正式后端为 FastAPI），只保留错误信息
      setTreeErr(`知识库接口不可用：${e.message}`));
  }, [loadKids]);

  /** 原型 :2414-2421 —— 目录点击：折叠 or 展开+首次拉取；失败走 toast。 */
  const onToggleDir = useCallback((rel: string) => {
    if (expandedRef.current[rel]) {
      setExpanded((prev) => { const n = { ...prev }; delete n[rel]; return n; });
      return;
    }
    setExpanded((prev) => ({ ...prev, [rel]: true }));
    loadKids(rel).catch((e: Error) => toast(e.message));
  }, [loadKids, toast]);

  /** Task 8 仅高亮选中行；文档载入/父级展开属 Task 9（中栏）范围。 */
  const onOpenFile = useCallback((rel: string) => setActiveRel(rel), []);

  // —— 搜索：300ms 防抖 + 序号防竞态（原型 :2716-2727 kbSearchSeq；间隔 260→300ms 按 brief）——
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<KbSearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const searchSeq = useRef(0);
  const searchTimer = useRef<number>();
  const onQueryChange = useCallback((v: string) => {
    setQuery(v);
    window.clearTimeout(searchTimer.current);
    const q = v.trim();
    const seq = ++searchSeq.current;
    if (!q) { searchSeq.current++; setSearchHits(null); setSearching(false); setSearchErr(null); return; }
    setSearching(true);
    searchTimer.current = window.setTimeout(() => {
      kbSearchFiles(q, 200).then((j) => {
        if (seq !== searchSeq.current) return;
        setSearchErr(null); setSearchHits(j.hits); setSearching(false);
      }).catch((e: Error) => {
        if (seq !== searchSeq.current) return;
        setSearchErr(e.message); setSearching(false);
      });
    }, 300);
  }, []);
  useEffect(() => () => { window.clearTimeout(searchTimer.current); window.clearTimeout(toastTimer.current); }, []);

  // —— 双 resizer：拖拽写 --kbw/--kbc（原型 :2708-2715，夹持范围按裁定放宽）——
  useEffect(() => {
    const layout = layoutRef.current;
    if (!layout) return;
    const specs: Array<[string, (x: number) => void]> = [
      ["kbResizer", (x) => {
        const w = Math.min(Math.max(x - layout.getBoundingClientRect().left, KBW_MIN), KBW_MAX);
        layout.style.setProperty("--kbw", `${w}px`);
      }],
      ["kbChatResizer", (x) => {
        const w = Math.min(Math.max(layout.getBoundingClientRect().right - x, KBC_MIN), KBC_MAX);
        layout.style.setProperty("--kbc", `${w}px`);
      }],
    ];
    const detachers: Array<() => void> = [];
    specs.forEach(([id, onMove]) => {
      const el = document.getElementById(id);
      if (!el) return;
      detachers.push(bindDragBar(el, (ev) => onMove(ev.clientX)));
    });
    return () => detachers.forEach((fn) => fn());
  }, []);

  const searchingView = query.trim() && (searching || searchErr !== null);

  return (
    <>
      {/* 原型 :696-751 三栏骨架；收起类名 side-hidden/chat-hidden（CSS 依赖 #kbResizer/#kbChatResizer id） */}
      <div
        ref={layoutRef}
        className={`kb-layout${sideHidden ? " side-hidden" : ""}${chatHidden ? " chat-hidden" : ""}`}
      >
        <aside className="kb-side">
          <div className="kb-side-head">📚 目录<span className="num">{total === null ? "–" : `${total} 项`}</span><div className="spacer"></div>
            <button className="icon-btn" title="隐藏目录树" onClick={() => setSideHidden(true)}>«</button>
          </div>
          <div className="kb-search"><span className="mag">🔍</span>
            <input value={query} placeholder="全库搜索文件名…" onChange={(e) => onQueryChange(e.target.value)} />
          </div>
          {searchingView ? (
            <TreeBoxPlaceholder msg={searchErr !== null ? searchErr : "搜索中…"} root={root} />
          ) : treeErr !== null && !query.trim() ? (
            /* 原型同盒覆盖：搜索出结果后错误行让位于命中列表，清空搜索再回显 */
            <TreeBoxPlaceholder msg={treeErr} root={root} />
          ) : (
            <KbTreePane
              root={root}
              kids={kids}
              expanded={expanded}
              activeRel={activeRel}
              total={total}
              searchHits={searchHits}
              onToggleDir={onToggleDir}
              onOpenFile={onOpenFile}
            />
          )}
        </aside>
        <div className="resizer" id="kbResizer" title="拖拽调整目录树宽度"></div>

        <section className="kb-main">
          <div className="kb-head">
            {/* 恢复钮按裁定带文字标签；原型放在中栏头部（:709/:718），侧栏收起后唯一可见位置 */}
            {sideHidden && <button className="mini-btn" id="btnKbSideShow" title="显示目录树" onClick={() => setSideHidden(false)}>📁 目录</button>}
            <div className="kb-crumbs">未打开文件</div>
            <div className="spacer"></div>
            {chatHidden && <button className="mini-btn" id="btnKbChatShow" title="显示知识库助手" onClick={() => setChatHidden(false)}>💬 助手</button>}
          </div>
          {/* Task 9 填充：kb-meta / 预览 / 编辑 / 底部操作栏；本任务仅占位空壳（原型初始空态 :722） */}
          <div className="kb-doc">
            <div className="kb-empty"><span className="big">📚</span><span>左侧选择知识库文件；中间渲染或编辑，右侧可让助手帮你改。</span></div>
          </div>
        </section>

        <div className="resizer" id="kbChatResizer" title="拖拽调整助手宽度"></div>
        <aside className="kb-chat">
          <div className="kb-chat-head">🤖 知识库助手<div className="spacer"></div>
            {/* 原型初始 zhb_kb，root 载入后换实体目录名（kbLoadDir :2381） */}
            <span className="scope">{root ? root.split(/[\\/]/).pop() : "zhb_kb"}</span>
            <button className="icon-btn" title="隐藏助手" onClick={() => setChatHidden(true)}>»</button>
          </div>
          {/* Task 10 填充：消息流 / 快捷指令 / 输入区；本任务仅占位空壳 */}
          <div className="kb-msgs"></div>
        </aside>
      </div>
      <div className={`toast${toastMsg ? " show" : ""}`}>{toastMsg}</div>
    </>
  );
}
