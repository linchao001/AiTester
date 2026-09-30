import { useEffect, useState } from "react";
import { getHealth, type HealthResponse } from "../api/client";

export default function ChatPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <section className="placeholder">
      <p>聊天页 · 骨架占位，后续按原型专项开发</p>
      {error !== null && <p className="status-bad">后端未联通：{error}（请先启动 backend，见 README）</p>}
      {health !== null && (
        <p className="status-ok">
          后端联通正常：{health.service}（{health.ok ? "ok" : "异常"}）
        </p>
      )}
      {health !== null && (
        <p className="status-ok">
          当前模型提供商：{health.llm_provider}（mock 为回显；真实调用请在 backend/.env 配置 Key）
        </p>
      )}
    </section>
  );
}
