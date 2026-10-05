import { useCallback, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { getHealth, type HealthResponse } from "./api/client";
import SettingsModal from "./components/settings/SettingsModal";
import ChatPage from "./pages/ChatPage";
import KbPage from "./pages/KbPage";
import ProjectsPage from "./pages/ProjectsPage";
import "./App.css";

const tabs = [
  { to: "/chat", label: "💬 聊天" },
  { to: "/kb", label: "📚 知识库" },
  { to: "/projects", label: "🗂 项目管理" },
];

const HEALTH_RETRY_MS = 3000;

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const refreshHealth = useCallback(() => {
    getHealth()
      .then((h) => {
        setHealth(h);
        setHealthError(null);
      })
      .catch((e: Error) => setHealthError(e.message));
  }, []);

  // 首次成功之前自己爬：挂载时后端还没起（或中途重启）会让 health 永远停在 null，
  // 于是每个 guard() 都把「后端活着」报成「后端未就绪」，只能靠用户手动刷新页面。
  // 成功一次即停（依赖里的 health 变化会清掉定时器）→ 就绪后零后台流量。
  useEffect(() => {
    if (health) return;
    refreshHealth();
    const id = setInterval(refreshHealth, HEALTH_RETRY_MS);
    return () => clearInterval(id);
  }, [health, refreshHealth]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="logo">
          <b>Ai</b>Tester{" "}
          <span style={{ fontWeight: 500, fontSize: 13, color: "var(--text-2)" }}>测试智能体</span>
        </div>
        <span className="ver">v0.1 MVP</span>
        <nav className="nav-tabs">
          {tabs.map((t) => (
            <NavLink
              key={t.to}
              to={t.to}
              className={({ isActive }) => (isActive ? "nav-tab on" : "nav-tab")}
            >
              {t.label}
            </NavLink>
          ))}
        </nav>
        <div className="spacer"></div>
        <button className="icon-btn" title="设置" onClick={() => setSettingsOpen(true)}>⚙</button>
      </header>
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route
          path="/chat"
          element={
            <ChatPage
              health={health}
              healthError={healthError}
              onOpenSettings={() => setSettingsOpen(true)}
              onRetryHealth={refreshHealth}
            />
          }
        />
        <Route path="/kb" element={<KbPage />} />
        <Route path="/projects" element={<ProjectsPage />} />
      </Routes>
      {settingsOpen && (
        <SettingsModal onClose={() => setSettingsOpen(false)} onChanged={refreshHealth} />
      )}
    </div>
  );
}
