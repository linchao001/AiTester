import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError, chatSend, deleteChatSession, getCapabilities, getModels,
  getProjects, getSessionMessages, getSessions,
  type AgentInfo, type ChatMessage, type ChatSession, type HealthResponse, type Project,
} from "../api/client";
import Composer from "./chat/Composer";
import MessageList from "./chat/MessageList";
import SessionPane from "./chat/SessionPane";

/** 视图上下文，不是数据：项目本身已落盘在后端，这里只记「这次打开 /chat 看着哪个」。
 *  换浏览器不带走选择——这是第 2 片裁定 3 的代价，写在注释里免得后来人当 bug 修。 */
const PROJECT_STORAGE_KEY = "aitester.chat.projectId";

interface Props {
  health: HealthResponse | null;
  healthError: string | null;
  onOpenSettings: () => void;
  onRetryHealth: () => void;   // healthError 归 App 持有（App.tsx:21-28），本页不自愈，重试必须打回 App
}

export default function ChatPage({ health, healthError, onOpenSettings, onRetryHealth }: Props) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentId, setAgentId] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectsLoaded, setProjectsLoaded] = useState(false);   // 引导态判据：拉成功且确实为空，区别于「还没拉到」
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);            // 与 KbPage 同款同步重入锁（不依赖重渲染时序）
  const openSeq = useRef(0);                // 开会话的「最新一次点击」序号
  const listSeq = useRef(0);                // 拉整表的「最新一次请求」序号
  const metaOkRef = useRef(false);          // 能力清单是否成功拉到过：决定 meta 失败占满屏还是降级 toast
  const loadingRef = useRef(false);         // 会话正文在途：此时发送会写进另一条会话，必须挡在 guard 之后
  const mutRef = useRef(false);             // 删除等改整表的操作在途：尾部会自动开会话，交叉点就点在别的智能体上
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [modelLabel, setModelLabel] = useState("未配置模型");
  const [cap, setCap] = useState(0);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [error, setError] = useState("");
  const [toastMsg, setToastMsg] = useState("");
  const toastTimer = useRef<number>();

  const toast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(""), 2200);
  }, []);

  // 进页面：能力（智能体清单 + 生效模型）与模型上限一次拉齐，任一失败都要给重试出路
  const reloadMeta = useCallback(async () => {
    try {
      const [caps, models] = await Promise.all([getCapabilities(), getModels()]);
      metaOkRef.current = true;
      setAgents(caps.agents);
      const agent = caps.agents.find((a) => a.id === agentId) || caps.agents[0];
      setSystemPrompt(agent?.prompt || "");
      const uid = agent?.effective_uid || models.default_uid;
      // uid 形态是 "provider/model"（模型专项既有约定），所以要跨 provider 找
      const hit = models.providers
        .flatMap((pv) => pv.models.map((m) => ({ label: m.name || m.id, ctx: m.context, key: `${pv.id}/${m.id}` })))
        .find((x) => x.key === uid);
      setModelLabel(hit ? hit.label : "未配置模型");
      setCap(hit ? hit.ctx : 0);
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
    try {
      const j = await getProjects();
      setProjects(j.projects);
      // 函数式更新：不读 projectId 闭包，避免「切项目」与「拉项目列表」交叉时拿到过期快照
      setProjectId((cur) => {
        if (cur && j.projects.some((p) => p.id === cur)) return cur;
        const saved = window.localStorage.getItem(PROJECT_STORAGE_KEY) || "";
        const hit = j.projects.find((p) => p.id === saved) || j.projects[0];
        return hit ? hit.id : "";
      });
      setProjectsLoaded(true);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err));
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
      // stale 分支仍 return j.sessions：两个触发键都只能在 guard() 之后改（切智能体/切项目互斥，
      // send/retryAll 被 mutRef 挡在 guard() 后），remove 自己的整表刷新又必赢 listSeq，
      // 空交集那一支已在上面的提前 return 处作废在途请求；故调用方拿到的整表只可能属于当前
      // 「智能体 × 项目」。最坏情形也只是页头标题回落成「新会话」，不会串到别的 agent
      return j.sessions;
    } catch (err) {
      if (seq === listSeq.current) toast(err instanceof ApiError ? err.message : String(err));
      return [];
    }
  }, [agentId, projectId, toast]);

  useEffect(() => { void reloadMeta(); }, [reloadMeta]);
  useEffect(() => { void reloadSessions(); }, [reloadSessions]);
  // 卸载清定时器（KbPage/ProjectsPage 同款收尾约定）
  useEffect(() => () => { window.clearTimeout(toastTimer.current); }, []);

  const openSession = useCallback(async (id: string | null) => {
    const seq = ++openSeq.current;                    // 只认最新一次点击，慢响应不得覆盖后点的会话
    loadingRef.current = !!id;                        // 新点击直接接管在途标记：null 代表「没有正文要拉」，否则上一条 stale 请求的 finally 不认它，标记永真
    setActiveId(id);
    if (!id) { setMessages([]); return; }
    try {
      const j = await getSessionMessages(id);
      if (seq !== openSeq.current) return;
      setMessages(j.messages);
    } catch (err) {
      if (seq !== openSeq.current) return;
      // 加载失败就退回「新会话」：留着 activeId 会让页头挂着那条会话的标题、正文却是欢迎态，
      // 此时发送等于悄悄续写那条没加载出来的会话
      setActiveId(null);
      setMessages([]);
      toast(err instanceof ApiError ? err.message : String(err));
    } finally {
      if (seq === openSeq.current) loadingRef.current = false;
    }
  }, [toast]);

  const guard = useCallback((): boolean => {
    // 无 health 即 /api/health 还没成功过（App 启动时拉），此时发送必失败，先给可见提示
    if (!health) { toast("后端未就绪，请稍候或重试"); return false; }
    if (busyRef.current) { toast("上一条消息还在执行，请稍候"); return false; }
    if (loadingRef.current) { toast("会话还在加载，请稍候"); return false; }
    if (mutRef.current) { toast("上一个操作还在执行，请稍候"); return false; }
    return true;
  }, [health, toast]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || !guard() || !agentId || !projectId) return;
    busyRef.current = true;
    setBusy(true);
    // 乐观气泡按对象身份撤，不按文案匹配：同一句话在历史里出现过时，按内容 filter 会把旧的那条一起删掉
    const optimistic: ChatMessage = { role: "user", content: text, ts: Date.now(), steps: null };
    setMessages((prev) => [...prev, optimistic]);
    setInput("");
    try {
      const resp = await chatSend(activeId ?? "", text, agentId, projectId);
      setMessages((prev) => [...prev, {
        role: "assistant", content: resp.reply, ts: Date.now(), steps: resp.steps,
      }]);
      if (resp.session_id !== activeId) setActiveId(resp.session_id);
      const rows = await reloadSessions();
      // 新建会话后标题由服务端定，用返回的 title 就地补齐，避免等整表刷新才可见
      const mine = rows.find((r) => r.id === resp.session_id);
      if (mine && resp.title && mine.title !== resp.title) {
        setSessions((prev) => prev.map((r) => (r.id === mine.id ? { ...r, title: resp.title } : r)));
      }
    } catch (err) {
      // 失败必须可见：撤掉乐观 user 气泡并回填原文，不让用户对着「发出去了却没回」的空框
      setMessages((prev) => prev.filter((m) => m !== optimistic));
      setInput(text);
      toast(err instanceof ApiError ? err.message : String(err));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [activeId, agentId, guard, input, projectId, reloadSessions]);

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
      if (activeId === id) void openSession(rows.length ? rows[0].id : null);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err));
    } finally {
      mutRef.current = false;
    }
  }, [activeId, guard, reloadSessions, sessions, openSession, toast]);

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
    window.localStorage.setItem(PROJECT_STORAGE_KEY, id);
    setProjectId(id);
    setSessions([]);
    void openSession(null);
  }, [guard, openSession]);

  const agent = agentOptions.find((a) => a.id === agentId);

  // healthError 只有 App 的 refreshHealth 成功才会清（App.tsx:20-27），本页自己拉不动它，
  // 所以两个错误态的重试都同时打回 App 与本页，否则后端恢复后仍永久停在「后端未就绪」
  const retryAll = useCallback(() => {
    onRetryHealth();
    void reloadMeta();
    void reloadSessions();   // 失败前 agentId 可能已经有值，effect 不会再触发整表拉取，重试必须把侧栏一起补回来
    void reloadProjects();   // 引导态的「重试」必须能救回项目列表本身，否则列表拉失败会永远卡在「还没有项目」
  }, [onRetryHealth, reloadMeta, reloadSessions, reloadProjects]);

  // toast 是 fixed 定位，三个 return 分支都要挂：错误页的「重试」若只失败在拉整表上，
  // 这条是用户唯一的可见结果，只挂主布局会被提前 return 丢掉
  const toastEl = toastMsg ? <div className="toast show">{toastMsg}</div> : null;

  if (error) {
    return (
      <div className="page">
        <p className="p-empty">加载失败：{error} <button className="mini-btn" onClick={retryAll}>重试</button></p>
        {toastEl}
      </div>
    );
  }
  if (healthError) {
    return (
      <div className="page">
        <p className="p-empty">后端未就绪：{healthError} <button className="mini-btn" onClick={retryAll}>重试</button></p>
        {toastEl}
      </div>
    );
  }
  // projectsLoaded 必须先为真，否则首帧会闪一下「还没有项目」；拉失败（loaded 恒假）走 toast 而不进引导态
  if (projectsLoaded && !projects.length) {
    return (
      <div className="page">
        <p className="p-empty">
          📁 还没有项目<br />
          请先到项目页添加需求文档所在目录，智能体就在那里读写文件
          {" "}<button className="mini-btn" onClick={() => navigate("/projects")}>去项目页</button>
          {" "}<button className="mini-btn" onClick={retryAll}>重试</button>
        </p>
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
          <button className="icon-btn" title="新建会话" disabled={busy} onClick={newSession}>
            💬<sup style={{ color: "var(--primary)", fontWeight: 800 }}>＋</sup>
          </button>
        </div>
        <MessageList
          messages={messages}
          agentName={agent?.name ?? "用例设计智能体"}
          projectName={projects.find((p) => p.id === projectId)?.name ?? ""}
          busy={busy}
          onCopy={(t) => void copy(t)}
          onChip={(t) => {
            // chip 只填值 + 聚焦，绝不自动发送；自增高交给 Composer 按 input 变化统一量
            setInput(t);
            inputRef.current?.focus();
          }}
        />
        <Composer
          input={input}
          busy={busy}
          modelLabel={modelLabel}
          cap={cap}
          systemPrompt={systemPrompt}
          messages={messages}
          projectName={projects.find((p) => p.id === projectId)?.name ?? ""}
          inputRef={inputRef}
          onInput={setInput}
          onSubmit={() => void send()}
          onToast={toast}
        />
      </main>
      {toastEl}
    </div>
  );
}
