import type { AgentInfo, ChatSession } from "../../api/client";
import { fmtTime, groupSessions } from "./utils";

interface Props {
  agents: AgentInfo[];
  agentId: string;
  sessions: ChatSession[];
  activeId: string | null;
  query: string;
  busy: boolean;
  collapsed: boolean;
  onAgentChange: (id: string) => void;
  onQueryChange: (q: string) => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onCollapse: () => void;
}

/** 原型 :568-591 侧栏；「当前项目」card 整块不渲染（第 2 片才接项目维度）。 */
export default function SessionPane(p: Props) {
  const agent = p.agents.find((a) => a.id === p.agentId);
  const keyword = p.query.trim().toLowerCase();
  const shown = keyword ? p.sessions.filter((s) => s.title.toLowerCase().includes(keyword)) : p.sessions;
  const groups = groupSessions(shown);

  return (
    <aside className={`sidebar${p.collapsed ? " collapsed" : ""}`}>
      <div className="ctx-card">
        <div className="ctx-label">当前智能体 ({p.agents.length})</div>
        {/* busy 期间禁止切智能体：切智能体即切会话域，半途切换会让 activeId 与列表错位 */}
        {/* 用原生 select（可键盘可达、无死控件），内联样式压掉 .ctx-value 的 flex/pointer 卡片态：
            .ctx-value 原为「div + pop 弹层」设计，直接套在 select 上会露出透明背景与光标错位 */}
        <select
          className="ctx-value"
          style={{ width: "100%", border: "none", background: "transparent", font: "inherit", fontWeight: 700 }}
          value={p.agentId}
          disabled={p.busy}
          onChange={(e) => p.onAgentChange(e.target.value)}
          title="切换智能体会刷新会话列表"
        >
          {p.agents.map((a) => (
            <option key={a.id} value={a.id}>{a.icon} {a.name}</option>
          ))}
        </select>
      </div>
      <div className="side-head">
        <span>💬 会话历史</span>
        <button className="icon-btn" title="隐藏会话列表" disabled={p.busy} onClick={p.onCollapse}>«</button>
      </div>
      <button className="btn-new" disabled={p.busy} onClick={p.onNew}>＋ 新建会话</button>
      <div className="search">
        <span className="mag">🔍</span>
        <input value={p.query} placeholder="搜索会话…" onChange={(e) => p.onQueryChange(e.target.value)} />
      </div>
      <div className="session-list">
        {!groups.length && (
          <div className="empty-tip">
            「{agent ? agent.name.replace("智能体", "") : p.agentId}」下暂无会话<br />点击「＋ 新建会话」开始
          </div>
        )}
        {groups.map((g) => (
          <div key={g.label}>
            <div className="group-title">
              <span>⌄ {g.label}</span><span className="count">{g.items.length}</span>
            </div>
            {g.items.map((s) => (
              <div
                key={s.id}
                className={`session${s.id === p.activeId ? " active" : ""}`}
                onClick={() => p.onSelect(s.id)}
              >
                <div className="t">
                  <span className="dot" /><span className="tt">{s.title}</span>
                  <button
                    className="s-del"
                    title="删除会话"
                    disabled={p.busy}
                    onClick={(e) => { e.stopPropagation(); p.onDelete(s.id); }}
                  >✕</button>
                </div>
                <div className="m"><span>{fmtTime(s.updated_at)}</span></div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </aside>
  );
}
