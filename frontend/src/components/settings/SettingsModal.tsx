import { useCallback, useEffect, useState } from "react";
import {
  getCapabilities,
  getModels,
  putDefault,
  type CapabilityResponse,
  type ModelsResponse,
} from "../../api/client";
import AgentPane from "./AgentPane";
import ModelPane from "./ModelPane";
import ToolPane from "./ToolPane";

type SettingsTab = "model" | "agent" | "tool";

const TABS: { id: SettingsTab; label: string }[] = [
  { id: "model", label: "🧠 模型设置" },
  { id: "agent", label: "🤖 智能体配置" },
  { id: "tool", label: "🛠 工具" },
];

interface SettingsModalProps {
  onClose: () => void;
  onChanged: () => void;
}

export default function SettingsModal({ onClose, onChanged }: SettingsModalProps) {
  const [tab, setTab] = useState<SettingsTab>("model");
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [caps, setCaps] = useState<CapabilityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    Promise.all([getModels(), getCapabilities()])
      .then(([m, c]) => {
        setModels(m);
        setCaps(c);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const runModels = useCallback(
    async (action: () => Promise<ModelsResponse>): Promise<void> => {
      if (saving) return;
      setSaving(true);
      setError(null);
      try {
        setModels(await action());
        // 模型停用/清 Key 会让智能体默认模型回落，视图需同步
        setCaps(await getCapabilities());
        onChanged();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setSaving(false);
      }
    },
    [saving, onChanged],
  );

  const runCaps = useCallback(
    async (action: () => Promise<CapabilityResponse>): Promise<void> => {
      if (saving) return;
      setSaving(true);
      setError(null);
      try {
        setCaps(await action());
        onChanged();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setSaving(false);
      }
    },
    [saving, onChanged],
  );

  return (
    <div className="mask" onClick={onClose}>
      <div className="modal set-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="m-head">
          ⚙ 设置
          <span className="def-llm" hidden={tab !== "model"}>
            默认 LLM
            <select
              value={models === null ? "" : models.default_uid}
              disabled={models === null || saving}
              onChange={(e) => {
                if (e.target.value !== "") void runModels(() => putDefault(e.target.value));
              }}
            >
              {models === null ? (
                <option value="">加载中…</option>
              ) : (
                usableOptions(models)
              )}
            </select>
          </span>
          <div className="spacer"></div>
          <button className="icon-btn" title="关闭" onClick={onClose}>✕</button>
        </div>
        {error !== null && (
          <div className="set-tip" style={{ color: "var(--fail)", padding: "8px 20px 0" }}>
            {error}
          </div>
        )}
        <div className="s-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={t.id === tab ? "s-tab on" : "s-tab"}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {models === null || caps === null ? (
          <div className="set-body tabbed">
            <div className="set-right">
              <p className="mdl-empty">{error === null ? "加载中…" : "配置加载失败，请关闭后重试"}</p>
            </div>
          </div>
        ) : (
          <>
            <div className="set-body tabbed" hidden={tab !== "model"}>
              <ModelPane models={models} saving={saving} onAction={runModels} />
            </div>
            <div className="set-body tabbed" hidden={tab !== "agent"}>
              <AgentPane models={models} caps={caps} saving={saving} onAction={runCaps} />
            </div>
            <div className="set-body tabbed" hidden={tab !== "tool"}>
              <ToolPane caps={caps} saving={saving} onAction={runCaps} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function usableOptions(models: ModelsResponse) {
  const options = models.providers.flatMap((p) =>
    p.models
      .filter((m) => m.enabled && p.has_key)
      .map((m) => (
        <option key={`${p.id}/${m.id}`} value={`${p.id}/${m.id}`}>
          {p.name} / {m.name || m.id}
        </option>
      )),
  );
  return options.length > 0 ? options : <option value="">（无可用模型：请先配置 API Key）</option>;
}
