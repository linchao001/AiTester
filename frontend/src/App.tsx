import { NavLink, Navigate, Route, Routes } from "react-router-dom";
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
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/kb" element={<KbPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
        </Routes>
      </main>
    </div>
  );
}
