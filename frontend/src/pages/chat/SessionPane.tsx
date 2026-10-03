import type { AgentInfo, ChatSession, Project } from "../../api/client";
import { fmtTime, groupSessions } from "./utils";

interface Props {
  projects: Project[];
  projectId: string;
  onProjectChange: (id: string) => void;
  agents: AgentInfo[];
  agentsLoaded: boolean;    // 智能体清单是否已落地：没落地时「该项目未启用智能体」是冤枉话，一句都不能说
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

// 两张 ctx-card 的下拉共用一处定义：同形控件同一套形态（项目/智能体级联）
const selectStyle = {
  width: "100%", border: "none", background: "transparent", font: "inherit", fontWeight: 700,
} as const;

/** 原型 :568-591 侧栏：「当前项目」→「当前智能体」级联，两张 card 同形。 */
export default function SessionPane(p: Props) {
  const agent = p.agents.find((a) => a.id === p.agentId);
  const agentName = agent ? agent.name.replace("智能体", "") : p.agentId;
  const project = p.projects.find((x) => x.id === p.projectId);
  const keyword = p.query.trim().toLowerCase();
  const shown = keyword ? p.sessions.filter((s) => s.title.toLowerCase().includes(keyword)) : p.sessions;
  const groups = groupSessions(shown);

  return (
    <aside className={`sidebar${p.collapsed ? " collapsed" : ""}`}>
      <div className="ctx-card">
        <div className="ctx-label">当前项目 ({p.projects.length})</div>
        {/* busy 期间禁切项目：与切智能体同判据，半途切换会让 activeId 与列表错位 */}
        <select
          className="ctx-value"
          style={selectStyle}
          value={p.projectId}
          disabled={p.busy}
          onChange={(e) => p.onProjectChange(e.target.value)}
          title="切换项目会刷新会话列表"
        >
          {p.projects.map((x) => (
            <option key={x.id} value={x.id}>{x.name}</option>
          ))}
        </select>
      </div>
      <div className="ctx-card">
        <div className="ctx-label">当前智能体 ({p.agents.length})</div>
        {/* busy 期间禁止切智能体：切智能体即切会话域，半途切换会让 activeId 与列表错位 */}
        {/* 用原生 select（可键盘可达、无死控件），内联样式压掉 .ctx-value 的 flex/pointer 卡片态：
            .ctx-value 原为「div + pop 弹层」设计，直接套在 select 上会露出透明背景与光标错位 */}
        <select
          className="ctx-value"
          style={selectStyle}
          value={p.agentId}
          disabled={p.busy}
          onChange={(e) => p.onAgentChange(e.target.value)}
          title="切换智能体会刷新会话列表"
        >
          {p.agents.map((a) => (
            <option key={a.id} value={a.id}>{a.icon} {a.name}</option>
          ))}
        </select>
        {/* 只在清单确实落地、且确实选了项目之后才说「未启用」：agentOptions 在 caps 到位前恒空，
            拉到失败也恒空——那时把用户指向项目页是冤枉 */}
        {p.agentsLoaded && p.projectId && !p.agents.length && (
          <div className="empty-tip">
            该项目未启用可见智能体<br />请到项目页调整
          </div>
        )}
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
            {keyword ? (
              <>无匹配「{p.query.trim()}」的会话<br />换个关键词试试</>
            ) : (
              <>「{[project?.name, agentName].filter(Boolean).join(" · ")}」下暂无会话<br />点击「＋ 新建会话」开始</>
            )}
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
