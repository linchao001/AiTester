import { useCallback, useEffect, useState } from "react";
import {
  getCapabilities,
  getModels,
  type CapabilityResponse,
  type ModelsResponse,
} from "../../api/client";
import ModelPane from "./ModelPane";

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

  useEffect(() => {
    Promise.all([getModels(), getCapabilities()])
      .then(([m, c]) => {
        setModels(m);
        setCaps(c);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

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

  // @ts-expect-error Task 7 起被 <AgentPane onAction={runCaps}/> 消费；接线后请删除本行指令
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
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>⚙ 设置</strong>
          <button className="btn-secondary" onClick={onClose}>关闭</button>
        </div>
        {error !== null && <p className="modal-error">{error}</p>}
        <div className="s-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={t.id === tab ? "s-tab active" : "s-tab"}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {models === null || caps === null ? (
          <p>{error === null ? "加载中…" : "配置加载失败，请关闭后重试"}</p>
        ) : (
          <>
            <div className="modal-body" hidden={tab !== "model"}>
              <ModelPane models={models} saving={saving} onAction={runModels} />
            </div>
            <div className="modal-body" hidden={tab !== "agent"} />
            <div className="modal-body" hidden={tab !== "tool"} />
          </>
        )}
      </div>
    </div>
  );
}
