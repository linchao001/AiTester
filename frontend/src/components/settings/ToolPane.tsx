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
  const note = !tool.available
    ? `不可用：${tool.unavailable_reason}`
    : holders.length > 0
      ? `被 ${holders.join("、")} 携带`
      : "未被任何智能体携带";
  return (
    <tr className={tool.available ? undefined : "t-dis"}>
      <td>
        <span className="tname">
          {tool.icon}
          <span className="mid">{tool.label}</span>
        </span>
      </td>
      <td>
        <div className="tdesc">{tool.desc}</div>
        <div className={tool.available ? "mnote" : "mnote warn"}>{note}</div>
      </td>
      <td>
        <span className="cap-tag">{tool.os}</span>
      </td>
      <td>
        {tool.available ? (
          <span className={tool.enabled ? "t-state on" : "t-state off"}>
            {tool.enabled ? "● 已启用" : "○ 已禁用"}
          </span>
        ) : (
          <span className="t-state off" title={tool.unavailable_reason ?? ""}>
            ⚠ 不可用
          </span>
        )}
      </td>
      <td>
        <button
          className={tool.enabled ? "mini-btn danger" : "mini-btn"}
          disabled={saving || !tool.available}
          title={tool.available ? undefined : (tool.unavailable_reason ?? "")}
          onClick={() => onToggle(tool)}
        >
          {tool.enabled ? "禁用" : "启用"}
        </button>
      </td>
    </tr>
  );
}

export default function ToolPane({ caps, saving, onAction }: ToolPaneProps) {
  const [tip, setTip] = useState("");
  const enabledCount = caps.tools.filter((t) => t.enabled).length;
  const unavailable = caps.tools.filter((t) => !t.available).length;

  const groups: { name: string; tools: ToolInfo[] }[] = [];
  caps.tools.forEach((t) => {
    const last = groups[groups.length - 1];
    if (last !== undefined && last.name === t.group) last.tools.push(t);
    else groups.push({ name: t.group, tools: [t] });
  });

  function toggle(tool: ToolInfo): void {
    if (!tool.enabled) {
      onAction(async () => {
        setTip(`已启用 ${tool.label}，可在「智能体配置」里勾选携带。`);
        return putToolEnabled(tool.id, true);
      });
      return;
    }
    const holders = caps.agents.filter((a) => a.tool_ids.includes(tool.id)).map((a) => a.name);
    const message = `禁用「${tool.label}」工具？\n\n将从 ${holders.length} 个智能体摘掉该工具：${holders.join("、") || "无"}`;
    if (!window.confirm(message)) return;
    onAction(async () => {
      setTip(`已禁用 ${tool.label}，相关智能体不再具备该能力。`);
      return putToolEnabled(tool.id, false);
    });
  }

  return (
    <div className="set-right">
      <div className="sec-title">
        内置工具 <span className="num">{enabledCount}/{caps.tools.length}</span>{" "}
        <span className="m-sub">
          禁用后，所有智能体都不会再调用该工具
          {unavailable > 0 && `；另有 ${unavailable} 个工具在本机不可用，只能保持禁用`}
        </span>
      </div>
      <div className="mdl-wrap">
        <table className="mdl-table">
          <thead>
            <tr>
              <th>工具</th>
              <th>说明</th>
              <th style={{ width: 96 }}>平台</th>
              <th style={{ width: 88 }}>状态</th>
              <th style={{ width: 86 }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <Fragment key={g.name}>
                <tr className="tgrp">
                  <td colSpan={5}>🧩 {g.name}</td>
                </tr>
                {g.tools.map((t) => (
                  <ToolRow key={t.id} tool={t} caps={caps} saving={saving} onToggle={toggle} />
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <div className="set-tip">{tip}</div>
    </div>
  );
}
