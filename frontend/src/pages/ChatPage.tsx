import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError, chatApprove, chatResumeStream, chatSendStream, chatStop, deleteChatSession,
  getCapabilities, getModels, getPending, getProjects, getSessionMessages, getSessions,
  type AgentInfo, type AuthDecision, type ChatMessage, type ChatSession, type ContextSnapshot, type HealthResponse,
  type PermMode, type PendingRunInfo, type Project, type StreamEvent,
} from "../api/client";
import { applyEvent, finalize, held, newStreamState, type StreamingState } from "./chat/streamState";
import { loadPermMode, savePermMode } from "./chat/utils";
import PageState from "../components/PageState";
import Composer from "./chat/Composer";
import MessageList from "./chat/MessageList";
import SessionPane from "./chat/SessionPane";
import WorkspacePane from "./chat/WorkspacePane";

/** 视图上下文，不是数据：项目本身已落盘在后端，这里只记「这次打开 /chat 看着哪个」。
 *  换浏览器不带走选择——这是第 2 片裁定 3 的代价，写在注释里免得后来人当 bug 修。 */
const PROJECT_STORAGE_KEY = "aitester.chat.projectId";

interface Props {
  health: HealthResponse | null;
  healthError: string | null;
  onOpenSettings: () => void;
  onRetryHealth: () => void;   // healthError 归 App 持有（App.tsx:23-30），本页不自愈，重试必须打回 App
}

