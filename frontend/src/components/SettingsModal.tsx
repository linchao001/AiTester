import { useEffect, useState } from "react";
import {
  getModels,
  putApiKey,
  putDefault,
  putModelEnabled,
  type ModelsResponse,
} from "../api/client";

interface SettingsModalProps {
  onClose: () => void;
  onChanged: () => void;
}

export default function SettingsModal({ onClose, onChanged }: SettingsModalProps) {
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [keyInput, setKeyInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getModels()
      .then((resp) => {
        setModels(resp);
        setSelectedId(resp.providers[0].id);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  async function run(action: () => Promise<ModelsResponse>): Promise<void> {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const resp = await action();
      setModels(resp);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const selected = models?.providers.find((p) => p.id === selectedId) ?? null;

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>设置 · 模型设置</strong>
          <button className="btn-secondary" onClick={onClose}>关闭</button>
        </div>
        {error !== null && <p className="modal-error">{error}</p>}
        {models === null ? (
          <p>加载中…</p>
        ) : (
          <div className="modal-body">
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
            {selected !== null && (
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
                      run(async () => {
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
                      onClick={() => run(() => putApiKey(selected.id, ""))}
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
                    onChange={(e) => run(() => putDefault(e.target.value))}
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
                              run(() => putModelEnabled(selected.id, m.id, e.target.checked))
                            }
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
