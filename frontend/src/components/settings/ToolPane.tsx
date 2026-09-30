import { Fragment, useState } from "react";
import { putToolEnabled, type CapabilityResponse, type ToolInfo } from "../../api/client";

interface ToolPaneProps {
  caps: CapabilityResponse;
  saving: boolean;
  onAction: (action: () => Promise<CapabilityResponse>) => void;
}

function ToolRow({
  tool,
  caps,
  saving,
  onToggle,
}: {
  tool: ToolInfo;
  caps: CapabilityResponse;
  saving: boolean;
  onToggle: (tool: ToolInfo) => void;
}) {
  const holders = caps.agents
    .filter((a) => a.tool_ids.includes(tool.id))
    .map((a) => a.name.replace("智能体", ""));
  return (
    <tr>
      <td>
        {tool.icon} {tool.label}
      </td>
      <td>
        <div>{tool.desc}</div>
        <div className="pane-item-sub">
          {holders.length > 0 ? `被 ${holders.join("、")} 携带` : "未被任何智能体携带"}
        </div>
      </td>
      <td>{tool.os}</td>
      <td>
        <span className={tool.enabled ? "tool-state on" : "tool-state off"}>
          {tool.enabled ? "● 已启用" : "○ 已禁用"}
        </span>
      </td>
      <td>
        <button className="btn-secondary" disabled={saving} onClick={() => onToggle(tool)}>
          {tool.enabled ? "禁用" : "启用"}
        </button>
      </td>
    </tr>
  );
}

export default function ToolPane({ caps, saving, onAction }: ToolPaneProps) {
  const [tip, setTip] = useState("");
  const enabledCount = caps.tools.filter((t) => t.enabled).length;

  const groups: { name: string; tools: ToolInfo[] }[] = [];
  caps.tools.forEach((t) => {
    const last = groups[groups.length - 1];
    if (last !== undefined && last.name === t.group) last.tools.push(t);
    else groups.push({ name: t.group, tools: [t] });
  });

  function toggle(tool: ToolInfo): void {
    if (!tool.enabled) {
      onAction(async () => {
        const resp = await putToolEnabled(tool.id, true);
        setTip(`已启用 ${tool.label}，可在「智能体配置」里勾选携带。`);
        return resp;
      });
      return;
    }
    const holders = caps.agents.filter((a) => a.tool_ids.includes(tool.id)).map((a) => a.name);
    const message = `禁用「${tool.label}」工具？\n\n将从 ${holders.length} 个智能体摘掉该工具：${holders.join("、") || "无"}`;
    if (!window.confirm(message)) return;
    onAction(async () => {
      const resp = await putToolEnabled(tool.id, false);
      setTip(`已禁用 ${tool.label}，相关智能体不再具备该能力。`);
      return resp;
    });
  }

  return (
    <div className="provider-form">
      <p className="field-hint">
        内置工具 <strong>{enabledCount}/{caps.tools.length}</strong>　禁用后，所有智能体都不会再调用该工具
      </p>
      <table className="model-table">
        <thead>
          <tr>
            <th>工具</th>
            <th>说明</th>
            <th>平台</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <Fragment key={g.name}>
              <tr className="group-row">
                <td colSpan={5}>🧩 {g.name}</td>
              </tr>
              {g.tools.map((t) => (
                <ToolRow key={t.id} tool={t} caps={caps} saving={saving} onToggle={toggle} />
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
      {tip !== "" && <p className="field-hint">{tip}</p>}
    </div>
  );
}
