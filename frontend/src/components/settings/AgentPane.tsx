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

export default function AgentPane({ models, caps, saving, onAction }: AgentPaneProps) {
  const [selectedId, setSelectedId] = useState(caps.agents[0].id);
  const [promptOpen, setPromptOpen] = useState(false);
  const agent = caps.agents.find((a) => a.id === selectedId) ?? caps.agents[0];
  const stale = agent.default_uid !== "" && agent.default_uid !== agent.effective_uid;
  const allModels = models.providers.flatMap((p) =>
    p.models.map((m) => ({
      uid: `${p.id}/${m.id}`,
      usable: m.enabled && p.has_key,
    })),
  );
  const globalLabel = models.default_uid === "" ? "未配置" : models.default_uid;

  function toggleTool(toolId: string, checked: boolean): void {
    const next = checked ? [...agent.tool_ids, toolId] : agent.tool_ids.filter((t) => t !== toolId);
    onAction(() => putAgentTools(agent.id, next));
  }

  return (
    <>
      <div className="pane-list">
        {caps.agents.map((a) => (
          <button
            key={a.id}
            className={a.id === selectedId ? "pane-item active" : "pane-item"}
            onClick={() => {
              setSelectedId(a.id);
              setPromptOpen(false);
            }}
          >
            <span>
              {a.icon} {a.name}
              {a.default_uid === "" ? " · 跟随默认" : ""}
            </span>
            <span className="pane-item-sub">
              {a.effective_uid === "" ? "未配置可用模型" : a.effective_uid} · 携带 {a.tool_ids.length} 个工具
            </span>
          </button>
        ))}
        <p className="pane-note">内置智能体由平台统一维护，本期只配置已注册的智能体。</p>
      </div>
      <div className="provider-form">
        <div className="field-row">
          <label>职责</label>
          <p className="field-hint" style={{ flex: 1 }}>{agent.desc}</p>
        </div>
        <div className="field-row">
          <label>默认模型</label>
          <select
            value={agent.default_uid}
            disabled={saving}
            onChange={(e) => onAction(() => putAgentDefaultModel(agent.id, e.target.value))}
          >
            <option value="">跟随全局默认（{globalLabel}）</option>
            {allModels.map((m) => (
              <option key={m.uid} value={m.uid} disabled={!m.usable}>
                {m.uid}
                {m.usable ? "" : "（不可用）"}
              </option>
            ))}
          </select>
        </div>
        <p className="field-hint">新建会话自动带入该默认模型；不指定则用全局默认 LLM。</p>
        {stale && (
          <p className="stale-warn">
            已选的 {agent.default_uid} 当前不可用，实际回落全局默认 {agent.effective_uid || "（无）"}；
            请到 设置 · 模型设置 启用该模型或配置 API Key。
          </p>
        )}
        <div className="field-row" style={{ alignItems: "flex-start" }}>
          <label>携带工具</label>
          <div className="check-opts">
            {caps.tools.map((t) => {
              const carried = agent.tool_ids.includes(t.id);
              return (
                <label key={t.id} className={carried && t.enabled ? "check-opt on" : "check-opt"} title={t.desc}>
                  <input
                    type="checkbox"
                    checked={carried && t.enabled}
                    disabled={saving || !t.enabled}
                    onChange={(e) => toggleTool(t.id, e.target.checked)}
                  />
                  {t.icon} {t.label}
                </label>
              );
            })}
          </div>
        </div>
        <p className="field-hint">
          只能勾选「🛠 工具」里已启用的工具；禁用某工具会自动从所有智能体摘掉，重新启用不自动补回。
        </p>
        <div className="field-row">
          <label>系统提示词</label>
          <button className="btn-secondary" onClick={() => setPromptOpen((v) => !v)}>
            {promptOpen ? "收起系统提示词" : "查看系统提示词"}
          </button>
          <span className="field-hint">
            {promptOpen ? "展开中" : "只读展示，由平台统一维护"}
          </span>
          <span className="field-hint">
            {agent.prompt.split(/\r?\n/).length} 行 · {agent.prompt.length} 字
          </span>
        </div>
        {promptOpen && <pre className="prompt-pre">{agent.prompt}</pre>}
      </div>
    </>
  );
}
