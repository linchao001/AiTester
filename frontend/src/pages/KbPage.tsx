import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError, chatSendStream, chatStop, getProjects,
  kbIndexRebuild, kbIndexSync, kbPostFile, kbPutFile, kbReadFile, kbSave, kbSearchFiles, kbTree,
  type KbBrowseItem, type KbDraft, type KbIndexResponse, type KbSearchHit, type KbWriteResponse,
  type Project,
} from "../api/client";
import { applyEvent, finalize, liveText, newStreamState } from "./chat/streamState";
import { bindDragBar } from "../components/dragBar";
import PageState from "../components/PageState";
import KbTreePane from "./kb/KbTreePane";
import KbEditorPane from "./kb/KbEditorPane";
import KbAssistantPane, { type KbChatMsg } from "./kb/KbAssistantPane";
import { type KbDraftState } from "./kb/KbDraftCard";
import { KB_ALIAS, KB_BUCKETS, kbDirOf, kbDisp, kbJoin, kbSlug } from "./kb/utils";

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

/** 与聊天页共用：当前项目是视图上下文，刷新保留、换浏览器不带走。 */
const PROJECT_STORAGE_KEY = "aitester.chat.projectId";
/** 助手消息按项目落盘；宽度偏好全局一份。 */
const msgsStorageKey = (pid: string) => `aitester.kb.msgs.${pid}`;
const WIDTH_STORAGE_KEY = "aitester.kb.widths";

/** 左栏夹持仍保留；助手栏只保留下限，上限随布局动态算（可盖住中栏阅读区）。 */
const KBW_MIN = 180, KBW_MAX = 560, KBC_MIN = 260;

function loadKbMsgs(pid: string): KbChatMsg[] {
  try {
    const raw = window.localStorage.getItem(msgsStorageKey(pid));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((m): m is KbChatMsg => !!m && (m.who === "me" || m.who === "ai") && typeof m.text === "string")
      .filter((m) => !m.pending) // 刷新时丢掉「思考中…」半截气泡
      .map((m) => ({
        ...m,
        drafts: m.drafts?.map((d) => ({
          ...d,
          // 写盘中断在半途：回落为可再确认，避免卡在 writing
          state: d.state === "writing" ? "pending" as const : d.state,
        })),
      }));
  } catch {
    return [];
  }
}

function saveKbMsgs(pid: string, msgs: KbChatMsg[]) {
  try {
    const slim = msgs.filter((m) => !m.pending);
    window.localStorage.setItem(msgsStorageKey(pid), JSON.stringify(slim));
  } catch { /* quota / 隐私模式：忽略 */ }
}

function loadKbWidths(): { kbw?: string; kbc?: string } {
  try {
    const raw = window.localStorage.getItem(WIDTH_STORAGE_KEY);
    if (!raw) return {};
    const j = JSON.parse(raw) as { kbw?: string; kbc?: string };
    return j && typeof j === "object" ? j : {};
  } catch {
    return {};
  }
}

function saveKbWidths(kbw: string, kbc: string) {
  try {
    window.localStorage.setItem(WIDTH_STORAGE_KEY, JSON.stringify({ kbw, kbc }));
  } catch { /* ignore */ }
}

/** 原型对 #kbTree 直接换 innerHTML（搜索中…/错误行），React 等价：占位盒与 KbTreePane 同构（树盒+脚注）。 */
function TreeBoxPlaceholder({
  msg, root, onRetry,
}: { msg: string; root: string; onRetry?: () => void }): ReactNode {
  return (
    <>
      <div className="kb-tree">
        <div className="hd" style={{ whiteSpace: "pre-wrap" }}>{msg}</div>
        {onRetry && (
          <div style={{ padding: "8px 10px" }}>
            <button type="button" className="mini-btn" onClick={onRetry}>↻ 重新加载</button>
          </div>
        )}
      </div>
      <div className="kb-root">{root ? `KB · ${root}` : ""}</div>
    </>
  );
}

