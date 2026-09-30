import type { HealthResponse } from "../api/client";

interface ChatPageProps {
  health: HealthResponse | null;
  healthError: string | null;
  onOpenSettings: () => void;
}

export default function ChatPage({ health, healthError, onOpenSettings }: ChatPageProps) {
  return (
    <section className="placeholder">
      <p>聊天页 · 骨架占位，后续按原型专项开发</p>
      {healthError !== null && (
        <p className="status-bad">后端未联通：{healthError}（请先启动 backend，见 README）</p>
      )}
      {health !== null && (
        <p className="status-ok">
          后端联通正常：{health.service}（{health.ok ? "ok" : "异常"}）
        </p>
      )}
      {health !== null &&
        (health.llm_provider === "mock" ? (
          <p className="status-bad">
            未配置可用模型，
            <button className="link-btn" onClick={onOpenSettings}>打开 设置 · 模型设置</button>
          </p>
        ) : (
          <p className="status-ok">当前默认模型：{health.llm_provider}</p>
        ))}
    </section>
  );
}
