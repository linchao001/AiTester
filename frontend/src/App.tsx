import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { getHealth, type HealthResponse } from "./api/client";
import SettingsModal from "./components/settings/SettingsModal";
import ChatPage from "./pages/ChatPage";
import KbPage from "./pages/KbPage";
import ProjectsPage from "./pages/ProjectsPage";
import "./App.css";

const tabs = [
  { to: "/chat", label: "聊天" },
  { to: "/kb", label: "知识库" },
  { to: "/projects", label: "项目管理" },
];

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

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">AiTester · 测试智能体</span>
        <nav>
          {tabs.map((t) => (
            <NavLink key={t.to} to={t.to} className={({ isActive }) => (isActive ? "tab active" : "tab")}>
              {t.label}
            </NavLink>
          ))}
        </nav>
        <button className="btn-settings" onClick={() => setSettingsOpen(true)}>⚙ 设置</button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route
            path="/chat"
            element={
              <ChatPage
                health={health}
                healthError={healthError}
                onOpenSettings={() => setSettingsOpen(true)}
              />
            }
          />
          <Route path="/kb" element={<KbPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
        </Routes>
      </main>
      {settingsOpen && (
        <SettingsModal onClose={() => setSettingsOpen(false)} onChanged={refreshHealth} />
      )}
    </div>
  );
}