export default function KbPage() {
  const navigate = useNavigate();

  // —— 三栏布局状态（原型 viewKb classList + --kbw/--kbc）——
  const layoutRef = useRef<HTMLDivElement>(null);
  const [sideHidden, setSideHidden] = useState(false);
  const [chatHidden, setChatHidden] = useState(false);

  // —— toast（原型 :1512：2200ms 自动收起，重复触发重置计时）——
  const [toastMsg, setToastMsg] = useState("");
  const toastTimer = useRef<number>();
  const toast = useCallback((msg: string, ms = 2200) => {
    setToastMsg(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(""), ms);
  }, []);

  // —— 索引同步 / 全量重建（相对 browse：只催 Reme 派生索引）——
  const [indexBusy, setIndexBusy] = useState(false);
  const indexBusyRef = useRef(false);

  // —— 项目：知识库浏览根随项目走，必须先选项目 ——
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectsLoaded, setProjectsLoaded] = useState(false);
  const [projectsError, setProjectsError] = useState("");
  const projSeq = useRef(0);
  const projectIdRef = useRef(projectId);
  projectIdRef.current = projectId;

  const reloadProjects = useCallback(async () => {
    const seq = ++projSeq.current;
    try {
      const j = await getProjects();
      if (seq !== projSeq.current) return;
      setProjects(j.projects);
      setProjectsError("");
      setProjectsLoaded(true);
      setProjectId((cur) => {
        if (cur && j.projects.some((p) => p.id === cur)) return cur;
        const saved = window.localStorage.getItem(PROJECT_STORAGE_KEY) || "";
        const hit = j.projects.find((p) => p.id === saved) || j.projects[0];
        if (!hit) window.localStorage.removeItem(PROJECT_STORAGE_KEY);
        else if (hit.id !== saved) window.localStorage.setItem(PROJECT_STORAGE_KEY, hit.id);
        return hit ? hit.id : "";
      });
    } catch (err) {
      if (seq !== projSeq.current) return;
      setProjectsError(err instanceof Error ? err.message : String(err));
      setProjectsLoaded(false);
    }
  }, []);

  useEffect(() => { void reloadProjects(); }, [reloadProjects]);

  // —— 树数据加载（原型 KB.kids/expanded/root + kbLoadDir :2378-2385，缓存留在本页）——
  // 展示根名恒为别名：后端 browse 响应里的实体根路径只用于服务端定位，不进 UI（脱敏裁定）
  const root = KB_ALIAS;
  const [kids, setKids] = useState<Record<string, KbBrowseItem[]>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ "": true });
  const [total, setTotal] = useState<number | null>(null);
  const [treeErr, setTreeErr] = useState<string | null>(null);
  const [activeRel, setActiveRel] = useState("");
  const kidsRef = useRef(kids);
  const expandedRef = useRef(expanded);
  kidsRef.current = kids;
  expandedRef.current = expanded;

  // —— 中栏文档状态（Task 9；原型 KB.path/content/disk/mtime/mode 的 React 化）——
  const [doc, setDoc] = useState<KbDocState | null>(null);
  const docRef = useRef(doc);
  docRef.current = doc;

  // —— 搜索 / 助手（切项目时一并清零，故声明提前）——
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<KbSearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const searchSeq = useRef(0);
  const searchTimer = useRef<number>();
  const [msgs, setMsgs] = useState<KbChatMsg[]>([]);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const runIdRef = useRef("");
  const stopRequestedRef = useRef(false);
  const [stopRequested, setStopRequested] = useState(false);
  const kbAbortRef = useRef<AbortController | null>(null);

  const resetBrowse = useCallback(() => {
    kidsRef.current = {};
    expandedRef.current = { "": true };
    setKids({});
    setExpanded({ "": true });
    setTotal(null);
    setTreeErr(null);
    setActiveRel("");
    docRef.current = null;
    setDoc(null);
    setQuery("");
    setSearchHits(null);
    setSearching(false);
    setSearchErr(null);
    searchSeq.current++;
    // 助手消息不在此清空：随 projectId 从 localStorage 换载，避免切项目时误抹旧项目缓存
  }, []);

  // 助手会话：按项目读写 localStorage，刷新不丢；切项目换一份
  const msgsPidRef = useRef("");
  useEffect(() => {
    if (!projectId) {
      msgsPidRef.current = "";
      setMsgs([]);
      return;
    }
    if (msgsPidRef.current !== projectId) {
      msgsPidRef.current = projectId;
      setMsgs(loadKbMsgs(projectId));
      return;
    }
    saveKbMsgs(projectId, msgs);
  }, [projectId, msgs]);

  /** 原型 kbLoadDir：kids 缺失才拉（懒加载=首次展开触发）。 */
  const loadKids = useCallback(async (rel: string): Promise<KbBrowseItem[]> => {
    const pid = projectIdRef.current;
    if (!pid) throw new Error("请先选择项目");
    if (kidsRef.current[rel]) return kidsRef.current[rel];
    const j = await kbTree(pid, rel);
    kidsRef.current = { ...kidsRef.current, [rel]: j.items };
    setKids(kidsRef.current);
    if (rel === "") setTotal(j.items.length); // 计数 num：根条目数
    return j.items;
  }, []);

  const reloadTree = useCallback(() => {
    if (!projectIdRef.current) return;
    kidsRef.current = {};
    setKids({});
    setTotal(null);
    setTreeErr(null);
    loadKids("").catch((e: Error) => setTreeErr(e.message));
  }, [loadKids]);

  // 选中项目后拉根目录；切项目由 resetBrowse + 本 effect 重进
  useEffect(() => {
    if (!projectId) return;
    reloadTree();
  }, [projectId, reloadTree]);

  /** 原型 :2414-2421 —— 目录点击：折叠 or 展开+首次拉取；失败走 toast。 */
  const onToggleDir = useCallback((rel: string) => {
    if (expandedRef.current[rel]) {
      setExpanded((prev) => { const n = { ...prev }; delete n[rel]; return n; });
      return;
    }
    setExpanded((prev) => ({ ...prev, [rel]: true }));
    loadKids(rel).catch((e: Error) => toast(e.message));
  }, [loadKids, toast]);

  /** 原型 kbOpen :2427-2447 —— 读盘载入；非 force 且有未保存修改先确认放弃。
      docRef 同步写（同 kidsRef 模式）：紧随其后的 kbWrite/进编辑态不依赖重渲染时序。 */
  const kbOpen = useCallback(async (rel: string, force: boolean): Promise<boolean> => {
    const pid = projectIdRef.current;
    if (!pid) { toast("请先选择项目"); return false; }
    const cur = docRef.current;
    if (!force && cur && cur.content !== cur.disk) {
      if (!window.confirm("当前文件有未保存的修改，放弃并切换？")) return false;
    }
    let j;
    try { j = await kbReadFile(pid, rel); }
    catch (err) { toast(err instanceof Error ? err.message : String(err)); return false; }
    const next: KbDocState = {
      rel: j.rel, name: j.name, ext: j.ext,
      content: j.content, disk: j.content, mtime: j.mtime, mode: "view",
    };
    docRef.current = next; setDoc(next);
    setActiveRel(rel);
    // 原型 :2436-2437 —— 父目录未展开则自动展开并拉取
    const parent = kbDirOf(j.rel);
    if (parent && !expandedRef.current[parent]) {
      expandedRef.current = { ...expandedRef.current, [parent]: true };
      setExpanded(expandedRef.current);
      loadKids(parent).catch(() => {});
    }
    return true;
  }, [loadKids, toast]);

  /** brief Step 2 kbWrite 原样转写；Task 10 草案卡确认复用同一函数（409 自动重载语义统一）。
      原型 doc?.mtime 的闭包读取改为 docRef（避免 setTimeout/异步链路拿旧值）。 */
  const kbWrite = useCallback(async (
    rel: string, content: string, mode: "PUT" | "POST", mtime?: number,
  ): Promise<KbWriteResponse | null> => {
    const pid = projectIdRef.current;
    if (!pid) { toast("请先选择项目"); return null; }
    try {
      const j = mode === "PUT"
        ? await kbPutFile(pid, rel, content, mtime !== undefined ? mtime : docRef.current?.mtime ?? 0)
        : await kbPostFile(pid, rel, content);
      if (rel === docRef.current?.rel) {
        const next = { ...(docRef.current as KbDocState), content: j ? content : docRef.current!.content, disk: content, mtime: j.mtime };
        docRef.current = next; setDoc(next);
      }
      // 目录刷新：kids 删除 rel 所在目录缓存再重拉（原型 kbWrite 尾部同款 :2494-2496）
      const dir = kbDirOf(rel);
      kidsRef.current = { ...kidsRef.current };
      delete kidsRef.current[dir];
      setKids(kidsRef.current);
      if (dir && !expandedRef.current[dir]) {
        expandedRef.current = { ...expandedRef.current, [dir]: true };
        setExpanded(expandedRef.current);
      }
      loadKids(dir).catch(() => {});
      toast(`已写入 ${j.rel} · 索引自动收敛后可被检索（约数十秒）`);
      return j;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && mode === "PUT") {
        // 跨文件护栏（终审项 3）：当前打开的是另一份脏文档时不强制切换重载，
        // 否则未保存修改被无声丢弃；同文件或干净文档保留规范认可的强制重载。
        const cur = docRef.current;
        if (cur && cur.rel !== rel && cur.content !== cur.disk) {
          toast("存在未保存的修改，已跳过自动重载，手动保存或放弃后可重新打开");
          return null;
        }
        toast("文件在别处被改过，已重新载入磁盘最新内容（未保存的修改已丢弃）");
        void kbOpen(rel, true);
        return null;
      }
      toast(err instanceof Error ? err.message : String(err));
      return null;
    }
  }, [kbOpen, loadKids, toast]);

  const onOpenFile = useCallback((rel: string) => { void kbOpen(rel, false); }, [kbOpen]);

  const dirty = doc !== null && doc.content !== doc.disk;

  const onProjectChange = useCallback((id: string) => {
    if (id === projectIdRef.current) return;
    if (busyRef.current) { toast("助手正在回答，请稍后再切换项目"); return; }
    const cur = docRef.current;
    if (cur && cur.content !== cur.disk
      && !window.confirm("当前文件有未保存的修改，切换项目将丢弃它们。继续？")) {
      return;
    }
    window.localStorage.setItem(PROJECT_STORAGE_KEY, id);
    resetBrowse();
    setProjectId(id);
  }, [resetBrowse, toast]);

  // —— 中栏回调：确认弹窗文案逐字照原型 :2501-2516（window.confirm，plan-mandated）——
  const onMode = useCallback((m: "view" | "edit") => {
    // textarea 受控（onEditContent 实时回收），edit→view 无原型「先取 area.value」的额外步骤
    const cur = docRef.current;
    if (!cur) return;
    const next = { ...cur, mode: m };
    docRef.current = next; setDoc(next);
  }, []);
  const onEditContent = useCallback((v: string) => {
    const cur = docRef.current;
    if (!cur) return;
    const next = { ...cur, content: v };
    docRef.current = next; setDoc(next);
  }, []);
  const onSave = useCallback(() => {
    const cur = docRef.current;
    if (!cur) return;
    if (cur.content === cur.disk) { toast("没有需要保存的修改"); return; }
    if (!window.confirm(`确认写入磁盘？\n\n${kbDisp(cur.rel)}\n\n这是知识库真实文件，保存会直接覆盖。`)) return;
    void kbWrite(cur.rel, cur.content, "PUT");
  }, [kbWrite, toast]);
  const onDiscard = useCallback(() => {
    const cur = docRef.current;
    if (!cur) return;
    if (cur.content === cur.disk) { toast("没有未保存的修改"); return; }
    if (!window.confirm("放弃未保存的修改，恢复成磁盘内容？")) return;
    const next = { ...cur, content: cur.disk };
    docRef.current = next; setDoc(next); toast("已恢复磁盘内容");
  }, [toast]);
  const onReload = useCallback(() => {
    const cur = docRef.current;
    if (!cur) return;
    if (cur.content !== cur.disk && !window.confirm("有未保存修改，重新载入会丢弃它们，继续？")) return;
    void kbOpen(cur.rel, true).then((ok) => { if (ok) toast("已从磁盘重新载入"); });
  }, [kbOpen, toast]);

  // —— 新建笔记弹窗（原型 openKbNew + btnKbNewCreate :2518-2553；#kbNewMask → 状态驱动条件渲染）——
  const [newNoteOpen, setNewNoteOpen] = useState(false);
  const [nfTitle, setNfTitle] = useState("");
  const [nfDir, setNfDir] = useState("");
  const [nfName, setNfName] = useState("");
  const [nfDesc, setNfDesc] = useState("");
  const [nfTip, setNfTip] = useState("");
  const openNewNote = useCallback(() => {
    if (!projectIdRef.current) { toast("请先选择项目"); return; }
    if (treeErr) { toast(treeErr); return; }
    // 原型 :2521-2522 —— 目录默认当前打开文件的父目录或 _inbox
    const cur = docRef.current;
    setNfDir(cur ? (kbDirOf(cur.rel) || "_inbox") : "_inbox");
    setNfTitle(""); setNfName(""); setNfDesc(""); setNfTip("");
    setNewNoteOpen(true);
  }, [toast, treeErr]);
  const onNfTitleChange = useCallback((v: string) => {
    // 原型 :2534 —— 标题 → kbSlug 自动文件名（补 .md）；改动即清 tip
    const n = kbSlug(v);
    setNfTitle(v); setNfName(n ? `${n}.md` : ""); setNfTip("");
  }, []);
  const pickBucket = useCallback((b: string) => { setNfDir(b); setNfTip(""); }, []);
  const createNote = useCallback(async () => {
    const title = nfTitle.trim();
    const dir = nfDir.trim().replace(/^\/+|\/+$/g, "");
    let name = nfName.trim();
    if (!title) { setNfTip("请填写标题"); return; }
    if (!dir) { setNfTip("请填写所在目录"); return; }
    if (!name) name = `${kbSlug(title)}.md`;
    if (!/\.[A-Za-z0-9]+$/.test(name)) name += ".md";
    const rel = kbJoin(dir, name);
    const desc = nfDesc.trim();
    // 模板 frontmatter 全文照原型 :2548；updated_by_agent 按 plan 改为 aitester_kb_assistant（原型串为 aitester_ui）
    const content = `---\nname: "${title}"\ndescription: "${desc || title}"\nbucket: ${dir}\nstatus: draft\nconfidence: 0.5\nupdated_by_agent: aitester_kb_assistant\nupdated_at: ${new Date().toISOString()}\n---\n\n# ${title}\n\n${desc || "一句话说明这条知识是什么。"}\n\n## 背景\n\n- \n\n## 结论\n\n- \n`;
    const j = await kbWrite(rel, content, "POST");
    if (!j) { setNfTip("创建失败，见右上角提示"); return; }
    setNewNoteOpen(false);
    if (await kbOpen(rel, true)) onMode("edit"); // 原型 :2552 —— 开新档并进编辑态
  }, [kbOpen, kbWrite, nfDesc, nfDir, nfName, nfTitle, onMode]);

  // —— 搜索：300ms 防抖 + 序号防竞态（原型 :2716-2727 kbSearchSeq；间隔 260→300ms 按 brief）——
  const onQueryChange = useCallback((v: string) => {
    setQuery(v);
    window.clearTimeout(searchTimer.current);
    const q = v.trim();
    const seq = ++searchSeq.current;
    if (!q) { searchSeq.current++; setSearchHits(null); setSearching(false); setSearchErr(null); return; }
    const pid = projectIdRef.current;
    if (!pid) { setSearchErr("请先选择项目"); setSearching(false); return; }
    setSearching(true);
    searchTimer.current = window.setTimeout(() => {
      kbSearchFiles(pid, q, 200).then((j) => {
        if (seq !== searchSeq.current) return;
        setSearchErr(null); setSearchHits(j.hits); setSearching(false);
      }).catch((e: Error) => {
        if (seq !== searchSeq.current) return;
        setSearchErr(e.message); setSearching(false);
      });
    }, 300);
  }, []);
  useEffect(() => () => {
    window.clearTimeout(searchTimer.current); window.clearTimeout(toastTimer.current);
    kbAbortRef.current?.abort();   // 离开 /kb 即断流：后端不再无人消费地跑完（与聊天页同构）
  }, []);

  // —— 助手栏（Task 10：原型 kbAsk/kbSay/kbDraftCard :2556-2596/:2675-2689 的接线迁进流式通道，
  //     与聊天页同一条 /api/chat/send/stream；kb-console 是临时键 use_file=False，会话行不落 jsonl）——

  /** 原型 :2682-2685 —— 用最新回复（+逐条草案卡 / 错误行）更新「思考中…」占位气泡。
      keepPending=true 时气泡仍是 pending 态（流式中的 live 更新），false 即终态替换。
      逆序找最后一条 pending ai 消息；终态却找不到占位（异常路径）则追加，不让已到手的回复消失。 */
  const setLastAi = useCallback((
    text: string, drafts: KbDraft[], keepPending: boolean, error = false, stopped = false,
  ) => {
    setMsgs((prev) => {
      const next = [...prev];
      let i = next.length - 1;
      while (i >= 0 && !(next[i].who === "ai" && next[i].pending)) i--;
      // 草案卡流内即刻挂上后，用户可能在两条事件之间就点了确认：按「下标+草案对象身份」
      // 沿用旧卡状态（applyEvent 对 drafts 追加不改写，对象引用稳定），否则下一个 delta
      // 就把 writing/done 打回 pending，露出二次写盘窗口。
      const carried = i >= 0 ? next[i].drafts : undefined;
      const msg: KbChatMsg = {
        who: "ai", text,
        pending: keepPending || undefined,
        error: error || undefined,
        stopped: stopped || undefined,
        drafts: drafts.length
          ? drafts.map((d, di) => {
            const e = carried?.[di];
            return e && e.draft === d ? e : { draft: d, state: "pending" as const };
          })
          : undefined,
      };
      if (i >= 0) next[i] = msg;
      else if (!keepPending) next.push(msg);   // 终态却找不到占位（异常路径）：宁可追加，也不能让已到手的回复消失
      return next;
    });
  }, []);

  const replaceLastAi = useCallback((text: string, drafts: KbDraft[], error = false, stopped = false) => {
    setLastAi(text, drafts, false, error, stopped);
  }, [setLastAi]);

  /** session_id/agent_id 固定；必须带当前项目，Reme 绑该项目 .AiTester。
   *  kb-console 临时键不落 jsonl（进程内短窗 + 本页 localStorage）。 */
  const ask = useCallback(async (text: string) => {
    if (busyRef.current) return;
    if (!projectIdRef.current) { toast("请先选择项目"); return; }
    if (treeErr) { toast(treeErr); return; }
    busyRef.current = true;
    setBusy(true);
    runIdRef.current = "";
    stopRequestedRef.current = false;
    setStopRequested(false);
    setMsgs((prev) => [...prev, { who: "me", text }, { who: "ai", text: "思考中…", pending: true }]);
    const st = newStreamState();
    const controller = new AbortController();
    kbAbortRef.current = controller;
    let errMsg = "";
    try {
      await chatSendStream(
        { session_id: "kb-console", message: text, agent_id: "kb_assistant", project_id: projectIdRef.current },
        (ev) => {
          if (ev.type === "start") runIdRef.current = ev.run_id;
          Object.assign(st, applyEvent(st, ev));   // 折叠就地推进：本栏只需一份累加器
          if (st.terminal) return;                 // 终态不 patch 气泡，交给下面的收尾一次落定
          // live 文本进「思考中…」占位气泡（pending 保持真），草案卡在流内即刻挂上
          setLastAi(liveText(st) || "思考中…", st.drafts, true);
        }, controller.signal);
    } catch (err) {
      // 离开 /kb 触发的 abort：组件已不在，连接已断，后端走 GeneratorExit 收尾
      if (err instanceof DOMException && err.name === "AbortError") return;
      // 非 ApiError 的断流（如网络 TypeError）不给英文原文，中文兜底（Task 9 裁定 1）
      errMsg = err instanceof ApiError ? err.message : "连接中断，助手未完成";
    }
    kbAbortRef.current = null;
    busyRef.current = false;
    setBusy(false);
    setStopRequested(false);   // 终态统一清旗（与 ChatPage 同口径）：ask 开头的重置保留不动
    const done = st.done;
    if (done) {
      const f = finalize(st, done);
      replaceLastAi(f.content, f.drafts, false, f.stopped);
      return;
    }
    // 失败进气泡（红色），不抢 toast；草案卡保留（draft 必在 prepare_kb_write 成功后才发，确认走独立 REST）
    replaceLastAi(errMsg || st.fail || "连接中断，助手未完成", st.drafts, true);
  }, [replaceLastAi, setLastAi, toast, treeErr]);

  const stopAsk = useCallback(() => {
    if (stopRequestedRef.current) return;
    const rid = runIdRef.current;
    if (!rid) { toast("还在建立连接，请稍候"); return; }   // start 未到没有 run_id 可停：给可见反馈，不留死按钮
    stopRequestedRef.current = true;
    setStopRequested(true);
    chatStop(rid).catch((err: unknown) => {
      // 404「这条回答已经结束」是与终态并发的正常竞态；非 ApiError 中文兜底（Task 9 裁定 1）
      toast(err instanceof ApiError ? err.message : "停止请求失败，助手可能仍在继续");
    });
  }, [toast]);

  /** 草案态迁移：drafts 内嵌于消息（原型卡片挂在 ai 气泡内），以 (消息下标, 草案下标) 定位。 */
  const setDraftState = useCallback((mi: number, di: number, state: KbDraftState, writtenAt?: number) => {
    setMsgs((prev) => prev.map((m, i) => (i !== mi || !m.drafts) ? m : {
      ...m,
      drafts: m.drafts.map((e, j) => (j !== di ? e : { ...e, state, writtenAt: writtenAt ?? e.writtenAt })),
    }));
  }, []);

  /** 草案确认：走 Reme save_to_knowledge（/api/kb/save），不经 browse 直写。
      setDraftState("writing") 在 await 前同步迁移，避免双击重复提交。 */
  const confirmDraft = useCallback(async (mi: number, di: number, d: KbDraft) => {
    const where = d.path ? kbDisp(d.path) : `${d.bucket} · ${d.title}`;
    if (!window.confirm(`确认写入知识库（Reme）？\n${d.op === "create" ? "新建" : "合并/更新"}：${where}`)) return;
    setDraftState(mi, di, "writing");
    try {
      const j = await kbSave(d.title, d.content, d.bucket);
      if (!j.success) {
        toast(typeof j.answer === "string" && j.answer ? j.answer : "知识库写入失败");
        setDraftState(mi, di, "failed");
        return;
      }
      // 写成功后刷新树缓存（桶目录），索引仍由 Reme watch 收敛
      const dir = d.bucket;
      kidsRef.current = { ...kidsRef.current };
      delete kidsRef.current[dir];
      delete kidsRef.current[""];
      setKids(kidsRef.current);
      loadKids(dir).catch(() => {});
      loadKids("").catch(() => {});
      const written = Array.isArray(j.metadata?.written) ? String(j.metadata.written[0] ?? "") : "";
      toast(written
        ? `已写入 ${kbDisp(written)} · 索引自动收敛后可被检索（约数十秒）`
        : (typeof j.answer === "string" && j.answer) || "已写入知识库");
      setDraftState(mi, di, "done", Date.now());
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err));
      setDraftState(mi, di, "failed");
    }
  }, [loadKids, setDraftState, toast]);

  const cancelDraft = useCallback((mi: number, di: number) => {
    setDraftState(mi, di, "canceled");
  }, [setDraftState]);

  const formatIndexToast = useCallback((label: string, body: KbIndexResponse) => {
    const row = body.results[0];
    const c = row?.counts ?? { added: 0, modified: 0, deleted: 0 };
    const sec = ((body.total_elapsed_ms || row?.elapsed_ms || 0) / 1000).toFixed(1);
    if (!body.success) {
      return `${label}失败：${row?.error || "未知错误"}（${sec}s）`;
    }
    return `${label}：新增 ${c.added} · 修改 ${c.modified} · 删除 ${c.deleted}（${sec}s）`;
  }, []);

  const runIndex = useCallback(async (mode: "sync" | "rebuild") => {
    if (!projectId || indexBusyRef.current) return;
    if (mode === "rebuild") {
      const ok = window.confirm(
        "将清空当前项目的派生检索索引（切块 / BM25 / 图谱）并全量重建，知识库正文文件不动。"
        + "\n重建期间请勿写入知识库。"
        + "\n若已配置向量模型，冷缓存时可能产生 embedding 费用。"
        + "\n\n确定继续？",
      );
      if (!ok) return;
    }
    indexBusyRef.current = true;
    setIndexBusy(true);
    try {
      const body = mode === "sync"
        ? await kbIndexSync(projectId)
        : await kbIndexRebuild(projectId);
      toast(formatIndexToast(mode === "sync" ? "索引已同步" : "索引已重建", body), 4200);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : (err instanceof Error ? err.message : String(err)), 4200);
    } finally {
      indexBusyRef.current = false;
      setIndexBusy(false);
    }
  }, [formatIndexToast, projectId, toast]);

  // —— 双 resizer：必须在主布局真正挂载后再绑（首帧是项目加载空态，layoutRef 为空；
  //    旧 deps=[] 会永久漏绑，表现为「怎么都拖不动」）。——
  useEffect(() => {
    if (!projectId || !projectsLoaded || !!projectsError || !projects.length) return;
    const layout = layoutRef.current;
    if (!layout) return;

    const saved = loadKbWidths();
    if (saved.kbw) layout.style.setProperty("--kbw", saved.kbw);
    if (saved.kbc) layout.style.setProperty("--kbc", saved.kbc);

    const persist = () => {
      const kbw = layout.style.getPropertyValue("--kbw") || getComputedStyle(layout).getPropertyValue("--kbw");
      const kbc = layout.style.getPropertyValue("--kbc") || getComputedStyle(layout).getPropertyValue("--kbc");
      saveKbWidths(kbw.trim(), kbc.trim());
    };

    const specs: Array<[string, (x: number) => void]> = [
      ["kbResizer", (x) => {
        const w = Math.min(Math.max(x - layout.getBoundingClientRect().left, KBW_MIN), KBW_MAX);
        layout.style.setProperty("--kbw", `${w}px`);
        persist();
      }],
      ["kbChatResizer", (x) => {
        const rect = layout.getBoundingClientRect();
        const side = layout.querySelector(".kb-side") as HTMLElement | null;
        const sideW = layout.classList.contains("side-hidden")
          ? 0
          : (side?.getBoundingClientRect().width ?? 0);
        // 左/右 resizer 各约 5px；中栏可压到 0，助手可完全占满剩余宽度
        const max = Math.max(KBC_MIN, rect.width - sideW - 10);
        const w = Math.min(Math.max(rect.right - x, KBC_MIN), max);
        layout.style.setProperty("--kbc", `${w}px`);
        persist();
      }],
    ];
    const detachers: Array<() => void> = [];
    specs.forEach(([id, onMove]) => {
      const el = document.getElementById(id);
      if (!el) return;
      detachers.push(bindDragBar(el, (ev) => onMove(ev.clientX)));
    });
    return () => detachers.forEach((fn) => fn());
  }, [projectId, projectsLoaded, projectsError, projects.length]);

  const searchingView = query.trim() && (searching || searchErr !== null);
  const currentProject = projects.find((p) => p.id === projectId);
  const toastEl = toastMsg ? <div className="toast show">{toastMsg}</div> : null;

  if (projectsLoaded && !projects.length) {
    return (
      <div className="page state-page">
        <PageState
          icon="📁"
          title="还没有项目"
          desc="知识库已与项目绑定。先到项目页添加一个目录，再回来浏览与编辑该项目下的知识库。"
          actions={
            <>
              <button className="btn-primary" onClick={() => navigate("/projects")}>去项目页</button>
              <button className="mini-btn" onClick={() => void reloadProjects()}>↻ 重试</button>
            </>
          }
        />
        {toastEl}
      </div>
    );
  }

  if (projectsError) {
    return (
      <div className="page state-page">
        <PageState
          icon="⚠️"
          title="项目列表加载失败"
          desc={projectsError}
          actions={<button className="btn-primary" onClick={() => void reloadProjects()}>↻ 重试</button>}
        />
        {toastEl}
      </div>
    );
  }

  if (!projectsLoaded || !projectId) {
    return (
      <div className="page state-page">
        <PageState icon="📚" title="正在加载项目…" desc="请稍候" />
        {toastEl}
      </div>
    );
  }

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
          <div className="kb-project">
            <label className="kb-project-label" htmlFor="kbProjectSelect">当前项目</label>
            <select
              id="kbProjectSelect"
              className="kb-project-select"
              value={projectId}
              disabled={busy}
              onChange={(e) => onProjectChange(e.target.value)}
              title="切换项目会刷新知识库目录"
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div className="kb-search"><span className="mag">🔍</span>
            <input
              value={query}
              placeholder="全库搜索文件名…"
              disabled={!!treeErr}
              onChange={(e) => onQueryChange(e.target.value)}
            />
          </div>
          <div className="kb-index-actions">
            <button
              type="button"
              className="mini-btn"
              disabled={indexBusy || !!treeErr}
              title="增量重扫检索索引，正文不动"
              onClick={() => void runIndex("sync")}
            >
              {indexBusy ? "索引同步中…" : "同步索引"}
            </button>
            <button
              type="button"
              className="mini-btn danger"
              disabled={indexBusy || !!treeErr}
              title="清空派生索引后全量重建，正文不动"
              onClick={() => void runIndex("rebuild")}
            >
              {indexBusy ? "索引同步中…" : "全量重建索引"}
            </button>
          </div>
          {searchingView ? (
            <TreeBoxPlaceholder msg={searchErr !== null ? searchErr : "搜索中…"} root={root} />
          ) : treeErr !== null && !query.trim() ? (
            /* 未挂载 / 目录不可用等：友好 detail 直接展示在树盒，保留项目选择器可切换 */
            <TreeBoxPlaceholder msg={treeErr} root={root} onRetry={reloadTree} />
          ) : (
            <KbTreePane
              root={root}
              kids={kids}
              expanded={expanded}
              activeRel={activeRel}
              total={total}
              projectName={currentProject?.name ?? ""}
              searchHits={searchHits}
              onToggleDir={onToggleDir}
              onOpenFile={onOpenFile}
            />
          )}
        </aside>
        <div className="resizer" id="kbResizer" title="拖拽调整目录树宽度"></div>

        {/* Task 9：中栏编辑组件接管 section.kb-main；恢复钮经插槽回传（原型 :709/:718 在同一 kb-head 内） */}
        <KbEditorPane
          doc={doc}
          dirty={dirty}
          root={root}
          headLeading={sideHidden ? <button className="mini-btn" id="btnKbSideShow" title="显示目录树" onClick={() => setSideHidden(false)}>📁 目录</button> : undefined}
          headTrailing={chatHidden ? <button className="mini-btn" id="btnKbChatShow" title="显示知识库助手" onClick={() => setChatHidden(false)}>💬 助手</button> : undefined}
          onMode={onMode}
          onEditContent={onEditContent}
          onSave={onSave}
          onDiscard={onDiscard}
          onReload={onReload}
          onNewNote={openNewNote}
        />

        <div className="resizer" id="kbChatResizer" title="拖拽调整助手宽度"></div>
        {/* Task 10：助手栏整列迁入 KbAssistantPane（head/消息流/快捷指令/输入区，原型 :737-750） */}
        <KbAssistantPane
          messages={msgs}
          busy={busy}
          stopRequested={stopRequested}
          root={root}
          onHide={() => setChatHidden(true)}
          onAsk={(t) => void ask(t)}
          onStop={stopAsk}
          onConfirmDraft={(mi, di, d) => void confirmDraft(mi, di, d)}
          onCancelDraft={cancelDraft}
        />
      </div>
      {/* 新建笔记弹窗：原型 :754-785 #kbNewMask（React 状态驱动条件渲染，类名逐字保留） */}
      {newNoteOpen && (
        <div className="mask" onClick={(e) => { if (e.target === e.currentTarget) setNewNoteOpen(false); }}>
          <div className="modal" role="dialog" aria-modal="true" style={{ width: 520 }}>
            <div className="m-head">＋ 新建知识库笔记<div className="spacer"></div>
              <button className="icon-btn" title="关闭" onClick={() => setNewNoteOpen(false)}>✕</button>
            </div>
            <div className="m-body">
              <div className="field">
                <label htmlFor="kbfTitle">标题 <span className="req">*</span></label>
                <input type="text" id="kbfTitle" autoFocus placeholder="例如：结算金额舍入规则"
                  value={nfTitle} onChange={(e) => onNfTitleChange(e.target.value)} />
              </div>
              <div className="field">
                <label>所在目录 <span className="req">*</span></label>
                <input type="text" id="kbfDir" spellCheck={false} placeholder="test/test_cases"
                  value={nfDir} onChange={(e) => { setNfDir(e.target.value); setNfTip(""); }} />
                {/* 原型 :2524 —— 七桶快捷 chips，选中态 .on 跟随当前目录值 */}
                <div className="kb-bucket" id="kbfBuckets">
                  {KB_BUCKETS.map((b) => (
                    <button key={b} className={`mini-btn${b === nfDir ? " on" : ""}`} onClick={() => pickBucket(b)}>{b}</button>
                  ))}
                </div>
              </div>
              <div className="field">
                <label htmlFor="kbfName">文件名 <span className="req">*</span></label>
                <input type="text" id="kbfName" spellCheck={false} placeholder="自动生成，可修改"
                  value={nfName} onChange={(e) => { setNfName(e.target.value); setNfTip(""); }} />
              </div>
              <div className="field">
                <label htmlFor="kbfDesc">描述</label>
                <input type="text" id="kbfDesc" placeholder="一句话说明这条知识是什么"
                  value={nfDesc} onChange={(e) => { setNfDesc(e.target.value); setNfTip(""); }} />
              </div>
            </div>
            <div className="m-foot">
              {/* 原型 kbNewTip :2532 —— 「已」开头显绿色，其余红色 */}
              <span className="m-tip" style={nfTip ? { color: nfTip.startsWith("已") ? "var(--ok)" : "var(--fail)" } : undefined}>{nfTip}</span>
              <div className="spacer"></div>
              <button className="mini-btn" onClick={() => setNewNoteOpen(false)}>取消</button>
              <button className="btn-primary" style={{ height: 34 }} onClick={() => void createNote()}>创建并写入</button>
            </div>
          </div>
        </div>
      )}
      <div className={`toast${toastMsg ? " show" : ""}`}>{toastMsg}</div>
    </>
  );
}
