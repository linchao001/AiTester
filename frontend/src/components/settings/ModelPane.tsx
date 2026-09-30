import { useState } from "react";
import {
  putApiKey,
  putDefault,
  putModelEnabled,
  type ModelsResponse,
} from "../../api/client";

interface ModelPaneProps {
  models: ModelsResponse;
  saving: boolean;
  onAction: (action: () => Promise<ModelsResponse>) => void;
}

export default function ModelPane({ models, saving, onAction }: ModelPaneProps) {
  const [selectedId, setSelectedId] = useState(models.providers[0].id);
  const [keyInput, setKeyInput] = useState("");
  const selected =
    models.providers.find((p) => p.id === selectedId) ?? models.providers[0];

  return (
    <>
      <div className="provider-list">
        {models.providers.map((p) => (
          <button
            key={p.id}
            className={p.id === selectedId ? "provider-item active" : "provider-item"}
            onClick={() => {
              setSelectedId(p.id);
              setKeyInput("");
            }}
          >
            <span>{p.name}</span>
            <span className="provider-state">
              {p.has_key ? "● Key 已配置" : "○ Key 未配置"}
            </span>
          </button>
        ))}
      </div>
      <div className="provider-form">
        <div className="field-row">
          <label>Base URL</label>
          <input className="text-input" value={selected.base_url} readOnly />
        </div>
        <div className="field-row">
          <label>API Key</label>
          <input
            className="text-input"
            type="password"
            value={keyInput}
            placeholder={
              selected.has_key
                ? `当前 ${selected.key_masked}（已配置，留空则不变）`
                : "尚未配置，粘贴 API Key"
            }
            onChange={(e) => setKeyInput(e.target.value)}
          />
          <button
            className="btn-primary"
            disabled={keyInput === "" || saving}
            onClick={() =>
              onAction(async () => {
                const resp = await putApiKey(selected.id, keyInput);
                setKeyInput("");
                return resp;
              })
            }
          >
            保存 Key
          </button>
          {selected.has_key && (
            <button
              className="btn-secondary"
              disabled={saving}
              onClick={() => onAction(() => putApiKey(selected.id, ""))}
            >
              清除 Key
            </button>
          )}
        </div>
        <div className="field-row">
          <label>默认 LLM</label>
          <select
            value={models.default_uid}
            disabled={saving}
            onChange={(e) => onAction(() => putDefault(e.target.value))}
          >
            <option value="" disabled>未配置默认模型</option>
            {models.providers.flatMap((p) =>
              p.models.map((m) => {
                const uid = `${p.id}/${m.id}`;
                const usable = m.enabled && p.has_key;
                const note = usable ? "" : m.enabled ? " · 未配 Key" : " · 已停用";
                return (
                  <option key={uid} value={uid} disabled={!usable}>
                    {uid}
                    {note}
                  </option>
                );
              }),
            )}
          </select>
        </div>
        <table className="model-table">
          <thead>
            <tr>
              <th>模型 ID</th>
              <th>最大上下文</th>
              <th>最大输出</th>
              <th>启用</th>
            </tr>
          </thead>
          <tbody>
            {selected.models.map((m) => (
              <tr key={m.id}>
                <td>{m.id}</td>
                <td>{m.context.toLocaleString()}</td>
                <td>{m.max_output.toLocaleString()}</td>
                <td>
                  <input
                    type="checkbox"
                    checked={m.enabled}
                    disabled={saving}
                    onChange={(e) =>
                      onAction(() => putModelEnabled(selected.id, m.id, e.target.checked))
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
