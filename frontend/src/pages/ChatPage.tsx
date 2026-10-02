import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import {
  ApiError,
  chatSend,
  deleteChatSession,
  getCapabilities,
  getModels,
  getSessionMessages,
  getSessions,
  type AgentInfo,
  type ChatMessage,
  type ChatSession,
  type HealthResponse,
} from "../api/client";
import Composer from "./chat/Composer";
import MessageList from "./chat/MessageList";
import SessionPane from "./chat/SessionPane";

interface Props {
  health: HealthResponse | null;
  healthError: string | null;
  onOpenSettings: () => void;
}

export default function ChatPage({ health, healthError, onOpenSettings }: Props) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentId, setAgentId] = useState("");
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);            // 与 KbPage 同款同步重入锁（不依赖重渲染时序）
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
      setAgents(caps.agents);
      if (!agentId && caps.agents.length) {
        setAgentId(caps.agents[0].id);
      }
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
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [agentId]);

  const reloadSessions = useCallback(async () => {
    if (!agentId) return [];
    try {
      const j = await getSessions(agentId);
      setSessions(j.sessions);
      return j.sessions;
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err));
      return [];
    }
  }, [agentId, toast]);

  useEffect(() => { void reloadMeta(); }, [reloadMeta]);
  useEffect(() => { void reloadSessions(); }, [reloadSessions]);
  // 卸载清定时器（KbPage/ProjectsPage 同款收尾约定）
  useEffect(() => () => { window.clearTimeout(toastTimer.current); }, []);

  const openSession = useCallback(async (id: string | null) => {
    setActiveId(id);
    if (!id) { setMessages([]); return; }
    try {
      const j = await getSessionMessages(id);
      setMessages(j.messages);
    } catch (err) {
      setMessages([]);
      toast(err instanceof ApiError ? err.message : String(err));
    }
  }, [toast]);

  const guard = useCallback((): boolean => {
    // 无 health 即 /api/health 还没成功过（App 启动时拉），此时发送必失败，先给可见提示
    if (!health) { toast("后端未就绪，请稍候或重试"); return false; }
    if (busyRef.current) { toast("上一条消息还在执行，请稍候"); return false; }
    return true;
  }, [health, toast]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || !guard() || !agentId) return;
    busyRef.current = true;
    setBusy(true);
    // 乐观气泡按对象身份撤，不按文案匹配：同一句话在历史里出现过时，按内容 filter 会把旧的那条一起删掉
    const optimistic: ChatMessage = { role: "user", content: text, ts: Date.now(), steps: null };
    setMessages((prev) => [...prev, optimistic]);
    setInput("");
    try {
      const resp = await chatSend(activeId ?? "", text, agentId);
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
  }, [activeId, agentId, guard, input, reloadSessions]);

  const newSession = useCallback(() => {
    if (!guard()) return;
    setQuery("");
    void openSession(null);
  }, [openSession]);

  const remove = useCallback(async (id: string) => {
    if (!guard()) return;
    const row = sessions.find((s) => s.id === id);
    if (!window.confirm(`删除会话「${row?.title ?? id}」？删除后不可恢复。`)) return;
    try {
      await deleteChatSession(id);
      toast(`已删除会话「${row?.title ?? id}」`);
      const rows = await reloadSessions();
      if (activeId === id) void openSession(rows.length ? rows[0].id : null);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err));
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

  const agent = agents.find((a) => a.id === agentId);

  if (error) {
    return (
      <div className="page">
        <p className="p-empty">加载失败：{error} <button className="mini-btn" onClick={() => void reloadMeta()}>重试</button></p>
      </div>
    );
  }
  if (healthError) {
    return (
      <div className="page">
        <p className="p-empty">后端未就绪：{healthError} <button className="mini-btn" onClick={() => void reloadMeta()}>重试</button></p>
      </div>
    );
  }

  return (
    <div className="layout">
      <SessionPane
        agents={agents}
        agentId={agentId}
        sessions={sessions}
        activeId={activeId}
        query={query}
        busy={busy}
        collapsed={collapsed}
        onAgentChange={(id) => { if (!guard()) return; setAgentId(id); void openSession(null); }}
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
          busy={busy}
          onCopy={(t) => void copy(t)}
          onChip={(t) => {
            // chip 只填值 + 聚焦、绝不自动发送；setInput 要 flushSync 落 DOM 后才能按 Composer 同款算法量出自增高
            flushSync(() => setInput(t));
            const el = inputRef.current;
            if (el) {
              el.focus();
              el.style.height = "auto";
              el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
            }
          }}
        />
        <Composer
          input={input}
          busy={busy}
          modelLabel={modelLabel}
          cap={cap}
          systemPrompt={systemPrompt}
          messages={messages}
          inputRef={inputRef}
          onInput={setInput}
          onSubmit={() => void send()}
          onToast={toast}
        />
      </main>
      {toastMsg && <div className="toast show">{toastMsg}</div>}
    </div>
  );
}
