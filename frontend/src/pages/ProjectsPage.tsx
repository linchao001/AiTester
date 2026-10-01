import PlaceholderPage from "../components/PlaceholderPage";

export default function ProjectsPage() {
  return (
    <PlaceholderPage
      big="🗂"
      title="项目管理页 · 待按原型实现"
      hint="项目列表与新建 / 编辑 / 删除弹窗将在项目管理专项中对齐原型"
      health={null}
      healthError={null}
      onOpenSettings={() => undefined}
    />
  );
}
