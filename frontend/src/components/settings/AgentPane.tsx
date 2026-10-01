import { useState } from "react";
import {
  putAgentDefaultModel,
  putAgentTools,
  type CapabilityResponse,
  type ModelsResponse,
} from "../../api/client";

interface AgentPaneProps {
  models: ModelsResponse;
  caps: CapabilityResponse;
  saving: boolean;
  onAction: (action: () => Promise<CapabilityResponse>) => void;
}

function modelLabel(models: ModelsResponse, uid: string): string {
  if (uid === "") return "无";
  const [pid, mid] = uid.split("/");
  const p = models.providers.find((x) => x.id === pid);
  const m = p?.models.find((x) => x.id === mid);
  return p ? `${p.name} / ${m?.name ?? m?.id ?? mid}` : uid;
}

export default function AgentPane({ models, caps, saving, onAction }: AgentPaneProps) {
  const [selectedId, setSelectedId] = useState(caps.agents[0].id);
  const [promptOpen, setPromptOpen] = useState(false);
  const agent = caps.agents.find((a) => a.id === selectedId) ?? caps.agents[0];
  const stale = agent.default_uid !== "" && agent.default_uid !== agent.effective_uid;

  function toggleTool(toolId: string, checked: boolean): void {
    const next = checked ? [...agent.tool_ids, toolId] : agent.tool_ids.filter((t) => t !== toolId);
    onAction(() => putAgentTools(agent.id, next));
  }

  return (
    <>
      <div className="set-left">
        <div className="set-l-head">
          智能体 <span className="num">{caps.agents.length}</span>
        </div>
        <div className="m-list">
          {caps.agents.map((a) => (
            <div
              key={a.id}
              className={a.id === selectedId ? "m-item on" : "m-item"}
              onClick={() => {
                setSelectedId(a.id);
                setPromptOpen(false);
              }}
            >
              <div className="n">
                {a.icon} <span className="nm">{a.name}</span>
                {a.default_uid === "" && <span className="p-tag">跟随默认</span>}
              </div>
              <div className="s">
                {modelLabel(models, a.effective_uid)} · 携带 {a.tool_ids.length} 个工具
              </div>
            </div>
          ))}
        </div>
        <div className="hint" style={{ fontSize: 11.5 }}>
          内置 {caps.agents.length} 个测试智能体，职责与提示词由平台维护。
        </div>
      </div>
      <div className="set-right">
        <div className="sec-title">智能体配置 <span>{agent.name}</span></div>
        <div className="field">
          <label>职责</label>
          <div className="ag-desc">{agent.desc}</div>
        </div>
        <div className="field">
          <label>默认模型</label>
          <select
            value={agent.default_uid}
            disabled={saving}
            onChange={(e) => onAction(() => putAgentDefaultModel(agent.id, e.target.value))}
          >
            <option value="">跟随全局默认（{modelLabel(models, models.default_uid)}）</option>
            {models.providers.flatMap((p) =>
              p.models.map((m) => {
                const uid = `${p.id}/${m.id}`;
                const usable = m.enabled && p.has_key;
                return (
                  <option key={uid} value={uid} disabled={!usable}>
                    {p.name} / {m.name ?? m.id}
                    {usable ? "" : "（不可用）"}
                  </option>
                );
              }),
            )}
          </select>
          <div className="hint">
            新建会话自动带入该默认模型；会话内切换只影响当前会话。不指定则用全局默认 LLM。
          </div>
          {stale && (
            <p className="stale-warn">
              已选的 {agent.default_uid} 当前不可用，实际回落全局默认 {agent.effective_uid || "（无）"}；
              请到 设置 · 模型设置 启用该模型或配置 API Key。
            </p>
          )}
        </div>
        <div className="field">
          <label>
            携带工具 <span className="num">{agent.tool_ids.length}</span>
          </label>
          <div className="agent-opts">
            {caps.tools.map((t) => {
              const carried = t.enabled && agent.tool_ids.includes(t.id);
              const cls = carried ? "on" : t.enabled && t.available ? "" : "dis";
              return (
                <label
                  key={t.id}
                  className={cls === "" ? undefined : cls}
                  title={t.available ? t.desc : `不可用：${t.unavailable_reason}`}
                >
                  <input
                    type="checkbox"
                    checked={carried}
                    disabled={saving || !t.enabled}
                    onChange={(e) => toggleTool(t.id, e.target.checked)}
                  />
                  {t.icon} {t.label}
                  {!t.available && <span className="na">不可用</span>}
                </label>
              );
            })}
          </div>
          <div className="hint">
            只能勾选「工具」分区里已启用的工具；禁用某工具会自动从所有智能体摘掉。
            标「不可用」的工具在 设置 · 工具 里也启用不了（本机缺依赖）。
          </div>
        </div>
        <div className="field">
          <label>系统提示词</label>
          <div className="ro">
            <button
              className="mini-btn"
              onClick={() => setPromptOpen((v) => !v)}
            >
              {promptOpen ? "收起系统提示词" : "查看系统提示词"}
            </button>
            <span className="hint">{promptOpen ? "展开中" : "只读展示，由平台统一维护"}</span>
            <div className="spacer"></div>
            <span className="num">
              {agent.prompt.split(/\r?\n/).length} 行 · {agent.prompt.length} 字
            </span>
          </div>
          <pre className="ag-prompt" hidden={!promptOpen}>{agent.prompt}</pre>
        </div>
      </div>
    </>
  );
}
