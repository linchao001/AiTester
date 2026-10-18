import { useMemo } from "react";
import type { ChatSession } from "../../api/client";
import { fmtTime, groupSessions } from "../chat/utils";

export interface KbSessionsPaneProps {
  /** 真实会话列表（按 kb_assistant × 项目取），复用聊天页的分组/搜索。 */
  sessions: ChatSession[];
  activeSessionId: string;
  /** 助手正在回答：期间禁新建/切换/删除，避免把消息写进另一条会话。 */
  busy: boolean;
  sessionQuery: string;
  onSessionQueryChange: (q: string) => void;
  onNewSession: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onHide: () => void;
}

/** 会话历史独立左侧栏（与目录树同构：可拖拽宽度 + » 收起，收起态由主栏头部的「显示」chip 复原）。
    内容从原助手栏内联块迁出，样式沿用全局 .btn-new/.search/.session-list/.group-title/.session。 */
export default function KbSessionsPane({
  sessions, activeSessionId, busy, sessionQuery, onSessionQueryChange,
  onNewSession, onSelectSession, onDeleteSession, onHide,
}: KbSessionsPaneProps) {
  // 搜索过滤 + 按更新时间分组（今天 / 7 天内 / 更早），与聊天页同源
  const sessionGroups = useMemo(() => {
    const keyword = sessionQuery.trim().toLowerCase();
    const shown = keyword ? sessions.filter((s) => s.title.toLowerCase().includes(keyword)) : sessions;
    return groupSessions(shown);
  }, [sessions, sessionQuery]);

  return (
    <aside className="kb-sess-col">
      <div className="kb-side-head">💬 会话历史<span className="num">{sessions.length ? `${sessions.length} 个` : "空"}</span><div className="spacer"></div>
        <button className="icon-btn" title="隐藏会话列表" onClick={onHide}>»</button>
      </div>
      <div className="kb-sess-body">
        <button className="btn-new" disabled={busy} onClick={onNewSession}>＋ 新建会话</button>
        <div className="search">
          <span className="mag">🔍</span>
          <input
            value={sessionQuery}
            placeholder="搜索会话…"
            onChange={(e) => onSessionQueryChange(e.target.value)}
          />
        </div>
        <div className="session-list">
          {!sessionGroups.length && (
            <div className="empty-tip">
              {sessionQuery.trim()
                ? <>无匹配「{sessionQuery.trim()}」的会话<br />换个关键词试试</>
                : <>本项目下暂无会话<br />发送消息即新建，或点「＋ 新建会话」</>}
            </div>
          )}
          {sessionGroups.map((g) => (
            <div key={g.label}>
              <div className="group-title">
                <span>⌄ {g.label}</span><span className="count">{g.items.length}</span>
              </div>
              {g.items.map((s) => (
                <div
                  key={s.id}
                  className={`session${s.id === activeSessionId ? " active" : ""}`}
                  onClick={() => { if (!busy) onSelectSession(s.id); }}
                >
                  <div className="t">
                    <span className="dot" /><span className="tt">{s.title}</span>
                    <button
                      className="s-del"
                      title="删除会话"
                      disabled={busy}
                      onClick={(e) => { e.stopPropagation(); onDeleteSession(s.id); }}
                    >✕</button>
                  </div>
                  <div className="m"><span>{fmtTime(s.updated_at)}</span></div>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
}