export default function ChatPage({ health, healthError, onOpenSettings, onRetryHealth }: Props) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentId, setAgentId] = useState("");
  const [agentsLoaded, setAgentsLoaded] = useState(false);      // 智能体清单是否已落地：未落地时「没有可用智能体」这类话一句都不能说
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectsLoaded, setProjectsLoaded] = useState(false);   // 引导态判据：拉成功且确实为空，区别于「还没拉到」
  const [projectsError, setProjectsError] = useState("");         // 项目列表拉失败的讯息：toast 会过期，不可重试的失败必须有状态兜住
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);            // 与 KbPage 同款同步重入锁（不依赖重渲染时序）
  const [live, setLive] = useState<StreamingState | null>(null);
  const liveRef = useRef<StreamingState | null>(null);   // 终态折叠要同步读到最后一帧
  const [stopRequested, setStopRequested] = useState(false);
  const stopRequestedRef = useRef(false);
  const runIdRef = useRef("");
  const abortRef = useRef<AbortController | null>(null);
  const openSeq = useRef(0);                // 开会话的「最新一次点击」序号
  const listSeq = useRef(0);                // 拉整表的「最新一次请求」序号
  const projSeq = useRef(0);                // 拉项目列表的「最新一次请求」序号：迟到的成败都不得盖过更新的一轮
  const metaOkRef = useRef(false);          // 能力清单是否成功拉到过：决定 meta 失败占满屏还是降级 toast
  const loadingRef = useRef(false);         // 会话正文在途：此时发送会写进另一条会话，必须挡在 guard 之后
  const mutRef = useRef(false);             // 删除等改整表的操作在途：尾部会自动开会话，交叉点就点在别的智能体上
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [wsCollapsed, setWsCollapsed] = useState(false);   // 工作区收起（视图态，不落盘）
  const [wsSeq, setWsSeq] = useState(0);                   // 发送成功后 +1：工作区静默重拉
  const wsDirtyRef = useRef(false);                        // 工作区脏文档：切项目前问一句
  const [query, setQuery] = useState("");
  const [modelLabel, setModelLabel] = useState("未配置模型");
  // 上下文读数只认后端快照：null 代表「这一回合没有读数」，界面显示「—」而不是 0%（CM-3）
  const [ctxSnap, setCtxSnap] = useState<ContextSnapshot | null>(null);
  const [error, setError] = useState("");
  const [permMode, setPermMode] = useState<PermMode>(loadPermMode);
  const [pendingRuns, setPendingRuns] = useState<PendingRunInfo[]>([]);
  const pendingSeq = useRef(0);              // 待批表的「最新一次请求」序号：迟到的整表不得盖回来
  const [resumeRunId, setResumeRunId] = useState("");   // 续跑在途的那条 run：它的卡先摘掉，改由 live 气泡画
  const [toastMsg, setToastMsg] = useState("");
  const toastTimer = useRef<number>();

  const toast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(""), 2200);
  }, []);

  const onWsDirty = useCallback((d: boolean) => { wsDirtyRef.current = d; }, []);

  // 进页面：能力（智能体清单 + 生效模型）与模型上限一次拉齐，任一失败都要给重试出路
  const reloadMeta = useCallback(async () => {
    try {
      const [caps, models] = await Promise.all([getCapabilities(), getModels()]);
      metaOkRef.current = true;
      setAgents(caps.agents);
      setAgentsLoaded(true);      // 清单落地才允许对用户断言「该项目没有可用智能体」
      const agent = caps.agents.find((a) => a.id === agentId) || caps.agents[0];
      const uid = agent?.effective_uid || models.default_uid;
      // uid 形态是 "provider/model"（模型专项既有约定），所以要跨 provider 找
      const hit = models.providers
        .flatMap((pv) => pv.models.map((m) => ({ label: m.name || m.id, key: `${pv.id}/${m.id}` })))
        .find((x) => x.key === uid);
      setModelLabel(hit ? hit.label : "未配置模型");
      setError("");
    } catch (err) {
      // 迟到的旧失败不该把已经可用的页面整页打回错误页：只有还没成功拉到过能力清单
      // （页面还什么都不知道）时才占满屏错误，否则降级成 toast，已渲染的会话/输入照常可用。
      // 判据走 ref 不走 agents.length：deps 里的 agentId 一变才重建本回调，agents 读到的会是过期快照
      // （首次成功后的清单还没进闭包就把失败当成「白屏」）；把 agents 加进 deps 又会让每次成功都重拉一遍
      const msg = err instanceof Error ? err.message : String(err);
      if (!metaOkRef.current) setError(msg);
      else toast(msg);
    }
  }, [agentId]);

  const reloadProjects = useCallback(async () => {
    const seq = ++projSeq.current;
    try {
      const j = await getProjects();
      if (seq !== projSeq.current) return;      // 迟到的成功包不得盖过更新的一轮（retryAll 与挂载可交叉）
      setProjects(j.projects);
      // 函数式更新：不读 projectId 闭包，避免「切项目」与「拉项目列表」交叉时拿到过期快照
      setProjectId((cur) => {
        if (cur && j.projects.some((p) => p.id === cur)) return cur;
        const saved = window.localStorage.getItem(PROJECT_STORAGE_KEY) || "";
        const hit = j.projects.find((p) => p.id === saved) || j.projects[0];
        // saved 已不存在（项目被删）时要把真正落点写回去，否则死键一直留在 localStorage
        if (!hit) window.localStorage.removeItem(PROJECT_STORAGE_KEY);
        else if (hit.id !== saved) window.localStorage.setItem(PROJECT_STORAGE_KEY, hit.id);
        return hit ? hit.id : "";
      });
      setProjectsLoaded(true);
      setProjectsError("");        // 成功即清：错误页只在「最近一次拉取失败」时占屏
    } catch (err) {
      // 迟到的失败同样作废：否则旧的拒绝会把已经拉健康的页面打回错误态（本文件其余在途请求都这条判据）
      if (seq !== projSeq.current) return;
      const msg = err instanceof ApiError ? err.message : String(err);
      setProjectsError(msg);       // 只 toast 会把页面永久卡在「0 个选项 + 无项目名」的死态，必须留下可重试的错误态
      toast(msg);
    }
  }, [toast]);

  useEffect(() => { void reloadProjects(); }, [reloadProjects]);

  // 级联：智能体下拉只给「当前项目启用的」∩「可见目录里的」，顺序按 caps 原序（两套序会漂，认一份）
  const agentOptions = useMemo(() => {
    const proj = projects.find((p) => p.id === projectId);
    if (!proj) return agents;
    const allowed = new Set(proj.agents);
    return agents.filter((a) => allowed.has(a.id));
  }, [agents, projects, projectId]);

  useEffect(() => {
    if (!agentOptions.length) {
      if (agentId) setAgentId("");
      return;
    }
    if (!agentOptions.some((a) => a.id === agentId)) setAgentId(agentOptions[0].id);
  }, [agentOptions, agentId]);

  const reloadSessions = useCallback(async () => {
    if (!agentId || !projectId) {
      // 拉表由 agentId 与 projectId 共同触发：切项目时旧 agentId 会先带着新项目发一次请求，
      // 级联 effect 随后才把 agentId 收敛成 ""。这条提前 return 也必须先 bump 序号作废在途响应，
      // 否则迟到的「旧智能体 × 新项目」整表会认自己的 seq 仍是最新的而粘进侧栏（初始挂载 bump 无害）。
      ++listSeq.current;
      return [];
    }
    const seq = ++listSeq.current;          // 切智能体/切项目都会先清列表；慢响应不得把上一轮的整表覆盖回来
    try {
      const j = await getSessions(agentId, projectId);
      if (seq === listSeq.current) setSessions(j.sessions);
      // stale 分支仍 return j.sessions：取返回值的只有 send 与 remove，两者都在 busyRef/mutRef 锁内，
      // 用户切智能体/切项目被 guard() 挡在门外；两个键也会被无锁改到（级联 effect 改 agentId、
      // reloadProjects 写 projectId），但每次改键都让本回调重跑并 bump listSeq，setSessions 只认最新一次；
      // remove 自己的整表刷新又必赢 listSeq，空交集那一支已在上面的提前 return 处作废在途请求。
      // 故调用方拿到的整表只可能属于当前「智能体 × 项目」，最坏情形也只是页头标题回落成「新会话」，不会串到别的 agent
      return j.sessions;
    } catch (err) {
      if (seq === listSeq.current) {
        // 刷新失败必须清列表：留着上一个项目的行挂在新项目名下，点得开、发出去就是 404
        setSessions([]);
        toast(err instanceof ApiError ? err.message : String(err));
      }
      return [];
    }
  }, [agentId, projectId, toast]);

  const reloadPending = useCallback(async () => {
    if (!agentId || !projectId) {           // 没得查就先清：留着上一个项目的卡是假 affordance
      ++pendingSeq.current;
      setPendingRuns([]);
      return;
    }
    const seq = ++pendingSeq.current;
    try {
      const j = await getPending(agentId, projectId);
      if (seq === pendingSeq.current) setPendingRuns(j.runs);
    } catch {
      // 取不到就当没有：卡片宁可少画一张，也不画一条早已结束的（后端本就重启即丢）
      if (seq === pendingSeq.current) setPendingRuns([]);
    }
  }, [agentId, projectId]);

  useEffect(() => { void reloadPending(); }, [reloadPending]);

  useEffect(() => { void reloadMeta(); }, [reloadMeta]);
  useEffect(() => { void reloadSessions(); }, [reloadSessions]);

  const openSession = useCallback(async (id: string | null) => {
    const seq = ++openSeq.current;                    // 只认最新一次点击，慢响应不得覆盖后点的会话
    loadingRef.current = !!id;                        // 新点击直接接管在途标记：null 代表「没有正文要拉」，否则上一条 stale 请求的 finally 不认它，标记永真
    setActiveId(id);
    if (!id) { setMessages([]); setCtxSnap(null); return; }
    try {
      const j = await getSessionMessages(id);
      if (seq !== openSeq.current) return;
      setMessages(j.messages);
      // meter 跟的是「这条会话最后一次的读数」：倒找第一条 assistant 行，老 jsonl 行没有该节 ⇒ 「—」
      const last = [...j.messages].reverse().find((m) => m.role === "assistant");
      setCtxSnap(last?.context ?? null);
    } catch (err) {
      if (seq !== openSeq.current) return;
      // 加载失败就退回「新会话」：留着 activeId 会让页头挂着那条会话的标题、正文却是欢迎态，
      // 此时发送等于悄悄续写那条没加载出来的会话
      setActiveId(null);
      setMessages([]);
      setCtxSnap(null);
      toast(err instanceof ApiError ? err.message : String(err));
    } finally {
      if (seq === openSeq.current) loadingRef.current = false;
    }
  }, [toast]);

  const guard = useCallback((): boolean => {
    // 无 health 即 /api/health 还没成功过（App 会重试到成功），此时发送必失败，先给可见提示
    if (!health) { toast("后端未就绪，请稍候或重试"); return false; }
    if (busyRef.current) { toast("上一条消息还在执行，请稍候"); return false; }
    if (loadingRef.current) { toast("会话还在加载，请稍候"); return false; }
    if (mutRef.current) { toast("上一个操作还在执行，请稍候"); return false; }
    return true;
  }, [health, toast]);

  /** 一条流式请求（首回合与续跑）共用的收尾：挂起 / 完成 / 失败三选一，busy 一定落地。
   *  抽出来是因为续跑的收尾与首回合逐字同形——抄两份必漂，第 2 片「守门只有一段」的同一条判据。 */
  const drain = useCallback(async (opts: {
    optimistic: ChatMessage | null;   // send 的乐观 user 行；续跑没有这一行
    text: string;                     // 失败时回填输入框的原文；续跑传 ""
    aborted: boolean;
    errMsg: string;
  }) => {
    // 换页/卸载导致的 abort：连接已断，后端走 GeneratorExit 落截断盘，本页不再改任何 state
    if (opts.aborted) return;
    abortRef.current = null;
    busyRef.current = false;
    setBusy(false);
    setStopRequested(false);
    setResumeRunId("");
    runIdRef.current = "";
    void reloadPending();               // 三条出路都要重取表：尾巴上可能还挂着下一条 wait（R13）
    const st = liveRef.current;
    const done = st ? st.done : null;
    if (st && held(st)) {
      // 挂起（裁定 7/8）：流在 wait 之后断掉、一条不落。live 让位给 pending 气泡，
      // 乐观 user 行留着——它和那张卡说的是同一件事，撤掉它就是「发出去却没回」的空框
      liveRef.current = null;
      setLive(null);
      // 续跑段再挂起时，先前批准过的调用可能已经写盘：工作区树当场跟一次，不等最终 done
      setWsSeq((n) => n + 1);
      return;
    }
    let errMsg = opts.errMsg;
    if (!done && !errMsg) errMsg = st?.fail || "连接中断，本条回答未完成";
    if (done && st) {
      const f = finalize(st, done);
      liveRef.current = null;
      setLive(null);
      setCtxSnap(f.context ?? null);
      setMessages((prev) => [...prev, {
        role: "assistant", content: f.content, ts: Date.now(), steps: f.steps, stopped: f.stopped,
        context: f.context ?? null,
      }]);
      if (f.sessionId !== activeId) setActiveId(f.sessionId);
      setWsSeq((n) => n + 1);            // 模型可能刚写了产出物：工作区树静默重拉（被停止也照拉）
      const rows = await reloadSessions();
      // 新建会话后标题由服务端定，用返回的 title 就地补齐，避免等整表刷新才可见
      const mine = rows.find((r) => r.id === f.sessionId);
      if (mine && f.title && mine.title !== f.title) {
        setSessions((prev) => prev.map((r) => (r.id === mine.id ? { ...r, title: f.title } : r)));
      }
      return;
    }
    // 失败必须可见：撤掉乐观 user 气泡并回填原文，不让用户对着「发出去了却没回」的空框
    liveRef.current = null;
    setLive(null);
    if (opts.optimistic) {
      setMessages((prev) => prev.filter((m) => m !== opts.optimistic));
      setInput(opts.text);
    }
    toast(errMsg);
  }, [activeId, reloadPending, reloadSessions, toast]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || !guard() || !agentId || !projectId) return;
    busyRef.current = true;
    setBusy(true);
    // 乐观气泡按对象身份撤，不按文案匹配：同一句话在历史里出现过时，按内容 filter 会把旧的那条一起删掉
    const optimistic: ChatMessage = { role: "user", content: text, ts: Date.now(), steps: null };
    setMessages((prev) => [...prev, optimistic]);
    setInput("");
    liveRef.current = newStreamState();
    setLive(liveRef.current);
    runIdRef.current = "";
    stopRequestedRef.current = false;
    setStopRequested(false);
    const controller = new AbortController();
    abortRef.current = controller;
    let errMsg = "";                        // 只装「非事件」的失败（HTTP 守门 / 断流），终态一律从 state 读
    const onEvent = (ev: StreamEvent) => {
      if (ev.type === "start") runIdRef.current = ev.run_id;
      const cur = liveRef.current ?? newStreamState();
      liveRef.current = applyEvent(cur, ev);   // done/error 也照折：终态与 detail 都随状态回给下面
      setLive(liveRef.current);
    };
    let aborted = false;
    try {
      await chatSendStream(
        { session_id: activeId ?? "", message: text, agent_id: agentId,
          project_id: projectId,
          ...(permMode === "free" ? {} : { perm_mode: permMode }) },
        onEvent, controller.signal);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") aborted = true;
      // 非 ApiError 的断流（如网络 TypeError）不给英文原文，中文兜底
      else errMsg = err instanceof ApiError ? err.message : "连接中断，本条回答未完成";
    }
    await drain({ optimistic, text, aborted, errMsg });
  // 依赖里故意不列 activeId：它经 drain 进来（drain 自己列了 activeId）。摘掉 drain 那条依赖，这条回答就会写进上一个会话。
  }, [agentId, drain, guard, input, permMode, projectId]);

  const stop = useCallback(() => {
    if (stopRequestedRef.current) return;
    const rid = runIdRef.current;
    // start 事件还没到时没有 run_id 可停：给一句可见反馈，而不是让按钮空转成死控件
    if (!rid) { toast("还在建立连接，请稍候"); return; }
    stopRequestedRef.current = true;
    setStopRequested(true);
    chatStop(rid).catch((err: unknown) => {
      // 404「这条回答已经结束」是与终态并发的正常竞态：界面随后自己收到 done，这里只把原因说出来
      // 非 ApiError（网络层 TypeError）不给英文原文，中文兜底
      toast(err instanceof ApiError ? err.message : "停止请求失败，本条回答可能仍在继续");
    });
  }, [toast]);

  /** 批准或拒绝一条待批：登记决策后立刻另起一条流续跑（拒绝也要跑，模型得收到拒绝继续作答）。
   *  两步分开做才收敛重复提交：approve 失败根本不发起续跑。 */
  const decide = useCallback(async (runId: string, callId: string,
    decision: AuthDecision, remember: boolean) => {
    if (resumeRunId) { toast("这条回答正在续跑，请稍候"); return; }
    if (!health) { toast("后端未就绪，请稍候或重试"); return; }
    if (busyRef.current || loadingRef.current || mutRef.current) {
      toast("上一条操作还在执行，请稍候"); return;
    }
    // 占位必须在登记之前同步落下：resumeRunId 要等 approve 成功才置位，靠它挡不住一个 RTT 内的第二次点击
    busyRef.current = true;
    try {
      await chatApprove(runId, callId, decision, remember);
    } catch (err) {
      busyRef.current = false;
      // 404「这条回答已经结束」/ 409「这条已经答过了」都是真相：重取表让卡片按后端的样子消失
      toast(err instanceof ApiError ? err.message : "授权登记失败，请重试");
      void reloadPending();
      return;
    }
    setBusy(true);
    setResumeRunId(runId);
    stopRequestedRef.current = false;
    setStopRequested(false);
    runIdRef.current = runId;               // 续跑沿用同一条 run：停止钮打的还是它（P4）
    liveRef.current = newStreamState();
    setLive(liveRef.current);
    const controller = new AbortController();
    abortRef.current = controller;
    const onEvent = (ev: StreamEvent) => {
      const cur = liveRef.current ?? newStreamState();
      liveRef.current = applyEvent(cur, ev);
      setLive(liveRef.current);
    };
    let aborted = false;
    let errMsg = "";
    try {
      await chatResumeStream(runId, onEvent, controller.signal);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") aborted = true;
      else errMsg = err instanceof ApiError ? err.message : "连接中断，本条回答未完成";
    }
    await drain({ optimistic: null, text: "", aborted, errMsg });
  }, [busyRef, drain, health, reloadPending, resumeRunId, toast]);

  /** 待批期间的停止（裁定 10 第二条）：后端 cancel_pending 是挂起期唯一一次落盘，
   *  那条会话可能第一次出现，正看着它的用户必须重开才看得见截断行。 */
  const stopPending = useCallback(async (run: PendingRunInfo) => {
    try {
      await chatStop(run.run_id);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "停止请求失败，这条回答可能还挂着");
      void reloadPending();
      return;
    }
    toast("已停止这条回答：已生成的部分留在会话里");
    await reloadPending();
    const rows = await reloadSessions();
    if (rows.some((s) => s.id === run.session_id)
      && (activeId === run.session_id || activeId === null)) void openSession(run.session_id);
  }, [activeId, openSession, reloadPending, reloadSessions, toast]);

  const onPermMode = useCallback((m: PermMode) => {
    if (m === permMode) return;
    setPermMode(m);
    savePermMode(m);
    // 文案取原型 :1473 逐字（free 说清「自动执行」，另两档不给长句）
    toast(m === "free" ? "已切换为「自由权限」：操作无需你授权，自动执行" : "已切换权限模式");
  }, [permMode, toast]);

  // 卸载清定时器（KbPage/ProjectsPage 同款收尾约定）并断流
  useEffect(() => () => {
    window.clearTimeout(toastTimer.current);
    abortRef.current?.abort();   // 离开页面即断流：后端收 GeneratorExit，落截断行并标 stopped
  }, []);

  const newSession = useCallback(() => {
    if (!guard()) return;
    setQuery("");
    void openSession(null);
  }, [openSession]);

  const remove = useCallback(async (id: string) => {
    if (!guard()) return;
    const row = sessions.find((s) => s.id === id);
    if (!window.confirm(`删除会话「${row?.title ?? id}」？删除后不可恢复。`)) return;
    mutRef.current = true;                 // 两次 await 期间锁住切智能体/选中：尾部会自动开会话，交叉了就把正文开在别的智能体的会话上
    try {
      await deleteChatSession(id);
      // 先就地摘掉这一行：整表刷新失败时侧栏也不会留着一条已删的会话
      setSessions((prev) => prev.filter((s) => s.id !== id));
      toast(`已删除会话「${row?.title ?? id}」`);
      const rows = await reloadSessions();
      void reloadPending();            // 删会话的级联在后端做了：待批表不跟着重取就留下一张点不动的假卡
      if (activeId === id) void openSession(rows.length ? rows[0].id : null);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err));
    } finally {
      mutRef.current = false;
    }
  }, [activeId, guard, reloadPending, reloadSessions, sessions, openSession, toast]);

  const copy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制到剪贴板");
    } catch {
      toast("复制失败：浏览器未授权访问剪贴板");
    }
  }, [toast]);

  // 切项目与切智能体同构：guard → 清列表 → 回欢迎态；列表由 reloadSessions 的 effect 按新项目重拉
  const onProjectChange = useCallback((id: string) => {
    if (!guard()) return;
    // 工作区有脏文件时先问：确认才切（取消即早退，受控 select 会停在原项目）
    if (wsDirtyRef.current && !window.confirm("工作区有未保存的文件修改，切换项目将丢弃它们。继续？")) return;
    window.localStorage.setItem(PROJECT_STORAGE_KEY, id);
    setProjectId(id);
    setSessions([]);
    void openSession(null);
  }, [guard, openSession]);

  const agent = agentOptions.find((a) => a.id === agentId);
  const currentProject = projects.find((p) => p.id === projectId);

  // 发送阻塞由状态的唯一持有者算清楚再下传：send() 在 !agentId || !projectId 时是静默 return 的，
  // 按钮必须跟着一起哑下来并把原因写进 title，否则就是一只点不动的死按钮（UI 约定：0 个死按钮）
  const sendBlock = !agentsLoaded
    ? "智能体清单加载中，请稍候"
    : !agentId
      ? "该项目未启用可见智能体，请到项目页启用后再发送"
      : !projectId
        ? "尚未选择项目，请先在左侧「当前项目」里选一个"
        : "";

  // healthError 只有 App 的 refreshHealth 成功才会清（App.tsx:20-27），本页自己拉不动它，
  // 所以两个错误态的重试都同时打回 App 与本页，否则后端恢复后仍永久停在「后端未就绪」
  const retryAll = useCallback(() => {
    onRetryHealth();
    void reloadMeta();
    void reloadSessions();   // 失败前 agentId 可能已经有值，effect 不会再触发整表拉取，重试必须把侧栏一起补回来
    void reloadProjects();   // 引导态的「重试」必须能救回项目列表本身，否则列表拉失败会永远卡在「还没有项目」
  }, [onRetryHealth, reloadMeta, reloadSessions, reloadProjects]);

  // toast 是 fixed 定位，每个提前 return 的分支都要挂：错误页的「重试」若只失败在拉整表上，
  // 这条是用户唯一的可见结果，只挂主布局会被提前 return 丢掉
  const toastEl = toastMsg ? <div className="toast show">{toastMsg}</div> : null;

  if (error) {
    return (
      <div className="page state-page">
        <PageState
          icon="⚠️"
          title="聊天页加载失败"
          desc={error}
          actions={<button className="btn-primary" onClick={retryAll}>↻ 重试</button>}
        />
        {toastEl}
      </div>
    );
  }
  if (healthError) {
    return (
      <div className="page state-page">
        <PageState
          icon="🔌"
          title="后端未就绪"
          desc={healthError}
          actions={<button className="btn-primary" onClick={retryAll}>↻ 重试</button>}
        />
        {toastEl}
      </div>
    );
  }
  // projectsLoaded 必须先为真，否则首帧会闪一下「还没有项目」；拉失败（loaded 恒假）不进引导态，
  // 由下面的「项目列表加载失败」错误页接手
  if (projectsLoaded && !projects.length) {
    return (
      <div className="page state-page">
        <PageState
          icon="📁"
          title="还没有项目"
          desc="智能体要在项目目录里读写需求文档与产出物。先到项目页添加一个目录，再回来开始聊天。"
          actions={
            <>
              <button className="btn-primary" onClick={() => navigate("/projects")}>去项目页</button>
              <button className="mini-btn" onClick={retryAll}>↻ 重试</button>
            </>
          }
        />
        {toastEl}
      </div>
    );
  }

  // 项目列表拉失败：这页没有项目可选、发送也会静默 return，必须占屏给出路（toast 2.2s 就没了）。
  // 放在引导态之后：真正「拉成功且为空」永远先显示「还没有项目」的指路，错误页不遮蔽它
  if (projectsError) {
    return (
      <div className="page state-page">
        <PageState
          icon="⚠️"
          title="项目列表加载失败"
          desc={projectsError}
          actions={<button className="btn-primary" onClick={retryAll}>↻ 重试</button>}
        />
        {toastEl}
      </div>
    );
  }

  return (
    <div className="layout">
      <SessionPane
        projects={projects}
        projectId={projectId}
        agents={agentOptions}
        agentsLoaded={agentsLoaded}
        agentId={agentId}
        sessions={sessions}
        activeId={activeId}
        query={query}
        busy={busy}
        collapsed={collapsed}
        onProjectChange={onProjectChange}
        onAgentChange={(id) => { if (!guard()) return; setAgentId(id); setSessions([]); void openSession(null); }}
        onQueryChange={setQuery}
        onNew={newSession}
        onSelect={(id) => { if (!guard()) return; void openSession(id); }}
        onDelete={(id) => void remove(id)}
        onCollapse={() => setCollapsed(true)}
      />
      <main className="chat">
        <div className="chat-header">
          <button
            className="icon-btn"
            title={collapsed ? "显示会话列表" : "隐藏会话列表"}
            style={collapsed ? { color: "var(--primary-deep)" } : undefined}
            onClick={() => setCollapsed((v) => !v)}
          >☰</button>
          <span className="chat-title">{activeId ? (sessions.find((s) => s.id === activeId)?.title ?? "新会话") : "新会话"}</span>
          <button className="model-chip" title="会话级模型切换本期未开放，设置中改默认模型" onClick={onOpenSettings}>
            <span className="m-dot" />{modelLabel} ▾
          </button>
          <div className="spacer" />
          {wsCollapsed && currentProject && (
            <button className="mini-btn" title="显示工作区" onClick={() => setWsCollapsed(false)}>📁 工作区</button>
          )}
          <button className="icon-btn" title="新建会话" disabled={busy} onClick={newSession}>
            💬<sup style={{ color: "var(--primary)", fontWeight: 800 }}>＋</sup>
          </button>
        </div>
        <MessageList
          messages={messages}
          agentName={agent?.name ?? "用例设计智能体"}
          projectName={currentProject?.name ?? ""}
          busy={busy}
          live={live}
          pending={resumeRunId ? pendingRuns.filter((r) => r.run_id !== resumeRunId) : pendingRuns}
          resumeBusy={!!resumeRunId}
          onCopy={(t) => void copy(t)}
          onDecide={(runId, callId, decision, remember) => void decide(runId, callId, decision, remember)}
          onStopPending={(run) => void stopPending(run)}
          onChip={(t) => {
            // chip 只填值 + 聚焦，绝不自动发送；自增高交给 Composer 按 input 变化统一量
            setInput(t);
            inputRef.current?.focus();
          }}
        />
        <Composer
          input={input}
          busy={busy}
          usage={ctxSnap}
          projectName={currentProject?.name ?? ""}
          permMode={permMode}
          permLocked={pendingRuns.length > 0}
          onPermMode={onPermMode}
          sendBlock={sendBlock}
          inputRef={inputRef}
          stopRequested={stopRequested}
          onStop={stop}
          onInput={setInput}
          onSubmit={() => void send()}
        />
      </main>
      {currentProject && (
        <WorkspacePane
          key={currentProject.id}
          project={currentProject}
          collapsed={wsCollapsed}
          refreshSeq={wsSeq}
          onCollapse={() => setWsCollapsed(true)}
          onToast={toast}
          onDirtyChange={onWsDirty}
        />
      )}
      {toastEl}
    </div>
  );
}
