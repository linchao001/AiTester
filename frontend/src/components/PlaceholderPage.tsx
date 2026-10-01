import type { HealthResponse } from "../api/client";

interface Props {
  big: string;
  title: string;
  hint: string;
  health: HealthResponse | null;
  healthError: string | null;
  onOpenSettings: () => void;
}

export default function PlaceholderPage({ big, title, hint, health, healthError, onOpenSettings }: Props) {
  return (
    <div className="layout">
      <section className="welcome">
        <div className="w-logo">{big}</div>
        <h2>{title}</h2>
        <p>{hint}</p>
        <div className="chips">
          {healthError !== null && <span className="chip warn">后端未联通：{healthError}</span>}
          {health !== null && health.llm_provider !== "mock" && (
            <span className="chip">当前默认模型：{health.llm_provider}</span>
          )}
          {health !== null && health.llm_provider === "mock" && (
            <button className="chip link" onClick={onOpenSettings}>
              未配置可用模型，打开 设置 · 模型设置
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
