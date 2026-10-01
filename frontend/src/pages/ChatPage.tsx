import type { HealthResponse } from "../api/client";
import PlaceholderPage from "../components/PlaceholderPage";

interface ChatPageProps {
  health: HealthResponse | null;
  healthError: string | null;
  onOpenSettings: () => void;
}

export default function ChatPage({ health, healthError, onOpenSettings }: ChatPageProps) {
  return (
    <PlaceholderPage
      big="💬"
      title="聊天页 · 待按原型实现"
      hint="会话列表、当前项目 / 当前智能体、工作区面板将在聊天页专项中对齐原型"
      health={health}
      healthError={healthError}
      onOpenSettings={onOpenSettings}
    />
  );
}
